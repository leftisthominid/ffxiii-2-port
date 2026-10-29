#include "../../project/src/x2_jp_dlc_provenance.h"
#include <cassert>
#include <chrono>
#include <iostream>

int main() {
  namespace fs = std::filesystem;
  const auto root = fs::temp_directory_path() /
      ("x2-jp-provenance-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  struct Cleanup { fs::path path; ~Cleanup() { std::error_code ec; fs::remove_all(path, ec); } } cleanup{root};
  const auto source = root / "installed", copy = root / "japanese";
  fs::create_directories(source); fs::create_directories(copy);
  const std::string list = "filelist_p0000071img_u.x360.bin", arc = "white_p0000071img_u.x360.bin";
  auto write = [](const fs::path& p, const std::string& data) { std::ofstream f(p, std::ios::binary); f << data; assert(bool(f)); };
  const std::string original(140000, 'a');
  write(source / list, "original list"); write(source / arc, original);
  write(copy / list, "Japanese list"); write(copy / arc, original + "Japanese replacement audio");
  std::string reason;
  // A legacy copy cannot establish which installed package it came from.
  assert(!X2JpDlcCurrent(source, copy, "0000071", reason));
  write(copy / ("source_" + list), "original list");
  write(copy / ("verified_" + list), "Japanese list");
  uint64_t progress = 0;
  assert(X2JpDlcCurrent(source, copy, "0000071", reason, [&](uint64_t n) { progress = n; }));
  assert(progress == original.size());
  // Same-size content changes must be detected even if timestamps are preserved.
  const auto stamp = fs::last_write_time(source / arc);
  std::string changed = original; changed[70000] = 'b'; write(source / arc, changed);
  fs::last_write_time(source / arc, stamp);
  assert(!X2JpDlcCurrent(source, copy, "0000071", reason));
  write(source / arc, original);
  write(source / list, "different lst");
  assert(!X2JpDlcCurrent(source, copy, "0000071", reason));
  write(source / list, "original list");
  write(copy / list, "corrupted list");
  assert(!X2JpDlcCurrent(source, copy, "0000071", reason));
  write(copy / list, "Japanese list");
  write(copy / arc, original.substr(0, original.size() - 1));
  assert(!X2JpDlcCurrent(source, copy, "0000071", reason));
  write(copy / arc, original + "Japanese replacement audio");
  write(copy / "white_p0000071scr_u.x360.bin", "orphan archive");
  assert(!X2JpDlcCurrent(source, copy, "0000071", reason));
  fs::remove(copy / "white_p0000071scr_u.x360.bin");
  assert(X2JpDlcCurrent(source, copy, "0000071", reason));
  assert(!X2JpDlcCurrent(source, copy, "../evil", reason));
  assert(!X2JpDlcCurrent(source, copy, "0000072", reason));
  std::cout << "PASS DLC provenance: current, legacy, same-size mutation, list drift, truncation, partial pair, invalid ID\n";
}
