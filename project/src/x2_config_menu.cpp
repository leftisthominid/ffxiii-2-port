// ffxiii2 - Settings > Port Settings: the port's settings in the game's own Settings menu.
//
// XIII-2's item list holds 17 items (15 before the title update) and the game fills at most 15,
// so the port adds one item, "Port Settings", whose A opens a second page of the same list (the
// way the Lightning Returns port's Keyboard Settings works): same rows, cursor, sounds, help bar,
// value popup (A opens it, Up/Down + A picks, B cancels), and Yes/No dialog. B goes back to the
// game's page. The items of the page:
//   Voice Language   English / Japanese         (from the next start; needs jpvoice\)
//   Text Language    English / Japanese         (from the next start)
//   Button Prompts   Auto / Xbox 360 / PlayStation 3
//   Confirm Button   A / B
//   Frame Rate       30 / 60 (gameplay) / 60 (all)
//   Resolution       Auto / 720p / 1440p / 2160p (from the next start)
//   Anti-Aliasing    Off / FXAA / FXAA Extreme
// A change that applies at the next start asks whether to restart now (Yes/No).
//
// How the menu works (static analysis; Lightning Returns' equivalents in lr_config_menu.inc):
//   MainConfigMenu   +0x8C ConfigList, +0x90 value popup, +0x98 Yes/No dialog. Input callback
//                    sub_82627510: A -> sub_82626ED0, B -> sub_82624A50(menu), X -> restore
//                    defaults question sub_82625F38; step sub_82628098 (popup closed:
//                    sub_825BE8A8(id, value) store + sub_825BE710(id) apply; dialog answered
//                    Yes (+0x8C == 0): sub_82627A30 = restore defaults).
//   ConfigList       item list at +0x100 (built by sub_82625AD0 from the 15 ids at
//                    0x820BF8E0): +0x00 u32 id[17], +0x44 u32 value[17], +0x88 count.
//                    +0x1A4 busy (scrolling), +0x1B0 first shown index, +0x1B4 its target,
//                    +0x1B8 cursor, +0x1BC 15 row widgets. (The title update grew the list by
//                    two items: before it, 15 items, values +0x3C, count +0x78, and every field
//                    after it 0x10 lower, 14 rows.)
//   row fill         sub_82627268(items, row, index): label "$f_con_%02d" % base[id], value
//                    "$f_con_%02d" % (base[id] + 1 + value) (sub_82623F00; base table
//                    0x820BF398); sub_82653818(row, text) label, sub_82624890(row, text) value,
//                    sub_82626C98(row, enabled); row->vtable[0x18](row, hidden).
//   value popup      ctor sub_82625250(popup, id, value), 0x100 bytes: +0x80 id, +0xE0
//                    hovered, +0xE4 value count (4 for id 0, else 2), +0xF4 id again. Value
//                    texts numbered base[id] + 1 + value, for id 0 from 0x820BF3DC (2, 1, 4, 3).
//                    The rows are laid out for the count (sub_82624F78(count, popup + 0x90)).
//   help bar         sub_82626008(menu) and sub_826243B0(popup), through sub_825A4608(gui, key,
//                    0, 0); gui = [0x82E86E8C].
//   text             every '$' key is looked up by sub_8259FBF8(gui, key): a heap copy
//                    (sub_825B56E0) of the text, or 0.
// LR_CONFIG_MENU_ITEMS=0 leaves the menu as the game built it.

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

#include <rex/hook.h>
#include <rex/logging/macros.h>
#include <rex/ppc/func.h>
#include <rex/runtime.h>

// Backend (x2_settings.cpp, x2_hooks.cpp).
int X2ConfigGetVoice();
void X2ConfigSetVoice(int jp);
int X2ConfigVoiceAvailable();
int X2VoiceAtStart();
int X2ConfigGetText();
void X2ConfigSetText(int jp);
int X2TextAtStart();
int X2ConfigGetUiMode();
void X2ConfigSetUiMode(int mode);
int X2ConfigGetConfirm();
void X2ConfigSetConfirm(int b);
int X2ConfigGetFps();
void X2ConfigSetFps(int choice);
bool X2FpsNeedsRestart();
int X2ConfigGetAa();
void X2ConfigSetAa(int choice);
int X2ResolutionChoice();
bool X2SetResolution(int choice);
int X2ResolutionScaleFor(int choice);
int X2ResolutionActiveScale();
void X2RestartNow();
void X2FilterXboxCopy(uint8_t* base, uint32_t text, const char* key);

namespace {

constexpr uint32_t kGuiManager = 0x82E86E8C;     // [kGuiManager] = GUI manager
constexpr uint32_t kBaseTextTable = 0x820BF398;  // u32 per game id: first "$f_con_NN" number
constexpr uint32_t kId0ValueTexts = 0x820BF3DC;  // id 0's value text numbers, one per value

// MainConfigMenu / ConfigList / item list / popup fields.
constexpr uint32_t kMenuList = 0x8C, kMenuPopup = 0x90, kMenuDialog = 0x98;
constexpr uint32_t kListItems = 0x100, kListBusy = 0x1A4, kListFirst = 0x1B0, kListFirstTarget = 0x1B4,
                   kListCursor = 0x1B8, kListRows = 0x1BC, kVisibleRows = 15,
                   kListPointerRow = 0x204;  // the pointer's row on screen (cursor - first shown)
constexpr uint32_t kItemIds = 0x00, kItemValues = 0x44, kItemCount = 0x88, kItemCapacity = 17;
constexpr uint32_t kPopupId = 0x80, kPopupHovered = 0xE0, kPopupCount = 0xE4, kPopupId2 = 0xF4;

constexpr uint32_t kSoundConfirm = 4, kSoundCancel = 3, kSoundBuzzer = 0x48, kSoundDialog = 0x49;

// Game ids are 0..16 (the switches in sub_825BE8A8 / sub_825BE448 / sub_825BE160 cover them);
// ours take the default case wherever the hooks below do not catch them.
constexpr uint32_t kIdPort = 0x6F;  // "Port Settings" on the game's page
constexpr uint32_t kIdVoice = 0x60, kIdText = 0x61, kIdUi = 0x62, kIdConfirm = 0x63, kIdFps = 0x64,
                   kIdResolution = 0x65, kIdAa = 0x66;

struct X2Item {
  uint32_t id;
  const char* label;  // keys, at most 15 characters (the game's key buffers are 16)
  const char* help;
  const char* help_unavailable;
  uint32_t count;  // 2; 3 or 4 (built as the game's id 0)
  const char* value[4];
  const char* value_help[4];
};

constexpr X2Item kItems[] = {
    {kIdVoice, "$x2_voice", "$x2_voiceh", "$x2_voiceu", 2, {"$x2_en", "$x2_jp"}, {"$x2_voice_0h", "$x2_voice_1h"}},
    {kIdText, "$x2_text", "$x2_texth", nullptr, 2, {"$x2_en", "$x2_jp"}, {"$x2_text_0h", "$x2_text_1h"}},
    {kIdUi, "$x2_ui", "$x2_uih", nullptr, 3, {"$x2_ui_a", "$x2_ui_0", "$x2_ui_1"}, {"$x2_ui_ah", "$x2_ui_0h", "$x2_ui_1h"}},
    {kIdConfirm, "$x2_cfm", "$x2_cfmh", nullptr, 2, {"$x2_cfm_0", "$x2_cfm_1"}, {"$x2_cfm_0h", "$x2_cfm_1h"}},
#if defined(__ANDROID__)
    {kIdFps, "$x2_fps", "$x2_fpsh", nullptr, 3, {"$x2_fps_0", "$x2_fps_1", "$x2_fps_2"},
     {"$x2_fps_0h", "$x2_fps_1h", "$x2_fps_2h"}},
#else
    {kIdFps, "$x2_fps", "$x2_fpsh", nullptr, 4, {"$x2_fps_0", "$x2_fps_1", "$x2_fps_2", "$x2_fps_3"},
     {"$x2_fps_0h", "$x2_fps_1h", "$x2_fps_2h", "$x2_fps_3h"}},
#endif
#if defined(__ANDROID__)
    // Android: 720p (the default) or 1440p; the menu's values 0 / 1 are the choices 720 / 1440.
    {kIdResolution, "$x2_res", "$x2_resh", nullptr, 2, {"$x2_res_1", "$x2_res_2"}, {"$x2_res_1h", "$x2_res_2h"}},
#else
    {kIdResolution, "$x2_res", "$x2_resh", nullptr, 4, {"$x2_res_0", "$x2_res_1", "$x2_res_2", "$x2_res_3"},
     {"$x2_res_0h", "$x2_res_1h", "$x2_res_2h", "$x2_res_3h"}},
#endif
    {kIdAa, "$x2_aa", "$x2_aah", nullptr, 3, {"$x2_aa_0", "$x2_aa_1", "$x2_aa_2"}, {"$x2_aa_0h", "$x2_aa_1h", "$x2_aa_2h"}},
};
constexpr uint32_t kItemsCount = sizeof(kItems) / sizeof(kItems[0]);

// The entry on the game's page.
constexpr X2Item kPortItem = {kIdPort, "$x2_port", "$x2_porth", nullptr, 1, {"$x2_none"}, {"$x2_porth"}};

// Texts in the game's own text encoding: plain ASCII, "@r" = line break.
struct X2Text {
  const char* key;
  const char* text;
};
constexpr X2Text kTexts[] = {
    {"$x2_port", "Port Settings"},
    {"$x2_porth", "Language, button prompts, frame rate, resolution, and other settings of this port."},
    {"$x2_none", ""},
    {"$x2_en", "English"},
    {"$x2_jp", "Japanese"},
    {"$x2_voice", "Voice Language"},
    {"$x2_voiceh", "Choose the language of the spoken dialogue. A new choice asks to restart the game."},
    {"$x2_voiceu", "The Japanese voices are not installed (the builder converts them from the Steam version)."},
    {"$x2_voice_0h", "Characters speak in English."},
    {"$x2_voice_1h", "Characters speak in Japanese, with the Japanese lip-sync."},
    {"$x2_text", "Text Language"},
    {"$x2_texth", "Choose the language of the on-screen text. A new choice asks to restart the game."},
    {"$x2_text_0h", "On-screen text in English."},
    {"$x2_text_1h", "On-screen text in Japanese."},
    {"$x2_ui", "Button Prompts"},
    {"$x2_uih", "Choose which controller's buttons are shown on screen."},
    {"$x2_ui_a", "Auto (Controller)"},
    {"$x2_ui_ah", "Show the buttons of the controller in use: Xbox or PlayStation."},
    {"$x2_ui_0", "Xbox 360"},
    {"$x2_ui_0h", "Show Xbox 360 controller buttons."},
    {"$x2_ui_1", "PlayStation 3"},
    {"$x2_ui_1h", "Show PlayStation 3 controller buttons."},
    {"$x2_cfm", "Confirm Button"},
    {"$x2_cfmh", "Choose which button confirms and which cancels."},
    {"$x2_cfm_0", "A / Cross"},
    {"$x2_cfm_0h", "A (Cross) confirms and B (Circle) cancels, as on the Xbox 360."},
    {"$x2_cfm_1", "B / Circle"},
    {"$x2_cfm_1h", "B (Circle) confirms and A (Cross) cancels, as in the Japanese release."},
    {"$x2_fps", "Frame Rate"},
    {"$x2_fpsh", "Choose the frame rate of the field, battles, and scenes."},
    {"$x2_fps_0", "30 fps"},
    {"$x2_fps_0h", "30 frames per second, as on the Xbox 360."},
    {"$x2_fps_1", "60 fps (gameplay)"},
    {"$x2_fps_1h", "60 fps in the field, battles, and menus; cutscenes stay at 30 fps."},
    {"$x2_fps_2", "60 fps (all)"},
    {"$x2_fps_2h", "60 frames per second everywhere, cutscenes included. Game speed stays the same."},
    {"$x2_fps_3", "120 fps"},
    {"$x2_fps_3h", "120 fps in the field, battles, and menus; cutscenes at 60 fps. Needs a 120 Hz display. Asks to restart the game."},
    {"$x2_res", "Resolution"},
    {"$x2_res_0", "Auto (Display)"},
    {"$x2_res_1", "720p"},
    {"$x2_res_2", "1440p"},
    {"$x2_res_3", "2160p (4K)"},
    {"$x2_aa", "Anti-Aliasing"},
    {"$x2_aah", "Smooth jagged edges over the whole picture."},
    {"$x2_aa_0", "Off"},
    {"$x2_aa_0h", "No extra smoothing (the game's own anti-aliasing only)."},
    {"$x2_aa_1", "FXAA"},
    {"$x2_aa_1h", "FXAA: smoother edges, slightly softer text."},
    {"$x2_aa_2", "FXAA Extreme"},
    {"$x2_aa_2h", "Stronger FXAA: smoothest edges, softer picture."},
    // The Save screen's delete question (x2_save_delete.cpp).
    {"$x2_delq", "Delete this save data?@rDeleted save data cannot be recovered."},
    // "$x2_resh", "$x2_res_Nh", and "$x2_rstq" (the restart question) are built in x2_dynamic_text.
};

uint32_t ld32(uint8_t* base, uint32_t a) {
  uint32_t v;
  std::memcpy(&v, base + a, 4);
  return __builtin_bswap32(v);
}

void st32(uint8_t* base, uint32_t a, uint32_t v) {
  v = __builtin_bswap32(v);
  std::memcpy(base + a, &v, 4);
}

bool x2_items_enabled() {
  static const bool enabled = [] {
    const char* v = std::getenv("LR_CONFIG_MENU_ITEMS");
    return !(v && v[0] == '0');
  }();
  return enabled;
}

const X2Item* x2_item(uint32_t id) {
  if (id == kIdPort) return &kPortItem;
  for (const X2Item& item : kItems)
    if (item.id == id) return &item;
  return nullptr;
}

// Restart question: what changed (bit 0 voices, bit 1 text, bit 2 resolution, bit 3 120 fps), set when a
// choice is stored; the menu whose Yes/No dialog is ours while it is open.
std::atomic<int> g_restart_ask{0};
std::atomic<int> g_restart_what{0};
std::atomic<uint32_t> g_restart_menu{0};

const char* x2_dynamic_text(const char* key) {
  static std::string text;
  auto size = [](int scale) {
    return scale ? std::to_string(1280 * scale) + "x" + std::to_string(720 * scale) : std::string("the config file's size");
  };
  if (std::strcmp(key, "$x2_resh") == 0) {
    text = "Render size, now " + size(X2ResolutionActiveScale()) + ". A new choice asks to restart the game.";
    return text.c_str();
  }
  if (std::strcmp(key, "$x2_rstq") == 0) {
    const int what = g_restart_what.load();
    std::string parts;
    auto add = [&](const std::string& p) { parts += (parts.empty() ? "" : ", ") + p; };
    if (what & 1) add(std::string(X2ConfigGetVoice() ? "Japanese" : "English") + " voices");
    if (what & 2) add(std::string(X2ConfigGetText() ? "Japanese" : "English") + " text");
    if (what & 4) add("rendering at " + size(X2ResolutionScaleFor(X2ResolutionChoice())));
    if (what & 8) add("120 fps");
    text = "Restart the game now for " + parts + "?@rUnsaved progress is lost. Otherwise the change applies at the next start.";
    return text.c_str();
  }
  if (std::strncmp(key, "$x2_res_", 8) == 0 && key[8] >= '0' && key[8] <= '3' && key[9] == 'h' && !key[10]) {
    switch (key[8] - '0') {
      case 0: text = "Match the display: " + size(X2ResolutionScaleFor(0)) + " on this PC."; break;
#if defined(__ANDROID__)
      case 1: text = "1280x720, as on the Xbox 360. Recommended on phones."; break;
      case 2: text = "2560x1440: sharper, but heavy for a phone (slow frames and late textures)."; break;
#else
      case 1: text = "1280x720, as on the Xbox 360."; break;
      case 2: text = "2560x1440: sharp on 1080p and 1440p displays."; break;
#endif
      default: text = "3840x2160 for 4K displays; needs a fast graphics card."; break;
    }
    return text.c_str();
  }
  return nullptr;
}

const char* x2_text(const char* key) {
  for (const X2Text& t : kTexts)
    if (std::strcmp(t.key, key) == 0) return t.text;
  return x2_dynamic_text(key);
}

bool x2_item_selectable(uint32_t id) { return id != kIdVoice || X2ConfigVoiceAvailable() != 0; }

int x2_item_value(uint32_t id) {
  switch (id) {
    case kIdVoice: return X2ConfigGetVoice() && x2_item_selectable(id) ? 1 : 0;
    case kIdText: return X2ConfigGetText();
    case kIdUi: return X2ConfigGetUiMode();
    case kIdConfirm: return X2ConfigGetConfirm();
    case kIdFps: return X2ConfigGetFps();
#if defined(__ANDROID__)
    case kIdResolution: return X2ResolutionChoice() == 2 ? 1 : 0;  // 1440p, else 720p
#else
    case kIdResolution: return X2ResolutionChoice();
#endif
    case kIdAa: return X2ConfigGetAa();
    default: return 0;
  }
}

uint32_t x2_value_index(const X2Item& item, uint32_t value) { return value < item.count ? value : item.count - 1; }

// Scratch space on the guest stack for the duration of a hook: strings for the game's
// functions, then a frame for the callees. r1 and lr are restored on scope exit.
class GuestFrame {
 public:
  GuestFrame(PPCContext& ctx, uint8_t* base) : ctx_(ctx), base_(base), saved_r1_(ctx.r1.u32), saved_lr_(ctx.lr) {}
  ~GuestFrame() {
    ctx_.r1.u32 = saved_r1_;
    ctx_.lr = saved_lr_;
  }
  GuestFrame(const GuestFrame&) = delete;
  GuestFrame& operator=(const GuestFrame&) = delete;

  uint32_t PushString(const char* s) {
    uint32_t n = static_cast<uint32_t>(std::strlen(s)) + 1;
    ctx_.r1.u32 = (ctx_.r1.u32 - n) & ~15u;
    std::memcpy(base_ + ctx_.r1.u32, s, n);
    return ctx_.r1.u32;
  }
  uint32_t PushBytes(uint32_t n) {
    ctx_.r1.u32 = (ctx_.r1.u32 - n) & ~15u;
    std::memset(base_ + ctx_.r1.u32, 0, n);
    return ctx_.r1.u32;
  }
  // Call after the last Push, before the first call into the game.
  void Open() {
    uint32_t sp = (ctx_.r1.u32 - 0x100) & ~15u;
    st32(base_, sp, saved_r1_);  // back chain, as stwu would leave it
    ctx_.r1.u32 = sp;
  }

 private:
  PPCContext& ctx_;
  uint8_t* base_;
  uint32_t saved_r1_;
  uint64_t saved_lr_;
};

// Value popup being built on this thread: our id, how many values, the game id it is built as.
thread_local uint32_t t_popup_id = 0;
thread_local uint32_t t_popup_count = 0;
thread_local uint32_t t_popup_build_id = 0;
thread_local uint32_t t_popup = 0;

std::atomic<bool> g_logged{false};

// The port's page: the game's list as it was, to come back to.
struct X2Page {
  uint32_t menu = 0;
  uint32_t ids[kItemCapacity] = {}, values[kItemCapacity] = {}, count = 0;
  uint32_t cursor = 0, first = 0, first_target = 0, pointer_row = 0;
};
X2Page g_page;

}  // namespace

REX_EXTERN(sub_825B56E0);  // game heap allocate (size)
REX_EXTERN(sub_825A4608);  // help bar: (gui, key, 0, 0)
REX_EXTERN(sub_82653818);  // row label: (row, text)
REX_EXTERN(sub_82624890);  // row value: (row, text)
REX_EXTERN(sub_82626C98);  // row enabled: (row, 1 = normal, 0 = greyed)
REX_EXTERN(sub_825A9DB8);  // play menu sound: (gui, id)
REX_EXTERN(sub_82627268);  // row fill: (items, row, index); hooked below
REX_EXTERN(sub_82626008);  // help bar for the cursor's item: (menu); hooked below
REX_EXTERN(sub_82624B68);  // open the menu's Yes/No dialog: (menu, "question|choices")
REX_EXTERN(sub_825B95F8);  // LocalizedText ctor (text)
REX_EXTERN(sub_825B9848);  // LocalizedText::Set (text, key, 0)
REX_EXTERN(sub_825B9610);  // LocalizedText dtor (text)
REX_EXTERN(sub_825A7EE0);  // move the pointer to the focused menu's cursor: (gui)

namespace {

void x2_show_help(PPCContext& ctx, uint8_t* base, const char* key) {
  GuestFrame frame(ctx, base);
  uint32_t k = frame.PushString(key);
  frame.Open();
  ctx.r3.u64 = ld32(base, kGuiManager);
  ctx.r4.u64 = k;
  ctx.r5.u64 = 0;
  ctx.r6.u64 = 0;
  sub_825A4608(ctx, base);
}

void x2_sound(PPCContext& ctx, uint8_t* base, uint32_t id) {
  GuestFrame frame(ctx, base);
  frame.Open();
  ctx.r3.u64 = ld32(base, kGuiManager);
  ctx.r4.u64 = id;
  sub_825A9DB8(ctx, base);
}

// row->vtable[0x18](row, hide): 0 shows the row.
void x2_show_row(PPCContext& ctx, uint8_t* base, uint32_t row) {
  uint32_t target = ld32(base, ld32(base, row) + 0x18);
  if (PPCFunc* fn = rex::runtime::ResolveIndirectFunction(target)) {
    ctx.r3.u64 = row;
    ctx.r4.u64 = 0;
    fn(ctx, base);
  }
}

// Every visible row filled again (as sub_82627A30 does after restoring the defaults).
void x2_refresh_rows(PPCContext& ctx, uint8_t* base, uint32_t list) {
  GuestFrame frame(ctx, base);
  frame.Open();
  const uint32_t first = ld32(base, list + kListFirst);
  for (uint32_t k = 0; k < kVisibleRows; ++k) {
    uint32_t row = ld32(base, list + kListRows + 4 * k);
    if (!row) continue;
    ctx.r3.u64 = list + kListItems;
    ctx.r4.u64 = row;
    ctx.r5.u64 = first + k;
    sub_82627268(ctx, base);
  }
}

// The pointer (hand) to the cursor's row: the game does this after each cursor move
// (sub_82625E68 and the others call sub_825A7EE0); setting the cursor directly does not move it.
void x2_move_pointer(PPCContext& ctx, uint8_t* base) {
  GuestFrame frame(ctx, base);
  frame.Open();
  ctx.r3.u64 = ld32(base, kGuiManager);
  sub_825A7EE0(ctx, base);
}

void x2_refresh_help(PPCContext& ctx, uint8_t* base, uint32_t menu) {
  GuestFrame frame(ctx, base);
  frame.Open();
  ctx.r3.u64 = menu;
  sub_82626008(ctx, base);
}

bool x2_menu_idle(uint8_t* base, uint32_t menu) {
  uint32_t list = ld32(base, menu + kMenuList);
  return list && !ld32(base, menu + kMenuPopup) && !ld32(base, menu + kMenuDialog) && !ld32(base, list + kListBusy);
}

void x2_page_enter(PPCContext& ctx, uint8_t* base, uint32_t menu) {
  const uint32_t list = ld32(base, menu + kMenuList), items = list + kListItems;
  g_page.count = std::min<uint32_t>(ld32(base, items + kItemCount), kItemCapacity);
  for (uint32_t i = 0; i < g_page.count; ++i) {
    g_page.ids[i] = ld32(base, items + kItemIds + 4 * i);
    g_page.values[i] = ld32(base, items + kItemValues + 4 * i);
  }
  g_page.cursor = ld32(base, list + kListCursor);
  g_page.first = ld32(base, list + kListFirst);
  g_page.first_target = ld32(base, list + kListFirstTarget);
  g_page.pointer_row = ld32(base, list + kListPointerRow);
  for (uint32_t i = 0; i < kItemsCount; ++i) {
    st32(base, items + kItemIds + 4 * i, kItems[i].id);
    st32(base, items + kItemValues + 4 * i, x2_item_value(kItems[i].id));
  }
  st32(base, items + kItemCount, kItemsCount);
  st32(base, list + kListCursor, 0);
  st32(base, list + kListFirst, 0);
  st32(base, list + kListFirstTarget, 0);
  st32(base, list + kListPointerRow, 0);  // the pointer on the first row (it stayed where it was)
  g_page.menu = menu;
  x2_refresh_rows(ctx, base, list);
  x2_refresh_help(ctx, base, menu);
  x2_move_pointer(ctx, base);
  x2_sound(ctx, base, kSoundConfirm);
  REXLOG_INFO("XIII-2 config menu: Port Settings opened");
}

void x2_page_leave(PPCContext& ctx, uint8_t* base) {
  const uint32_t menu = g_page.menu, list = ld32(base, menu + kMenuList), items = list + kListItems;
  for (uint32_t i = 0; i < g_page.count; ++i) {
    st32(base, items + kItemIds + 4 * i, g_page.ids[i]);
    st32(base, items + kItemValues + 4 * i, g_page.values[i]);
  }
  st32(base, items + kItemCount, g_page.count);
  st32(base, list + kListCursor, g_page.cursor);
  st32(base, list + kListFirst, g_page.first);
  st32(base, list + kListFirstTarget, g_page.first_target);
  st32(base, list + kListPointerRow, g_page.pointer_row);
  g_page.menu = 0;
  x2_refresh_rows(ctx, base, list);
  x2_refresh_help(ctx, base, menu);
  x2_move_pointer(ctx, base);
  x2_sound(ctx, base, kSoundCancel);
  REXLOG_INFO("XIII-2 config menu: back to the game's Settings");
}

}  // namespace

// ---------------------------------------------------------------------------------------
// Text: sub_8259FBF8(gui, key) is asked first for every '$' key; answer for ours with a heap
// copy, as the text resource does (double NUL, the game's text terminator).
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_8259FBF8);
REX_HOOK_RAW(sub_8259FBF8) {
  const uint32_t key_address = ctx.r4.u32;
  if (key_address) {
    const char* key = reinterpret_cast<const char*>(base + key_address);
    if (key[0] == '$' && key[1] == 'x' && key[2] == '2' && key[3] == '_') {
      if (const char* text = x2_text(key)) {
        const uint32_t len = static_cast<uint32_t>(std::strlen(text));
        const auto lr = ctx.lr;
        ctx.r3.u64 = len + 2;
        sub_825B56E0(ctx, base);
        ctx.lr = lr;
        const uint32_t copy = ctx.r3.u32;
        if (copy) {
          std::memcpy(base + copy, text, len);
          base[copy + len] = 0;
          base[copy + len + 1] = 0;
        }
        ctx.r3.u64 = copy;
        return;
      }
    }
  }
  char key[48] = {};
  for (size_t i = 0; key_address && i + 1 < sizeof(key) && base[key_address + i]; ++i) key[i] = char(base[key_address + i]);
  __imp__sub_8259FBF8(ctx, base);
  if (key[0] == '$') X2FilterXboxCopy(base, ctx.r3.u32, key);  // x2_xbox_texts.cpp
}

// ---------------------------------------------------------------------------------------
// Item list: "Port Settings" after the game's items.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82625AD0);
REX_HOOK_RAW(sub_82625AD0) {
  const uint32_t items = ctx.r3.u32;
  __imp__sub_82625AD0(ctx, base);
  g_page.menu = 0;  // a new Settings menu: a page left open in an earlier one is forgotten
  if (!x2_items_enabled()) return;
  const uint32_t count = ld32(base, items + kItemCount);
  if (count >= kItemCapacity) {
    REXLOG_WARN("XIII-2 config menu: {} items, no room for Port Settings", count);
    return;
  }
  st32(base, items + kItemIds + 4 * count, kIdPort);
  st32(base, items + kItemValues + 4 * count, 0);
  st32(base, items + kItemCount, count + 1);
  ctx.r3.u64 = items;
  if (!g_logged.exchange(true)) REXLOG_INFO("XIII-2 config menu: Port Settings added after the game's {} items", count);
}

// ---------------------------------------------------------------------------------------
// Row fill (every row refresh: open, scroll, value change, restore defaults).
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82627268);
REX_HOOK_RAW(sub_82627268) {
  const uint32_t items = ctx.r3.u32, row = ctx.r4.u32, index = ctx.r5.u32;
  const X2Item* item = nullptr;
  if (index < ld32(base, items + kItemCount)) item = x2_item(ld32(base, items + kItemIds + 4 * index));
  if (!item) {
    __imp__sub_82627268(ctx, base);
    return;
  }
  const uint32_t value = x2_value_index(*item, ld32(base, items + kItemValues + 4 * index));
  GuestFrame frame(ctx, base);
  const uint32_t label = frame.PushString(item->label);
  const uint32_t text = frame.PushString(item->value[value]);
  frame.Open();
  x2_show_row(ctx, base, row);
  ctx.r3.u64 = row;
  ctx.r4.u64 = label;
  sub_82653818(ctx, base);
  ctx.r3.u64 = row;
  ctx.r4.u64 = text;
  sub_82624890(ctx, base);
  ctx.r3.u64 = row;
  ctx.r4.u64 = x2_item_selectable(item->id) ? 1 : 0;
  sub_82626C98(ctx, base);
}

// ---------------------------------------------------------------------------------------
// Help bar for the item under the cursor.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82626008);
REX_HOOK_RAW(sub_82626008) {
  const uint32_t list = ld32(base, ctx.r3.u32 + kMenuList);
  if (list) {
    const uint32_t cursor = ld32(base, list + kListCursor);
    if (cursor < ld32(base, list + kListItems + kItemCount)) {
      if (const X2Item* item = x2_item(ld32(base, list + kListItems + kItemIds + 4 * cursor))) {
        const char* key = item->help;
        if (!x2_item_selectable(item->id) && item->help_unavailable) key = item->help_unavailable;
        x2_show_help(ctx, base, key);
        return;
      }
    }
  }
  __imp__sub_82626008(ctx, base);
}

// ---------------------------------------------------------------------------------------
// A: "Port Settings" opens the page; a greyed item gets the game's refusal (buzzer).
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82626ED0);
REX_HOOK_RAW(sub_82626ED0) {
  const uint32_t menu = ctx.r3.u32;
  if (x2_menu_idle(base, menu)) {
    const uint32_t list = ld32(base, menu + kMenuList);
    const uint32_t cursor = ld32(base, list + kListCursor);
    if (cursor < ld32(base, list + kListItems + kItemCount)) {
      const uint32_t id = ld32(base, list + kListItems + kItemIds + 4 * cursor);
      if (id == kIdPort) {
        PPCContext c = ctx;
        x2_page_enter(c, base, menu);
        return;
      }
      if (x2_item(id) && !x2_item_selectable(id)) {
        PPCContext c = ctx;
        x2_sound(c, base, kSoundBuzzer);
        return;
      }
    }
  }
  __imp__sub_82626ED0(ctx, base);
}

// B: on the port's page, back to the game's page instead of closing the menu.
REX_EXTERN(__imp__sub_82624A50);
REX_HOOK_RAW(sub_82624A50) {
  if (g_page.menu && g_page.menu == ctx.r3.u32) {
    if (x2_menu_idle(base, ctx.r3.u32)) {
      PPCContext c = ctx;
      x2_page_leave(c, base);
    }
    return;
  }
  __imp__sub_82624A50(ctx, base);
}

// ---------------------------------------------------------------------------------------
// Value popup: the game builds it (2 values for any id but 0, 4 for id 0); only the value
// texts need our keys. sub_82623F00(buf, size, number) is asked for "$f_con_%02d" with
// number = base[id] + 1 + value (id 0: kId0ValueTexts[value]) while the popup is built. A 3- or
// 4-value item is built as id 0 and gets its own id back before anything reads it; a 3-value
// one has its row count cut to 3 where the rows are laid out.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82625250);
REX_HOOK_RAW(sub_82625250) {
  const X2Item* item = x2_item(ctx.r4.u32);
  if (!item || item->id == kIdPort) {
    __imp__sub_82625250(ctx, base);
    return;
  }
  const uint32_t popup = ctx.r3.u32;
  const uint32_t saved_id = t_popup_id, saved_count = t_popup_count, saved_build = t_popup_build_id,
                 saved_popup = t_popup;
  t_popup_id = item->id;
  t_popup_count = item->count;
  t_popup_build_id = item->count > 2 ? 0 : item->id;
  t_popup = popup;
  ctx.r4.u64 = t_popup_build_id;
  __imp__sub_82625250(ctx, base);
  if (t_popup_build_id != item->id) {
    st32(base, popup + kPopupId, item->id);
    st32(base, popup + kPopupId2, item->id);
  }
  t_popup_id = saved_id;
  t_popup_count = saved_count;
  t_popup_build_id = saved_build;
  t_popup = saved_popup;
}

// Row layout of the popup being built: (count, popup + 0x90). For a 3-value item the count the
// constructor goes on with (its r31) and the popup's count become 3 as well.
REX_EXTERN(__imp__sub_82624F78);
REX_HOOK_RAW(sub_82624F78) {
  const bool three = t_popup_id && t_popup_count == 3 && ctx.r4.u32 == t_popup + 0x90;
  if (three) ctx.r3.u64 = 3;
  __imp__sub_82624F78(ctx, base);
  if (three) {
    ctx.r31.u64 = 3;
    st32(base, t_popup + kPopupCount, 3);
  }
}

REX_EXTERN(__imp__sub_82623F00);
REX_HOOK_RAW(sub_82623F00) {
  if (t_popup_id) {
    const X2Item* item = x2_item(t_popup_id);
    uint32_t value = ~0u;
    if (t_popup_build_id == 0) {
      for (uint32_t k = 0; k < 4; ++k)
        if (ld32(base, kId0ValueTexts + 4 * k) == ctx.r5.u32) value = k;
    } else {
      value = ctx.r5.u32 - (ld32(base, kBaseTextTable + 4 * t_popup_build_id) + 1);
    }
    const uint32_t buf = ctx.r3.u32, size = ctx.r4.u32;
    if (item && value < item->count && buf && size) {
      const char* key = item->value[value];
      uint32_t n = static_cast<uint32_t>(std::strlen(key));
      if (n >= size) n = size - 1;
      std::memcpy(base + buf, key, n);
      base[buf + n] = 0;
      ctx.r3.u64 = buf;
      return;
    }
  }
  __imp__sub_82623F00(ctx, base);
}

// Help bar while the popup is open, for the hovered value.
REX_EXTERN(__imp__sub_826243B0);
REX_HOOK_RAW(sub_826243B0) {
  const uint32_t popup = ctx.r3.u32;
  if (const X2Item* item = x2_item(ld32(base, popup + kPopupId))) {
    x2_show_help(ctx, base, item->value_help[x2_value_index(*item, ld32(base, popup + kPopupHovered))]);
    return;
  }
  __imp__sub_826243B0(ctx, base);
}

// ---------------------------------------------------------------------------------------
// Store / apply / default. The game calls the store also when the popup is cancelled (with
// the old value), so the backend is only told about real changes.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_825BE8A8);
REX_HOOK_RAW(sub_825BE8A8) {
  const uint32_t id = ctx.r3.u32, v = ctx.r4.u32;
  if (!x2_item(id)) {
    __imp__sub_825BE8A8(ctx, base);
    return;
  }
  const int value = int(v);
  switch (id) {
    case kIdVoice:
      if ((value ? 1 : 0) != X2ConfigGetVoice()) {
        X2ConfigSetVoice(value ? 1 : 0);
        if ((value ? 1 : 0) != X2VoiceAtStart()) g_restart_what.fetch_or(1);
        else g_restart_what.fetch_and(~1);
        g_restart_ask.store(g_restart_what.load() != 0);
      }
      break;
    case kIdText:
      if ((value ? 1 : 0) != X2ConfigGetText()) {
        X2ConfigSetText(value ? 1 : 0);
        if ((value ? 1 : 0) != X2TextAtStart()) g_restart_what.fetch_or(2);
        else g_restart_what.fetch_and(~2);
        g_restart_ask.store(g_restart_what.load() != 0);
      }
      break;
    case kIdUi:
      if (value < 3 && value != X2ConfigGetUiMode()) X2ConfigSetUiMode(value);
      break;
    case kIdConfirm:
      if ((value ? 1 : 0) != X2ConfigGetConfirm()) X2ConfigSetConfirm(value ? 1 : 0);
      break;
    case kIdFps:
      if (value < 4 && int(value) != X2ConfigGetFps()) {
        X2ConfigSetFps(value);
        // 120 needs this start's vertical blank at 120 Hz (from the next start); 30 and 60 switch now.
        if (X2FpsNeedsRestart()) g_restart_what.fetch_or(8);
        else g_restart_what.fetch_and(~8);
        g_restart_ask.store(g_restart_what.load() != 0);
      }
      break;
    case kIdAa:
      if (value < 3 && value != X2ConfigGetAa()) X2ConfigSetAa(value);
      break;
    case kIdResolution: {
#if defined(__ANDROID__)
      const int choice = value == 1 ? 2 : 1;  // the menu's 720p / 1440p
#else
      const int choice = value;
#endif
      if (choice < 4 && choice != X2ResolutionChoice()) {
        if (X2SetResolution(choice)) g_restart_what.fetch_or(4);
        else g_restart_what.fetch_and(~4);
        g_restart_ask.store(g_restart_what.load() != 0);
      }
      break;
    }
    default:
      break;
  }
}

REX_EXTERN(__imp__sub_825BE710);
REX_HOOK_RAW(sub_825BE710) {
  const uint32_t id = ctx.r3.u32;
  if (x2_item(id)) return;
  // Option 9 (menus and dialogs drawn larger; applied at every load and new game, and from the
  // menu): logged when it changes ([gui]+3572; x2_steam_saves.h, kLargeUiBit).
  const uint32_t gui = ld32(base, kGuiManager);
  const uint32_t before = gui ? ld32(base, gui + 3572) : 0;
  __imp__sub_825BE710(ctx, base);
  static std::atomic<int> logged{-1};
  const uint32_t now = gui ? ld32(base, gui + 3572) : 0;
  if (id == 9 && gui && (now != before || logged.load() < 0)) {
    logged.store(int(now != 0));
    REXLOG_INFO("XIII-2 settings: option 9 (larger menus and dialogs) {}", now ? "on" : "off");
  }
}

REX_EXTERN(__imp__sub_825BE448);
REX_HOOK_RAW(sub_825BE448) {
  const uint32_t id = ctx.r3.u32;
  if (!x2_item(id)) {
    __imp__sub_825BE448(ctx, base);
    return;
  }
  // Restore defaults: English, Auto prompts, A confirms, 30 fps, Auto resolution, no FXAA.
  ctx.r3.u64 = 0;
}

// ---------------------------------------------------------------------------------------
// Restart question: the menu's own Yes/No dialog (the one "Restore defaults" opens:
// sub_82624B68(menu, "question|choices"), kept at +0x98). The step (sub_82628098) closes it and
// calls sub_82627A30 for the first choice (Yes); for our dialog that restarts the game instead
// of restoring the defaults. No keeps the choice for the next start.
// ---------------------------------------------------------------------------------------
namespace {
// A key's text as the game resolves it (our "$x2_" keys come from the text hook above).
std::string x2_resolved_text(PPCContext& ctx, uint8_t* base, const char* key) {
  GuestFrame frame(ctx, base);
  const uint32_t k = frame.PushString(key);
  const uint32_t text = frame.PushBytes(64);
  frame.Open();
  ctx.r3.u64 = text;
  sub_825B95F8(ctx, base);
  ctx.r3.u64 = text;
  ctx.r4.u64 = k;
  ctx.r5.u64 = 0;
  sub_825B9848(ctx, base);
  std::string out;
  uint32_t chars = ld32(base, text);
  if (chars) {
    uint16_t offset_be;
    std::memcpy(&offset_be, base + text + 10, 2);
    chars += uint32_t(int32_t(int16_t(__builtin_bswap16(offset_be))));
    for (uint32_t i = 0; i < 480 && base[chars + i]; ++i) out += char(base[chars + i]);
  }
  ctx.r3.u64 = text;
  sub_825B9610(ctx, base);
  return out;
}

void x2_open_restart_dialog(PPCContext& ctx, uint8_t* base, uint32_t menu) {
  const std::string question = x2_resolved_text(ctx, base, "$x2_rstq");
  const std::string choices = x2_resolved_text(ctx, base, "$f_con_def_01");  // the game's Yes / No
  REXLOG_INFO("XIII-2 config menu: asking to restart ('{}' | '{}')", question, choices);
  const std::string all = question + "|" + choices;
  GuestFrame frame(ctx, base);
  const uint32_t text = frame.PushString(all.c_str());
  frame.Open();
  ctx.r3.u64 = menu;
  ctx.r4.u64 = text;
  sub_82624B68(ctx, base);
  ctx.r3.u64 = ld32(base, kGuiManager);
  ctx.r4.u64 = kSoundDialog;
  sub_825A9DB8(ctx, base);
}
}  // namespace

REX_EXTERN(__imp__sub_82628098);
REX_HOOK_RAW(sub_82628098) {
  const uint32_t menu = ctx.r3.u32;
  if (g_restart_ask.load() && x2_menu_idle(base, menu)) {
    g_restart_ask.store(0);
    PPCContext c = ctx;
    x2_open_restart_dialog(c, base, menu);
    if (ld32(base, menu + kMenuDialog)) g_restart_menu.store(menu);
    else REXLOG_WARN("XIII-2 config menu: the restart question did not open; the change applies at the next start");
  }
  __imp__sub_82628098(ctx, base);
  if (g_restart_menu.load() == menu && !ld32(base, menu + kMenuDialog)) {
    g_restart_menu.store(0);
    REXLOG_INFO("XIII-2 config menu: restart declined; the change applies at the next start");
  }
}

// Restore defaults - or Yes in our restart question.
REX_EXTERN(__imp__sub_82627A30);
REX_HOOK_RAW(sub_82627A30) {
  if (g_restart_menu.load() && g_restart_menu.load() == ctx.r3.u32) {
    g_restart_menu.store(0);
    X2RestartNow();
    return;  // could not restart: the change applies at the next start
  }
  __imp__sub_82627A30(ctx, base);
}
