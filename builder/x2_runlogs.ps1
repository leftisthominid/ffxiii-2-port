# Preserve every run and relaunch log before the next game process starts.
function Save-X2RunLogs([string]$logs) {
  $files = @(Get-ChildItem -LiteralPath $logs -File -Filter 'run*.log' -ErrorAction SilentlyContinue)
  if (-not $files.Count) { return $null }
  $name = (Get-Date -Format 'yyyyMMdd-HHmmss') + '_' + [guid]::NewGuid().ToString('N')
  $history = Join-Path $logs ('run-history\' + $name)
  New-Item -ItemType Directory -Path $history -Force | Out-Null
  foreach ($file in $files) { Move-Item -LiteralPath $file.FullName -Destination (Join-Path $history $file.Name) }
  return $history
}
