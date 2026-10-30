// ffxiii2 - frame pacing and speed fixes from the Lightning Returns port (lr_hooks.cpp). The
// functions are the same code in both games (the XIII-2 addresses were matched instruction for
// instruction):
//   timer resolution     1 ms Windows timer for the whole process
//   frame-pacing fence   sub_828572D0 (LR sub_829CA1B8) called from sub_828881D8 (sub_82A82278)
//   GPU wait step        sub_82856928 (sub_829C9810)
//   copies               sub_82B62A38 (sub_82D3AC88), sub_82B62988 (sub_82D3ABD8),
//                        sub_82B628D8 (sub_82D3AB28), sub_82B62AC0 (sub_82D3AD10),
//                        sub_82B50230 (sub_82D1ED20)
// Each can be turned off as in Lightning Returns: LR_FENCE_FIX=0, LR_GPU_WAIT_YIELD=0,
// LR_COPY_INVALIDATE=0, LR_NATIVE_MEMCPY=0 (LR_NATIVE_COPY_AD10=1 makes the sound copy native).

#include <atomic>
#include <cctype>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <thread>

#include <rex/hook.h>
#include <rex/logging/macros.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>
#include <rex/thread/mutex.h>
#include "x2_audio_pool.h"
#include "x2_effect_lookup_capture.h"

#if defined(_M_X64) || defined(__x86_64__)
#include <immintrin.h>
#endif
#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace {
uint32_t ld32(uint8_t* base, uint32_t a) { return __builtin_bswap32(*reinterpret_cast<uint32_t*>(base + a)); }

bool env_off(const char* name) {
  const char* v = std::getenv(name);
  return v && v[0] == '0';
}
}  // namespace

// ---------------------------------------------------------------------------------------
// Frame pacing. The emulated 360 vblank (60 Hz, which the game counts to hold 30 fps) is
// produced by a thread that sleeps 1 ms at a time, and the GPU thread sleeps in whole
// milliseconds while it waits for the game. Windows rounds a 1 ms sleep up to its timer
// period, 15.6 ms unless the process asks for 1 ms - so vblanks arrived in 15-16 ms lumps
// and frames alternated between ~31 and ~47 ms. Ask for a 1 ms timer for the whole process.
// ---------------------------------------------------------------------------------------
#if defined(_WIN32)
namespace {
struct X2TimerResolution {
  X2TimerResolution() {
    if (HMODULE winmm = LoadLibraryW(L"winmm.dll")) {
      using TimeBeginPeriod = UINT(WINAPI*)(UINT);
      if (auto begin = reinterpret_cast<TimeBeginPeriod>(GetProcAddress(winmm, "timeBeginPeriod"))) begin(1);
    }
    // Windows 11 lowers the timer resolution of processes it considers in the background
    // unless they opt out.
    PROCESS_POWER_THROTTLING_STATE state{};
    state.Version = PROCESS_POWER_THROTTLING_CURRENT_VERSION;
    state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED | 0x4;  // 0x4: IGNORE_TIMER_RESOLUTION
    state.StateMask = 0;
    SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling, &state, sizeof(state));
  }
} g_x2_timer_resolution;
}  // namespace
#endif

// ---------------------------------------------------------------------------------------
// Frame-pacing fence deadlock (Lightning Returns: the freeze on Continue / loading, and in
// gameplay). sub_828881D8 takes the spinlock at [0x82E9A434]+0x1AD8 and, while holding it,
// calls sub_828572D0 to insert a GPU fence. That function takes one of the device's fence slots
// and, when none is free, spins until one is. Slots are freed only by the periodic callback,
// which first takes the same spinlock - so once the slots run out the game deadlocks. They run
// out because sub_828881D8 stores each new fence into +0x1AD0 over the previous one while the
// callback has not caught up (loading screens, slow frames), leaking a slot every time.
// Here: while a fence from sub_828881D8 is still pending it is kept instead of taking another
// slot, and a new one is taken in the game's own no-wait mode (returns 0 = no fence when the
// slots are full) instead of spinning with the lock held.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_828572D0);
REX_HOOK_RAW(sub_828572D0) {
  static const bool off = env_off("LR_FENCE_FIX");
  if (uint32_t(ctx.lr) == 0x828881D8 + 0x78) {  // the call in sub_828881D8 (returns to 82888250)
    const uint32_t pacing = ld32(base, 0x82E9A434);
    const uint64_t pending = pacing ? __builtin_bswap64(*reinterpret_cast<uint64_t*>(base + pacing + 0x1AD0)) : 0;
    if (pending) {
      // The previous frame's fence was not given back yet (that frame finished late).
      static std::atomic<uint32_t> late{0};
      const uint32_t n = ++late;
      if ((n & (n - 1)) == 0) {
        if (off) REXLOG_INFO("XIII-2 fence: {} GPU fence slot(s) leaked by late frames (LR_FENCE_FIX=0)", n);
        else REXLOG_INFO("XIII-2 fence: a frame finished late {}x; its GPU fence slot was reused instead of leaked", n);
      }
      if (!off) {
        ctx.r3.u64 = pending;
        return;
      }
    }
    if (!off) ctx.r8.u64 |= 2;
  }
  __imp__sub_828572D0(ctx, base);
}

// ---------------------------------------------------------------------------------------
// sub_82856928 is the game's GPU wait step (D3D): it checks the command processor's
// write-back once and returns 1 to be called again, 0 to give up. On the console it starts
// with cctpl / db16cyc x32 / cctpm, which lower the waiting hardware thread's priority; the
// recompiled code drops those, so every GPU wait was a full-speed busy loop competing with the
// renderer's own thread for a core. The first polls only pause; after that the thread yields
// its time slice between polls.
// ---------------------------------------------------------------------------------------
REX_EXTERN(__imp__sub_82856928);
REX_HOOK_RAW(sub_82856928) {
  static const bool yield = !env_off("LR_GPU_WAIT_YIELD");
  thread_local uint32_t wait_state = 0, polls = 0;
  const uint32_t state = ctx.r3.u32;
  __imp__sub_82856928(ctx, base);
  if (!yield || !ctx.r3.u32) {
    polls = 0;
    return;
  }
  if (state != wait_state) {
    wait_state = state;
    polls = 0;
  }
  if (++polls < 64) {
#if defined(_M_X64) || defined(__x86_64__)
    for (int i = 0; i < 16; ++i) _mm_pause();
#endif
  } else {
    std::this_thread::yield();
  }
}

// ---------------------------------------------------------------------------------------
// Copies. sub_82B62A38 is the game's memcpy for 256 bytes and up (XMemCpy, or a write-combined
// copy for physical memory); the game moves its GPU resources around with it while streaming.
// Every first write to memory the renderer watches is an access violation + page unprotect; a
// large copy into physical memory now tells the renderer about the whole destination at once.
// The copy loops themselves (sub_82B62988, sub_82B628D8, sub_82B62AC0, sub_82B50230) are plain
// copies that return the destination, so the host's memmove does the same faster; overlapping
// ranges run the game's own copy (it runs forward and zeroes cache lines ahead: other bytes
// than memmove there). sub_82B62AC0 is the sound engine's copy: native only with
// LR_NATIVE_COPY_AD10=1 (in Lightning Returns audio popped with it native).
// ---------------------------------------------------------------------------------------
namespace {
bool native_memcpy() {
  static const bool enabled = !env_off("LR_NATIVE_MEMCPY");
  return enabled;
}

// From 64 KB: the notification takes the runtime's global lock even where nothing is watched.
void copy_unwatch(uint32_t dst, uint32_t size) {
  if (size < 0x10000 || dst < 0xA0000000u || size > 0x20000000u - (dst & 0x1FFFFFFFu)) return;
  if (auto* kernel_state = REX_KERNEL_STATE()) {
    if (auto* memory = kernel_state->memory()) {
      memory->TriggerPhysicalMemoryCallbacks(rex::thread::global_critical_region::AcquireDirect(), dst, size, true,
                                             true);
    }
  }
}

bool forward_overlap(uint32_t dst, uint32_t src, uint32_t size) {
  return size && dst < uint64_t(src) + size && src < uint64_t(dst) + size;
}

// Returns true when the copy was done natively.
bool native_copy(PPCContext& ctx, uint8_t* base, bool unwatch) {
  if (!native_memcpy()) return false;
  const uint32_t dst = ctx.r3.u32, src = ctx.r4.u32, size = ctx.r5.u32;
  if (forward_overlap(dst, src, size)) return false;
  if (unwatch) copy_unwatch(dst, size);
  if (size) std::memmove(base + dst, base + src, size);
  ctx.r3.u64 = dst;
  return true;
}
}  // namespace

REX_EXTERN(__imp__sub_82B62A38);
REX_HOOK_RAW(sub_82B62A38) {
  static const bool enabled = !env_off("LR_COPY_INVALIDATE");
  if (enabled) copy_unwatch(ctx.r3.u32, ctx.r5.u32);
  __imp__sub_82B62A38(ctx, base);
}

REX_EXTERN(__imp__sub_82B62988);
REX_HOOK_RAW(sub_82B62988) {
  if (!native_copy(ctx, base, true)) __imp__sub_82B62988(ctx, base);
}

REX_EXTERN(__imp__sub_82B628D8);
REX_HOOK_RAW(sub_82B628D8) {
  if (!native_copy(ctx, base, true)) __imp__sub_82B628D8(ctx, base);
}

REX_EXTERN(__imp__sub_82B62AC0);
REX_HOOK_RAW(sub_82B62AC0) {
  static const bool native = [] {
    const char* v = std::getenv("LR_NATIVE_COPY_AD10");
    return v && v[0] == '1';
  }();
  if (!native || !native_copy(ctx, base, true)) __imp__sub_82B62AC0(ctx, base);
}

REX_EXTERN(__imp__sub_82B50230);
REX_HOOK_RAW(sub_82B50230) {
  if (!native_copy(ctx, base, false)) __imp__sub_82B50230(ctx, base);
}

// ---------------------------------------------------------------------------------------
// Controller input, one call at a time. The game polls the pad from jobs that can run on
// several threads at once, and the runtime's input system rebuilds its device list on every
// call without a lock, so two polls at the same moment corrupt the heap (Lightning Returns: an
// abort in InputSystem::RefreshDevices in a long run). On the 360 these calls are thread safe.
// Every call reaches the runtime through these three wrappers: sub_82B62530 (XInputGetState),
// sub_82B62540 (XInputGetCapabilities + XInputSetState), sub_82B62608 (XInputGetKeystroke).
// ---------------------------------------------------------------------------------------
#include <mutex>

namespace {
std::mutex g_input_mutex;

std::unique_lock<std::mutex> input_lock() {
  std::unique_lock<std::mutex> lock(g_input_mutex, std::try_to_lock);
  if (!lock.owns_lock()) {
    static std::atomic<uint32_t> collisions{0};
    const uint32_t n = collisions.fetch_add(1) + 1;
    if (n == 1 || n == 100 || n == 10000)
      REXLOG_INFO("XIII-2 input: two game threads read the controller at the same time (seen {}x); the calls now take turns",
                  n);
    lock.lock();
  }
  return lock;
}
}  // namespace

#if defined(__ANDROID__)
void X2TouchMerge(uint8_t* base, uint32_t state, PPCContext& ctx);                            // x2_android.cpp
bool X2TouchKeystroke(uint8_t* base, uint32_t user, uint32_t keystroke, PPCContext& ctx);  // x2_android.cpp
#endif

REX_EXTERN(__imp__sub_82B62530);
REX_HOOK_RAW(sub_82B62530) {
  auto lock = input_lock();
  const uint32_t user = ctx.r3.u32, state = ctx.r4.u32;
  const PPCContext saved = ctx;
  __imp__sub_82B62530(ctx, base);
  // Controller 1 not connected, but another one is: the game reads that one (as in Lightning
  // Returns). The input system keeps each device's slot, and Android announces a USB controller
  // twice when the game starts (its own controller path, then SDL's USB driver, which then drops
  // the first), so the controller that remains can sit in slot 2.
  if ((user & 0xFF) == 0 && state && ctx.r3.u32 != 0) {
    for (uint32_t other = 1; other < 4; ++other) {
      PPCContext c = saved;
      c.r3.u64 = other;
      c.r4.u64 = state;
      __imp__sub_82B62530(c, base);
      if (c.r3.u32 == 0) {
        ctx.r3.u64 = 0;
        static std::atomic<uint32_t> logged{~0u};
        if (logged.exchange(other) != other)
          REXLOG_INFO("XIII-2 input: no controller in slot 1; reading the one in slot {}", other + 1);
        break;
      }
    }
  }
#if defined(__ANDROID__)
  // On-screen controls and the Historia Crux menu (android/project X2Touch.java).
  if ((user & 0xFF) == 0 && state) X2TouchMerge(base, state, ctx);
#endif
}

REX_EXTERN(__imp__sub_82B62540);
REX_HOOK_RAW(sub_82B62540) {
  auto lock = input_lock();
  __imp__sub_82B62540(ctx, base);
}

REX_EXTERN(__imp__sub_82B62608);
REX_HOOK_RAW(sub_82B62608) {
  auto lock = input_lock();
#if defined(__ANDROID__)
  const uint32_t user = ctx.r3.u32, keystroke = ctx.r5.u32;
  __imp__sub_82B62608(ctx, base);
  X2TouchKeystroke(base, user, keystroke, ctx);
#else
  __imp__sub_82B62608(ctx, base);
#endif
}

// ---------------------------------------------------------------------------------------
// A crash nothing handled (as in Lightning Returns, lr_crash_filter): where it happened and
// how the game got there, logged before the process ends. The host address is matched to
// the recompiled game function it lies in (PPCFuncMappings, sorted by host address), the
// host stack is unwound the same way, and the guest stack is walked from r1 (each frame's
// back chain and saved return address).
// ---------------------------------------------------------------------------------------
#if defined(_WIN32) && (defined(_M_X64) || defined(__x86_64__))
#include <algorithm>
#include <string>
#include <utility>
#include <vector>

#include <rex/ppc/func.h>

void X2LogGamePrints(const char* tag);

namespace {
const std::vector<std::pair<uintptr_t, uint32_t>>& host_functions() {
  static const std::vector<std::pair<uintptr_t, uint32_t>> table = [] {
    std::vector<std::pair<uintptr_t, uint32_t>> t;
    for (const PPCFuncMapping* m = PPCFuncMappings; m->host; ++m)
      t.emplace_back(reinterpret_cast<uintptr_t>(m->host), uint32_t(m->guest));
    std::sort(t.begin(), t.end());
    return t;
  }();
  return table;
}

// "exe+OFFSET (in sub_X)" or "module+OFFSET" for one host code address.
std::string describe_host(uintptr_t pc) {
  HMODULE module = nullptr;
  GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                     reinterpret_cast<LPCWSTR>(pc), &module);
  std::string out;
  if (module == GetModuleHandleW(nullptr)) {
    out = fmt::format("exe+{:X}", pc - uintptr_t(module));
    const auto& t = host_functions();
    auto it = std::upper_bound(t.begin(), t.end(), std::make_pair(pc, ~0u));
    if (it != t.begin()) {
      --it;
      // Within 1 MB of the function's start, else the address is in other exe code.
      if (pc - it->first < (1u << 20)) out += fmt::format(" (in sub_{:08X}+{:X})", it->second, pc - it->first);
    }
  } else if (module) {
    wchar_t path[MAX_PATH] = {};
    GetModuleFileNameW(module, path, MAX_PATH);
    std::wstring w(path);
    std::string name;
    for (wchar_t c : w.substr(w.find_last_of(L"\\/") + 1)) name += char(c < 128 ? c : '?');
    out = fmt::format("{}+{:X}", name, pc - uintptr_t(module));
  } else {
    out = fmt::format("{:016X}", uint64_t(pc));
  }
  return out;
}

bool read_guest32(uint8_t* base, uint32_t address, uint32_t& out) {
  uint32_t value = 0;
  SIZE_T read = 0;
  if (address & 3) return false;
  if (!ReadProcessMemory(GetCurrentProcess(), base + address, &value, 4, &read) || read != 4) return false;
  out = __builtin_bswap32(value);
  return true;
}

std::string guest_stack(uint8_t* base, uint32_t sp) {
  std::string out;
  for (int depth = 0; base && depth < 24; ++depth) {
    if (sp < 0x10000000 || sp >= 0xE0000000 || (sp & 3)) break;
    uint32_t caller_sp, ret;
    if (!read_guest32(base, sp, caller_sp)) break;
    if (caller_sp <= sp || caller_sp - sp > (1u << 20)) break;
    if (!read_guest32(base, caller_sp - 8, ret)) break;
    if (ret >= 0x82000000 && ret < 0x83400000) out += fmt::format(" {:08X}", ret);
    sp = caller_sp;
  }
  return out.empty() ? " (unreadable)" : out;
}

// The return addresses of a host stack (no allocation: also used on a suspended thread).
int host_pcs(const CONTEXT* start, uintptr_t* pcs, int max) {
  CONTEXT c = *start;
  int n = 0;
  while (n < max && c.Rip) {
    pcs[n++] = uintptr_t(c.Rip);
    DWORD64 image_base = 0;
    PRUNTIME_FUNCTION fn = RtlLookupFunctionEntry(c.Rip, &image_base, nullptr);
    if (!fn) {
      // Leaf function: the return address is on top of the stack.
      if (!c.Rsp) break;
      c.Rip = *reinterpret_cast<DWORD64*>(c.Rsp);
      c.Rsp += 8;
      continue;
    }
    void* handler_data = nullptr;
    DWORD64 frame = 0;
    RtlVirtualUnwind(UNW_FLAG_NHANDLER, image_base, c.Rip, fn, &c, &handler_data, &frame, nullptr);
  }
  return n;
}

std::string describe_pcs(const uintptr_t* pcs, int n) {
  std::string out;
  for (int i = 0; i < n; ++i) out += "\n    " + describe_host(pcs[i]);
  return out;
}

std::string host_stack(const CONTEXT* start) {
  uintptr_t pcs[24];
  return describe_pcs(pcs, host_pcs(start, pcs, 24));
}

// The text a guest register points to, when it is printable (resource names and the like).
std::string guest_text(uint8_t* base, uint32_t address) {
  if (!base || address < 0x10000) return {};
  char buf[64] = {};
  SIZE_T read = 0;
  if (!ReadProcessMemory(GetCurrentProcess(), base + address, buf, sizeof(buf) - 1, &read) || read == 0) return {};
  std::string s;
  for (SIZE_T i = 0; i < read && buf[i]; ++i) {
    if (buf[i] < 0x20 || buf[i] > 0x7E) return {};
    s.push_back(buf[i]);
  }
  return s.size() >= 3 ? s : std::string();
}

void log_guest_regs(const char* tag, const PPCContext* c) {
  uint8_t* base = rex::system::kernel_state() ? rex::system::kernel_state()->memory()->virtual_membase() : nullptr;
  if (c) {
    const PPCRegister* rs[32] = {&c->r0,  &c->r1,  &c->r2,  &c->r3,  &c->r4,  &c->r5,  &c->r6,  &c->r7,
                                 &c->r8,  &c->r9,  &c->r10, &c->r11, &c->r12, &c->r13, &c->r14, &c->r15,
                                 &c->r16, &c->r17, &c->r18, &c->r19, &c->r20, &c->r21, &c->r22, &c->r23,
                                 &c->r24, &c->r25, &c->r26, &c->r27, &c->r28, &c->r29, &c->r30, &c->r31};
    std::string regs, texts;
    for (int r = 0; r < 32; ++r) {
      if (r == 1) continue;
      regs += fmt::format(" r{}={:08X}", r, rs[r]->u32);
      const std::string s = guest_text(base, rs[r]->u32);
      if (!s.empty()) texts += fmt::format(" r{}='{}'", r, s);
      if (s == "SEDBmtb") {
        // Preserve the live byte order and section table. The source motion
        // can be valid while an in-place runtime conversion is incomplete.
        uint8_t header[256] = {};
        SIZE_T count = 0;
        if (ReadProcessMemory(GetCurrentProcess(), base + rs[r]->u32, header, sizeof(header), &count)) {
          std::string hex;
          for (SIZE_T i = 0; i < count; ++i) hex += fmt::format("{:02X}", header[i]);
          REXLOG_ERROR("{}: live motion header r{} at {:08X}: {}", tag, r, rs[r]->u32, hex);
        }
      }
    }
    REXLOG_ERROR("{}: guest lr {:08X} r1 {:08X};{}", tag, uint32_t(c->lr), c->r1.u32, regs);
    if (base && uint32_t(c->lr) == 0x829E9068u) {
      // The Valhalla failure dereferences an effect resource selected by the
      // pair at r27+4. Preserve the actual record and surrounding context, rather
      // than guessing their schema from unrelated eight-byte file records.
      // ReadProcessMemory also handles a damaged pointer without another fault.
      struct Range { const char* name; uint32_t address; size_t size; };
      const Range ranges[] = {{"record-r27", c->r27.u32, 64}, {"references-r4", c->r4.u32, 16},
                              {"object-r3", c->r3.u32, 256}, {"context-r25", c->r25.u32, 128},
                              {"context-r31", c->r31.u32, 256}};
      for (const auto& range : ranges) {
        if (range.address < 0x10000 || uint64_t(range.address) + range.size > 0x100000000ull) continue;
        uint8_t bytes[256] = {};
        SIZE_T count = 0;
        const bool complete = ReadProcessMemory(GetCurrentProcess(), base + range.address,
                                                 bytes, range.size, &count) != 0;
        std::string hex;
        for (SIZE_T i = 0; i < count && i < sizeof(bytes); ++i) hex += fmt::format("{:02X}", bytes[i]);
        REXLOG_ERROR("{}: effect lookup {} at {:08X}, {}/{} bytes{}: {}", tag, range.name, range.address,
                     count, range.size, complete ? "" : " (unreadable/partial)", hex);
      }
      const auto lookup = X2CaptureEffectLookup(c->r30.u32, c->r27.u32 + 4,
          [&](uint32_t address, uint8_t* bytes, size_t size) {
            SIZE_T count = 0;
            return ReadProcessMemory(GetCurrentProcess(), base + address, bytes, size, &count) && count == size;
          });
      REXLOG_ERROR("{}: effect selected group {} index {} resource {:08X}, capture {} at {}",
                   tag, lookup.group, lookup.index, lookup.resource, lookup.complete ? "complete" : "incomplete", lookup.stage);
      if (lookup.source_bounds_known) {
        REXLOG_ERROR("{}: effect source declares {} resources; selected index {} is {} its bounds",
                     tag, lookup.source_resource_count, lookup.index,
                     lookup.index_in_source_bounds ? "inside" : "outside");
      } else {
        REXLOG_ERROR("{}: effect source resource count unavailable; index bounds unknown", tag);
      }
      for (const auto& range : lookup.ranges) {
        std::string hex;
        for (auto byte : range.bytes) hex += fmt::format("{:02X}", byte);
        REXLOG_ERROR("{}: effect chain {} at {:08X}: {}", tag, range.name, range.address, hex);
      }
    }
    if (!texts.empty()) REXLOG_ERROR("{}: registers pointing to text:{}", tag, texts);
    REXLOG_ERROR("{}: guest calls (return addresses):{}", tag, guest_stack(base, c->r1.u32));
  } else {
    REXLOG_ERROR("{}: not a game thread", tag);
  }
}

void log_guest_state(const char* tag) {
  rex::runtime::ThreadState* ts = rex::runtime::ThreadState::Get();
  log_guest_regs(tag, ts ? ts->context() : nullptr);
}

// A read of the guest's first 64 KB (a null pointer plus a field offset). Lightning Returns
// never did this; XIII-2 did once while loading a save into an area (the game thread, about 9 s
// after the load started). Emulators make that page read as zeros (Xenia's protect_zero=false)
// because 360 games can get there through timing the emulation does not match. The first such
// read is logged with where it came from, the page becomes readable (zeros; writes still
// fault), and the game goes on. X2_NULL_READ_CRASH=1 keeps the crash instead.
bool recover_null_read(EXCEPTION_POINTERS* info) {
  const EXCEPTION_RECORD* rec = info->ExceptionRecord;
  if (rec->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || rec->NumberParameters < 2) return false;
  if (rec->ExceptionInformation[0] != 0) return false;  // writes are real crashes
  if (const char* v = std::getenv("X2_NULL_READ_CRASH"); v && v[0] == '1') return false;
  auto* kernel_state = rex::system::kernel_state();
  uint8_t* base = kernel_state ? kernel_state->memory()->virtual_membase() : nullptr;
  const uintptr_t at = uintptr_t(rec->ExceptionInformation[1]);
  if (!base || at < uintptr_t(base) || at >= uintptr_t(base) + 0x10000) return false;
  static std::atomic<int> recovered{0};
  if (recovered.fetch_add(1) > 0) return false;  // already readable: something else
  REXLOG_ERROR("XIII-2 null read: guest {:08X} read at {}; the zero page now reads as zeros and the game goes on",
               uint32_t(at - uintptr_t(base)), describe_host(uintptr_t(rec->ExceptionAddress)));
  log_guest_state("XIII-2 null read");
  REXLOG_ERROR("XIII-2 null read: host calls:{}", host_stack(info->ContextRecord));
  DWORD old = 0;
  if (!VirtualProtect(base, 0x10000, PAGE_READONLY, &old)) {
    REXLOG_ERROR("XIII-2 null read: could not make the zero page readable (error {})", GetLastError());
    ::rex::FlushLogging();
    return false;
  }
  ::rex::FlushLogging();
  return true;
}

LPTOP_LEVEL_EXCEPTION_FILTER g_previous_crash_filter = nullptr;
LONG WINAPI x2_crash_filter(EXCEPTION_POINTERS* info) {
  if (recover_null_read(info)) return EXCEPTION_CONTINUE_EXECUTION;
  static std::atomic<int> once{0};
  if (once.fetch_add(1) == 0) {
    const EXCEPTION_RECORD* rec = info->ExceptionRecord;
    std::string what = fmt::format("exception {:08X}", uint32_t(rec->ExceptionCode));
    if (rec->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && rec->NumberParameters >= 2)
      what = fmt::format("access violation ({} of host {:016X})", rec->ExceptionInformation[0] ? "write" : "read",
                         uint64_t(rec->ExceptionInformation[1]));
    REXLOG_ERROR("XIII-2 crash: {} at {}", what, describe_host(uintptr_t(rec->ExceptionAddress)));
    log_guest_state("XIII-2 crash");
    REXLOG_ERROR("XIII-2 crash: host calls:{}", host_stack(info->ContextRecord));
    X2LogGamePrints("XIII-2 crash");
    ::rex::FlushLogging();
  }
  return g_previous_crash_filter ? g_previous_crash_filter(info) : EXCEPTION_CONTINUE_SEARCH;
}

// A [FATAL] of the runtime (a call to guest address 0 and the like) ends in abort(), which no
// exception filter sees: the same report, from the thread that aborted.
void x2_abort_handler(int) {
  static std::atomic<int> once{0};
  if (once.fetch_add(1) == 0) {
    REXLOG_ERROR("XIII-2 crash: abort (the [FATAL] line above)");
    log_guest_state("XIII-2 crash");
    CONTEXT here{};
    RtlCaptureContext(&here);
    REXLOG_ERROR("XIII-2 crash: host calls:{}", host_stack(&here));
    X2LogGamePrints("XIII-2 crash");
    ::rex::FlushLogging();
  }
}

// Hang report: when the game thread starts no frame for 8 s (it had started some), where it
// is: its host stack (which recompiled game functions, and a kernel wait or a spin) twice,
// 1 s apart, its guest registers and calls, and the game's last debug messages. Twice per run.
std::atomic<uint64_t> g_hang_frames{0};
std::atomic<const PPCContext*> g_hang_ctx{nullptr};
std::atomic<DWORD> g_hang_tid{0};

void log_thread_host_stack(const char* tag, DWORD tid) {
  HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, tid);
  if (!h) {
    REXLOG_ERROR("{}: could not open the game thread (error {})", tag, GetLastError());
    return;
  }
  if (SuspendThread(h) != DWORD(-1)) {
    // only unwound while it is stopped; described (allocating) after it runs again
    CONTEXT c{};
    c.ContextFlags = CONTEXT_FULL;
    uintptr_t pcs[32];
    const int n = GetThreadContext(h, &c) ? host_pcs(&c, pcs, 32) : -1;
    ResumeThread(h);
    REXLOG_ERROR("{}: host calls:{}", tag, n < 0 ? std::string(" (no context)") : describe_pcs(pcs, n));
  }
  CloseHandle(h);
}

void hang_watch() {
  uint64_t last = 0;
  auto last_change = std::chrono::steady_clock::now();
  int reports = 0;
  bool reported = false;
  for (;;) {
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    const uint64_t frames = g_hang_frames.load(std::memory_order_relaxed);
    const auto now = std::chrono::steady_clock::now();
    if (frames != last) {
      last = frames;
      last_change = now;
      reported = false;
      continue;
    }
    if (!frames || reported || reports >= 2 || now - last_change < std::chrono::seconds(8)) continue;
    reported = true;
    ++reports;
    REXLOG_ERROR("XIII-2 hang: the game thread started no frame for 8 s (after {} frames)", frames);
    const DWORD tid = g_hang_tid.load();
    log_thread_host_stack("XIII-2 hang", tid);
    log_guest_regs("XIII-2 hang", g_hang_ctx.load());
    X2LogGamePrints("XIII-2 hang");
    ::rex::FlushLogging();
    std::this_thread::sleep_for(std::chrono::seconds(1));
    log_thread_host_stack("XIII-2 hang (1 s later)", tid);
    ::rex::FlushLogging();
  }
}
}  // namespace

// Called by the frame hook (x2_hooks.cpp, sub_82910538) on the game thread, once per frame.
void X2HangFrame(const PPCContext* ctx) {
  if (!g_hang_tid.load(std::memory_order_relaxed)) {
    g_hang_tid.store(GetCurrentThreadId());
    g_hang_ctx.store(ctx);
  }
  g_hang_frames.fetch_add(1, std::memory_order_relaxed);
}

// Called from the frame hook: by then the runtime has installed its own handlers.
void X2InstallCrashReport() {
  static std::atomic<bool> installed{false};
  if (installed.exchange(true)) return;
  host_functions();  // sorted now, not inside the crash
  g_previous_crash_filter = SetUnhandledExceptionFilter(x2_crash_filter);
  std::signal(SIGABRT, x2_abort_handler);
  std::thread(hang_watch).detach();
  REXLOG_INFO("XIII-2 crash report: on ({} game functions)", host_functions().size());
}
#else
void X2InstallCrashReport() {}
void X2HangFrame(const PPCContext*) {}
#endif

// ---------------------------------------------------------------------------------------
// The game's debug print, sub_8255DE80(format, ...) (641 callers, for example the physics
// loader's "phy resource name %d:<%s>"; the retail game prints nothing unless a debug flag is
// set). Each message is formatted from the guest arguments (r4-r10, then the caller's
// parameter area) and kept: the first 300 are logged as they come ("XIII-2 game: ..."), the last 64
// are logged again when the game crashes. X2_GAME_PRINT=1 logs every one; =0 turns this off.
// ---------------------------------------------------------------------------------------
#include <cstdio>
#include <mutex>
#include <string>

#include <rex/ppc/func.h>

namespace {
std::mutex g_print_mutex;
std::string g_prints[64];
uint32_t g_print_count = 0;

std::string print_guest_cstr(uint8_t* base, uint32_t a, size_t max) {
  std::string s;
  if (a < 0x10000) return "(null)";
  for (size_t i = 0; i < max && base[a + i]; ++i) s.push_back(char(base[a + i]));
  return s;
}

uint64_t print_arg(PPCContext& ctx, uint8_t* base, int k) {
  switch (k) {
    case 1: return ctx.r4.u64;
    case 2: return ctx.r5.u64;
    case 3: return ctx.r6.u64;
    case 4: return ctx.r7.u64;
    case 5: return ctx.r8.u64;
    case 6: return ctx.r9.u64;
    case 7: return ctx.r10.u64;
    default: {
      const uint32_t at = ctx.r1.u32 + 0x10 + 8 * uint32_t(k);
      uint64_t v;
      std::memcpy(&v, base + at, 8);
      return __builtin_bswap64(v);
    }
  }
}

std::string format_guest(PPCContext& ctx, uint8_t* base, const std::string& fmt) {
  std::string out;
  int arg = 1;
  for (size_t i = 0; i < fmt.size() && out.size() < 600; ++i) {
    const char c = fmt[i];
    if (c != '%') {
      out.push_back(c);
      continue;
    }
    size_t j = i + 1;
    std::string spec = "%";
    while (j < fmt.size() && std::strchr("-+ #0", fmt[j])) spec += fmt[j++];
    while (j < fmt.size() && (std::isdigit(static_cast<unsigned char>(fmt[j])) || fmt[j] == '.')) spec += fmt[j++];
    bool wide = false;
    while (j < fmt.size() && std::strchr("hlLqjzt", fmt[j])) wide |= fmt[j++] == 'l';
    if (j >= fmt.size()) break;
    const char conv = fmt[j];
    char buf[256];
    buf[0] = 0;
    if (conv == '%') {
      out.push_back('%');
    } else if (conv == 's') {
      out += print_guest_cstr(base, uint32_t(print_arg(ctx, base, arg++)), 200);
    } else if (std::strchr("dic", conv)) {
      const uint64_t v = print_arg(ctx, base, arg++);
      std::snprintf(buf, sizeof(buf), (spec + "ll" + (conv == 'c' ? 'd' : conv)).c_str(),
                    wide ? (long long)int64_t(v) : (long long)int32_t(uint32_t(v)));
      if (conv == 'c') { buf[0] = char(v); buf[1] = 0; }
    } else if (std::strchr("uxXop", conv)) {
      const uint64_t v = print_arg(ctx, base, arg++);
      std::snprintf(buf, sizeof(buf), (spec + "ll" + (conv == 'p' ? 'x' : conv)).c_str(),
                    wide ? (unsigned long long)v : (unsigned long long)uint32_t(v));
    } else if (std::strchr("fFeEgGaA", conv)) {
      const uint64_t v = print_arg(ctx, base, arg++);
      double d;
      std::memcpy(&d, &v, 8);
      std::snprintf(buf, sizeof(buf), (spec + conv).c_str(), d);
    } else {
      spec += conv;
      out += spec;
    }
    out += buf;
    i = j;
  }
  for (char& ch : out)
    if (static_cast<unsigned char>(ch) < 0x20 || static_cast<unsigned char>(ch) > 0x7E) ch = ' ';
  while (!out.empty() && out.back() == ' ') out.pop_back();
  return out;
}

int game_print_mode() {
  static const int mode = [] {
    const char* v = std::getenv("X2_GAME_PRINT");
    return v && v[0] == '0' ? 0 : v && v[0] == '1' ? 2 : 1;
  }();
  return mode;
}
}  // namespace

void X2LogGamePrints(const char* tag) {
  std::lock_guard<std::mutex> lock(g_print_mutex);
  const uint32_t n = g_print_count < 64 ? g_print_count : 64;
  REXLOG_ERROR("{}: the game's last {} debug messages (of {}):", tag, n, g_print_count);
  for (uint32_t k = g_print_count - n; k < g_print_count; ++k) REXLOG_ERROR("{}:   {}", tag, g_prints[k % 64]);
}

REX_EXTERN(__imp__sub_8255DE80);
REX_HOOK_RAW(sub_8255DE80) {
  if (game_print_mode() && ctx.r3.u32 >= 0x10000) {
    const std::string text = format_guest(ctx, base, print_guest_cstr(base, ctx.r3.u32, 300));
    uint32_t count;
    {
      std::lock_guard<std::mutex> lock(g_print_mutex);
      g_prints[g_print_count % 64] = text;
      count = ++g_print_count;
    }
    if (count <= 300 || game_print_mode() == 2) REXLOG_INFO("XIII-2 game: {}", text);
    if (count == 300 && game_print_mode() != 2) REXLOG_INFO("XIII-2 game: (300 messages logged; the rest are kept for a crash report)");
  }
  __imp__sub_8255DE80(ctx, base);
}

// ---------------------------------------------------------------------------------------
// The engine's ApplicationSpace heap (white::InitialConfiguration: 15 MB + 2 MB, the same in
// the disc and title update builds; sub_829116D0(guard, size, 0) stores its size, sub_829126E0
// creates it through sub_828A2878(kind, size, 0, 0), handle [0x82EA1814]; sub_82518100(size)
// allocates from it). With the title update, the DLC packages, and the Japanese voices in the
// update archives (16,510 more file entries than the 360's update ever had), it ran full: entering
// a town, an NPC's 272-byte physics table could not be allocated and the game wrote through the
// null pointer. The heap is given 32 MB more (X2_APP_SPACE_EXTRA_MB, 0 = the game's size). Its
// memory is part of the engine's one virtual block (sub_82914050 reserves the sum of the space sizes and
// lays them out one after another), in the game's virtual range, not the 360's 512 MB of physical
// memory.
// ---------------------------------------------------------------------------------------
namespace {
uint32_t app_space_extra() {
  static const uint32_t extra = [] {
    const char* v = std::getenv("X2_APP_SPACE_EXTRA_MB");
    const long mb = v && v[0] ? std::strtol(v, nullptr, 10) : 32;
    return uint32_t(mb < 0 ? 0 : mb > 256 ? 256 : mb) << 20;
  }();
  return extra;
}
}  // namespace

REX_EXTERN(__imp__sub_829116D0);
REX_HOOK_RAW(sub_829116D0) {
  const uint32_t size = ctx.r4.u32;
  if (size && ctx.r5.u32 == 0 && app_space_extra()) {
    ctx.r4.u64 = size + app_space_extra();
    REXLOG_INFO("XIII-2 memory: ApplicationSpace heap {} KB (the game's {} KB)", ctx.r4.u32 >> 10, size >> 10);
  }
  __imp__sub_829116D0(ctx, base);
}

// The engine's heaps as they are created (kind, size, where), for the log.
REX_EXTERN(__imp__sub_828A2878);
REX_HOOK_RAW(sub_828A2878) {
  const uint32_t audio_size = X2AudioPoolBytes(uint32_t(ctx.lr), ctx.r4.u32);
  if (audio_size != ctx.r4.u32) ctx.r4.u64 = audio_size;
  const uint32_t kind = ctx.r3.u32, size = ctx.r4.u32, at = ctx.r5.u32;
  __imp__sub_828A2878(ctx, base);
  static std::atomic<int> n{0};
  if (n.fetch_add(1) < 40)
    REXLOG_INFO("XIII-2 memory: heap kind {} of {} KB{} -> {:08X}", kind, size >> 10,
                at ? fmt::format(" at {:08X}", at) : std::string(), ctx.r3.u32);
}

// An allocation from the ApplicationSpace heap that failed: logged (the first 16) with the caller.
REX_EXTERN(__imp__sub_82518100);
REX_HOOK_RAW(sub_82518100) {
  const uint32_t size = ctx.r3.u32, lr = uint32_t(ctx.lr);
  __imp__sub_82518100(ctx, base);
  if (!ctx.r3.u32) {
    static std::atomic<int> n{0};
    if (n.fetch_add(1) < 16)
      REXLOG_WARN("XIII-2 memory: ApplicationSpace allocation of {} bytes failed (called from {:08X})", size, lr);
  }
}
