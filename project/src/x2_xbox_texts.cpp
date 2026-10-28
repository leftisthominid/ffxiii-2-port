// ffxiii2 - Xbox LIVE, storage device, and hard drive messages, stripped (as the Lightning Returns
// port stripped Outerworld and the storage device texts, lr_hooks.cpp).
//
//   text lookup        sub_8259FAE0(table, "$key") -> entry, text at entry+4 (every menu text and
//                      message goes through it; sub_82597470(key) is the wrapper the message
//                      windows use: table [0x82E86E8C]). The game's texts end with two NULs.
//   script dialogs     Window.askOk sub_8281FFF0 (1 argument), askOkCancel sub_82820170 (1),
//                      askChoice sub_82820580 (2), askChoiceByList sub_828202B8 (2),
//                      askChoiceWithInitNo sub_828206E8 (3), askChoiceByListWithInitNo
//                      sub_82820420 (3) - Lightning Returns' natives (sub_8299A2F8 ...), same
//                      script VM: stack at vm+0x958 (8-byte slots: u16 type, u32 value at +4),
//                      vm+0x964 bit 0x80 = resumed wait.
// A script dialog whose text is only about Xbox LIVE, a storage device, or the hard drive
// (there is one fixed drive here and no network) is answered at once, the way the native
// returns when no window is needed: OK for a notice, the second choice ("No" / "Cancel") for a question. Labels
// that name the storage device ("Selected Storage Device: ...", "Select storage device") are
// emptied, except the Save screen's "Select storage device" hint, which says Delete (that
// button deletes saves, x2_save_delete.cpp). A message that says more loses only its Xbox
// sentences, so the game's own dialogs show the rest. Every such text is logged once with its
// key.
// X2_XBOX_TEXTS=1 keeps everything.

#include <atomic>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <rex/hook.h>
#include <rex/logging/macros.h>

namespace {
uint32_t ld32(uint8_t* base, uint32_t a) { return __builtin_bswap32(*reinterpret_cast<uint32_t*>(base + a)); }

bool keep_xbox_texts() {
  static const bool keep = [] {
    const char* v = std::getenv("X2_XBOX_TEXTS");
    return v && v[0] == '1';
  }();
  return keep;
}

bool guest_cstr(uint8_t* base, uint32_t p, char* out, size_t size) {
  if (!p || p < 0x10000) return false;
  size_t i = 0;
  for (; i + 1 < size; ++i) {
    char c = static_cast<char>(base[p + i]);
    out[i] = c;
    if (!c) return true;
  }
  out[i] = 0;
  return false;
}

// The game's text (up to its double NUL), lower-cased ASCII for matching; control codes and
// non-ASCII bytes become spaces.
std::string game_text_lower(uint8_t* base, uint32_t text, size_t max = 2048) {
  std::string out;
  if (!text) return out;
  for (size_t i = 0; i < max; ++i) {
    uint8_t c = base[text + i];
    if (!c) {
      if (!base[text + i + 1]) break;
      out.push_back(' ');
      continue;
    }
    out.push_back(c < 0x80 ? char(std::tolower(c)) : ' ');
  }
  return out;
}

enum class XboxText { kNone, kLabel, kMessage };

XboxText classify(const std::string& t) {
  const bool live = t.find("xbox live") != std::string::npos || t.find("xbox  live") != std::string::npos;
  const bool storage = t.find("storage device") != std::string::npos;
  const bool hdd = t.find("hard drive") != std::string::npos || t.find("hard disk") != std::string::npos;
  const bool profile = t.find("gamer profile") != std::string::npos || t.find("sign in") != std::string::npos ||
                       t.find("signed in") != std::string::npos || t.find("signed out") != std::string::npos;
  if (!live && !storage && !hdd && !profile) return XboxText::kNone;
  // Short texts that name the device (a label or a button hint), not a sentence.
  if ((storage || hdd) && !live && (t.size() < 48 || t.find(':') != std::string::npos) && t.find('?') == std::string::npos &&
      t.find(". ") == std::string::npos)
    return XboxText::kLabel;
  return XboxText::kMessage;
}

// The Save screen's button hint ("Select device", "Select Storage Device", "Change storage
// device"): that button deletes the highlighted save here (x2_save_delete.cpp), so the phrase
// becomes "Delete"; button icons and the rest of the text stay. t is the text lower-cased,
// byte for byte. X2_SAVE_DELETE=0 empties it as the other labels.
bool relabel_delete(uint8_t* base, uint32_t text, const std::string& t) {
  static const bool on = [] {
    const char* v = std::getenv("X2_SAVE_DELETE");
    return !(v && v[0] == '0');
  }();
  if (!on) return false;
  // "select/change [a] [storage] device(s)", not "selected".
  size_t start = std::string::npos, end = 0;
  for (size_t device = t.find("device"); device != std::string::npos && start == std::string::npos;
       device = t.find("device", device + 1)) {
    for (const char* verb : {"select", "change"}) {
      const size_t at = t.rfind(verb, device);
      if (at == std::string::npos || device - at > 20 || t.compare(at, 8, "selected") == 0) continue;
      const std::string between = t.substr(at + 6, device - at - 6);
      if (between != " " && between != " a " && between != " storage " && between != " a storage ") continue;
      start = at;
      end = device + 6;
      if (end < t.size() && t[end] == 's') ++end;
    }
  }
  if (start == std::string::npos) return false;
  size_t length = 0;
  while (length < 4094 && (base[text + length] || base[text + length + 1])) ++length;
  if (end > length) return false;
  static const char kWith[] = "Delete";
  const size_t with = sizeof(kWith) - 1;
  std::memcpy(base + text + start, kWith, with);
  std::memmove(base + text + start + with, base + text + end, length - end + 2);
  return true;
}

// A message with other sentences as well loses only the ones about Xbox LIVE, the storage
// device, the hard drive, or signing in (sentences end with ". ", "! ", "? ", or the game's
// line break "@r"); the rest stays as the game wrote it. t is the text lower-cased, byte for
// byte. A message with nothing else is left whole (false).
bool cut_xbox_sentences(uint8_t* base, uint32_t text, const std::string& t) {
  std::vector<std::pair<size_t, size_t>> sentences;
  size_t start = 0;
  for (size_t i = 0; i < t.size(); ++i) {
    size_t end = 0;
    if ((t[i] == '.' || t[i] == '!' || t[i] == '?') && (i + 1 == t.size() || t[i + 1] == ' ')) end = i + 1;
    else if (t[i] == '@' && i + 1 < t.size() && t[i + 1] == 'r') end = i + 2;
    if (!end) continue;
    while (end < t.size() && t[end] == ' ') ++end;
    sentences.push_back({start, end});
    start = end;
    i = end - 1;
  }
  if (start < t.size()) sentences.push_back({start, t.size()});
  std::string kept;
  bool cut = false;
  for (const auto& [from, to] : sentences) {
    if (classify(t.substr(from, to - from)) != XboxText::kNone) {
      cut = true;
      continue;
    }
    kept.append(reinterpret_cast<const char*>(base + text + from), to - from);
  }
  // Trailing spaces and line breaks left by a cut last sentence.
  while (!kept.empty()) {
    if (kept.back() == ' ') kept.pop_back();
    else if (kept.size() >= 2 && kept.compare(kept.size() - 2, 2, "@r") == 0) kept.resize(kept.size() - 2);
    else break;
  }
  if (!cut || kept.find_first_not_of(' ') == std::string::npos) return false;
  std::memcpy(base + text, kept.data(), kept.size());
  base[text + kept.size()] = 0;
  base[text + kept.size() + 1] = 0;
  return true;
}

std::mutex g_seen_mutex;
std::set<std::string> g_seen;

// Texts already looked at, by address (the text table stays where it is): lookups happen every
// frame for some menus.
std::mutex g_cache_mutex;
std::unordered_map<uint32_t, XboxText> g_cache;

void log_once(const char* key, const std::string& text, const char* what, uint32_t caller) {
  std::lock_guard<std::mutex> lock(g_seen_mutex);
  if (!g_seen.insert(key).second || g_seen.size() > 200) return;
  std::string shown = text.size() > 140 ? text.substr(0, 137) + "..." : text;
  REXLOG_INFO("XIII-2 Xbox texts: {} '{}' {} (looked up from {:08X})", key, shown, what, caller);
}
}  // namespace

REX_EXTERN(__imp__sub_8259FAE0);
REX_HOOK_RAW(sub_8259FAE0) {
  char key[48];
  const bool have_key = guest_cstr(base, ctx.r4.u32, key, sizeof(key)) && key[0] == '$';
  const uint32_t caller = uint32_t(ctx.lr);
  __imp__sub_8259FAE0(ctx, base);
  if (!have_key || keep_xbox_texts() || !ctx.r3.u32) return;
  const uint32_t text = ld32(base, ctx.r3.u32 + 4);
  if (!text) return;
  {
    std::lock_guard<std::mutex> lock(g_cache_mutex);
    auto it = g_cache.find(text);
    if (it != g_cache.end() && it->second != XboxText::kLabel) return;
  }
  const std::string t = game_text_lower(base, text);
  const XboxText kind = classify(t);
  {
    std::lock_guard<std::mutex> lock(g_cache_mutex);
    if (g_cache.size() < 100000) g_cache[text] = kind;
  }
  if (kind == XboxText::kNone) return;
  if (kind == XboxText::kLabel) {
    if (relabel_delete(base, text, t)) {
      log_once(key, t, "relabelled Delete (the button deletes the highlighted save)", caller);
      return;
    }
    log_once(key, t, "emptied (a storage device label)", caller);
    base[text] = 0;
    base[text + 1] = 0;
  } else if (cut_xbox_sentences(base, text, t)) {
    log_once(key, t, "its Xbox sentences cut", caller);
    std::lock_guard<std::mutex> lock(g_cache_mutex);
    g_cache[text] = XboxText::kNone;
  } else {
    log_once(key, t, "(an Xbox message; script dialogs with it are answered without showing)", caller);
  }
}

// The same for a text the game copied for one use (sub_8259FBF8, LocalizedText::Set: the
// Save screen's labels and hints come this way): the copy is edited. Called from
// x2_config_menu.cpp's hook of sub_8259FBF8.
void X2FilterXboxCopy(uint8_t* base, uint32_t text, const char* key) {
  if (!text || keep_xbox_texts()) return;
  const std::string t = game_text_lower(base, text);
  if (relabel_delete(base, text, t)) {
    log_once(key, t, "relabelled Delete (the button deletes the highlighted save)", 0);
    return;
  }
  const XboxText kind = classify(t);
  if (kind == XboxText::kLabel) {
    log_once(key, t, "emptied (a storage device label)", 0);
    base[text] = 0;
    base[text + 1] = 0;
  } else if (kind == XboxText::kMessage && cut_xbox_sentences(base, text, t)) {
    log_once(key, t, "its Xbox sentences cut", 0);
  }
}

namespace {
REX_EXTERN(sub_82597470);  // "$key" -> text (message windows)

// Is this guest string a "$key" whose text is an Xbox message?
bool is_xbox_message_key(PPCContext& ctx, uint8_t* base, uint32_t key_address, char* key, size_t key_size) {
  if (!guest_cstr(base, key_address, key, key_size) || key[0] != '$') return false;
  PPCContext saved = ctx;
  ctx.r3.u64 = key_address;
  sub_82597470(ctx, base);
  const uint32_t text = ctx.r3.u32;
  ctx = saved;
  return text && classify(game_text_lower(base, text)) == XboxText::kMessage;
}

// For a Window.ask* native with `args` arguments: if one of them is an Xbox message, answers
// without asking (arguments popped, the answer pushed as an int). Only a fresh call.
bool strip_xbox_ask(PPCContext& ctx, uint8_t* base, uint32_t args, uint32_t answer, const char* native) {
  if (keep_xbox_texts()) return false;
  const uint32_t vm = ctx.r3.u32;
  if (!vm || (ld32(base, vm + 0x964) & 0x80)) return false;
  uint32_t sp = ld32(base, vm + 0x958);
  char key[48];
  bool found = false;
  for (uint32_t i = 0; i < args && !found; ++i) found = is_xbox_message_key(ctx, base, ld32(base, sp + 8 * i + 4), key, sizeof(key));
  if (!found) return false;
  sp += 8 * args - 8;
  *reinterpret_cast<uint16_t*>(base + sp) = __builtin_bswap16(1);
  *reinterpret_cast<uint32_t*>(base + sp + 4) = __builtin_bswap32(answer);
  *reinterpret_cast<uint32_t*>(base + vm + 0x958) = __builtin_bswap32(sp);
  static std::atomic<int> logged{0};
  if (logged.fetch_add(1) < 32) REXLOG_INFO("XIII-2 Xbox texts: {} {} not shown (answered {})", native, key, answer);
  return true;
}
}  // namespace

REX_EXTERN(__imp__sub_8281FFF0);
REX_HOOK_RAW(sub_8281FFF0) {
  if (strip_xbox_ask(ctx, base, 1, 0, "askOk")) return;
  __imp__sub_8281FFF0(ctx, base);
}
REX_EXTERN(__imp__sub_82820170);
REX_HOOK_RAW(sub_82820170) {
  if (strip_xbox_ask(ctx, base, 1, 1, "askOkCancel")) return;
  __imp__sub_82820170(ctx, base);
}
REX_EXTERN(__imp__sub_82820580);
REX_HOOK_RAW(sub_82820580) {
  if (strip_xbox_ask(ctx, base, 2, 1, "askChoice")) return;
  __imp__sub_82820580(ctx, base);
}
REX_EXTERN(__imp__sub_828202B8);
REX_HOOK_RAW(sub_828202B8) {
  if (strip_xbox_ask(ctx, base, 2, 1, "askChoiceByList")) return;
  __imp__sub_828202B8(ctx, base);
}
REX_EXTERN(__imp__sub_828206E8);
REX_HOOK_RAW(sub_828206E8) {
  if (strip_xbox_ask(ctx, base, 3, 1, "askChoiceWithInitNo")) return;
  __imp__sub_828206E8(ctx, base);
}
REX_EXTERN(__imp__sub_82820420);
REX_HOOK_RAW(sub_82820420) {
  if (strip_xbox_ask(ctx, base, 3, 1, "askChoiceByListWithInitNo")) return;
  __imp__sub_82820420(ctx, base);
}
