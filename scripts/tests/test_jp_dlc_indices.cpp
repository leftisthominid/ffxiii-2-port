#define main x2_jpvoice_cli_main
#include "../../converters/x2_jpvoice.cpp"
#undef main
#include <cassert>

int main() {
  const auto root = fs::temp_directory_path() /
      ("x2-jp-index-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  struct Cleanup { fs::path path; ~Cleanup() { std::error_code ec; fs::remove_all(path, ec); } } cleanup{root};
  const auto source = root / "installed", active = root / "japanese";
  fs::create_directories(source); fs::create_directories(active);
  const std::string list = "filelist_p0000071img_u.x360.bin", arc = "white_p0000071img_u.x360.bin";
  Bytes scd(160);
  memcpy(scd.data(), "SEDBSSCF", 8); wr32(&scd[8], 3, true); scd[12] = 1;
  wr16(&scd[14], 48, true); wr16(&scd[52], 1, true);
  wr32(&scd[60], 64, true); wr32(&scd[64], 96, true);
  wr32(&scd[108], 0xFFFFFFFF, true);  // a valid dummy stream, no game audio
  Bytes original(4096, 0); original[0] = 42;
  std::copy(scd.begin(), scd.end(), original.begin() + 2048);
  Bytes copy = original; copy.insert(copy.end(), scd.begin(), scd.end());
  write_file(source / arc, original); write_file(active / arc, copy);
  std::vector<PatchFile> files{{1,16,"vfx/material.trb",0,1},
                            {2,160,"sound/pack/usa/cszb/line.x360.scd",1,uint32_t(scd.size())}};
  write_filelist(source / list, files);
  write_file(active / ("source_" + list), read_file(source / list));
  auto put = [&](const std::vector<PatchFile>& changed) {
    write_filelist(active / list, changed);
    write_file(active / ("verified_" + list), read_file(active / list));
  };
  std::string reason;
  put(files); assert(verify_jp_dlc(source, active, "0000071", reason));
  auto changed = files; changed[1].sector = 2; put(changed);
  assert(verify_jp_dlc(source, active, "0000071", reason));
  auto bad = changed; bad[0].sector = 1; put(bad);
  assert(!verify_jp_dlc(source, active, "0000071", reason));
  bad = changed; bad[1].path = "sound/pack/usa/clzb/unrelated.x360.scd"; put(bad);
  assert(!verify_jp_dlc(source, active, "0000071", reason));
  bad = changed; bad.pop_back(); put(bad);
  assert(!verify_jp_dlc(source, active, "0000071", reason));
  bad = changed; bad[1].sector = 999999; put(bad);
  assert(!verify_jp_dlc(source, active, "0000071", reason));
  put(changed); copy[4096] = 0; write_file(active / arc, copy);
  assert(!verify_jp_dlc(source, active, "0000071", reason));
  copy[4096] = 'S'; write_file(active / arc, copy);
  assert(verify_jp_dlc(source, active, "0000071", reason));
  puts("PASS active Japanese index: unchanged assets, replacement SCD, nonvoice redirect, wrong identity, missing file, out-of-range entry, corrupt audio");
}
