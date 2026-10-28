// ffxiii2 - the Steam (PC) version's saves, converted to the Xbox 360 format (as the Lightning
// Returns port's lr_steam_saves.h does for that game).
//
// The two formats (from a Steam save and a 360 save of this game; both decrypt to their own
// checksum):
//   Steam ff13_2-NN.dat                      360 ff13-NN.dat\APP.DAT (548176 bytes)
//   header 0x210: 0x0D, u64 LE flags at 8    header 0x110: 0x0D, u64 BE flags at 8 (the same
//     (0x40_08004320 + progress << 4),         value), playtime and fragments text at 0x10
//     text at 0x10
//   body 0x85CC0 bytes, then more data the   body 0x85C40 bytes
//     360 format has no place for
//   the FF XIII save cipher, key             the same cipher; key from an MD5 the game takes at
//     0x65B738EF095C74E0 (after the setup)     start over its mounted archives and one data file
//                                              (sub_82509360; 0xAFE4711BE4130D89 after the setup
//                                              with the 360 game's own files)
//   section table: 58 x {name[16], u32 id/version, count, reserved, size}, little-endian on
//     Steam, big-endian on the 360; then a 0x20-byte record, the sections' data from 0x760 in
//     table order (byte for byte the same on both), the trailer [u32 LE length][u32 LE
//     checksum]. Two entries differ: db_partyman's id (0x106DE68C on the 360) and gameconfig
//     (Steam version 2, 0x8C bytes; 360 version 1, 0x0C bytes - another layout), so a converted
//     save takes both from a 360 save already there, or the values of the 360 save they were
//     read from.
#pragma once

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace x2_steam_saves {

constexpr size_t kPcHeader = 0x210, kPcBody = 0x85CC0;
constexpr size_t kXboxHeader = 0x110, kXboxBody = 0x85C40, kXboxSize = kXboxHeader + kXboxBody;
constexpr uint64_t kPcKey = 0x65B738EF095C74E0ull;    // after the key setup
constexpr uint64_t kXboxKey = 0xAFE4711BE4130D89ull;  // after the key setup, the 360 game's files
constexpr int kSections = 58;
constexpr size_t kTable = kSections * 32, kData = 0x760;
constexpr uint32_t kXboxPartyId = 0x106DE68C;
constexpr uint8_t kXboxGameconfig[12] = {0, 0, 0, 0, 0, 0, 0, 0xEE, 0, 0, 0, 0};

inline uint32_t ld_le(const uint8_t* p) { return p[0] | p[1] << 8 | p[2] << 16 | uint32_t(p[3]) << 24; }
inline uint32_t ld_be(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
inline void st_le(uint8_t* p, uint32_t v) {
  for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (8 * i));
}
inline void st_be(uint8_t* p, uint32_t v) {
  for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (24 - 8 * i));
}

// The game's key setup (sub_8286C950), applied to the 64-bit value it hashed.
inline uint64_t SetupKey(uint64_t k) {
  k = ((k & 0xFF00000000000000ull) >> 16) | ((k & 0x0000FF0000000000ull) << 16) | (k & 0x00FF00FFFFFFFFFFull);
  k = ((k & 0x00000000FF00FF00ull) >> 8) | ((k & 0x0000000000FF00FFull) << 8) | (k & 0xFFFFFFFF00000000ull);
  uint8_t b[8];
  for (int i = 0; i < 8; ++i) b[i] = uint8_t(k >> (8 * i));
  b[0] = uint8_t(b[0] + 0x45);
  for (int i = 1; i < 8; ++i) b[i] = uint8_t(((b[i - 1] + b[i] + 0xD4) ^ ((b[i - 1] << 2) & 0x3FC)) ^ 0x45);
  k = 0;
  for (int i = 0; i < 8; ++i) k |= uint64_t(b[i]) << (8 * i);
  return k;
}

// The FF XIII-family save cipher (as in bucanero's ff13-decrypter, GPL-3.0): 32 subkeys, each
// the previous times 5, applied per 8-byte block.
inline void Crypt(uint8_t* data, size_t size, uint64_t key, bool decrypt) {
  uint32_t lo[32], hi[32];
  uint8_t delta[32];
  for (int i = 0; i < 32; ++i) {
    uint32_t sum = 0;
    for (int b = 0; b < 8; ++b) sum += uint8_t(key >> (8 * b));
    lo[i] = uint32_t(key);
    hi[i] = uint32_t(key >> 32);
    delta[i] = uint8_t(0x78 * 8 - sum);
    key *= 5;
  }
  for (size_t j = 0; j < size / 8; ++j) {
    uint8_t* d = data + j * 8;
    const uint32_t klo = lo[j % 32], khi = hi[j % 32];
    const uint64_t o = j * 8;
    const uint64_t cog = o << 20;
    const uint32_t base = uint32_t(cog) | uint32_t(o << 10) | uint32_t(o);
    const uint32_t gear1 = base + 0xA1652347u;
    const uint32_t gear2 = (uint32_t(j * 2) | uint32_t(cog >> 32)) + (base > ~0xA1652347u ? 1u : 0u);
    if (decrypt) {
      uint8_t t[8], prev = 0;
      for (int i = 0; i < 8; ++i) {
        const uint8_t c = d[i];
        t[i] = uint8_t((i == 0 ? c ^ uint8_t(0x45 ^ j) : c ^ prev) + delta[j % 32]);
        prev = c;
      }
      const uint32_t a = ld_le(t), b = ld_le(t + 4);
      const uint32_t p2 = klo ^ gear1 ^ (a - klo);
      const uint32_t p1 = khi ^ gear2 ^ (b - khi - (a < klo ? 1u : 0u));
      st_le(d, p1);
      st_le(d + 4, p2);
    } else {
      const uint32_t p1 = ld_le(d), p2 = ld_le(d + 4);
      const uint32_t a = klo ^ gear1 ^ p2;
      const uint32_t b = khi ^ gear2 ^ p1;
      uint8_t t[8];
      st_le(t, a + klo);
      st_le(t + 4, b + khi + (a > ~klo ? 1u : 0u));
      uint8_t prev = 0;
      for (int i = 0; i < 8; ++i) {
        uint8_t c = uint8_t(t[i] - delta[j % 32]);
        c ^= i == 0 ? uint8_t(0x45 ^ j) : prev;
        d[i] = c;
        prev = c;
      }
    }
  }
}

// Byte 0 of every 32-bit word, all but the last 8 bytes.
inline uint32_t Checksum(const uint8_t* body, size_t size) {
  uint32_t sum = 0;
  for (size_t i = 0; i + 8 < size; i += 4) sum += body[i];
  return sum;
}

inline bool BodyOk(const uint8_t* body, size_t size) {
  return size > 8 && std::memcmp(body, "db_partyman\0", 12) == 0 && ld_le(body + size - 8) == size - 8 &&
         ld_le(body + size - 4) == Checksum(body, size);
}

// A 360 save decrypted (body only), or empty when the key does not fit.
inline std::vector<uint8_t> DecryptXbox(const std::vector<uint8_t>& app, uint64_t key) {
  if (app.size() != kXboxSize || app[0] != 0x0D) return {};
  std::vector<uint8_t> body(app.begin() + kXboxHeader, app.end());
  Crypt(body.data(), body.size(), key, true);
  if (!BodyOk(body.data(), body.size())) return {};
  return body;
}

// The game settings of a decrypted body: the gameconfig object's three words (the option bits,
// then two numeric options; sub_82790FA0 reads them). A section's data starts 0x20 bytes before
// the place the table's sizes give it (the 0x20 bytes after the table are db_partyman's start),
// so they are the 12 bytes before the table's "gameconfig" place. Null when the table does not fit.
inline uint8_t* GameSettings(std::vector<uint8_t>& body) {
  size_t from = kData;
  for (int s = 0; s < kSections; ++s) {
    const uint8_t* e = body.data() + s * 32;
    const uint32_t size = ld_be(e + 28);
    if (from + size > body.size() - 8) return nullptr;
    if (std::memcmp(e, "gameconfig\0", 11) == 0) return body.data() + from - 0x20;
    from += size;
  }
  return nullptr;
}
// Settings option 9 (bit 11 of the option bits; sub_825BE8A8 stores it, sub_825BE710 applies it
// to [0x82E86E8C]+3572, which the menus and dialogs scale themselves by): on, they are drawn
// about a third larger. The PS3 save has it on (0x00006A3D), the 360 and Steam saves off.
constexpr uint32_t kLargeUiBit = 0x0800;

// What a converted save takes from a 360 save (see the top).
// The last 0x20 bytes of db_bt_result in a 360 save (the 28-hour one): the start of the game
// settings that follow it (every section's last bytes are the start of the next one's data).
constexpr uint8_t kXboxSettingsStart[0x20] = {0, 0, 0x62, 0x3D, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xD7,
                                              0, 0, 0xFF, 0xD7, 0, 0, 0x0F, 0xFE, 0, 0, 0, 0, 0, 0, 0, 0};
struct XboxParts {
  uint32_t party_id = kXboxPartyId;
  std::vector<uint8_t> gameconfig = std::vector<uint8_t>(kXboxGameconfig, kXboxGameconfig + sizeof(kXboxGameconfig));
  std::vector<uint8_t> settings_start = std::vector<uint8_t>(kXboxSettingsStart, kXboxSettingsStart + 0x20);
};

// db_partyman's id and gameconfig of a decrypted 360 body.
inline bool PartsFrom(const std::vector<uint8_t>& body, XboxParts& parts) {
  size_t from = kData;
  for (int s = 0; s < kSections; ++s) {
    const uint8_t* e = body.data() + s * 32;
    const uint32_t size = ld_be(e + 28);
    if (from + size > body.size() - 8) return false;
    if (std::memcmp(e, "db_partyman\0", 12) == 0) parts.party_id = ld_be(e + 16);
    if (std::memcmp(e, "gameconfig\0", 11) == 0) parts.gameconfig.assign(body.begin() + from, body.begin() + from + size);
    if (std::memcmp(e, "db_bt_result\0", 13) == 0 && size >= 0x20)
      parts.settings_start.assign(body.begin() + from + size - 0x20, body.begin() + from + size);
    from += size;
  }
  return from == body.size() - 8;
}

inline bool SteamToXbox(const std::vector<uint8_t>& pc, uint64_t xbox_key, const XboxParts& parts, std::vector<uint8_t>& out,
                        std::string& why) {
  if (pc.size() < kPcHeader + kPcBody || pc[0] != 0x0D || std::memcmp(pc.data() + 0x10, "Total hours played:", 19) != 0) {
    why = "not a FINAL FANTASY XIII-2 Steam save";
    return false;
  }
  std::vector<uint8_t> body(pc.begin() + kPcHeader, pc.begin() + kPcHeader + kPcBody);
  Crypt(body.data(), body.size(), kPcKey, true);
  if (!BodyOk(body.data(), body.size())) {
    why = "does not decrypt (damaged, or another game version)";
    return false;
  }
  std::vector<uint8_t> xb(kTable, 0);
  std::vector<uint8_t> data;
  size_t from = kData;
  bool root = false, config = false;
  for (int s = 0; s < kSections; ++s) {
    const uint8_t* e = body.data() + s * 32;
    uint8_t* x = xb.data() + s * 32;
    std::memcpy(x, e, 16);
    uint32_t f[4];
    for (int i = 0; i < 4; ++i) f[i] = ld_le(e + 16 + 4 * i);
    if (from + f[3] > body.size() - 8) {
      why = "section table out of range";
      return false;
    }
    const uint8_t* src = body.data() + from;
    size_t size = f[3];
    from += f[3];
    if (std::memcmp(e, "db_partyman\0", 12) == 0) {
      f[0] = parts.party_id;
      root = true;
    } else if (std::memcmp(e, "gameconfig\0", 11) == 0) {
      if (f[0] != 2 || f[3] != 0x8C) {
        why = "unexpected save layout (gameconfig)";
        return false;
      }
      f[0] = 1;
      f[3] = uint32_t(parts.gameconfig.size());
      src = parts.gameconfig.data();
      size = parts.gameconfig.size();
      config = true;
    }
    for (int i = 0; i < 4; ++i) st_be(x + 16 + 4 * i, f[i]);
    data.insert(data.end(), src, src + size);
  }
  if (!root || !config || from != body.size() - 8) {
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
  st_le(xb.data() + xb.size() - 8, uint32_t(xb.size() - 8));
  st_le(xb.data() + xb.size() - 4, Checksum(xb.data(), xb.size()));
  Crypt(xb.data(), xb.size(), xbox_key, false);

  out.assign(kXboxHeader, 0);
  out[0] = 0x0D;
  for (int i = 0; i < 8; ++i) out[8 + i] = pc[15 - i];  // the flags, big-endian
  std::memcpy(out.data() + 0x10, pc.data() + 0x10, kXboxHeader - 0x10);
  out[kXboxHeader - 1] = 0;
  out.insert(out.end(), xb.begin(), xb.end());
  if (DecryptXbox(out, xbox_key).empty()) {
    why = "conversion self-check failed";
    return false;
  }
  return true;
}

}  // namespace x2_steam_saves
