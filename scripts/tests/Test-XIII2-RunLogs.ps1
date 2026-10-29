param([string]$ScratchRoot = [IO.Path]::GetTempPath())
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../../builder/x2_runlogs.ps1')
$root = Join-Path $ScratchRoot ('runlogs-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
try {
  [IO.File]::WriteAllText((Join-Path $root 'run.log'),'first run')
  [IO.File]::WriteAllText((Join-Path $root 'run.1.log'),'first rotation')
  [IO.File]::WriteAllText((Join-Path $root 'other.log'),'untouched')
  $first = Save-X2RunLogs $root
  if ([IO.File]::ReadAllText((Join-Path $first 'run.log')) -ne 'first run') { throw 'First run lost' }
  if ([IO.File]::ReadAllText((Join-Path $first 'run.1.log')) -ne 'first rotation') { throw 'Rotated log lost' }
  [IO.File]::WriteAllText((Join-Path $root 'run.log'),'second run')
  $second = Save-X2RunLogs $root
  if ($second -eq $first) { throw 'History directory reused' }
  if ([IO.File]::ReadAllText((Join-Path $second 'run.log')) -ne 'second run') { throw 'Second run lost' }
  if ([IO.File]::ReadAllText((Join-Path $first 'run.log')) -ne 'first run') { throw 'History overwritten' }
  if ([IO.File]::ReadAllText((Join-Path $root 'other.log')) -ne 'untouched') { throw 'Unrelated log changed' }
  if ($null -ne (Save-X2RunLogs $root)) { throw 'Empty run created a history entry' }
  Write-Output 'PASS run logs: runs and rotations preserved; relaunches and unrelated files retained'
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
