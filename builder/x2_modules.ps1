# FFXIII-2 test build - the zone code modules (z0070.xex, z0130.xex, z0160.xex, z0161.xex).
# All four load at the same address (0x88800000), one at a time; ReXGlue's codegen maps every
# module it processes into one guest address space, so with more than one at once the later
# ones are analyzed over the first one's memory (bogus "target not in any function" errors).
# Each module is recompiled on its own (codegen --target), and the two files a single run
# writes for its one module - the DLL build list and the runtime's module registry - are then
# written for all of them. Dot-sourced by XIII-2_Test.ps1.

$script:X2Modules = @('z0070', 'z0130', 'z0160', 'z0161')

function Write-X2ModuleLists([string]$generated) {
  # Rebuilt from the first entry each time, so running it again does not add duplicates.
  $targets = Join-Path $generated 'default\dll_targets.cmake'
  $registry = Join-Path $generated 'default\module_registry.cpp'
  $t = [IO.File]::ReadAllText($targets)
  $ms = [regex]::Matches($t, '(?s)\r?\n# DLL module: (\w+)\r?\n.*?\r?\nendif\(\)\r?\n')
  if ($ms.Count -eq 0) { throw "No module block in $targets (another ReXGlue version?)" }
  $one = $ms[0].Groups[1].Value
  $last = $ms[$ms.Count - 1]
  $body = ($script:X2Modules | ForEach-Object { $ms[0].Value.Replace($one, $_) }) -join ''
  [IO.File]::WriteAllText($targets, $t.Substring(0, $ms[0].Index) + $body + $t.Substring($last.Index + $last.Length))

  $r = [IO.File]::ReadAllText($registry)
  $cs = [regex]::Matches($r, '(?s)  kernel_state->RegisterRecompiledModule\(\s*"([^"]+)\.xex",.*?\);\r?\n')
  if ($cs.Count -eq 0) { throw "No module registration in $registry (another ReXGlue version?)" }
  $one = $cs[0].Groups[1].Value
  $last = $cs[$cs.Count - 1]
  $calls = ($script:X2Modules | ForEach-Object { $cs[0].Value.Replace($one, $_) }) -join "`n"
  [IO.File]::WriteAllText($registry, $r.Substring(0, $cs[0].Index) + $calls + $r.Substring($last.Index + $last.Length))
  return "$($script:X2Modules.Count) zone modules listed"
}

# The project's own build step would run codegen again, for all modules at once (which fails,
# see above) whenever the manifest is newer than its outputs - and every codegen run stamps the
# manifest. Codegen is run by XIII-2_Test.ps1 instead; the build step only touches its stamp.
# generated/rexglue.cmake is written again by the next codegen run, which gets this again.
function Disable-BuildCodegen([string]$generated) {
  $f = Join-Path $generated 'rexglue.cmake'
  $t = [IO.File]::ReadAllText($f)
  if ($t.Contains('X2_TEST: codegen runs outside the build')) { return 'build codegen already off' }
  $m = [regex]::Match($t, '(?m)^(\s*)COMMAND \$<TARGET_FILE:rex::rexglue> codegen [^\r\n]*\r?\n\s*DEPFILE [^\r\n]*\r?\n')
  if (-not $m.Success) { throw "The codegen build step was not found in $f (another ReXGlue version?)" }
  $i = $m.Groups[1].Value
  $new = "$i# X2_TEST: codegen runs outside the build (XIII-2_Test.ps1, one module at a time)`n" +
         "${i}COMMAND `${CMAKE_COMMAND} -E touch `"`${CMAKE_CURRENT_SOURCE_DIR}/generated/default/codegen.build.stamp`"`n"
  [IO.File]::WriteAllText($f, $t.Substring(0, $m.Index) + $new + $t.Substring($m.Index + $m.Length))
  return 'build codegen off'
}
