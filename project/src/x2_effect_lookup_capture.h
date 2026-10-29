#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

struct X2EffectCaptureRange {
  std::string name;
  uint32_t address;
  std::vector<uint8_t> bytes;
};
struct X2EffectLookupCapture {
  int16_t group = 0, index = 0;
  uint32_t resource = 0;
  bool complete = false;
  std::string stage;
  std::vector<X2EffectCaptureRange> ranges;
};

// Follow the exact 828F0CD0 pointer chain through a checked reader. No game
// memory is changed, and unreadable pointers stop capture instead of faulting.
inline X2EffectLookupCapture X2CaptureEffectLookup(
    uint32_t object, uint32_t references,
    const std::function<bool(uint32_t, uint8_t*, size_t)>& read) {
  X2EffectLookupCapture out;
  auto capture = [&](const char* name, uint32_t address, size_t size) -> const uint8_t* {
    out.stage = name;
    if (address < 0x10000 || uint64_t(address) + size > 0x100000000ull) return nullptr;
    std::vector<uint8_t> bytes(size);
    if (!read(address, bytes.data(), size)) return nullptr;
    out.ranges.push_back({name, address, std::move(bytes)});
    return out.ranges.back().bytes.data();
  };
  auto word = [](const uint8_t* p) {
    return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
  };
  auto p = capture("references", references, 4);
  if (!p) return out;
  out.group = int16_t(uint16_t(p[0]) << 8 | p[1]);
  out.index = int16_t(uint16_t(p[2]) << 8 | p[3]);
  p = capture("object", object, 4);
  if (!p) return out;
  const uint32_t node = word(p);
  p = capture("node", node, 12);
  if (!p) return out;
  const uint32_t manager = word(p + 8);
  p = capture("manager", manager, 20);
  if (!p) return out;
  const uint32_t table = word(p + (out.group < 0 ? 16 : 12));
  const uint32_t ordinal = out.group < 0 ? uint32_t(-int32_t(out.group) - 1) : uint32_t(out.group);
  const uint32_t entry = table + ordinal * 20;
  p = capture("selected-group", entry, 20);
  if (!p) return out;
  const uint32_t array = word(p + 16);
  const uint32_t slot = array + uint32_t(int32_t(out.index)) * 4;
  p = capture("selected-slot", slot, 4);
  if (!p) return out;
  out.resource = word(p);
  // A short pointer-array prefix helps distinguish a valid entry from an
  // out-of-bounds read into nearby allocator metadata. Its absence is harmless.
  capture("resource-array-prefix", array, 32);
  out.complete = capture("resource-header", out.resource, 64) != nullptr;
  return out;
}
