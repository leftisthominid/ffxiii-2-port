// ffxiii2 - ReXGlue Recompiled Project (FFXIII-2 port)
//
// The app around the recompiled game, carried over from the Lightning Returns port
// (lightningreturns_app.h): no-sound fallback, frame rate overlay (F2), screenshots (Share,
// Back + left stick click, F12), keyboard and mouse while no controller is connected, cursor
// auto-hide, render resolution at startup, and ffxiii2_launch.txt next to the exe.

#pragma once

#include <rex/audio/audio_driver.h>
#include <rex/audio/sdl/sdl_audio_system.h>
#include <rex/cvar.h>
#include <rex/filesystem.h>
#include <rex/filesystem/devices/host_path_device.h>
#include <rex/filesystem/vfs.h>
#include <rex/system/kernel_state.h>
#include <rex/system/user_module.h>
#include <rex/logging.h>
#include <rex/rex_app.h>
#include <rex/runtime.h>
#include <rex/system/xmemory.h>
#include <rex/ui/imgui_dialog.h>
#include <rex/ui/keybinds.h>
#include <rex/ui/presenter.h>
#include <rex/ui/window_listener.h>
#include <imgui.h>

#include <SDL3/SDL.h>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

// Share button through Microsoft's GameInput (API 3, lr_gameinput.h: Microsoft's MIT-licensed
// header as SDL ships it). Its system-button callback is the only place Windows reports Share
// for USB / wireless-adapter Xbox controllers. The runtime is GameInputRedist.dll (the GameInput
// redistributable) or a recent inbox GameInput.dll; without one, SDL alone reads the controller.
#if defined(_WIN32) && defined(__has_include)
#if __has_include("lr_gameinput.h")
#include "lr_gameinput.h"
#if defined(GAMEINPUT_API_VERSION) && GAMEINPUT_API_VERSION >= 1
#define LR_GAMEINPUT_SHARE 1
#include <atomic>
#include <cstdint>
#include <string>
#define LR_GI_CAT2(a, b) a##b
#define LR_GI_CAT(a, b) LR_GI_CAT2(a, b)
namespace lr_gameinput {
using namespace GameInput::LR_GI_CAT(v, GAMEINPUT_API_VERSION);
inline std::atomic<bool> g_share_down{false};
inline void CALLBACK OnSystemButtons(GameInputCallbackToken, void*, IGameInputDevice*, uint64_t,
                                     GameInputSystemButtons current, GameInputSystemButtons) {
  g_share_down.store((current & GameInputSystemButtonShare) != 0, std::memory_order_relaxed);
}
// The runtime, as Microsoft's loader looks for it: GameInputRedist.dll in System32 or in the
// redistributable's folder (HKLM\SOFTWARE\Microsoft\GameInput RedistDir), else GameInput.dll.
inline HMODULE LoadRuntime() {
  wchar_t sys[MAX_PATH];
  UINT n = GetSystemDirectoryW(sys, MAX_PATH);
  std::wstring system32 = n ? std::wstring(sys, n) + L"\\" : std::wstring();
  if (HMODULE m = LoadLibraryW((system32 + L"GameInputRedist.dll").c_str())) return m;
  wchar_t dir[MAX_PATH];
  DWORD size = sizeof(dir);
  if (RegGetValueW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Microsoft\\GameInput", L"RedistDir",
                   RRF_RT_REG_SZ | 0x00020000 /* RRF_SUBKEY_WOW6432KEY */, nullptr, dir, &size) == ERROR_SUCCESS) {
    std::wstring path(dir);
    if (!path.empty() && path.back() != L'\\') path += L'\\';
    if (HMODULE m = LoadLibraryW((path + L"GameInputRedist.dll").c_str())) return m;
  }
  return LoadLibraryW((system32 + L"GameInput.dll").c_str());
}
// Registers the callback. Returns what went wrong, for the log (nullptr: working).
inline const char* Start() {
  HMODULE dll = LoadRuntime();
  if (!dll) return "no GameInput runtime found (install the GameInput redistributable)";
  using Init = HRESULT(WINAPI*)(REFIID, void**);
  auto init = reinterpret_cast<Init>(reinterpret_cast<void*>(GetProcAddress(dll, "GameInputInitialize")));
  if (!init) return "the GameInput runtime is too old (no GameInputInitialize; install the redistributable)";
  IGameInput* gi = nullptr;
  if (FAILED(init(IID_IGameInput, reinterpret_cast<void**>(&gi))) || !gi)
    return "the GameInput runtime does not offer API 3 (install the current redistributable)";
  GameInputCallbackToken token = 0;
  if (FAILED(gi->RegisterSystemButtonCallback(nullptr, GameInputSystemButtonShare, nullptr, OnSystemButtons, &token)))
    return "RegisterSystemButtonCallback failed";
  return nullptr;  // gi stays alive for the callback
}
}  // namespace lr_gameinput
#endif
#endif
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <unordered_map>
#include <memory>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

int X2RecentFrameTimesMs(float* out, int max);                          // x2_hooks.cpp
void X2ResolutionStartup(const std::filesystem::path& settings_dir);   // x2_hooks.cpp
void X2FrameRateStartup();                                             // x2_settings.cpp
void X2FrameRateAfterSetup();                                          // x2_settings.cpp

// x2_settings.cpp
void X2UiStyleForController(int ps3, const char* name);
void X2SetVoiceAtStart(int jp, const std::string& note);
void X2SetUpdateKind(int kind);  // 0 nothing, 1 the Japanese voices alone, 2 the title update (+ voices)
int X2UpdateKind();
void X2SetJpDlcMounted();
std::filesystem::path X2InstallDir();  // x2_settings.cpp: next to the exe (Android: X2_INSTALL_DIR)
int X2VoiceAtStart();
#if defined(__ANDROID__)
extern std::atomic<bool> g_x2_screenshot_request;  // x2_android.cpp: the touch layer's screenshot button
#endif

// With no sound output device (none plugged in, or all disabled) the runtime's audio driver
// fails to open, the game's XAudio registration fails, and the movie player crashes. This
// backend falls back to a silent driver that takes the game's audio frames at the real rate
// (256 samples at 48 kHz), so the game runs normally without sound.
class X2SilentAudioDriver : public rex::audio::AudioDriver {
 public:
  X2SilentAudioDriver(rex::memory::Memory* memory, rex::thread::Semaphore* semaphore)
      : AudioDriver(memory), semaphore_(semaphore), thread_([this] { Run(); }) {}
  ~X2SilentAudioDriver() override {
    running_ = false;
    thread_.join();
  }
  void SubmitFrame(uint32_t) override { ++queued_; }

 private:
  void Run() {
    using clock = std::chrono::steady_clock;
    const auto start = clock::now();
    const auto frame = std::chrono::microseconds(16000) / 3;  // 256 samples at 48 kHz
    uint64_t played = 0;
    while (running_) {
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
      // Catches up after a long sleep (coarse timers): a frame plays every 5.33 ms, as on a
      // sound device, and the game is told when a frame it queued has played.
      for (uint64_t due = uint64_t((clock::now() - start) / frame); played < due; ++played) {
        int q = queued_.load();
        while (q > 0 && !queued_.compare_exchange_weak(q, q - 1)) {
        }
        if (q > 0) semaphore_->Release(1, nullptr);
      }
    }
  }
  rex::thread::Semaphore* semaphore_;
  std::atomic<int> queued_{0};
  std::atomic<bool> running_{true};
  std::thread thread_;
};

class X2AudioSystem : public rex::audio::sdl::SDLAudioSystem {
  using X_STATUS = rex::X_STATUS;

 public:
  using SDLAudioSystem::SDLAudioSystem;
  static std::unique_ptr<rex::audio::AudioSystem> Create(rex::runtime::FunctionDispatcher* dispatcher) {
    return std::make_unique<X2AudioSystem>(dispatcher);
  }
  X_STATUS CreateDriver(size_t index, rex::thread::Semaphore* semaphore,
                        rex::audio::AudioDriver** out_driver) override {
    if (XSUCCEEDED(SDLAudioSystem::CreateDriver(index, semaphore, out_driver))) return X_STATUS_SUCCESS;
    REXLOG_WARN("XIII-2 audio: no sound output device could be opened; the game runs without sound "
                "(connect or enable one and restart the game for sound)");
    *out_driver = new X2SilentAudioDriver(memory(), semaphore);
    return X_STATUS_SUCCESS;
  }
  void DestroyDriver(rex::audio::AudioDriver* driver) override {
    if (auto* silent = dynamic_cast<X2SilentAudioDriver*>(driver)) {
      delete silent;
      return;
    }
    SDLAudioSystem::DestroyDriver(driver);
  }
};

// Frame rate and frame time graph in the top-right corner (F2, bind_lr_frame_overlay). The
// times are the game's own frames (its main loop, x2_hooks.cpp), not the window's redraws.
class X2FrameOverlay final : public rex::ui::ImGuiDialog {
 public:
  explicit X2FrameOverlay(rex::ui::ImGuiDrawer* drawer) : ImGuiDialog(drawer) {}

 protected:
  void OnDraw(ImGuiIO& io) override {
    constexpr int kMax = 240;
    float ms[kMax];
    int n = X2RecentFrameTimesMs(ms, kMax);
    if (n < 2) return;
    // Frame rate over the last half second; the average, slowest, and target lines below.
    double sum = 0;
    int count = 0;
    for (int i = n - 1; i >= 0 && sum < 500.0; --i, ++count) sum += ms[i];
    double fps = sum > 0 ? 1000.0 * count / sum : 0;
    float worst = 0;
    for (int i = 0; i < n; ++i) worst = std::max(worst, ms[i]);

    float s = std::max(1.0f, io.DisplaySize.y / 1080.0f);
    float pad = 12.0f * s;
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x - pad, pad), ImGuiCond_Always, ImVec2(1.0f, 0.0f));
    ImGui::SetNextWindowBgAlpha(0.45f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(8.0f * s, 6.0f * s));
    const ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                                   ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoNav |
                                   ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing;
    if (ImGui::Begin("##x2_frame_overlay", nullptr, flags)) {
      ImGui::SetWindowFontScale(s);
      ImU32 fps_color = fps >= 55 ? IM_COL32(120, 230, 120, 255)
                        : fps >= 28 ? IM_COL32(240, 220, 110, 255)
                                    : IM_COL32(250, 110, 100, 255);
      ImGui::PushStyleColor(ImGuiCol_Text, fps_color);
      ImGui::Text("%.0f FPS", fps);
      ImGui::PopStyleColor();
      ImGui::SameLine();
      ImGui::TextDisabled("%.1f ms  max %.0f ms", count ? sum / count : 0.0, worst);

      // Graph: one bar per frame, oldest left; lines at 16.7 ms (60 fps) and 33.3 ms (30 fps).
      ImVec2 size(240.0f * s, 56.0f * s);
      ImVec2 p0 = ImGui::GetCursorScreenPos();
      ImGui::Dummy(size);
      ImDrawList* dl = ImGui::GetWindowDrawList();
      float top = std::max(50.0f, worst * 1.1f);
      auto y_of = [&](float v) { return p0.y + size.y - std::min(v, top) / top * size.y; };
      dl->AddRectFilled(p0, ImVec2(p0.x + size.x, p0.y + size.y), IM_COL32(0, 0, 0, 90));
      for (float line : {16.7f, 33.3f}) {
        float y = y_of(line);
        dl->AddLine(ImVec2(p0.x, y), ImVec2(p0.x + size.x, y), IM_COL32(255, 255, 255, 60));
      }
      float w = size.x / kMax;
      int first = kMax - n;
      for (int i = 0; i < n; ++i) {
        float v = ms[i];
        ImU32 c = v <= 17.5f ? IM_COL32(120, 230, 120, 220)
                  : v <= 34.5f ? IM_COL32(240, 220, 110, 220)
                               : IM_COL32(250, 110, 100, 230);
        float x = p0.x + (first + i) * w;
        dl->AddRectFilled(ImVec2(x, y_of(v)), ImVec2(x + std::max(1.0f, w - 0.5f), p0.y + size.y), c);
      }
    }
    ImGui::End();
    ImGui::PopStyleVar();
  }
};

class Ffxiii2App : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<Ffxiii2App>(new Ffxiii2App(ctx, "ffxiii2",
        PPCImageConfig));
  }

  // Button Prompts "Auto": 1 for a PlayStation controller, 0 for an Xbox one, -1 for other kinds.
  // SDL's type first; where the backend gives no type, the USB vendor (Sony 054C, Microsoft
  // 045E) or the name decides.
  static int PadKind(SDL_Gamepad* pad) {
    switch (SDL_GetGamepadType(pad)) {
      case SDL_GAMEPAD_TYPE_PS3:
      case SDL_GAMEPAD_TYPE_PS4:
      case SDL_GAMEPAD_TYPE_PS5:
        return 1;
      case SDL_GAMEPAD_TYPE_XBOX360:
      case SDL_GAMEPAD_TYPE_XBOXONE:
        return 0;
      default:
        break;
    }
    const Uint16 vendor = SDL_GetGamepadVendor(pad);
    if (vendor == 0x054C) return 1;
    if (vendor == 0x045E) return 0;
    const std::string name = SDL_GetGamepadName(pad) ? SDL_GetGamepadName(pad) : "";
    if (name.find("Xbox") != std::string::npos) return 0;
    for (const char* ps : {"DualSense", "DualShock", "PS5", "PS4", "PS3", "PlayStation"})
      if (name.find(ps) != std::string::npos) return 1;
    if (name == "Wireless Controller") return 1;  // DualShock 4 / DualSense over Bluetooth on Linux
    return -1;
  }

  // Screenshot on the Xbox Series controller Share/capture button (SDL_GAMEPAD_BUTTON_MISC1),
  // Back + left stick click, or F12 while the game window has focus: saves the game's output
  // frame as <user data>\screenshots\X2_<time>.png. The runtime's own SDL instance lives inside
  // rexruntime.dll, so this keeps a separate read-only gamepad watcher. Through XInput Windows
  // does not report the Share button; SDL reads it through GameInput (its share-button callback)
  // for USB / wireless-adapter controllers, and through HIDAPI for Bluetooth ones.
  // The same watcher turns keyboard and mouse on while no controller is connected.
  // LR_NO_CAPTURE_BUTTON=1 turns it off.
  void StartCaptureButtonWatcher() {
    if (std::getenv("LR_NO_CAPTURE_BUTTON")) return;
    capture_thread_ = std::thread([this] {
      SDL_SetHint(SDL_HINT_JOYSTICK_GAMEINPUT, "1");
      SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_XBOX, "1");  // Bluetooth Xbox Series: Share in the HID report
#if defined(LR_GAMEINPUT_SHARE)
      if (const char* why = lr_gameinput::Start())
        REXLOG_INFO("Capture button: GameInput share button not available: {}", why);
      else
        REXLOG_INFO("Capture button: Share button read through GameInput (API {})", GAMEINPUT_API_VERSION);
#endif
      if (!SDL_InitSubSystem(SDL_INIT_GAMEPAD)) {
        REXLOG_WARN("Capture button: SDL gamepad init failed: {}", SDL_GetError());
        return;
      }
      std::vector<SDL_Gamepad*> pads;
      bool was_down = false;
      // Button Prompts "Auto": the controller that last gave input decides (Xbox -> 360,
      // DualShock / DualSense -> PS3; other kinds don't change it).
      std::unordered_map<SDL_JoystickID, uint64_t> pad_state;
      SDL_JoystickID active_pad = 0;
      auto next_scan = std::chrono::steady_clock::now();
      while (!capture_stop_) {
        SDL_UpdateGamepads();
        if (std::chrono::steady_clock::now() >= next_scan) {
          for (auto* pad : pads) SDL_CloseGamepad(pad);
          pads.clear();
          int count = 0;
          if (SDL_JoystickID* ids = SDL_GetGamepads(&count)) {
            for (int i = 0; i < count; ++i)
              if (SDL_Gamepad* pad = SDL_OpenGamepad(ids[i])) {
                pads.push_back(pad);
                // Once per controller: does this backend report the Share button at all?
                if (logged_pads_.insert(ids[i]).second) {
                  const char* name = SDL_GetGamepadName(pad);
                  REXLOG_INFO("Capture button: {} (type {}, {:04x}:{:04x}): Share button {}; "
                              "Back + left stick click or F12 also take a screenshot",
                              name ? name : "controller", int(SDL_GetGamepadType(pad)), SDL_GetGamepadVendor(pad),
                              SDL_GetGamepadProduct(pad),
                              SDL_GamepadHasButton(pad, SDL_GAMEPAD_BUTTON_MISC1)
                                  ? "reported"
                                  : "not reported through XInput (needs the GameInput runtime, or Bluetooth)");
                }
              }
            SDL_free(ids);
          }
          UpdateKeyboardMouseMode(pads);
          next_scan = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        }
        for (auto* pad : pads) {
          uint64_t state = 0;
          for (int b = 0; b < SDL_GAMEPAD_BUTTON_COUNT && b < 48; ++b)
            if (SDL_GetGamepadButton(pad, SDL_GamepadButton(b))) state |= uint64_t(1) << b;
          for (int a = 0; a < SDL_GAMEPAD_AXIS_COUNT && a < 8; ++a)
            if (std::abs(int(SDL_GetGamepadAxis(pad, SDL_GamepadAxis(a)))) > 16000) state |= uint64_t(1) << (48 + a);
          SDL_JoystickID id = SDL_GetGamepadID(pad);
          auto [it, added] = pad_state.try_emplace(id, state);
          bool used = (!added && it->second != state && state) || (added && !active_pad);
          it->second = state;
          if (!used || id == active_pad) continue;
          int kind = PadKind(pad);
          if (kind < 0) continue;
          active_pad = id;
          const char* name = SDL_GetGamepadName(pad);
          X2UiStyleForController(kind, name ? name : (kind ? "PlayStation controller" : "Xbox controller"));
        }
        // Share button, or Back + left stick click (XInput does not report Share, and the
        // Windows Game Bar can keep it for itself).
        bool down = false;
#if defined(LR_GAMEINPUT_SHARE)
        down = lr_gameinput::g_share_down.load(std::memory_order_relaxed);
#endif
        for (auto* pad : pads)
          down |= SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_MISC1) ||
                  (SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_BACK) &&
                   SDL_GetGamepadButton(pad, SDL_GAMEPAD_BUTTON_LEFT_STICK));
#if defined(_WIN32)
        if (GetAsyncKeyState(VK_F12) & 0x8000) {
          DWORD pid = 0;
          GetWindowThreadProcessId(GetForegroundWindow(), &pid);
          down |= pid == GetCurrentProcessId();
        }
#endif
#if defined(__ANDROID__)
        // The touch layer's screenshot button (X2Touch.java).
        if (g_x2_screenshot_request.exchange(false)) SaveScreenshot();
#endif
        if (down && !was_down) SaveScreenshot();
        was_down = down;
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
      }
      for (auto* pad : pads) SDL_CloseGamepad(pad);
      SDL_QuitSubSystem(SDL_INIT_GAMEPAD);
    });
  }

  // Keyboard and mouse: the runtime's keyboard/mouse controller (mnk_mode) is on only while no
  // controller is connected, so a controller always wins. Bindings come from keyboard.txt in
  // the settings folder ("keybind_x = M" lines; written on first start).
  static constexpr const char* kKeyboardDefaults =
      "# Final Fantasy XIII-2 - keyboard (used only while no controller is connected).\n"
      "# keyboard layout x2 v1: the Lightning Returns port's layout (the Steam versions' keys).\n"
      "# One \"setting = keys\" per line; several keys separated by commas. Key names: A-Z, 0-9,\n"
      "# Space, Return, Backspace, Tab, Escape, Up/Down/Left/Right, Comma, Period, Semicolon,\n"
      "# Quote, F1-F12, Numpad0-Numpad9. A Shift+/Ctrl+/Alt+ prefix requires that modifier.\n"
      "# Move W A S D, camera I J K L, camera reset Comma\n"
      "keybind_lstick_up = W\n"
      "keybind_lstick_down = S\n"
      "keybind_lstick_left = A\n"
      "keybind_lstick_right = D\n"
      "keybind_rstick_up = I\n"
      "keybind_rstick_down = K\n"
      "keybind_rstick_left = J\n"
      "keybind_rstick_right = L\n"
      "keybind_rstick_press = Comma\n"
      "keybind_lstick_press = X\n"
      "# Enter confirm, Backspace cancel / jump, M map, Period menu\n"
      "keybind_a = Return\n"
      "keybind_b = Backspace\n"
      "keybind_x = M\n"
      "keybind_y = Period\n"
      "keybind_right_shoulder = U\n"
      "keybind_left_shoulder = E\n"
      "keybind_left_trigger = Q\n"
      "keybind_right_trigger = O\n"
      "# Esc pause menu; F1 Back; arrow keys D-pad\n"
      "keybind_start = Escape\n"
      "keybind_back = F1\n"
      "keybind_dpad_up = Up\n"
      "keybind_dpad_down = Down\n"
      "keybind_dpad_left = Left\n"
      "keybind_dpad_right = Right\n"
      "# Set true to turn the camera with the mouse.\n"
      "mnk_mouse = false\n"
      "mnk_sensitivity = 1.0\n";

  void LoadKeyboardBindings() {
    auto path = user_data_root() / "keyboard.txt";
    std::error_code ec;
    if (!std::filesystem::exists(path, ec)) {
      std::filesystem::create_directories(user_data_root(), ec);
      if (FILE* f = std::fopen(path.string().c_str(), "w")) {
        std::fputs(kKeyboardDefaults, f);
        std::fclose(f);
      }
    }
    std::ifstream in(path);
    std::string line;
    int applied = 0;
    while (std::getline(in, line)) {
      auto trim = [](std::string v) {
        while (!v.empty() && (v.back() == ' ' || v.back() == '\r' || v.back() == '\t')) v.pop_back();
        size_t i = 0;
        while (i < v.size() && (v[i] == ' ' || v[i] == '\t')) ++i;
        return v.substr(i);
      };
      line = trim(line);
      if (line.empty() || line[0] == '#') continue;
      size_t eq = line.find('=');
      if (eq == std::string::npos) continue;
      std::string name = trim(line.substr(0, eq)), value = trim(line.substr(eq + 1));
      if (name.rfind("keybind_", 0) != 0 && name != "mnk_mouse" && name != "mnk_sensitivity") continue;
      if (rex::cvar::SetFlagByName(name, value)) {
        ++applied;
      } else {
        REXLOG_WARN("XIII-2 keyboard: unknown setting '{}' in {}", name, path.string());
      }
    }
    REXLOG_INFO("XIII-2 keyboard: {} binding(s) from {}", applied, path.string());
  }

  void UpdateKeyboardMouseMode(const std::vector<SDL_Gamepad*>& pads) {
#if defined(__ANDROID__)
    (void)pads;  // Android: the touch layer instead (x2_android.cpp)
    return;
#endif
    int controllers = 0;
    for (auto* pad : pads)
      if (!SDL_IsJoystickVirtual(SDL_GetGamepadID(pad))) ++controllers;
    bool want = controllers == 0;
    if (keyboard_mode_known_ && want == keyboard_mode_) return;
    if (!keyboard_mode_known_) LoadKeyboardBindings();
    keyboard_mode_known_ = true;
    keyboard_mode_ = want;
    rex::cvar::SetFlagByName("mnk_mode", want ? "true" : "false");
    REXLOG_INFO("XIII-2 keyboard: keyboard and mouse {} ({} controller(s) connected)", want ? "on" : "off",
                controllers);
  }

  void SaveScreenshot() {
    auto* graphics = runtime() ? runtime()->graphics_system() : nullptr;
    auto* presenter = graphics ? graphics->presenter() : nullptr;
    rex::ui::RawImage image;
    if (!presenter || !presenter->CaptureGuestOutput(image) || !image.width) {
      REXLOG_WARN("Capture button: no frame to capture");
      return;
    }
    // RawImage is R8G8B8X8; force X to opaque for PNG.
    for (uint32_t y = 0; y < image.height; ++y)
      for (uint32_t x = 0; x < image.width; ++x) image.data[y * image.stride + x * 4 + 3] = 0xFF;
    SDL_Surface* surface = SDL_CreateSurfaceFrom(int(image.width), int(image.height), SDL_PIXELFORMAT_RGBA32,
                                                 image.data.data(), int(image.stride));
    if (!surface) return;
    std::time_t now = std::time(nullptr);
    std::tm local{};
#if defined(_WIN32)
    localtime_s(&local, &now);
#else
    localtime_r(&now, &local);
#endif
    char name[64];
    std::strftime(name, sizeof(name), "FFXIII-2_%Y%m%d_%H%M%S", &local);
    auto dir = user_data_root() / "screenshots";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    auto path = dir / (std::string(name) + ".png");
    for (int n = 2; std::filesystem::exists(path, ec); ++n)
      path = dir / (std::string(name) + "_" + std::to_string(n) + ".png");
    if (SDL_SavePNG(surface, path.string().c_str())) {
      REXLOG_INFO("Capture button: saved {} ({}x{})", path.string(), image.width, image.height);
    } else {
      REXLOG_WARN("Capture button: could not write {}: {}", path.string(), SDL_GetError());
    }
    SDL_DestroySurface(surface);
  }

  void OnShutdown() override {
    capture_stop_ = true;
    if (capture_thread_.joinable()) capture_thread_.join();
    // Before the runtime destroys the ImGui drawer the overlay is registered with.
    rex::ui::UnregisterBind("bind_lr_frame_overlay");
    frame_overlay_.reset();
  }

  // F2: frame rate and frame time graph (rebindable as bind_lr_frame_overlay in the F4 settings).
  void OnCreateDialogs(rex::ui::ImGuiDrawer* drawer) override {
    rex::ui::RegisterBind("bind_lr_frame_overlay", "F2", "Frame rate and frame time graph", [this, drawer] {
      if (frame_overlay_) {
        frame_overlay_.reset();
      } else {
        frame_overlay_ = std::make_unique<X2FrameOverlay>(drawer);
      }
      REXLOG_INFO("XIII-2 frame overlay: {}", frame_overlay_ ? "on" : "off");
    });
    // The mouse cursor hides after 5 s without movement (keyboard mode's mouse capture hides it
    // outright).
    if (window() && window()->GetCursorVisibility() == rex::ui::Window::CursorVisibility::kVisible) {
      window()->SetCursorAutoHideDelayMs(5000);
      window()->SetCursorVisibility(rex::ui::Window::CursorVisibility::kAutoHidden);
    }
  }

  void OnPostLaunchModule(rex::system::XThread*) override { StartCaptureButtonWatcher(); }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    config.audio_factory = REX_AUDIO_BACKEND(X2AudioSystem);
    // XIII-2_Test.ps1 looks for this line: without it the game that ran is an older build.
    REXLOG_INFO("XIII-2 build: test v41 (title update 0.0.1.13)");
    // Render resolution (resolution.txt in the settings folder): read by the renderer plugin
    // when it is loaded, right after this.
    X2ResolutionStartup(user_data_root());
    // Frame rate 120 (PC): the 120 Hz vertical blank, before the graphics start (x2_settings.cpp).
    X2FrameRateStartup();
  }

  // Started without arguments (double-clicked): the folders come from ffxiii2_launch.txt next
  // to the exe ("game_data_root = <360 game folder>"; also update_data_root and LR_* settings),
  // else game\ next to the exe. Command line arguments and environment variables win over both.
  void OnConfigurePaths(rex::PathConfig& paths) override {
#if defined(_WIN32)
    const std::filesystem::path exe_dir = rex::filesystem::GetExecutableFolder();
    auto resolve = [&](const std::string& v) {
      std::filesystem::path p = std::filesystem::u8path(v);
      return p.is_absolute() ? p : exe_dir / p;
    };
    // _putenv_s, not SetEnvironmentVariableW: the game code and the renderer read these with
    // getenv, which only sees the C runtime's copy of the environment.
    auto set_env = [](const char* name, const std::string& value) {
      const char* cur = std::getenv(name);
      if (!cur || !*cur) _putenv_s(name, value.c_str());
    };
    std::ifstream launch(exe_dir / "ffxiii2_launch.txt");
    std::string line;
    while (std::getline(launch, line)) {
      while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
      size_t eq = line.find('=');
      if (line.empty() || line[0] == '#' || eq == std::string::npos) continue;
      std::string key = line.substr(0, eq), value = line.substr(eq + 1);
      while (!key.empty() && key.back() == ' ') key.pop_back();
      while (!value.empty() && value.front() == ' ') value.erase(0, 1);
      if (value.size() >= 2 && value.front() == '"' && value.back() == '"') value = value.substr(1, value.size() - 2);
      if (key == "game_data_root" && paths.game_data_root.empty()) {
        paths.game_data_root = resolve(value);
      } else if (key == "update_data_root" && paths.update_data_root.empty()) {
        paths.update_data_root = resolve(value);
      } else if (key.rfind("LR_", 0) == 0) {
        set_env(key.c_str(), value);
      }
    }
    std::error_code ec;
    if (paths.game_data_root.empty() && std::filesystem::exists(exe_dir / "game" / "default.xex", ec)) {
      paths.game_data_root = exe_dir / "game";
    }
#endif
    // The update folder (UPDATE:, the game's "udp/"). This build is the title update's executable
    // (XIII-2_Test.ps1 recompiles default.xex with its default.xexp), so its patch archives belong
    // there; the game switches its downloadable content on when they load (sub_8225FC40). Choices:
    //   update_tu\      the 360 title update, unpacked by X2_Import_360_Content.ps1 -TuOut
    //   update_merged\  the title update with the Japanese voices added (x2_jpvoice --merge-update)
    //   jpvoice\        the Japanese voices alone: only when no title update was unpacked (the
    //                   title update's code then runs without its data; not expected)
    {
      const std::filesystem::path exe = X2InstallDir();
      const std::filesystem::path jp = exe / "jpvoice", tu = exe / "update_tu", merged = exe / "update_merged";
      std::ifstream in(paths.user_data_root / "voice.txt");
      std::string v;
      std::getline(in, v);
      while (!v.empty() && (v.back() == '\r' || v.back() == ' ')) v.pop_back();
      std::error_code ec2;
      const bool installed = std::filesystem::exists(jp / "filelist_patch_u.x360.bin", ec2);
      const bool have_tu = std::filesystem::exists(tu / "x2_title_update.txt", ec2);
      const bool have_merged = have_tu && std::filesystem::exists(merged / "x2_merged.txt", ec2);
      const bool given = !paths.update_data_root.empty();
      bool on = false;
      std::string note;
      if (given) {
        note = "English (an update folder was given)";
      } else if (v == "jp" && installed && !have_tu) {
        paths.update_data_root = jp;
        X2SetUpdateKind(1);
        on = true;
        note = "Japanese (" + jp.string() + ")";
      } else if (v == "jp" && installed && have_merged) {
        paths.update_data_root = merged;
        X2SetUpdateKind(2);
        on = true;
        note = "Japanese, with the title update (" + merged.string() + ")";
      } else if (have_tu) {
        paths.update_data_root = tu;
        X2SetUpdateKind(2);
        note = v == "jp" && installed ? "English (the title update and the Japanese voices are not combined yet: run XIII-2_Test again)"
                                      : "English (Settings > Port Settings > Voice Language)";
      } else {
        note = std::string("English (") + (!installed ? "the Japanese voices are not installed" : "Settings > Port Settings > Voice Language") + ")";
      }
      if (have_tu) REXLOG_INFO("XIII-2 title update: {}", tu.string());
      else note += "; NO TITLE UPDATE in " + tu.string() + " (this build is made for it)";
      X2SetVoiceAtStart(on ? 1 : 0, note);
    }
  }

  void OnPostSetup() override {
    X2FrameRateAfterSetup();  // 120 fps: the game is told 60 Hz again (x2_settings.cpp)
    auto* kernel_state = rex::system::kernel_state();
    auto* vfs = kernel_state ? kernel_state->file_system() : nullptr;
    if (!vfs) return;
    // The DLC packages' archives with Japanese voices (jpvoice_dlc\NNNNNNN\, XIII-2_Test.ps1), as
    // X2JPDLC:\; x2_hooks.cpp opens them instead of the packages' own while the voices are Japanese.
    {
      const std::filesystem::path dir = X2InstallDir() / "jpvoice_dlc";
      std::error_code ec;
      if (std::filesystem::is_directory(dir, ec)) {
        auto device = std::make_unique<rex::filesystem::HostPathDevice>("\\Device\\X2JpDlc", dir, true);
        if (device->Initialize() && vfs->RegisterDevice(std::move(device)) &&
            vfs->RegisterSymbolicLink("X2JPDLC:", "\\Device\\X2JpDlc")) {
          X2SetJpDlcMounted();
          REXLOG_INFO("XIII-2 voices: DLC voices in Japanese from {} ({})", dir.string(),
                      X2VoiceAtStart() ? "used" : "not used: the voices are English");
        }
      }
    }
    // Debug: X2_DUMP_IMAGE=<file> writes the loaded game image (0x82000000, with a title update's
    // default.xexp applied by the loader when it sits next to default.xex) a few seconds after
    // start, for matching the port's addresses to an updated executable.
    if (const char* dump = std::getenv("X2_DUMP_IMAGE"); dump && dump[0]) {
      std::thread([path = std::string(dump), kernel_state] {
        std::this_thread::sleep_for(std::chrono::seconds(4));
        uint8_t* membase = kernel_state->memory()->virtual_membase();
        if (FILE* f = std::fopen(path.c_str(), "wb")) {
          std::fwrite(membase + 0x82000000u, 1, 0x00F10000u, f);
          std::fclose(f);
          REXLOG_INFO("XIII-2 image: written to {}", path);
        }
        // X2_DUMP_MODULE=z0161: that zone module too (loaded here, its .xexp applied), to <file>.zNNNN
        if (const char* mod = std::getenv("X2_DUMP_MODULE"); mod && mod[0]) {
          auto module = rex::system::object_ref<rex::system::UserModule>(new rex::system::UserModule(kernel_state));
          const std::string guest = std::string("game:\\") + mod + ".xex";
          if (static_cast<uint32_t>(module->LoadFromFile(guest)) == 0 && module->xex_module()) {
            const uint32_t at = module->xex_module()->base_address(), size = module->xex_module()->image_size();
            if (FILE* f = std::fopen((path + "." + mod).c_str(), "wb")) {
              std::fwrite(membase + at, 1, size, f);
              std::fclose(f);
            }
            REXLOG_INFO("XIII-2 image: {} at {:08X}, {:X} bytes, written", mod, at, size);
          } else {
            REXLOG_INFO("XIII-2 image: {} could not be loaded", guest);
          }
        }
      }).detach();
    }
  }

  std::unique_ptr<X2FrameOverlay> frame_overlay_;
  std::thread capture_thread_;
  std::unordered_set<SDL_JoystickID> logged_pads_;  // controllers already described in the log
  std::atomic<bool> capture_stop_{false};
  bool keyboard_mode_known_ = false;  // UpdateKeyboardMouseMode (capture thread only)
  bool keyboard_mode_ = false;
};
