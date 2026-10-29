#include "../../project/src/x2_voice_policy.h"
#include <cassert>
#include <iostream>
int main() {
  for (unsigned bits = 0; bits < 32; ++bits) {
    const bool jp=bits&1, tu=bits&2, base=bits&4, merged=bits&8, given=bits&16;
    const auto p=X2SelectVoicePolicy(jp,tu,base,merged,given);
    assert(p.use_title_update == (given || tu));
    assert(p.request_japanese_dlc == jp);
    assert(p.use_japanese_base == (jp && tu && base && merged && !given));
    assert(p.use_merged_update == p.use_japanese_base);
    const auto other=X2SelectVoicePolicy(!jp,tu,base,merged,given);
    assert(p.use_title_update == other.use_title_update);
  }
  std::cout << "PASS 32 startup combinations: title-update availability independent of language; DLC language independent of base patch\n";
}
