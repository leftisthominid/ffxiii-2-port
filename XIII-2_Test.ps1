<#
  FFXIII-2 test build. Recompiles the Xbox 360 FFXIII-2 default.xex with ReXGlue, patched by
  the 360 title update (version 0.0.1.13, whose code loads the downloadable content), builds it
  for Windows with the tools the Lightning Returns builder already set up, then runs it on the
  360 game files and collects the logs.

  Only reads: the 360 game folder (-Game), the 360 content folder or title update package
  (-Content, -TitleUpdate), and the Lightning Returns build (-LrRoot: its tools and its renderer
  DLL). Everything is written under -Out, plus XIII-2_Test_Logs.zip in Downloads. Running it again
  reuses the recompiled code and the build when nothing changed.
#>
param(
  [string]$Game = 'C:\FFXIII-2\360\game',
  [string]$LrRoot = 'C:\Lightning Returns',
  [string]$Out = 'C:\FFXIII-2 Test',
  [string]$Ps3 = 'C:\FFXIII-2\Final Fantasy XIII-2 (USA) (En,Fr,Es).iso',  # '' = no PS3 movies
  [string]$Steam = 'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2',  # '' = no Japanese voices
  [string]$Content = 'C:\FFXIII-2\Content',  # 360 drive content folder: the title update and DLC packages are installed from it ('' = none)
  [string]$TitleUpdate = '',  # the title update package file (tu0000000d_00000000), when it is not in -Content
  [string]$Ps3Saves = '',     # PS3 saves to import: a zip of save folders (BLUS30776-02 ...) or such a folder
  [string]$Search = 'C:\FFXIII-2',  # where to look for the inputs above when they are not where the defaults say
  [int]$Minutes = 0,          # 0: run until the game window is closed (or the game stops)
  [ValidateSet('', 'one', 'nofield', 'field', 'all')]
  [string]$VoiceTest = '',    # narrow the Japanese voice patch for a test (one / nofield / field); all = every voice file

  [switch]$Rebuild,           # recompile and build again from scratch
  [switch]$DlcSample,         # only: a sample of the Steam and 360 DLC files (XIII-2_DLC_Sample.zip in Downloads)
  [ValidateSet('', 'compare', 'install', 'restore', 'export')]
  [string]$SteamDlc = '',     # only: the DLC from the Steam version (builder\x2_steamdlc.ps1): compare / install / restore / export
  [string]$DlcOnly = ''       # with -SteamDlc compare: only the files under these paths (comma-separated), all of them in the report
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Here = $PSScriptRoot
$XexSha256 = '73D79C6D7C9BA087E33AECE7A88A9A8669D6A6D8444FE789EDD68AEF0318B66E'
# The title update 0.0.1.13 (tu0000000d_00000000): its code patches, one per module.
$XexpSha256 = @{
  'default' = 'D980AD1D01FA3C922E7A94A3EC29E1FCEDB913580B99BB2AF8DD41ED457EF9DD'
  'z0070' = '98FF7BFE3B37DA6DE0ABB451515E461F17A8393DE8BFC1EFEBC7AC8FC15AE043'
  'z0130' = '2641BF84299FEF841CB101B5C13C59737BC705352A0268CEDDCD21EC76E6580C'
  'z0160' = 'F5BC60570A54F50DDDC707CF4CD1F4641F50E2B383D625FB18F8234C37E8487D'
  'z0161' = 'A847031AB54BA58C3F45D85DDCE0A363BB574EE7548942B49A41C92689CDA3A5'
}

function Say([string]$text, [string]$color = '') {
  if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
}
function Ask-YesNo([string]$question) {
  while ($true) {
    $a = (Read-Host "$question (y/n)").Trim().ToLowerInvariant()
    if ($a -eq 'y' -or $a -eq 'yes') { return $true }
    if ($a -eq 'n' -or $a -eq 'no') { return $false }
    Say 'Please answer y or n.' 'Yellow'
  }
}
. (Join-Path $Here 'builder\tools.ps1')
. (Join-Path $Here 'builder\patches.ps1')
. (Join-Path $Here 'builder\x2_modules.ps1')
. (Join-Path $Here 'builder\iso.ps1')
. (Join-Path $Here 'builder\x2_ps3.ps1')
. (Join-Path $Here 'builder\x2_jpvoice.ps1')
. (Join-Path $Here 'builder\x2_ui.ps1')
. (Join-Path $Here 'builder\x2_steamdlc.ps1')

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
# The last line of a file another process is still writing.
function Get-LastLine([string]$path) {
  try {
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
      $len = $fs.Length
      $fs.Seek([Math]::Max(0, $len - 4096), 'Begin') | Out-Null
      $buf = New-Object byte[] ([int]([Math]::Min(4096, $len)))
      $n = $fs.Read($buf, 0, $buf.Length)
      $t = [Text.Encoding]::UTF8.GetString($buf, 0, $n).TrimEnd()
      $i = $t.LastIndexOf("`n")
      return $t.Substring($i + 1)
    } finally { $fs.Dispose() }
  } catch { return '' }
}

$sw = [Diagnostics.Stopwatch]::StartNew()
Say 'FFXIII-2 test build' 'Cyan'
$logs = Join-Path $Out 'logs'
$project = Join-Path $Out 'project'
$build = Join-Path $Out 'build'
$run = Join-Path $Out 'run'
New-Item -ItemType Directory -Force -Path $logs, $run | Out-Null

# --- 1. inputs
Say '[1/9] Checking the inputs'
# An input not where its parameter says: looked for under -Search, a few folders deep.
function Find-Input([string]$what, [scriptblock]$test, [int]$depth = 4) {
  if (-not $Search -or -not (Test-Path -LiteralPath $Search)) { return $null }
  Say "  looking for $what under $Search..."
  $found = Get-ChildItem -LiteralPath $Search -Recurse -Depth $depth -Force -ErrorAction SilentlyContinue | Where-Object $test | Select-Object -First 1
  if ($found) { return $found.FullName }
  return $null
}
if (-not (Test-Path -LiteralPath (Join-Path $Game 'default.xex'))) {
  $found = Find-Input 'the 360 game folder (default.xex)' { -not $_.PSIsContainer -and $_.Name -eq 'default.xex' -and (Test-Path -LiteralPath (Join-Path $_.DirectoryName 'alba_data')) }
  if ($found) { $Game = Split-Path $found; Say "  360 game folder: $Game" }
}
$xex = Join-Path $Game 'default.xex'
if (-not (Test-Path -LiteralPath $xex)) { throw "No default.xex in $Game. Pass the extracted 360 game folder with -Game." }
foreach ($f in 'alba_data\sys\filelistu.x360.bin', 'alba_data\sys\white_imgu.x360.bin') {
  if (-not (Test-Path -LiteralPath (Join-Path $Game $f))) { throw "$Game has no $f; is it the whole extracted 360 disc?" }
}
Say "  checking default.xex..."
$hash = (Get-FileHash -LiteralPath $xex -Algorithm SHA256).Hash
if ($hash -ne $XexSha256) { throw "This default.xex (SHA-256 $hash) is not the one the function lists were made for (FFXIII-2 USA, title 53510806)." }
Say "  $xex (the expected FFXIII-2 USA default.xex)"
foreach ($z in $script:X2Modules) {
  if (-not (Test-Path -LiteralPath (Join-Path $Game "$z.xex"))) { throw "$Game has no $z.xex (a zone code module from the disc's top folder)." }
}
Say "  zone code modules: $(($script:X2Modules | ForEach-Object { "$_.xex" }) -join ', ')"
if ($Ps3 -and -not (Test-Path -LiteralPath $Ps3)) {
  $name = Split-Path $Ps3 -Leaf
  $found = Find-Input "the PS3 disc image ($name)" { -not $_.PSIsContainer -and $_.Name -eq $name }
  if ($found) { $Ps3 = $found; Say "  PS3 disc image: $Ps3" }
}
if ($Content -and -not (Test-Path -LiteralPath $Content)) {
  $found = Find-Input 'the 360 content folder (Content)' { $_.PSIsContainer -and $_.Name -eq 'Content' } 3
  if ($found) { $Content = $found; Say "  360 content folder: $Content" }
}
$lrGame = Join-Path $LrRoot 'Lightning Returns'
$plugin = Join-Path $lrGame 'rexgpu-xenos.dll'
if (-not (Test-Path -LiteralPath $plugin)) { throw "No rexgpu-xenos.dll in $lrGame. -LrRoot must be the Lightning Returns builder's output folder." }
$t = Get-WindowsTools (Join-Path $LrRoot 'tools')
Say "  build tools from $LrRoot\tools"

# -DlcSample: for working out the Steam DLC conversion. Files of the installed 360 DLC packages
# with their Steam (PC) counterparts (x2_jpvoice --dlc-sample), zipped; nothing else is built or run.
if ($DlcSample) {
  Say 'DLC sample: files of your 360 DLC with their Steam versions (only reads them)'
  if (-not $Steam -or -not (Test-Path -LiteralPath $Steam)) { throw "No Steam game folder at '$Steam' (give -Steam <folder>)" }
  $dlcRoot = Join-Path $Out 'userdata\0000000000000000\53510806\00000002'
  if (-not (Test-Path -LiteralPath $dlcRoot)) { throw "No 360 DLC installed at $dlcRoot (run XIII-2_Test.ps1 once without -DlcSample)" }
  $work = Join-Path $Out 'work'
  $exe = Join-Path $work 'x2_jpvoice.exe'
  Say '  building the converter'
  Build-X2Converter $t (Join-Path $Here 'converters\x2_jpvoice.cpp') $exe
  $sample = Join-Path $work 'dlc_sample'
  if (Test-Path -LiteralPath $sample) { Remove-Item -LiteralPath $sample -Recurse -Force }
  $log = Join-Path $logs 'dlc_sample.log'
  Say '  taking the sample (a few minutes)'
  $rc = Invoke-Logged $exe @('--dlc-sample', $Steam, $dlcRoot, $sample) $log
  Get-Content -LiteralPath $log | Select-Object -Last 6 | ForEach-Object { Say "  $_" }
  if ($rc -ne 0) { throw "The sample failed; see $log" }
  Copy-Item -LiteralPath $log -Destination $sample
  $zip = Join-Path (Join-Path $HOME 'Downloads') 'XIII-2_DLC_Sample.zip'
  if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
  Say '  zipping'
  Compress-Archive -Path (Join-Path $sample '*') -DestinationPath $zip -CompressionLevel Optimal
  Say ("Done: {0} ({1:N0} MB). Send this file." -f $zip, ((Get-Item -LiteralPath $zip).Length / 1MB)) 'Green'
  exit 0
}

# The title update and the DLC packages (installed once; saves are never touched here). The
# title update is unpacked into run\update_tu: its code patches (default.xexp, zNNNN.xexp) are
# recompiled with the game, its archives are the game's update folder. A DLC package goes where
# the 360 dashboard installs it (userdata\0000000000000000\53510806\00000002), where the title
# update's own code finds it.
$tuDir = Join-Path $run 'update_tu'
$importer = Join-Path $Here 'scripts\X2_Import_360_Content.ps1'
function Import-360Content([string]$source) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File $importer -Source $source -UserData (Join-Path $Out 'userdata') -Only dlc -Skip -TuOut $tuDir -ListOut (Join-Path $logs "content_$((Split-Path $source -Leaf) -replace '[^A-Za-z0-9_.-]', '_').txt") -ExcludeList (Join-Path $Out 'work\steamdlc\moved_360_packages.txt') |
    Where-Object { $_ -notmatch '^  (reading|skip) ' } | ForEach-Object { Say "  $_" }
}
if ($TitleUpdate) {
  if (-not (Test-Path -LiteralPath $TitleUpdate -PathType Leaf)) { throw "No title update package file at $TitleUpdate" }
  Import-360Content $TitleUpdate
}
if (-not $Content) { Say '  no content folder (-Content '''')' }
elseif (-not (Test-Path -LiteralPath $Content)) { Say "  no content folder at $Content" 'Yellow' }
else { Import-360Content $Content }
if (-not (Test-Path -LiteralPath (Join-Path $tuDir 'x2_title_update.txt'))) {
  # Not in the content folder: the package file anywhere under -Search or in Downloads.
  $tuFile = $null
  foreach ($root in @($Search, (Join-Path $HOME 'Downloads'))) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    Say "  looking for the title update (tu0000000d_00000000) under $root..."
    $tuFile = Get-ChildItem -LiteralPath $root -Recurse -Depth 6 -Force -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -eq 'tu0000000d_00000000' } | Select-Object -First 1
    if ($tuFile) { break }
  }
  if ($tuFile) { Import-360Content $tuFile.FullName }
}
if (-not (Test-Path -LiteralPath (Join-Path $tuDir 'x2_title_update.txt'))) {
  throw ("No FFXIII-2 title update found. This build needs it (its code loads the DLC). Put the file tu0000000d_00000000 " +
         "(from the 360 drive: Content\0000000000000000\53510806\000B0000) into $Content, or pass its path with -TitleUpdate.")
}
foreach ($m in @('default') + $script:X2Modules) {
  $p = Join-Path $tuDir "$m.xexp"
  if (-not (Test-Path -LiteralPath $p)) { throw "The title update in $tuDir has no $m.xexp" }
  $h = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
  if ($h -ne $XexpSha256[$m]) { throw "This title update's $m.xexp (SHA-256 $h) is not the one the function lists were made for (title update 0.0.1.13)." }
}
Say "  title update 0.0.1.13: $tuDir"

# -SteamDlc: the DLC from the Steam version (builder\x2_steamdlc.ps1); nothing else is built or run.
if ($SteamDlc) {
  Say "Steam DLC: $SteamDlc"
  $script:X2DlcOnly = $DlcOnly
  Invoke-X2SteamDlc $SteamDlc $Game $tuDir $Steam (Join-Path $LrRoot 'tools') $Out $logs
  exit 0
}

# The game folder the port runs on: the 360 game folder's contents linked (folders as junctions,
# files as hard links or copies), plus the title update's code patches next to the modules. The
# 360 game folder itself is not changed.
$gameRun = Join-Path $run 'game_tu'
New-Item -ItemType Directory -Force -Path $gameRun | Out-Null
foreach ($e in Get-ChildItem -LiteralPath $Game -Force) {
  $to = Join-Path $gameRun $e.Name
  if (Test-Path -LiteralPath $to) { continue }
  if ($e.PSIsContainer) { New-Item -ItemType Junction -Path $to -Target $e.FullName | Out-Null }
  else {
    try { New-Item -ItemType HardLink -Path $to -Target $e.FullName -ErrorAction Stop | Out-Null }
    catch { Copy-Item -LiteralPath $e.FullName -Destination $to -Force }
  }
}
foreach ($m in @('default') + $script:X2Modules) { Copy-IfChanged (Join-Path $tuDir "$m.xexp") (Join-Path $gameRun "$m.xexp") }
Say "  game folder with the title update's code: $gameRun"

# --- 2. project
Say '[2/9] Project files'
if ($Rebuild) {
  foreach ($d in $project, $build) { if (Test-Path -LiteralPath $d) { Say "  removing $d"; Remove-Item -LiteralPath $d -Recurse -Force } }
}
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

# --- 4. build
Say '[4/9] Building ffxiii2.exe and the zone module DLLs'
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

# --- 5. game folder: the exe, the SDK runtime, the Lightning Returns renderer, settings
Say '[5/9] Test game folder'
Copy-Item -LiteralPath $exe -Destination $run -Force
Copy-Item -LiteralPath (Join-Path $t.sdkBin 'rexruntime.dll') -Destination $run -Force
Copy-Item -LiteralPath $plugin -Destination $run -Force
foreach ($z in $script:X2Modules) {
  $dll = Get-ChildItem -LiteralPath $build -Recurse -Filter "ffxiii2_$z.dll" | Select-Object -First 1
  if (-not $dll) { throw "The build made no ffxiii2_$z.dll" }
  Copy-Item -LiteralPath $dll.FullName -Destination $run -Force
}
$lines = @('gpu_plugin = "xenos"', 'graphics_backend = "d3d12"', 'log_level = "info"', 'anisotropic_override = 5',
           'texture_cache_memory_limit_soft = 2048', 'texture_cache_memory_limit_hard = 4096',
           'clear_memory_page_state = false', 'occlusion_query_enable = false', 'readback_memexport = true')
[IO.File]::WriteAllLines((Join-Path $run 'ffxiii2.toml'), [string[]]$lines)
# As in the Lightning Returns port: the runtime writes saves under B13EBABEBABEBABE but looks
# for them under 00000000BABEBABE when listing them. A junction makes both the same folder.
$userData = Join-Path $Out 'userdata'
# PS3 saves: into userdata\ps3_saves, where the game imports each once at its start (as a new save
# slot; existing saves are never overwritten). The PS3 files are only read.
if ($Ps3Saves) {
  $ps3Dest = Join-Path $userData 'ps3_saves'
  New-Item -ItemType Directory -Force -Path $ps3Dest | Out-Null
  if (Test-Path -LiteralPath $Ps3Saves -PathType Leaf) {
    Expand-Archive -LiteralPath $Ps3Saves -DestinationPath $ps3Dest -Force
  } elseif (Test-Path -LiteralPath $Ps3Saves -PathType Container) {
    Copy-Item -LiteralPath $Ps3Saves -Destination $ps3Dest -Recurse -Force
  } else {
    Say "  -Ps3Saves: $Ps3Saves not found" 'Yellow'
  }
  $ps3Found = @(Get-ChildItem -LiteralPath $ps3Dest -Recurse -Filter 'APP.DAT' -ErrorAction SilentlyContinue)
  Say ("  PS3 saves for the game to import: {0} in {1}" -f $ps3Found.Count, $ps3Dest)
}
$real = Join-Path $userData 'B13EBABEBABEBABE'
$alias = Join-Path $userData '00000000BABEBABE'
New-Item -ItemType Directory -Force -Path $real | Out-Null
$item = Get-Item -LiteralPath $alias -Force -ErrorAction SilentlyContinue
if (-not $item) {
  New-Item -ItemType Junction -Path $alias -Target $real | Out-Null
  Say "  save folder link: $alias -> $real"
} elseif ($item.LinkType -ne 'Junction') {
  Say "  $alias is a normal folder, not a link; the save list may stay empty until it is removed" 'Yellow'
}
Say "  $run (renderer DLL from $lrGame; GPU memory writes copied back, for the text)"

# --- 6. PS3 cinematics (once; later runs reuse them)
Say '[6/9] PS3 cinematics, buttons, and UI'
if (-not $Ps3) { Say '  skipped (-Ps3 not given)' }
elseif (-not (Test-Path -LiteralPath $Ps3)) { Say "  skipped: no PS3 disc image at $Ps3" 'Yellow' }
else {
  try {
    $ffmpeg = Get-Ffmpeg (Join-Path $LrRoot 'tools')
    Convert-X2Ps3Movies $Ps3 $ffmpeg (Join-Path $run 'movies_ps3') (Join-Path $Out 'work') $logs
    Copy-Item -LiteralPath $ffmpeg -Destination (Join-Path $run 'ffmpeg.exe') -Force
  } catch { Say "  PS3 movies skipped: $($_.Exception.Message)" 'Yellow' }
  try {
    Say '  PS3 buttons / UI and sharp fonts:'
    Convert-X2Ps3Ui $t $Ps3 $Game (Join-Path $run 'textures') (Join-Path $Out 'work') $logs
  } catch { Say "  PS3 buttons / UI skipped: $($_.Exception.Message)" 'Yellow' }
}
try {
  Convert-X2TitleMenu $t $Game (Join-Path $run 'textures') (Join-Path $Out 'work') $logs
} catch { Say "  title menu labels skipped: $($_.Exception.Message)" 'Yellow' }

# --- 7. Japanese voices (once; later runs reuse them)
Say '[7/9] Japanese voices (Steam)'
if (-not $Steam) { Say '  skipped (-Steam not given)' }
elseif (-not (Test-Path -LiteralPath $Steam)) { Say "  skipped: no Steam game folder at $Steam" 'Yellow' }
else {
  try {
    Convert-X2JpVoice $t $Game $Steam (Get-Ffmpeg (Join-Path $LrRoot 'tools')) (Join-Path $run 'jpvoice') (Join-Path $Out 'work') $logs
  } catch { Say "  Japanese voices skipped: $($_.Exception.Message)" 'Yellow' }
  try {
    Convert-X2JpMovieAudio $t $Game $Steam (Get-Ffmpeg (Join-Path $LrRoot 'tools')) (Join-Path $run 'movies_jp') (Join-Path $Out 'work') $logs
  } catch { Say "  Japanese movie audio skipped: $($_.Exception.Message)" 'Yellow' }
  try {
    Convert-X2JpDlcVoices $t $Steam (Get-Ffmpeg (Join-Path $LrRoot 'tools')) (Join-Path $Out 'userdata\0000000000000000\53510806\00000002') (Join-Path $run 'jpvoice_dlc') (Join-Path $Out 'work') $logs
  } catch { Say "  Japanese DLC voices skipped: $($_.Exception.Message)" 'Yellow' }
}

# Test 1 (voices set to English) showed the game works without the Japanese voice patch; with
# the lip-sync files out of it, the voices go back to Japanese.
# 120 fps: v22 and v23 crashed at start with it (the port's own bug, fixed in v24); their failed
# start is forgotten once, so the 120 fps choice is tried again instead of falling back to 60.
$fpsTrial = Join-Path $Out 'userdata\fps_trial.txt'
$fpsReset = Join-Path $Out 'userdata\fps_trial_reset_v24.txt'
if (-not (Test-Path -LiteralPath $fpsReset)) {
  if (Test-Path -LiteralPath $fpsTrial) { Remove-Item -LiteralPath $fpsTrial -Force }
  New-Item -ItemType Directory -Force -Path (Split-Path $fpsReset) | Out-Null
  Set-Content -LiteralPath $fpsReset -Value 'done'
}
# Once (v29): the PS3 saves imported so far are backed up and removed, and imported again by the
# game at its start (with the 360 game settings and an empty battle result; earlier imports kept
# PS3 settings, which draw the menus and dialogs a third larger).
$ps3Reset = Join-Path $Out 'userdata\ps3_reimport_v29.txt'
$ps3List = Join-Path $Out 'userdata\ps3_saves_imported.txt'
if (-not (Test-Path -LiteralPath $ps3Reset)) {
  if (Test-Path -LiteralPath $ps3List) {
    $bak = Join-Path $Out ("save_backups\ps3-reimport-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    foreach ($line in @(Get-Content -LiteralPath $ps3List)) {
      $slot = ($line -split ' ')[1]
      if ($slot -notmatch '^ff13-\d\d\.dat$') { continue }
      $saveDir = Join-Path $Out "userdata\B13EBABEBABEBABE\53510806\00000001\$slot"
      $header = Join-Path $Out "userdata\B13EBABEBABEBABE\53510806\Headers\00000001\$slot.header"
      New-Item -ItemType Directory -Force -Path $bak | Out-Null
      if (Test-Path -LiteralPath $saveDir) {
        Copy-Item -LiteralPath $saveDir -Destination $bak -Recurse -Force
        Remove-Item -LiteralPath $saveDir -Recurse -Force
      }
      if (Test-Path -LiteralPath $header) {
        Copy-Item -LiteralPath $header -Destination $bak -Force
        Remove-Item -LiteralPath $header -Force
      }
      Say "  PS3 save in $slot removed (backup: $bak); the game imports it again at its start"
    }
    Remove-Item -LiteralPath $ps3List -Force
  }
  $fixedMarker = Join-Path $Out 'userdata\ps3_gameconfig_fixed.txt'
  if (Test-Path -LiteralPath $fixedMarker) { Remove-Item -LiteralPath $fixedMarker -Force }
  New-Item -ItemType Directory -Force -Path (Split-Path $ps3Reset) | Out-Null
  Set-Content -LiteralPath $ps3Reset -Value 'done'
}
# The saves. Once (v30): the test saves of the dialog-size tests (their text says TEST n) and
# copies of the PS3 save imported by earlier builds that the import list no longer names are moved
# to save_backups. Then, at every run, what each slot holds.
$saveRoot = Join-Path $Out 'userdata\B13EBABEBABEBABE\53510806\00000001'
$headRoot = Join-Path $Out 'userdata\B13EBABEBABEBABE\53510806\Headers\00000001'
function Read-SaveHead([string]$app) {
  $b = New-Object byte[] 0x110
  $fs = [IO.File]::OpenRead($app)
  try { $got = $fs.Read($b, 0, 0x110) } finally { $fs.Dispose() }
  if ($got -lt 0x110) { return $null }
  $text = ([Text.Encoding]::ASCII.GetString($b, 0x10, 0x100).Split([char]0)[0]) -replace "`r?`n", ', '
  # 0x0000004008004320: the flags the PS3 import writes (the game writes its own at the next save)
  $ps3Flags = ($b[8] -eq 0) -and ($b[9] -eq 0) -and ($b[10] -eq 0) -and ($b[11] -eq 0x40) -and ($b[12] -eq 8) -and ($b[13] -eq 0) -and ($b[14] -eq 0x43) -and ($b[15] -eq 0x20)
  return @{ Text = $text; Ps3Flags = $ps3Flags }
}
function Read-ImportList([string]$file) {
  $m = @{}
  if (Test-Path -LiteralPath $file) {
    foreach ($line in @(Get-Content -LiteralPath $file)) { $f = $line -split ' '; if ($f.Count -ge 2) { $m[$f[1]] = $true } }
  }
  return $m
}
$ps3Listed = Read-ImportList (Join-Path $Out 'userdata\ps3_saves_imported.txt')
$steamListed = Read-ImportList (Join-Path $Out 'userdata\steam_saves_imported.txt')
$saveCleanup = Join-Path $Out 'userdata\save_cleanup_v30.txt'
if ((Test-Path -LiteralPath $saveRoot) -and -not (Test-Path -LiteralPath $saveCleanup)) {
  $ps3Texts = @{}
  foreach ($slot in $ps3Listed.Keys) {
    $app = Join-Path $saveRoot "$slot\APP.DAT"
    if (Test-Path -LiteralPath $app) { $h = Read-SaveHead $app; if ($h) { $ps3Texts[$h.Text] = $true } }
  }
  $bak = Join-Path $Out ("save_backups\cleanup-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
  foreach ($dir in @(Get-ChildItem -LiteralPath $saveRoot -Directory -Filter 'ff13-*.dat' | Sort-Object Name)) {
    $app = Join-Path $dir.FullName 'APP.DAT'
    if (-not (Test-Path -LiteralPath $app)) { continue }
    $h = Read-SaveHead $app
    if (-not $h) { continue }
    $why = ''
    if ($h.Text -match '(^|, )TEST \d+') { $why = 'a test save' }
    elseif ($h.Ps3Flags -and -not $ps3Listed.ContainsKey($dir.Name) -and $ps3Texts.ContainsKey($h.Text)) { $why = 'an older copy of the imported PS3 save' }
    if (-not $why) { continue }
    New-Item -ItemType Directory -Force -Path $bak | Out-Null
    Copy-Item -LiteralPath $dir.FullName -Destination $bak -Recurse -Force
    Remove-Item -LiteralPath $dir.FullName -Recurse -Force
    $head = Join-Path $headRoot "$($dir.Name).header"
    if (Test-Path -LiteralPath $head) { Copy-Item -LiteralPath $head -Destination $bak -Force; Remove-Item -LiteralPath $head -Force }
    Say "  removed $($dir.Name) (${why}: $($h.Text)); backup in $bak"
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $saveCleanup) | Out-Null
  Set-Content -LiteralPath $saveCleanup -Value 'done'
}
if (Test-Path -LiteralPath $saveRoot) {
  Say '  Saves (the PS3 and Steam saves are imported when the game starts; a new import shows at the next run):'
  foreach ($dir in @(Get-ChildItem -LiteralPath $saveRoot -Directory -Filter 'ff13-*.dat' | Sort-Object Name)) {
    $app = Join-Path $dir.FullName 'APP.DAT'
    if (-not (Test-Path -LiteralPath $app)) { continue }
    $h = Read-SaveHead $app
    $n = [int]($dir.Name.Substring(5, 2)) + 1
    $from = if ($ps3Listed.ContainsKey($dir.Name)) { 'PS3 save, imported' }
            elseif ($steamListed.ContainsKey($dir.Name)) { 'Steam save, imported' }
            elseif ($h -and $h.Ps3Flags) { 'PS3 save, imported by an earlier build' }
            else { '360 save' }
    Say ("    Save Data {0:D2}  {1,-38} {2}" -f $n, $from, $(if ($h) { $h.Text } else { '(unreadable)' }))
  }
}
$voiceFile = Join-Path $Out 'userdata\voice.txt'
if ((Test-Path -LiteralPath (Join-Path $run 'jpvoice\filelist_patch_u.x360.bin.with_lipsync')) -and
    (Test-Path -LiteralPath $voiceFile) -and (Get-Content -LiteralPath $voiceFile -TotalCount 1) -eq 'en' -and
    -not (Test-Path -LiteralPath (Join-Path $Out 'userdata\voice_reset_v10.txt'))) {
  Set-Content -LiteralPath $voiceFile -Value 'jp'
  Set-Content -LiteralPath (Join-Path $Out 'userdata\voice_reset_v10.txt') -Value 'done'
  Say '  Voice Language set back to Japanese (once; Settings > Port Settings changes it)'
}

# Without -VoiceTest, a patch narrowed by an earlier test gets all its voice files back.
$voiceRestore = -not $VoiceTest -and (Test-Path -LiteralPath (Join-Path $run 'jpvoice\filelist_patch_u.x360.bin.voices'))
if ($voiceRestore) { $VoiceTest = 'all' }
if ($VoiceTest) {
  $jpDir = Join-Path $run 'jpvoice'
  if (-not (Test-Path -LiteralPath (Join-Path $jpDir 'filelist_patch_u.x360.bin'))) { throw 'no Japanese voices to test (step 7)' }
  $exe = Join-Path (Join-Path $Out 'work') 'x2_jpvoice.exe'
  Build-X2Converter $t (Join-Path $Here 'converters\x2_jpvoice.cpp') $exe
  $rc = Invoke-Logged $exe @('--subset', $jpDir, $VoiceTest) (Join-Path $logs 'voicetest.log')
  Get-Content -LiteralPath (Join-Path $logs 'voicetest.log') | ForEach-Object { Say "  $_" }
  if ($rc -ne 0) { throw 'the voice test setup failed' }
  if ($voiceRestore) {
    # every voice file is back: the saved test lists are no longer needed
    Get-ChildItem -LiteralPath $jpDir -Filter '*.voices' | Remove-Item -Force
  } else {
    Set-Content -LiteralPath (Join-Path $Out 'userdata\voice.txt') -Value 'jp'
    Say "  Voice Language set to Japanese for this test" 'Cyan'
  }
}

# --- 8. 360 DLC and title update (installed in step 1)
Say '[8/9] 360 downloadable content and title update'
Say "  title update: $tuDir"
# What each installed DLC package holds (for the log).
$dlcRoot = Join-Path $Out 'userdata\0000000000000000\53510806\00000002'
$dlcList = Join-Path $logs 'dlc_files.txt'
Set-Content -LiteralPath $dlcList -Value "Installed DLC under $dlcRoot"
$dlcNames = @()
if (Test-Path -LiteralPath $dlcRoot) {
  foreach ($d in Get-ChildItem -LiteralPath $dlcRoot -Directory) { $dlcNames += $d.Name }
  foreach ($f in Get-ChildItem -LiteralPath $dlcRoot -File -Recurse) {
    Add-Content -LiteralPath $dlcList -Value ('{0}  {1:N0} bytes' -f $f.FullName.Substring($dlcRoot.Length + 1), $f.Length)
  }
}
Add-Content -LiteralPath $dlcList -Value "Title update under $tuDir"
foreach ($f in Get-ChildItem -LiteralPath $tuDir -File -Recurse) {
  Add-Content -LiteralPath $dlcList -Value ('{0}  {1:N0} bytes' -f $f.FullName.Substring($tuDir.Length + 1), $f.Length)
}
if ($dlcNames.Count) { Say "  DLC packages installed: $($dlcNames.Count) ($dlcRoot)" }
else { Say '  no DLC packages installed' 'Yellow' }
# Left over from version 17 (the DLC archives renamed for the game without its title update):
# no longer used.
$pkgStage = Join-Path $run 'update_packages'
if (Test-Path -LiteralPath $pkgStage) {
  foreach ($c in Get-ChildItem -LiteralPath $pkgStage -Force) {
    if ($c.Attributes -band [IO.FileAttributes]::ReparsePoint) { [IO.Directory]::Delete($c.FullName, $false) }
    else { Remove-Item -LiteralPath $c.FullName -Recurse -Force }
  }
  Remove-Item -LiteralPath $pkgStage -Force
}
$dlcForce = Join-Path $Out 'userdata\dlc.txt'
if (Test-Path -LiteralPath $dlcForce) { Remove-Item -LiteralPath $dlcForce -Force }
# The title update and the Japanese voices share the update folder: combined into update_merged
# (again whenever either changes).
$jpDir = Join-Path $run 'jpvoice'
$mergedDir = Join-Path $run 'update_merged'
if ((Test-Path -LiteralPath (Join-Path $tuDir 'x2_title_update.txt')) -and (Test-Path -LiteralPath (Join-Path $jpDir 'filelist_patch_u.x360.bin'))) {
  $stamp = Join-Path $mergedDir 'x2_merged.txt'
  $newest = @(Get-ChildItem -LiteralPath $jpDir -File -Filter 'filelist_patch*.bin') + @(Get-Item -LiteralPath (Join-Path $tuDir 'x2_title_update.txt')) |
    Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not (Test-Path -LiteralPath $stamp) -or (Get-Item -LiteralPath $stamp).LastWriteTime -lt $newest.LastWriteTime) {
    $exe = Join-Path (Join-Path $Out 'work') 'x2_jpvoice.exe'
    Build-X2Converter $t (Join-Path $Here 'converters\x2_jpvoice.cpp') $exe
    Say '  combining the title update and the Japanese voices...'
    $rc = Invoke-Logged $exe @('--merge-update', $tuDir, $jpDir, $mergedDir) (Join-Path $logs 'merge_update.log')
    Get-Content -LiteralPath (Join-Path $logs 'merge_update.log') -ErrorAction SilentlyContinue | ForEach-Object { Say "    $_" }
    if ($rc -ne 0) { Say '  combining failed; the voices stay English while the title update is used' 'Yellow' }
  } else {
    Say "  title update + Japanese voices: $mergedDir"
  }
}

# Samples for the next steps of the port (into the logs zip): the title menu's texture sheets
# (to relabel DOWNLOADABLE CONTENT as QUIT TO DESKTOP), read raw out of the 360 archive, and
# the 360 save's APP.DAT (to work out the Steam save conversion).
$samples = Join-Path $logs 'samples'
New-Item -ItemType Directory -Force -Path $samples | Out-Null
$scr = Join-Path $Game 'alba_data\sys\white_scru.x360.bin'
if (Test-Path -LiteralPath $scr) {
  # name, position (2048-byte units), stored size - from filelist_scru.x360.bin
  foreach ($e in @(@('title0.x360.xgr', 0x16ca, 0xb34c), @('title0.x360.imgb', 0x16e1, 0xae8d7),
                   @('title1.x360.xgr', 0x184f, 0xc95), @('title1.x360.imgb', 0x1851, 0x42aba))) {
    $fs = [IO.File]::OpenRead($scr)
    try {
      $fs.Position = [int64]$e[1] * 2048
      $buf = New-Object byte[] $e[2]
      $n = $fs.Read($buf, 0, $buf.Length)
      $sampleFile = [IO.File]::Create((Join-Path $samples ($e[0] + '.raw')))
      try { $sampleFile.Write($buf, 0, $n) } finally { $sampleFile.Dispose() }
    } finally { $fs.Dispose() }
  }
}
$jpReport = Join-Path $run 'jpvoice\report.csv'
if (Test-Path -LiteralPath $jpReport) { Copy-Item -LiteralPath $jpReport -Destination (Join-Path $samples 'jpvoice_report.csv') -Force }
$app = Join-Path $Out 'userdata\B13EBABEBABEBABE\53510806\00000001\ff13-00.dat\APP.DAT'
if (Test-Path -LiteralPath $app) { Copy-Item -LiteralPath $app -Destination (Join-Path $samples 'APP.DAT') -Force }

# --- 9. run
Say '[9/9] Running FFXIII-2'
$runLog = Join-Path $logs 'run.log'
Get-ChildItem -LiteralPath $logs -Filter 'run*.log' -ErrorAction SilentlyContinue | Remove-Item -Force
$gameArgs = @("--game_data_root=$gameRun", "--user_data_root=$(Join-Path $Out 'userdata')", '--gpu_plugin=xenos',
          '--graphics_backend=d3d12', "--log_file=$runLog", '--log_max_file_size_mb=300')
$quoted = ($gameArgs | ForEach-Object { [LrRun]::Quote($_) }) -join ' '
$p = Start-Process -FilePath (Join-Path $run 'ffxiii2.exe') -ArgumentList $quoted -WorkingDirectory $run -PassThru
$null = $p.Handle  # Windows PowerShell 5.1 reports no exit code without it
if ($Minutes -gt 0) { Say "  the game runs for up to $Minutes minutes; close its window to stop it sooner" } else { Say '  close the game window when you have seen enough (or when it stops responding)' }
$limit = if ($Minutes -gt 0) { [TimeSpan]::FromMinutes($Minutes) } else { [TimeSpan]::MaxValue }
$rsw = [Diagnostics.Stopwatch]::StartNew()
while (-not $p.WaitForExit(15000)) {
  $l = Get-LastLine $runLog
  if ($l.Length -gt 110) { $l = $l.Substring(0, 107) + '...' }
  Say ("    ... {0:hh\:mm\:ss} {1}" -f $rsw.Elapsed, $l) 'DarkGray'
  if ($rsw.Elapsed -ge $limit) { Say '  time is up; stopping the game'; $p.Kill(); $p.WaitForExit(); break }
}
# Quitting to the title screen starts a new game process and ends this one (x2_hooks.cpp):
# keep waiting while one runs.
while ($rsw.Elapsed -lt $limit) {
  $next = Get-Process -Name 'ffxiii2' -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $next) { break }
  if (-not $next.WaitForExit(15000)) {
    $l = Get-LastLine ((Get-ChildItem -LiteralPath $logs -Filter 'run*.log' | Sort-Object LastWriteTime | Select-Object -Last 1).FullName)
    if ($l.Length -gt 110) { $l = $l.Substring(0, 107) + '...' }
    Say ("    ... {0:hh\:mm\:ss} {1}" -f $rsw.Elapsed, $l) 'DarkGray'
  }
}
if ($rsw.Elapsed -ge $limit) { Get-Process -Name 'ffxiii2' -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill() } }
Say ("  the game ended after {0:hh\:mm\:ss} (exit code {1})" -f $rsw.Elapsed, $p.ExitCode)

# --- summary + logs zip
$runLogs = @(Get-ChildItem -LiteralPath $logs -Filter 'run*.log' | ForEach-Object { $_.FullName })
$lineCount = 0; foreach ($f in $runLogs) { $lineCount += @([IO.File]::ReadLines($f)).Count }
$fatal = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern '\[critical\]|FATAL' | ForEach-Object { $_.Line } })
$av = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern 'access violation' }).Count
$missing = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern "NtCreateFile\] FAILED: path='([^']+)'" | ForEach-Object { $_.Matches[0].Groups[1].Value } | Sort-Object -Unique })
Say ''
Say 'Summary' 'Cyan'
Say "  log lines: $lineCount; access violations: $av"
if ($runLogs -and -not (Select-String -LiteralPath $runLogs -Pattern 'XIII-2 build: test v38' -Quiet)) {
  Say '  THE GAME THAT RAN IS AN OLDER BUILD (no "XIII-2 build: test v38" line in the log)' 'Red'
}
if ($runLogs -and -not (Select-String -LiteralPath $runLogs -Pattern 'XEX patch applied successfully' -Quiet)) {
  Say '  THE TITLE UPDATE PATCH WAS NOT APPLIED (no "XEX patch applied" line in the log)' 'Red'
}
foreach ($f in $fatal | Select-Object -First 5) { Say "  $f" 'Yellow' }
$dlcInfo = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern 'XIII-2 (DLC mode|title update|voices):|XEX patch applied' | ForEach-Object { $_.Line -replace '^\[[^\]]*\] \[[^\]]*\] \[[^\]]*\] \[[^\]]*\] ', '' } | Select-Object -Unique })
foreach ($f in $dlcInfo | Select-Object -First 8) { Say "  $f" }
$menuInfo = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern 'XIII-2 (save delete|title menu: D|save key|Steam saves|PS3 saves|settings: option 9|config menu: Port|memory: (Appl|no )|frame rate: (120|.*chosen|.*120 fps setting|.*refused|the game is told|.*did not get going))' | ForEach-Object { $_.Line -replace '^\[[^\]]*\] \[[^\]]*\] \[[^\]]*\] \[[^\]]*\] ', '' } | Select-Object -Unique })
foreach ($f in $menuInfo | Select-Object -First 16) { Say "  $f" }
# The DLC packages the game mounted and the DLC files it opened (the first ones).
$dlcSeen = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern "XamContentCreateEx\(.* type 2,|XIII-2 DLC files: CreateFile\('(DLC|dlc)" | ForEach-Object { $_.Line -replace '^\[[^\]]*\] \[[^\]]*\] \[[^\]]*\] \[[^\]]*\] ', '' } | Select-Object -Unique })
Say "  DLC: $($dlcSeen.Count) package mount(s) / file opens logged"
foreach ($f in $dlcSeen | Select-Object -First 12) {
  if ($f.Length -gt 160) { $f = $f.Substring(0, 157) + '...' }
  Say "    $f"
}
$crash = @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern 'XIII-2 (crash|null read|hang): (access|exception|abort|the game thread|guest lr|registers pointing)' | ForEach-Object { $_.Line } })
# The game's own last messages before a crash (the last 8 of the ones the crash report lists).
$crash += @(if ($runLogs) { Select-String -LiteralPath $runLogs -Pattern 'XIII-2 crash:   ' | Select-Object -Last 8 | ForEach-Object { $_.Line } })
foreach ($f in $crash | Select-Object -First 14) {
  $f = $f -replace '^\[[^\]]*\] \[[^\]]*\] \[[^\]]*\] \[[^\]]*\] ', ''
  if ($f.Length -gt 160) { $f = $f.Substring(0, 157) + '...' }
  Say "  $f" 'Yellow'
}
if ($missing.Count) { Say "  files the game looked for and did not find ($($missing.Count)):"; $missing | Select-Object -First 12 | ForEach-Object { Say "    $_" } }
$downloads = Join-Path $HOME 'Downloads'
$zip = Join-Path $downloads 'XIII-2_Test_Logs.zip'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $logs '*') -DestinationPath $zip
Say ''
Say ("Done in {0:hh\:mm\:ss}. Upload this file: {1}" -f $sw.Elapsed, $zip) 'Green'
