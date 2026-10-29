param([string]$ScratchRoot = [IO.Path]::GetTempPath())
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../builder/x2_jpvoice.ps1')
function Say { param($message,$color) }
function Build-X2Converter { }
$root = Join-Path $ScratchRoot ('jp-publication-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$script:mode = ''
function Invoke-Logged {
  param($exe,$arguments,$log)
  $stage = $arguments[-1]
  $ready = Join-Path $stage '0000071'
  New-Item -ItemType Directory -Path $ready | Out-Null
  [IO.File]::WriteAllText((Join-Path $ready 'white_p0000071img_u.x360.bin'),'new archive')
  if ($script:mode -eq 'exception') { throw 'Simulated interruption' }
  if ($script:mode -eq 'failed') { return 1 }
  if ($script:mode -eq 'empty-index') { return 0 }
  [IO.File]::WriteAllText((Join-Path $ready 'filelist_p0000071img_u.x360.bin'),'new index')
  return 0
}
try {
  foreach ($mode in @('failed','exception','empty-index','success')) {
    $script:mode = $mode
    $case = Join-Path $root $mode
    $packages = Join-Path $case 'packages'
    $pkg = Join-Path $packages 'XIII2SteamDLC0000071'
    $out = Join-Path $case 'out'
    $active = Join-Path $out '0000071'
    $work = Join-Path $case 'work'
    $logs = Join-Path $case 'logs'
    foreach ($path in @($pkg,$active,$work,$logs)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    foreach ($name in @('filelist_p0000071img_u.x360.bin','white_p0000071img_u.x360.bin')) {
      [IO.File]::WriteAllText((Join-Path $pkg $name),'source media')
      [IO.File]::WriteAllText((Join-Path $active $name),'stale copy')
    }
    $threw = $false
    try { Convert-X2JpDlcVoices @{} 'steam' 'ffmpeg' $packages $out $work $logs } catch { $threw = $true }
    $shouldThrow = $mode -in @('exception','empty-index')
    if ($threw -ne $shouldThrow) { throw "Unexpected exception result: $mode" }
    if ($mode -eq 'success') {
      if ([IO.File]::ReadAllText((Join-Path $active 'filelist_p0000071img_u.x360.bin')) -ne 'new index') { throw 'New index missing' }
      if ([IO.File]::ReadAllText((Join-Path $active 'white_p0000071img_u.x360.bin')) -ne 'new archive') { throw 'New archive missing' }
      if (-not (Test-Path -LiteralPath (Join-Path $out '0000071.txt'))) { throw 'Success stamp missing' }
    } else {
      if (Test-Path -LiteralPath $active) { throw "Stale or partial DLC remained active: $mode" }
      if (Test-Path -LiteralPath (Join-Path $out '0000071.txt')) { throw 'Failure stamped as success' }
    }
    if (@(Get-ChildItem -LiteralPath $work -Directory -Filter 'jpvoice_dlc_staging_*').Count) { throw 'Staging not cleaned' }
    if ([IO.File]::ReadAllText((Join-Path $pkg 'white_p0000071img_u.x360.bin')) -ne 'source media') { throw 'Source modified' }
    Write-Output "PASS $mode"
  }
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
