// Compile with -std=c++17 -pthread; no game media or SDK is required.
#define main x2_jpvoice_cli_main
#include "../../converters/x2_jpvoice.cpp"
#undef main
#include <cassert>

int main() {
  struct Ref { const Entry* e = nullptr; };
  Entry english, correct, collision;
  english.code = 123; english.ftype = 160;
  english.path = "sound/pack/usa/cszb/line.x360.scd";
  correct.code = 456; correct.ftype = 160;
  correct.path = "sound/pack/cszb/line.win32.scd";
  collision.code = 123; collision.ftype = 160;
  collision.path = "sound/pack/clzb/unrelated.win32.scd";
  std::map<std::string, Ref> japanese{
      {correct.path, {&correct}}, {collision.path, {&collision}}};
  // A different Japanese file code is valid when the normalized path matches.
  // The old code-first selection chose collision instead.
  assert(japanese_voice(english, japanese).e == &correct);
  japanese.erase(correct.path);
  assert(japanese_voice(english, japanese).e == nullptr);
  japanese.emplace(correct.path, Ref{&correct});
  correct.ftype = 16;
  assert(japanese_voice(english, japanese).e == nullptr);
  correct.ftype = 160;
  english.path = "SOUND/PACK/USA/CSZB/LINE.X360.SCD";
  assert(japanese_voice(english, japanese).e == &correct);
  english.path = "mot/mon/lsdpack_us.bin";
  assert(japanese_voice(english, japanese).e == nullptr);
  puts("PASS Japanese voice identity: path match, code collision, missing path, type, case, non-voice");
}
