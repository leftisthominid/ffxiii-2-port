# FFXIII-2 PS3 buttons / UI and sharp fonts, with the Lightning Returns port's lr_uiconv:
#   - every UI sheet in both the 360 data (white_scru) and the PS3 disc (white_imgu) is decoded
#     in both versions; the PS3 image of each texture that differs (button icons, controller
#     pictures, ...) goes to <game folder>\textures\ps3ui, named by the 360 texture's hash. The
#     renderer shows it while Button Prompts is PlayStation 3 (Settings > Port Settings).
#   - the font atlases rebuilt at 4x from the 360 glyphs, in textures\replace\fonts.
# converters\x2_ui_sheets.csv: the sheets and where they are (from the decrypted file lists).
$script:X2Ps3UiPack = 'PS3_GAME/USRDIR/alba_data/sys/white_imgu.ps3.bin'
$script:X2Ps3UiPackSize = 2076227584
$script:X2UiVersion = '1'

function Convert-X2Ps3Ui($t, [string]$iso, [string]$game, [string]$textures, [string]$work, [string]$logs) {
  $ps3ui = Join-Path $textures 'ps3ui'
  $fonts = Join-Path $textures 'replace\fonts'
  $versionFile = Join-Path $textures 'x2_ui_version.txt'
  if ((Test-Path -LiteralPath $versionFile) -and (Get-Content -LiteralPath $versionFile -TotalCount 1) -eq $script:X2UiVersion) {
    Say "  already converted ($ps3ui)"
    return
  }
  New-Item -ItemType Directory -Force -Path $work, $logs, (Split-Path $fonts) | Out-Null
  $csv = Join-Path $PSScriptRoot '..\converters\x2_ui_sheets.csv'
  $exe = Join-Path $work 'lr_uiconv.exe'
  if (-not ((Test-Path -LiteralPath $exe) -and (Get-Item -LiteralPath $exe).LastWriteTime -gt (Get-Item -LiteralPath (Join-Path $PSScriptRoot '..\converters\lr_uiconv.cpp')).LastWriteTime)) {
    $xx = Get-ChildItem -LiteralPath (Join-Path $t.sdk 'include') -Recurse -Filter 'xxhash.h' | Select-Object -First 1
    if (-not $xx) { throw "no xxhash.h in $($t.sdk)\include" }
    Say '  building the converter...'
    $rc = Invoke-Logged $t.clangxx @('-O2', '-std=c++17', "-I$($xx.DirectoryName)", (Join-Path $PSScriptRoot '..\converters\lr_uiconv.cpp'), '-o', $exe) "$exe.build.log"
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath $exe)) { throw "could not build lr_uiconv.exe; see $exe.build.log" }
  }
  $sys360 = Join-Path $game 'alba_data\sys'
  # Sharp fonts (360 data only).
  $new = "$fonts.new"
  if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force }
  Say '  rebuilding the fonts at 4x (about a minute)...'
  $rc = Invoke-Logged $exe @('--fonts', $csv, $sys360, $new, '--scale', '4') (Join-Path $logs 'fonts.log') -Quiet
  $n = @(Get-ChildItem -LiteralPath $new -Filter '*.dds' -ErrorAction SilentlyContinue).Count
  if ($rc -eq 0 -and $n -gt 0) {
    if (Test-Path -LiteralPath $fonts) { Remove-Item -LiteralPath $fonts -Recurse -Force }
    Move-Item -LiteralPath $new -Destination $fonts
    Say "  $n font textures in $fonts"
  } else {
    Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
    Say "  no font textures rebuilt (see $(Join-Path $logs 'fonts.log')); the game's own fonts stay" 'Yellow'
  }
  # PS3 UI: white_imgu.ps3.bin copied out of the disc image (2 GB, removed afterwards).
  $at = [LrIso]::Locate($iso, $script:X2Ps3UiPack)
  if ($at[1] -ne $script:X2Ps3UiPackSize) {
    throw ("white_imgu.ps3.bin on this PS3 disc is {0:N0} bytes, not the {1:N0} of the USA disc (BLUS30776)." -f $at[1], $script:X2Ps3UiPackSize)
  }
  $sysPs3 = Join-Path $work 'ps3sys'
  $copy = Join-Path $sysPs3 'white_imgu.ps3.bin'
  New-Item -ItemType Directory -Force -Path $sysPs3 | Out-Null
  if (-not ((Test-Path -LiteralPath $copy) -and (Get-Item -LiteralPath $copy).Length -eq $at[1])) {
    Say '  copying white_imgu.ps3.bin out of the PS3 disc image (2 GB)...'
    $job = Start-Job -ScriptBlock { param($i, $r, $d, $isoPs1) . $isoPs1; [LrIso]::Extract($i, $r, $d) } -ArgumentList $iso, $script:X2Ps3UiPack, $copy, (Join-Path $PSScriptRoot 'iso.ps1')
    try {
      Wait-WithProgress { [bool](Wait-Job $job -Timeout 15) } { '{0:N1} of {1:N1} GB' -f ((Get-Item -LiteralPath $copy -ErrorAction SilentlyContinue).Length / 1GB), ($at[1] / 1GB) }
      Receive-Job $job -ErrorAction Stop | Out-Null
    } finally { Remove-Job $job -Force }
  }
  $new = "$ps3ui.new"
  if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force }
  Say '  comparing the 360 and PS3 UI sheets (a few minutes)...'
  $rc = Invoke-Logged $exe @('--table', $csv, $sys360, $sysPs3, $new) (Join-Path $logs 'ps3ui.log')
  Remove-Item -LiteralPath $sysPs3 -Recurse -Force -ErrorAction SilentlyContinue
  if ($rc -ne 0) {
    Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
    throw "converting the PS3 buttons / UI failed; see $(Join-Path $logs 'ps3ui.log')"
  }
  if (Test-Path -LiteralPath $ps3ui) { Remove-Item -LiteralPath $ps3ui -Recurse -Force }
  Move-Item -LiteralPath $new -Destination $ps3ui
  Copy-Item -LiteralPath (Join-Path $ps3ui 'report.csv') -Destination (Join-Path $logs 'ps3ui_report.csv') -ErrorAction SilentlyContinue
  Set-Content -LiteralPath $versionFile -Value $script:X2UiVersion
  Say ("  {0} PS3 UI textures in {1}" -f @(Get-ChildItem -LiteralPath $ps3ui -Filter '*.dds').Count, $ps3ui)
}

# Title menu without EXTRAS and DOWNLOADABLE CONTENT (the port takes both out): the label sheet
# title_sel01us (gui/resident/title0, 1024x512; the labels are baked into it) is rebuilt at 4x
# like the fonts, with those two labels cleared in both of its columns, into
# textures\replace\title. 360 data only.
$script:X2TitleVersion = '1'
function Convert-X2TitleMenu($t, [string]$game, [string]$textures, [string]$work, [string]$logs) {
  $dir = Join-Path $textures 'replace\title'
  $versionFile = Join-Path $dir 'x2_title_version.txt'
  if ((Test-Path -LiteralPath $versionFile) -and (Get-Content -LiteralPath $versionFile -TotalCount 1) -eq $script:X2TitleVersion) {
    Say "  title menu labels already done ($dir)"
    return
  }
  New-Item -ItemType Directory -Force -Path $work, $logs, (Split-Path $dir) | Out-Null
  $csv = Join-Path $PSScriptRoot '..\converters\x2_ui_sheets.csv'
  $exe = Join-Path $work 'lr_uiconv.exe'
  if (-not ((Test-Path -LiteralPath $exe) -and (Get-Item -LiteralPath $exe).LastWriteTime -gt (Get-Item -LiteralPath (Join-Path $PSScriptRoot '..\converters\lr_uiconv.cpp')).LastWriteTime)) {
    $xx = Get-ChildItem -LiteralPath (Join-Path $t.sdk 'include') -Recurse -Filter 'xxhash.h' | Select-Object -First 1
    if (-not $xx) { throw "no xxhash.h in $($t.sdk)\include" }
    Say '  building the converter...'
    $rc = Invoke-Logged $t.clangxx @('-O2', '-std=c++17', "-I$($xx.DirectoryName)", (Join-Path $PSScriptRoot '..\converters\lr_uiconv.cpp'), '-o', $exe) "$exe.build.log"
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath $exe)) { throw "could not build lr_uiconv.exe; see $exe.build.log" }
  }
  $new = "$dir.new"
  if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force }
  # Label rectangles (x0,y0,x1,y1) in the 1024x512 sheet: EXTRAS and DOWNLOADABLE CONTENT in the
  # small left column and the large right column.
  $rc = Invoke-Logged $exe @('--fonts', $csv, (Join-Path $game 'alba_data\sys'), $new, '--scale', '4', '--sheet', 'gui/resident/title0',
                             '--tex', 'title_sel01us', '--erase', '218,101,318,126', '--erase', '0,126,318,151',
                             '--erase', '880,66,1024,100', '--erase', '560,132,1024,166') (Join-Path $logs 'title_menu.log') -Quiet
  $n = @(Get-ChildItem -LiteralPath $new -Filter '*.dds' -ErrorAction SilentlyContinue).Count
  if ($rc -ne 0 -or $n -eq 0) {
    Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
    throw "the title menu labels could not be rebuilt; see $(Join-Path $logs 'title_menu.log')"
  }
  if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
  Move-Item -LiteralPath $new -Destination $dir
  Set-Content -LiteralPath $versionFile -Value $script:X2TitleVersion
  Say "  title menu without EXTRAS and DOWNLOADABLE CONTENT ($dir)"
}
