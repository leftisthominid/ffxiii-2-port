# FFXIII-2 Japanese voices: the Steam version's Japanese voice lines and lip-sync, converted to
# the 360 layout (converters\x2_jpvoice.cpp) into <game folder>\jpvoice as an update patch. The
# game mounts it when Settings > Port Settings > Voice Language is Japanese.
$script:X2JpVoiceVersion = '4'

# Built again whenever the source changed (by content: a zip's file dates can be older than an
# earlier build).
function Build-X2Converter($t, [string]$source, [string]$exe) {
  $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
  $stamp = "$exe.source"
  if ((Test-Path -LiteralPath $exe) -and (Test-Path -LiteralPath $stamp) -and (Get-Content -LiteralPath $stamp -TotalCount 1) -eq $hash) { return }
  New-Item -ItemType Directory -Force -Path (Split-Path $exe) | Out-Null
  $rc = Invoke-Logged $t.clangxx @('-O2', '-std=c++17', $source, '-o', $exe) "$exe.build.log"
  if ($rc -ne 0 -or -not (Test-Path -LiteralPath $exe)) { throw "Could not build $(Split-Path $exe -Leaf); see $exe.build.log" }
  Set-Content -LiteralPath $stamp -Value $hash
}

# The Japanese voices of the installed 360 DLC packages (the Steam version has the DLC in its own
# archives): x2_jpvoice --dlc makes a copy of each package's archives with its voices converted,
# in <out>\NNNNNNN\ (the port opens those copies while the voices are Japanese). Once per package
# and converter version.
# 2: the lines too large at 24 kHz go down to 16, 11, or 8 kHz; the DLC scenes' Japanese lip-sync.
$script:X2JpDlcVersion = '3'
function Convert-X2JpDlcVoices($t, [string]$steam, [string]$ffmpeg, [string]$dlcRoot, [string]$out, [string]$work, [string]$logs) {
  if (-not (Test-Path -LiteralPath $dlcRoot)) { Say '  no DLC packages installed'; return }
  $exe = Join-Path $work 'x2_jpvoice.exe'
  Build-X2Converter $t (Join-Path $PSScriptRoot '..\converters\x2_jpvoice.cpp') $exe
  New-Item -ItemType Directory -Force -Path $out | Out-Null
  foreach ($pkg in Get-ChildItem -LiteralPath $dlcRoot -Directory) {
    $lists = @(Get-ChildItem -LiteralPath $pkg.FullName -File -Filter 'filelist_p*_u.x360.bin')
    if (-not $lists.Count) { continue }
    $id = $lists[0].Name.Substring(10, 7)
    # every archive of the package by size and date: a package installed again (the converted
    # Steam DLC) can keep its file list sizes
    $arcs = @(Get-ChildItem -LiteralPath $pkg.FullName -File | Where-Object { $_.Name -like 'filelist_p*' -or $_.Name -like 'white_p*' } | Sort-Object Name)
    $sig = "$script:X2JpDlcVersion $($pkg.Name) " + (($arcs | ForEach-Object { "$($_.Name)=$($_.Length)@$($_.LastWriteTimeUtc.Ticks)" }) -join ' ')
    $stamp = Join-Path $out "$id.txt"
    if ((Test-Path -LiteralPath $stamp) -and (Get-Content -LiteralPath $stamp -TotalCount 1) -eq $sig) {
      Say "  DLC $id`: already done"
      continue
    }
    Say "  DLC $id`: converting its Japanese voices (a few minutes for the big packages)..."
    $log = Join-Path $logs "jpvoice_dlc_$id.log"
    # (--cache: each line's encode kept by its input; a package converted again re-encodes only
    # the lines that changed)
    # Deactivate the previous copy before rebuilding. A failed or interrupted
    # conversion must fall back to the installed package, never stale DLC.
    $active = Join-Path $out $id
    if (Test-Path -LiteralPath $active) {
      $disabled = Join-Path $work ('jpvoice_dlc_disabled\' + $id + '_' + [guid]::NewGuid().ToString('N'))
      New-Item -ItemType Directory -Force -Path (Split-Path $disabled) | Out-Null
      Move-Item -LiteralPath $active -Destination $disabled
    }
    Remove-Item -LiteralPath $stamp -Force -ErrorAction SilentlyContinue
    $staging = Join-Path $work ('jpvoice_dlc_staging_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
      $rc = Invoke-Logged $exe @('--ffmpeg', $ffmpeg, '--cache', (Join-Path $work 'jpvoice_cache'), '--dlc', $steam, $pkg.FullName, $staging) $log
      if ($rc -eq 0 -or $rc -eq 2) {
        $ready = Join-Path $staging $id
        if (Test-Path -LiteralPath $ready) {
          $readyLists = @(Get-ChildItem -LiteralPath $ready -File -Filter 'filelist_p*_u.x360.bin')
          if (-not $readyLists.Count) { throw "Japanese DLC file lists missing: $ready" }
          foreach ($list in $readyLists) {
            $archive = Join-Path $ready ($list.Name -replace '^filelist_', 'white_')
            if (-not (Test-Path -LiteralPath $archive)) { throw "Japanese DLC archive missing: $archive" }
          }
          Move-Item -LiteralPath $ready -Destination $active
        }
        foreach ($csv in Get-ChildItem -LiteralPath $staging -File -Filter '*.csv') {
          Move-Item -LiteralPath $csv.FullName -Destination (Join-Path $out $csv.Name) -Force
        }
      }
    } finally {
      Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    Get-Content -LiteralPath $log -ErrorAction SilentlyContinue | Where-Object { $_ -match ': .*(voice|lip-sync)|kept English:|error' } | Select-Object -First 10 | ForEach-Object { Say "    $_" }
    if ($rc -ne 0 -and $rc -ne 2) { Say "  DLC $id`: the conversion failed; its voices stay English (see $log)" 'Yellow'; continue }
    Set-Content -LiteralPath $stamp -Value $sig
  }
  # What each converted file became (and why some stay English), for the logs.
  foreach ($csv in Get-ChildItem -LiteralPath $out -File -Filter '*.csv' -ErrorAction SilentlyContinue) {
    Copy-Item -LiteralPath $csv.FullName -Destination (Join-Path $logs "jpvoice_dlc_$($csv.Name)") -Force
  }
}

function Convert-X2JpVoice($t, [string]$game, [string]$steam, [string]$ffmpeg, [string]$out, [string]$work, [string]$logs) {
  if (-not (Test-Path -LiteralPath (Join-Path $steam 'alba_data\sys\filelistc.win32.bin'))) {
    throw "no Japanese voice data in $steam (alba_data\sys\filelistc.win32.bin)"
  }
  $versionFile = Join-Path $out 'version.txt'
  $have = if (Test-Path -LiteralPath $versionFile) { Get-Content -LiteralPath $versionFile -TotalCount 1 } else { '' }
  if ($have -eq $script:X2JpVoiceVersion -and (Test-Path -LiteralPath (Join-Path $out 'filelist_patch_u.x360.bin'))) {
    Say "  already converted ($out)"
    return
  }
  # Version 3 differs from 4 only by the Japanese lip-sync / facial motion files, which broke
  # Serah (she did not load after a save was loaded): they are taken out of its file lists.
  if ($have -eq '3' -and (Test-Path -LiteralPath (Join-Path $out 'jp_lipsync.txt'))) {
    $exe = Join-Path $work 'x2_jpvoice.exe'
    Build-X2Converter $t (Join-Path $PSScriptRoot '..\converters\x2_jpvoice.cpp') $exe
    Say '  taking the Japanese lip-sync files out of the voice patch...'
    $log = Join-Path $logs 'jpvoice_strip.log'
    $rc = Invoke-Logged $exe @('--strip-lipsync', $out) $log
    Get-Content -LiteralPath $log -ErrorAction SilentlyContinue | ForEach-Object { Say "    $_" }
    if ($rc -ne 0) { throw "taking the lip-sync files out failed; see $log" }
    Set-Content -LiteralPath $versionFile -Value $script:X2JpVoiceVersion
    Say "  Japanese voices in $out (the 360 lip movements stay)"
    return
  }
  $exe = Join-Path $work 'x2_jpvoice.exe'
  Say '  building the converter...'
  Build-X2Converter $t (Join-Path $PSScriptRoot '..\converters\x2_jpvoice.cpp') $exe
  $tmp = "$out.new"
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  Say '  converting the Japanese voices and lip-sync (10-30 minutes)...'
  $log = Join-Path $logs 'jpvoice.log'
  $rc = Invoke-Logged $exe @('--ffmpeg', $ffmpeg, $game, $steam, $tmp) $log
  Get-Content -LiteralPath $log -ErrorAction SilentlyContinue | Where-Object { $_ -match '^(English voice|Japanese lip|Voices converted|Voice size|Voice files kept|Wrote|  kept English|  lip-sync kept)' } |
    Select-Object -First 12 | ForEach-Object { Say "    $_" }
  if ($rc -ne 0 -and $rc -ne 3) {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    throw "converting the Japanese voices failed; see $log"
  }
  if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
  Move-Item -LiteralPath $tmp -Destination $out
  Set-Content -LiteralPath $versionFile -Value $script:X2JpVoiceVersion
  if ($rc -eq 3) { Say '  fewer than half of the voice files converted; the others stay English' 'Yellow' }
  Say "  Japanese voices in $out (Settings > Port Settings > Voice Language)"
}

# Japanese audio for the recap movies (tm_abst_*, the "previously on" narration): Steam's
# Japanese movies (alba_data\movie\moviec.win32.bin) through ffmpeg into <game folder>\movies_jp
# as <name>.wav. The port plays them instead of the movie's own English tracks when Voice
# Language is Japanese.
$script:X2JpMovieAudioVersion = '1'
function Convert-X2JpMovieAudio($t, [string]$game, [string]$steam, [string]$ffmpeg, [string]$out, [string]$work, [string]$logs) {
  if (-not (Test-Path -LiteralPath (Join-Path $steam 'alba_data\sys\movielistc.win32.bin'))) {
    throw "no Japanese movie list in $steam (alba_data\sys\movielistc.win32.bin)"
  }
  $versionFile = Join-Path $out 'version.txt'
  if ((Test-Path -LiteralPath $versionFile) -and (Get-Content -LiteralPath $versionFile -TotalCount 1) -eq $script:X2JpMovieAudioVersion) {
    Say "  movie audio already converted ($out)"
    return
  }
  $exe = Join-Path $work 'x2_jpvoice.exe'
  Build-X2Converter $t (Join-Path $PSScriptRoot '..\converters\x2_jpvoice.cpp') $exe
  $tmp = "$out.new"
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
  Say '  converting the Japanese movie audio (about a minute)...'
  $log = Join-Path $logs 'jpmovies.log'
  $rc = Invoke-Logged $exe @('--ffmpeg', $ffmpeg, '--movies', $game, $steam, $tmp) $log
  Get-Content -LiteralPath $log -ErrorAction SilentlyContinue | Where-Object { $_ -match '^(Movie audio|  Steam movie folder|error)' } |
    Select-Object -First 8 | ForEach-Object { Say "    $_" }
  if ($rc -ne 0) {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    throw "converting the Japanese movie audio failed; see $log"
  }
  if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
  Move-Item -LiteralPath $tmp -Destination $out
  Set-Content -LiteralPath $versionFile -Value $script:X2JpMovieAudioVersion
  Say "  Japanese movie audio in $out"
}
