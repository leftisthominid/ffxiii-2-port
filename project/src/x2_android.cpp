// Final Fantasy XIII-2 - Android touch layer (android/project/.../X2Touch.java), built into the
// Android build only. As in the Lightning Returns port: the on-screen controls are merged into
// controller 1's state, so the game sees one pad whether or not a controller is connected (with
// none, the on-screen pad is reported as connected). While the Historia Crux menu is open the
// game sees no buttons, and controller presses go to the menu instead. Called from the input
// wrappers in x2_perf.cpp (sub_82B62530 XInputGetState, sub_82B62608 XInputGetKeystroke), under
// their input lock.
#if defined(__ANDROID__)

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <utility>

#include <rex/hook.h>

namespace {
struct X2TouchPad {
  bool active = false;
  uint16_t buttons = 0;
  uint8_t lt = 0, rt = 0;
  int16_t lx = 0, ly = 0, rx = 0, ry = 0;
  uint32_t seq = 0;
};
std::mutex g_touch_mutex;
X2TouchPad g_touch;
std::atomic<bool> g_touch_menu{false};
std::atomic<uint32_t> g_touch_menu_edges{0};
uint16_t g_touch_menu_last = 0;  // under the input lock
std::atomic<bool> g_touch_physical{false};

// Keystrokes made from the on-screen controls' changes, for the screens that read keystrokes
// (the controller's own come from the runtime).
struct X2KeystrokeSynth {
  uint32_t last = 0;                                // touch_bits of the last call
  std::deque<std::pair<uint16_t, uint16_t>> queue;  // (VK_PAD_*, flags: 1 down, 2 up)
};
X2KeystrokeSynth g_touch_keystrokes;  // under the input lock

X2TouchPad touch_get() {
  std::lock_guard<std::mutex> lock(g_touch_mutex);
  return g_touch;
}

uint16_t load_be16(const uint8_t* base, uint32_t address) {
  uint16_t v;
  std::memcpy(&v, base + address, 2);
  return __builtin_bswap16(v);
}
uint32_t load_be32(const uint8_t* base, uint32_t address) {
  uint32_t v;
  std::memcpy(&v, base + address, 4);
  return __builtin_bswap32(v);
}
void store_be16(uint8_t* p, uint16_t v) {
  p[0] = uint8_t(v >> 8);
  p[1] = uint8_t(v);
}

// Bits 0-15 the XInput buttons, 16/17 the triggers, 18-25 the stick directions.
uint32_t touch_bits(const X2TouchPad& t) {
  constexpr int kDead = 16000;
  uint32_t bits = t.buttons;
  if (t.lt > 0x80) bits |= 1u << 16;
  if (t.rt > 0x80) bits |= 1u << 17;
  if (t.ly > kDead) bits |= 1u << 18;
  if (t.ly < -kDead) bits |= 1u << 19;
  if (t.lx < -kDead) bits |= 1u << 20;
  if (t.lx > kDead) bits |= 1u << 21;
  if (t.ry > kDead) bits |= 1u << 22;
  if (t.ry < -kDead) bits |= 1u << 23;
  if (t.rx < -kDead) bits |= 1u << 24;
  if (t.rx > kDead) bits |= 1u << 25;
  return bits;
}

// VK_PAD_* keystroke code for one of the bits above.
uint16_t pad_vk(uint32_t bit) {
  switch (bit) {
    case 0: return 0x5810;   // DPAD_UP
    case 1: return 0x5811;   // DPAD_DOWN
    case 2: return 0x5812;   // DPAD_LEFT
    case 3: return 0x5813;   // DPAD_RIGHT
    case 4: return 0x5814;   // START
    case 5: return 0x5815;   // BACK
    case 6: return 0x5816;   // LTHUMB_PRESS
    case 7: return 0x5817;   // RTHUMB_PRESS
    case 8: return 0x5805;   // LSHOULDER
    case 9: return 0x5804;   // RSHOULDER
    case 12: return 0x5800;  // A
    case 13: return 0x5801;  // B
    case 14: return 0x5802;  // X
    case 15: return 0x5803;  // Y
    case 16: return 0x5806;  // LTRIGGER
    case 17: return 0x5807;  // RTRIGGER
    case 18: return 0x5820;  // LTHUMB_UP
    case 19: return 0x5821;  // LTHUMB_DOWN
    case 20: return 0x5823;  // LTHUMB_LEFT
    case 21: return 0x5822;  // LTHUMB_RIGHT
    case 22: return 0x5830;  // RTHUMB_UP
    case 23: return 0x5831;  // RTHUMB_DOWN
    case 24: return 0x5833;  // RTHUMB_LEFT
    case 25: return 0x5832;  // RTHUMB_RIGHT
    default: return 0;
  }
}
}  // namespace

// After XInputGetState for controller 1 (state = guest X_INPUT_STATE: packet number, then
// X_INPUT_GAMEPAD big endian: buttons u16, LT u8, RT u8, LX, LY, RX, RY s16).
void X2TouchMerge(uint8_t* base, uint32_t state, PPCContext& ctx) {
  uint8_t* st = base + state;
  const bool connected = ctx.r3.u32 == 0;
  if (connected) {
    uint16_t b = load_be16(base, state + 4);
    auto axis = [&](uint32_t o) { return int(int16_t(load_be16(base, state + o))); };
    if (b || st[6] > 0x40 || st[7] > 0x40 || std::abs(axis(8)) > 12000 || std::abs(axis(10)) > 12000 ||
        std::abs(axis(12)) > 12000 || std::abs(axis(14)) > 12000)
      g_touch_physical.store(true, std::memory_order_relaxed);
  }
  if (g_touch_menu.load(std::memory_order_relaxed)) {
    uint16_t b = connected ? load_be16(base, state + 4) : 0;
    // Stick directions count as d-pad presses in the menu.
    int16_t ly = connected ? int16_t(load_be16(base, state + 10)) : 0;
    if (ly > 20000) b |= 0x0001;
    if (ly < -20000) b |= 0x0002;
    g_touch_menu_edges.fetch_or(b & ~g_touch_menu_last, std::memory_order_relaxed);
    g_touch_menu_last = b;
    if (connected) std::memset(st + 4, 0, 12);
    return;
  }
  g_touch_menu_last = 0;
  X2TouchPad t = touch_get();
  if (!t.active) return;
  if (!connected) {
    std::memset(st, 0, 16);
    ctx.r3.u64 = 0;
  }
  store_be16(st + 4, uint16_t(load_be16(base, state + 4) | t.buttons));
  st[6] = std::max(st[6], t.lt);
  st[7] = std::max(st[7], t.rt);
  auto put_axis = [&](uint32_t o, int16_t v) {
    if (v) store_be16(st + o, uint16_t(v));
  };
  put_axis(8, t.lx);
  put_axis(10, t.ly);
  put_axis(12, t.rx);
  put_axis(14, t.ry);
  uint32_t packet = load_be32(base, state) + (t.seq << 16);
  uint32_t be = __builtin_bswap32(packet);
  std::memcpy(st, &be, 4);
}

// After XInputGetKeystroke (user r3, keystroke = guest XINPUT_KEYSTROKE). True when the call's
// result was replaced.
bool X2TouchKeystroke(uint8_t* base, uint32_t user, uint32_t keystroke, PPCContext& ctx) {
  constexpr uint32_t kErrorEmpty = 0x10D2;
  if (g_touch_menu.load(std::memory_order_relaxed)) {
    ctx.r3.u64 = kErrorEmpty;  // the Historia Crux menu is open: the game gets no keystrokes
    return true;
  }
  if (ctx.r3.u32 != kErrorEmpty || !keystroke || ((user & 0xFF) != 0 && (user & 0xFF) != 0xFF)) return false;
  X2TouchPad t = touch_get();
  auto& synth = g_touch_keystrokes;
  uint32_t now = t.active ? touch_bits(t) : 0;
  uint32_t changed = now ^ synth.last;
  for (uint32_t bit = 0; bit < 26; ++bit) {
    if (!(changed & (1u << bit))) continue;
    if (uint16_t vk = pad_vk(bit)) synth.queue.emplace_back(vk, (now & (1u << bit)) ? 0x0001 : 0x0002);
  }
  synth.last = now;
  if (synth.queue.empty()) return false;
  auto [vk, flags] = synth.queue.front();
  synth.queue.pop_front();
  uint16_t be_vk = __builtin_bswap16(vk), be_flags = __builtin_bswap16(flags), zero = 0;
  std::memcpy(base + keystroke, &be_vk, 2);
  std::memcpy(base + keystroke + 2, &zero, 2);
  std::memcpy(base + keystroke + 4, &be_flags, 2);
  base[keystroke + 6] = 0;
  base[keystroke + 7] = 0;
  ctx.r3.u64 = 0;
  return true;
}

// ---------------------------------------------------------------------------------------
// Frame work time for Android's performance hint (android/game/x2_android_main.cpp,
// X2AndroidFrameDone): the main thread's frame time without the frame pacer's sleeping, which
// goes through SleepEx (sub_82B69868) and the yield wrapper (sub_82C186C0).
// ---------------------------------------------------------------------------------------
void X2AndroidFrameDone(uint64_t work_us, double frame_rate);  // x2_android_main.cpp
double X2RunningFrameRate();                                  // x2_settings.cpp

namespace {
thread_local bool t_main_thread = false;
thread_local uint64_t t_sleep_us = 0;

uint64_t now_us() {
  return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
                      std::chrono::steady_clock::now().time_since_epoch()).count());
}

template <void (*Original)(PPCContext&, uint8_t*)>
void timed_sleep(PPCContext& ctx, uint8_t* base) {
  if (!t_main_thread) return Original(ctx, base);
  uint64_t t0 = now_us();
  Original(ctx, base);
  t_sleep_us += now_us() - t0;
}
}  // namespace

REX_EXTERN(__imp__sub_82B69868);
REX_HOOK_RAW(sub_82B69868) { timed_sleep<__imp__sub_82B69868>(ctx, base); }
REX_EXTERN(__imp__sub_82C186C0);
REX_HOOK_RAW(sub_82C186C0) { timed_sleep<__imp__sub_82C186C0>(ctx, base); }

// Start of each of the game's frames, on the main thread (x2_hooks.cpp, sub_82910538).
void X2AndroidFrameStart() {
  static uint64_t last_us = 0;
  t_main_thread = true;
  uint64_t now = now_us();
  if (last_us) {
    uint64_t frame = now - last_us;
    X2AndroidFrameDone(frame > t_sleep_us ? frame - t_sleep_us : 0, X2RunningFrameRate());
  }
  last_us = now;
  t_sleep_us = 0;
}

void X2TouchSetPad(bool active, uint16_t buttons, uint8_t lt, uint8_t rt, int16_t lx, int16_t ly, int16_t rx,
                   int16_t ry) {
  std::lock_guard<std::mutex> lock(g_touch_mutex);
  X2TouchPad& t = g_touch;
  if (t.active == active && t.buttons == buttons && t.lt == lt && t.rt == rt && t.lx == lx && t.ly == ly &&
      t.rx == rx && t.ry == ry)
    return;
  t.active = active;
  t.buttons = buttons;
  t.lt = lt;
  t.rt = rt;
  t.lx = lx;
  t.ly = ly;
  t.rx = rx;
  t.ry = ry;
  ++t.seq;
}

std::atomic<bool> g_x2_screenshot_request{false};  // ffxiii2_app.h's capture watcher takes one when set

void X2TouchSetMenuOpen(bool open) {
  g_touch_menu.store(open, std::memory_order_relaxed);
  g_touch_menu_edges.store(0, std::memory_order_relaxed);
}
uint32_t X2TouchTakeMenuButtons() { return g_touch_menu_edges.exchange(0, std::memory_order_relaxed); }
bool X2TouchTakePhysicalActivity() { return g_touch_physical.exchange(false, std::memory_order_relaxed); }

#endif
