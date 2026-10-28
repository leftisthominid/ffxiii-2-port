// ffxiii2 - PS3 cinematics (ported from the Lightning Returns port, lr_hooks.cpp).
//
// The 360 movies are Bink 1 files; the PS3 release has the same 97 movies as H.264 PAMF
// streams at about ten times the bit rate. Bink keeps playing the 360 file (audio, timing,
// skipping, and looping stay the game's own); each time a frame finishes decoding, the same
// frame of the PS3 movie (copied out of the disc as <name>.mp4) is written over it in the
// game's frame buffers. XIII-2's movie player is Lightning Returns' (sub_82873F28 opens,
// sub_82874188 closes); Bink 1 has the same HBINK and BINKFRAMEBUFFERS layout as Bink 2:
//
//   sub_82C36D38  BinkOpen(name, options, flags): name is 8 + 16 characters '@'+nibble
//                 encoding the pack file handle and the movie's file code (disc executable: sub_8283FF80;
//                 Lightning Returns puts the byte offset in the pack there)
//   sub_82C33830  BinkRegisterFrameBuffers(bink, BINKFRAMEBUFFERS*)
//   sub_82C31978  BinkDoFrameAsyncWait(bink, us): -1 = until the frame is done
//   sub_82C37930  BinkClose(bink)
// HBINK: +0 width, +4 height, +8 frames, +12 current frame (1-based), +20/+24 frame rate.
// BINKFRAMEBUFFERS: +0 count, +4/+8 Y size, +12/+16 chroma size, +20 index of the buffer
// just decoded, +24 buffers of 48 bytes: Y, Cr, Cb, A planes of {allocate, address, pitch}.
// Colours as in Lightning Returns: limited-range BT.709 in, full-range BT.601 out.
//
// LR_PS3_MOVIES = folder with <name>.mp4 (default: movies_ps3 next to the game); none = 360.
// LR_FFMPEG = ffmpeg executable (default: ffmpeg.exe next to the game, then PATH; Android: the
// app's libffmpeg.so, set by x2_android_main.cpp).

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>

#include <rex/hook.h>
#include <rex/logging/macros.h>
#if !defined(_WIN32)
#include <SDL3/SDL.h>
#endif

std::filesystem::path X2InstallDir();  // x2_settings.cpp: next to the exe (Android: X2_INSTALL_DIR)
int X2ConfigGetSkipStoryRecaps();

namespace {
uint32_t lr_load_be32(uint8_t* base, uint32_t address) {
  return __builtin_bswap32(*reinterpret_cast<uint32_t*>(base + address));
}

uint64_t lr_now_us() {
  return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}
}  // namespace

#include <condition_variable>
#include <shared_mutex>
#include <functional>
#include <cstdio>
#include <cstring>
#include <deque>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <fstream>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <intrin.h>
#else
#include <csignal>
#include <dlfcn.h>
#include <pthread.h>
#include <time.h>
#include <fcntl.h>
#include <link.h>
#include <spawn.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <sys/syscall.h>
#if defined(__GLIBC__)
#include <execinfo.h>
#endif
#include <unistd.h>
extern char** environ;
#endif

namespace {

struct LrMovieEntry {
  const char* name;
  uint32_t sector;  // position in the 360 pack, 2048-byte units
  uint32_t size;    // 360 .bik size
  uint32_t code;    // file code: what the game puts in the BinkOpen name
};

// Generated from the prepared Steam movies. Matching the verified transport's
// size avoids importing Xbox DLC file codes or movie lists into the overlay.
// The converter keeps these sizes distinct from one another and base movies.
struct X2SteamMovie {
  std::string name;
  LrMovieEntry entry{};
};
const std::vector<X2SteamMovie>& x2_steam_movies() {
  static const std::vector<X2SteamMovie> entries = [] {
    std::vector<X2SteamMovie> result;
    std::ifstream file(X2InstallDir() / "movies_steam" / "index.txt");
    std::string name;
    uint32_t size;
    while (file >> name >> size) {
      if (name.empty() || name.size() > 80 || size < 44 ||
          name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") != std::string::npos) continue;
      result.push_back({name, {nullptr, 0, size, 0}});
    }
    for (auto& movie : result) movie.entry.name = movie.name.c_str();
    return result;
  }();
  return entries;
}

// From movielistu.x360.bin (one pack, movieu.x360.bin), in pack order.
constexpr LrMovieEntry kLrMovies[] = {
    {"hm_hstr_300_us", 0x0, 0x1b83e0, 0x079e3760},
    {"hm_hstr_311_us", 0x371, 0xf53a4, 0x089e38d2},
    {"hm_hstr_310_us", 0x55c, 0xf49a0, 0x089e38d3},
    {"hm_hstr_320_us", 0x746, 0xf4980, 0x099e3a46},
    {"hm_hstr_331_us", 0x930, 0x1152e8, 0x0a9e3c38},
    {"hm_hstr_330_us", 0xb5b, 0x115304, 0x0a9e3c39},
    {"hm_hstr_230_us", 0xd86, 0x1566d8, 0x0aa07a90},
    {"hm_hstr_231_us", 0x1033, 0x15668c, 0x0aa07a91},
    {"hm_hstr_340_us", 0x12e0, 0xf49f0, 0x0b9e3dac},
    {"hm_hstr_220_us", 0x14ca, 0xf4a40, 0x0ba07c03},
    {"hm_hstr_351_us", 0x16b4, 0x1b82a8, 0x0c9e3f1e},
    {"hm_hstr_350_us", 0x1a25, 0x1b9088, 0x0c9e3f1f},
    {"hm_hstr_210_us", 0x1d98, 0xf4974, 0x0ca07df6},
    {"hm_hstr_211_us", 0x1f82, 0xf5270, 0x0ca07df7},
    {"hm_hstr_450_us", 0x216d, 0x1b8a98, 0x0caf8860},
    {"hm_hstr_360_us", 0x24df, 0xf6f9c, 0x0d9e40f2},
    {"hm_hstr_200_us", 0x26cd, 0x12578c, 0x0da07f69},
    {"hm_hstr_160_us", 0x2918, 0xf6704, 0x0da2bdc0},
    {"hm_hstr_440_us", 0x2b05, 0xf4898, 0x0daf89d3},
    {"hm_hstr_371_us", 0x2cef, 0xf5584, 0x0e9e4264},
    {"hm_hstr_370_us", 0x2eda, 0xfd7cc, 0x0e9e4265},
    {"hm_hstr_270_us", 0x30d5, 0x125810, 0x0ea080dc},
    {"hm_hstr_171_us", 0x3321, 0x1b83ac, 0x0ea2bfb2},
    {"hm_hstr_170_us", 0x3692, 0x1b8324, 0x0ea2bfb3},
    {"hm_hstr_470_us", 0x3a03, 0x1b834c, 0x0eaf8b46},
    {"hm_hstr_471_us", 0x3d74, 0x1b8c2c, 0x0eaf8b47},
    {"hm_hstr_380_us", 0x40e6, 0x16ec78, 0x0f9e43d8},
    {"hm_hstr_381_us", 0x43c4, 0x16ee9c, 0x0f9e43d9},
    {"hm_hstr_261_us", 0x46a2, 0xf4d30, 0x0fa0824e},
    {"hm_hstr_260_us", 0x488c, 0x103f48, 0x0fa0824f},
    {"hm_hstr_140_us", 0x4a94, 0xf48f4, 0x0fa2c126},
    {"hm_hstr_460_us", 0x4c7e, 0x104e5c, 0x0faf8d39},
    {"hm_hstr_390_us", 0x4e88, 0xf48a0, 0x109e454b},
    {"hm_hstr_250_us", 0x5072, 0x11d524, 0x10a08422},
    {"hm_hstr_151_us", 0x52ad, 0x1a7e24, 0x10a2c298},
    {"hm_hstr_150_us", 0x55fd, 0x1a8b08, 0x10a2c299},
    {"hm_hstr_090_us", 0x594f, 0x125830, 0x10a50170},
    {"hm_hstr_091_us", 0x5b9b, 0x125a4c, 0x10a50171},
    {"hm_hstr_410_us", 0x5de7, 0xf49b0, 0x10af8eac},
    {"hm_hstr_240_us", 0x5fd1, 0x1bb840, 0x11a08595},
    {"hm_hstr_120_us", 0x6349, 0xf49f8, 0x11a2c40c},
    {"hm_hstr_080_us", 0x6533, 0xf577c, 0x11a502e3},
    {"hm_hstr_401_us", 0x671e, 0xfa878, 0x11af901e},
    {"hm_hstr_400_us", 0x6914, 0xf6ff4, 0x11af901f},
    {"hm_hstr_131_us", 0x6b02, 0xf6de4, 0x12a2c5fe},
    {"hm_hstr_130_us", 0x6cf0, 0xf51a8, 0x12a2c5ff},
    {"hm_hstr_430_us", 0x6edb, 0x1c7888, 0x12af91f2},
    {"hm_hstr_100_us", 0x726b, 0xf7d40, 0x13a2c752},
    {"hm_hstr_421_us", 0x745b, 0xf4884, 0x13af9364},
    {"hm_hstr_420_us", 0x7645, 0xf4894, 0x13af9365},
    {"hm_hstr_290_us", 0x782f, 0x1ba0e8, 0x14a08a6e},
    {"hm_hstr_111_us", 0x7ba4, 0xf71d8, 0x14a2c8c4},
    {"hm_hstr_110_us", 0x7d93, 0x10027c, 0x14a2c8c5},
    {"hm_hstr_281_us", 0x7f94, 0x158bd8, 0x15a08bc0},
    {"hm_hstr_280_us", 0x8246, 0x159d30, 0x15a08bc1},
    {"hm_hstr_010_us", 0x84fa, 0x1bed84, 0x18a50de8},
    {"hm_hstr_011_us", 0x8878, 0x1b8410, 0x18a50de9},
    {"hm_hstr_490_us", 0x8be9, 0x1b837c, 0x18af9b04},
    {"hm_hstr_480_us", 0x8f5a, 0xf4fb0, 0x19af9cf7},
    {"hm_hstr_030_us", 0x9144, 0x12d054, 0x1aa510ce},
    {"hm_hstr_180_us", 0x939f, 0x1b83b0, 0x1ba2d3ca},
    {"hm_hstr_021_us", 0x9710, 0x1b8690, 0x1ba512a0},
    {"hm_hstr_020_us", 0x9a81, 0x1ba7d0, 0x1ba512a1},
    {"hm_hstr_191_us", 0x9df6, 0xf4954, 0x1ca2d5bc},
    {"hm_hstr_190_us", 0x9fe0, 0xf5e20, 0x1ca2d5bd},
    {"hm_hstr_050_us", 0xa1cc, 0x1b82d8, 0x1ca51414},
    {"hm_hstr_041_us", 0xa53d, 0x1b83ac, 0x1da51586},
    {"hm_hstr_040_us", 0xa8ae, 0x1b8298, 0x1da51587},
    {"hm_hstr_070_us", 0xac1f, 0x1ba18c, 0x1ea5177a},
    {"hm_hstr_071_us", 0xaf94, 0x1b82c8, 0x1ea5177b},
    {"hm_hstr_060_us", 0xb305, 0x1b82ec, 0x1fa518ed},
    {"hm_hstr_520_us", 0xb676, 0xf49ec, 0x89ac7bb0},
    {"hm_hstr_531_us", 0xb860, 0x1b832c, 0x8aac7d22},
    {"hm_hstr_530_us", 0xbbd1, 0x1bf250, 0x8aac7d23},
    {"hm_hstr_500_us", 0xbf50, 0x1b84e4, 0x8bac7e96},
    {"hm_hstr_501_us", 0xc2c1, 0x1b83a0, 0x8bac7e97},
    {"hm_hstr_510_us", 0xc632, 0xf4944, 0x8cac8009},
    {"hm_hstr_560_us", 0xc81c, 0x126b4c, 0x8dac81fc},
    {"hm_hstr_570_us", 0xca6a, 0x1b8440, 0x8eac836f},
    {"hm_hstr_540_us", 0xcddb, 0xf49b0, 0x8fac84c2},
    {"hm_hstr_551_us", 0xcfc5, 0x1b9a00, 0x90ac86b4},
    {"hm_hstr_550_us", 0xd339, 0x1b8480, 0x90ac86b5},
    {"hm_hstr_580_us", 0xd6aa, 0x199a48, 0x93ac8b0e},
    {"hm_hstr_590_us", 0xd9de, 0x1b8290, 0x94ac8ce1},
    {"hm_hstr_620_us", 0xdd4f, 0x1b8810, 0xa3aa65e7},
    {"hm_hstr_610_us", 0xe0c1, 0x1bbd48, 0xa4aa675a},
    {"hm_hstr_611_us", 0xe439, 0x1b9118, 0xa4aa675b},
    {"hm_hstr_601_us", 0xe7ac, 0x1b8f30, 0xa5aa68cc},
    {"hm_hstr_600_us", 0xeb1e, 0x1b8340, 0xa5aa68cd},
    {"tm_abst_080_us", 0xee8f, 0x7ac160, 0xcb256e12},
    {"tm_abst_060_us", 0xfde8, 0x78eeb8, 0xcd257178},
    {"tm_abst_070_us", 0x10d06, 0x7850a4, 0xce2572eb},
    {"tm_abst_040_us", 0x11c11, 0x7a2e30, 0xcf25745e},
    {"tm_abst_050_us", 0x12b57, 0x7a17c0, 0xd0257631},
    {"tm_abst_020_us", 0x13a9a, 0x7957a8, 0xd12577a4},
    {"tm_abst_030_us", 0x149c5, 0x7899d8, 0xd2257917},
    {"tm_abst_010_us", 0x158d9, 0x79feb8, 0xd4257c7d},
};

std::filesystem::path lr_env_path(const char* name) {
#if defined(_WIN32)
  std::wstring wname(name, name + std::strlen(name));
  if (const wchar_t* v = _wgetenv(wname.c_str()); v && *v) return std::filesystem::path(v);
#else
  if (const char* v = std::getenv(name); v && *v) return std::filesystem::path(v);
#endif
  return {};
}

std::filesystem::path lr_exe_dir() {
#if defined(_WIN32)
  wchar_t exe[MAX_PATH];
  DWORD n = GetModuleFileNameW(nullptr, exe, MAX_PATH);
  if (n && n < MAX_PATH) return std::filesystem::path(exe).parent_path();
  return {};
#else
  std::error_code ec;
  return std::filesystem::read_symlink("/proc/self/exe", ec).parent_path();
#endif
}

// LR_PS3_MOVIES, else movies_ps3 next to the exe (the builder puts them there).
const std::filesystem::path& lr_movie_dir() {
  static const std::filesystem::path dir = [] {
    std::filesystem::path d = lr_env_path("LR_PS3_MOVIES");
    if (d.empty()) {
      std::error_code ec;
      std::filesystem::path local = X2InstallDir() / "movies_ps3";
      if (std::filesystem::is_directory(local, ec)) d = local;
    }
    return d;
  }();
  return dir;
}

std::filesystem::path lr_ffmpeg_path() {
  std::filesystem::path p = lr_env_path("LR_FFMPEG");
  if (!p.empty()) return p;
#if defined(_WIN32)
  wchar_t exe[MAX_PATH];
  DWORD n = GetModuleFileNameW(nullptr, exe, MAX_PATH);
  if (n && n < MAX_PATH) {
    std::filesystem::path local = std::filesystem::path(exe).parent_path() / L"ffmpeg.exe";
    std::error_code ec;
    if (std::filesystem::exists(local, ec)) return local;
  }
  return L"ffmpeg.exe";
#else
  return "ffmpeg";
#endif
}

// An ffmpeg child process writing raw yuv420p frames to a pipe.
class LrFfmpegPipe {
 public:
  ~LrFfmpegPipe() { Stop(); }

  bool Start(const std::filesystem::path& ffmpeg, const std::filesystem::path& file, double seek_seconds,
             uint32_t width, uint32_t height, double fps) {
    // One output frame per frame of the 360 movie: the game asks for frames by the Bink frame
    // number, and a PS3 stream at another rate would drift and run out before the movie ends.
    char vf[320];
    std::snprintf(vf, sizeof(vf),
                  "fps=fps=%.6f:round=near,"
                  "scale=w=%u:h=%u:in_color_matrix=bt709:out_color_matrix=bt601:in_range=tv:out_range=pc:"
                  "flags=bicubic,format=yuv420p",
                  fps, width, height);
    char ss[32];
    std::snprintf(ss, sizeof(ss), "%.4f", seek_seconds);
    std::vector<std::string> args = {"-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2"};
    if (seek_seconds > 0) {
      args.push_back("-ss");
      args.push_back(ss);
    }
    std::vector<std::string> tail = {"-map", "0:v:0", "-an", "-sn", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"};
#if defined(_WIN32)
    auto quote = [](const std::wstring& a) { return L"\"" + a + L"\""; };
    auto widen = [](const std::string& a) { return std::wstring(a.begin(), a.end()); };
    std::wstring cmd = quote(ffmpeg.wstring());
    for (auto& a : args) cmd += L" " + widen(a);
    cmd += L" -i " + quote(file.wstring());
    for (auto& a : tail) cmd += L" " + (a == vf ? quote(widen(a)) : widen(a));

    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    HANDLE read_end = nullptr, write_end = nullptr;
    if (!CreatePipe(&read_end, &write_end, &sa, 4 << 20)) return false;
    SetHandleInformation(read_end, HANDLE_FLAG_INHERIT, 0);
    std::filesystem::path log = std::filesystem::temp_directory_path() / L"lr_movie_ffmpeg.log";
    HANDLE err = CreateFileW(log.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa,
                             OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = nullptr;
    si.hStdOutput = write_end;
    si.hStdError = err != INVALID_HANDLE_VALUE ? err : nullptr;
    PROCESS_INFORMATION pi{};
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
                             nullptr, nullptr, &si, &pi);
    CloseHandle(write_end);
    if (err != INVALID_HANDLE_VALUE) CloseHandle(err);
    if (!ok) {
      CloseHandle(read_end);
      return false;
    }
    CloseHandle(pi.hThread);
    process_ = pi.hProcess;
    read_ = read_end;
    return true;
#else
    int fds[2];
    if (pipe(fds) != 0) return false;
    std::vector<std::string> all = {ffmpeg.string()};
    all.insert(all.end(), args.begin(), args.end());
    all.push_back("-i");
    all.push_back(file.string());
    all.insert(all.end(), tail.begin(), tail.end());
    std::vector<char*> argv;
    for (auto& a : all) argv.push_back(a.data());
    argv.push_back(nullptr);
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, fds[1], 1);
    posix_spawn_file_actions_addclose(&fa, fds[0]);
    posix_spawn_file_actions_addopen(&fa, 0, "/dev/null", O_RDONLY, 0);
#if defined(__ANDROID__)
    // No /tmp on Android: ffmpeg's messages go to the logs folder of the user data.
    static const std::string err_log = (X2InstallDir() / "logs" / "movie_ffmpeg.log").string();
    posix_spawn_file_actions_addopen(&fa, 2, err_log.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
#else
    posix_spawn_file_actions_addopen(&fa, 2, "/tmp/lr_movie_ffmpeg.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
#endif
    pid_t pid = 0;
    int rc = posix_spawnp(&pid, argv[0], &fa, nullptr, argv.data(), environ);
    posix_spawn_file_actions_destroy(&fa);
    close(fds[1]);
    if (rc != 0) {
      close(fds[0]);
      return false;
    }
    pid_ = pid;
    fd_ = fds[0];
    return true;
#endif
  }

  // Reads exactly size bytes; false at the end of the stream or on an error.
  bool Read(uint8_t* dst, size_t size) {
    size_t done = 0;
    while (done < size) {
#if defined(_WIN32)
      DWORD got = 0;
      DWORD want = DWORD((std::min<size_t>)(size - done, 1u << 20));
      if (!read_ || !ReadFile(read_, dst + done, want, &got, nullptr) || got == 0) return false;
#else
      if (fd_ < 0) return false;
      ssize_t got = read(fd_, dst + done, size - done);
      if (got <= 0) return false;
#endif
      done += size_t(got);
    }
    return true;
  }

  void Stop() {
#if defined(_WIN32)
    if (process_) {
      TerminateProcess(process_, 0);
      WaitForSingleObject(process_, 2000);
      CloseHandle(process_);
      process_ = nullptr;
    }
    if (read_) {
      CloseHandle(read_);
      read_ = nullptr;
    }
#else
    if (pid_ > 0) {
      kill(pid_, SIGKILL);
      waitpid(pid_, nullptr, 0);
      pid_ = -1;
    }
    if (fd_ >= 0) {
      close(fd_);
      fd_ = -1;
    }
#endif
  }

  // Unblocks a Read on another thread (the reader then calls Stop).
  void Interrupt() {
#if defined(_WIN32)
    if (process_) TerminateProcess(process_, 0);
#else
    if (pid_ > 0) kill(pid_, SIGKILL);
#endif
  }

 private:
#if defined(_WIN32)
  HANDLE process_ = nullptr;
  HANDLE read_ = nullptr;
#else
  pid_t pid_ = -1;
  int fd_ = -1;
#endif
};

// One ffmpeg run from a start frame, decoding ahead on its own thread. That thread also
// starts and ends the ffmpeg process, so a game thread never waits for a process to start
// or exit; it only takes frames that are ready.
class LrMovieDecoder {
 public:
  LrMovieDecoder(const std::filesystem::path& file, uint32_t first, uint32_t width, uint32_t height, double fps)
      : frame_bytes_(size_t(width) * height * 3 / 2), next_number_(first) {
    worker_ = std::thread([this, file, first, width, height, fps] { Run(file, first, width, height, fps); });
  }
  ~LrMovieDecoder() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stop_ = true;
      if (pipe_) pipe_->Interrupt();
    }
    cv_.notify_all();
    if (worker_.joinable()) worker_.join();
  }

  uint32_t next_number() {
    std::lock_guard<std::mutex> lock(mutex_);
    return next_number_;
  }

  // The PS3 video has no more frames (or ffmpeg could not run).
  bool ended() {
    std::lock_guard<std::mutex> lock(mutex_);
    return ended_ && ready_.empty();
  }

  // Moves frame n into out; false if it is not decoded within `wait`, or past the end of the
  // PS3 video (then the 360 frames play on rather than the last PS3 frame freezing).
  bool Take(uint32_t n, std::vector<uint8_t>& out, uint32_t& out_number, std::chrono::milliseconds wait) {
    std::unique_lock<std::mutex> lock(mutex_);
    auto deadline = std::chrono::steady_clock::now() + wait;
    bool got = false;
    while (next_number_ <= n) {
      if (!cv_.wait_until(lock, deadline, [&] { return !ready_.empty() || ended_; })) return got && out_number == n;
      if (ready_.empty()) return got && out_number == n;  // ended
      out.swap(ready_.front());
      spare_.push_back(std::move(ready_.front()));
      ready_.pop_front();
      out_number = next_number_++;
      got = true;
      cv_.notify_all();
    }
    return got;
  }

 private:
  void Run(std::filesystem::path file, uint32_t first, uint32_t width, uint32_t height, double fps) {
    auto pipe = std::make_unique<LrFfmpegPipe>();
    // Seek a little before the frame so rounding cannot skip it; ffmpeg decodes from the
    // previous key frame and drops everything before the seek time.
    double seek = first > 1 ? (double(first) - 1.5) / fps : 0.0;
    std::filesystem::path ffmpeg = lr_ffmpeg_path();
    if (!pipe->Start(ffmpeg, file, seek, width, height, fps)) {
      REXLOG_WARN("LR movie: could not start {}", ffmpeg.string());
      std::lock_guard<std::mutex> lock(mutex_);
      ended_ = true;
      cv_.notify_all();
      return;
    }
    {
      std::lock_guard<std::mutex> lock(mutex_);
      pipe_ = pipe.get();
      if (stop_) pipe_->Interrupt();
    }
    for (;;) {
      std::vector<uint8_t> frame;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [&] { return stop_ || ready_.size() < kAhead; });
        if (stop_) break;
        if (!spare_.empty()) {
          frame = std::move(spare_.back());
          spare_.pop_back();
        }
      }
      frame.resize(frame_bytes_);
      bool ok = pipe->Read(frame.data(), frame.size());
      std::lock_guard<std::mutex> lock(mutex_);
      if (!ok || stop_) {
        ended_ = true;
        cv_.notify_all();
        break;
      }
      ready_.push_back(std::move(frame));
      cv_.notify_all();
    }
    {
      std::lock_guard<std::mutex> lock(mutex_);
      pipe_ = nullptr;
    }
    pipe->Stop();
  }

  static constexpr size_t kAhead = 6;
  size_t frame_bytes_;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<std::vector<uint8_t>> ready_;
  std::vector<std::vector<uint8_t>> spare_;
  uint32_t next_number_;  // number of ready_.front()
  bool ended_ = false, stop_ = false;
  LrFfmpegPipe* pipe_ = nullptr;
  std::thread worker_;
};

// Destroys on a background thread: stopping ffmpeg and joining the decoder thread never
// holds up the game thread that closes or seeks a movie.
template <typename T>
void lr_retire(std::unique_ptr<T> object) {
  if (object) std::thread([o = std::move(object)]() mutable { o.reset(); }).detach();
}

// What the PS3 movie path was doing last; logged when frames stop ("LR hang").
struct LrMovieTrace {
  std::atomic<const char*> name{nullptr};
  std::atomic<uint32_t> frame{0}, shown{0}, late{0}, restarts{0};
  std::atomic<uint64_t> last_call_us{0}, last_write_us{0};
  std::atomic<bool> in_get{false}, in_write{false}, open{false};
} g_lr_movie_trace;

// The PS3 video of one movie. Get(n) returns frame n (1-based) or nullptr if it is not
// ready in time, in which case the 360 frame stays on screen. A jump (seek, loop) starts a
// new decoder at that frame.
class LrPs3Movie {
 public:
  LrPs3Movie(std::string name, std::filesystem::path file, uint32_t width, uint32_t height, double fps)
      : name_(std::move(name)), file_(std::move(file)), width_(width), height_(height), fps_(fps) {}
  ~LrPs3Movie() { lr_retire(std::move(decoder_)); }

  const std::string& name() const { return name_; }
  uint32_t width() const { return width_; }
  uint32_t height() const { return height_; }
  uint32_t late() const { return late_; }
  uint32_t after_end() const { return after_end_; }

  void Start() { Restart(1); }

  // Never blocks: the caller is a game thread holding game locks. A frame that is not
  // decoded yet leaves the 360 frame on screen for that one frame.
  const uint8_t* Get(uint32_t n) {
    if (n == 0) n = 1;
    if (n == current_number_ && !current_.empty()) return current_.data();
    uint32_t next = decoder_ ? decoder_->next_number() : 0;
    auto now = std::chrono::steady_clock::now();
    // Past the end of the PS3 video: nothing to restart (the 360 frames play on).
    bool past_end = decoder_ && n >= next && decoder_->ended();
    bool jump = !past_end && (!decoder_ || n < next || n > next + uint32_t(fps_ * 4));
    // At most one restart per second: a restart that has not produced frames yet must not
    // be replaced by the next one over and over.
    if (jump && (!decoder_ || now - last_restart_ > std::chrono::seconds(1))) {
      Restart(n);
      last_restart_ = now;
      g_lr_movie_trace.restarts.fetch_add(1, std::memory_order_relaxed);
    }
    bool ok = decoder_->Take(n, current_, current_number_, std::chrono::milliseconds(0));
    if (!ok) {
      if (decoder_->ended()) {
        ++after_end_;  // past the end of the PS3 video: the 360 frames play on
        return nullptr;
      }
      ++late_;
      // A frame that is merely late repeats the last PS3 frame (a few frames at most) rather
      // than flashing the 360 one, whose colours differ.
      if (!current_.empty() && ++held_ <= 5) return current_.data();
      return nullptr;
    }
    held_ = 0;
    fresh_ = false;
    return current_.empty() ? nullptr : current_.data();
  }

 private:
  void Restart(uint32_t first) {
    lr_retire(std::move(decoder_));
    decoder_ = std::make_unique<LrMovieDecoder>(file_, first, width_, height_, fps_);
    current_.clear();
    current_number_ = 0;
    fresh_ = true;
  }

  std::string name_;
  std::filesystem::path file_;
  uint32_t width_, height_;
  double fps_;
  std::unique_ptr<LrMovieDecoder> decoder_;
  std::vector<uint8_t> current_;
  uint32_t current_number_ = 0;
  bool fresh_ = true;
  uint32_t late_ = 0;
  uint32_t after_end_ = 0;  // frames the 360 movie had after the PS3 video ended
  uint32_t held_ = 0;  // late frames in a row shown as the previous PS3 frame
  std::chrono::steady_clock::time_point last_restart_{};
};

struct LrMovieSession {
  std::unique_ptr<LrPs3Movie> movie;
  uint32_t frame_buffers = 0;  // guest BINKFRAMEBUFFERS*
  uint32_t last_frame = 0, last_index = ~0u;
  std::atomic<uint32_t> written{0};
  uint32_t logged = 0;
  std::atomic<bool> closed{false};   // BinkClose started: the buffers are about to be freed
  std::atomic<bool> writing{false};  // the writer thread is copying into this movie's buffers
};

// Copies PS3 frames into the game's frame buffers on its own thread. The game thread that
// calls BinkDoFrameAsyncWait holds game locks; a write into memory the GPU watches makes
// the runtime take its global lock, which the graphics interrupt holds while it waits for
// one of those game locks - a deadlock (the New Game freeze). This thread holds no game
// locks, and the game thread never waits for it (BinkClose waits at most 50 ms).
class LrMovieWriter {
 public:
  struct Plane {
    uint32_t address, pitch, cols, rows, src_offset, src_pitch;
  };
  struct Job {
    std::shared_ptr<LrMovieSession> session;
    uint8_t* base;
    std::vector<uint8_t> frame;
    Plane planes[3];
  };

  static LrMovieWriter& Get() {
    static LrMovieWriter* writer = new LrMovieWriter();  // never destroyed: lives until exit
    return *writer;
  }

  // Takes a spare frame buffer to fill (avoids allocating 1.4 MB per frame).
  std::vector<uint8_t> Spare() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (spare_.empty()) return {};
    auto v = std::move(spare_.back());
    spare_.pop_back();
    return v;
  }

  void Submit(Job job) {
    std::lock_guard<std::mutex> lock(mutex_);
    // A frame that is still waiting is replaced: only the newest matters.
    while (queue_.size() >= 2) {
      spare_.push_back(std::move(queue_.front().frame));
      queue_.pop_front();
    }
    queue_.push_back(std::move(job));
    cv_.notify_one();
  }

 private:
  LrMovieWriter() {
    std::thread([this] { Run(); }).detach();
  }

  void Run() {
    for (;;) {
      Job job;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [&] { return !queue_.empty(); });
        job = std::move(queue_.front());
        queue_.pop_front();
      }
      LrMovieSession& s = *job.session;
      s.writing.store(true);
      g_lr_movie_trace.in_write.store(true, std::memory_order_relaxed);
      if (!s.closed.load()) {
        bool complete = true;
        for (const Plane& p : job.planes) {
          for (uint32_t y = 0; y < p.rows; ++y) {
            if (s.closed.load(std::memory_order_relaxed)) {
              complete = false;
              break;
            }
            std::memcpy(job.base + p.address + y * p.pitch, job.frame.data() + p.src_offset + size_t(y) * p.src_pitch,
                        p.cols);
          }
          if (!complete) break;
        }
        if (complete) {
          s.written.fetch_add(1);
          g_lr_movie_trace.shown.fetch_add(1, std::memory_order_relaxed);
          g_lr_movie_trace.last_write_us.store(lr_now_us(), std::memory_order_relaxed);
        }
      }
      g_lr_movie_trace.in_write.store(false, std::memory_order_relaxed);
      s.writing.store(false);
      std::lock_guard<std::mutex> lock(mutex_);
      if (spare_.size() < 4) spare_.push_back(std::move(job.frame));
    }
  }

  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<Job> queue_;
  std::vector<std::vector<uint8_t>> spare_;
};

std::mutex g_lr_movie_mutex;
std::unordered_map<uint32_t, std::shared_ptr<LrMovieSession>> g_lr_movies;  // by HBINK

std::shared_ptr<LrMovieSession> lr_movie_session(uint32_t bink) {
  std::lock_guard<std::mutex> lock(g_lr_movie_mutex);
  auto it = g_lr_movies.find(bink);
  return it == g_lr_movies.end() ? nullptr : it->second;
}

// Same crop as Lightning Returns' sub_82D1BDF0 (the XIII-2 movies are expected to be 1280x720).
uint32_t lr_movie_visible_height(uint32_t h) {
  switch (h) {
    case 768: case 736: return 720;
    case 384: case 368: return 360;
    case 192: case 184: return 180;
    default: return h;
  }
}

}  // namespace

// ---------------------------------------------------------------------------------------
// Japanese movie audio. The "previously on" recaps (tm_abst_*) have their narration in the
// movie's own Bink audio (four tracks: front, centre, LFE, and surround). With Japanese voices
// on, the converter has written the Steam Japanese audio as movies_jp\<name>.wav; the game's
// BinkSetVolume (sub_82C346E8(bink, track, volume), called for each track right after BinkOpen)
// is then made 0 for that movie, and the WAV plays from its first frame until BinkClose.
// ---------------------------------------------------------------------------------------
int X2VoiceAtStart();  // x2_settings.cpp

namespace {
const std::filesystem::path& x2_jp_movie_dir() {
  static const std::filesystem::path dir = X2InstallDir() / "movies_jp";
  return dir;
}

struct X2JpMovieAudio {
  std::string name;
  std::filesystem::path wav;
  bool started = false;
};
std::mutex g_jp_audio_mutex;
std::unordered_map<uint32_t, X2JpMovieAudio> g_jp_audio;  // by HBINK

#if defined(_WIN32)
using PlaySoundFn = BOOL(WINAPI*)(LPCWSTR, HMODULE, DWORD);
PlaySoundFn x2_play_sound() {
  static const PlaySoundFn fn = [] {
    HMODULE winmm = LoadLibraryW(L"winmm.dll");
    return winmm ? reinterpret_cast<PlaySoundFn>(GetProcAddress(winmm, "PlaySoundW")) : nullptr;
  }();
  return fn;
}
#endif

#if !defined(_WIN32)
// Elsewhere (Android, Linux): the WAV through an SDL audio stream on the default output.
std::mutex g_jp_stream_mutex;
SDL_AudioStream* g_jp_stream = nullptr;
#endif

void x2_jp_audio_stop() {
#if defined(_WIN32)
  if (PlaySoundFn play = x2_play_sound()) play(nullptr, nullptr, 0);
#else
  std::lock_guard<std::mutex> lock(g_jp_stream_mutex);
  if (g_jp_stream) SDL_DestroyAudioStream(g_jp_stream);
  g_jp_stream = nullptr;
#endif
}

bool x2_jp_audio_play(const std::filesystem::path& wav) {
#if defined(_WIN32)
  PlaySoundFn play = x2_play_sound();
  constexpr DWORD kFilename = 0x00020000, kAsync = 0x0001, kNoDefault = 0x0002;  // SND_*
  return play && play(wav.c_str(), nullptr, kFilename | kAsync | kNoDefault);
#else
  x2_jp_audio_stop();
  if (!SDL_WasInit(SDL_INIT_AUDIO) && !SDL_InitSubSystem(SDL_INIT_AUDIO)) return false;
  SDL_AudioSpec spec;
  Uint8* data = nullptr;
  Uint32 size = 0;
  if (!SDL_LoadWAV(wav.string().c_str(), &spec, &data, &size)) {
    REXLOG_WARN("XIII-2 movie audio: {}: {}", wav.string(), SDL_GetError());
    return false;
  }
  SDL_AudioStream* stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, nullptr, nullptr);
  bool ok = stream && SDL_PutAudioStreamData(stream, data, int(size)) && SDL_FlushAudioStream(stream) &&
            SDL_ResumeAudioStreamDevice(stream);
  SDL_free(data);
  if (!ok) {
    REXLOG_WARN("XIII-2 movie audio: could not open the audio output: {}", SDL_GetError());
    if (stream) SDL_DestroyAudioStream(stream);
    return false;
  }
  std::lock_guard<std::mutex> lock(g_jp_stream_mutex);
  g_jp_stream = stream;
  return true;
#endif
}

// BinkOpen of a known movie: use the Japanese audio for it if there is one.
void x2_jp_audio_open(uint32_t bink, const char* name, bool steam = false) {
  if (!steam && X2VoiceAtStart() != 1) return;
  std::filesystem::path wav = (steam ? X2InstallDir() / "movies_steam" : x2_jp_movie_dir()) /
                              (std::string(name) + ".wav");
  std::error_code ec;
  if (steam && X2VoiceAtStart() == 1) {
    const auto japanese = X2InstallDir() / "movies_steam" / (std::string(name) + "_jp.wav");
    if (std::filesystem::exists(japanese, ec)) wav = japanese;
  }
  if (!std::filesystem::exists(wav, ec)) return;
  std::lock_guard<std::mutex> lock(g_jp_audio_mutex);
  g_jp_audio[bink] = X2JpMovieAudio{name, wav, false};
  REXLOG_INFO("XIII-2 movie audio: {} plays {} ({})", name, steam ? "Steam movie audio" : "Japanese audio", wav.string());
}

bool x2_jp_audio_muted(uint32_t bink) {
  std::lock_guard<std::mutex> lock(g_jp_audio_mutex);
  return g_jp_audio.count(bink) != 0;
}

void x2_jp_audio_frame(uint32_t bink) {
  std::lock_guard<std::mutex> lock(g_jp_audio_mutex);
  auto it = g_jp_audio.find(bink);
  if (it == g_jp_audio.end() || it->second.started) return;
  it->second.started = true;
  if (!x2_jp_audio_play(it->second.wav))
    REXLOG_WARN("XIII-2 movie audio: could not play {}", it->second.wav.string());
}

void x2_jp_audio_close(uint32_t bink) {
  bool stop = false;
  {
    std::lock_guard<std::mutex> lock(g_jp_audio_mutex);
    auto it = g_jp_audio.find(bink);
    if (it != g_jp_audio.end()) {
      stop = it->second.started;
      g_jp_audio.erase(it);
    }
  }
  if (stop) x2_jp_audio_stop();
}
}  // namespace

REX_EXTERN(__imp__sub_82C346E8);
REX_HOOK_RAW(sub_82C346E8) {
  if (x2_jp_audio_muted(ctx.r3.u32)) ctx.r5.u64 = 0;
  __imp__sub_82C346E8(ctx, base);
}

REX_EXTERN(__imp__sub_82C36D38);
REX_HOOK_RAW(sub_82C36D38) {
  const uint32_t name_address = ctx.r3.u32;
  uint32_t handle = 0;
  uint64_t offset = 0;
  bool encoded = true;
  for (int i = 0; i < 24; ++i) {
    uint8_t c = base[name_address + i];
    if (c < 0x40 || c > 0x4F) {
      encoded = false;
      break;
    }
    if (i < 8) handle |= uint32_t(c - 0x40) << (4 * i);
    else offset |= uint64_t(c - 0x40) << (4 * (i - 8));
  }
  REXLOG_INFO("LR movie: BinkOpen (file code {:X})", offset);
  if (encoded && X2ConfigGetSkipStoryRecaps()) {
    for (const auto& movie : kLrMovies) {
      if (movie.code == uint32_t(offset) && std::strncmp(movie.name, "tm_abst_", 8) == 0) {
        // The ordinary movie player clears its pending-open flag on a null
        // handle (82874170). Automatic recaps then finish through that path.
        ctx.r3.u64 = 0;
        REXLOG_INFO("XIII-2 Story Recaps: {} skipped by Port Settings", movie.name);
        return;
      }
    }
  }
#if defined(LR_LOCAL_TEST_HARNESS)
  // LR_TEST_NO_MOVIES=1: every movie fails to open, so the game skips it (local test runs
  // stall in the opening movie, whose frames never decode here).
  if (const char* v = std::getenv("LR_TEST_NO_MOVIES"); v && v[0] == '1') {
    ctx.r3.u64 = 0;
    REXLOG_INFO("LR test: movie skipped (BinkOpen returns 0)");
    return;
  }
#endif
  __imp__sub_82C36D38(ctx, base);
  const uint32_t bink = ctx.r3.u32;
  REXLOG_INFO("LR movie: BinkOpen returned {:08X}", bink);
#if defined(LR_LOCAL_TEST_HARNESS)
  // LR_TEST_BINK_FRAMES=N: the movie ends after N frames (to reach a natural end quickly).
  if (const char* v = std::getenv("LR_TEST_BINK_FRAMES")) {
    if (bink) {
      uint32_t frames = __builtin_bswap32(uint32_t(std::strtoul(v, nullptr, 10)));
      std::memcpy(base + bink + 8, &frames, 4);
      REXLOG_INFO("LR test: movie shortened to {} frames", v);
    }
  }
#endif
  if (!bink || !encoded) return;

  const uint32_t width = lr_load_be32(base, bink + 0), height = lr_load_be32(base, bink + 4);
  const uint32_t frames = lr_load_be32(base, bink + 8);
  const uint32_t rate = lr_load_be32(base, bink + 20), rate_div = lr_load_be32(base, bink + 24);
  // Which movie: XIII-2 opens movies by file code (the low 32 bits of the name's second
  // part; the top bit is a flag), else by the file size, which HBINK holds in one of its first words.
  const LrMovieEntry* match = nullptr;
  bool steam = false;
  for (const auto& movie : x2_steam_movies()) {
    for (uint32_t word = 0; word < 32 && !match; ++word) {
      const uint32_t value = lr_load_be32(base, bink + word * 4);
      if (value + 16 >= movie.entry.size && value <= movie.entry.size + 16) {
        match = &movie.entry;
        steam = true;
      }
    }
  }
  for (const auto& m : kLrMovies) {
    if (match) break;
    if (m.code == uint32_t(offset)) match = &m;
  }
  for (size_t k = 0; !match && k < std::size(kLrMovies); ++k) {
    const LrMovieEntry* m = &kLrMovies[k];
    for (uint32_t w = 0; w < 32 && !match; ++w) {
      uint32_t v = lr_load_be32(base, bink + w * 4);
      if (v + 16 >= m->size && v <= m->size + 16) match = m;
    }
  }
  if (!match) {
    REXLOG_INFO("LR movie: unknown movie (file code {:X}, {}x{}, {} frames); playing the 360 video", offset,
                width, height, frames);
    return;
  }
  x2_jp_audio_open(bink, match->name, steam);
  if (!steam && lr_movie_dir().empty()) return;
  std::filesystem::path file = (steam ? X2InstallDir() / "movies_steam" : lr_movie_dir()) /
                                (std::string(match->name) + ".mp4");
  std::error_code ec;
  if (!std::filesystem::exists(file, ec)) {
    REXLOG_INFO("LR movie: {} - no PS3 file {}; playing the 360 video", match->name, file.string());
    return;
  }
  double fps = (rate && rate_div) ? double(rate) / double(rate_div) : 30000.0 / 1001.0;
  uint32_t visible = lr_movie_visible_height(height);
  auto session = std::make_shared<LrMovieSession>();
  session->movie = std::make_unique<LrPs3Movie>(match->name, file, width, visible, fps);
  session->movie->Start();
  REXLOG_INFO("LR movie: {} ({}x{}, {} frames, {:.3f} fps) -> {} video {}", match->name, width, height, frames, fps,
              steam ? "Steam" : "PS3",
              file.string());
  g_lr_movie_trace.name.store(match->name, std::memory_order_relaxed);
  g_lr_movie_trace.open.store(true, std::memory_order_relaxed);
  g_lr_movie_trace.frame.store(0);
  g_lr_movie_trace.shown.store(0);
  g_lr_movie_trace.late.store(0);
  g_lr_movie_trace.restarts.store(0);
  std::lock_guard<std::mutex> lock(g_lr_movie_mutex);
  g_lr_movies[bink] = std::move(session);
}

REX_EXTERN(__imp__sub_82C33830);
REX_HOOK_RAW(sub_82C33830) {
  const uint32_t bink = ctx.r3.u32, frame_buffers = ctx.r4.u32;
  __imp__sub_82C33830(ctx, base);
  if (auto s = lr_movie_session(bink)) s->frame_buffers = frame_buffers;
}

REX_EXTERN(__imp__sub_82C31978);
REX_HOOK_RAW(sub_82C31978) {
  const uint32_t bink = ctx.r3.u32;
  const int32_t wait_us = int32_t(ctx.r4.u32);
  __imp__sub_82C31978(ctx, base);
  if (wait_us != -1 && !ctx.r3.u32) return;  // still decoding
  x2_jp_audio_frame(bink);
  auto s = lr_movie_session(bink);
  if (!s || !s->frame_buffers) return;
  const uint32_t fb = s->frame_buffers;
  const uint32_t count = lr_load_be32(base, fb + 0);
  const uint32_t index = lr_load_be32(base, fb + 20);
  const uint32_t frame = lr_load_be32(base, bink + 12);
  if (index >= count || index >= 8) return;
  if (frame == s->last_frame && index == s->last_index) return;
  g_lr_movie_trace.frame.store(frame, std::memory_order_relaxed);
  g_lr_movie_trace.last_call_us.store(lr_now_us(), std::memory_order_relaxed);
  g_lr_movie_trace.in_get.store(true, std::memory_order_relaxed);
  const uint8_t* yuv = s->movie->Get(frame);
  g_lr_movie_trace.in_get.store(false, std::memory_order_relaxed);
  if (!yuv) {
    g_lr_movie_trace.late.fetch_add(1, std::memory_order_relaxed);
    return;
  }
  s->last_frame = frame;
  s->last_index = index;

  const uint32_t w = s->movie->width(), h = s->movie->height();
  const uint32_t set = fb + 24 + index * 48;
  const uint32_t yw = lr_load_be32(base, fb + 4), yh = lr_load_be32(base, fb + 8);
  const uint32_t cw = lr_load_be32(base, fb + 12), ch = lr_load_be32(base, fb + 16);
  // Some XIII-2 movies are stored at half width and/or half height (the Bink file's doubling
  // flags; HBINK still says 1280x720): the frame buffers are 1280x360 or 640x360 and the game
  // scales them up. The PS3 frame is then averaged down to the buffer's size (2x1, 1x2, or 2x2
  // pixels to one), so the game shows the whole picture as it does the 360 one.
  const uint32_t buf_h = lr_movie_visible_height(yh);
  const uint32_t fx = (yw && yw * 2 <= w && w % yw == 0) ? w / yw : 1;
  const uint32_t fy = (buf_h && buf_h * 2 <= h && h % buf_h == 0) ? h / buf_h : 1;
  const uint32_t dw = w / fx, dh = h / fy;
  if (s->logged == 0 && (fx > 1 || fy > 1))
    REXLOG_INFO("LR movie: {} frame buffers are {}x{}; the PS3 frames are scaled down {}x{} to fit", s->movie->name(),
                yw, yh, fx, fy);
  auto plane = [&](uint32_t plane_offset, uint32_t src_offset, uint32_t src_w, uint32_t src_h, uint32_t buf_w,
                   uint32_t buf_h) {
    LrMovieWriter::Plane p{};
    p.address = lr_load_be32(base, set + plane_offset + 4);
    p.pitch = lr_load_be32(base, set + plane_offset + 8);
    if (p.address && p.pitch) {
      p.cols = (std::min)({src_w, buf_w, p.pitch});
      p.rows = (std::min)(src_h, buf_h);
    }
    p.src_offset = src_offset;
    p.src_pitch = src_w;
    return p;
  };
  const size_t frame_bytes = size_t(dw) * dh * 3 / 2;
  LrMovieWriter& writer = LrMovieWriter::Get();
  LrMovieWriter::Job job;
  job.session = s;
  job.base = base;
  job.frame = writer.Spare();
  job.frame.resize(frame_bytes);
  // host memory: no GPU watch involved
  if (fx == 1 && fy == 1) {
    std::memcpy(job.frame.data(), yuv, frame_bytes);
  } else {
    // I420 planes, each averaged over fx by fy source pixels.
    auto shrink = [&](const uint8_t* src, uint32_t sw, uint32_t sh, uint8_t* dst) {
      const uint32_t ow = sw / fx, oh = sh / fy, n = fx * fy;
      for (uint32_t y = 0; y < oh; ++y) {
        const uint8_t* row = src + size_t(y) * fy * sw;
        uint8_t* out = dst + size_t(y) * ow;
        for (uint32_t x = 0; x < ow; ++x) {
          uint32_t sum = 0;
          for (uint32_t j = 0; j < fy; ++j)
            for (uint32_t i = 0; i < fx; ++i) sum += row[size_t(j) * sw + x * fx + i];
          out[x] = uint8_t((sum + n / 2) / n);
        }
      }
    };
    uint8_t* dst = job.frame.data();
    shrink(yuv, w, h, dst);
    shrink(yuv + size_t(w) * h, w / 2, h / 2, dst + size_t(dw) * dh);
    shrink(yuv + size_t(w) * h + size_t(w / 2) * (h / 2), w / 2, h / 2,
           dst + size_t(dw) * dh + size_t(dw / 2) * (dh / 2));
  }
  const uint32_t u_offset = dw * dh, v_offset = u_offset + (dw / 2) * (dh / 2);
  job.planes[0] = plane(0, 0, dw, dh, yw, yh);                    // Y
  job.planes[1] = plane(12, v_offset, dw / 2, dh / 2, cw, ch);    // Cr
  job.planes[2] = plane(24, u_offset, dw / 2, dh / 2, cw, ch);    // Cb
  writer.Submit(std::move(job));
  if (s->logged < 3) {
    ++s->logged;
    REXLOG_INFO("LR movie: {} frame {} -> buffer {} (Y {}x{} pitch {}, chroma {}x{})", s->movie->name(), frame, index,
                yw, yh, lr_load_be32(base, set + 8), cw, ch);
  }
}

REX_EXTERN(__imp__sub_82C37930);
REX_HOOK_RAW(sub_82C37930) {
  const uint32_t bink = ctx.r3.u32;
  std::shared_ptr<LrMovieSession> s;
  {
    std::lock_guard<std::mutex> lock(g_lr_movie_mutex);
    auto it = g_lr_movies.find(bink);
    if (it != g_lr_movies.end()) {
      s = std::move(it->second);
      g_lr_movies.erase(it);
    }
  }
  if (s) {
    s->closed.store(true);
    // The writer checks `closed` before every row; give an in-flight copy a moment to stop.
    // Bounded, so this can never hang the game.
    for (int i = 0; i < 200 && s->writing.load(); ++i) std::this_thread::sleep_for(std::chrono::microseconds(250));
    REXLOG_INFO("LR movie: {} closed; {} PS3 frames shown, {} late, {} after the PS3 video ended",
                s->movie->name(), s->written.load(), s->movie->late(), s->movie->after_end());
    // The decoder is stopped on a background thread (LrPs3Movie's destructor retires it).
    s->movie.reset();
  }
  x2_jp_audio_close(bink);
  __imp__sub_82C37930(ctx, base);
  g_lr_movie_trace.open.store(false, std::memory_order_relaxed);
  REXLOG_INFO("LR movie: BinkClose done");
}


// ---- DLC movies the installed package does not have ------------------------------------------
// The converted Steam DLC currently lacks five Bundle Pack movies indexed in Steam's movie_win/
// (tm_tevb_050: Sazh's cockpit scene, tm_vazb_013 / 038, em_vazb_030 / 055). A cutscene that plays
// one (ev_cszb_011) never opened it, so its end clip never ran and the game stayed in movie mode:
// no 3D drawn, black from then on. The game's own movie_disable option (byte 82C78AFC, set from
// the command line) makes the movie clips run on a timer instead (open / play / end, the scene goes
// on after the movie's length); it is set here around the clips of a missing movie only.
//   clip methods (vtable slots of the movie clip classes) that read the option:
//     sub_827792D8 / sub_82779270 (open), sub_82779EA0 / sub_82779D68 (play),
//     sub_82777370 / sub_82777478 (end)
//   the clip's record: [this + 20]; the movie's name (as in the schedule, 'tm_tevb_050') at +16
// The names: x2_missing_movies.txt in the user data folder, written by the converter when it
// installs a package without them.
std::filesystem::path X2SettingsDir();  // x2_hooks.cpp: the user data folder

namespace {
constexpr uint32_t kX2MovieDisable = 0x82C78AFC;

const std::unordered_set<std::string>& x2_missing_movies() {
  static const std::unordered_set<std::string> names = [] {
    std::unordered_set<std::string> s;
    std::FILE* f = std::fopen((X2SettingsDir() / "x2_missing_movies.txt").string().c_str(), "rb");
    if (!f) return s;
    char line[128];
    while (std::fgets(line, sizeof line, f)) {
      std::string n(line);
      while (!n.empty() && (n.back() == '\n' || n.back() == '\r' || n.back() == ' ')) n.pop_back();
      if (!n.empty()) s.insert(n);
    }
    std::fclose(f);
    REXLOG_INFO("XIII-2 movies: {} DLC movie(s) not in the installed packages; their cutscene clips run without them", s.size());
    return s;
  }();
  return names;
}

// The movie name of a movie clip whose movie is missing (empty: play it as usual).
std::string x2_missing_clip_movie(uint8_t* base, uint32_t clip, uint32_t method) {
  if (x2_missing_movies().empty() || clip < 0x10000 || (clip & 3)) return {};
  const uint32_t vt = lr_load_be32(base, clip);
  if (vt < 0x82000000 || vt >= 0x82400000 || (vt & 3)) return {};
  bool ours = false;
  for (uint32_t i = 0; i < 32 && !ours; ++i) ours = lr_load_be32(base, vt + 4 * i) == method;
  if (!ours) return {};
  const uint32_t rec = lr_load_be32(base, clip + 20);
  if (rec < 0x40000000 || rec >= 0xC0000000) return {};
  std::string name;
  for (uint32_t i = 0; i < 32; ++i) {
    const char c = char(base[rec + 16 + i]);
    if (!c) break;
    if (c < 0x20 || c > 0x7E) return {};
    name.push_back(c);
  }
  return x2_missing_movies().count(name) ? name : std::string();
}

struct X2MovieClipSkip {
  uint8_t* base;
  bool on = false;
  uint8_t old = 0;
  X2MovieClipSkip(uint8_t* b, uint32_t clip, uint32_t method, const char* what) : base(b) {
    const std::string name = x2_missing_clip_movie(b, clip, method);
    if (name.empty()) return;
    old = base[kX2MovieDisable];
    base[kX2MovieDisable] = 1;
    on = true;
    static std::atomic<uint32_t> n{0};
    if (n.fetch_add(1) < 32) REXLOG_INFO("XIII-2 movies: {} - not in the package; {} runs without it", name, what);
  }
  ~X2MovieClipSkip() {
    if (on) base[kX2MovieDisable] = old;
  }
};
}  // namespace

#define X2_MOVIE_CLIP(fn, address, what)               \
  REX_EXTERN(__imp__##fn);                             \
  REX_HOOK_RAW(fn) {                                   \
    X2MovieClipSkip skip(base, ctx.r3.u32, address, what); \
    __imp__##fn(ctx, base);                            \
  }
X2_MOVIE_CLIP(sub_827792D8, 0x827792D8u, "its open clip")
X2_MOVIE_CLIP(sub_82779270, 0x82779270u, "its open clip")
X2_MOVIE_CLIP(sub_82779EA0, 0x82779EA0u, "its play clip")
X2_MOVIE_CLIP(sub_82779D68, 0x82779D68u, "its play clip")
X2_MOVIE_CLIP(sub_82777370, 0x82777370u, "its end clip")
X2_MOVIE_CLIP(sub_82777478, 0x82777478u, "its end clip")
#undef X2_MOVIE_CLIP
