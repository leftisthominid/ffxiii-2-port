// ffxiii2 - guest function hooks (FFXIII-2 test build)
//
// Each hook replaces one weak recompiled function and calls the original through its
// __imp__ name. Ported from the Lightning Returns port (lr_hooks.cpp); the addresses are
// XIII-2's, found by matching the two games' code.

#include <cstdint>
#include <cstring>
#include <cctype>
#include <cstdlib>

#include <rex/hook.h>
#include <rex/thread/mutex.h>

// Same definitions as lr_guest_irq.inc puts in the generated header, so both name the same
// per-thread state.
struct LrLockSpin {
  uint64_t last_leave = 0;
  uint32_t rapid = 0;
  uint32_t guest_depth = 0;
  uint32_t host_holds = 0;
};

inline LrLockSpin& lr_lock_spin() {
  thread_local LrLockSpin spin;
  return spin;
}

REX_EXTERN(__imp__sub_828510C8);

// sub_828510C8 is the graphics interrupt handler the game registers with
// VdSetGraphicsInterruptCallback (Lightning Returns: sub_829C4A60, the same code). ReXGlue runs
// it through FunctionDispatcher::ExecuteInterrupt, which holds the runtime's global mutex for
// the whole call. The handler takes kernel spinlocks, and ReXGlue's KfAcquireSpinLock spins in
// host code. A game thread that holds one of those spinlocks and then makes any kernel call or
// writes memory the renderer watches needs the global mutex: the handler spins forever holding
// it and the game thread waits forever for it, so no frame is presented again (the loading
// screen freeze after the opening battles: the GPU thread entering "interrupts off" 21 million
// times a second inside the handler).
// In the default lock mode guest code no longer takes the global mutex for its "interrupts
// off" sections, so holding it around the handler protects nothing on the guest side; the
// handler runs without it, as interrupts on the console run alongside the other hardware
// threads. LR_LOCK_MODE=global keeps ReXGlue's behaviour (and counts the hold, so a guest
// spin inside the handler can hand it over).
REX_HOOK_RAW(sub_828510C8) {
  static const bool global_mode = [] {
    const char* mode = std::getenv("LR_LOCK_MODE");
    return mode && (mode[0] == 'g' || mode[0] == 'G');
  }();
  if (global_mode) {
    auto& spin = lr_lock_spin();
    ++spin.host_holds;
    __imp__sub_828510C8(ctx, base);
    --spin.host_holds;
    return;
  }
  auto& global = rex::thread::global_critical_region::mutex();
  global.unlock();
  __imp__sub_828510C8(ctx, base);
  global.lock();
}

// ---------------------------------------------------------------------------------------
// Saves: one log line per call of the game's own wrappers around the runtime's content
// (save) functions, so a play log shows what the Save screen asks for and gets back (the
// runtime itself logs these only at debug level). The first 64 calls of each are logged.
// ---------------------------------------------------------------------------------------
#include <atomic>
#include <cctype>
#include <filesystem>
#include <string>
#include <string_view>

#include <rex/filesystem.h>
#include <rex/logging/macros.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>
#include "x2_module_paths.h"
#include "x2_audio_pool.h"

namespace {
uint32_t x2_be32(uint8_t* base, uint32_t address) {
  return address ? __builtin_bswap32(*reinterpret_cast<uint32_t*>(base + address)) : 0;
}

std::string x2_cstr(uint8_t* base, uint32_t p, size_t max) {
  std::string s;
  for (size_t i = 0; p && i < max && base[p + i]; ++i) {
    char c = char(base[p + i]);
    s.push_back((c >= 32 && c < 127) ? c : '.');
  }
  return s;
}

// A big-endian UTF-16 guest string, non-ASCII characters as '?'.
std::string x2_wstr(uint8_t* base, uint32_t p, size_t max) {
  std::string s;
  for (size_t i = 0; p && i < max; ++i) {
    uint16_t c = uint16_t(base[p + 2 * i] << 8 | base[p + 2 * i + 1]);
    if (!c) break;
    s.push_back((c >= 32 && c < 127) ? char(c) : (c == '\n' ? ' ' : '?'));
  }
  return s;
}

bool x2_log_slot(std::atomic<uint32_t>& count) { return count.fetch_add(1) < 64; }
}  // namespace

// LoadLibraryA / LoadLibraryEx wrappers: preserve their flags and return values,
// changing only the spelling of the four statically recompiled zone paths.
// The runtime can then unregister and unload the old module through its normal
// XexUnloadImage path, including DllMain and thunk-cache cleanup.
REX_EXTERN(__imp__sub_82B63F90);
REX_EXTERN(__imp__sub_82B63FD8);
namespace {
void x2_load_module(PPCContext& ctx, uint8_t* base, PPCFunc* original) {
  const std::string requested = x2_cstr(base, ctx.r3.u32, 512);
  auto* kernel = REX_KERNEL_STATE();
  const std::string resolved = X2ModuleLoadPath(kernel, requested);
  if (resolved == requested) { original(ctx, base); return; }
  const uint32_t guest = kernel->memory()->SystemHeapAlloc(uint32_t(resolved.size() + 1));
  if (!guest) { original(ctx, base); return; }
  struct Release {
    rex::memory::Memory* memory;
    uint32_t guest;
    ~Release() { memory->SystemHeapFree(guest); }
  } release{kernel->memory(), guest};
  std::memcpy(base + guest, resolved.c_str(), resolved.size() + 1);
  REXLOG_INFO("XIII-2 zone module: {} -> {} (matching load/unload identity)", requested, resolved);
  ctx.r3.u64 = guest;
  original(ctx, base);
}
}  // namespace
REX_HOOK_RAW(sub_82B63F90) { x2_load_module(ctx, base, __imp__sub_82B63F90); }
REX_HOOK_RAW(sub_82B63FD8) { x2_load_module(ctx, base, __imp__sub_82B63FD8); }

REX_EXTERN(__imp__sub_82A0E3F0);
REX_HOOK_RAW(sub_82A0E3F0) {
  const uint32_t bytes = X2AudioPoolBytes(uint32_t(ctx.lr), ctx.r3.u32);
  if (bytes != ctx.r3.u32) {
    REXLOG_INFO("XIII-2 audio: Ogg input-buffer pool {} -> {} bytes", ctx.r3.u32, bytes);
    ctx.r3.u64 = bytes;
  }
  __imp__sub_82A0E3F0(ctx, base);
}

// XamContentCreateEx(user r3, root name r4, XCONTENT_DATA* r5, flags r6, disposition* r7,
// license* r8, ...); XCONTENT_DATA: +0 device id, +4 content type, +264 file name[42].
REX_EXTERN(__imp__sub_82B61F40);
REX_HOOK_RAW(sub_82B61F40) {
  uint32_t user = ctx.r3.u32, flags = ctx.r6.u32, data = ctx.r5.u32, disposition = ctx.r7.u32;
  std::string root = x2_cstr(base, ctx.r4.u32, 15), file = x2_cstr(base, data ? data + 264 : 0, 42);
  uint32_t device = x2_be32(base, data), type = data ? x2_be32(base, data + 4) : 0;
  __imp__sub_82B61F40(ctx, base);
  static std::atomic<uint32_t> n{0}, n_dlc{0};
  if (type == 2 ? x2_log_slot(n_dlc) : x2_log_slot(n)) {
    REXLOG_INFO("XIII-2 saves: XamContentCreateEx(user {}, '{}' as {}, device {:08X}, type {}, flags {:08X}) -> {:08X}, disposition {}",
                user, file, root, device, type, flags, ctx.r3.u32, x2_be32(base, disposition));
  }
}

// CreateFileA (sub_82B63998: path r3, access r4, share r5, security r6, disposition r7,
// flags r8, template r9): the downloadable content's files (DLC:, UPDATE:, udp, dlc, identifier)
// with the result, so a log shows what the game's DLC code looks for and whether it is there.
// The DLC packages' archives with Japanese voices (made by XIII-2_Test.ps1 from the Steam version, which
// has the DLC in its own archives: x2_jpvoice --dlc) are in jpvoice_dlc\NNNNNNN\ next to the exe,
// reached as X2JPDLC:\ (ffxiii2_app.h). When the game opens a package's file list or archive
// ("NNNNNNN:\filelist_pNNNNNNN..." / "white_p...") while the voices are Japanese, the copy is opened
// instead.
int X2VoiceAtStart();
bool X2JpDlcMounted();
std::filesystem::path X2InstallDir();  // x2_settings.cpp
namespace {
std::string x2_jp_dlc_path(const std::string& path) {
  if (path.size() < 20 || path[7] != ':' || path[8] != '\\' || !X2JpDlcMounted() || !X2VoiceAtStart()) return {};
  for (int i = 0; i < 7; ++i)
    if (!std::isdigit(static_cast<unsigned char>(path[i]))) return {};
  const std::string name = path.substr(9);
  if (name.rfind("filelist_p", 0) != 0 && name.rfind("white_p", 0) != 0) return {};
  const std::string id = path.substr(0, 7);
  std::error_code ec;
  if (!std::filesystem::exists(X2InstallDir() / "jpvoice_dlc" / id / name, ec)) return {};
  return "X2JPDLC:\\" + id + "\\" + name;
}
}  // namespace

REX_EXTERN(__imp__sub_82B63998);
REX_HOOK_RAW(sub_82B63998) {
  const std::string path = x2_cstr(base, ctx.r3.u32, 200);
  if (const std::string jp = x2_jp_dlc_path(path); !jp.empty()) {
    static uint32_t buffer = 0;
    if (!buffer) buffer = rex::system::kernel_state()->memory()->SystemHeapAlloc(256);
    if (buffer && jp.size() < 256) {
      std::memcpy(base + buffer, jp.c_str(), jp.size() + 1);
      const PPCContext saved = ctx;
      ctx.r3.u64 = buffer;
      __imp__sub_82B63998(ctx, base);
      static std::atomic<uint32_t> n{0};
      if (n.fetch_add(1) < 32)
        REXLOG_INFO("XIII-2 DLC files: CreateFile('{}') -> Japanese voices '{}' -> {}", path, jp,
                    ctx.r3.u32 == 0xFFFFFFFFu ? std::string("not found") : fmt::format("handle {:08X}", ctx.r3.u32));
      if (ctx.r3.u32 != 0xFFFFFFFFu) return;
      ctx = saved;  // the copy did not open: the package's own file
    }
  }
  __imp__sub_82B63998(ctx, base);
  std::string lower = path;
  for (auto& c : lower) c = char(std::tolower(static_cast<unsigned char>(c)));
  if (lower.find("dlc") == std::string::npos && lower.find("update") == std::string::npos &&
      lower.find("pkg") == std::string::npos && lower.find("_p00") == std::string::npos &&
      lower.find("udp") == std::string::npos && lower.find("identifier") == std::string::npos)
    return;
  static std::atomic<uint32_t> n{0};
  if (n.fetch_add(1) < 200)
    REXLOG_INFO("XIII-2 DLC files: CreateFile('{}') -> {}", path, ctx.r3.u32 == 0xFFFFFFFFu ? std::string("not found") : fmt::format("handle {:08X}", ctx.r3.u32));
}

// XamContentCreateEnumerator(user r3, device r4, content type r5, flags r6, items r7,
// buffer size* r8, handle* r9).
REX_EXTERN(__imp__sub_82B61FD8);
REX_HOOK_RAW(sub_82B61FD8) {
  uint32_t user = ctx.r3.u32, device = ctx.r4.u32, type = ctx.r5.u32, flags = ctx.r6.u32, items = ctx.r7.u32;
  uint32_t size_ptr = ctx.r8.u32, handle_ptr = ctx.r9.u32;
  __imp__sub_82B61FD8(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) {
    REXLOG_INFO("XIII-2 saves: XamContentCreateEnumerator(user {}, device {:08X}, type {}, flags {:08X}, items {}) -> {:08X}, buffer {}, handle {:08X}",
                user, device, type, flags, items, ctx.r3.u32, x2_be32(base, size_ptr), x2_be32(base, handle_ptr));
  }
}

// XEnumerate(handle r3, buffer r4, size r5, items returned* r6, overlapped r7): the game's
// wrapper, which calls XamEnumerate with flags 0 inserted after the handle.
REX_EXTERN(__imp__sub_82B63ED0);
REX_HOOK_RAW(sub_82B63ED0) {
  uint32_t handle = ctx.r3.u32, buffer = ctx.r4.u32, items_ptr = ctx.r6.u32, overlapped = ctx.r7.u32;
  __imp__sub_82B63ED0(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) {
    std::string first = buffer ? x2_cstr(base, buffer + 264, 42) : std::string();
    REXLOG_INFO("XIII-2 saves: XEnumerate(handle {:08X}{}) -> {:08X}, items {}, first '{}'", handle,
                overlapped ? ", overlapped" : "", ctx.r3.u32, x2_be32(base, items_ptr), first);
  }
}

// XamContentDelete(user r3, XCONTENT_DATA* r4, overlapped r5).
REX_EXTERN(__imp__sub_82B61FC8);
REX_HOOK_RAW(sub_82B61FC8) {
  std::string file = x2_cstr(base, ctx.r4.u32 ? ctx.r4.u32 + 264 : 0, 42);
  __imp__sub_82B61FC8(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) REXLOG_INFO("XIII-2 saves: XamContentDelete('{}') -> {:08X}", file, ctx.r3.u32);
}

// XamShowDeviceSelectorUI(user r3, content type r4, flags r5, bytes r6 (64-bit),
// device id* r7, overlapped r8).
REX_EXTERN(__imp__sub_82B61F10);
REX_HOOK_RAW(sub_82B61F10) {
  uint32_t user = ctx.r3.u32, type = ctx.r4.u32, flags = ctx.r5.u32, device_ptr = ctx.r7.u32;
  uint64_t bytes = ctx.r6.u64;
  __imp__sub_82B61F10(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) {
    REXLOG_INFO("XIII-2 saves: XamShowDeviceSelectorUI(user {}, type {}, flags {:08X}, bytes {}) -> {:08X}, device {:08X}",
                user, type, flags, bytes, ctx.r3.u32, x2_be32(base, device_ptr));
  }
}

// XamContentGetDeviceState(device r3, overlapped r4).
REX_EXTERN(__imp__sub_82B61FE0);
REX_HOOK_RAW(sub_82B61FE0) {
  uint32_t device = ctx.r3.u32;
  __imp__sub_82B61FE0(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) REXLOG_INFO("XIII-2 saves: XamContentGetDeviceState(device {:08X}) -> {:08X}", device, ctx.r3.u32);
}

// XamContentGetDeviceData(device r3, data* r4).
REX_EXTERN(__imp__sub_82B61FE8);
REX_HOOK_RAW(sub_82B61FE8) {
  uint32_t device = ctx.r3.u32;
  __imp__sub_82B61FE8(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) REXLOG_INFO("XIII-2 saves: XamContentGetDeviceData(device {:08X}) -> {:08X}", device, ctx.r3.u32);
}

// XamShowMessageBoxUI(user r3, title r4, text r5, buttons r6, ...): system message boxes
// (a save error may come as one).
REX_EXTERN(__imp__sub_82B61F18);
REX_HOOK_RAW(sub_82B61F18) {
  std::string title = x2_wstr(base, ctx.r4.u32, 80), text = x2_wstr(base, ctx.r5.u32, 300);
  __imp__sub_82B61F18(ctx, base);
  static std::atomic<uint32_t> n{0};
  if (x2_log_slot(n)) REXLOG_INFO("XIII-2 saves: XamShowMessageBoxUI('{}': '{}') -> {:08X}", title, text, ctx.r3.u32);
}

// ---------------------------------------------------------------------------------------
// Settings files: one value per file in the settings folder (the user data folder), as in the
// Lightning Returns port (resolution.txt, ...).
// ---------------------------------------------------------------------------------------
#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <thread>
#include <vector>

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/platform/env.h>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")
#else
#include <signal.h>
#include <spawn.h>
#include <unistd.h>
extern char** environ;
#endif

namespace {
std::filesystem::path g_x2_settings_dir;

std::string x2_read_setting(const char* name) {
  std::ifstream in(g_x2_settings_dir / name);
  std::string v;
  std::getline(in, v);
  while (!v.empty() && (v.back() == '\r' || v.back() == ' ' || v.back() == '\n')) v.pop_back();
  return v;
}

void x2_write_setting(const char* name, const std::string& value) {
  std::error_code ec;
  std::filesystem::create_directories(g_x2_settings_dir, ec);
  std::ofstream out(g_x2_settings_dir / name, std::ios::trunc);
  out << value << "\n";
}
}  // namespace

// For the other files (x2_settings.cpp): the settings folder (the user data root) and its
// one-line setting files.
std::filesystem::path X2SettingsDir() { return g_x2_settings_dir; }
std::string X2ReadSetting(const char* name) { return x2_read_setting(name); }
void X2WriteSetting(const char* name, const std::string& value) { x2_write_setting(name, value); }

// ---------------------------------------------------------------------------------------
// Quit to title. The game's main loop (sub_829142D8; Lightning Returns: sub_82ABEE80) runs
// sub_82910538(app) each frame and stops when app+38 is set; app+39 set as well means
// "restart" (back to the title screen): it then calls the app's shutdown and XLaunchNewImage
// (sub_82913EC8). In Lightning Returns that shutdown never finishes (a freeze when quitting to
// the title), and it only tears down what the process exit discards anyway - so a new process
// is started as soon as the game asks to leave, with the same arguments (the log file renamed
// to <log>_relaunch<N>.log); it waits for this one to be gone (LR_WAIT_PID).
// ---------------------------------------------------------------------------------------
namespace {
struct X2RelaunchWait {
  X2RelaunchWait() {
    const char* v = std::getenv("LR_WAIT_PID");
    if (!v || !*v) return;
    unsigned long pid = std::strtoul(v, nullptr, 10);
#if defined(_WIN32)
    SetEnvironmentVariableW(L"LR_WAIT_PID", nullptr);
    if (HANDLE h = OpenProcess(SYNCHRONIZE, FALSE, DWORD(pid))) {
      WaitForSingleObject(h, 15000);
      CloseHandle(h);
    }
#else
    unsetenv("LR_WAIT_PID");
    for (int i = 0; i < 1500 && pid && kill(pid_t(pid), 0) == 0; ++i) usleep(10000);
#endif
  }
};
X2RelaunchWait g_x2_relaunch_wait;

int x2_relaunch_generation() {
  const char* v = std::getenv("LR_RELAUNCH");
  return v ? std::atoi(v) : 0;
}

// <dir>/<stem>_relaunch<N><ext>, from the original log name.
std::filesystem::path x2_relaunch_log_name(const std::filesystem::path& log, int generation) {
  std::string stem = log.stem().string();
  if (auto pos = stem.find("_relaunch"); pos != std::string::npos) stem.resize(pos);
  return log.parent_path() / (stem + "_relaunch" + std::to_string(generation) + log.extension().string());
}

bool x2_restart_process() {
  const int generation = x2_relaunch_generation() + 1;
#if defined(_WIN32)
  wchar_t exe[MAX_PATH];
  DWORD n = GetModuleFileNameW(nullptr, exe, MAX_PATH);
  if (!n || n >= MAX_PATH) return false;
  int argc = 0;
  LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  if (!argv) return false;
  // Quoting that CommandLineToArgvW / the CRT read back as the same argument.
  auto quote = [](const std::wstring& a) {
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t c : a) {
      if (c == L'\\') {
        ++slashes;
        continue;
      }
      out.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
      slashes = 0;
      out.push_back(c);
    }
    out.append(slashes * 2, L'\\');
    out.push_back(L'"');
    return out;
  };
  std::wstring cmd = quote(exe);
  const std::wstring log_flag = L"--log_file=";
  for (int i = 1; i < argc; ++i) {
    std::wstring a = argv[i];
    if (a.rfind(log_flag, 0) == 0) a = log_flag + x2_relaunch_log_name(a.substr(log_flag.size()), generation).wstring();
    cmd += L" " + quote(a);
  }
  LocalFree(argv);
  SetEnvironmentVariableW(L"LR_WAIT_PID", std::to_wstring(GetCurrentProcessId()).c_str());
  SetEnvironmentVariableW(L"LR_RELAUNCH", std::to_wstring(generation).c_str());
  STARTUPINFOW si{};
  si.cb = sizeof(si);
  PROCESS_INFORMATION pi{};
  BOOL ok = CreateProcessW(exe, cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi);
  SetEnvironmentVariableW(L"LR_WAIT_PID", nullptr);
  if (!ok) return false;
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  return true;
#elif defined(__ANDROID__)
  return X2AndroidRestartProcess(generation);  // android/game/x2_android_main.cpp
#else
  std::vector<std::string> args;
  if (FILE* f = std::fopen("/proc/self/cmdline", "rb")) {
    std::string cur;
    for (int c; (c = std::fgetc(f)) != EOF;) {
      if (c == 0) {
        args.push_back(cur);
        cur.clear();
      } else {
        cur.push_back(char(c));
      }
    }
    std::fclose(f);
  }
  if (args.empty()) return false;
  const std::string log_flag = "--log_file=";
  for (size_t i = 1; i < args.size(); ++i) {
    if (args[i].rfind(log_flag, 0) == 0)
      args[i] = log_flag + x2_relaunch_log_name(args[i].substr(log_flag.size()), generation).string();
  }
  std::vector<char*> argv;
  for (auto& a : args) argv.push_back(a.data());
  argv.push_back(nullptr);
  setenv("LR_WAIT_PID", std::to_string(getpid()).c_str(), 1);
  setenv("LR_RELAUNCH", std::to_string(generation).c_str(), 1);
  pid_t pid = 0;
  int rc = posix_spawn(&pid, "/proc/self/exe", nullptr, nullptr, argv.data(), environ);
  unsetenv("LR_WAIT_PID");
  return rc == 0;
#endif
}

[[noreturn]] void x2_end_process() {
  std::fflush(nullptr);
#if defined(_WIN32)
  TerminateProcess(GetCurrentProcess(), 0);
#endif
  std::_Exit(0);
}

std::atomic<bool> g_x2_relaunching{false};

// Frame overlay (F2, ffxiii2_app.h): the start time of each of the game's frames, written by
// the main loop below (one writer), read by the UI thread.
constexpr uint32_t kX2FrameRing = 512;
std::array<std::atomic<uint64_t>, kX2FrameRing> g_x2_frame_start_ns{};
std::atomic<uint32_t> g_x2_frame_starts{0};

void x2_resolution_frame(uint32_t frames);
}  // namespace

int X2RecentFrameTimesMs(float* out, int max) {
  uint32_t n = g_x2_frame_starts.load(std::memory_order_acquire);
  uint32_t frames = std::min<uint32_t>({n ? n - 1 : 0, uint32_t(max), kX2FrameRing - 1});
  for (uint32_t k = 0; k < frames; ++k) {
    uint32_t i = n - frames + k;  // frame i ends where frame i + 1 starts
    uint64_t a = g_x2_frame_start_ns[(i - 1) % kX2FrameRing].load(std::memory_order_relaxed);
    uint64_t b = g_x2_frame_start_ns[i % kX2FrameRing].load(std::memory_order_relaxed);
    out[k] = b > a ? float(double(b - a) / 1e6) : 0.0f;
  }
  return int(frames);
}

void X2SettingsFrame(uint8_t* base);  // x2_settings.cpp
void X2InstallCrashReport();         // x2_perf.cpp
void X2HangFrame(const PPCContext* ctx);  // x2_perf.cpp
#if defined(__ANDROID__)
void X2AndroidFrameStart();  // x2_android.cpp
#endif

REX_EXTERN(__imp__sub_82910538);
REX_HOOK_RAW(sub_82910538) {
  uint32_t app = ctx.r3.u32;
  uint32_t i = g_x2_frame_starts.load(std::memory_order_relaxed);
  g_x2_frame_start_ns[i % kX2FrameRing].store(
      uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                   std::chrono::steady_clock::now().time_since_epoch()).count()),
      std::memory_order_relaxed);
  g_x2_frame_starts.store(i + 1, std::memory_order_release);
  x2_resolution_frame(i + 1);
  X2InstallCrashReport();
  X2HangFrame(&ctx);
  X2SettingsFrame(base);
#if defined(__ANDROID__)
  X2AndroidFrameStart();  // x2_android.cpp
#endif
  __imp__sub_82910538(ctx, base);
  if (!app || !base[app + 38] || !base[app + 39] || std::getenv("LR_NO_RELAUNCH")) return;
  if (g_x2_relaunching.exchange(true)) return;
  REXLOG_INFO("XIII-2 relaunch: the game is leaving to the title screen; starting a new process now "
              "(the game's own shutdown is skipped)");
  ::rex::FlushLogging();
  if (x2_restart_process()) x2_end_process();
  REXLOG_WARN("XIII-2 relaunch: could not start a new process; letting the game shut down");
  g_x2_relaunching.store(false);
}

// ---------------------------------------------------------------------------------------
// Render resolution. The renderer reads its draw resolution scale (cvar resolution_scale:
// 1 = 1280x720, 2 = 2560x1440, 3 = 3840x2160) once, when the plugin starts; X2ResolutionStartup
// runs before the plugin is loaded (OnPreSetup) and hands the choice over as
// REX_RESOLUTION_SCALE. Choice: LR_RESOLUTION (saved), else resolution.txt in the settings
// folder: auto, 720, 1440, or 2160. Auto = the tallest display's height / 720 rounded up, at
// most 3 (a 1080p display renders at 1440p, scaled down), as in the Lightning Returns port.
// Safe mode: a resolution is on trial until the game has run 600 frames with it; if the last
// start with it never got there, this start goes back to the last one that did.
// (A Settings menu item comes with the XIII-2 menu work.)
// ---------------------------------------------------------------------------------------
namespace {
constexpr const char* kX2ResolutionNames[] = {"auto", "720", "1440", "2160"};
std::atomic<int> g_x2_res_choice{0};

int x2_res_parse(std::string v) {
  while (!v.empty() && std::isspace(static_cast<unsigned char>(v.back()))) v.pop_back();
  if (v == "720" || v == "720p" || v == "1") return 1;
  if (v == "1440" || v == "1440p" || v == "2") return 2;
  if (v == "2160" || v == "2160p" || v == "4k" || v == "4K" || v == "3") return 3;
  return 0;
}

// Height of the tallest display attached to the desktop, 0 when not known.
uint32_t x2_display_height() {
#if defined(_WIN32)
  uint32_t tallest = 0;
  DISPLAY_DEVICEW device = {};
  device.cb = sizeof(device);
  for (DWORD i = 0; EnumDisplayDevicesW(nullptr, i, &device, 0); ++i) {
    if (device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP) {
      DEVMODEW mode = {};
      mode.dmSize = sizeof(mode);
      if (EnumDisplaySettingsW(device.DeviceName, ENUM_CURRENT_SETTINGS, &mode))
        tallest = std::max(tallest, uint32_t(mode.dmPelsHeight));
    }
    device = {};
    device.cb = sizeof(device);
  }
  return tallest;
#else
  return 0;
#endif
}

int x2_resolution_scale_for(int choice) {
#if defined(__ANDROID__)
  return choice == 2 ? 2 : 1;  // Android: 1440p when chosen; else 720p (Auto, 720p, and 2160p)
#endif
  if (choice >= 1 && choice <= 3) return choice;
  uint32_t height = x2_display_height();
  if (!height) return 0;
  return int(std::min<uint32_t>(3, std::max<uint32_t>(1, (height + 719) / 720)));
}

void x2_resolution_frame(uint32_t frames) {
  if (frames != 600) return;
  const char* name = kX2ResolutionNames[g_x2_res_choice.load()];
  if (x2_read_setting("resolution_ok.txt") != name) x2_write_setting("resolution_ok.txt", name);
  x2_write_setting("resolution_trial.txt", "");
}
}  // namespace

void X2ResolutionStartup(const std::filesystem::path& settings_dir) {
  g_x2_settings_dir = settings_dir;
  int choice;
  auto requested = rex::platform::env::get("LR_RESOLUTION");
  if (requested && !requested->empty()) {
    choice = x2_res_parse(*requested);
    x2_write_setting("resolution.txt", kX2ResolutionNames[choice]);
    rex::platform::env::unset("LR_RESOLUTION");
  } else {
    choice = x2_res_parse(x2_read_setting("resolution.txt"));
  }
  const std::string good = x2_read_setting("resolution_ok.txt");
  const std::string trial = x2_read_setting("resolution_trial.txt");
  const int good_choice = good.empty() ? 0 : x2_res_parse(good);
  if (choice != good_choice && trial == kX2ResolutionNames[choice]) {
    REXLOG_WARN("XIII-2 resolution: the last start with {} did not get going; back to {}", kX2ResolutionNames[choice],
                kX2ResolutionNames[good_choice]);
    choice = good_choice;
    x2_write_setting("resolution.txt", kX2ResolutionNames[choice]);
    x2_write_setting("resolution_trial.txt", "");
  } else if (choice != good_choice) {
    x2_write_setting("resolution_trial.txt", kX2ResolutionNames[choice]);
  }
  g_x2_res_choice.store(choice);
  int scale = x2_resolution_scale_for(choice);
  if (scale) rex::platform::env::set("REX_RESOLUTION_SCALE", std::to_string(scale));
  REXLOG_INFO("XIII-2 resolution: {}{} ({})", kX2ResolutionNames[choice],
              scale ? fmt::format(" -> {}x{}", 1280 * scale, 720 * scale) : std::string(" -> config file"),
              (settings_dir / "resolution.txt").string());
#if defined(__ANDROID__)
  // The renderer options x2_android_main.cpp applied before the log started.
  if (auto options = rex::platform::env::get("X2_ANDROID_OPTIONS_LOG")) {
    std::string_view rest = *options;
    while (!rest.empty()) {
      size_t end = rest.find('\n');
      std::string_view line = rest.substr(0, end);
      if (!line.empty()) REXLOG_INFO("{}", line);
      if (end == std::string_view::npos) break;
      rest.remove_prefix(end + 1);
    }
  }
#endif
}

// Settings > Resolution (x2_config_menu.cpp): 0 auto, 1 720p, 2 1440p, 3 2160p. The choice is
// stored for the next start; true when it differs from what the renderer uses now.
int X2ResolutionChoice() { return x2_res_parse(x2_read_setting("resolution.txt")); }
int X2ResolutionScaleFor(int choice) { return x2_resolution_scale_for(choice); }
int X2ResolutionActiveScale() {
  auto v = rex::platform::env::get("REX_RESOLUTION_SCALE");
  return v && !v->empty() ? std::atoi(v->c_str()) : 0;
}
bool X2SetResolution(int choice) {
  if (choice < 0 || choice > 3) choice = 0;
  x2_write_setting("resolution.txt", kX2ResolutionNames[choice]);
  const int now = X2ResolutionActiveScale(), next = x2_resolution_scale_for(choice);
  REXLOG_INFO("XIII-2 resolution: {} chosen (now {}x{}, next start {})", kX2ResolutionNames[choice], 1280 * now,
              720 * now, next ? fmt::format("{}x{}", 1280 * next, 720 * next) : std::string("the config file's size"));
  return next != now;
}

// Restarts the game now (a new process with the same arguments, as on quitting to the title);
// returns only if that could not be done.
void X2RestartNow() {
  if (g_x2_relaunching.exchange(true)) return;
  REXLOG_INFO("XIII-2 restart: restarting for the new settings");
  ::rex::FlushLogging();
  if (x2_restart_process()) x2_end_process();
  REXLOG_WARN("XIII-2 restart: could not start a new process; the settings apply at the next start");
  g_x2_relaunching.store(false);
}

// ---------------------------------------------------------------------------------------
// Title menu without DOWNLOADABLE CONTENT and EXTRAS. GuiTitleMenu's rows (the label sheet
// title_sel01us): 0 NEW GAME, 1 CONTINUE, 2 DOWNLOADABLE CONTENT, 3 BEGINNER'S PRIMER, 4 EXTRAS,
// 5 SETTINGS. Its confirm (sub_826CBEC8, this = the menu: +0x88 page, 0 = the main list, 1 = the
// difficulty choice; +0x94 + 8 * page the page's item; the title update moved it from +0x90)
// acts by item: 0 the difficulty page, 1 continue, 2 the DLC screen (DLC mode on) or a buzzer,
// 3 result 3 to the scene, 4 the #TitleSp script, 5 screen 2. Rows 2 and 4 are hidden (and
// choosing them does nothing);
// the rows below move up so no gap is left. Every choice is logged. X2_TITLE_ITEMS=1 keeps
// the menu as it is.
// ---------------------------------------------------------------------------------------
namespace {
bool x2_title_keep() {
  static const bool keep = [] {
    const char* v = std::getenv("X2_TITLE_ITEMS");
    return v && v[0] == '1';
  }();
  return keep;
}
}  // namespace

REX_EXTERN(__imp__sub_826CBEC8);
REX_HOOK_RAW(sub_826CBEC8) {
  const uint32_t menu = ctx.r3.u32;
  const uint32_t page = menu ? x2_be32(base, menu + 0x88) : 0, item = menu ? x2_be32(base, menu + 0x94) : 0;
  const bool removed = menu && page == 0 && (item == 2 || item == 4) && !x2_title_keep();
  static std::atomic<int> logged{0};
  if (logged.fetch_add(1) < 32)
    REXLOG_INFO("XIII-2 title menu: item {} chosen (page {}){}", item, page, removed ? " - a removed item, nothing happens" : "");
  if (removed) {
    ctx.r3.u64 = 1;
    return;
  }
  __imp__sub_826CBEC8(ctx, base);
}

// The rows. Pages are in the child list at [menu+0x44]+0x10, a page's rows in the child list
// at page+0x10 (first child at list+4, next at child+0x20). A row widget: +0x80 scale and
// +0xC0 position (float vectors x, y, z, w), +0xE4 flags: 0x2 hidden (not drawn; the cursor
// moves sub_826CA7E0 / sub_826CAA00 step past it, as the Save screen's list does), 0x4 the
// transform to be updated (the game sets it after writing +0x80 / +0xC0). Each frame
// (GuiTitleMenu's update sub_826CB288) and before each cursor move: rows 2 and 4 hidden, row 3
// at row 2's position, row 5 halfway between rows 2 and 4 (where row 3 was; the rows are evenly
// spaced, and the hidden rows keep the layout's positions, so this holds while the menu
// animates).
namespace {
uint32_t x2_child(uint8_t* base, uint32_t list, uint32_t index) {
  uint32_t child = list ? x2_be32(base, list + 4) : 0;
  for (uint32_t i = 0; child && i < index; ++i) child = x2_be32(base, child + 0x20);
  return child;
}

float x2_ldf(uint8_t* base, uint32_t a) {
  const uint32_t v = x2_be32(base, a);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}

void x2_stf(uint8_t* base, uint32_t a, float f) {
  uint32_t v;
  std::memcpy(&v, &f, 4);
  *reinterpret_cast<uint32_t*>(base + a) = __builtin_bswap32(v);
}

void x2_set_flags(uint8_t* base, uint32_t row, uint32_t bits) {
  const uint32_t flags = x2_be32(base, row + 0xE4);
  if ((flags & bits) != bits) *reinterpret_cast<uint32_t*>(base + row + 0xE4) = __builtin_bswap32(flags | bits);
}

void x2_title_layout(uint8_t* base, uint32_t menu) {
  if (x2_title_keep() || !menu || x2_be32(base, menu + 0x88) != 0) return;
  const uint32_t container = x2_be32(base, menu + 0x44);
  const uint32_t page = container ? x2_child(base, container + 0x10, 0) : 0;
  if (!page) return;
  uint32_t row[6];
  for (uint32_t i = 0; i < 6; ++i) {
    row[i] = x2_child(base, page + 0x10, i);
    if (!row[i]) return;
  }
  x2_set_flags(base, row[2], 2);
  x2_set_flags(base, row[4], 2);
  bool moved = false;
  for (uint32_t k = 0; k < 3; ++k) {  // x, y, z
    const float p2 = x2_ldf(base, row[2] + 0xC0 + 4 * k), p4 = x2_ldf(base, row[4] + 0xC0 + 4 * k);
    const float mid = (p2 + p4) * 0.5f;
    if (x2_ldf(base, row[3] + 0xC0 + 4 * k) != p2) { x2_stf(base, row[3] + 0xC0 + 4 * k, p2); moved = true; }
    if (x2_ldf(base, row[5] + 0xC0 + 4 * k) != mid) { x2_stf(base, row[5] + 0xC0 + 4 * k, mid); moved = true; }
  }
  if (moved) {
    x2_set_flags(base, row[3], 4);
    x2_set_flags(base, row[5], 4);
  }
  // The cursor on a hidden row (a menu opened with it there): onto the next shown one.
  const uint32_t item = x2_be32(base, menu + 0x94);
  if (item == 2 || item == 4) *reinterpret_cast<uint32_t*>(base + menu + 0x94) = __builtin_bswap32(item + 1);
  static std::atomic<int> logged{0};
  if (logged.fetch_add(1) < 2)
    REXLOG_INFO("XIII-2 title menu: DOWNLOADABLE CONTENT and EXTRAS hidden; BEGINNER'S PRIMER y {} -> {}, SETTINGS y {} (rows 2 / 4 at y {} / {})",
                x2_ldf(base, row[3] + 0xC4), x2_ldf(base, row[2] + 0xC4), x2_ldf(base, row[5] + 0xC4), x2_ldf(base, row[2] + 0xC4),
                x2_ldf(base, row[4] + 0xC4));
}
}  // namespace

REX_EXTERN(__imp__sub_826CB288);
REX_HOOK_RAW(sub_826CB288) {
  x2_title_layout(base, ctx.r3.u32);
  __imp__sub_826CB288(ctx, base);
}

REX_EXTERN(__imp__sub_826CA7E0);
REX_HOOK_RAW(sub_826CA7E0) {
  x2_title_layout(base, ctx.r3.u32);
  __imp__sub_826CA7E0(ctx, base);
}

REX_EXTERN(__imp__sub_826CAA00);
REX_HOOK_RAW(sub_826CAA00) {
  x2_title_layout(base, ctx.r3.u32);
  __imp__sub_826CAA00(ctx, base);
}
