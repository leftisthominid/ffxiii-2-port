#include "x2_story_recaps.h"
#include <mutex>
#include <rex/hook.h>
#include <rex/logging/macros.h>

int X2ConfigGetSkipStoryRecaps();
namespace {
struct LoadingClass { uint32_t address = 0, size = 0; };
thread_local LoadingClass loading;
std::mutex patch_mutex;
std::span<uint8_t> resident;
size_t branch = 0;
}

void X2ApplyStoryRecapSetting(int skip) {
  std::lock_guard guard(patch_mutex);
  if (resident.empty()) return;
  if (!X2SetStoryRecapBranch(resident, branch, skip != 0)) {
    resident = {};
    REXLOG_WARN("XIII-2 Story Recaps: load-script layout changed; branch not modified");
  }
}

REX_EXTERN(__imp__sub_828250D0);
REX_HOOK_RAW(sub_828250D0) {
  const auto previous = loading;
  struct Restore { LoadingClass value; ~Restore() { loading = value; } } restore{previous};
  loading = {ctx.r3.u32, ctx.r4.u32};
  __imp__sub_828250D0(ctx, base);
}

REX_EXTERN(__imp__sub_828248A8);
REX_HOOK_RAW(sub_828248A8) {
  const uint32_t address = ctx.r3.u32;
  __imp__sub_828248A8(ctx, base);
  if (address != loading.address || loading.size < 56 || loading.size > 4 * 1024 * 1024 ||
      uint64_t(address) + loading.size > 0x100000000ULL) return;
  std::span<uint8_t> bytes(base + address, loading.size);
  auto at = X2FindStoryRecapBranch(bytes, address);
  if (!at) return;
  const bool skip = X2ConfigGetSkipStoryRecaps() != 0;
  std::lock_guard guard(patch_mutex);
  resident = bytes;
  branch = *at;
  X2SetStoryRecapBranch(resident, branch, skip);
  REXLOG_INFO("XIII-2 Story Recaps: load-script presentation {} (intro, narration and recap scene)",
              skip ? "disabled" : "enabled");
}
