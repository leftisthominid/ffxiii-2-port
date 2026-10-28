// ReXGlue 0.10 registers a recompiled module under the spelling passed to
// LoadUserModule, but tears it down under UserModule::path() (the VFS device
// path). Use that same resolved path for both operations. A stale registration
// otherwise prevents the next zone, which shares image base 0x88800000, loading.
#pragma once

#include <algorithm>
#include <cctype>
#include <mutex>
#include <string>
#include <string_view>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/vfs.h>
#include <rex/system/kernel_state.h>

inline std::string X2ModuleLoadPath(rex::system::KernelState* kernel,
                                    std::string_view requested) {
  std::string name(requested.substr(requested.find_last_of("/\\:") + 1));
  std::transform(name.begin(), name.end(), name.begin(),
                 [](unsigned char c) { return char(std::tolower(c)); });
  if (name != "z0070.xex" && name != "z0130.xex" &&
      name != "z0160.xex" && name != "z0161.xex") return std::string(requested);
  // The game normally supplies GAME:\\name; also accept its bare filenames.
  const std::string lookup = requested.find_first_of("/\\:") == std::string_view::npos
                                 ? "GAME:\\" + std::string(requested)
                                 : std::string(requested);
  auto* entry = kernel->file_system()->ResolvePath(lookup);
  if (!entry) return std::string(requested);
  const std::string resolved = entry->absolute_path();
  static std::mutex mutex;
  std::lock_guard<std::mutex> lock(mutex);
  if (!kernel->FindRecompiledModule(resolved)) {
    const std::string library = "ffxiii2_" + name.substr(0, 5);
    kernel->RegisterRecompiledModule(name.c_str(), resolved.c_str(), library.c_str());
  }
  return resolved;
}
