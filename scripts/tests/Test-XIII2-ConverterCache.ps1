param([string]$ScratchRoot = [IO.Path]::GetTempPath())
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../builder/x2_jpvoice.ps1')
$root = Join-Path $ScratchRoot ('converter-cache-test-' + [guid]::NewGuid().ToString('N'))
$script:calls = 0
function Invoke-Logged {
  param($exe,$arguments,$log)
  $script:calls++
  [IO.File]::WriteAllText($arguments[-1],'compiled fixture')
  return 0
}
try {
  $srcDir = Join-Path $root 'converters'
  $incDir = Join-Path $root 'project/src'
  foreach ($dir in @($srcDir,$incDir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $source = Join-Path $srcDir 'x2_jpvoice.cpp'
  $header = Join-Path $incDir 'x2_jp_dlc_provenance.h'
  $exe = Join-Path $root 'bin/x2_jpvoice.exe'
  [IO.File]::WriteAllText($source,'source version one')
  [IO.File]::WriteAllText($header,'header version one')
  Build-X2Converter @{clangxx='mock compiler'} $source $exe
  Build-X2Converter @{clangxx='mock compiler'} $source $exe
  if ($script:calls -ne 1) { throw 'Unchanged converter did not reuse its binary' }
  [IO.File]::WriteAllText($header,'header version two')
  Build-X2Converter @{clangxx='mock compiler'} $source $exe
  if ($script:calls -ne 2) { throw 'Changed provenance helper did not rebuild converter' }
  [IO.File]::WriteAllText($source,'source version two')
  Build-X2Converter @{clangxx='mock compiler'} $source $exe
  if ($script:calls -ne 3) { throw 'Changed converter source did not rebuild binary' }
  Write-Output 'PASS converter cache: unchanged binary reused; changed source and provenance header rebuild'
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
