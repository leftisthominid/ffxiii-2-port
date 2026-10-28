// ffxiii2 - imports the Steam (PC) version's saves (formats: x2_steam_saves.h).
//
// Once at start, after the game took its save key (sub_8225FC40 -> sub_82509360, which writes
// the hashed 64-bit value to [0x82E6A290]), every Steam save found - ff13_2-NN.dat in
// %LOCALAPPDATA%\SquareEnix\FinalFantasyXIII-2\save, or dropped into <settings>\steam_saves -
// is converted to the 360 format and added as a save slot. The game then lists and loads it like
// any other save and saves it in the 360 format from then on.
//   - Steam files are only read, never changed.
//   - A save is imported once (steam_saves_imported.txt lists them by content), into the slot
//     with its Steam number when that is free, otherwise into the first free slot. Existing
//     saves are never overwritten.
//   - The key: the one that opens the saves already there (the game's own, or imported from the
//     360); without any, the game's key of this start.
// PS3 saves (formats: x2_ps3_saves.h) the same way, once each: every save folder dropped into
// <settings>\ps3_saves (BLUS30776-02 ... as copied off a PS3 to USB, or from RPCS3's
// dev_hdd0\home\<user>\savedata), into the slot with its PS3 number (PARAM.SFO "Save Data N")
// when that is free (ps3_saves_imported.txt lists them).
// X2_STEAM_SAVES=0 imports nothing.

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <set>
#include <string>
#include <vector>

#include <rex/logging/macros.h>

#include "x2_ps3_saves.h"
#include "x2_steam_saves.h"

std::filesystem::path X2SettingsDir();

namespace {
namespace fs = std::filesystem;
using namespace x2_steam_saves;

constexpr uint32_t kKeySeed = 0x82E6A290;  // the hashed value, big-endian u64
constexpr int kMaxSlot = 49;               // the save manager holds 50 (0x32) saves

std::vector<uint8_t> read_file(const fs::path& path) {
  std::ifstream in(path, std::ios::binary);
  return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

uint64_t fnv64(const std::vector<uint8_t>& data) {
  uint64_t h = 0xCBF29CE484222325ull;
  for (uint8_t b : data) h = (h ^ b) * 0x100000001B3ull;
  return h;
}

// The 360 content header the runtime keeps next to a save (as X2_Import_360_Content.ps1 writes).
std::vector<uint8_t> content_header(const std::string& file_name, int slot) {
  std::vector<uint8_t> h(0x148, 0);
  h[3] = 1;  // device: HDD
  h[7] = 1;  // content type: saved game
  const std::string display = "Save Data " + std::string(slot + 1 < 10 ? "0" : "") + std::to_string(slot + 1);
  for (size_t i = 0; i < display.size() && i < 127; ++i) h[8 + 2 * i + 1] = uint8_t(display[i]);  // UTF-16BE
  std::memcpy(h.data() + 0x108, file_name.data(), std::min<size_t>(file_name.size(), 41));
  const uint64_t xuid = 0xB13EBABEBABEBABEull;
  for (int i = 0; i < 8; ++i) h[0x138 + i] = uint8_t(xuid >> (56 - 8 * i));
  st_be(h.data() + 0x140, 0x53510806);
  return h;
}

std::vector<fs::path> steam_save_files(const fs::path& settings) {
  std::vector<fs::path> dirs;
#if defined(_WIN32)
  if (const char* v = std::getenv("LOCALAPPDATA"); v && v[0]) dirs.push_back(fs::path(v) / "SquareEnix" / "FinalFantasyXIII-2" / "save");
#endif
  dirs.push_back(settings / "steam_saves");  // copied in by hand
  std::vector<fs::path> files;
  std::error_code ec;
  for (const auto& dir : dirs) {
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
      const std::string name = entry.path().filename().string();
      int slot = -1;
      if (entry.is_regular_file(ec) && name.size() == 13 && std::sscanf(name.c_str(), "ff13_2-%d.dat", &slot) == 1 && slot >= 0 &&
          slot <= 99)
        files.push_back(entry.path());
    }
  }
  std::sort(files.begin(), files.end());
  return files;
}

std::string header_text(const std::vector<uint8_t>& pc) {
  std::string s(reinterpret_cast<const char*>(pc.data()) + 0x10, strnlen(reinterpret_cast<const char*>(pc.data()) + 0x10, 0x100));
  for (char& c : s)
    if (c == '\n') c = ',';
  return s;
}

// A PS3 save's game settings option bits (for the log), or 0.
uint32_t ps3_options(const std::vector<uint8_t>& app, const std::vector<uint8_t>& key_dat, const std::vector<uint8_t>& pfd) {
  std::vector<uint8_t> body;
  bool ok = x2_ps3_saves::DecryptApp(app, key_dat, body);
  for (int id = 0; id < 2 && !ok && !pfd.empty(); ++id) {
    std::vector<uint8_t> key = key_dat;
    ok = x2_ps3_saves::PfdDecrypt(pfd, "KEY.DAT", x2_ps3_saves::kSecureFileIds[id], key) && x2_ps3_saves::DecryptApp(app, key, body);
  }
  const uint8_t* settings = ok ? GameSettings(body) : nullptr;
  return settings ? ld_be(settings) : 0;
}

// PS3 save folders in <settings>\ps3_saves (see the top).
template <typename PickSlot, typename WriteSlot, typename ReadList>
void import_ps3_saves(const fs::path& settings, uint64_t key, const XboxParts& parts, PickSlot pick_slot, WriteSlot write_slot,
                      ReadList read_list) {
  std::error_code ec;
  // Once: saves earlier builds imported kept the PS3 save's Settings option 9 (menus and dialogs
  // drawn a third larger): turned off in place (x2_steam_saves.h, kLargeUiBit).
  const fs::path fixed_marker = settings / "ps3_large_ui_off.txt";
  if (!fs::exists(fixed_marker, ec)) {
    std::ifstream list(settings / "ps3_saves_imported.txt");
    std::string line;
    while (std::getline(list, line)) {
      char slot[32] = {};
      if (std::sscanf(line.c_str(), "%*16s %31s", slot) != 1) continue;
      const fs::path app_path = settings / "B13EBABEBABEBABE" / "53510806" / "00000001" / slot / "APP.DAT";
      std::vector<uint8_t> app = read_file(app_path);
      uint32_t before = 0;
      if (app.empty() || !x2_ps3_saves::LargeUiOff(app, key, before)) {
        REXLOG_INFO("XIII-2 PS3 saves: {}: Settings option 9 already off (option bits {:08X})", slot, before);
        continue;
      }
      std::ofstream out(app_path, std::ios::binary);
      out.write(reinterpret_cast<const char*>(app.data()), std::streamsize(app.size()));
      REXLOG_INFO("XIII-2 PS3 saves: {}: Settings option 9 (larger menus and dialogs) turned off, option bits {:08X} -> {:08X}{}",
                  slot, before, before & ~kLargeUiBit, out.good() ? "" : " - could not write");
    }
    std::ofstream(fixed_marker) << "done\n";
  }
  std::vector<fs::path> folders;
  for (const auto& entry : fs::recursive_directory_iterator(settings / "ps3_saves", ec)) {
    if (entry.is_regular_file(ec) && entry.path().filename() == "APP.DAT") folders.push_back(entry.path().parent_path());
  }
  if (folders.empty()) {
    REXLOG_INFO("XIII-2 PS3 saves: none found (settings\\ps3_saves)");
    return;
  }
  std::sort(folders.begin(), folders.end());
  const fs::path list_path = settings / "ps3_saves_imported.txt";
  std::set<std::string> imported = read_list(list_path);
  int added = 0;
  for (const fs::path& dir : folders) {
    const std::vector<uint8_t> app = read_file(dir / "APP.DAT"), key_dat = read_file(dir / "KEY.DAT");
    const std::vector<uint8_t> pfd = read_file(dir / "PARAM.PFD"), sfo = read_file(dir / "PARAM.SFO");
    std::vector<uint8_t> both = app;
    both.insert(both.end(), key_dat.begin(), key_dat.end());
    char hash[17];
    std::snprintf(hash, sizeof(hash), "%016llX", static_cast<unsigned long long>(fnv64(both)));
    if (imported.count(hash)) continue;
    const std::string detail = x2_ps3_saves::SfoValue(sfo, "DETAIL");
    std::vector<uint8_t> xbox;
    std::string why;
    if (!x2_ps3_saves::Ps3ToXbox(app, key_dat, pfd, detail, key, parts, xbox, why)) {
      REXLOG_WARN("XIII-2 PS3 saves: {} not imported: {}", dir.string(), why);
      continue;
    }
    int number = 0;
    std::sscanf(x2_ps3_saves::SfoValue(sfo, "SUB_TITLE").c_str(), "Save Data %d", &number);
    const int slot = pick_slot(number - 1);
    if (slot < 0) {
      REXLOG_WARN("XIII-2 PS3 saves: {} not imported: no free save slot", dir.string());
      continue;
    }
    char name[32];
    std::snprintf(name, sizeof(name), "ff13-%02d.dat", slot);
    if (!write_slot(slot, xbox)) {
      REXLOG_WARN("XIII-2 PS3 saves: could not write slot {}", name);
      continue;
    }
    std::ofstream list(list_path, std::ios::app);
    list << hash << ' ' << name << ' ' << dir.string() << '\n';
    imported.insert(hash);
    ++added;
    std::string text = detail;
    for (char& c : text)
      if (c == '\n') c = ',';
    std::vector<uint8_t> check = DecryptXbox(xbox, key);
    const uint8_t* now = check.empty() ? nullptr : GameSettings(check);
    REXLOG_INFO("XIII-2 PS3 saves: {} -> {} (Save Data {:02d}: {}); game settings from a 360 save, Settings option 9 off "
                "(option bits {:08X}; the PS3 save's {:08X})",
                dir.string(), name, slot + 1, text, now ? ld_be(now) : 0, ps3_options(app, key_dat, pfd));
  }
  REXLOG_INFO("XIII-2 PS3 saves: {} found, {} newly imported", folders.size(), added);
}

void import_saves(uint8_t* base) {
  const fs::path settings = X2SettingsDir();
  if (settings.empty()) return;
  const fs::path saves = settings / "B13EBABEBABEBABE" / "53510806" / "00000001";
  const fs::path headers = settings / "B13EBABEBABEBABE" / "53510806" / "Headers" / "00000001";
  std::error_code ec;

  // The key: the game's of this start (the key setup of the hashed value), or the one the 360
  // game's own files give; whichever opens the saves already there.
  uint64_t seed = 0;
  for (int i = 0; i < 8; ++i) seed = seed << 8 | base[kKeySeed + i];
  const uint64_t runtime_key = SetupKey(seed);
  int opened_runtime = 0, opened_known = 0, others = 0;
  XboxParts parts;
  bool have_parts = false;
  for (const auto& entry : fs::directory_iterator(saves, ec)) {
    const std::vector<uint8_t> app = read_file(entry.path() / "APP.DAT");
    if (app.empty()) continue;
    std::vector<uint8_t> body = DecryptXbox(app, runtime_key);
    if (!body.empty()) ++opened_runtime;
    else if (!(body = DecryptXbox(app, kXboxKey)).empty()) ++opened_known;
    else ++others;
    // Not from a save that still holds PS3 settings (gameconfig byte 7 0x0E, the settings word
    // 0x0000BFD7; 360 saves: 0xEE and 0x0000FFD7), as PS3 saves imported by earlier builds do.
    XboxParts from;
    if (!body.empty() && !have_parts && PartsFrom(body, from) && from.gameconfig.size() == 12 &&
        (from.gameconfig[7] & 0xE0) == 0xE0 && from.settings_start.size() == 0x20 && (from.settings_start[14] & 0x40) &&
        !(ld_be(from.settings_start.data()) & kLargeUiBit)) {
      parts = from;
      have_parts = true;
    }
  }
  const uint64_t key = opened_known > opened_runtime ? kXboxKey : runtime_key;
  REXLOG_INFO("XIII-2 save key: {:016X} at this start{}; existing saves: {} open with it, {} with {:016X}, {} with neither",
              runtime_key, runtime_key == kXboxKey ? " (the 360 game's)" : "", opened_runtime, opened_known, kXboxKey, others);

  auto slot_used = [&](int slot) {
    char name[32];
    std::snprintf(name, sizeof(name), "ff13-%02d.dat", slot);
    return fs::exists(saves / name, ec) || fs::exists(headers / (std::string(name) + ".header"), ec);
  };
  auto read_list = [&](const fs::path& list_path) {
    std::set<std::string> imported;
    std::ifstream list(list_path);
    std::string line;
    while (std::getline(list, line))
      if (line.size() >= 16) imported.insert(line.substr(0, 16));
    return imported;
  };
  // The slot wanted when free, else the first free one; -1 when all are taken.
  auto pick_slot = [&](int slot) {
    if (slot < 0 || slot > kMaxSlot || slot_used(slot)) {
      slot = -1;
      for (int s = 0; s <= kMaxSlot && slot < 0; ++s)
        if (!slot_used(s)) slot = s;
    }
    return slot;
  };
  // Writes a converted save and its content header into a slot.
  auto write_slot = [&](int slot, const std::vector<uint8_t>& xbox) {
    char name[32];
    std::snprintf(name, sizeof(name), "ff13-%02d.dat", slot);
    fs::create_directories(saves / name, ec);
    fs::create_directories(headers, ec);
    const std::vector<uint8_t> header = content_header(name, slot);
    std::ofstream app(saves / name / "APP.DAT", std::ios::binary);
    app.write(reinterpret_cast<const char*>(xbox.data()), std::streamsize(xbox.size()));
    std::ofstream head(headers / (std::string(name) + ".header"), std::ios::binary);
    head.write(reinterpret_cast<const char*>(header.data()), std::streamsize(header.size()));
    return app.good() && head.good();
  };

  import_ps3_saves(settings, key, parts, pick_slot, write_slot, read_list);

  const std::vector<fs::path> files = steam_save_files(settings);
  if (files.empty()) {
    REXLOG_INFO("XIII-2 Steam saves: none found (%LOCALAPPDATA%\\SquareEnix\\FinalFantasyXIII-2\\save, settings\\steam_saves)");
    return;
  }
  const fs::path list_path = settings / "steam_saves_imported.txt";
  std::set<std::string> imported = read_list(list_path);
  int added = 0;
  for (const fs::path& path : files) {
    const std::vector<uint8_t> pc = read_file(path);
    char hash[17];
    std::snprintf(hash, sizeof(hash), "%016llX", static_cast<unsigned long long>(fnv64(pc)));
    if (imported.count(hash)) continue;
    std::vector<uint8_t> xbox;
    std::string why;
    if (!SteamToXbox(pc, key, parts, xbox, why)) {
      REXLOG_WARN("XIII-2 Steam saves: {} not imported: {}", path.string(), why);
      continue;
    }
    int slot = -1;
    std::sscanf(path.filename().string().c_str(), "ff13_2-%d.dat", &slot);
    slot = pick_slot(slot);
    if (slot < 0) {
      REXLOG_WARN("XIII-2 Steam saves: {} not imported: no free save slot", path.string());
      continue;
    }
    char name[32];
    std::snprintf(name, sizeof(name), "ff13-%02d.dat", slot);
    if (!write_slot(slot, xbox)) {
      REXLOG_WARN("XIII-2 Steam saves: could not write slot {}", name);
      continue;
    }
    std::ofstream list(list_path, std::ios::app);
    list << hash << ' ' << name << ' ' << path.string() << '\n';
    imported.insert(hash);
    ++added;
    REXLOG_INFO("XIII-2 Steam saves: {} -> {} (Save Data {:02d}: {}){}", path.string(), name, slot + 1, header_text(pc),
                have_parts ? "" : " - no 360 save to take gameconfig from, the defaults used");
  }
  REXLOG_INFO("XIII-2 Steam saves: {} found, {} newly imported", files.size(), added);
}
}  // namespace

// Called from the game's start (x2_settings.cpp, after sub_8225FC40).
void X2ImportSteamSaves(uint8_t* base) {
  static bool done = false;
  if (done) return;
  done = true;
  const char* v = std::getenv("X2_STEAM_SAVES");
  if (v && v[0] == '0') return;
  try {
    import_saves(base);
  } catch (const std::exception& e) {
    REXLOG_WARN("XIII-2 Steam saves: import stopped: {}", e.what());
  }
}
