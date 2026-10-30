// FFXIII-2 port - Japanese voice patch builder (from the Lightning Returns port's lr_jpvoice).
//
// Reads the English voice files of the Xbox 360 release and the Japanese voice files of the
// Steam release (set "c": alba_data\sys\filelistc, the udp\white_patch_c update archives,
// and the zone archives; voice lines matched by path), converts every Japanese voice file
// to the 360 layout, and writes them as a 360 update patch:
//   <out>/filelist_patch_u.x360.bin, white_patch_u.x360.bin           (base and zone files)
//   <out>/filelist_patch_scr_u.x360.bin, white_patch_scr_u.x360.bin   (scenario archive files)
// The game reads that folder as UPDATE:\ (--update_data_root; the port mounts it when Settings >
// Port Settings > Voice Language is Japanese) and then loads the patched files instead of the
// ones in its own archives. The 360 and Steam files are only read.
//
//   x2_jpvoice [--ffmpeg <ffmpeg>] <360 game folder> <Steam game folder> <out folder> [samples folder]
//   x2_jpvoice --scd <360 english.scd> <steam japanese.scd> <out.scd>
//   x2_jpvoice --wpd <360 english.wpd> <steam japanese.wpd> <out.wpd>
//   x2_jpvoice --list <filelist.bin>
//   x2_jpvoice --dlc-sample <Steam game folder> <360 DLC root> <out folder>   (dlc_sample)
//
// SCD: "SEDBSSCF" sound container. The 360 files are big-endian, the Steam ones little-endian;
// both hold Ogg Vorbis voices (codec 6) or the Steam ones MS ADPCM (re-encoded with ffmpeg). The
// converted file keeps the 360 English file's header and sound tables and takes the Japanese
// stream headers (byte-swapped) and audio. WPD: simple bundle of SCDs, converted member by
// member. The same patch also gets the Japanese lip-sync and facial motion (merge_jp_motion).
// The on-screen text needs no conversion: the 360 disc has every language (x2_settings.cpp).
#include <algorithm>
#include <atomic>
#include <chrono>
#include <unordered_map>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>
#include "../project/src/x2_jp_dlc_provenance.h"
#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;
typedef std::vector<uint8_t> Bytes;

static uint32_t rd32(const uint8_t* p, bool be) {
  return be ? (uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3])
            : (uint32_t(p[3]) << 24 | uint32_t(p[2]) << 16 | uint32_t(p[1]) << 8 | p[0]);
}
static uint16_t rd16(const uint8_t* p, bool be) {
  return be ? uint16_t(p[0] << 8 | p[1]) : uint16_t(p[1] << 8 | p[0]);
}
static void wr32(uint8_t* p, uint32_t v, bool be) {
  for (int i = 0; i < 4; i++) p[be ? i : 3 - i] = uint8_t(v >> (24 - 8 * i));
}
static void wr16(uint8_t* p, uint16_t v, bool be) {
  p[be ? 0 : 1] = uint8_t(v >> 8);
  p[be ? 1 : 0] = uint8_t(v);
}
static void swap32(uint8_t* p) { std::swap(p[0], p[3]); std::swap(p[1], p[2]); }
static size_t align_up(size_t v, size_t a) { return (v + a - 1) / a * a; }

static Bytes read_file(const fs::path& p) {
  std::ifstream f(p, std::ios::binary);
  if (!f) throw std::runtime_error("cannot open " + p.string());
  return Bytes((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}
static void write_file(const fs::path& p, const Bytes& d) {
  std::ofstream f(p, std::ios::binary);
  f.write((const char*)d.data(), d.size());
  if (!f) throw std::runtime_error("cannot write " + p.string());
}

// ---------------------------------------------------------------- inflate (RFC 1950/1951)

namespace inflate {
struct State {
  const uint8_t* in;
  size_t n, pos = 0;
  uint32_t buf = 0;
  int cnt = 0;
  Bytes* out;
  int bits(int need) {
    uint32_t v = buf;
    while (cnt < need) {
      if (pos >= n) throw std::runtime_error("inflate: out of input");
      v |= uint32_t(in[pos++]) << cnt;
      cnt += 8;
    }
    buf = v >> need;
    cnt -= need;
    return int(v & ((1u << need) - 1));
  }
};
struct Huff {
  uint16_t count[16];
  uint16_t sym[320];
};
static void build(Huff& h, const uint8_t* len, int n) {
  memset(h.count, 0, sizeof(h.count));
  for (int i = 0; i < n; i++) h.count[len[i]]++;
  h.count[0] = 0;
  uint16_t offs[16];
  offs[1] = 0;
  for (int l = 1; l < 15; l++) offs[l + 1] = offs[l] + h.count[l];
  for (int i = 0; i < n; i++)
    if (len[i]) h.sym[offs[len[i]]++] = uint16_t(i);
}
static int decode(State& s, const Huff& h) {
  int code = 0, first = 0, index = 0;
  for (int len = 1; len < 16; len++) {
    code |= s.bits(1);
    int count = h.count[len];
    if (code - count < first) return h.sym[index + (code - first)];
    index += count;
    first += count;
    first <<= 1;
    code <<= 1;
  }
  throw std::runtime_error("inflate: bad code");
}
static const uint16_t lbase[29] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27,
                                   31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
static const uint8_t lext[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2,
                                 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
static const uint16_t dbase[30] = {1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129,
                                   193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097,
                                   6145, 8193, 12289, 16385, 24577};
static const uint8_t dext[30] = {0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6,
                                 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
static void codes(State& s, const Huff& lc, const Huff& dc) {
  for (;;) {
    int sym = decode(s, lc);
    if (sym < 256) {
      s.out->push_back(uint8_t(sym));
    } else if (sym == 256) {
      return;
    } else {
      sym -= 257;
      if (sym >= 29) throw std::runtime_error("inflate: bad length");
      size_t len = lbase[sym] + s.bits(lext[sym]);
      int ds = decode(s, dc);
      if (ds >= 30) throw std::runtime_error("inflate: bad distance");
      size_t dist = dbase[ds] + s.bits(dext[ds]);
      if (dist > s.out->size()) throw std::runtime_error("inflate: distance too far");
      size_t from = s.out->size() - dist;
      for (size_t i = 0; i < len; i++) s.out->push_back((*s.out)[from + i]);
    }
  }
}
// zlib stream -> appended to out
static void zlib(const uint8_t* in, size_t n, Bytes& out) {
  if (n < 2 || (in[0] & 15) != 8 || ((in[0] << 8) | in[1]) % 31)
    throw std::runtime_error("inflate: not zlib data");
  State s;
  s.in = in + 2;
  s.n = n - 2;
  s.out = &out;
  int last;
  do {
    last = s.bits(1);
    int type = s.bits(2);
    if (type == 0) {
      s.buf = 0;
      s.cnt = 0;
      if (s.pos + 4 > s.n) throw std::runtime_error("inflate: out of input");
      unsigned len = s.in[s.pos] | s.in[s.pos + 1] << 8;
      s.pos += 4;
      if (s.pos + len > s.n) throw std::runtime_error("inflate: out of input");
      out.insert(out.end(), s.in + s.pos, s.in + s.pos + len);
      s.pos += len;
    } else if (type == 1) {
      static Huff lc, dc;
      static bool init = false;
      if (!init) {
        uint8_t l[288];
        for (int i = 0; i < 288; i++) l[i] = i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8;
        build(lc, l, 288);
        for (int i = 0; i < 30; i++) l[i] = 5;
        build(dc, l, 30);
        init = true;
      }
      codes(s, lc, dc);
    } else if (type == 2) {
      int nlen = s.bits(5) + 257, ndist = s.bits(5) + 1, ncode = s.bits(4) + 4;
      static const uint8_t order[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5,
                                        11, 4, 12, 3, 13, 2, 14, 1, 15};
      uint8_t l[320] = {0};
      for (int i = 0; i < ncode; i++) l[order[i]] = uint8_t(s.bits(3));
      Huff lc, dc;
      build(lc, l, 19);
      int i = 0;
      uint8_t lens[320] = {0};
      while (i < nlen + ndist) {
        int sym = decode(s, lc);
        if (sym < 16) {
          lens[i++] = uint8_t(sym);
        } else {
          int rep, val = 0;
          if (sym == 16) {
            if (!i) throw std::runtime_error("inflate: bad repeat");
            val = lens[i - 1];
            rep = 3 + s.bits(2);
          } else if (sym == 17) {
            rep = 3 + s.bits(3);
          } else {
            rep = 11 + s.bits(7);
          }
          if (i + rep > nlen + ndist) throw std::runtime_error("inflate: bad lengths");
          while (rep--) lens[i++] = uint8_t(val);
        }
      }
      build(lc, lens, nlen);
      build(dc, lens + nlen, ndist);
      codes(s, lc, dc);
    } else {
      throw std::runtime_error("inflate: bad block type");
    }
  } while (!last);
}
}  // namespace inflate

// zlib stream made of stored blocks (what the file list chunks need; no compression needed)
static Bytes zlib_stored(const Bytes& d) {
  Bytes z = {0x78, 0x01};
  size_t pos = 0;
  do {
    size_t n = std::min<size_t>(65535, d.size() - pos);
    z.push_back(pos + n == d.size() ? 1 : 0);
    z.push_back(uint8_t(n));
    z.push_back(uint8_t(n >> 8));
    z.push_back(uint8_t(~n));
    z.push_back(uint8_t(~n >> 8));
    z.insert(z.end(), d.begin() + pos, d.begin() + pos + n);
    pos += n;
  } while (pos < d.size());
  uint32_t a = 1, b = 0;
  for (uint8_t c : d) {
    a = (a + c) % 65521;
    b = (b + a) % 65521;
  }
  uint32_t adler = b << 16 | a;
  for (int i = 3; i >= 0; i--) z.push_back(uint8_t(adler >> (8 * i)));
  return z;
}

// ---------------------------------------------------------------- file lists
// Port of the file list crypto from Surihix's WhiteBinTools (GPL-3.0).

struct Entry {
  uint32_t code = 0;
  uint8_t ftype = 0;
  uint64_t sector = 0;
  uint32_t usize = 0, csize = 0;
  std::string path;
};

static void xor_table(uint64_t seed64, uint8_t table[264]) {
  uint8_t seed[8];
  for (int i = 0; i < 8; i++) seed[7 - i] = uint8_t(seed64 >> (8 * i));  // reversed LE bytes
  uint32_t a = rd32(seed, false), b = rd32(seed + 4, false);
  a = (a << 8) | (a >> 24);
  b = (b >> 16) | (b << 16);
  uint8_t blk[8];
  wr32(blk, b, false);
  wr32(blk + 4, a, false);
  blk[0] = uint8_t(blk[0] + 0x45);
  for (int i = 1; i < 8; i++) {
    unsigned t = blk[i] + 0xD4 + blk[i - 1];
    t ^= unsigned(blk[i - 1]) << 2;
    t ^= 0x45;
    blk[i] = uint8_t(t);
  }
  memcpy(table, blk, 8);
  uint64_t prev = uint64_t(rd32(blk, false)) | uint64_t(rd32(blk + 4, false)) << 32;
  for (int i = 1; i < 0x21; i++) {
    uint32_t ha = uint32_t(prev), hb = uint32_t(prev >> 32);
    uint64_t x = prev * 5;
    x ^= uint64_t(hb) << 32;
    uint32_t ta = ha ^ uint32_t(x);
    uint32_t tb = uint32_t(x >> 32);
    x = ha | (x & 0xFFFFFFFF00000000ull);
    uint32_t xa = uint32_t(x) ^ ta;
    uint32_t xb = tb ^ hb;
    wr32(table + i * 8, xa, false);
    wr32(table + i * 8 + 4, xb, false);
    prev = uint64_t(xa) | uint64_t(xb) << 32;
  }
}

static Bytes decrypt_filelist(const Bytes& data) {
  if (data.size() < 32) throw std::runtime_error("file list too small");
  uint32_t body = rd32(&data[16], true) + 8;
  const uint8_t* s = data.data();
  uint32_t seed32 = uint32_t(s[9]) << 24 | uint32_t(s[12]) << 16 | uint32_t(s[2]) << 8 | s[0];
  uint64_t seed = seed32;
  if (seed32 & 0x80000000u) seed |= 0xFFFFFFFF00000000ull;
  uint8_t table[264];
  xor_table(seed, table);
  Bytes out = data;
  size_t pos = 32;
  if (32 + size_t(body) > data.size()) throw std::runtime_error("file list truncated");
  for (uint32_t n = 0; n < body / 8; n++, pos += 8) {
    uint32_t counter = n * 8;
    const uint8_t* cur = &data[pos];
    unsigned off = counter & 0xF8;
    auto loop = [&](unsigned v) {
      for (int k = 0; k < 8; k++) v = (((v + 120) & 0xFF) - table[off + k]) & 0xFF;
      return uint8_t(v);
    };
    uint8_t d[8];
    d[0] = loop(((n ^ 69) & 255) ^ cur[0]);
    for (int k = 1; k < 8; k++) d[k] = loop(cur[k - 1] ^ cur[k]);
    uint8_t arr[8] = {d[4], d[5], d[6], d[7], d[0], d[1], d[2], d[3]};
    uint32_t hi = rd32(arr, false), lo = rd32(arr + 4, false);
    uint32_t xlo = rd32(table + off, false), xhi = rd32(table + off + 4, false);
    uint64_t c = counter;
    uint64_t ab = c << 10, cd = c << 20, ef = c << 30;
    uint64_t cv = (cd & 0xFFFFFFFF) | (ab & 0xFFFFFFFF);
    uint64_t dv = (cd >> 32) | (ab >> 32);
    uint32_t ev = uint32_t((ef & 0xFFFFFFFF) | c | cv);
    uint32_t fv = uint32_t((ef >> 32) | dv);
    uint32_t carry1 = ev > uint32_t(~0xA1652347u) ? 1 : 0;
    uint32_t k1 = ev + 0xA1652347u;
    uint32_t k2 = fv + carry1;
    uint32_t L = lo - xlo;
    uint32_t H = hi - xhi - (lo < xlo ? 1 : 0);
    L ^= k1;
    H ^= k2;
    L ^= xlo;
    H ^= xhi;
    wr32(&out[pos], H, false);
    wr32(&out[pos + 4], L, false);
  }
  uint32_t size = rd32(&out[16], true);
  if (32 + size_t(size) + 8 > out.size()) throw std::runtime_error("file list size field");
  uint32_t chk = rd32(&out[32 + size + 4], false), calc = 0;
  for (uint32_t i = 0; i < size / 4; i++) calc += out[32 + 4 * i];
  if (chk != calc) throw std::runtime_error("file list checksum mismatch after decrypt");
  return out;
}

static std::vector<Entry> parse_filelist(Bytes raw) {
  size_t base = 0;
  if (raw.size() >= 24 && rd32(&raw[20], false) == 0x1DE03478) {
    raw = decrypt_filelist(raw);
    base = 32;
  }
  if (raw.size() < base + 12) throw std::runtime_error("file list too small");
  bool be = rd32(&raw[base + 8], false) >= 0x100000;
  uint32_t chunk_tab = rd32(&raw[base], be), chunk_data = rd32(&raw[base + 4], be),
           count = rd32(&raw[base + 8], be);
  if (base + 12 + size_t(count) * 8 > raw.size()) throw std::runtime_error("file list entries");
  struct Raw { uint32_t code; unsigned info, chunk; uint8_t ftype; };
  std::vector<Raw> ents(count);
  unsigned nchunks = 0;
  for (uint32_t i = 0; i < count; i++) {
    const uint8_t* p = &raw[base + 12 + i * 8];
    Raw r;
    r.code = rd32(p, be);
    unsigned info = rd16(p + 4, be);
    r.chunk = unsigned(p[6]) << 1 | info >> 15;
    r.info = info & 0x7FFF;
    r.ftype = p[7];
    ents[i] = r;
    nchunks = std::max(nchunks, r.chunk + 1);
  }
  std::vector<Bytes> chunks(nchunks);
  for (unsigned c = 0; c < nchunks; c++) {
    size_t t = base + chunk_tab + 12 * c;
    if (t + 12 > raw.size()) throw std::runtime_error("file list chunk table");
    uint32_t un = rd32(&raw[t], be), cm = rd32(&raw[t + 4], be), cp = rd32(&raw[t + 8], be);
    size_t p = base + chunk_data + cp;
    if (p + cm > raw.size()) throw std::runtime_error("file list chunk data");
    inflate::zlib(&raw[p], cm, chunks[c]);
    if (chunks[c].size() != un) throw std::runtime_error("file list chunk size");
  }
  std::vector<Entry> out;
  out.reserve(count);
  for (auto& r : ents) {
    const Bytes& b = chunks[r.chunk];
    if (r.info >= b.size()) throw std::runtime_error("file list string offset");
    std::string text((const char*)&b[r.info]);
    Entry e;
    e.code = r.code;
    e.ftype = r.ftype;
    unsigned long long sec = 0, us = 0, cs = 0;
    char path[1024] = {0};
    if (sscanf(text.c_str(), "%llx:%llx:%llx:%1023s", &sec, &us, &cs, path) != 4)
      throw std::runtime_error("file list entry text: " + text);
    e.sector = sec;
    e.usize = uint32_t(us);
    e.csize = uint32_t(cs);
    e.path = path;
    out.push_back(e);
  }
  return out;
}

// ---------------------------------------------------------------- archives

struct Archive {
  fs::path path;
  std::ifstream f;
  uint64_t size = 0;
  explicit Archive(const fs::path& p) : path(p), f(p, std::ios::binary) {
    if (!f) throw std::runtime_error("cannot open " + p.string());
    size = fs::file_size(p);
  }
  Bytes raw(uint64_t offset, uint32_t n) {
    if (offset + n > size) throw std::runtime_error("entry past the end of " + path.filename().string());
    Bytes b(n);
    f.clear();
    f.seekg(std::streamoff(offset));
    f.read((char*)b.data(), n);
    if (!f) throw std::runtime_error("read failed in " + path.filename().string());
    return b;
  }
  Bytes read(const Entry& e) {
    Bytes b = raw(e.sector * 2048, e.csize);
    if (e.csize == e.usize) return b;
    Bytes out;
    out.reserve(e.usize);
    inflate::zlib(b.data(), b.size(), out);
    if (out.size() != e.usize) throw std::runtime_error("unexpected size after inflate: " + e.path);
    return out;
  }
};

// A file list together with the archive it describes.
struct Source {
  std::string name;
  std::shared_ptr<Archive> archive;
  std::vector<Entry> entries;
};

// ---------------------------------------------------------------- SCD

struct Scd {
  bool be = true;
  uint32_t tables = 0, headers_offset = 0;
  std::vector<uint32_t> metas;  // table 3 (stream headers), in table order
};

static bool parse_scd(const Bytes& d, Scd& s, std::string& why) {
  if (d.size() < 0x50 || memcmp(d.data(), "SEDBSSCF", 8)) { why = "not an SCD"; return false; }
  s.be = d[0x0C] == 1;
  uint32_t version = rd32(&d[8], s.be);
  if (version != 3) { why = "SCD version " + std::to_string(version); return false; }
  s.tables = rd16(&d[0x0E], s.be);
  if (s.tables + 0x20 > d.size()) { why = "SCD tables"; return false; }
  unsigned n = rd16(&d[s.tables + 4], s.be);
  s.headers_offset = rd32(&d[s.tables + 0x0C], s.be);
  if (s.headers_offset + 4ull * n > d.size()) { why = "SCD header table"; return false; }
  s.metas.clear();
  for (unsigned i = 0; i < n; i++) {
    uint32_t m = rd32(&d[s.headers_offset + 4 * i], s.be);
    if (m + 0x20ull > d.size()) { why = "SCD stream header offset"; return false; }
    s.metas.push_back(m);
  }
  return true;
}

static const uint8_t kOggV3Table[256] = {
    0x3A, 0x32, 0x32, 0x32, 0x03, 0x7E, 0x12, 0xF7, 0xB2, 0xE2, 0xA2, 0x67, 0x32, 0x32, 0x22, 0x32,
    0x32, 0x52, 0x16, 0x1B, 0x3C, 0xA1, 0x54, 0x7B, 0x1B, 0x97, 0xA6, 0x93, 0x1A, 0x4B, 0xAA, 0xA6,
    0x7A, 0x7B, 0x1B, 0x97, 0xA6, 0xF7, 0x02, 0xBB, 0xAA, 0xA6, 0xBB, 0xF7, 0x2A, 0x51, 0xBE, 0x03,
    0xF4, 0x2A, 0x51, 0xBE, 0x03, 0xF4, 0x2A, 0x51, 0xBE, 0x12, 0x06, 0x56, 0x27, 0x32, 0x32, 0x36,
    0x32, 0xB2, 0x1A, 0x3B, 0xBC, 0x91, 0xD4, 0x7B, 0x58, 0xFC, 0x0B, 0x55, 0x2A, 0x15, 0xBC, 0x40,
    0x92, 0x0B, 0x5B, 0x7C, 0x0A, 0x95, 0x12, 0x35, 0xB8, 0x63, 0xD2, 0x0B, 0x3B, 0xF0, 0xC7, 0x14,
    0x51, 0x5C, 0x94, 0x86, 0x94, 0x59, 0x5C, 0xFC, 0x1B, 0x17, 0x3A, 0x3F, 0x6B, 0x37, 0x32, 0x32,
    0x30, 0x32, 0x72, 0x7A, 0x13, 0xB7, 0x26, 0x60, 0x7A, 0x13, 0xB7, 0x26, 0x50, 0xBA, 0x13, 0xB4,
    0x2A, 0x50, 0xBA, 0x13, 0xB5, 0x2E, 0x40, 0xFA, 0x13, 0x95, 0xAE, 0x40, 0x38, 0x18, 0x9A, 0x92,
    0xB0, 0x38, 0x00, 0xFA, 0x12, 0xB1, 0x7E, 0x00, 0xDB, 0x96, 0xA1, 0x7C, 0x08, 0xDB, 0x9A, 0x91,
    0xBC, 0x08, 0xD8, 0x1A, 0x86, 0xE2, 0x70, 0x39, 0x1F, 0x86, 0xE0, 0x78, 0x7E, 0x03, 0xE7, 0x64,
    0x51, 0x9C, 0x8F, 0x34, 0x6F, 0x4E, 0x41, 0xFC, 0x0B, 0xD5, 0xAE, 0x41, 0xFC, 0x0B, 0xD5, 0xAE,
    0x41, 0xFC, 0x3B, 0x70, 0x71, 0x64, 0x33, 0x32, 0x12, 0x32, 0x32, 0x36, 0x70, 0x34, 0x2B, 0x56,
    0x22, 0x70, 0x3A, 0x13, 0xB7, 0x26, 0x60, 0xBA, 0x1B, 0x94, 0xAA, 0x40, 0x38, 0x00, 0xFA, 0xB2,
    0xE2, 0xA2, 0x67, 0x32, 0x32, 0x12, 0x32, 0xB2, 0x32, 0x32, 0x32, 0x32, 0x75, 0xA3, 0x26, 0x7B,
    0x83, 0x26, 0xF9, 0x83, 0x2E, 0xFF, 0xE3, 0x16, 0x7D, 0xC0, 0x1E, 0x63, 0x21, 0x07, 0xE3, 0x01,
};

// Checks one stream block (stream header at b[0]) and returns the Ogg layout. Works on either
// byte order. For the 360 files: header XOR byte applies to the Vorbis header pages only.
struct OggInfo {
  uint32_t codec = 0, stream_size = 0, extradata = 0, aux = 0;
  size_t mini = 0, seek = 0, vorb = 0;  // offsets/sizes inside the block
  uint8_t version = 0, xorb = 0;
};
static bool check_stream(const uint8_t* b, size_t len, bool be, OggInfo& o, std::string& why) {
  if (len < 0x20) { why = "stream header past the end"; return false; }
  o.stream_size = rd32(b, be);
  o.codec = rd32(b + 0x0C, be);
  o.extradata = rd32(b + 0x18, be);
  o.aux = rd32(b + 0x1C, be);
  if (o.codec == 0xFFFFFFFF) return true;  // dummy entry
  if (o.codec != 6) { why = "codec " + std::to_string(o.codec) + " (not Ogg)"; return false; }
  if (0x20ull + o.extradata + o.stream_size > len) { why = "stream data past the end"; return false; }
  size_t p = 0x20;
  if (o.aux) {
    if (o.aux != 1 || memcmp(b + p, "MARK", 4)) { why = "unknown aux chunk"; return false; }
    uint32_t asz = rd32(b + p + 4, be);
    if (asz < 8 || asz % 4 || p + asz + 0x20 > len) { why = "MARK chunk size"; return false; }
    p += asz;
  }
  o.mini = p;
  o.version = b[p];
  o.xorb = b[p + 2];
  if (o.version != 2 && o.version != 3) { why = "Ogg header version " + std::to_string(o.version); return false; }
  o.seek = rd32(b + p + 0x10, be);
  o.vorb = rd32(b + p + 0x14, be);
  if ((p - 0x20) + 0x20 + o.seek + o.vorb != o.extradata) { why = "Ogg header sizes"; return false; }
  size_t vstart = p + 0x20 + o.seek;
  const uint8_t* v = b + vstart;
  uint8_t x = o.version == 2 ? o.xorb : 0;
  if (o.version == 2 && ((v[0] ^ x) != 'O' || (v[1] ^ x) != 'g' || (v[2] ^ x) != 'g' || (v[3] ^ x) != 'S')) {
    why = "Vorbis header does not decode to an Ogg page";
    return false;
  }
  if (o.version == 2 && o.stream_size >= 4 && memcmp(v + o.vorb, "OggS", 4)) {
    why = "audio data does not start with an Ogg page";
    return false;
  }
  return true;
}

// Byte-swaps a little-endian stream block (header, MARK chunk, Ogg header, seek table) to
// big-endian in place; turns a version 3 (table XOR) Ogg stream into a version 2 one.
static bool block_to_be(uint8_t* b, size_t len, std::string& why) {
  OggInfo o;
  if (!check_stream(b, len, false, o, why)) return false;
  for (int i = 0; i < 8; i++) swap32(b + 4 * i);
  if (o.codec == 0xFFFFFFFF) return true;
  if (o.aux)
    for (size_t p = 0x24, end = 0x20 + rd32(b + 0x24, true); p < end; p += 4) swap32(b + p);
  // version 3 decryption works on the little-endian sizes read above
  size_t vstart = o.mini + 0x20 + o.seek;
  if (o.version == 3) {
    uint8_t key = uint8_t(o.stream_size & 0xFF);
    uint8_t b1 = key & 0x7F, b2 = key & 0x3F;
    for (size_t i = 0; i < o.vorb + o.stream_size; i++)
      b[vstart + i] ^= kOggV3Table[(b2 + i) & 0xFF] ^ b1;
    uint8_t x = 0x1E;  // what the 360 files use
    for (size_t i = 0; i < o.vorb; i++) b[vstart + i] ^= x;
    b[o.mini] = 2;
    b[o.mini + 2] = x;
  }
  for (int i = 1; i < 8; i++) swap32(b + o.mini + 4 * i);
  for (size_t i = 0; i + 4 <= o.seek; i += 4) swap32(b + o.mini + 0x20 + i);
  OggInfo check;
  if (!check_stream(b, len, true, check, why)) { why = "after conversion: " + why; return false; }
  return true;
}

// ---------------------------------------------------------------- MS ADPCM -> Ogg Vorbis
// Most Steam voices are MS ADPCM (codec 12), which the 360 sound driver does not play. They
// are decoded here and re-encoded to Ogg Vorbis by ffmpeg (g_ffmpeg), then wrapped the way
// the 360 files are: Ogg header (version 2, header pages XOR'ed with one byte), a seek table
// with one entry per 0.1 s (byte offset of the page holding that sample), and the audio pages.

static bool g_lipsync = false;  // --lipsync: also the Japanese lip-sync / facial motion (off: it broke Serah)
static std::string g_ffmpeg;  // set from the command line; empty: MS ADPCM files stay English
static fs::path g_tmp;
static std::atomic<unsigned> g_tmp_counter{0};

// --cache <dir>: what each ffmpeg encode made, by its input and settings (128-bit FNV-1a), so a
// package converted again re-encodes only lines it has not seen (empty: no cache)
static fs::path g_cache;
struct CacheKey {
  uint64_t a = 0xcbf29ce484222325ull, b = 0x84222325cbf29ce4ull;
  void add(const void* p, size_t n) {
    const uint8_t* d = static_cast<const uint8_t*>(p);
    for (size_t i = 0; i < n; i++) {
      a = (a ^ d[i]) * 0x100000001b3ull;
      b = (b ^ d[i]) * 0x100000001b3ull + 0x9e3779b97f4a7c15ull;
    }
  }
  void add(const std::string& s) { add(s.data(), s.size()); add("\0", 1); }
  fs::path file(const char* kind) const {
    char h[40];
    snprintf(h, sizeof h, "%016llx%016llx", (unsigned long long)a, (unsigned long long)b);
    return g_cache / (std::string(kind) + h + ".bin");
  }
};
// a cached result: 4 bytes sample rate (little-endian), then the Ogg stream
static bool cache_get(const fs::path& f, Bytes& ogg, int& sr) {
  if (g_cache.empty()) return false;
  std::error_code ec;
  if (!fs::exists(f, ec)) return false;
  Bytes d = read_file(f);
  if (d.size() < 4 + 4 || memcmp(&d[4], "OggS", 4)) return false;
  sr = int(d[0] | d[1] << 8 | d[2] << 16 | uint32_t(d[3]) << 24);
  ogg.assign(d.begin() + 4, d.end());
  return true;
}
static void cache_put(const fs::path& f, const Bytes& ogg, int sr) {
  if (g_cache.empty() || ogg.empty()) return;
  Bytes d(4 + ogg.size());
  d[0] = uint8_t(sr); d[1] = uint8_t(sr >> 8); d[2] = uint8_t(sr >> 16); d[3] = uint8_t(uint32_t(sr) >> 24);
  memcpy(&d[4], ogg.data(), ogg.size());
  fs::path t = f;
  t += "." + std::to_string(g_tmp_counter++) + ".part";
  std::error_code ec;
  write_file(t, d);
  fs::rename(t, f, ec);
  if (ec) fs::remove(t, ec);
}

static bool decode_msadpcm(const uint8_t* d, size_t n, int ch, int align, const int16_t* coef, int ncoef,
                           std::vector<int16_t>& out) {
  static const int adapt[16] = {230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230};
  if (ch < 1 || ch > 2 || align < 7 * ch) return false;
  auto s16 = [](const uint8_t* p) { return int16_t(p[0] | p[1] << 8); };
  for (size_t pos = 0; pos + 7 * ch <= n; pos += align) {
    const uint8_t* b = d + pos;
    size_t len = std::min<size_t>(align, n - pos);
    int pred[2], delta[2], s1[2], s2[2];
    for (int c = 0; c < ch; c++) {
      pred[c] = b[c];
      if (pred[c] >= ncoef) return false;
      delta[c] = s16(b + ch + 2 * c);
      s1[c] = s16(b + 3 * ch + 2 * c);
      s2[c] = s16(b + 5 * ch + 2 * c);
    }
    for (int c = 0; c < ch; c++) out.push_back(int16_t(s2[c]));
    for (int c = 0; c < ch; c++) out.push_back(int16_t(s1[c]));
    int c = 0;
    for (size_t i = 7 * ch; i < len; i++) {
      for (int half = 0; half < 2; half++) {
        int nib = half == 0 ? b[i] >> 4 : b[i] & 15;
        int snib = nib >= 8 ? nib - 16 : nib;
        int predicted = (s1[c] * coef[2 * pred[c]] + s2[c] * coef[2 * pred[c] + 1]) / 256;
        int pcm = std::clamp(predicted + snib * delta[c], -32768, 32767);
        s2[c] = s1[c];
        s1[c] = pcm;
        delta[c] = std::max(16, (adapt[nib] * delta[c]) / 256);
        out.push_back(int16_t(pcm));
        c = (c + 1) % ch;
      }
    }
  }
  return true;
}

// Runs ffmpeg with the given arguments and waits; returns its exit code (-1: could not start).
static int run_ffmpeg(const std::vector<std::string>& args) {
#if defined(_WIN32)
  std::wstring cmd = L"\"" + fs::path(g_ffmpeg).wstring() + L"\"";
  for (auto& a : args) cmd += L" \"" + fs::path(a).wstring() + L"\"";
  STARTUPINFOW si{};
  si.cb = sizeof(si);
  PROCESS_INFORMATION pi{};
  if (!CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi))
    return -1;
  WaitForSingleObject(pi.hProcess, INFINITE);
  DWORD code = 1;
  GetExitCodeProcess(pi.hProcess, &code);
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  return int(code);
#else
  std::string cmd = "'" + g_ffmpeg + "'";
  for (auto& a : args) cmd += " '" + a + "'";
  return std::system(cmd.c_str());
#endif
}

// The 360 voices are 48000 Hz Ogg streams with serial number 0 and pages of about half a second
// (the game follows a voice's playback by them; lip-sync stayed still with Steam's 48011 Hz, one
// big page, and a random serial). The output is made the same way: 48011 Hz is resampled to 48000.
static bool encode_ogg(const std::vector<int16_t>& pcm, int ch, int& sr, Bytes& ogg, std::string& why) {
  if (g_ffmpeg.empty()) { why = "MS ADPCM voice (needs ffmpeg)"; return false; }
  CacheKey key;
  key.add(std::string("encode 1"));
  key.add(&ch, sizeof ch); key.add(&sr, sizeof sr);
  key.add(pcm.data(), pcm.size() * 2);
  const fs::path cached = key.file("e");
  { int csr = 0; if (cache_get(cached, ogg, csr)) { sr = csr; return true; } }
  unsigned id = g_tmp_counter++;
  fs::path wav = g_tmp / ("v" + std::to_string(id) + ".wav"), oggp = g_tmp / ("v" + std::to_string(id) + ".ogg");
  {
    Bytes w(44);
    uint32_t data = uint32_t(pcm.size() * 2);
    memcpy(&w[0], "RIFF", 4); wr32(&w[4], 36 + data, false); memcpy(&w[8], "WAVEfmt ", 8);
    wr32(&w[16], 16, false); wr16(&w[20], 1, false); wr16(&w[22], uint16_t(ch), false); wr32(&w[24], sr, false);
    wr32(&w[28], sr * ch * 2, false); wr16(&w[32], uint16_t(ch * 2), false); wr16(&w[34], 16, false);
    memcpy(&w[36], "data", 4); wr32(&w[40], data, false);
    w.resize(44 + data);
    for (size_t i = 0; i < pcm.size(); i++) wr16(&w[44 + 2 * i], uint16_t(pcm[i]), false);
    write_file(wav, w);
  }
  const int out_sr = (sr > 47500 && sr < 48500) ? 48000 : sr;
  int rc = run_ffmpeg({"-v", "error", "-nostdin", "-y", "-i", wav.string(), "-ar", std::to_string(out_sr),
                       "-c:a", "libvorbis", "-q:a", "4", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                       "-page_duration", "500000", oggp.string()});
  std::error_code ec;
  fs::remove(wav, ec);
  if (rc != 0 || !fs::exists(oggp)) { why = "ffmpeg could not encode Ogg Vorbis (code " + std::to_string(rc) + ")"; fs::remove(oggp, ec); return false; }
  ogg = read_file(oggp);
  fs::remove(oggp, ec);
  sr = out_sr;
  cache_put(cached, ogg, sr);
  return true;
}

// Ogg page CRC (polynomial 0x04C11DB7, no reflection, as libogg).
static uint32_t ogg_crc(const uint8_t* d, size_t n) {
  static uint32_t table[256];
  static bool made = false;
  if (!made) {
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t r = i << 24;
      for (int k = 0; k < 8; k++) r = (r & 0x80000000u) ? (r << 1) ^ 0x04C11DB7u : r << 1;
      table[i] = r;
    }
    made = true;
  }
  uint32_t crc = 0;
  for (size_t i = 0; i < n; i++) crc = (crc << 8) ^ table[((crc >> 24) ^ d[i]) & 0xFF];
  return crc;
}

// Serial number 0 on every page, as in the 360 files (CRCs made again).
static void ogg_zero_serial(Bytes& ogg) {
  for (size_t q = 0; q + 27 <= ogg.size() && !memcmp(&ogg[q], "OggS", 4);) {
    int nseg = ogg[q + 26];
    if (q + 27 + nseg > ogg.size()) return;
    size_t body = 0;
    for (int k = 0; k < nseg; k++) body += ogg[q + 27 + k];
    size_t size = 27 + nseg + body;
    if (q + size > ogg.size()) return;
    memset(&ogg[q + 14], 0, 4);
    memset(&ogg[q + 22], 0, 4);
    uint32_t c = ogg_crc(&ogg[q], size);
    wr32(&ogg[q + 22], c, false);
    q += size;
  }
}

// Builds a big-endian 360 Ogg stream block (stream header + Ogg header + seek table + data).
struct OggLoop {
  uint32_t start_byte = 0, end_byte = 0;  // stream header loop fields (bytes of Ogg data)
  uint32_t mini4 = 0, mini8 = 0, mini18 = 0;  // Ogg header fields that go with them
  uint8_t xorb = 0x1E;                         // header XOR byte (0x1E: what most 360 voices use)
};
static bool ogg_block(const Bytes& ogg, int ch, int sr, Bytes& blk, std::string& why, const OggLoop& loop = OggLoop()) {
  struct Page { size_t off, size; int64_t gran; };
  std::vector<Page> pages;
  int packets = 0;
  size_t header_end = 0;  // end of the page that completes the third (setup) header packet
  for (size_t q = 0; q + 27 <= ogg.size();) {
    if (memcmp(&ogg[q], "OggS", 4)) { why = "encoder output is not Ogg"; return false; }
    int nseg = ogg[q + 26];
    if (q + 27 + nseg > ogg.size()) { why = "truncated Ogg page"; return false; }
    size_t body = 0;
    for (int k = 0; k < nseg; k++) {
      body += ogg[q + 27 + k];
      if (ogg[q + 27 + k] < 255 && packets < 3) packets++;
    }
    size_t size = 27 + nseg + body;
    if (q + size > ogg.size()) { why = "truncated Ogg page"; return false; }
    int64_t gran = 0;
    for (int k = 7; k >= 0; k--) gran = gran << 8 | ogg[q + 6 + k];
    pages.push_back({q, size, gran});
    q += size;
    if (packets == 3 && !header_end) header_end = q;
  }
  if (!header_end) { why = "no Vorbis headers"; return false; }
  size_t vorb = header_end;
  Bytes data(ogg.begin() + vorb, ogg.end());
  int64_t total = pages.empty() ? 0 : pages.back().gran;
  uint32_t step = uint32_t(sr / 10);
  std::vector<uint32_t> seek;
  for (int64_t smp = 0; smp < total; smp += step) {
    uint32_t off = 0;
    for (auto& p : pages)
      if (p.off >= vorb) {
        off = uint32_t(p.off - vorb);
        if (p.gran >= smp) break;
      }
    seek.push_back(off);
  }
  uint32_t extradata = uint32_t(0x20 + seek.size() * 4 + vorb);
  blk.assign(0x20 + extradata, 0);
  uint8_t* h = blk.data();
  wr32(h + 0x00, uint32_t(data.size()), true);
  wr32(h + 0x04, ch, true);
  wr32(h + 0x08, sr, true);
  wr32(h + 0x0C, 6, true);
  wr32(h + 0x10, loop.start_byte, true);
  wr32(h + 0x14, loop.end_byte, true);
  wr32(h + 0x18, extradata, true);
  uint8_t* m = h + 0x20;
  const uint8_t x = loop.xorb ? loop.xorb : 0x1E;
  m[0] = 2; m[1] = 0x20; m[2] = x;
  wr32(m + 0x04, loop.mini4, true);
  wr32(m + 0x08, loop.mini8, true);
  wr32(m + 0x0C, 0x3DCCCCCD, true);  // 0.1 s per seek entry
  wr32(m + 0x18, loop.mini18, true);
  wr32(m + 0x10, uint32_t(seek.size() * 4), true);
  wr32(m + 0x14, uint32_t(vorb), true);
  for (size_t i = 0; i < seek.size(); i++) wr32(m + 0x20 + 4 * i, seek[i], true);
  uint8_t* v = m + 0x20 + seek.size() * 4;
  for (size_t i = 0; i < vorb; i++) v[i] = ogg[i] ^ x;
  blk.insert(blk.end(), data.begin(), data.end());
  OggInfo check;
  if (!check_stream(blk.data(), blk.size(), true, check, why)) { why = "built Ogg block: " + why; return false; }
  return true;
}

// Steam MS ADPCM stream block (little-endian) -> 360 Ogg block.
// A Japanese stream larger than the 360 stream it replaces is re-encoded to fit: an area's
// voice banks are loaded whole into fixed game memory, and larger banks ran it out (XIII-2:
// allocations failed while an area loaded - Serah missing, then a crash). budget = bytes the
// Ogg data may take; 0 = no limit. Looping streams keep their data (loop points are byte
// positions in it).
static std::atomic<unsigned> g_shrunk{0}, g_over{0};
static bool fit_ogg(Bytes& ogg, int ch, int& sr, size_t budget, const OggLoop& loop) {
  if (!budget || ogg.size() <= budget || g_ffmpeg.empty() || loop.end_byte) {
    if (budget && ogg.size() > budget) g_over++;
    return true;
  }
  CacheKey key;
  key.add(std::string("fit 1"));
  key.add(&ch, sizeof ch); key.add(&sr, sizeof sr);
  uint64_t b64 = budget;
  key.add(&b64, sizeof b64);
  key.add(ogg.data(), ogg.size());
  const fs::path cached = key.file("f");
  {
    Bytes c; int csr = 0;
    if (cache_get(cached, c, csr)) {
      if (c.size() > budget) g_over++; else g_shrunk++;
      if (c.size() < ogg.size()) { ogg.swap(c); sr = csr; }
      return true;
    }
  }
  unsigned id = g_tmp_counter++;
  fs::path in = g_tmp / ("s" + std::to_string(id) + ".ogg"), wav = g_tmp / ("s" + std::to_string(id) + ".wav"),
           outp = g_tmp / ("s" + std::to_string(id) + "o.ogg");
  std::error_code ec;
  write_file(in, ogg);
  int rc = run_ffmpeg({"-v", "error", "-nostdin", "-y", "-i", in.string(), "-c:a", "pcm_s16le", wav.string()});
  fs::remove(in, ec);
  if (rc != 0 || !fs::exists(wav)) { g_over++; fs::remove(wav, ec); return true; }
  double seconds = double(fs::file_size(wav, ec) > 44 ? fs::file_size(wav, ec) - 44 : 0) / (2.0 * ch * sr);
  // libvorbis takes no less than about 32 kbps per channel at 48000 Hz; below that the stream
  // is made at 24000 Hz, which goes down to 16 kbps per channel. Lines that still do not fit
  // (short ones: the Vorbis headers take about 4 KB of the budget, and a Japanese take is often
  // longer than the English one) get their trailing silence cut and go to 16000, 11025, then 8000
  // Hz (libvorbis has no bitrate below 16 kbps; at these rates quality 0 is its smallest).
  bool fitted = false;
  Bytes best;
  int best_sr = sr;
  for (int attempt = 0; attempt < 8 && seconds > 0.05 && !fitted; attempt++) {
    double payload = double(budget > 4608 ? budget - 4608 : budget / 2);
    long br = long(payload * 8 / seconds * (0.92 - 0.12 * attempt));
    int rate = br >= 40000L * ch ? sr : 24000;
    if (attempt == 5) rate = std::min(sr, 16000);
    if (attempt == 6) rate = std::min(sr, 11025);
    if (attempt == 7) rate = std::min(sr, 8000);
    br = std::max(br, 16000L * ch);
    std::vector<std::string> args = {"-v", "error", "-nostdin", "-y", "-i", wav.string()};
    if (attempt >= 5)
      args.insert(args.end(), {"-af", "areverse,silenceremove=start_periods=1:start_threshold=-55dB:start_silence=0.05,areverse"});
    args.insert(args.end(), {"-ar", std::to_string(rate), "-c:a", "libvorbis"});
    if (attempt < 4) args.insert(args.end(), {"-b:a", std::to_string(br)});
    else args.insert(args.end(), {"-q:a", attempt == 4 ? "-1" : "0"});
    args.insert(args.end(), {"-fflags", "+bitexact", "-flags:a", "+bitexact", "-page_duration", "500000", outp.string()});
    rc = run_ffmpeg(args);
    if (rc != 0 || !fs::exists(outp)) continue;
    Bytes o = read_file(outp);
    if (best.empty() || o.size() < best.size()) {
      best.swap(o);
      best_sr = rate;
    }
    fitted = best.size() <= budget;
  }
  if (!best.empty() && best.size() < ogg.size()) {
    ogg_zero_serial(best);
    ogg.swap(best);
    sr = best_sr;
  }
  cache_put(cached, ogg, sr);       // (what this line ends as: fitted or not)
  (fitted ? g_shrunk : g_over)++;
  fs::remove(wav, ec);
  fs::remove(outp, ec);
  return true;
}

static bool msadpcm_block_to_ogg(const uint8_t* b, size_t len, Bytes& out, std::string& why, size_t budget = 0) {
  uint32_t ss = rd32(b, false), ch = rd32(b + 4, false), sr = rd32(b + 8, false);
  uint32_t ex = rd32(b + 0x18, false), aux = rd32(b + 0x1C, false);
  size_t p = 0x20;
  if (aux) {
    if (aux != 1 || memcmp(b + p, "MARK", 4)) { why = "unknown aux chunk"; return false; }
    p += rd32(b + p + 4, false);
  }
  if (0x20ull + ex + ss > len || p + 22 > 0x20ull + ex) { why = "MS ADPCM stream past the end"; return false; }
  const uint8_t* f = b + p;  // WAVEFORMATEX
  int align = rd16(f + 12, false), ncoef = rd16(f + 20, false);
  if (ncoef < 1 || ncoef > 32 || p + 22 + 4ull * ncoef > 0x20ull + ex) { why = "MS ADPCM format block"; return false; }
  std::vector<int16_t> coef(2 * ncoef);
  for (int i = 0; i < 2 * ncoef; i++) coef[i] = int16_t(rd16(f + 22 + 2 * i, false));
  std::vector<int16_t> pcm;
  if (!decode_msadpcm(b + 0x20 + ex, ss, int(ch), align, coef.data(), ncoef, pcm)) { why = "MS ADPCM decode"; return false; }
  Bytes ogg;
  int rate = int(sr);
  if (!encode_ogg(pcm, int(ch), rate, ogg, why)) return false;
  ogg_zero_serial(ogg);
  if (!ogg_block(ogg, int(ch), rate, out, why)) return false;
  if (budget && out.size() > budget) {
    fit_ogg(ogg, int(ch), rate, budget > out.size() - ogg.size() ? budget - (out.size() - ogg.size()) : 1, OggLoop());
    return ogg_block(ogg, int(ch), rate, out, why);
  }
  return true;
}

// Steam Ogg stream block (little-endian) -> 360 Ogg block built the same way as the
// re-encoded ones (plain Ogg pages extracted first, then wrapped again).
static bool ogg_block_rewrap(const uint8_t* b, size_t len, Bytes& out, std::string& why, size_t budget = 0) {
  OggInfo o;
  if (!check_stream(b, len, false, o, why)) return false;
  if (o.codec == 0xFFFFFFFF) { why = "dummy stream"; return false; }
  size_t vstart = o.mini + 0x20 + o.seek;
  Bytes ogg(b + vstart, b + vstart + o.vorb + o.stream_size);
  if (o.version == 3) {
    uint8_t key = uint8_t(o.stream_size & 0xFF), b1 = key & 0x7F, b2 = key & 0x3F;
    for (size_t i = 0; i < ogg.size(); i++) ogg[i] ^= kOggV3Table[(b2 + i) & 0xFF] ^ b1;
  } else {
    for (size_t i = 0; i < o.vorb; i++) ogg[i] ^= o.xorb;
  }
  if (ogg.size() < 4 || memcmp(ogg.data(), "OggS", 4)) { why = "Ogg data did not decode"; return false; }
  ogg_zero_serial(ogg);
  OggLoop loop;
  loop.start_byte = rd32(b + 0x10, false);
  loop.end_byte = rd32(b + 0x14, false);
  loop.mini4 = rd32(b + o.mini + 4, false);
  loop.mini8 = rd32(b + o.mini + 8, false);
  loop.mini18 = rd32(b + o.mini + 0x18, false);
  if (o.version == 2) loop.xorb = o.xorb;
  const int ch = int(rd32(b + 4, false));
  int sr = int(rd32(b + 8, false));
  if (!ogg_block(ogg, ch, sr, out, why, loop)) return false;
  if (budget && out.size() > budget) {
    fit_ogg(ogg, ch, sr, budget > out.size() - ogg.size() ? budget - (out.size() - ogg.size()) : 1, loop);
    return ogg_block(ogg, ch, sr, out, why, loop);
  }
  return true;
}

// us: 360 English file (big-endian); jp: Steam Japanese file (little-endian, or big-endian)
static bool srd_to_x360(Bytes& b);
static std::atomic<unsigned> g_voice_lip{0};  // converted voices that carry their lip-sync block

// Lip-sync of a voice line, after the SCD data (size at +0x10, 64-bit): an SRD block and a
// 16-byte footer "add srd", 0, u32 SRD size. Every Steam Japanese voice file has one; the 360
// field lines have one in big-endian (the game takes the lip-sync of a line from it, by name).
static bool scd_lip_block(const Bytes& d, bool be, size_t& body, Bytes& srd) {
  srd.clear();
  uint64_t size = be ? (uint64_t(rd32(&d[0x10], true)) << 32 | rd32(&d[0x14], true))
                     : (uint64_t(rd32(&d[0x14], false)) << 32 | rd32(&d[0x10], false));
  body = d.size();
  if (size < 0x50 || size >= d.size()) return false;
  if (d.size() - size < 32 || memcmp(&d[size], "SRD", 3) || memcmp(&d[d.size() - 16], "add srd", 8)) return false;
  uint32_t n = rd32(&d[size + 4], be);
  if (n < 18 || size + n + 16 != d.size() || rd32(&d[d.size() - 4], be) != n) return false;
  body = size_t(size);
  srd.assign(d.begin() + size, d.begin() + size + n);
  return true;
}

static bool convert_scd(const Bytes& us, const Bytes& jp, Bytes& out, std::string& why) {
  Scd u, j;
  if (!parse_scd(us, u, why)) { why = "360 file: " + why; return false; }
  if (!parse_scd(jp, j, why)) { why = "Steam file: " + why; return false; }
  if (!u.be) { why = "360 file is not big-endian"; return false; }
  if (u.metas.size() != j.metas.size()) {
    why = "stream count " + std::to_string(u.metas.size()) + " vs " + std::to_string(j.metas.size());
    return false;
  }
  if (u.metas.empty()) { out = us; return true; }  // empty bank
  uint32_t u0 = *std::min_element(u.metas.begin(), u.metas.end());
  for (size_t i = 0; i < 6; i++) {
    uint32_t t = rd32(&us[u.tables + 8 + 4 * i], true);
    if (i != 3 && i != 5 && t > u0) { why = "360 table after stream data"; return false; }
  }
  for (size_t i = 1; i < j.metas.size(); i++)
    if (j.metas[i] <= j.metas[i - 1]) { why = "Steam streams out of order"; return false; }
  size_t jbody;
  Bytes srd;
  const bool lip = scd_lip_block(jp, j.be, jbody, srd);
  if (lip && !j.be && !srd_to_x360(srd)) { why = "Steam lip-sync block"; return false; }
  // Each 360 stream's size (stream data runs from its header to the next one, the last to the
  // end of the stream area) is the budget of the Japanese stream in its place.
  std::vector<uint32_t> sorted(u.metas.begin(), u.metas.end());
  std::sort(sorted.begin(), sorted.end());
  size_t ubody = us.size();
  {
    Bytes srd_u;
    size_t b2 = 0;
    if (scd_lip_block(us, true, b2, srd_u)) ubody = b2;
  }
  auto budget_of = [&](size_t i) -> size_t {
    auto it = std::upper_bound(sorted.begin(), sorted.end(), u.metas[i]);
    size_t end = it == sorted.end() ? ubody : *it;
    return end > u.metas[i] ? end - u.metas[i] : 0;
  };
  out.assign(us.begin(), us.begin() + u0);
  for (size_t i = 0; i < j.metas.size(); i++) {
    size_t start = j.metas[i];
    size_t end = i + 1 < j.metas.size() ? j.metas[i + 1] : jbody;
    Bytes blk(jp.begin() + start, jp.begin() + end);
    uint32_t codec = rd32(&blk[0x0C], j.be);
    if (!j.be && codec == 12) {
      Bytes conv;
      if (!msadpcm_block_to_ogg(blk.data(), blk.size(), conv, why, budget_of(i))) { why = "Steam stream: " + why; return false; }
      blk.swap(conv);
    } else if (!j.be && codec == 6) {
      Bytes conv;
      if (!ogg_block_rewrap(blk.data(), blk.size(), conv, why, budget_of(i))) { why = "Steam stream: " + why; return false; }
      blk.swap(conv);
    } else if (j.be) {
      OggInfo o;
      if (!check_stream(blk.data(), blk.size(), true, o, why)) { why = "Steam stream: " + why; return false; }
    } else if (!block_to_be(blk.data(), blk.size(), why)) {
      why = "Steam stream: " + why;
      return false;
    }
    out.resize(align_up(out.size(), 16), 0);
    wr32(&out[u.headers_offset + 4 * i], uint32_t(out.size()), true);
    out.insert(out.end(), blk.begin(), blk.end());
  }
  out.resize(align_up(out.size(), 128), 0);  // the 360 files are padded to 128 bytes
  wr32(&out[0x10], 0, true);  // 64-bit file size
  wr32(&out[0x14], uint32_t(out.size()), true);
  if (lip) {
    g_voice_lip++;
    out.insert(out.end(), srd.begin(), srd.end());
    static const char tag[8] = {'a', 'd', 'd', ' ', 's', 'r', 'd', 0};
    out.insert(out.end(), tag, tag + 8);
    out.resize(out.size() + 8, 0);
    wr32(&out[out.size() - 4], uint32_t(srd.size()), true);
  }
  return true;
}

// ---------------------------------------------------------------- WPD

struct WpdEntry {
  std::string name, ext;
  uint32_t off = 0, size = 0;
};
static bool parse_wpd(const Bytes& d, bool& be, std::vector<WpdEntry>& ents) {
  if (d.size() < 16 || memcmp(d.data(), "WPD", 3)) return false;
  for (int pass = 0; pass < 2; pass++) {
    be = pass == 0;
    uint32_t n = rd32(&d[4], be);
    if (16ull + 32ull * n > d.size()) continue;
    ents.clear();
    bool ok = true;
    for (uint32_t i = 0; i < n && ok; i++) {
      const uint8_t* e = &d[16 + 32 * i];
      WpdEntry w;
      w.name.assign((const char*)e, strnlen((const char*)e, 16));
      w.off = rd32(e + 16, be);
      w.size = rd32(e + 20, be);
      w.ext.assign((const char*)e + 24, strnlen((const char*)e + 24, 8));
      if (uint64_t(w.off) + w.size > d.size()) ok = false;
      ents.push_back(w);
    }
    if (ok) return true;
  }
  return false;
}

struct WpdStats {
  int members = 0, converted = 0, kept = 0;
  std::string first_problem;
};
static bool convert_wpd(const Bytes& us, const Bytes& jp, Bytes& out, std::string& why, WpdStats& st) {
  bool ube, jbe;
  std::vector<WpdEntry> ue, je;
  if (!parse_wpd(us, ube, ue) || !ube) { why = "360 file is not a big-endian WPD"; return false; }
  if (!parse_wpd(jp, jbe, je)) { why = "Steam file is not a WPD"; return false; }
  std::map<std::string, const WpdEntry*> jmap;
  for (auto& e : je) jmap[e.name + "." + e.ext] = &e;
  size_t align = 2048;
  for (auto& e : ue)
    while (align > 16 && e.off % align) align >>= 1;
  out.assign(us.begin(), us.begin() + 16 + 32 * ue.size());
  size_t pos = ue.empty() ? out.size() : ue[0].off;
  for (size_t i = 0; i < ue.size(); i++) {
    const WpdEntry& e = ue[i];
    Bytes member(us.begin() + e.off, us.begin() + e.off + e.size);
    st.members++;
    auto it = jmap.find(e.name + "." + e.ext);
    if (it != jmap.end() && e.ext == "scd") {
      Bytes jm(jp.begin() + it->second->off, jp.begin() + it->second->off + it->second->size), conv;
      std::string w;
      if (convert_scd(member, jm, conv, w)) {
        member.swap(conv);
        st.converted++;
      } else {
        st.kept++;
        if (st.first_problem.empty()) st.first_problem = e.name + ": " + w;
      }
    } else {
      st.kept++;
    }
    pos = std::max(pos, align_up(out.size(), align));
    out.resize(pos, 0);
    wr32(&out[16 + 32 * i + 16], uint32_t(pos), true);
    wr32(&out[16 + 32 * i + 20], uint32_t(member.size()), true);
    out.insert(out.end(), member.begin(), member.end());
    pos = out.size();
  }
  out.resize(align_up(out.size(), align), 0);
  if (!st.converted) { why = "no member converted (" + st.first_problem + ")"; return false; }
  return true;
}

// ---------------------------------------------------------------- lip-sync and facial motion
// Steam keeps a Japanese copy of the event and character motion files (set "v"), the 360
// release only the English one. Both are WPD bundles; Japanese and English differ in their
// motion members (mtb: facial and lip motion of cutscenes) and in lsdpack's srd members (lip
// shapes for battle and field lines). mtb is the same on both platforms; srd differs only in
// byte order. Built here: the members of the Japanese file, each one taken from the 360 file
// where Japanese = Steam English, else converted from Steam. A member type is converted only
// if every member the 360 and the Steam English file share converts to the 360 one.

// Steam srd -> 360: "SRD", version 0/1, u32 size at +4; records from +18 {len, n, n byte
// pairs, u16 time}, ended by a record of length 0. The last record's time keeps its byte order.
static bool srd_to_x360(Bytes& b) {
  if (b.size() < 18 || memcmp(b.data(), "SRD", 3) || b[3] > 1) return false;
  swap32(&b[4]);
  std::vector<size_t> recs;
  size_t i = 18;
  while (i < b.size() && b[i]) {
    if (b[i] < 4 || i + b[i] > b.size()) return false;
    recs.push_back(i);
    i += b[i];
  }
  for (size_t k = 0; k + 1 < recs.size(); k++) std::swap(b[recs[k] + b[recs[k]] - 2], b[recs[k] + b[recs[k]] - 1]);
  return true;
}
static bool member_to_x360(const std::string& ext, Bytes& b) { return ext == "srd" ? srd_to_x360(b) : true; }

// The 360 bundle with the Japanese lip-sync and facial motion members (mtb, srd) put in: the
// 360 file's member list and order stay as they are, and only an mtb / srd member that the
// Steam Japanese bundle has in another form than the Steam English one is replaced (converted to
// the 360 form). Everything else - scripts, cameras, the characters an event places - stays the
// 360 game's own: whole Japanese event files broke area starts (XIII-2: Serah did not appear
// after loading a save, so the player could not move).
static bool merge_jp_motion(const Bytes& x, const Bytes& en, const Bytes& jp, Bytes& out, std::string& why,
                            int& taken) {
  bool xbe, ebe, jbe;
  std::vector<WpdEntry> xe, ee, je;
  if (!parse_wpd(x, xbe, xe) || !xbe) { why = "360 file is not a big-endian WPD"; return false; }
  if (!parse_wpd(en, ebe, ee) || !parse_wpd(jp, jbe, je)) { why = "Steam file is not a WPD"; return false; }
  auto slice = [](const Bytes& d, const WpdEntry& e) { return Bytes(d.begin() + e.off, d.begin() + e.off + e.size); };
  std::map<std::string, const WpdEntry*> em, jm;
  for (auto& e : ee) em[e.name + "." + e.ext] = &e;
  for (auto& e : je) jm[e.name + "." + e.ext] = &e;
  std::vector<std::pair<const WpdEntry*, Bytes>> members;
  taken = 0;
  for (auto& e : xe) {
    std::string key = e.name + "." + e.ext;
    Bytes m = slice(x, e);
    auto ij = jm.find(key);
    auto ie = em.find(key);
    if ((e.ext == "mtb" || e.ext == "srd") && ij != jm.end() && ie != em.end()) {
      Bytes j = slice(jp, *ij->second);
      if (j != slice(en, *ie->second)) {
        if (!member_to_x360(e.ext, j)) {
          why = e.ext + " member " + e.name + " could not be converted";
          return false;
        }
        m = std::move(j);
        taken++;
      }
    }
    members.push_back({&e, std::move(m)});
  }
  if (!taken) { why = "same as English"; return false; }
  size_t align = 2048;
  for (auto& e : xe)
    while (align > 16 && e.off % align) align >>= 1;
  out.assign(16 + 32 * members.size(), 0);
  memcpy(out.data(), x.data(), 4);
  wr32(&out[4], uint32_t(members.size()), true);
  for (size_t i = 0; i < members.size(); i++) {
    const WpdEntry& e = *members[i].first;
    uint8_t* d = &out[16 + 32 * i];
    memcpy(d, e.name.data(), std::min<size_t>(16, e.name.size()));
    memcpy(d + 24, e.ext.data(), std::min<size_t>(8, e.ext.size()));
    out.resize(align_up(out.size(), align), 0);
    d = &out[16 + 32 * i];
    wr32(d + 16, uint32_t(out.size()), true);
    wr32(d + 20, uint32_t(members[i].second.size()), true);
    out.insert(out.end(), members[i].second.begin(), members[i].second.end());
  }
  out.resize(align_up(out.size(), align), 0);
  return true;
}

// ---------------------------------------------------------------- patch writer

struct PatchFile {
  uint32_t code;
  uint8_t ftype;
  std::string path;
  uint64_t sector;
  uint32_t size;
  uint32_t csize = 0;  // stored size when compressed (0: stored as it is)
};

static void write_filelist(const fs::path& p, std::vector<PatchFile> files) {
  std::sort(files.begin(), files.end(), [](const PatchFile& a, const PatchFile& b) {
    return std::tie(a.ftype, a.code) < std::tie(b.ftype, b.code);
  });
  std::vector<Bytes> chunks(1);
  struct E { uint32_t code; uint8_t ftype; unsigned chunk, off; };
  std::vector<E> ents;
  for (auto& f : files) {
    char text[1200];
    snprintf(text, sizeof(text), "%llx:%x:%x:%s", (unsigned long long)f.sector, f.size, f.csize ? f.csize : f.size,
             f.path.c_str());
    size_t n = strlen(text) + 1;
    if (chunks.back().size() + n > 8192) chunks.emplace_back();
    ents.push_back({f.code, f.ftype, unsigned(chunks.size() - 1), unsigned(chunks.back().size())});
    chunks.back().insert(chunks.back().end(), text, text + n);
  }
  const char* end = "end";
  chunks.back().insert(chunks.back().end(), end, end + 4);
  if (chunks.size() > 512) throw std::runtime_error("too many files for one patch list");
  uint32_t count = uint32_t(ents.size());
  uint32_t chunk_tab = 12 + 8 * count, chunk_data = chunk_tab + 12 * uint32_t(chunks.size());
  Bytes out(chunk_data);
  wr32(&out[0], chunk_tab, false);
  wr32(&out[4], chunk_data, false);
  wr32(&out[8], count, false);
  for (uint32_t i = 0; i < count; i++) {
    uint8_t* e = &out[12 + 8 * i];
    wr32(e, ents[i].code, false);
    wr16(e + 4, uint16_t(ents[i].off | (ents[i].chunk & 1) << 15), false);
    e[6] = uint8_t(ents[i].chunk >> 1);
    e[7] = ents[i].ftype;
  }
  uint32_t pos = 0;
  for (size_t c = 0; c < chunks.size(); c++) {
    Bytes z = zlib_stored(chunks[c]);
    uint8_t* t = &out[chunk_tab + 12 * c];
    wr32(t, uint32_t(chunks[c].size()), false);
    wr32(t + 4, uint32_t(z.size()), false);
    wr32(t + 8, pos, false);
    pos += uint32_t(z.size());
    out.insert(out.end(), z.begin(), z.end());
  }
  write_file(p, out);
}

// ---------------------------------------------------------------- main build (FFXIII-2)

static std::string lower(std::string s) {
  for (auto& c : s) c = char(tolower((unsigned char)c));
  return s;
}

// The file lists of one language set of alba_data. set: "u" (360; Steam English) or "c" (Steam
// Japanese). Steam's update archives (udp/white_patch_<set>, white_patch_scr_<set>) come first:
// they replace files of the base archives, so a file code found there wins.
static std::vector<Source> load_sources(const fs::path& alba, const std::string& set, const std::string& plat) {
  std::vector<Source> out;
  for (const char* n : {"patch_", "patch_scr_"}) {
    fs::path list = alba / "udp" / ("filelist_" + std::string(n) + set + "." + plat + ".bin");
    fs::path arc = alba / "udp" / ("white_" + std::string(n) + set + "." + plat + ".bin");
    if (!fs::exists(list) || !fs::exists(arc)) continue;
    Source s;
    s.name = "udp " + std::string(n) + set;
    s.entries = parse_filelist(read_file(list));
    s.archive = std::make_shared<Archive>(arc);
    out.push_back(s);
  }
  // filelist (white_img) and filelist_scr (white_scr: event and zone texts, field voices)
  for (const char* n : {"", "_scr"}) {
    Source s;
    s.name = std::string("filelist") + n + set;
    fs::path list = alba / "sys" / (s.name + "." + plat + ".bin");
    const bool scr = std::string(n) == "_scr";
    fs::path arc = alba / "sys" / (std::string(scr ? "white_scr" : "white_img") + set + "." + plat + ".bin");
    if (scr && (!fs::exists(list) || !fs::exists(arc))) continue;
    s.entries = parse_filelist(read_file(list));
    s.archive = std::make_shared<Archive>(arc);
    out.push_back(s);
  }
  std::vector<Source> zones;
  for (auto& s : out) {
    for (auto& e : s.entries) {
      size_t z = e.path.find("zone/filelist_z");
      if (z == std::string::npos || e.path.size() < 4 || e.path.substr(e.path.size() - 4) != ".bin") continue;
      std::string zid = e.path.substr(z + 14, 5);  // z0010
      fs::path arc = alba / "zone" / ("white_" + zid + set + "_img." + plat + ".bin");
      if (!fs::exists(arc) || fs::file_size(arc) <= 2048) continue;
      Source zs;
      zs.name = "zone " + zid;
      try {
        zs.entries = parse_filelist(s.archive->read(e));
      } catch (const std::exception& ex) {
        printf("  (zone %s: %s; skipped)\n", zid.c_str(), ex.what());
        continue;
      }
      zs.archive = std::make_shared<Archive>(arc);
      zones.push_back(zs);
    }
  }
  out.insert(out.end(), zones.begin(), zones.end());
  return out;
}

static bool is_english_voice(const std::string& p) {
  if (p.compare(0, 11, "sound/pack/")) return false;
  if (p.find("/usa/") == std::string::npos) return false;
  size_t n = p.size();
  return n > 4 && (p.compare(n - 4, 4, ".scd") == 0 || p.compare(n - 4, 4, ".wpd") == 0);
}
static std::string jp_path_for(std::string p) {
  size_t u = p.find("/usa/");
  p.erase(u, 4);
  size_t x = p.find(".x360.");
  if (x != std::string::npos) p.replace(x, 6, ".win32.");
  return p;
}

// File codes are not a cross-language identity: unrelated voice lines can share
// (ftype, code). Only the corresponding Japanese path may replace an English
// line. A missing path keeps the English audio instead of borrowing another line.
template <class Ref>
static Ref japanese_voice(const Entry& english, const std::map<std::string, Ref>& byPath) {
  if (!is_english_voice(lower(english.path))) return {};
  const auto it = byPath.find(jp_path_for(lower(english.path)));
  if (it == byPath.end() || !it->second.e || it->second.e->ftype != english.ftype) return {};
  return it->second;
}

template <class Ref>
static Ref steam_english_voice(const Entry& english, const std::map<std::string, Ref>& byPath) {
  std::string path = lower(english.path);
  if (!is_english_voice(path)) return {};
  const auto platform = path.find(".x360.");
  if (platform != std::string::npos) path.replace(platform, 6, ".win32.");
  const auto it = byPath.find(path);
  if (it == byPath.end() || !it->second.e || it->second.e->ftype != english.ftype) return {};
  return it->second;
}

// One output archive and its list: the base files' patch (white_patch_u, which the game also
// checks for the zone files) or the scenario files' patch (white_patch_scr_u).
struct PatchOut {
  fs::path arc_path, list_path, final_path;
  std::ofstream arc;
  std::vector<PatchFile> files;
  uint64_t written = 0;
  void add(const Entry& e, const Bytes& data) {
    files.push_back(PatchFile{e.code, e.ftype, e.path, written / 2048, uint32_t(data.size())});
    arc.write((const char*)data.data(), data.size());
    written += data.size();
    static const char zeros[2048] = {0};
    size_t pad = align_up(written, 2048) - written;
    arc.write(zeros, pad);
    written += pad;
  }
};

static int build(const fs::path& root360, const fs::path& rootSteam, const fs::path& outDir, const fs::path& samples) {
  fs::path a360 = root360 / "alba_data", aSteam = rootSteam / "alba_data";
  printf("Reading the 360 file lists\n");
  auto us = load_sources(a360, "u", "x360");
  printf("Reading the Steam Japanese file lists\n");
  auto jp = load_sources(aSteam, "c", "win32");
  printf("Reading the Steam English file lists\n");
  auto en = load_sources(aSteam, "u", "win32");
  for (auto* set : {&us, &jp, &en})
    for (auto& s : *set) printf("  %-18s %6zu files  %s\n", s.name.c_str(), s.entries.size(), s.archive->path.filename().string().c_str());
  fflush(stdout);
  if (jp.empty()) throw std::runtime_error("no Japanese file lists (alba_data\\sys\\filelistc.win32.bin) in " + rootSteam.string());

  struct Ref { Source* src; const Entry* e; };
  typedef std::pair<uint8_t, uint32_t> Key;
  std::map<Key, Ref> jpByCode, enByCode;
  std::map<std::string, Ref> jpByPath, enByPath;
  for (auto& s : jp)
    for (auto& e : s.entries) {
      jpByCode.emplace(Key(e.ftype, e.code), Ref{&s, &e});
      jpByPath.emplace(lower(e.path), Ref{&s, &e});
    }
  for (auto& s : en)
    for (auto& e : s.entries) {
      enByCode.emplace(Key(e.ftype, e.code), Ref{&s, &e});
      enByPath.emplace(lower(e.path), Ref{&s, &e});
    }

  fs::create_directories(outDir);
  FILE* report = fopen((outDir / "report.csv").string().c_str(), "w");
  if (!report) throw std::runtime_error("cannot write report.csv");
  fprintf(report, "path,result,detail,size_360,size_steam,size_out\n");

  // Voices: every English voice file of the 360 release with a Japanese Steam version.
  std::vector<std::pair<Ref, Ref>> jobs;
  std::set<Key> seen;
  int noJp = 0, byName = 0;
  for (auto& s : us)
    for (auto& e : s.entries) {
      if (!is_english_voice(e.path) || !seen.insert({e.ftype, e.code}).second) continue;
      Ref j = japanese_voice(e, jpByPath);
      if (j.e && j.e->code != e.code) byName++;
      if (!j.e) {
        noJp++;
        fprintf(report, "%s,kept English,no Japanese file,%u,,\n", e.path.c_str(), e.usize);
        continue;
      }
      jobs.push_back({Ref{&s, &e}, j});
    }
  printf("English voice files: %zu with a Japanese version, %d without (those stay English)\n", jobs.size(), noJp);
  if (byName) printf("  (%d path matches have a different file code)\n", byName);
  fflush(stdout);

  PatchOut outs[2];
  const char* names[2] = {"patch_u", "patch_scr_u"};
  for (int k = 0; k < 2; k++) {
    outs[k].final_path = outDir / ("white_" + std::string(names[k]) + ".x360.bin");
    outs[k].arc_path = outDir / ("white_" + std::string(names[k]) + ".x360.bin.tmp");
    outs[k].list_path = outDir / ("filelist_" + std::string(names[k]) + ".x360.bin");
    outs[k].arc.open(outs[k].arc_path, std::ios::binary);
    if (!outs[k].arc) throw std::runtime_error("cannot write " + outs[k].arc_path.string());
  }
  auto out_for = [&](const Source* s) -> PatchOut& { return s->name.rfind("filelist_scr", 0) == 0 ? outs[1] : outs[0]; };

  int ok = 0, failed = 0, samplesScd = 0, samplesWpd = 0, samplesBad = 0, done = 0;
  std::map<std::string, int> reasons;
  if (!samples.empty()) fs::create_directories(samples);
  auto save_sample = [&](const char* kind, const std::string& path, const Bytes& a, const Bytes& b, const Bytes* c) {
    std::string base = path.substr(path.rfind('/') + 1);
    base = base.substr(0, base.find('.'));
    write_file(samples / (std::string(kind) + "_" + base + ".360"), a);
    write_file(samples / (std::string(kind) + "_" + base + ".steam"), b);
    if (c) write_file(samples / (std::string(kind) + "_" + base + ".out"), *c);
  };
  // Archives are read on this thread; conversions (MS ADPCM -> Ogg runs ffmpeg) run on a
  // pool of worker threads; results are written here in whatever order they finish.
  struct Task {
    size_t job = 0;
    Bytes ub, jb, ob;
    std::string why;
    bool good = false, wpd = false;
    WpdStats st;
  };
  std::mutex mu;
  std::condition_variable cv_in, cv_out;
  std::deque<std::unique_ptr<Task>> inq, outq;
  bool closing = false;
  unsigned nworkers = std::max(2u, std::min(16u, std::thread::hardware_concurrency()));
  std::vector<std::thread> workers;
  for (unsigned w = 0; w < nworkers; w++)
    workers.emplace_back([&] {
      for (;;) {
        std::unique_ptr<Task> t;
        {
          std::unique_lock<std::mutex> lk(mu);
          cv_in.wait(lk, [&] { return closing || !inq.empty(); });
          if (inq.empty()) return;
          t = std::move(inq.front());
          inq.pop_front();
        }
        if (t->why.empty()) {
          try {
            t->good = t->wpd ? convert_wpd(t->ub, t->jb, t->ob, t->why, t->st) : convert_scd(t->ub, t->jb, t->ob, t->why);
          } catch (const std::exception& ex) {
            t->why = ex.what();
          }
        }
        std::lock_guard<std::mutex> lk(mu);
        outq.push_back(std::move(t));
        cv_out.notify_one();
      }
    });
  auto last_print = std::chrono::steady_clock::now();
  auto finish = [&](Task& t) {
    const Ref& ur = jobs[t.job].first;
    const Entry& ue = *ur.e;
    const Entry& je = *jobs[t.job].second.e;
    // A converted file may not need more memory than the 360 one (2% slack for padding): the
    // game loads voice banks into fixed pools.
    if (t.good && t.ob.size() > size_t(ue.usize) + ue.usize / 50) {
      t.good = false;
      t.why = "larger than the 360 file after re-encoding";
    }
    if (t.good) {
      ok++;
      out_for(ur.src).add(ue, t.ob);
      std::string detail = t.wpd ? (std::to_string(t.st.converted) + " of " + std::to_string(t.st.members) + " sounds") : "";
      if (t.wpd && t.st.kept && !t.st.first_problem.empty()) detail += " (" + t.st.first_problem + ")";
      for (auto& ch : detail) if (ch == ',') ch = ';';
      fprintf(report, "%s,Japanese,%s,%u,%u,%zu\n", ue.path.c_str(), detail.c_str(), ue.usize, je.usize, t.ob.size());
      if (!samples.empty() && ((!t.wpd && samplesScd < 3) || (t.wpd && samplesWpd < 1))) {
        save_sample(t.wpd ? "wpd" : "scd", ue.path, t.ub, t.jb, &t.ob);
        (t.wpd ? samplesWpd : samplesScd)++;
      }
    } else {
      failed++;
      std::string why = t.why;
      reasons[why.size() > 60 ? why.substr(0, 60) : why]++;
      for (auto& ch : why) if (ch == ',') ch = ';';
      fprintf(report, "%s,kept English,%s,%u,%u,\n", ue.path.c_str(), why.c_str(), ue.usize, je.usize);
      if (!samples.empty() && samplesBad < 4 && !t.ub.empty() && !t.jb.empty() && t.ub.size() + t.jb.size() < 8u << 20) {
        save_sample("failed", ue.path, t.ub, t.jb, nullptr);
        samplesBad++;
      }
    }
    ++done;
    if (std::chrono::steady_clock::now() - last_print > std::chrono::seconds(10)) {
      last_print = std::chrono::steady_clock::now();
      printf("  %d / %zu voice files (%.0f MB written)\n", done, jobs.size(), (outs[0].written + outs[1].written) / 1048576.0);
    }
    fflush(stdout);
  };
  size_t submitted = 0;
  for (size_t i = 0; i < jobs.size(); i++) {
    auto t = std::make_unique<Task>();
    t->job = i;
    const Entry& ue = *jobs[i].first.e;
    t->wpd = ue.path.compare(ue.path.size() - 4, 4, ".wpd") == 0;
    try {
      t->ub = jobs[i].first.src->archive->read(ue);
      t->jb = jobs[i].second.src->archive->read(*jobs[i].second.e);
      // The same bytes in Steam's English set: not a voice in another language (music, monster
      // sounds); the 360 file stays.
      const Ref english = steam_english_voice(ue, enByPath);
      if (english.e && english.e->usize == t->jb.size() &&
          english.src->archive->read(*english.e) == t->jb)
        t->why = "the same in both languages on Steam";
    } catch (const std::exception& ex) {
      t->why = ex.what();
    }
    {
      std::lock_guard<std::mutex> lk(mu);
      inq.push_back(std::move(t));
      cv_in.notify_one();
    }
    submitted++;
    // keep at most 4 tasks per worker in flight; write finished ones meanwhile
    for (;;) {
      std::unique_ptr<Task> r;
      {
        std::unique_lock<std::mutex> lk(mu);
        if (submitted - size_t(done) <= 4 * nworkers && outq.empty()) break;
        cv_out.wait(lk, [&] { return !outq.empty(); });
        r = std::move(outq.front());
        outq.pop_front();
      }
      finish(*r);
    }
  }
  while (size_t(done) < submitted) {
    std::unique_ptr<Task> r;
    {
      std::unique_lock<std::mutex> lk(mu);
      cv_out.wait(lk, [&] { return !outq.empty(); });
      r = std::move(outq.front());
      outq.pop_front();
    }
    finish(*r);
  }
  {
    std::lock_guard<std::mutex> lk(mu);
    closing = true;
  }
  cv_in.notify_all();
  for (auto& w : workers) w.join();

  // Japanese lip-sync, facial motion, and captions: every other file whose Steam Japanese and
  // Steam English versions differ (same file code; e.g. mot/.../lsdpack.bin and lsdpack_us.bin,
  // event DataSets, zone/lip/lsdpack<N>.bin, sound/pack/caption). The 360 file of that code gets
  // the Japanese version: as it is when the 360 file is the Steam English one byte for byte,
  // else merged member by member (merge_jp_motion: WPD bundles of mtb / srd members).
  int lipOk = 0, lipSame = 0, lipKept = 0;
  if (!g_lipsync) printf("Japanese lip-sync / motion: off (the 360 lip movements stay)\n");
  std::map<std::string, int> lipReasons;
  FILE* lipKeys = fopen((outDir / "jp_lipsync.txt").string().c_str(), "w");
  std::set<Key> lip_seen;
  for (auto& s : us)
    for (auto& e : s.entries) {
      if (!g_lipsync || is_english_voice(e.path) || !lip_seen.insert({e.ftype, e.code}).second) continue;
      const std::string p = lower(e.path);
      if (p.compare(0, 4, "mot/") && p.compare(0, 6, "event/") && p.compare(0, 9, "zone/lip/") &&
          p.compare(0, 19, "sound/pack/caption/"))
        continue;
      auto ij = jpByCode.find({e.ftype, e.code});
      auto ie = enByCode.find({e.ftype, e.code});
      if (ij == jpByCode.end() || ie == enByCode.end()) continue;
      Bytes jb, eb, xb, ob;
      int taken = 0;
      try {
        if (ij->second.e->usize == ie->second.e->usize) {
          jb = ij->second.src->archive->read(*ij->second.e);
          eb = ie->second.src->archive->read(*ie->second.e);
          if (jb == eb) { lipSame++; continue; }
        } else {
          jb = ij->second.src->archive->read(*ij->second.e);
          eb = ie->second.src->archive->read(*ie->second.e);
        }
        xb = s.archive->read(e);
        bool wpd_x = false;
        std::vector<WpdEntry> probe;
        wpd_x = parse_wpd(xb, wpd_x, probe);
        // Plain (non-bundle) files are taken whole only where they hold nothing but lip-sync or
        // captions; bundles get their mtb / srd members only.
        const bool lip_only = !p.compare(0, 9, "zone/lip/") || !p.compare(0, 19, "sound/pack/caption/") ||
                              (!p.compare(0, 4, "mot/") && p.find("lsdpack") != std::string::npos);
        if (!wpd_x) {
          if (!lip_only || xb != eb) throw std::runtime_error("not a bundle; kept the 360 file");
          ob = jb;
          taken = 1;
        } else {
          std::string why;
          if (!merge_jp_motion(xb, eb, jb, ob, why, taken)) {
            if (why == "same as English") { lipSame++; continue; }
            throw std::runtime_error(why);
          }
        }
      } catch (const std::exception& ex) {
        lipKept++;
        std::string r = ex.what();
        lipReasons[r.size() > 60 ? r.substr(0, 60) : r]++;
        for (auto& ch : r) if (ch == ',') ch = ';';
        fprintf(report, "%s,kept English lip-sync,%s,%u,%u,\n", e.path.c_str(), r.c_str(), e.usize, ij->second.e->usize);
        continue;
      }
      out_for(&s).add(e, ob);
      if (lipKeys) fprintf(lipKeys, "%llx %s\n", (unsigned long long)e.ftype << 32 | e.code, e.path.c_str());
      lipOk++;
      fprintf(report, "%s,Japanese lip-sync,%d member(s),%u,%u,%zu\n", e.path.c_str(), taken, e.usize,
              ij->second.e->usize, ob.size());
      if (std::chrono::steady_clock::now() - last_print > std::chrono::seconds(10)) {
        last_print = std::chrono::steady_clock::now();
        printf("  lip-sync: %d files\n", lipOk);
      }
      fflush(stdout);
    }
  if (lipKeys) fclose(lipKeys);
  printf("Japanese lip-sync / motion / captions: %d files, %d already the same, %d kept English\n", lipOk, lipSame, lipKept);
  for (auto& r : lipReasons) printf("  lip-sync kept English: %d x %s\n", r.second, r.first.c_str());
  fclose(report);
  printf("Voices converted %d, kept English %d\n", ok, failed);
  printf("Voice lip-sync: %u voice files carry their Japanese lip-sync block\n", g_voice_lip.load());
  printf("Voice size: %u streams re-encoded to fit the 360 size, %u still larger\n", g_shrunk.load(), g_over.load());
  printf("Voice files kept English: %d the same in both languages, %d still larger than the 360 file\n",
         reasons["the same in both languages on Steam"], reasons["larger than the 360 file after re-encoding"]);
  for (auto& r : reasons) printf("  kept English: %d x %s\n", r.second, r.first.c_str());
  for (auto& o : outs) o.arc.close();
  if (!ok && !lipOk) {
    for (auto& o : outs) fs::remove(o.arc_path);
    return 2;
  }
  for (auto& o : outs) {
    if (o.files.empty()) {
      fs::remove(o.arc_path);
      if (fs::exists(o.final_path)) fs::remove(o.final_path);
      if (fs::exists(o.list_path)) fs::remove(o.list_path);
      continue;
    }
    if (fs::exists(o.final_path)) fs::remove(o.final_path);
    fs::rename(o.arc_path, o.final_path);
    write_filelist(o.list_path, o.files);
    printf("Wrote %s (%.0f MB, %zu files)\n", o.final_path.string().c_str(), o.written / 1048576.0, o.files.size());
  }
  return ok * 2 >= ok + failed ? 0 : 3;
}


// ---------------------------------------------------------------- movie audio

// The recap movies (movielistu: hm_hstr_*, tm_abst_*) carry their narration in the Bink file.
// Steam has a Japanese set (movielistc, same file codes); its audio is written as
// <out>/<360 name>.wav (48000 Hz stereo) for the port to play instead of the movie's own
// tracks. Bink audio tracks: count at +40 of the header, then per track 4 bytes, then per
// track sample rate (u16) and flags (u16, 0x2000 = stereo). Four tracks of 2, 1, 1, and 2
// channels are front, centre, LFE, and surround (how the game routes them, sub_82840E20) and
// are mixed down as 5.1.
static int movies(const fs::path& root360, const fs::path& rootSteam, const fs::path& outDir) {
  if (g_ffmpeg.empty()) throw std::runtime_error("--movies needs --ffmpeg");
  fs::path list360 = root360 / "alba_data" / "sys" / "movielistu.x360.bin";
  fs::path listJp = rootSteam / "alba_data" / "sys" / "movielistc.win32.bin";
  fs::path pack = rootSteam / "alba_data" / "movie" / "moviec.win32.bin";
  if (!fs::exists(pack)) {
    std::error_code ec;
    for (auto& d : fs::directory_iterator(rootSteam / "alba_data" / "movie", ec)) {
      std::string n = lower(d.path().filename().string());
      printf("  Steam movie folder: %s (%llu bytes)\n", n.c_str(), (unsigned long long)fs::file_size(d.path(), ec));
      if (n.find("moviec") != std::string::npos) pack = d.path();
    }
  }
  if (!fs::exists(pack)) throw std::runtime_error("no Japanese movie pack (alba_data\\movie\\moviec.win32.bin) in the Steam folder");
  auto ours = parse_filelist(read_file(list360));
  auto jp = parse_filelist(read_file(listJp));
  std::unordered_map<uint32_t, const Entry*> by_code, en_by_code;
  for (auto& e : jp) by_code[e.code] = &e;
  // Steam's English list: a movie at the same place with the same size in both packs is the
  // same file (the hm_hstr previews, which have music only) and keeps the 360 audio.
  std::vector<Entry> en;
  fs::path listEn = rootSteam / "alba_data" / "sys" / "movielistu.win32.bin";
  if (fs::exists(listEn)) en = parse_filelist(read_file(listEn));
  for (auto& e : en) en_by_code[e.code] = &e;
  int same = 0;
  Archive arc(pack);
  fs::create_directories(outDir);
  int done = 0, missing = 0, failed = 0, n = 0;
  auto last = std::chrono::steady_clock::now();
  for (auto& e : ours) {
    ++n;
    std::string name = e.path.substr(e.path.find_last_of('/') + 1);
    if (name.size() > 4 && name.substr(name.size() - 4) == ".bik") name.resize(name.size() - 4);
    auto it = by_code.find(e.code);
    if (it == by_code.end()) {
      printf("  %s: no Japanese version\n", name.c_str());
      ++missing;
      continue;
    }
    if (auto u = en_by_code.find(e.code);
        u != en_by_code.end() && u->second->sector == it->second->sector && u->second->usize == it->second->usize) {
      ++same;
      continue;
    }
    if (it->second->usize < 1024) {
      ++missing;
      continue;
    }
    Bytes bik = arc.read(*it->second);
    std::vector<int> chans;
    if (bik.size() >= 48 && (!memcmp(bik.data(), "BIK", 3) || !memcmp(bik.data(), "KB2", 3))) {
      uint32_t tracks = rd32(&bik[40], false);
      if (tracks <= 8 && 44 + tracks * 8 <= bik.size())
        for (uint32_t t = 0; t < tracks; t++) chans.push_back((rd16(&bik[44 + tracks * 4 + t * 4 + 2], false) & 0x2000) ? 2 : 1);
    }
    if (chans.empty()) {
      printf("  %s: no audio in the Japanese movie (%c%c%c header)\n", name.c_str(), bik.size() > 3 ? bik[0] : '?',
             bik.size() > 3 ? bik[1] : '?', bik.size() > 3 ? bik[2] : '?');
      ++failed;
      continue;
    }
    fs::path tmp = g_tmp / (name + ".bik"), wav = outDir / (name + ".wav"), part = outDir / (name + ".part.wav");
    write_file(tmp, bik);
    std::vector<std::string> args = {"-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", tmp.string(), "-vn"};
    // Two sets of front, centre, LFE, and surround: only the first is used.
    if (chans == std::vector<int>{2, 1, 1, 2, 2, 1, 1, 2}) {
      printf("  %s: 8 audio tracks; using the first 4\n", name.c_str());
      chans.resize(4);
    }
    if (chans.size() == 1) {
      args.insert(args.end(), {"-map", "0:a:0"});
    } else {
      std::string fc;
      for (size_t t = 0; t < chans.size(); t++) fc += "[0:a:" + std::to_string(t) + "]";
      fc += "amerge=inputs=" + std::to_string(chans.size());
      if (chans == std::vector<int>{2, 1, 1, 2}) fc += ",channelmap=channel_layout=5.1";
      fc += "[a]";
      args.insert(args.end(), {"-filter_complex", fc, "-map", "[a]"});
    }
    args.insert(args.end(), {"-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", "-f", "wav", part.string()});
    int rc = run_ffmpeg(args);
    std::error_code ec;
    fs::remove(tmp, ec);
    if (rc != 0 || !fs::exists(part) || fs::file_size(part, ec) < 1024) {
      printf("  %s: ffmpeg could not read the Japanese audio (exit %d, %zu tracks)\n", name.c_str(), rc, chans.size());
      fs::remove(part, ec);
      ++failed;
      continue;
    }
    fs::rename(part, wav, ec);
    ++done;
    auto now = std::chrono::steady_clock::now();
    if (n == 1 || now - last > std::chrono::seconds(5)) {
      last = now;
    }
    printf("  movie audio %d of %zu: %s (%zu tracks)\n", n, ours.size(), name.c_str(), chans.size());
    fflush(stdout);
  }
  printf("Movie audio: %d of %zu converted, %d the same in both languages (kept), %d without a Japanese version, %d failed\n",
         done, ours.size(), same, missing, failed);
  return done == 0 ? 1 : 0;
}

// --strip-lipsync <jpvoice folder>: takes the lip-sync / facial motion / caption files listed in
// jp_lipsync.txt out of an already converted patch (only the two file lists change; the data
// stays in the archives, unreferenced). XIII-2: with them, Serah did not load after a save
// was loaded (the game then crashed); the voices alone are fine.
static int strip_lipsync(const fs::path& dir) {
  std::set<uint64_t> keys;
  {
    std::ifstream in(dir / "jp_lipsync.txt");
    std::string line;
    while (std::getline(in, line)) {
      if (line.empty()) continue;
      keys.insert(std::stoull(line.substr(0, line.find(' ')), nullptr, 16));
    }
  }
  printf("lip-sync files listed: %zu\n", keys.size());
  int removed = 0;
  for (const char* n : {"filelist_patch_u.x360.bin", "filelist_patch_scr_u.x360.bin"}) {
    fs::path list = dir / n;
    if (!fs::exists(list)) continue;
    fs::path backup = dir / (std::string(n) + ".with_lipsync");
    std::error_code ec;
    if (!fs::exists(backup)) fs::copy_file(list, backup, ec);
    std::vector<PatchFile> keep;
    int here = 0;
    for (auto& e : parse_filelist(read_file(list))) {
      if (keys.count(uint64_t(e.ftype) << 32 | e.code)) { here++; continue; }
      if (e.csize != e.usize) throw std::runtime_error("compressed entry in " + list.string());
      keep.push_back({e.code, e.ftype, e.path, e.sector, e.usize});
    }
    write_filelist(list, keep);
    printf("%s: %d lip-sync files taken out, %zu files left\n", n, here, keep.size());
    removed += here;
  }
  printf("Lip-sync taken out: %d files\n", removed);
  return 0;
}

// --subset <jpvoice folder> <test>: narrows the converted patch for a test, always from the
// voice-only lists saved on first use (<list>.voices): "one" keeps a single voice file,
// (one per file list), "nofield" leaves out the field character packs (sound/pack/03xx, e.g. Serah's own sounds),
// "field" keeps only those, "all" puts every voice file back.
static int subset(const fs::path& dir, const std::string& test) {
  int kept_total = 0;
  for (const char* n : {"filelist_patch_u.x360.bin", "filelist_patch_scr_u.x360.bin"}) {
    fs::path list = dir / n, base = dir / (std::string(n) + ".voices");
    if (!fs::exists(list)) continue;
    std::error_code ec;
    if (!fs::exists(base)) fs::copy_file(list, base, ec);
    std::vector<PatchFile> keep;
    for (auto& e : parse_filelist(read_file(base))) {
      const bool field = !e.path.compare(0, 13, "sound/pack/03");
      bool take = test == "all" || (test == "nofield" && !field) || (test == "field" && field) ||
                  (test == "one" && keep.empty() && !field && is_english_voice(e.path));
      if (take) keep.push_back({e.code, e.ftype, e.path, e.sector, e.usize});
    }
    write_filelist(list, keep);
    kept_total += int(keep.size());
    printf("%s: %zu files (%s)\n", n, keep.size(), test.c_str());
    for (size_t i = 0; i < keep.size() && test == "one"; i++) printf("  %s\n", keep[i].path.c_str());
  }
  printf("Voice test '%s': %d files in the patch\n", test.c_str(), kept_total);
  return 0;
}

// --merge-update <title update folder> <jpvoice folder> <out folder>: the 360 title update with
// the Japanese voices added, for the update folder (the game reads one patch there): every file
// of the title update is copied (hard links where the drive allows), and each of its two patch
// lists / archives (filelist_patch_u / white_patch_u, _scr_) gets the voice files appended -
// a voice file replaces the title update's file of the same type and code.
static void link_or_copy(const fs::path& from, const fs::path& to) {
  std::error_code ec;
  fs::create_directories(to.parent_path(), ec);
  fs::remove(to, ec);
  fs::create_hard_link(from, to, ec);
  if (ec) fs::copy_file(from, to, fs::copy_options::overwrite_existing);
}
static int merge_update(const fs::path& tu, const fs::path& voices, const fs::path& out) {
  if (!fs::exists(tu / "x2_title_update.txt")) throw std::runtime_error("no unpacked title update in " + tu.string());
  fs::path tmp = out;
  tmp += ".new";
  std::error_code ec;
  fs::remove_all(tmp, ec);
  fs::create_directories(tmp);
  const char* lists[2] = {"filelist_patch_u.x360.bin", "filelist_patch_scr_u.x360.bin"};
  const char* archives[2] = {"white_patch_u.x360.bin", "white_patch_scr_u.x360.bin"};
  // Validate both inputs before constructing or publishing a merged update.
  // A marker alone is not evidence that a title-update archive is complete.
  std::map<uint64_t, std::string> declared_lips;
  {
    std::ifstream manifest(voices / "jp_lipsync.txt");
    std::string line;
    while (std::getline(manifest, line)) {
      const auto space = line.find(' ');
      if (space == std::string::npos) continue;
      declared_lips.emplace(std::stoull(line.substr(0, space), nullptr, 16), lower(line.substr(space + 1)));
    }
  }
  bool title_data = false;
  for (int k = 0; k < 2; ++k) {
    for (const auto& root : {tu, voices}) {
      const bool list = fs::is_regular_file(root / lists[k]);
      const bool archive = fs::is_regular_file(root / archives[k]);
      if (list != archive) throw std::runtime_error("incomplete update list/archive pair in " + root.string());
      if (!list) continue;
      if (root == tu) title_data = true;
      const auto size = fs::file_size(root / archives[k]);
      std::set<std::pair<uint8_t, uint32_t>> identities;
      for (const auto& entry : parse_filelist(read_file(root / lists[k]))) {
        if (!identities.insert({entry.ftype, entry.code}).second)
          throw std::runtime_error("duplicate update identity: " + entry.path);
        if (entry.sector > size / 2048 || entry.csize > size - entry.sector * 2048)
          throw std::runtime_error("update entry outside archive: " + entry.path);
        const auto path = lower(entry.path);
        const auto declared = declared_lips.find(uint64_t(entry.ftype) << 32 | entry.code);
        const bool lip_path = path.rfind("mot/", 0) == 0 || path.rfind("event/", 0) == 0 ||
            path.rfind("zone/lip/", 0) == 0 || path.rfind("sound/pack/caption/", 0) == 0;
        const bool lip = lip_path && declared != declared_lips.end() && declared->second == path;
        if (root == voices && !is_english_voice(path) && !lip)
          throw std::runtime_error("non-dialogue asset in voice patch: " + entry.path);
      }
    }
  }
  if (!title_data) throw std::runtime_error("title update has no complete patch archive");
  for (auto& e : fs::recursive_directory_iterator(tu)) {
    if (!e.is_regular_file()) continue;
    fs::path rel = fs::relative(e.path(), tu);
    std::string name = lower(rel.filename().string());
    bool patch = rel.parent_path().empty() && (name == lists[0] || name == lists[1] || name == archives[0] || name == archives[1]);
    if (!patch) link_or_copy(e.path(), tmp / rel);
  }
  for (int k = 0; k < 2; k++) {
    fs::path tl = tu / lists[k], ta = tu / archives[k], vl = voices / lists[k], va = voices / archives[k];
    const bool has_tu = fs::exists(tl) && fs::exists(ta), has_v = fs::exists(vl) && fs::exists(va);
    if (!has_tu && !has_v) continue;
    if (!has_v) {
      link_or_copy(tl, tmp / lists[k]);
      link_or_copy(ta, tmp / archives[k]);
      printf("%s: the title update's (no voices)\n", lists[k]);
      continue;
    }
    std::vector<Entry> ve = parse_filelist(read_file(vl));
    std::set<std::pair<uint8_t, uint32_t>> voiced;
    for (auto& e : ve) voiced.insert({e.ftype, e.code});
    std::vector<PatchFile> files;
    uint64_t shift = 0;
    int from_tu = 0, replaced = 0;
    std::ofstream arc(tmp / archives[k], std::ios::binary);
    if (has_tu) {
      for (auto& e : parse_filelist(read_file(tl))) {
        if (voiced.count({e.ftype, e.code})) {
          const auto replacement = std::find_if(ve.begin(), ve.end(), [&](const Entry& v) {
            return v.ftype == e.ftype && v.code == e.code;
          });
          if (lower(replacement->path) != lower(e.path))
            throw std::runtime_error("voice/title-update identity collision: " + e.path);
          replaced++; continue;
        }
        files.push_back({e.code, e.ftype, e.path, e.sector, e.usize, e.csize != e.usize ? e.csize : 0});
        from_tu++;
      }
      std::ifstream in(ta, std::ios::binary);
      arc << in.rdbuf();
      uint64_t size = fs::file_size(ta);
      uint64_t padded = (size + 2047) / 2048 * 2048;
      for (uint64_t i = size; i < padded; i++) arc.put(0);
      shift = padded / 2048;
    }
    {
      std::ifstream in(va, std::ios::binary);
      arc << in.rdbuf();
    }
    arc.close();
    if (!arc) throw std::runtime_error("cannot write " + (tmp / archives[k]).string());
    for (auto& e : ve) files.push_back({e.code, e.ftype, e.path, e.sector + shift, e.usize, e.csize != e.usize ? e.csize : 0});
    write_filelist(tmp / lists[k], files);
    printf("%s: %d title update files (%d replaced by voices), %zu voice files\n", lists[k], from_tu, replaced, ve.size());
  }
  {
    std::ifstream stamp_in(tu / "x2_title_update.txt");
    std::string stamp;
    std::getline(stamp_in, stamp);
    std::ifstream vv(voices / "version.txt");
    std::string vver;
    std::getline(vv, vver);
    std::ofstream(tmp / "x2_merged_v2.txt") << stamp << " | voices " << vver << "\n";
  }
  fs::remove_all(out, ec);
  fs::rename(tmp, out);
  printf("Merged update written to %s\n", out.string().c_str());
  return 0;
}

// ---------------------------------------------------------------- DLC voices
static bool verify_jp_dlc(const fs::path& pkg, const fs::path& copy, const std::string& id,
                          std::string& reason, const std::function<void(uint64_t)>& progress = {}) {
  if (!X2JpDlcCurrent(pkg, copy, id, reason, progress)) return false;
  using Key = std::pair<uint8_t, uint32_t>;
  size_t checked = 0, voices = 0, lips = 0;
  uint64_t audited_bytes = 0;
  auto scd_ok = [](const Bytes& before, const Bytes& after) {
    Scd a, b; std::string why;
    if (!parse_scd(before, a, why) || !parse_scd(after, b, why) || !b.be || a.metas.size() != b.metas.size()) return false;
    for (const auto off : b.metas) {
      OggInfo info;
      if (!check_stream(after.data() + off, after.size() - off, true, info, why)) return false;
    }
    return true;
  };
  try {
    for (const char* kind : {"img", "scr"}) {
      const std::string suffix = "p" + id + kind + "_u.x360.bin";
      const auto fl = copy / ("filelist_" + suffix);
      if (!fs::is_regular_file(fl)) continue;
      const auto originals = parse_filelist(read_file(pkg / fl.filename()));
      const auto entries = parse_filelist(read_file(fl));
      std::map<Key, const Entry*> by_key;
      for (const auto& e : originals)
        if (!by_key.emplace(Key{e.ftype, e.code}, &e).second) throw std::runtime_error("duplicate source ID");
      if (entries.size() != by_key.size()) throw std::runtime_error("entry count changed");
      Archive source(pkg / ("white_" + suffix)), active(copy / ("white_" + suffix));
      Key previous{}; bool first = true;
      for (const auto& e : entries) {
        const Key key{e.ftype, e.code};
        if (!first && key <= previous) throw std::runtime_error("Japanese index is not strictly sorted");
        first = false; previous = key;
        const auto it = by_key.find(key);
        if (it == by_key.end() || it->second->path != e.path) throw std::runtime_error("file identity changed: " + e.path);
        const Entry& original = *it->second;
        if (original.sector > source.size / 2048 || original.csize > source.size - original.sector * 2048)
          throw std::runtime_error("entry outside installed archive: " + e.path);
        if (e.sector > active.size / 2048 || e.csize > active.size - e.sector * 2048)
          throw std::runtime_error("entry outside Japanese archive: " + e.path);
        checked++;
        if (progress) progress(audited_bytes += e.csize);
        if (e.sector == original.sector && e.usize == original.usize && e.csize == original.csize) continue;
        const std::string p = lower(e.path);
        const bool lip = (!p.compare(0, 15, "event/schedule/") && p.find("/dataset/") != std::string::npos) ||
                         !p.compare(0, 9, "zone/lip/") ||
                         (!p.compare(0, 8, "mot/mon/") && p.find("lsdpack") != std::string::npos);
        if (!is_english_voice(p)) {
          if (!lip) throw std::runtime_error("non-voice asset changed: " + e.path);
          // Lip-sync is intentionally allowed to change; this is not a semantic
          // comparison of its animation against the Steam Japanese source.
          active.read(e); lips++; continue;
        }
        if (uint64_t(e.usize) > uint64_t(original.usize) + original.usize / 50)
          throw std::runtime_error("voice exceeds installed allocation budget: " + e.path);
        const Bytes before = source.read(original), after = active.read(e);
        if (p.compare(p.size() - 4, 4, ".scd") == 0) {
          if (!scd_ok(before, after)) throw std::runtime_error("invalid Japanese voice streams: " + e.path);
        } else {
          bool abe, bbe; std::vector<WpdEntry> a, b;
          if (!parse_wpd(before, abe, a) || !parse_wpd(after, bbe, b) || !bbe || a.size() != b.size())
            throw std::runtime_error("invalid Japanese voice bank: " + e.path);
          for (size_t i = 0; i < a.size(); i++) {
            if (a[i].name != b[i].name || a[i].ext != b[i].ext) throw std::runtime_error("voice bank member identity changed");
            Bytes old_member(before.begin() + a[i].off, before.begin() + a[i].off + a[i].size);
            Bytes new_member(after.begin() + b[i].off, after.begin() + b[i].off + b[i].size);
            if (old_member != new_member && (a[i].ext != "scd" || !scd_ok(old_member, new_member)))
              throw std::runtime_error("invalid replacement voice bank member: " + a[i].name);
          }
        }
        voices++;
      }
    }
  } catch (const std::exception& ex) { reason = ex.what(); return false; }
  reason = "current; " + std::to_string(checked) + " indexed files, " + std::to_string(voices) +
           " replacement voice files structurally checked, " + std::to_string(lips) + " lip-sync files (semantics not compared)";
  return true;
}

// A 360 DLC package's voices in Japanese. The Steam version has the DLC in its own archives,
// so its Japanese set holds the DLC's Japanese voices too. For each file list of an installed
// package (filelist_pNNNNNNN{img,scr}_u.x360.bin with its white_p... archive), the archive is
// copied to <out>\NNNNNNN\, every English voice with a Japanese Steam version is converted as
// for the disc (same size limit) and appended, and the file list is written again with those
// entries pointing at the appended data. The port reads these copies instead of the package's
// own when the voices are Japanese (x2_hooks.cpp). The package itself is only read.
static bool g_dlc_lipsync = true;  // --no-dlc-lipsync: the DLC scenes keep the English lip-sync
static int dlc_voices(const fs::path& rootSteam, const fs::path& pkg, const fs::path& outDir) {
  struct List { fs::path list, arc; std::string id, name; };
  std::vector<List> lists;
  for (auto& e : fs::directory_iterator(pkg)) {
    const std::string n = lower(e.path().filename().string());
    if (n.size() != 31 || n.compare(0, 10, "filelist_p") || n.compare(n.size() - 11, 11, "_u.x360.bin")) continue;
    List l;
    l.id = n.substr(10, 7);
    l.list = e.path();
    l.arc = pkg / ("white_p" + n.substr(10));
    l.name = n;
    if (fs::exists(l.arc)) lists.push_back(l);
  }
  if (lists.empty()) {
    printf("%s: no file lists\n", pkg.string().c_str());
    return 0;
  }
  // Only packages with English voices are converted (the weapon and outfit packages have none).
  std::vector<std::pair<List, std::vector<Entry>>> todo;
  for (auto& l : lists) {
    auto ents = parse_filelist(read_file(l.list));
    size_t voices = 0;
    for (auto& e : ents) voices += is_english_voice(e.path);
    if (voices) todo.push_back({l, ents});
    else printf("%s: no English voices\n", l.name.c_str());
  }
  if (todo.empty()) return 0;
  fs::path aSteam = rootSteam / "alba_data";
  printf("Reading the Steam Japanese and English file lists\n");
  fflush(stdout);
  auto jp = load_sources(aSteam, "c", "win32");
  auto en = load_sources(aSteam, "u", "win32");
  if (jp.empty()) throw std::runtime_error("no Japanese file lists in " + rootSteam.string());
  typedef std::pair<uint8_t, uint32_t> Key;
  struct Ref { Source* src; const Entry* e; };
  std::map<Key, Ref> jpByCode, enByCode;
  std::map<std::string, Ref> jpByPath, enByPath;
  for (auto& s : jp)
    for (auto& e : s.entries) {
      jpByCode.emplace(Key(e.ftype, e.code), Ref{&s, &e});
      jpByPath.emplace(lower(e.path), Ref{&s, &e});
    }
  for (auto& s : en)
    for (auto& e : s.entries) {
      enByCode.emplace(Key(e.ftype, e.code), Ref{&s, &e});
      enByPath.emplace(lower(e.path), Ref{&s, &e});
    }

  int total_ok = 0;
  for (auto& [l, ents] : todo) {
    Archive arc(l.arc);
    std::vector<std::pair<size_t, Ref>> jobs;  // entry index, Japanese file
    int noJp = 0;
    for (size_t i = 0; i < ents.size(); i++) {
      const Entry& e = ents[i];
      if (!is_english_voice(e.path)) continue;
      Ref j = japanese_voice(e, jpByPath);
      if (j.e) jobs.push_back({i, j});
      else noJp++;
    }
    printf("%s: %zu English voice files with a Japanese version, %d without\n", l.name.c_str(), jobs.size(), noJp);
    fflush(stdout);
    fs::path dir = outDir / l.id;
    fs::create_directories(dir);
    fs::path outArc = dir / (l.arc.filename().string() + ".tmp");
    fs::copy_file(l.arc, outArc, fs::copy_options::overwrite_existing);
    std::fstream out(outArc, std::ios::binary | std::ios::in | std::ios::out | std::ios::ate);
    if (!out) throw std::runtime_error("cannot write " + outArc.string());
    uint64_t written = fs::file_size(outArc);
    {
      static const char zeros[2048] = {0};
      size_t pad = align_up(written, 2048) - written;
      out.write(zeros, pad);
      written += pad;
    }
    std::vector<PatchFile> files;
    files.reserve(ents.size());
    for (auto& e : ents) files.push_back({e.code, e.ftype, e.path, e.sector, e.usize, e.csize != e.usize ? e.csize : 0});
    int ok = 0, kept = 0;
    // Every file kept English, with why: <out>\NNNNNNN_<list>.csv.
    FILE* report = fopen((outDir / (l.id + "_" + l.name.substr(17, 3) + ".csv")).string().c_str(), "w");
    if (report) fprintf(report, "path,result,detail,size_360,size_steam,size_out\n");
    std::map<std::string, int> reasons;
    auto append = [&](size_t index, const Bytes& data) {
      PatchFile& pf = files[index];
      pf.sector = written / 2048;
      pf.size = uint32_t(data.size());
      pf.csize = 0;
      out.write((const char*)data.data(), data.size());
      written += data.size();
      static const char zeros[2048] = {0};
      size_t pad = align_up(written, 2048) - written;
      out.write(zeros, pad);
      written += pad;
    };
    auto last_print = std::chrono::steady_clock::now();
    unsigned nthreads = std::max(2u, std::min(16u, std::thread::hardware_concurrency()));
    const size_t batch = 4 * nthreads;
    for (size_t b0 = 0; b0 < jobs.size(); b0 += batch) {
      const size_t n = std::min(batch, jobs.size() - b0);
      struct T { Bytes ub, jb, ob; std::string why; bool good = false; };
      std::vector<T> ts(n);
      for (size_t k = 0; k < n; k++) {
        const Entry& ue = ents[jobs[b0 + k].first];
        try {
          ts[k].ub = arc.read(ue);
          ts[k].jb = jobs[b0 + k].second.src->archive->read(*jobs[b0 + k].second.e);
          const Ref english = steam_english_voice(ue, enByPath);
          if (english.e && english.e->usize == ts[k].jb.size() && english.src->archive->read(*english.e) == ts[k].jb)
            ts[k].why = "the same in both languages on Steam";
        } catch (const std::exception& ex) {
          ts[k].why = ex.what();
        }
      }
      std::atomic<size_t> next{0};
      std::vector<std::thread> th;
      for (unsigned w = 0; w < nthreads; w++)
        th.emplace_back([&] {
          for (size_t k; (k = next.fetch_add(1)) < n;) {
            if (!ts[k].why.empty()) continue;
            const Entry& ue = ents[jobs[b0 + k].first];
            try {
              WpdStats st;
              const bool wpd = ue.path.compare(ue.path.size() - 4, 4, ".wpd") == 0;
              ts[k].good = wpd ? convert_wpd(ts[k].ub, ts[k].jb, ts[k].ob, ts[k].why, st) : convert_scd(ts[k].ub, ts[k].jb, ts[k].ob, ts[k].why);
              if (ts[k].good && ts[k].ob.size() > size_t(ue.usize) + ue.usize / 50) {
                ts[k].good = false;
                ts[k].why = "larger than the 360 file after re-encoding";
              }
            } catch (const std::exception& ex) {
              ts[k].why = ex.what();
            }
          }
        });
      for (auto& x : th) x.join();
      for (size_t k = 0; k < n; k++) {
        const Entry& ue = ents[jobs[b0 + k].first];
        if (!ts[k].good) {
          kept++;
          std::string why = ts[k].why;
          reasons[why.size() > 60 ? why.substr(0, 60) : why]++;
          for (auto& ch : why) if (ch == ',') ch = ';';
          if (report)
            fprintf(report, "%s,kept English,%s,%u,%u,%zu\n", ue.path.c_str(), why.c_str(), ue.usize,
                    jobs[b0 + k].second.e->usize, ts[k].ob.size());
          continue;
        }
        append(jobs[b0 + k].first, ts[k].ob);
        if (report) fprintf(report, "%s,Japanese,,%u,%u,%zu\n", ue.path.c_str(), ue.usize, jobs[b0 + k].second.e->usize, ts[k].ob.size());
        ok++;
      }
      if (std::chrono::steady_clock::now() - last_print > std::chrono::seconds(10)) {
        last_print = std::chrono::steady_clock::now();
        printf("  %zu / %zu voice files\n", b0 + n, jobs.size());
        fflush(stdout);
      }
    }
    for (auto& r : reasons) printf("  kept English: %d x %s\n", r.second, r.first.c_str());
    // Japanese lip-sync of the DLC's scenes: the event DataSets (event/.../DataSet/*_us), the
    // zone's lip-sync pack (zone/lip/lsdpackNNNN_us.bin), and the lip-sync packs of the DLC's own
    // characters (mot/mon/.../lsdpack_us.bin), taken as the disc build does it (whole when the
    // 360 file is Steam's English one byte for byte, else the mtb / srd members merged). The
    // player characters' motion (mot/pc: where the disc build's Japanese lip-sync broke Serah) is
    // not touched. --no-dlc-lipsync leaves all of it English.
    int lipOk = 0, lipSame = 0, lipKept = 0;
    for (size_t i = 0; i < ents.size() && g_dlc_lipsync; i++) {
      const Entry& e = ents[i];
      const std::string p = lower(e.path);
      const bool event = !p.compare(0, 15, "event/schedule/") && p.find("/dataset/") != std::string::npos;
      const bool zone = !p.compare(0, 9, "zone/lip/");
      const bool mon = !p.compare(0, 8, "mot/mon/") && p.find("lsdpack") != std::string::npos;
      if (!event && !zone && !mon) continue;
      auto ij = jpByCode.find({e.ftype, e.code});
      auto ie = enByCode.find({e.ftype, e.code});
      if (ij == jpByCode.end() || ie == enByCode.end()) continue;
      Bytes ob;
      int taken = 0;
      try {
        Bytes jb = ij->second.src->archive->read(*ij->second.e);
        Bytes eb = ie->second.src->archive->read(*ie->second.e);
        if (jb == eb) { lipSame++; continue; }
        Bytes xb = arc.read(e);
        bool be = false;
        std::vector<WpdEntry> probe;
        if (!parse_wpd(xb, be, probe)) {
          if ((!zone && !mon) || xb != eb) throw std::runtime_error("not a bundle; kept the 360 file");
          ob = jb;
          taken = 1;
        } else {
          std::string why;
          if (!merge_jp_motion(xb, eb, jb, ob, why, taken)) {
            if (why == "same as English") { lipSame++; continue; }
            throw std::runtime_error(why);
          }
        }
      } catch (const std::exception& ex) {
        lipKept++;
        std::string r = ex.what();
        for (auto& ch : r) if (ch == ',') ch = ';';
        if (report) fprintf(report, "%s,kept English lip-sync,%s,%u,%u,\n", e.path.c_str(), r.c_str(), e.usize, ij->second.e->usize);
        continue;
      }
      append(i, ob);
      lipOk++;
      if (report) fprintf(report, "%s,Japanese lip-sync,%d member(s),%u,%u,%zu\n", e.path.c_str(), taken, e.usize, ij->second.e->usize, ob.size());
    }
    if (g_dlc_lipsync) printf("%s: Japanese lip-sync %d files, %d already the same, %d kept English\n", l.name.c_str(), lipOk, lipSame, lipKept);
    if (report) fclose(report);
    ok += lipOk;
    out.close();
    if (!out) throw std::runtime_error("cannot write " + outArc.string());
    fs::path finalArc = dir / l.arc.filename();
    if (fs::exists(finalArc)) fs::remove(finalArc);
    fs::rename(outArc, finalArc);
    write_filelist(dir / l.list.filename(), files);
    write_file(dir / ("source_" + l.list.filename().string()), read_file(l.list));
    write_file(dir / ("verified_" + l.list.filename().string()), read_file(dir / l.list.filename()));
    printf("%s: %d voices in Japanese, %d kept English -> %s\n", l.name.c_str(), ok - lipOk, kept, dir.string().c_str());
    fflush(stdout);
    total_ok += ok;
  }
  std::set<std::string> validated;
  for (const auto& item : todo) {
    if (!validated.insert(item.first.id).second) continue;
    std::string reason;
    auto last = std::chrono::steady_clock::now();
    const auto progress = [&](uint64_t done) {
      const auto now = std::chrono::steady_clock::now();
      if (now - last >= std::chrono::seconds(10)) {
        printf("  validating Japanese DLC %s: %llu MiB\n", item.first.id.c_str(),
               (unsigned long long)(done >> 20));
        fflush(stdout); last = now;
      }
    };
    if (!verify_jp_dlc(pkg, outDir / item.first.id, item.first.id, reason, progress))
      throw std::runtime_error("Japanese DLC provenance: " + reason);
  }
  return total_ok ? 0 : 2;
}

// ---------------------------------------------------------------- DLC sample
// For working out the Steam DLC conversion: the Steam version has the DLC in its own archives
// (udp\white_patch_*, sys\white_*), in the PC format. Takes from each installed 360 DLC package
// (<dlc root>\<package>\filelist_pNNNNNNN*_u.x360.bin with its white_p... archive) a sample of
// files - all of a small package, up to 3 per file kind of a large one, and some of the files
// the Steam lists do not have - and writes each with its Steam counterpart (".x360." read as
// ".win32."):
//   <out>\x360\NNNNNNN\<path>, <out>\steam\<path>, <out>\index.csv
// Everything is only read.
static int dlc_sample(const fs::path& rootSteam, const fs::path& dlcRoot, const fs::path& outDir) {
  auto put = [](const fs::path& p, const Bytes& d) {
    fs::create_directories(p.parent_path());
    write_file(p, d);
  };
  auto t0 = std::chrono::steady_clock::now();
  auto last = t0;
  auto tick = [&](const std::string& what) {
    auto now = std::chrono::steady_clock::now();
    if (now - last >= std::chrono::seconds(10)) {
      printf("  ... %s (%lld s)\n", what.c_str(), (long long)std::chrono::duration_cast<std::chrono::seconds>(now - t0).count());
      fflush(stdout);
      last = now;
    }
  };
  printf("Reading the Steam file lists (English set)\n");
  fflush(stdout);
  auto steam = load_sources(rootSteam / "alba_data", "u", "win32");
  struct Ref { Source* src; const Entry* e; };
  std::unordered_map<std::string, Ref> byPath;
  for (auto& s : steam)
    for (auto& e : s.entries) byPath.emplace(lower(e.path), Ref{&s, &e});  // first source wins (udp first)
  printf("  %zu Steam files\n", byPath.size());
  fs::create_directories(outDir);
  FILE* idx = fopen((outDir / "index.csv").string().c_str(), "w");
  if (!idx) throw std::runtime_error("cannot write " + (outDir / "index.csv").string());
  fprintf(idx, "dlc,list,path_360,size_360,code_360,ftype_360,steam_source,path_steam,size_steam,code_steam,ftype_steam\n");
  uint64_t bytes = 0;
  const uint64_t budget = 160ull << 20;  // 360 and Steam files together
  int files360 = 0, filesSteam = 0;
  for (auto& pkg : fs::directory_iterator(dlcRoot)) {
    if (!pkg.is_directory()) continue;
    for (auto& f : fs::directory_iterator(pkg.path())) {
      const std::string n = lower(f.path().filename().string());
      if (n.compare(0, 10, "filelist_p") || n.size() < 22 || n.compare(n.size() - 11, 11, "_u.x360.bin")) continue;
      const std::string id = n.substr(10, 7);
      fs::path arcPath = pkg.path() / ("white_p" + n.substr(10));
      if (!fs::exists(arcPath)) continue;
      auto ents = parse_filelist(read_file(f.path()));
      Archive arc(arcPath);
      printf("%s: %zu files\n", n.c_str(), ents.size());
      fflush(stdout);
      // the sample: every file of a list of up to 64; otherwise per kind (the extension, with the
      // platform part) up to 3 (small, middle, the largest under 2 MB), plus up to 12 without a Steam twin
      std::vector<size_t> pick;
      if (ents.size() <= 64) {
        for (size_t i = 0; i < ents.size(); i++) pick.push_back(i);
      } else {
        std::map<std::string, std::vector<size_t>> kinds;
        std::vector<size_t> missing;
        for (size_t i = 0; i < ents.size(); i++) {
          std::string p = lower(ents[i].path);
          size_t slash = p.rfind('/');
          size_t dot = p.find('.', slash == std::string::npos ? 0 : slash);
          std::string kind = dot == std::string::npos ? "(none)" : p.substr(dot);
          std::string sp = p;
          size_t x = sp.find(".x360.");
          if (x != std::string::npos) sp.replace(x, 6, ".win32.");
          if (!byPath.count(sp)) {
            if (ents[i].usize < (2u << 20)) missing.push_back(i);  // (the movies are left out)
          }
          else kinds[kind].push_back(i);
        }
        for (auto& [kind, v] : kinds) {
          std::sort(v.begin(), v.end(), [&](size_t a, size_t b) { return ents[a].usize < ents[b].usize; });
          // a small one, a middle one, and the largest under 2 MB
          std::vector<size_t> c;
          for (double q : {0.1, 0.5}) c.push_back(v[size_t(q * (v.size() - 1))]);
          for (size_t k = v.size(); k-- > 0;)
            if (ents[v[k]].usize < (2u << 20)) { c.push_back(v[k]); break; }
          std::sort(c.begin(), c.end());
          c.erase(std::unique(c.begin(), c.end()), c.end());
          pick.insert(pick.end(), c.begin(), c.end());
        }
        std::map<std::string, size_t> byLower;
        for (size_t i = 0; i < ents.size(); i++) byLower.emplace(lower(ents[i].path), i);
        for (size_t k = 0; k < missing.size() && k < 12; k++) {
          const size_t i = missing[k * missing.size() / std::min<size_t>(12, missing.size())];
          pick.push_back(i);
          // an event's DataSet files missing on Steam: also the event's own .xwb (both sides), in
          // case Steam keeps them in it
          const std::string p = lower(ents[i].path);
          const size_t ds = p.find("/dataset/");
          if (p.compare(0, 15, "event/schedule/") || ds == std::string::npos) continue;
          const std::string ev = p.substr(15, ds - 15);
          auto x = byLower.find("event/schedule/" + ev + "/bin/" + ev + ".white.x360.xwb");
          if (x != byLower.end()) pick.push_back(x->second);
        }
        std::sort(pick.begin(), pick.end());
        pick.erase(std::unique(pick.begin(), pick.end()), pick.end());
      }
      for (size_t i : pick) {
        const Entry& e = ents[i];
        if (bytes > budget) break;
        Bytes d;
        try {
          d = arc.read(e);
        } catch (const std::exception& ex) {
          printf("  %s: %s\n", e.path.c_str(), ex.what());
          continue;
        }
        put(outDir / "x360" / id / e.path, d);
        bytes += d.size();
        files360++;
        std::string sp = lower(e.path);
        size_t x = sp.find(".x360.");
        if (x != std::string::npos) sp.replace(x, 6, ".win32.");
        auto it = byPath.find(sp);
        if (it == byPath.end()) {
          fprintf(idx, "%s,%s,%s,%u,%u,%u,,,,,\n", id.c_str(), n.c_str(), e.path.c_str(), e.usize, e.code, e.ftype);
        } else {
          const Entry& se = *it->second.e;
          Bytes sd;
          std::string err;
          try {
            sd = it->second.src->archive->read(se);
            put(outDir / "steam" / se.path, sd);
            bytes += sd.size();
            filesSteam++;
          } catch (const std::exception& ex) {
            err = ex.what();
          }
          fprintf(idx, "%s,%s,%s,%u,%u,%u,%s,%s,%u,%u,%u%s\n", id.c_str(), n.c_str(), e.path.c_str(), e.usize, e.code, e.ftype,
                  it->second.src->name.c_str(), se.path.c_str(), se.usize, se.code, se.ftype, err.empty() ? "" : (",read failed: " + err).c_str());
        }
        tick(e.path);
      }
    }
  }
  fclose(idx);
  printf("Sample: %d 360 files, %d Steam files, %.1f MB in %s\n", files360, filesSteam, bytes / 1048576.0, outDir.string().c_str());
  return files360 ? 0 : 2;
}

int main(int argc, char** argv) {
  // --ffmpeg <ffmpeg.exe>: re-encode the MS ADPCM voices to Ogg Vorbis (temp files next to out)
  std::vector<char*> args;
  for (int i = 0; i < argc; i++) {
    if (!strcmp(argv[i], "--lipsync")) {
      g_lipsync = true;
      continue;
    }
    if (!strcmp(argv[i], "--no-dlc-lipsync")) {
      g_dlc_lipsync = false;
      continue;
    }
    if (!strcmp(argv[i], "--ffmpeg") && i + 1 < argc) {
      g_ffmpeg = argv[++i];
      continue;
    }
    if (!strcmp(argv[i], "--cache") && i + 1 < argc) {
      g_cache = argv[++i];
      std::error_code ec;
      fs::create_directories(g_cache, ec);
      if (ec) g_cache.clear();
      continue;
    }
    args.push_back(argv[i]);
  }
  argc = int(args.size());
  argv = args.data();
  g_tmp = fs::temp_directory_path() / ("x2_jpvoice_" + std::to_string(unsigned(time(nullptr))));
  if (!g_ffmpeg.empty()) fs::create_directories(g_tmp);
  struct TmpCleanup {
    ~TmpCleanup() {
      std::error_code ec;
      if (!g_tmp.empty()) fs::remove_all(g_tmp, ec);
    }
  } tmp_cleanup;
  try {
    if (argc == 5 && !strcmp(argv[1], "--verify-dlc")) {
      std::string reason;
      auto last = std::chrono::steady_clock::now();
      const bool ok = verify_jp_dlc(argv[2], argv[3], argv[4], reason, [&](uint64_t done) {
        const auto now = std::chrono::steady_clock::now();
        if (now - last >= std::chrono::seconds(10)) {
          printf("  validating Japanese DLC: %llu MiB\n", (unsigned long long)(done >> 20));
          fflush(stdout); last = now;
        }
      });
      printf("Japanese DLC provenance: %s\n", reason.c_str());
      return ok ? 0 : 1;
    }
    if (argc == 5 && (!strcmp(argv[1], "--scd") || !strcmp(argv[1], "--wpd"))) {
      Bytes us = read_file(argv[2]), jp = read_file(argv[3]), out;
      std::string why;
      WpdStats st;
      bool ok = !strcmp(argv[1], "--scd") ? convert_scd(us, jp, out, why) : convert_wpd(us, jp, out, why, st);
      if (!ok) { fprintf(stderr, "failed: %s\n", why.c_str()); return 1; }
      if (st.members) printf("%d of %d members converted %s\n", st.converted, st.members, st.first_problem.c_str());
      write_file(argv[4], out);
      return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "--list")) {
      for (auto& e : parse_filelist(read_file(argv[2])))
        printf("%u|%u|%llx:%x:%x:%s\n", e.code, e.ftype, (unsigned long long)e.sector, e.usize, e.csize, e.path.c_str());
      return 0;
    }
    if (argc == 5 && !strcmp(argv[1], "--merge-update")) return merge_update(argv[2], argv[3], argv[4]);
    if (argc == 4 && !strcmp(argv[1], "--subset")) return subset(argv[2], argv[3]);
    if (argc == 3 && !strcmp(argv[1], "--strip-lipsync")) return strip_lipsync(argv[2]);
    if (argc == 5 && !strcmp(argv[1], "--movies")) return movies(argv[2], argv[3], argv[4]);
    if (argc == 5 && !strcmp(argv[1], "--dlc")) return dlc_voices(argv[2], argv[3], argv[4]);
    if (argc == 5 && !strcmp(argv[1], "--dlc-sample")) return dlc_sample(argv[2], argv[3], argv[4]);
    if (argc == 7 && !strcmp(argv[1], "--fit-test")) {  // <ogg> <channels> <rate> <budget> <out>: fit_ogg alone
      Bytes ogg = read_file(argv[2]);
      int sr = atoi(argv[4]);
      fit_ogg(ogg, atoi(argv[3]), sr, size_t(atol(argv[5])), OggLoop());
      write_file(argv[6], ogg);
      printf("%zu bytes at %d Hz (shrunk %u, over %u)\n", ogg.size(), sr, g_shrunk.load(), g_over.load());
      return 0;
    }
    if (argc == 4 || argc == 5) return build(argv[1], argv[2], argv[3], argc == 5 ? fs::path(argv[4]) : fs::path());
    fprintf(stderr, "usage: x2_jpvoice [--ffmpeg <ffmpeg>] <360 game folder> <Steam game folder> <out folder> [samples folder]\n");
    return 1;
  } catch (const std::exception& ex) {
    fprintf(stderr, "error: %s\n", ex.what());
    return 1;
  }
}
