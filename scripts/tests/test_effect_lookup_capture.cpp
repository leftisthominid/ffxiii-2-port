#include "../../project/src/x2_effect_lookup_capture.h"
#include <cassert>
#include <cstring>
#include <iostream>

int main() {
  std::vector<uint8_t> memory(0x20000);
  auto w32 = [&](uint32_t at, uint32_t value) {
    for (int i=0;i<4;i++) memory.at(at+i)=uint8_t(value>>(24-i*8));
  };
  auto read = [&](uint32_t at, uint8_t* out, size_t n) {
    if (uint64_t(at)+n>memory.size()) return false;
    std::memcpy(out,memory.data()+at,n);return true;
  };
  w32(0x10000,0x10100);w32(0x10108,0x10200);
  w32(0x1020C,0x10300);w32(0x10210,0x10400);
  w32(0x10310,0x10500);w32(0x10410,0x10600);
  w32(0x10504,0x10700);w32(0x10604,0x10800);
  w32(0x10900,0x00000001); // group 0, index 1
  auto result=X2CaptureEffectLookup(0x10000,0x10900,read);
  assert(result.group==0&&result.index==1&&result.resource==0x10700&&result.complete);
  assert(result.stage=="resource-header"&&result.ranges.back().bytes.size()==64);
  w32(0x10900,0xFFFF0001); // negative group -1 uses the other table
  result=X2CaptureEffectLookup(0x10000,0x10900,read);
  assert(result.group==-1&&result.resource==0x10800);
  w32(0x10900,0x00000001);w32(0x10504,0x01000000); // observed bad pointer
  result=X2CaptureEffectLookup(0x10000,0x10900,read);
  assert(result.resource==0x01000000&&result.stage=="resource-header"&&!result.complete);
  assert(result.ranges.back().name=="resource-array-prefix");
  w32(0x10000,0xFFFFFFFC);
  result=X2CaptureEffectLookup(0x10000,0x10900,read);
  assert(result.stage=="node"&&result.ranges.size()==2);
  result=X2CaptureEffectLookup(0x10000,0,read);
  assert(result.stage=="references"&&result.ranges.empty());
  std::cout<<"PASS effect capture: positive and negative tables, exact bad pointer, unreadable and overflow bounds\n";
}
