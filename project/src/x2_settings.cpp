// ffxiii2 - the port's settings (Settings > Port Settings, x2_config_menu.cpp) and what they
// change in the game. Ported from the Lightning Returns port (lr_hooks.cpp); the addresses are
// XIII-2's. Each choice is a one-line file in the settings folder (the user data root):
//   voice.txt      en / jp      Japanese voices: the converted Steam voices (jpvoice\ next to
//                               the game) are the update archive; from the next start
//   text.txt       en / jp      on-screen text language (the 360 disc has every language);
//                               from the next start
//   ui_style.txt   auto / 360 / ps3   Button Prompts (the renderer's PS3 UI texture set)
//   confirm.txt    a / b        which face button confirms
//   fps.txt        30 / 60g / 60 / 120   frame rate (60g: cutscenes stay at 30; 120: PC only,
//                               cutscenes at 60, from the next start)
//   aa.txt         off / fxaa / extreme   the renderer's FXAA pass
//   story_recaps.txt on / off   The Story So Far videos (next recap; default on)
//   resolution.txt (x2_hooks.cpp)

#include <algorithm>
#include <cstring>
#include <vector>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <thread>
#include <chrono>

#include <rex/cvar.h>
#include <rex/hook.h>
#include <rex/logging/macros.h>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#endif

std::filesystem::path X2SettingsDir();
std::string X2ReadSetting(const char* name);
void X2WriteSetting(const char* name, const std::string& value);
void X2LogVoiceStart();

namespace {
uint32_t ld32(uint8_t* base, uint32_t a) { return __builtin_bswap32(*reinterpret_cast<uint32_t*>(base + a)); }
void st32(uint8_t* base, uint32_t a, uint32_t v) { *reinterpret_cast<uint32_t*>(base + a) = __builtin_bswap32(v); }

std::atomic<bool> g_loaded{false};
std::atomic<int> g_voice_jp{0}, g_text_jp{0};
std::atomic<int> g_text_jp_at_start{-1};  // what this run's text language was set to
std::atomic<int> g_voice_jp_at_start{-1};
std::atomic<int> g_ui_auto{1}, g_ui_ps3{0}, g_pad_ps3{-1};
std::atomic<int> g_confirm_b{0};
std::atomic<int> g_skip_story_recaps{0};
std::atomic<int> g_fps{0};  // 0: 30 fps; 1: 60 fps, cutscenes 30; 2: 60 everywhere; 3: 120, cutscenes 60
std::atomic<bool> g_vblank120{false};  // this start's vertical blank runs at 120 Hz (X2FrameRateStartup)
std::atomic<int> g_aa{0}, g_aa_applied{-1};
std::atomic<uint8_t*> g_base{nullptr};

template <typename Fn>
Fn plugin_function(const char* name) {
#if defined(_WIN32)
  HMODULE module = GetModuleHandleW(L"rexgpu-xenos.dll");
  return module ? reinterpret_cast<Fn>(GetProcAddress(module, name)) : nullptr;
#else
  void* module = dlopen("librexgpu-xenos.so", RTLD_NOW | RTLD_NOLOAD);
  return module ? reinterpret_cast<Fn>(dlsym(module, name)) : nullptr;
#endif
}

std::filesystem::path exe_dir() {
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

}  // namespace

// The folder the port's converted files sit in (jpvoice, update_tu, update_merged, jpvoice_dlc,
// movies_ps3, movies_jp): next to the exe on the PC; on Android, where the phone install puts
// them (the app's own folder is read-only), given as X2_INSTALL_DIR by x2_android_main.cpp.
std::filesystem::path X2InstallDir() {
  if (const char* v = std::getenv("X2_INSTALL_DIR"); v && *v) return std::filesystem::path(v);
  return exe_dir();
}

namespace {

void load() {
  if (g_loaded.exchange(true)) return;
  g_voice_jp.store(X2ReadSetting("voice.txt") == "jp");
  g_text_jp.store(X2ReadSetting("text.txt") == "jp");
  const std::string ui = X2ReadSetting("ui_style.txt");
  g_ui_auto.store(ui != "360" && ui != "ps3");
  g_ui_ps3.store(ui == "ps3");
  // The renderer reads LR_UI_STYLE when it starts: with PS3 prompts chosen, the UI sheets the
  // game builds early (before the first frame tells the renderer) are matched against the PS3 set.
  if (ui == "ps3") {
#if defined(_WIN32)
    _putenv_s("LR_UI_STYLE", "ps3");
#else
    setenv("LR_UI_STYLE", "ps3", 1);
#endif
  }
  g_confirm_b.store(X2ReadSetting("confirm.txt") == "b");
  g_skip_story_recaps.store(X2ReadSetting("story_recaps.txt") == "off");
  const std::string fps = X2ReadSetting("fps.txt");
#if defined(__ANDROID__)
  // No 120 on Android: a PC's "120" (copied by the phone install) is 30, the phone default.
  g_fps.store(fps == "60" ? 2 : fps == "60g" ? 1 : 0);
#else
  g_fps.store(fps == "120" ? 3 : fps == "60" ? 2 : fps == "60g" ? 1 : 0);
#endif
  const std::string aa = X2ReadSetting("aa.txt");
  g_aa.store(aa == "extreme" ? 2 : aa == "fxaa" ? 1 : 0);
  REXLOG_INFO("XIII-2 settings: voices {}, text {}, Button Prompts {}, confirm {}, frame rate {}, anti-aliasing {}",
              g_voice_jp ? "Japanese" : "English", g_text_jp ? "Japanese" : "English",
              g_ui_auto ? "auto" : g_ui_ps3 ? "PS3" : "360", g_confirm_b ? "B" : "A",
              g_fps == 3 ? "120 (cutscenes 60)" : g_fps == 2 ? "60" : g_fps == 1 ? "60 (cutscenes 30)" : "30",
              g_aa == 2 ? "FXAA extreme" : g_aa == 1 ? "FXAA" : "off");
  REXLOG_INFO("XIII-2 Story Recaps: {}", g_skip_story_recaps.load() ? "off" : "on");
  X2LogVoiceStart();
}

void set_ui_style(int ps3) {
  g_ui_ps3.store(ps3 ? 1 : 0);
  if (auto set = plugin_function<int (*)(int)>("LrSetUiStyle")) {
    const int r = set(ps3 ? 1 : 0);
    REXLOG_INFO("XIII-2 Button Prompts: renderer told {} (LrSetUiStyle -> {})", ps3 ? "PS3" : "360", r);
  } else {
    REXLOG_WARN("XIII-2 Button Prompts: the renderer has no LrSetUiStyle (not loaded yet?)");
  }
}

// Confirm button: the game keeps the confirming and cancelling pad buttons in two masks and a
// flag that orders the face-button icons, all set once at the start by sub_8225FC40 (Lightning
// Returns: sub_822F44B0, 0x830EDD00): the 360 release fixes them at confirm 0x20 (A), cancel
// 0x80 (B), flag 1. B confirms: the masks swapped and the flag cleared (the Japanese layout).
constexpr uint32_t kConfirmMask = 0x82E51FCC, kCancelMask = 0x82E51FD0, kConfirmIconFlag = 0x82E51FD4;

void apply_confirm(uint8_t* base) {
  if (!base) return;
  const bool b = g_confirm_b.load() != 0;
  st32(base, kConfirmMask, b ? 0x80 : 0x20);
  st32(base, kCancelMask, b ? 0x20 : 0x80);
  base[kConfirmIconFlag] = b ? 0 : 1;
}
}  // namespace

// ---------------------------------------------------------------------------------------
// Backend for the menu (x2_config_menu.cpp) and the app (ffxiii2_app.h).
// ---------------------------------------------------------------------------------------
void X2SettingsLoad() { load(); }

int X2ConfigGetSkipStoryRecaps() { return (load(), g_skip_story_recaps.load()); }
void X2ApplyStoryRecapSetting(int skip);
void X2ConfigSetSkipStoryRecaps(int skip) {
  g_skip_story_recaps.store(skip ? 1 : 0);
  X2ApplyStoryRecapSetting(skip);
  X2WriteSetting("story_recaps.txt", skip ? "off" : "on");
  REXLOG_INFO("XIII-2 Story Recaps: {}", skip ? "off" : "on");
}

int X2ConfigGetVoice() { return (load(), g_voice_jp.load()); }
// The converted Japanese voices are installed (jpvoice\ next to the game, made by the builder).
int X2ConfigVoiceAvailable() {
  static const int available = [] {
    std::error_code ec;
    return std::filesystem::exists(X2InstallDir() / "jpvoice" / "filelist_patch_u.x360.bin", ec) ? 1 : 0;
  }();
  return available;
}
void X2ConfigSetVoice(int jp) {
  g_voice_jp.store(jp ? 1 : 0);
  X2WriteSetting("voice.txt", jp ? "jp" : "en");
  REXLOG_INFO("XIII-2 voices: {} chosen (from the next start)", jp ? "Japanese" : "English");
}
// Whether this run plays the Japanese voices (the update archive was mounted at the start).
// (OnConfigurePaths runs before logging is up: the line is logged with the settings.)
std::string g_voice_start_note;
void X2SetVoiceAtStart(int jp, const std::string& note) {
  g_voice_jp_at_start.store(jp);
  g_voice_start_note = note;
}
int X2VoiceAtStart() { return g_voice_jp_at_start.load(); }
namespace {
std::atomic<int> g_update_kind{0};
}
void X2SetUpdateKind(int kind) { g_update_kind.store(kind); }
namespace {
std::atomic<bool> g_jp_dlc{false};
}
void X2SetJpDlcMounted() { g_jp_dlc.store(true); }
bool X2JpDlcMounted() { return g_jp_dlc.load(); }
int X2UpdateKind() { return g_update_kind.load(); }

void X2LogVoiceStart() {
  if (!g_voice_start_note.empty()) REXLOG_INFO("XIII-2 voices: {}", g_voice_start_note);
}

int X2ConfigGetText() { return (load(), g_text_jp.load()); }
void X2ConfigSetText(int jp) {
  g_text_jp.store(jp ? 1 : 0);
  X2WriteSetting("text.txt", jp ? "jp" : "en");
  REXLOG_INFO("XIII-2 text: {} chosen (from the next start)", jp ? "Japanese" : "English");
}
int X2TextAtStart() { return g_text_jp_at_start.load(); }

// Button Prompts: 0 Auto (follows the controller in use), 1 Xbox 360, 2 PlayStation 3.
int X2ConfigGetUiMode() { return (load(), g_ui_auto.load() ? 0 : g_ui_ps3.load() ? 2 : 1); }
void X2ConfigSetUiMode(int mode) {
  if (mode == 1 || mode == 2) {
    g_ui_auto.store(0);
    set_ui_style(mode == 2);
    X2WriteSetting("ui_style.txt", mode == 2 ? "ps3" : "360");
  } else {
    g_ui_auto.store(1);
    X2WriteSetting("ui_style.txt", "auto");
    if (g_pad_ps3.load() >= 0) set_ui_style(g_pad_ps3.load());
  }
  REXLOG_INFO("XIII-2 Button Prompts: {}", mode == 2 ? "PS3" : mode == 1 ? "360" : "auto");
}
// 1 while the game shows PS3 prompts (the Android touch layer draws matching buttons).
int X2ConfigGetUiStyle() { return (load(), g_ui_ps3.load()); }
// The controller in use changed (ps3: DualShock / DualSense; else an Xbox controller).
void X2UiStyleForController(int ps3, const char* name) {
  load();
  g_pad_ps3.store(ps3 ? 1 : 0);
  if (!g_ui_auto.load() || (ps3 ? 1 : 0) == g_ui_ps3.load()) return;
  set_ui_style(ps3);
  REXLOG_INFO("XIII-2 input: {} -> Button Prompts {} (auto)", name ? name : "controller", ps3 ? "PS3" : "360");
}

int X2ConfigGetConfirm() { return (load(), g_confirm_b.load()); }
void X2ConfigSetConfirm(int b) {
  g_confirm_b.store(b ? 1 : 0);
  X2WriteSetting("confirm.txt", b ? "b" : "a");
  apply_confirm(g_base.load());
  REXLOG_INFO("XIII-2 confirm button: {}", b ? "B (A cancels)" : "A (B cancels)");
}

int X2ConfigGetFps() { return (load(), g_fps.load()); }
void X2ConfigSetFps(int choice) {
#if defined(__ANDROID__)
  const int top = 2;
#else
  const int top = 3;
#endif
  choice = choice < 0 || choice > top ? 0 : choice;
  g_fps.store(choice);
  X2WriteSetting("fps.txt", choice == 3 ? "120" : choice == 2 ? "60" : choice == 1 ? "60g" : "30");
  REXLOG_INFO("XIII-2 frame rate: {} chosen{}",
              choice == 3 ? "120 fps, cutscenes 60" : choice == 2 ? "60 fps" : choice == 1 ? "60 fps, cutscenes 30" : "30 fps",
              choice == 3 && !g_vblank120.load() ? " (60 until the next start)" : "");
}
// True when 120 fps is chosen but this start's vertical blank is 60 Hz (the choice applies at
// the next start; the config menu asks to restart).
bool X2FpsNeedsRestart() { return (load(), g_fps.load() == 3 && !g_vblank120.load()); }

int X2ConfigGetAa() { return (load(), g_aa.load()); }
void X2ConfigSetAa(int choice) {
  choice = choice < 0 || choice > 2 ? 0 : choice;
  g_aa.store(choice);
  X2WriteSetting("aa.txt", choice == 2 ? "extreme" : choice == 1 ? "fxaa" : "off");
  REXLOG_INFO("XIII-2 anti-aliasing: {} chosen", choice == 2 ? "FXAA extreme" : choice == 1 ? "FXAA" : "off");
}

// Once per frame on the main thread (x2_hooks.cpp, sub_82910538).
void X2SettingsFrame(uint8_t* base) {
  load();
  g_base.store(base, std::memory_order_relaxed);
  // 120 fps safe mode (X2FrameRateStartup): this start got going with it.
  static uint32_t frames = 0;
  if (++frames == 600 && g_vblank120.load(std::memory_order_relaxed)) {
    X2WriteSetting("fps_trial.txt", "");
    REXLOG_INFO("XIII-2 frame rate: 120 fps confirmed (600 frames)");
  }
  // Anti-aliasing: the renderer's FXAA pass, switched from the next frame; asked every frame
  // until the renderer is up and has taken the choice.
  const int want = g_aa.load(std::memory_order_relaxed);
  if (want != g_aa_applied.load(std::memory_order_relaxed)) {
    static int32_t (*set)(int32_t) = nullptr;
    if (!set) set = plugin_function<int32_t (*)(int32_t)>("lr_set_post_effect");
    if (set && set(want)) {
      g_aa_applied.store(want, std::memory_order_relaxed);
      REXLOG_INFO("XIII-2 anti-aliasing: {}", want == 2 ? "FXAA extreme" : want == 1 ? "FXAA" : "off");
    }
  }
  // Button Prompts fixed to one set: tell the renderer once it is up.
  static bool ui_told = false;
  if (!ui_told && !g_ui_auto.load()) {
    if (auto set = plugin_function<int (*)(int)>("LrSetUiStyle")) {
      const int r = set(g_ui_ps3.load());
      ui_told = true;
      REXLOG_INFO("XIII-2 Button Prompts: renderer told {} at start (LrSetUiStyle -> {})", g_ui_ps3.load() ? "PS3" : "360", r);
    }
  }
}

// ---------------------------------------------------------------------------------------
// Text language. sub_82507278(language) sets the game's text language once at the start
// ([0x82C66244], -1 until then; 0 Japanese, 1 English, then uk it gr fr sp ru kr ck ch: the
// txtres_<code>.ztr table at 0x82C6628C). sub_8225FC40 calls it from the console's language;
// the 360 disc holds every language's text, menus, and fonts, so Japanese is asked for here.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82507278);
REX_HOOK_RAW(sub_82507278) {
  load();
  const bool unset = int32_t(ld32(base, 0x82C66244)) < 0;
  if (unset) {
    const int asked = int32_t(ctx.r3.u32);
    if (g_text_jp.load()) ctx.r3.u64 = 0;
    g_text_jp_at_start.store(g_text_jp.load());
    REXLOG_INFO("XIII-2 text: language {} (the console asked for {})", g_text_jp.load() ? "Japanese" : "English", asked);
  }
  __imp__sub_82507278(ctx, base);
}

REX_EXTERN(__imp__sub_8225FC40);

// The game's start: once its save key is set (sub_82509360, which this calls), the Steam saves are
// imported; then the confirm button choice is applied. The title update's own code handles the
// downloadable content (its packages are found where the 360 dashboard installs them: the
// runtime's content folder, 0000000000000000\53510806\00000002).
int X2VoiceAtStart();
void X2ImportSteamSaves(uint8_t* base);
REX_HOOK_RAW(sub_8225FC40) {
  const uint32_t state = ctx.r3.u32;
  __imp__sub_8225FC40(ctx, base);
  if (state) {
    const uint32_t flags = __builtin_bswap32(*reinterpret_cast<uint32_t*>(base + state + 0x5C4));
    REXLOG_INFO("XIII-2 DLC mode: {} (the game's own, from its title update)", (flags & 0x100000u) ? "on" : "off");
  }
  load();
  g_base.store(base);
  X2ImportSteamSaves(base);  // the game's save key is set now (x2_steam_saves.cpp)
  if (g_confirm_b.load()) {
    apply_confirm(base);
    REXLOG_INFO("XIII-2 confirm button: B confirms, A cancels");
  }
}

// ---------------------------------------------------------------------------------------
// Frame rate. As in Lightning Returns (the same frame pacer): each frame sub_82910538 sets the
// target from the current scene, sub_8290D5E8(pacer, fps) (pacer [0x82EA1780]; 29.97 in the
// field, battle, and menus, 59.94 on system screens), and the main thread waits for
// round(refresh / target) vertical blanks. The game's frame time is the measured time between
// vertical blanks, so game speed does not depend on the frame rate. The current frame-rate
// mode is at app+0x2EC (sub_822596A8(app, push, mode)): 0 = 59.94, 1 = 29.97 (gameplay),
// 2+ = 29.97 (event scenes). "60 fps (gameplay)" runs mode 1 at 59.94 and keeps scenes at 30.
//
// 120 fps (PC only). The pacer counts whole vertical blanks, and the emulated one is 60 Hz, so
// 120 needs a 120 Hz vertical blank: the runtime's guest video mode refresh rate
// (video_mode_refresh_rate, which drives its vblank timer and what VdQueryVideoMode reports) is
// set to 120 before the graphics start, when the saved choice is 120. The pacer's own refresh
// rate is a constant (59.94 at pacer+0, set by its constructor sub_8290D778), used both for the
// number of vertical blanks per frame (sub_8290D618: round(refresh / target)) and for the frame
// time (sub_8290D8B8: the time between the last two vertical blanks rounded to whole 1/refresh
// periods); it becomes 119.88 with the 120 Hz vertical blank, so 30 fps is 4 vertical blanks,
// 60 is 2, 120 is 1, and a frame's time is still measured right (game speed unchanged).
// Cutscenes run at 59.94 with the 120 setting.
// ---------------------------------------------------------------------------------------
// The game itself must keep seeing a 60 Hz display: told 120 (VdQueryVideoMode,
// VdGetCurrentDisplayInformation, XGetVideoMode), v22 crashed about 4 s after start, before the
// first frame. The runtime reads the refresh rate for its vblank timer once, when the timer's
// thread starts (GraphicsSystem::Setup, before OnPostSetup); so 120 is set before the graphics
// start and the previous value is put back once the timer runs (X2FrameRateAfterSetup), before
// the game is launched.
namespace {
std::string g_refresh_before;
}

// Before the renderer starts (ffxiii2_app.h OnPreSetup): the 120 Hz vertical blank when 120 fps
// is the saved choice.
void X2FrameRateStartup() {
  load();
#if !defined(__ANDROID__)
  if (g_fps.load() != 3) return;
  // Safe mode, as for the resolution: 120 is on trial until the game has run 600 frames with it
  // (X2SettingsFrame). If the last start with it never got there, this start runs at 60.
  if (X2ReadSetting("fps_trial.txt") == "120") {
    REXLOG_WARN("XIII-2 frame rate: the last start with 120 fps did not get going; back to 60 fps (all)");
    g_fps.store(2);
    X2WriteSetting("fps.txt", "60");
    X2WriteSetting("fps_trial.txt", "");
    return;
  }
  X2WriteSetting("fps_trial.txt", "120");
  g_refresh_before = rex::cvar::GetFlagByName("video_mode_refresh_rate");
  const bool ok = rex::cvar::SetFlagByName("video_mode_refresh_rate", "120");
  g_vblank120.store(ok);
  if (ok) REXLOG_INFO("XIII-2 frame rate: 120 fps; vertical blank at 120 Hz (video_mode_refresh_rate 120)");
  else REXLOG_WARN("XIII-2 frame rate: 120 fps chosen, but the runtime refused video_mode_refresh_rate 120; running at 60");
#endif
}

// After the graphics are set up (ffxiii2_app.h OnPostSetup), before the game starts: the game
// is told 60 Hz again; the vblank timer keeps the 120 Hz it started with.
void X2FrameRateAfterSetup() {
#if !defined(__ANDROID__)
  if (!g_vblank120.load()) return;
  std::this_thread::sleep_for(std::chrono::milliseconds(300));  // the timer thread reads it as it starts
  const std::string back = g_refresh_before.empty() ? std::string("60") : g_refresh_before;
  const bool ok = rex::cvar::SetFlagByName("video_mode_refresh_rate", back);
  REXLOG_INFO("XIII-2 frame rate: the game is told {} Hz ({}); the vertical blank stays at 120 Hz",
              rex::cvar::GetFlagByName("video_mode_refresh_rate"), ok ? "put back" : "could not be put back");
#endif
}
namespace {
std::atomic<uint32_t> g_fps_app{0};
std::atomic<int> g_fps_running_milli{0};
}

REX_EXTERN(__imp__sub_822596A8);
REX_HOOK_RAW(sub_822596A8) {
  g_fps_app.store(ctx.r3.u32, std::memory_order_relaxed);
  __imp__sub_822596A8(ctx, base);
}

REX_EXTERN(__imp__sub_8290D5E8);
void X2InstallCrashReport();  // x2_perf.cpp

// sub_8290D5E8 is only "store f1 at this+4", a function the game shares between classes (some
// call it through their vtables, where +0 is the vtable): everything below is for the frame
// pacer alone, [0x82EA1780]. v22 wrote the pacer refresh rate into every caller's +0 and crashed
// on the next virtual call; the 60 fps settings likewise changed other objects' values.
constexpr uint32_t kFramePacer = 0x82EA1780;

REX_HOOK_RAW(sub_8290D5E8) {
  load();
  X2InstallCrashReport();  // already at the first pacer call, before the first frame
  if (ctx.r3.u32 == 0 || ctx.r3.u32 != ld32(base, kFramePacer)) {
    __imp__sub_8290D5E8(ctx, base);
    return;
  }
  const double asked = ctx.f1.f64;
  const int setting = g_fps.load(std::memory_order_relaxed);
  int mode = -1;
  if (uint32_t app = g_fps_app.load(std::memory_order_relaxed)) mode = int32_t(ld32(base, app + 0x2EC));
  const bool scene = mode >= 2;
  const bool vblank120 = g_vblank120.load(std::memory_order_relaxed);
  if (vblank120) {
    // The pacer's refresh rate, for the 120 Hz vertical blank (see above).
    const float refresh = 120000.0f / 1001.0f;
    uint32_t bits;
    std::memcpy(&bits, &refresh, 4);
    bits = __builtin_bswap32(bits);
    std::memcpy(base + ctx.r3.u32, &bits, 4);
  }
  double target = asked;
  const char* why = "";
  if (setting == 3 && vblank120 && asked > 20.0 && asked < 100.0) {
    target = scene ? 60000.0 / 1001.0 : 120000.0 / 1001.0;
    why = scene ? ", running at 59.94 (120 fps setting, cutscene)" : ", running at 119.88 (120 fps setting)";
  } else if (setting && asked > 20.0 && asked < 50.0 && (setting >= 2 || !scene)) {
    target = 60000.0 / 1001.0;  // 60 fps settings (and 120 until the next start)
    why = ", running at 59.94 (60 fps setting)";
  }
  ctx.f1.f64 = target;
  static std::atomic<int> last{-1};
  const int now = int(target + 0.5) * 100 + int(asked + 0.5) + 100000 * (mode + 1);
  if (last.exchange(now) != now)
    REXLOG_INFO("XIII-2 frame rate: the game asks for {:.2f} fps (mode {}){}", asked, mode, why);
  g_fps_running_milli.store(int(ctx.f1.f64 * 1000.0 + 0.5), std::memory_order_relaxed);
  __imp__sub_8290D5E8(ctx, base);
}

// The frame rate the game runs at now (for Android's performance hint, x2_android.cpp).
double X2RunningFrameRate() {
  int milli = g_fps_running_milli.load(std::memory_order_relaxed);
  return milli > 0 ? milli / 1000.0 : 30000.0 / 1001.0;
}
