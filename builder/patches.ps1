# Lightning Returns builder - patches applied to the recompiled code after every codegen. Both
# are idempotent. Dot-sourced by pipeline.ps1.

# lightningreturns_pch.h: ReXGlue's guest "interrupts off" macros (REX_CHECK/ENTER/LEAVE_GLOBAL_LOCK,
# one process-wide mutex) -> project\lr_guest_irq.inc (per-thread state; fixes the movie /
# loading / title freezes and most of the lock contention). Also swaps the flush-mode macros.
function Update-PchIrqLock([string]$pch, [string]$inc) {
  $text = [IO.File]::ReadAllText($pch)
  if ($text.Contains('LR_GUEST_IRQ_LOCK v7')) { return 'already patched' }
  if ($text.Contains('LR_GUEST_IRQ_LOCK') -or $text.Contains('LR_FAIR_GLOBAL_LOCK')) {
    throw "$pch carries an older interrupt-lock patch; delete the generated folder so codegen writes it again"
  }
  $start = $text.IndexOf('#define REX_CHECK_GLOBAL_LOCK()')
  if ($start -lt 0) { throw "REX_CHECK_GLOBAL_LOCK not found in $pch (another ReXGlue version?)" }
  $m = [regex]::new('(?s)#define REX_LEAVE_GLOBAL_LOCK\(\).*?\} while \(0\)\r?\n').Match($text, $start)
  if (-not $m.Success) { throw "REX_LEAVE_GLOBAL_LOCK not found in $pch" }
  $text = $text.Substring(0, $start) + [IO.File]::ReadAllText($inc) + $text.Substring($m.Index + $m.Length)
  [IO.File]::WriteAllText($pch, $text)
  $check = [IO.File]::ReadAllText($pch)
  if (-not $check.Contains('#define enableFlushMode disableFlushMode') -or $check.Contains('#define disableFlushMode enableFlushMode')) {
    throw "the interrupt-lock patch did not stick in $pch"
  }
  return 'patched'
}

# The recompiled functions: a memory fence after each guest lwsync / eieio / sync (codegen
# leaves only a comment). Required on ARM (Android): without them game threads can see a flag
# before the data it publishes and wait forever (a freeze loading a save). On x86 they compile
# to nothing more than compiler barriers.
function Add-MemoryBarriers([string]$generated) {
  $fence = @{
    'lwsync' = 'std::atomic_thread_fence(std::memory_order_acq_rel);'
    'eieio'  = 'std::atomic_thread_fence(std::memory_order_acq_rel);'
    'sync'   = 'std::atomic_thread_fence(std::memory_order_seq_cst);'
  }
  $re = [regex]::new('(?m)^(\t// (lwsync|eieio|sync) ?\n)(?!\tstd::atomic_thread_fence)')
  $total = 0; $files = 0
  foreach ($f in Get-ChildItem -LiteralPath $generated -Filter '*.cpp' -File | Sort-Object Name) {
    $s = [IO.File]::ReadAllText($f.FullName)
    $n = $re.Matches($s).Count
    $t = $re.Replace($s, { param($m) $m.Groups[1].Value + "`t" + $fence[$m.Groups[2].Value] + "`n" })
    if ($n) { [IO.File]::WriteAllText($f.FullName, $t); $total += $n; $files++ }
  }
  return "$total memory barriers in $files files"
}

# The Ogg input heap is enlarged by x2_audio_pool.h. Its reader also has a
# literal range check; keep that check aligned with the 4 MiB backing heap.
function Update-X2OggPoolRange([string]$generated) {
  $symbol = 'DEFINE_REX_FUNC(sub_82A32918)'
  $old = 'ctx.r11.s64 = ctx.r11.s64 + 786432;'
  $new = 'ctx.r11.s64 = ctx.r11.s64 + 4194304;'
  $found = 0; $state = ''
  foreach ($file in Get-ChildItem -LiteralPath $generated -Filter '*.cpp' -File) {
    $text = [IO.File]::ReadAllText($file.FullName)
    if (-not $text.Contains($symbol)) { continue }
    $found++
    $match = [regex]::Match($text, '(?ms)^DEFINE_REX_FUNC\(sub_82A32918\) \{.*?(?=^DEFINE_REX_FUNC\(|\z)')
    if (-not $match.Success) { throw 'Cannot isolate the Ogg input reader.' }
    $body = $match.Value
    if ($body.Contains($new) -and -not $body.Contains($old)) { $state = 'already 4 MiB'; continue }
    if ([regex]::Matches($body, [regex]::Escape($old)).Count -ne 1 -or $body.Contains($new)) {
      throw 'The Ogg reader range check changed; refusing an unverified patch.'
    }
    $body = $body.Replace($old, $new)
    [IO.File]::WriteAllText($file.FullName, $text.Substring(0, $match.Index) + $body + $text.Substring($match.Index + $match.Length))
    $state = 'patched to 4 MiB'
  }
  if ($found -ne 1) { throw "Expected one Ogg input reader, found $found." }
  return "Ogg input-buffer range $state"
}

# Record slot/tutorial state changes so a lost UI can be distinguished from a
# stalled minigame. This reads the verified slot object fields without changing
# registers or guest state. Only the z0130 module contains this implementation.
function Add-X2SlotStateTrace([string]$generated) {
  $symbol = 'DEFINE_REX_FUNC(sub_88837188)'
  $marker = '// XIII2_SLOT_STATE_TRACE v1'
  $found = 0; $state = ''
  $trace = @'
  // XIII2_SLOT_STATE_TRACE v1
  {
    const uint32_t x2_slot_object = ctx.r3.u32;
    const uint32_t x2_slot_state = REX_LOAD_U32(x2_slot_object + 13272);
    const uint32_t x2_tutorial_state = REX_LOAD_U32(x2_slot_object + 12696);
    const uint32_t x2_tutorial_active = REX_LOAD_U32(x2_slot_object + 12708);
    const uint32_t x2_slot_window = REX_LOAD_U32(x2_slot_object + 12680);
    const uint32_t x2_slot_tip = REX_LOAD_U32(x2_slot_object + 12684);
    uint64_t x2_slot_hash = 1469598103934665603ULL;
    for (uint32_t value : {x2_slot_object, x2_slot_state, x2_tutorial_state, x2_tutorial_active, x2_slot_window, x2_slot_tip}) {
      x2_slot_hash = (x2_slot_hash ^ value) * 1099511628211ULL;
    }
    static thread_local uint64_t x2_slot_previous = 0;
    if (x2_slot_hash != x2_slot_previous) {
      x2_slot_previous = x2_slot_hash;
      REXLOG_INFO("XIII-2 slots: state={} tutorial={} active={} window={:08X} tip={:08X}",
                  x2_slot_state, x2_tutorial_state, x2_tutorial_active, x2_slot_window, x2_slot_tip);
    }
  }
'@
  foreach ($file in Get-ChildItem -LiteralPath $generated -Filter '*.cpp' -File) {
    $text = [IO.File]::ReadAllText($file.FullName)
    if (-not $text.Contains($symbol)) { continue }
    $found++
    if ($text.Contains($marker)) { $state = 'already traced'; continue }
    $match = [regex]::Match($text, '(?m)^DEFINE_REX_FUNC\(sub_88837188\) \{\r?\n\tREX_FUNC_PROLOGUE\(\);')
    if (-not $match.Success) { throw 'Cannot locate the verified slot-update prologue.' }
    $text = $text.Insert($match.Index + $match.Length, "`n" + $trace)
    $text = '#include <rex/logging/macros.h>' + "`n" + $text
    [IO.File]::WriteAllText($file.FullName, $text)
    $state = 'state transitions traced'
  }
  if ($found -ne 1) { throw "Expected one slot update function, found $found." }
  return "slot/tutorial $state"
}

# Registration occurs after the patched XEX image is mapped, before DllMain.
# Clear only PE uninitialized data; initialized data and code stay intact.
function Update-X2ModuleBss([string]$generated, [string]$header) {
  $file = Join-Path $generated 'ffxiii2_register.cpp'
  $text = [IO.File]::ReadAllText($file)
  $marker = '// XIII2_MODULE_BSS v1'
  if ($text.Contains($marker)) { return 'module zero-initialization already patched' }
  if (-not (Test-Path -LiteralPath $header)) { throw "Module BSS helper missing: $header" }
  $start = 'void ReXModule_Register(rex::runtime::IModuleRegistrar* registrar) {'
  if ([regex]::Matches($text,[regex]::Escape($start)).Count -ne 1) { throw "Unexpected module registration shape: $file" }
  $body = @'
  // XIII2_MODULE_BSS v1
  const size_t x2_zeroed = X2ClearModuleBss(std::span<uint8_t>(
      REX_KERNEL_STATE()->memory()->virtual_membase() + REX_IMAGE_BASE, REX_IMAGE_SIZE));
  REXLOG_INFO("XIII-2 zone memory: zeroed {} bytes of uninitialized data at {:08X}", x2_zeroed, REX_IMAGE_BASE);
'@
  $text = $text.Replace($start, $start + "`n" + $body)
  $includes = '#include "' + $header.Replace('\','/') + '"' + "`n" + '#include <rex/system/kernel_state.h>' + "`n" + '#include <rex/logging/macros.h>' + "`n"
  [IO.File]::WriteAllText($file,$includes+$text)
  return 'module zero-initialization patched'
}
