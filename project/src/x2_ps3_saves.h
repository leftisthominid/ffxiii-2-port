// ffxiii2 - PS3 saves, converted to the Xbox 360 format (as the Lightning Returns port's
// lr_ps3_saves.h does for that game; the Steam saves: x2_steam_saves.h).
//
// The PS3 format (from a real PS3 save of BLUS30776, checked against 360 saves of this game):
//   PARAM.PFD: the PS3 system's save protection. Of the save's files it covers only PARAM.SFO and
//     KEY.DAT; KEY.DAT is AES-encrypted with the per-file key held in PARAM.PFD under the game's
//     secure file id. (RPCS3 writes KEY.DAT without it.)
//   KEY.DAT: 16 bytes; the save key is (bytes 0-7 XOR bytes 8-15, big-endian) | 1, XOR the
//     game's constant, then the key setup of the FF XIII-family save cipher.
//   APP.DAT (560736 bytes): no plain header; the whole file is the cipher body: the 360 body's
//     58 sections (big-endian, the same table), except:
//       db_partyman's id 0x106DE714 (360 0x106DE68C);
//       sections the PS3 version keeps larger: db_party0000-0003 0x1FC (360 0x1F4), every
//       ItemBag 4 + capacity x 32 (360 4 + capacity x 24; the capacity is the entry's first
//       field), db_inter_bt 0x4E70 (0x4C10), db_bt_result 0xB48 (0x908). In each, the 360
//       data is the PS3 data without zero bytes just before its last 0x20 bytes (checked; a
//       save with anything there is not imported).
//   A section's data starts 0x20 bytes before the place the table's sizes give it (the 0x20
//   bytes after the table are db_partyman's start), so a section's last 0x20 bytes by the table
//   are the start of the next one (an ItemBag's hold the next bag's capacity, db_bt_result's the
//   game settings, gameconfig).
//   db_bt_result (the last battle's result): a converted save starts with it empty, as a new
//     game does.
//   The game settings: those of a 360 save, with Settings option 9 off. The PS3 save has option 9
//     on (option bits 0x00006A3D, 360 saves 0x0000623D), which draws the menus and dialogs about a
//     third larger (x2_steam_saves.h, kLargeUiBit; the test saves made from a 360 save's
//     settings were the normal ones).
//   The 360 file's plain header (0x0D, flags, the "Total hours played / Total fragments" text)
//   is rebuilt: the text from PARAM.SFO (DETAIL), the flags those of an early 360 save
//   (0x4008004320; the game writes its own at the next save).
#pragma once

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include "x2_steam_saves.h"

namespace x2_ps3_saves {

using x2_steam_saves::ld_be;
using x2_steam_saves::ld_le;
using x2_steam_saves::st_be;
using x2_steam_saves::st_le;

constexpr size_t kPs3Size = 560736;
constexpr uint32_t kPs3PartyId = 0x106DE714;
constexpr uint64_t kXboxFlags = 0x4008004320ull;
// The save cipher's constant for this game and the KEY.DAT key derivation: bucanero's apollo-lib
// (source/decrypt.c, ff13_init_key, FFXIII_2_KEY in include/keys.h; GPL-3.0),
// https://github.com/bucanero/apollo-lib
constexpr uint64_t kGameKey = 0x9B1F01011A6438B0ull;
// The PS3 system's save protection: the syscon manager key and this game's secure file ids.
// All public:
//   syscon manager key - flatz's pfdtool (global.conf) and bucanero's Apollo Save Tool
//     (apollo-ps3, source/pfd_util.c); https://github.com/bucanero/apollo-ps3
//   secure file ids - Apollo's PS3 games.conf, FINAL FANTASY XIII-2 entries (BLUS30776 and
//     BLES01269); https://github.com/bucanero/apollo-patches (PS3/games.conf)
// The PFD layout and the file decryption follow pfdtool (pfd.c).
constexpr uint8_t kSysconManagerKey[16] = {0xD4, 0x13, 0xB8, 0x96, 0x63, 0xE1, 0xFE, 0x9F,
                                           0x75, 0x14, 0x3D, 0x3B, 0xB4, 0x56, 0x52, 0x74};
constexpr uint8_t kSecureFileIds[2][16] = {
    {0x20, 0xCC, 0xC7, 0x9F, 0x1C, 0x3B, 0x8B, 0x24, 0x91, 0x47, 0x53, 0x51, 0xC4, 0x86, 0xB4, 0x47},   // BLUS30776
    {0x58, 0xD0, 0xAB, 0xA0, 0x0D, 0x12, 0x7D, 0x50, 0xB9, 0x25, 0x63, 0x4D, 0xF5, 0x0E, 0x63, 0xE9}};  // BLES01269

// AES-128 (one block at a time), for the PS3 save protection.
class Aes128 {
 public:
  explicit Aes128(const uint8_t key[16]) {
    Tables();
    std::memcpy(rk_, key, 16);
    uint8_t rcon = 1;
    for (int i = 16; i < 176; i += 4) {
      uint8_t t[4] = {rk_[i - 4], rk_[i - 3], rk_[i - 2], rk_[i - 1]};
      if (i % 16 == 0) {
        const uint8_t f = t[0];
        t[0] = uint8_t(sbox_[t[1]] ^ rcon);
        t[1] = sbox_[t[2]];
        t[2] = sbox_[t[3]];
        t[3] = sbox_[f];
        rcon = Xtime(rcon);
      }
      for (int j = 0; j < 4; ++j) rk_[i + j] = uint8_t(rk_[i - 16 + j] ^ t[j]);
    }
  }
  void Encrypt(uint8_t b[16]) const {
    AddKey(b, 0);
    for (int r = 1; r <= 10; ++r) {
      for (int i = 0; i < 16; ++i) b[i] = sbox_[b[i]];
      Shift(b, false);
      if (r != 10) MixColumns(b, false);
      AddKey(b, r);
    }
  }
  void Decrypt(uint8_t b[16]) const {
    AddKey(b, 10);
    for (int r = 9; r >= 0; --r) {
      Shift(b, true);
      for (int i = 0; i < 16; ++i) b[i] = inv_sbox_[b[i]];
      AddKey(b, r);
      if (r) MixColumns(b, true);
    }
  }

 private:
  static uint8_t Xtime(uint8_t x) { return uint8_t((x << 1) ^ ((x & 0x80) ? 0x1B : 0)); }
  static uint8_t Mul(uint8_t a, uint8_t b) {
    uint8_t p = 0;
    for (; b; b >>= 1, a = Xtime(a))
      if (b & 1) p ^= a;
    return p;
  }
  static void Tables() {
    if (sbox_[0]) return;
    for (int x = 0; x < 256; ++x) {
      uint8_t inv = 0;
      for (int y = 1; y < 256 && x; ++y)
        if (Mul(uint8_t(x), uint8_t(y)) == 1) inv = uint8_t(y);
      uint8_t s = inv;
      for (int i = 1; i < 5; ++i) s ^= uint8_t((inv << i) | (inv >> (8 - i)));
      s ^= 0x63;
      sbox_[x] = s;
      inv_sbox_[s] = uint8_t(x);
    }
  }
  void AddKey(uint8_t b[16], int r) const {
    for (int i = 0; i < 16; ++i) b[i] ^= rk_[r * 16 + i];
  }
  static void Shift(uint8_t b[16], bool inverse) {
    uint8_t t[16];
    for (int c = 0; c < 4; ++c)
      for (int r = 0; r < 4; ++r) {
        const int from = inverse ? ((c - r + 4) % 4) : ((c + r) % 4);
        t[c * 4 + r] = b[from * 4 + r];
      }
    std::memcpy(b, t, 16);
  }
  static void MixColumns(uint8_t b[16], bool inverse) {
    const uint8_t m[4] = {uint8_t(inverse ? 14 : 2), uint8_t(inverse ? 11 : 3), uint8_t(inverse ? 13 : 1),
                          uint8_t(inverse ? 9 : 1)};
    for (int c = 0; c < 4; ++c) {
      uint8_t* col = b + c * 4;
      uint8_t t[4];
      for (int r = 0; r < 4; ++r)
        t[r] = uint8_t(Mul(col[r], m[0]) ^ Mul(col[(r + 1) % 4], m[1]) ^ Mul(col[(r + 2) % 4], m[2]) ^
                       Mul(col[(r + 3) % 4], m[3]));
      std::memcpy(col, t, 4);
    }
  }
  uint8_t rk_[176];
  static inline uint8_t sbox_[256] = {};
  static inline uint8_t inv_sbox_[256] = {};
};


// A file protected by PARAM.PFD -> its plain content (as pfdtool decrypts it), with one secure
// file id. False when the file is not listed there.
inline bool PfdDecrypt(const std::vector<uint8_t>& pfd, const std::string& file_name, const uint8_t secure_file_id[16],
                       std::vector<uint8_t>& data) {
  if (pfd.size() < 0x78 || ld_be(pfd.data() + 4) != 0x50464442u) return false;
  const uint64_t capacity = ld_be(pfd.data() + 0x64), used = ld_be(pfd.data() + 0x74);
  const size_t table = 0x78 + size_t(capacity) * 8;
  for (uint64_t i = 0; i < used; ++i) {
    const uint8_t* e = pfd.data() + table + size_t(i) * 272;
    if (e + 272 > pfd.data() + pfd.size()) return false;
    if (strnlen(reinterpret_cast<const char*>(e + 8), 65) != file_name.size() ||
        std::memcmp(e + 8, file_name.data(), file_name.size()) != 0)
      continue;
    const uint64_t size = uint64_t(ld_be(e + 264)) << 32 | ld_be(e + 268);
    if (size > data.size()) return false;
    // The entry key: AES-CBC with the syscon manager key, the IV made from the secure file id.
    uint8_t iv[16];
    for (int k = 0, j = 0; k < 16; ++k) iv[k] = k == 1 ? 11 : k == 2 ? 15 : k == 5 ? 14 : k == 8 ? 10 : secure_file_id[j++];
    uint8_t key[16];
    std::memcpy(key, e + 80, 16);
    Aes128(kSysconManagerKey).Decrypt(key);
    for (int k = 0; k < 16; ++k) key[k] ^= iv[k];
    // The file: per 16-byte block, AES decrypt, XOR the encrypted block counter.
    const Aes128 aes(key);
    data.resize((data.size() + 15) & ~size_t(15));
    for (size_t b = 0; b < data.size() / 16; ++b) {
      uint8_t counter[16] = {};
      for (int k = 0; k < 8; ++k) counter[k] = uint8_t(uint64_t(b) >> (56 - 8 * k));
      aes.Encrypt(counter);
      aes.Decrypt(data.data() + b * 16);
      for (int k = 0; k < 16; ++k) data[b * 16 + k] ^= counter[k];
    }
    data.resize(size_t(size));
    return true;
  }
  return false;
}

inline uint64_t SaveKey(const std::vector<uint8_t>& key_dat) {
  uint64_t k = 0;
  for (int i = 0; i < 8; ++i) k = k << 8 | uint8_t(key_dat[i] ^ key_dat[8 + i]);
  return x2_steam_saves::SetupKey((k | 1) ^ kGameKey);
}

// The plain body, or false.
inline bool DecryptApp(const std::vector<uint8_t>& app, const std::vector<uint8_t>& key_dat, std::vector<uint8_t>& body) {
  if (app.size() != kPs3Size || key_dat.size() != 16) return false;
  body = app;
  x2_steam_saves::Crypt(body.data(), body.size(), SaveKey(key_dat), true);
  return x2_steam_saves::BodyOk(body.data(), body.size());
}

// A PARAM.SFO text value (DETAIL: "Total hours played: 007:59:15\nTotal fragments: 002/160",
// SUB_TITLE: "Save Data 3"), or empty.
inline std::string SfoValue(const std::vector<uint8_t>& sfo, const char* name) {
  if (sfo.size() < 20 || ld_be(sfo.data()) != 0x00505346u) return {};
  const uint32_t keys = ld_le(sfo.data() + 8), values = ld_le(sfo.data() + 12), count = ld_le(sfo.data() + 16);
  const size_t name_size = std::strlen(name) + 1;
  for (uint32_t i = 0; i < count && 20 + (i + 1) * 16 <= sfo.size(); ++i) {
    const uint8_t* e = sfo.data() + 20 + i * 16;
    const size_t key = keys + (e[0] | e[1] << 8), len = ld_le(e + 4), value = values + ld_le(e + 12);
    if (key + name_size > sfo.size() || std::memcmp(sfo.data() + key, name, name_size) != 0 || value + len > sfo.size()) continue;
    return std::string(reinterpret_cast<const char*>(sfo.data() + value), strnlen(reinterpret_cast<const char*>(sfo.data() + value), len));
  }
  return {};
}

// The 360 size of a section the PS3 version keeps larger, or 0 when both are the same size.
inline uint32_t XboxSize(const uint8_t* entry, uint32_t ps3_size, bool& known) {
  known = true;
  const uint32_t first = ld_be(entry + 16);
  for (const char* n : {"db_party0000", "db_party0001", "db_party0002", "db_party0003"})
    if (std::memcmp(entry, n, 13) == 0) return ps3_size == 0x1FC ? 0x1F4 : (known = false, 0);
  if (std::memcmp(entry, "db_inter_bt\0", 12) == 0) return ps3_size == 0x4E70 ? 0x4C10 : (known = false, 0);
  if (std::memcmp(entry, "db_bt_result\0", 13) == 0) return ps3_size == 0xB48 ? 0x908 : (known = false, 0);
  if (std::memcmp(entry, "ItemBag\0", 8) == 0) return ps3_size == 4 + first * 32 ? 4 + first * 24 : (known = false, 0);
  return 0;
}

// A PS3 save folder's files -> 360 APP.DAT, encrypted with xbox_key. False (and why) when it is
// not a FINAL FANTASY XIII-2 save or does not convert.
inline bool Ps3ToXbox(const std::vector<uint8_t>& app, const std::vector<uint8_t>& key_in, const std::vector<uint8_t>& pfd,
                      const std::string& detail, uint64_t xbox_key, const x2_steam_saves::XboxParts& parts,
                      std::vector<uint8_t>& out, std::string& why) {
  using namespace x2_steam_saves;
  if (app.size() != kPs3Size) {
    why = "not a FINAL FANTASY XIII-2 PS3 save (APP.DAT size)";
    return false;
  }
  std::vector<uint8_t> body;
  bool ok = DecryptApp(app, key_in, body);
  for (int id = 0; id < 2 && !ok && !pfd.empty(); ++id) {
    std::vector<uint8_t> key = key_in;
    ok = PfdDecrypt(pfd, "KEY.DAT", kSecureFileIds[id], key) && DecryptApp(app, key, body);
  }
  if (!ok) {
    why = "does not decrypt (damaged, or KEY.DAT / PARAM.PFD missing or from another save)";
    return false;
  }
  std::vector<uint8_t> xb(kTable, 0);
  std::vector<uint8_t> data;
  size_t from = kData;
  bool root = false;
  for (int s = 0; s < kSections; ++s) {
    const uint8_t* e = body.data() + s * 32;
    uint8_t* x = xb.data() + s * 32;
    std::memcpy(x, e, 32);
    const uint32_t size = ld_be(e + 28);
    if (from + size > body.size() - 8) {
      why = "section table out of range";
      return false;
    }
    const uint8_t* src = body.data() + from;
    from += size;
    if (std::memcmp(e, "db_partyman\0", 12) == 0) {
      if (ld_be(e + 16) != kPs3PartyId) {
        why = "unexpected save layout (db_partyman)";
        return false;
      }
      st_be(x + 16, kXboxPartyId);
      root = true;
    }
    if (std::memcmp(e, "gameconfig\0", 11) == 0) {
      if (size != parts.gameconfig.size()) {
        why = "unexpected save layout (gameconfig)";
        return false;
      }
      data.insert(data.end(), parts.gameconfig.begin(), parts.gameconfig.end());
      continue;
    }
    bool known;
    const uint32_t xsize = XboxSize(e, size, known);
    if (!known) {
      why = std::string("unexpected save layout (") + std::string(reinterpret_cast<const char*>(e), strnlen(reinterpret_cast<const char*>(e), 16)) + ")";
      return false;
    }
    if (!xsize) {
      data.insert(data.end(), src, src + size);
      continue;
    }
    if (std::memcmp(e, "db_bt_result\0", 13) == 0) {
      // Empty, then the 360 settings' start (see the top).
      data.insert(data.end(), xsize - 0x20, 0);
      data.insert(data.end(), parts.settings_start.begin(), parts.settings_start.end());
      st_be(x + 28, xsize);
      continue;
    }
    for (size_t i = xsize - 0x20; i < size - 0x20; ++i) {
      if (src[i]) {
        why = "the save holds data the 360 version has no place for";
        return false;
      }
    }
    data.insert(data.end(), src, src + (xsize - 0x20));
    data.insert(data.end(), src + (size - 0x20), src + size);
    st_be(x + 28, xsize);
  }
  if (!root || from != body.size() - 8) {
    why = "unexpected save layout";
    return false;
  }
  xb.insert(xb.end(), body.begin() + kTable, body.begin() + kData);  // the 0x20-byte record
  xb.insert(xb.end(), data.begin(), data.end());
  xb.resize(xb.size() + 8);
  if (xb.size() != kXboxBody) {
    why = "unexpected save size";
    return false;
  }
  uint8_t* settings = GameSettings(xb);
  if (!settings) {
    why = "unexpected save layout (game settings)";
    return false;
  }
  st_be(settings, ld_be(settings) & ~kLargeUiBit);
  st_le(xb.data() + xb.size() - 8, uint32_t(xb.size() - 8));
  st_le(xb.data() + xb.size() - 4, Checksum(xb.data(), xb.size()));
  Crypt(xb.data(), xb.size(), xbox_key, false);

  out.assign(kXboxHeader, 0);
  out[0] = 0x0D;
  for (int i = 0; i < 8; ++i) out[8 + i] = uint8_t(kXboxFlags >> (56 - 8 * i));
  std::memcpy(out.data() + 0x10, detail.data(), std::min(detail.size(), kXboxHeader - 0x11));
  out.insert(out.end(), xb.begin(), xb.end());
  if (DecryptXbox(out, xbox_key).empty()) {
    why = "conversion self-check failed";
    return false;
  }
  return true;
}

// A 360 save imported from the PS3 by an earlier build: Settings option 9 turned off, in place.
// before: its option bits as they were. False when it does not open or the option is off.
inline bool LargeUiOff(std::vector<uint8_t>& app, uint64_t xbox_key, uint32_t& before) {
  using namespace x2_steam_saves;
  std::vector<uint8_t> body = DecryptXbox(app, xbox_key);
  uint8_t* settings = body.empty() ? nullptr : GameSettings(body);
  if (!settings) return false;
  before = ld_be(settings);
  if (!(before & kLargeUiBit)) return false;
  st_be(settings, before & ~kLargeUiBit);
  st_le(body.data() + body.size() - 4, Checksum(body.data(), body.size()));
  Crypt(body.data(), body.size(), xbox_key, false);
  std::copy(body.begin(), body.end(), app.begin() + kXboxHeader);
  return !DecryptXbox(app, xbox_key).empty();
}

}  // namespace x2_ps3_saves
