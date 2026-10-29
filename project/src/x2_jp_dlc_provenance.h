#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <string>

// The voice converter copies the installed archive verbatim, then appends
// Japanese replacements. Check that prefix and a snapshot of its input list
// before mounting the copy. This detects stale non-audio assets too, including
// same-size changes; timestamps and package IDs alone are not provenance.
inline bool X2JpDlcPrefix(const std::filesystem::path& source,
                         const std::filesystem::path& copy, bool exact,
                         const std::function<void(uint64_t)>& progress = {}) {
  std::error_code ec;
  const auto size = std::filesystem::file_size(source, ec);
  if (ec) return false;
  const auto copied = std::filesystem::file_size(copy, ec);
  if (ec || (exact ? copied != size : copied < size)) return false;
  std::ifstream a(source, std::ios::binary), b(copy, std::ios::binary);
  if (!a || !b) return false;
  std::array<char, 65536> left{}, right{};
  uint64_t done = 0;
  while (done < size) {
    const auto count = std::streamsize(std::min<uint64_t>(left.size(), size - done));
    a.read(left.data(), count); b.read(right.data(), count);
    if (a.gcount() != count || b.gcount() != count ||
        !std::equal(left.begin(), left.begin() + count, right.begin())) return false;
    done += uint64_t(count);
    if (progress) progress(done);
  }
  return true;
}

inline bool X2JpDlcCurrent(const std::filesystem::path& source,
                          const std::filesystem::path& copy,
                          const std::string& id, std::string& reason,
                          const std::function<void(uint64_t)>& progress = {}) {
  if (id.size() != 7 || id.find_first_not_of("0123456789") != std::string::npos) {
    reason = "invalid package ID"; return false;
  }
  bool found = false;
  for (const char* kind : {"img", "scr"}) {
    const std::string suffix = "p" + id + kind + "_u.x360.bin";
    const std::string list = "filelist_" + suffix, archive = "white_" + suffix;
    std::error_code ec;
    const bool has_list = std::filesystem::is_regular_file(copy / list, ec);
    ec.clear();
    const bool has_archive = std::filesystem::is_regular_file(copy / archive, ec);
    if (!has_list && !has_archive) continue;
    if (!has_list || !has_archive) { reason = "incomplete " + std::string(kind) + " copy"; return false; }
    found = true;
    if (!X2JpDlcPrefix(source / list, copy / ("source_" + list), true)) {
      reason = "source list changed or provenance missing: " + list; return false;
    }
    if (!X2JpDlcPrefix(copy / list, copy / ("verified_" + list), true)) {
      reason = "Japanese list changed or validation missing: " + list; return false;
    }
    if (!X2JpDlcPrefix(source / archive, copy / archive, false, progress)) {
      reason = "source archive changed or copy truncated: " + archive; return false;
    }
  }
  reason = found ? "current" : "no complete archive/list pairs";
  return found;
}
