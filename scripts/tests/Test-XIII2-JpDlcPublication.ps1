param([string]$ScratchRoot = [IO.Path]::GetTempPath())
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../builder/x2_jpvoice.ps1')
function Say { param($message,$color) }
function Build-X2Converter { if ($script:mode -eq 'compile-failed') { throw 'Simulated compiler failure' } }
$root = Join-Path $ScratchRoot ('jp-publication-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$script:mode = ''
function Invoke-Logged {
  param($exe,$arguments,$log)
  if ($arguments[0] -eq '--verify-dlc') {
    $script:verifies++
    if ($script:mode -eq 'cached-current') { return 0 }
    return 1
  }
  $stage = $arguments[-1]
  $ready = Join-Path $stage '0000071'
  New-Item -ItemType Directory -Path $ready | Out-Null
  [IO.File]::WriteAllText((Join-Path $ready 'white_p0000071img_u.x360.bin'),'new archive')
  if ($script:mode -eq 'exception') { throw 'Simulated interruption' }
  if ($script:mode -eq 'failed') { return 1 }
  if ($script:mode -eq 'empty-index') { return 0 }
  [IO.File]::WriteAllText((Join-Path $ready 'filelist_p0000071img_u.x360.bin'),'new index')
  if ($script:mode -ne 'no-provenance') { [IO.File]::WriteAllText((Join-Path $ready 'source_filelist_p0000071img_u.x360.bin'),'source media') }
  if ($script:mode -ne 'no-validation') { [IO.File]::WriteAllText((Join-Path $ready 'verified_filelist_p0000071img_u.x360.bin'),'new index') }
  return 0
}
try {
  foreach ($mode in @('compile-failed','failed','exception','empty-index','no-provenance','no-validation','success','cached-current','cached-stale')) {
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
    $script:verifies = 0
    if ($mode -like 'cached-*') {
      $arcs = @(Get-ChildItem -LiteralPath $pkg -File | Where-Object { $_.Name -like 'filelist_p*' -or $_.Name -like 'white_p*' } | Sort-Object Name)
      $sig = "$script:X2JpDlcVersion $(Split-Path $pkg -Leaf) " + (($arcs | ForEach-Object { "$($_.Name)=$($_.Length)@$($_.LastWriteTimeUtc.Ticks)" }) -join ' ')
      Set-Content -LiteralPath (Join-Path $out '0000071.txt') -Value $sig
    }
    $threw = $false
    try { Convert-X2JpDlcVoices @{} 'steam' 'ffmpeg' $packages $out $work $logs } catch { $threw = $true }
    $shouldThrow = $mode -in @('compile-failed','exception','empty-index','no-provenance','no-validation')
    if ($threw -ne $shouldThrow) { throw "Unexpected exception result: $mode" }
    if ($mode -eq 'cached-current') {
      if ($script:verifies -ne 1) { throw 'Cached copy was not verified' }
      if ([IO.File]::ReadAllText((Join-Path $active 'white_p0000071img_u.x360.bin')) -ne 'stale copy') { throw 'Verified cache unexpectedly rebuilt' }
    } elseif ($mode -in @('success','cached-stale')) {
      if ($mode -eq 'cached-stale' -and $script:verifies -ne 1) { throw 'Stale cache verification bypassed' }
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
