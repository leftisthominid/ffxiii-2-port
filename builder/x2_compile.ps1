# Shared code generation and compilation from the tested XIII-2 pipeline.
function Invoke-Logged([string]$exe, [string[]]$arguments, [string]$log, [string]$cwd = '', [switch]$Quiet) {
  New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
  $run = New-Object LrRun $exe, ([string[]]@($arguments)), $log, $cwd
  Wait-WithProgress { $run.Wait(15000) } { $run.Status }
  $rc = $run.Finish()
  if ($rc -ne 0 -and -not $Quiet) { Show-LogError $log }
  return $rc
}
function Show-LogError([string]$log) {
  $lines = @(Get-Content -LiteralPath $log -ErrorAction SilentlyContinue)
  $at = -1
  for ($i = 0; $i -lt $lines.Count; ++$i) { if ($lines[$i] -match '^FAILED: ') { $at = $i; break } }
  if ($at -ge 0) {
    Say "  (the failed step in $log)" 'Yellow'
    $shown = 0
    for ($i = $at; $i -lt $lines.Count -and $shown -lt 25; ++$i) {
      if ($i -gt $at -and $lines[$i] -match '^\[\d+/\d+\] ') { break }
      $t = $lines[$i]
      if ($t.Length -gt 300) { $t = $t.Substring(0, 297) + '...' }
      Say "    $t" 'Yellow'
      $shown++
    }
  } else {
    Say "  (last lines of $log)" 'Yellow'
    $lines | Select-Object -Last 15 | ForEach-Object { Say "    $_" 'Yellow' }
  }
}
# A file whose content changed gets the current time: the zip's dates are older than the
# objects of an earlier build, which would otherwise keep the old code (step 4 of version 4).
function Copy-IfChanged([string]$from, [string]$to) {
  if ((Test-Path -LiteralPath $to) -and ((Get-FileHash -LiteralPath $to).Hash -eq (Get-FileHash -LiteralPath $from).Hash)) { return }
  Copy-Item -LiteralPath $from -Destination $to -Force
  (Get-Item -LiteralPath $to).LastWriteTime = Get-Date
}

function Build-X2Project($p, $t) {
$Here = $script:Repo
$Game = $p.game
$gameRun = $p.gameRun
$project = $p.project
$build = $p.build
$logs = $p.logs
$Rebuild = $false
$src = Join-Path $Here 'project'
foreach ($d in 'src', 'analysis', 'generated') { New-Item -ItemType Directory -Force -Path (Join-Path $project $d) | Out-Null }
foreach ($d in 'src', 'analysis') {
  foreach ($f in Get-ChildItem -LiteralPath (Join-Path $src $d) -File) { Copy-IfChanged $f.FullName (Join-Path (Join-Path $project $d) $f.Name) }
}
# The port's own sources are always recompiled (a minute): the build does not notice a changed
# header (ffxiii2_app.h) on every PC, which kept an old main.cpp in version 5.
Get-ChildItem -LiteralPath (Join-Path $project 'src') -Filter '*.cpp' | ForEach-Object { $_.LastWriteTime = Get-Date }
Copy-IfChanged (Join-Path $src 'generated\rexglue.cmake') (Join-Path $project 'generated\rexglue.cmake')
foreach ($f in 'CMakeLists.txt', 'CMakePresets.json', 'lr_guest_irq.inc') { Copy-IfChanged (Join-Path $src $f) (Join-Path $project $f) }
$manifest = [IO.File]::ReadAllText((Join-Path $src 'ffxiii2_manifest.toml.in')).Replace('@GAME_ROOT@', ($gameRun -replace '\\', '/'))
[IO.File]::WriteAllText((Join-Path $project 'ffxiii2_manifest.toml'), $manifest)
# Zip dates can be in the future (time zones); CMake then regenerates its build forever.
$now = Get-Date
Get-ChildItem -LiteralPath $project -Recurse -File | Where-Object { $_.LastWriteTime -gt $now } | ForEach-Object { $_.LastWriteTime = $now.AddMinutes(-1) }
Say "  $project"

# --- 3. recompile (skipped when the manifest and function lists are unchanged)
Say '[3/9] Recompiling default.xex and the zone modules (with the title update)'
$generated = Join-Path $project 'generated'
$pch = Join-Path $generated 'default\ffxiii2_pch.h'
$inputsHash = (@($manifest) + @($XexpSha256.Keys | Sort-Object | ForEach-Object { $XexpSha256[$_] }) + @(Get-ChildItem -LiteralPath (Join-Path $project 'analysis') -Filter '*.toml' | Sort-Object Name | ForEach-Object { [IO.File]::ReadAllText($_.FullName) })) -join "`n"
$inputsHash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($inputsHash)))
$stamp = Join-Path $project 'codegen.inputs'
if ((Test-Path -LiteralPath $pch) -and (Test-Path -LiteralPath $stamp) -and ([IO.File]::ReadAllText($stamp) -eq $inputsHash)) {
  Say '  already done for these function lists'
} else {
  # One module per run (they share one load address; see builder\x2_modules.ps1). The first
  # run also recompiles default.xex (5-10 minutes); the others take about a minute each.
  foreach ($z in $script:X2Modules) {
    Say "  recompiling with $z.xex..."
    $rc = Invoke-Logged $t.rexglue @('codegen', 'ffxiii2_manifest.toml', '--target', $z) (Join-Path $logs "codegen_$z.log") $project
    if ($rc -ne 0) { throw "The recompiler failed; see $logs\codegen_$z.log" }
  }
  if (-not (Test-Path -LiteralPath $pch)) { throw "The recompiler wrote no $pch" }
  [IO.File]::WriteAllText($stamp, $inputsHash)
}
Say ('  ' + (Write-X2ModuleLists $generated))
Say ('  ' + (Disable-BuildCodegen $generated))
foreach ($d in @('default') + $script:X2Modules) {
  $g = Join-Path $generated $d
  Say ("  ${d}: interrupt lock " + (Update-PchIrqLock (Join-Path $g 'ffxiii2_pch.h') (Join-Path $project 'lr_guest_irq.inc')) + '; ' + (Add-MemoryBarriers $g))
}

Say ('  ' + (Update-X2OggPoolRange (Join-Path $generated 'default')))
foreach ($module in $script:X2Modules) {
  Say ('  ' + $module + ': ' + (Update-X2ModuleBss (Join-Path $generated $module) (Join-Path $project 'src\x2_module_bss.h')))
}

# --- 4. build
Say '[4/9] Building ffxiii2.exe and the zone module DLLs'
$ico = Join-Path $p.icon 'ffxiii2.ico'
if (-not (Test-Path -LiteralPath $ico)) { throw 'Application icon missing.' }
Copy-IfChanged $ico (Join-Path $project 'src\ffxiii2.ico')
$resourceIcon=(Join-Path $project 'src\ffxiii2.ico') -replace '\\','/'
[IO.File]::WriteAllText((Join-Path $project 'src\ffxiii2.rc'), "1 ICON `"$resourceIcon`"`r`n")
$cfg = @('-S', $project, '-B', $build) + (Get-CMakeToolArgs $t)
$rc = Invoke-Logged $t.cmake $cfg (Join-Path $logs 'configure.log')
if ($rc -ne 0) { $rc = Invoke-Logged $t.cmake $cfg (Join-Path $logs 'configure2.log') }
if ($rc -ne 0) { throw "Configuring the build failed; see $logs\configure*.log" }
Say '  building (all recompiled code: 15-45 minutes the first time)...'
$rc = Invoke-Logged $t.cmake @('--build', $build, '--parallel', "$([Environment]::ProcessorCount)") (Join-Path $logs 'build.log')
if ($rc -ne 0) { throw "The build failed; see $logs\build.log" }
$exe = Join-Path $build 'ffxiii2.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "The build finished without $exe" }
Say "  $exe"


}
