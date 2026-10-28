// ffxiii2 - Delete in the Save screen: the storage device button (Y / Triangle) deletes the
// highlighted save data after a Yes/No question, the screen's own (there is one storage device
// here, so the button has nothing else to do).
//
// Save screen (static analysis):
//   input            sub_826BD8A8(?, input r4, widget r5): screen = widget->vfunc[0x164](widget);
//                    sub_826BD5A0(screen) = busy; pressed buttons input+0xC, confirm mask
//                    [0x82E51FCC], cancel [0x82E51FD0]; bit 0x10 = storage device: gui+0xE24 = 1,
//                    sound 4, sub_825A3AA0(gui, screen+0x4C, 0) closes the screen. The save
//                    worker (disc executable: sub_824F37A0) then clears the device, selects it again
//                    (XamShowDeviceSelectorUI, answered at once here), lists the saves again,
//                    and opens the screen again - so a deletion shows at once.
//   step             sub_826BF670(screen): +0x100 state (1 = back to the list, 2 = list),
//                    +0x104 row, +0x10C rows, +0x114 save (1) or load (0).
//   question         sub_825A9558(gui, name 0x82E89D90, 0, 0, 0, 0, "q|yes|no", 1, f1 =
//                    [0x820F1390], stack +0x5C..+0x7C), as the overwrite question (stack
//                    +0x74 = 1); then window = sub_825A1DF0(gui, name, 1), dialog =
//                    window->vfunc[4](window), sub_826A9820(dialog, 1, 1),
//                    dialog->vfunc[0x17C](dialog, 0, 1). Answered when sub_826A76C0(dialog, 1)
//                    is nonzero; dialog+0x8C = 0 for Yes. The texts go through sub_8259FBF8
//                    (LocalizedText::Set), where x2_config_menu.cpp answers the "$x2_" keys.
//   saves            manager [[0x82CD3348]+0x43C]; its list [manager+0x178] while the screen is
//                    open: +0x8 entries (0x180 bytes, sorted as the rows are), +0x10 user, +0x14
//                    s8 count. An entry starts with its XCONTENT_DATA (0x134 bytes, file name at
//                    +0x108, "ff13-NN.dat"). Row i shows entry i (sub_8250BEB0(manager, row));
//                    in save mode the row after the last entry is New Save Data.
// X2_SAVE_DELETE=0 leaves the button as the game has it.

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

#include <rex/hook.h>
#include <rex/logging/macros.h>
#include <rex/ppc/func.h>
#include <rex/runtime.h>

namespace {

constexpr uint32_t kGui = 0x82E86E8C;          // [kGui] = GUI manager
constexpr uint32_t kGameState = 0x82CD3348;    // [kGameState]+0x43C = save manager
constexpr uint32_t kQuestionName = 0x82E89D90;  // the Save screen's Yes/No window name
constexpr uint32_t kDialogScale = 0x820F1390;   // float the game passes in f1
constexpr uint32_t kConfirmMask = 0x82E51FCC, kCancelMask = 0x82E51FD0;
constexpr uint32_t kStorageButton = 0x10;
constexpr uint32_t kSoundBuzzer = 0x48;
constexpr const char* kQuestion = "$x2_delq|$theme_sel_yes|$theme_sel_no";

uint32_t ld32(uint8_t* base, uint32_t a) {
  uint32_t v;
  std::memcpy(&v, base + a, 4);
  return __builtin_bswap32(v);
}

void st32(uint8_t* base, uint32_t a, uint32_t v) {
  v = __builtin_bswap32(v);
  std::memcpy(base + a, &v, 4);
}

bool enabled() {
  static const bool on = [] {
    const char* v = std::getenv("X2_SAVE_DELETE");
    return !(v && v[0] == '0');
  }();
  return on;
}

std::string file_name(uint8_t* base, uint32_t entry) {
  std::string s;
  for (uint32_t i = 0; i < 42 && base[entry + 0x108 + i]; ++i) s.push_back(char(base[entry + 0x108 + i]));
  return s;
}

// A copy of the caller's context with its own stack below the caller's (back chain written),
// so the game functions called from a hook leave the hook's context as it was.
struct GuestCall {
  PPCContext c;
  uint8_t* base;
  GuestCall(PPCContext& ctx, uint8_t* b) : c(ctx), base(b) {
    c.r1.u32 = (ctx.r1.u32 - 0x400) & ~15u;
    std::memset(base + c.r1.u32, 0, 0x100);
    st32(base, c.r1.u32, ctx.r1.u32);
  }
  // Scratch memory above the callee's frame (inside the 0x400 bytes this call reserved).
  uint32_t scratch() const { return c.r1.u32 + 0x200; }
};

bool call_virtual(GuestCall& g, uint32_t object, uint32_t offset) {
  const uint32_t target = ld32(g.base, ld32(g.base, object) + offset);
  PPCFunc* fn = rex::runtime::ResolveIndirectFunction(target);
  if (!fn) return false;
  g.c.r3.u64 = object;
  fn(g.c, g.base);
  return true;
}

// The question being asked: which screen, which save.
std::mutex g_mutex;
uint32_t g_screen = 0;
uint32_t g_entry = 0;
uint32_t g_user = 0;
std::string g_file;
std::atomic<uint32_t> g_close{0};  // screen to close (and list again) after a deletion

uint32_t save_list(uint8_t* base) {
  const uint32_t state = ld32(base, kGameState);
  const uint32_t manager = state ? ld32(base, state + 0x43C) : 0;
  return manager ? ld32(base, manager + 0x178) : 0;
}

}  // namespace

REX_EXTERN(sub_825A9558);  // open a window: (gui, name, ...) - see above
REX_EXTERN(sub_825A1DF0);  // find a window: (gui, name, 1)
REX_EXTERN(sub_826A9820);  // dialog setup: (dialog, 1, 1)
REX_EXTERN(sub_826A76C0);  // dialog answered: (dialog, 1)
REX_EXTERN(sub_825A9DB8);  // menu sound: (gui, id)
REX_EXTERN(sub_825A3AA0);  // close a screen: (gui, id, 0)
REX_EXTERN(sub_826BD5A0);  // Save screen busy: (screen)
REX_EXTERN(sub_82B61FC8);  // XamContentDelete(user, XCONTENT_DATA*, overlapped)
REX_EXTERN(sub_825B56E0);  // game heap allocate (size)

namespace {

uint32_t find_question(PPCContext& ctx, uint8_t* base) {
  GuestCall g(ctx, base);
  g.c.r3.u64 = ld32(base, kGui);
  g.c.r4.u64 = kQuestionName;
  g.c.r5.u64 = 1;
  sub_825A1DF0(g.c, base);
  const uint32_t window = g.c.r3.u32;
  if (!window || !call_virtual(g, window, 4)) return 0;
  return g.c.r3.u32;
}

// The question in guest memory that stays (the window may keep the pointer): allocated once.
uint32_t question_text(PPCContext& ctx, uint8_t* base) {
  static uint32_t text = 0;
  if (!text) {
    GuestCall g(ctx, base);
    g.c.r3.u64 = std::strlen(kQuestion) + 1;
    sub_825B56E0(g.c, base);
    text = g.c.r3.u32;
    if (text) std::memcpy(base + text, kQuestion, std::strlen(kQuestion) + 1);
  }
  return text;
}

bool open_question(PPCContext& ctx, uint8_t* base) {
  const uint32_t text = question_text(ctx, base);
  if (!text) return false;
  {
    GuestCall g(ctx, base);
    uint32_t scale;
    std::memcpy(&scale, base + kDialogScale, 4);
    scale = __builtin_bswap32(scale);
    float f;
    std::memcpy(&f, &scale, 4);
    const uint32_t sp = g.c.r1.u32;
    st32(base, sp + 0x5C, 0);
    st32(base, sp + 0x64, 0);
    st32(base, sp + 0x6C, 0);
    st32(base, sp + 0x74, 1);
    st32(base, sp + 0x7C, 0);
    g.c.r3.u64 = ld32(base, kGui);
    g.c.r4.u64 = kQuestionName;
    g.c.r5.u64 = 0;
    g.c.r6.u64 = 0;
    g.c.r7.u64 = 0;
    g.c.r8.u64 = 0;
    g.c.r9.u64 = text;
    g.c.r10.u64 = 1;
    g.c.f1.f64 = f;
    sub_825A9558(g.c, base);
  }
  const uint32_t dialog = find_question(ctx, base);
  if (!dialog) return false;
  GuestCall g(ctx, base);
  g.c.r3.u64 = dialog;
  g.c.r4.u64 = 1;
  g.c.r5.u64 = 1;
  sub_826A9820(g.c, base);
  g.c.r4.u64 = 0;
  g.c.r5.u64 = 1;
  call_virtual(g, dialog, 0x17C);
  return true;
}

void sound(PPCContext& ctx, uint8_t* base, uint32_t id) {
  GuestCall g(ctx, base);
  g.c.r3.u64 = ld32(base, kGui);
  g.c.r4.u64 = id;
  sub_825A9DB8(g.c, base);
}

uint32_t delete_save(PPCContext& ctx, uint8_t* base, uint32_t user, uint32_t entry) {
  GuestCall g(ctx, base);
  const uint32_t data = g.scratch();
  std::memcpy(base + data, base + entry, 0x134);
  g.c.r3.u64 = user;
  g.c.r4.u64 = data;
  g.c.r5.u64 = 0;
  sub_82B61FC8(g.c, base);
  return g.c.r3.u32;
}

}  // namespace

// Save screen input: the storage device button asks to delete the highlighted save.
REX_EXTERN(__imp__sub_826BD8A8);
REX_HOOK_RAW(sub_826BD8A8) {
  if (!enabled()) {
    __imp__sub_826BD8A8(ctx, base);
    return;
  }
  const uint32_t input = ctx.r4.u32, widget = ctx.r5.u32;
  uint32_t screen = 0;
  if (widget) {
    GuestCall g(ctx, base);
    if (call_virtual(g, widget, 0x164)) screen = g.c.r3.u32;
  }
  {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_screen && g_screen == screen) return;  // our question is open: the list waits
    if (g_screen) {
      REXLOG_INFO("XIII-2 save delete: the screen of the question closed; '{}' kept", g_file);
      g_screen = 0;
    }
  }
  const uint32_t pressed = input ? ld32(base, input + 0xC) : 0;
  if (!screen || !(pressed & kStorageButton) || (pressed & (ld32(base, kConfirmMask) | ld32(base, kCancelMask)))) {
    __imp__sub_826BD8A8(ctx, base);
    return;
  }
  {
    GuestCall g(ctx, base);
    g.c.r3.u64 = screen;
    sub_826BD5A0(g.c, base);
    if (g.c.r3.u32) return;  // busy: the game ignores every button now
  }
  const int32_t row = int32_t(ld32(base, screen + 0x104));
  const uint32_t list = save_list(base);
  const int32_t count = list ? int32_t(int8_t(base[list + 0x14])) : 0;
  const uint32_t entries = list ? ld32(base, list + 8) : 0;
  if (row < 0 || row >= count || !entries) {
    REXLOG_INFO("XIII-2 save delete: row {} has no save data ({} saves)", row, count);
    sound(ctx, base, kSoundBuzzer);
    return;
  }
  const uint32_t entry = entries + uint32_t(row) * 0x180;
  const std::string file = file_name(base, entry);
  if (!open_question(ctx, base)) {
    REXLOG_WARN("XIII-2 save delete: the question did not open; '{}' kept", file);
    sound(ctx, base, kSoundBuzzer);
    return;
  }
  std::lock_guard<std::mutex> lock(g_mutex);
  g_screen = screen;
  g_entry = entry;
  g_user = ld32(base, list + 0x10);
  g_file = file;
  REXLOG_INFO("XIII-2 save delete: asked about row {} ('{}')", row, file);
}

// Save screen step: the answer, the deletion, and the screen closed so the worker lists the
// saves again.
REX_EXTERN(__imp__sub_826BF670);
REX_HOOK_RAW(sub_826BF670) {
  const uint32_t screen = ctx.r3.u32;
  bool answered = false, yes = false;
  uint32_t entry = 0, user = 0;
  std::string file;
  {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_screen && g_screen == screen) {
      const uint32_t dialog = find_question(ctx, base);
      if (!dialog) {
        answered = true;  // closed without an answer
      } else {
        GuestCall g(ctx, base);
        g.c.r3.u64 = dialog;
        g.c.r4.u64 = 1;
        sub_826A76C0(g.c, base);
        if (g.c.r3.u32) {
          answered = true;
          yes = ld32(base, dialog + 0x8C) == 0;
        }
      }
      if (answered) {
        entry = g_entry;
        user = g_user;
        file = g_file;
        g_screen = 0;
        st32(base, screen + 0x100, 1);  // back to the list, as after the game's own questions
      }
    }
  }
  if (answered && yes) {
    if (file_name(base, entry) != file || !save_list(base)) {
      REXLOG_WARN("XIII-2 save delete: the save list changed; '{}' kept", file);
    } else {
      const uint32_t result = delete_save(ctx, base, user, entry);
      REXLOG_INFO("XIII-2 save delete: '{}' deleted -> {:08X}", file, result);
      g_close.store(screen);
    }
  } else if (answered) {
    REXLOG_INFO("XIII-2 save delete: '{}' kept", file);
  }
  __imp__sub_826BF670(ctx, base);
  if (g_close.load() == screen) {
    g_close.store(0);
    GuestCall g(ctx, base);
    const uint32_t gui = ld32(base, kGui);
    st32(base, gui + 0xE24, 1);
    g.c.r3.u64 = gui;
    g.c.r4.u64 = ld32(base, screen + 0x4C);
    g.c.r5.u64 = 0;
    sub_825A3AA0(g.c, base);
  }
}
