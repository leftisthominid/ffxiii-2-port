#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <utility>

// PE virtual data beyond SizeOfRawData must start at zero on every load.
// Zone modules share guest addresses; an earlier module's code can otherwise
// survive in the next module's uninitialized globals.
inline size_t X2ClearModuleBss(std::span<uint8_t> image) {
  auto fits = [&](size_t at, size_t size) {
    return at <= image.size() && size <= image.size() - at;
  };
  auto u16 = [&](size_t at) { return uint16_t(image[at]) | uint16_t(image[at+1]) << 8; };
  auto u32 = [&](size_t at) {
    return uint32_t(image[at]) | uint32_t(image[at+1]) << 8 |
           uint32_t(image[at+2]) << 16 | uint32_t(image[at+3]) << 24;
  };
  if (!fits(0,64) || image[0]!='M' || image[1]!='Z') return 0;
  const size_t pe=u32(60);
  if (!fits(pe,24) || u32(pe)!=0x4550) return 0;
  const size_t count=u16(pe+6), optional=u16(pe+20), table=pe+24+optional;
  if (!count || count>96 || !fits(table,count*40)) return 0;
  std::array<std::pair<size_t,size_t>,96> ranges{};
  size_t used=0,total=0;
  for (size_t i=0;i<count;i++) {
    const size_t at=table+i*40;
    const uint32_t size=u32(at+8), rva=u32(at+12), raw=u32(at+16), flags=u32(at+36);
    // Export/relocation sections in a patched XEX can lie outside the retained
    // image. Only writable, non-executable data with a virtual tail is relevant.
    if (!(flags&0x80000000u) || (flags&0x20000000u) || size<=raw) continue;
    if (!fits(rva,size) || rva<table+count*40) return 0;
    ranges[used++]={size_t(rva)+raw,size-raw};
    total+=size-raw;
  }
  for (size_t i=0;i<used;i++) std::memset(image.data()+ranges[i].first,0,ranges[i].second);
  return total;
}
