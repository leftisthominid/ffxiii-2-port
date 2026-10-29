#pragma once
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

// Inspect a class after the native CLB decoder has relocated its pointers.
// Return only the branch that selects WhiteResident's existing no-recap path.
inline std::optional<size_t> X2FindStoryRecapBranch(std::span<uint8_t> bytes, uint32_t guest) {
  auto inside = [&](size_t at, size_t n) { return at <= bytes.size() && n <= bytes.size() - at; };
  auto u16 = [&](size_t at) { return uint16_t(uint16_t(bytes[at]) << 8 | bytes[at + 1]); };
  auto u32 = [&](size_t at) { return uint32_t(bytes[at]) << 24 | uint32_t(bytes[at + 1]) << 16 |
                                  uint32_t(bytes[at + 2]) << 8 | bytes[at + 3]; };
  auto pointer = [&](uint32_t address, size_t n) -> std::optional<size_t> {
    if (address < guest) return {};
    size_t at = address - guest;
    return inside(at, n) ? std::optional<size_t>(at) : std::nullopt;
  };
  auto text = [&](uint32_t address) -> std::string_view {
    auto at = pointer(address, 1);
    if (!at) return {};
    for (size_t n = 0; n < 128 && inside(*at, n + 1); ++n)
      if (!bytes[*at + n]) return {reinterpret_cast<const char*>(bytes.data() + *at), n};
    return {};
  };
  if (!inside(0, 56) || u32(0) != 0x53535353 || text(u32(32)) != "WhiteResident") return {};
  const size_t constants = u16(8), methods = u16(16);
  auto pool = pointer(u32(24), constants * 16), table = pointer(u32(48), methods * 24);
  if (!pool || !table) return {};
  auto constant = [&](uint16_t index, uint8_t tag) -> std::optional<size_t> {
    if (index >= constants) return {};
    size_t at = *pool + 16 * index;
    return bytes[at] == tag ? std::optional<size_t>(at) : std::nullopt;
  };
  auto string = [&](uint16_t index) -> std::string_view {
    auto at = constant(index, 1);
    return at ? text(u32(*at + 12)) : std::string_view{};
  };
  auto member = [&](uint16_t index, uint8_t tag, std::string_view cls,
                    std::string_view name, std::string_view signature) {
    auto at = constant(index, tag);
    if (!at) return false;
    auto type = constant(u16(*at + 8), 7), pair = constant(u16(*at + 10), 12);
    return type && pair && text(u32(*type + 8)) == cls && string(u16(*pair + 8)) == name &&
           string(u16(*pair + 10)) == signature;
  };
  std::optional<size_t> found;
  for (size_t m = 0; m < methods; ++m) {
    size_t at = *table + m * 24;
    if (string(u16(at + 2)) != "doLoadMain") continue;
    auto body = pointer(u32(at + 20), 16);
    if (!body) return {};
    size_t size = u32(*body + 8);
    auto code = pointer(u32(*body + 12), size);
    if (!code) return {};
    for (size_t i = 0; i + 14 <= size; ++i) {
      size_t p = *code + i;
      if (bytes[p] != 0xB8 || bytes[p + 3] != 0x9D || bytes[p + 4] ||
          (bytes[p + 5] != 11 && bytes[p + 5] != 3) || bytes[p + 6] != 0x10 ||
          bytes[p + 7] != 10 || bytes[p + 8] != 0xB3 || bytes[p + 11] != 0xA7) continue;
      if (!member(u16(p + 1), 10, "fld/ppc", "getStoryMovieIndex", "()I") ||
          !member(u16(p + 9), 9, "WhiteResident", "LoadStep", "I")) continue;
      if (found) return {};  // An unexpected duplicate is not safe to guess at.
      found = p + 5;
    }
  }
  return found;
}

inline bool X2SetStoryRecapBranch(std::span<uint8_t> bytes, size_t at, bool skip) {
  if (at < 2 || at + 4 >= bytes.size() || bytes[at - 2] != 0x9D || bytes[at - 1] ||
      (bytes[at] != 11 && bytes[at] != 3) || bytes[at + 1] != 0x10 ||
      bytes[at + 2] != 10 || bytes[at + 3] != 0xB3) return false;
  // Both outcomes now reach LoadStep=10. The condition still consumes its
  // operand, preserving the VM stack and the normal post-load state machine.
  bytes[at] = skip ? 3 : 11;
  return true;
}
