param([string]$ScratchRoot = [IO.Path]::GetTempPath())
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../builder/x2_jpvoice.ps1')
function Say { param($message,$color) }
function Build-X2Converter {}
function Invoke-Logged {
  param($exe,$arguments,$log)
  if ($arguments[0] -eq '--strip-lipsync') { throw 'Old voice identities must be rebuilt' }
  $script:calls++
  $script:lastArguments=@($arguments)
  $stage = $arguments[-1]
  New-Item -ItemType Directory -Path $stage | Out-Null
  [IO.File]::WriteAllText((Join-Path $stage 'filelist_patch_u.x360.bin'),'new voice identities')
  return 0
}
$root = Join-Path $ScratchRoot ('jp-cache-test-' + [guid]::NewGuid().ToString('N'))
try {
  $steam = Join-Path $root 'steam'
  $sys = Join-Path $steam 'alba_data/sys'
  New-Item -ItemType Directory -Path $sys -Force | Out-Null
  $source = Join-Path $sys 'filelistc.win32.bin'
  [IO.File]::WriteAllText($source,'read-only source fixture')
  foreach ($old in @('3','4',$script:X2JpVoiceVersion)) {
    $case = Join-Path $root $old
    $out = Join-Path $case 'out'
    $work = Join-Path $case 'work'
    $logs = Join-Path $case 'logs'
    foreach ($path in @($out,$work,$logs)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    [IO.File]::WriteAllText((Join-Path $out 'version.txt'),$old)
    [IO.File]::WriteAllText((Join-Path $out 'filelist_patch_u.x360.bin'),'old voice identities')
    [IO.File]::WriteAllText((Join-Path $out 'jp_lipsync.txt'),'legacy migration marker')
    $script:calls = 0
    Convert-X2JpVoice @{} 'game' $steam 'ffmpeg' $out $work $logs
    $expected = if ($old -eq $script:X2JpVoiceVersion) { 0 } else { 1 }
    if ($script:calls -ne $expected) { throw "Unexpected conversion count for version $old" }
    if ((Get-Content -LiteralPath (Join-Path $out 'version.txt') -Raw).Trim() -ne $script:X2JpVoiceVersion) { throw 'Wrong cache version' }
    if ($expected -eq 1 -and [IO.File]::ReadAllText((Join-Path $out 'filelist_patch_u.x360.bin')) -ne 'new voice identities') { throw 'Old voice identities reused' }
    Convert-X2JpVoice @{} 'game' $steam 'ffmpeg' $out $work $logs
    if ($script:calls -ne $expected) { throw 'Current cache rebuilt unnecessarily' }
    Write-Output "PASS voice cache version $old"
  }
  $script:calls=0
  Convert-X2JpVoice @{} 'game' $steam 'ffmpeg' $out $work $logs -LipSync
  if ($script:calls -ne 1 -or $script:lastArguments -notcontains '--lipsync') {throw 'Lip-sync did not invalidate the audio-only cache or enable the converter flag'}
  Convert-X2JpVoice @{} 'game' $steam 'ffmpeg' $out $work $logs -LipSync
  if ($script:calls -ne 1) {throw 'Lip-sync cache was not reused'}
  Convert-X2JpVoice @{} 'game' $steam 'ffmpeg' $out $work $logs
  if ($script:calls -ne 2 -or $script:lastArguments -contains '--lipsync') {throw 'Audio-only conversion reused the lip-sync cache'}
  Write-Output 'PASS independent lip-sync cache and converter flag'
  if ([IO.File]::ReadAllText($source) -ne 'read-only source fixture') { throw 'Source changed' }
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
