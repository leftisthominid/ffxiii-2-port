# The downloadable content from the Steam version (converters\x2dlc, Python): the Steam version
# has all the DLC in its own archives, in the PC format; x2dlc converts it to the 360 packages the
# port loads, learning what it cannot work out from the base game (every file in both versions).
#   compare  learn, then convert the files of the 360 DLC packages installed here from the Steam
#            version and compare each with the real one (a report; nothing is installed), then
#            export the material shader programs of both versions (XIII-2_Shaders.partNN.bin in
#            Downloads: for working out the shader compilation)
#   install  learn, convert, move the 360 packages of the same DLC aside (save_backups) and install
#            the converted ones (for testing the game as a player without the 360 DLC)
#   restore  the converted packages out, the 360 ones back
# Python: the pinned portable CPython 3.12.7 (NuGet package) and numpy 2.1.3, in the Lightning
# Returns build's tools folder (shared). Dot-sourced by XIII-2_Test.ps1.

$script:X2DlcPyUrl = 'https://api.nuget.org/v3-flatcontainer/python/3.12.7/python.3.12.7.nupkg'
$script:X2DlcPySha = '149dd298e0b7a82250ca019471770fff079874088a4e8501ca20922d7df3a6ac'
$script:X2DlcNpUrl = 'https://files.pythonhosted.org/packages/a6/84/fa11dad3404b7634aaab50733581ce11e5350383311ea7a7010f464c0170/numpy-2.1.3-cp312-cp312-win_amd64.whl'
$script:X2DlcNpSha = '0d30c543f02e84e92c4b1f415b7c6b5326cbe45ee7882b6b77db7195fb971e3a'
# Bumped when the converter changes what it learns (base_data.pkl is made again).
$script:X2DlcLearnVersion = '5'
# Bumped when what dlcknown finds changes (dlc_known.pkl is made again).
$script:X2DlcKnownVersion = '5'

function Get-X2DlcFile([string]$url, [string]$sha, [string]$file) {
  if ((Test-Path -LiteralPath $file) -and (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $sha) { return }
  New-Item -ItemType Directory -Force -Path (Split-Path $file) | Out-Null
  Say "  downloading $url"
  Invoke-Download $url $file
  $h = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
  if ($h -ne $sha) { Remove-Item -LiteralPath $file -Force; throw "SHA-256 mismatch for $url ($h)" }
}

function Get-X2DlcPython([string]$toolsDir) {
  $pyDir = Join-Path $toolsDir 'python'
  $py = Join-Path $pyDir 'tools\python.exe'
  if (-not (Test-Path -LiteralPath $py)) {
    $pkg = Join-Path $toolsDir 'downloads\python.3.12.7.nupkg.zip'
    Get-X2DlcFile $script:X2DlcPyUrl $script:X2DlcPySha $pkg
    if (Test-Path -LiteralPath $pyDir) { Remove-Item -LiteralPath $pyDir -Recurse -Force }
    Expand-Archive -LiteralPath $pkg -DestinationPath $pyDir
  }
  & $py -I -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('numpy') else 1)"
  if ($LASTEXITCODE) {
    $whl = Join-Path $toolsDir 'downloads\numpy-2.1.3-cp312-cp312-win_amd64.whl'
    Get-X2DlcFile $script:X2DlcNpUrl $script:X2DlcNpSha $whl
    # (pip writes notices to stderr, which Windows PowerShell would treat as an error)
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $py -I -m pip install --no-index --no-deps --disable-pip-version-check --no-warn-script-location --quiet $whl 2>&1 | ForEach-Object { Say "  pip: $_" }
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $eap
    if ($rc) { throw 'numpy did not install into the portable Python' }
  }
  return $py
}

# Runs python -m x2dlc <arguments> from converters\, logged (progress every 15 s).
function Invoke-X2Dlc([string]$py, [string[]]$arguments, [string]$log) {
  $conv = (Resolve-Path (Join-Path $PSScriptRoot '..\converters')).Path
  $boot = "import sys, runpy; sys.path.insert(0, r'$conv'); sys.argv = ['x2dlc'] + sys.argv[1:]; runpy.run_module('x2dlc', run_name='__main__')"
  $rc = Invoke-Logged $py (@('-I', '-u', '-c', $boot) + $arguments) $log
  return $rc
}

function Invoke-X2SteamDlc([string]$mode, [string]$game, [string]$tuDir, [string]$steam, [string]$toolsDir, [string]$out, [string]$logs) {
  $userdata = Join-Path $out 'userdata'
  $work = Join-Path $out 'work\steamdlc'
  $root = Join-Path $userdata '0000000000000000\53510806'
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  if ($mode -eq 'restore') {
    $n = 0
    foreach ($d in @(Get-ChildItem -LiteralPath (Join-Path $root '00000002') -Directory -Filter 'XIII2SteamDLC*' -ErrorAction SilentlyContinue)) {
      Remove-Item -LiteralPath $d.FullName -Recurse -Force
      $h = Join-Path $root "Headers\00000002\$($d.Name).header"
      if (Test-Path -LiteralPath $h) { Remove-Item -LiteralPath $h -Force }
      Say "  removed the converted $($d.Name)"; $n++
    }
    $moved = Join-Path $work 'moved_360_packages.txt'
    if (Test-Path -LiteralPath $moved) {
      foreach ($line in @(Get-Content -LiteralPath $moved)) {
        $name, $from = $line -split "`t"
        $dest = Join-Path $root "00000002\$name"
        if (-not (Test-Path -LiteralPath $dest) -and (Test-Path -LiteralPath (Join-Path $from $name))) {
          Move-Item -LiteralPath (Join-Path $from $name) -Destination $dest
          if (Test-Path -LiteralPath (Join-Path $from "$name.header")) {
            Move-Item -LiteralPath (Join-Path $from "$name.header") -Destination (Join-Path $root "Headers\00000002\$name.header") -Force
          }
          Say "  360 package $name back"
        }
      }
      Remove-Item -LiteralPath $moved -Force
    }
    # (the list of movies the converted packages lacked: the 360 ones have them)
    $mm = Join-Path $userdata 'x2_missing_movies.txt'
    if (Test-Path -LiteralPath $mm) { Remove-Item -LiteralPath $mm -Force }
    Say "  restore done ($n converted package(s) removed)" 'Green'
    return
  }
  if (-not $steam -or -not (Test-Path -LiteralPath (Join-Path $steam 'alba_data\sys\filelistu.win32.bin'))) {
    throw "No Steam version at '$steam' (give -Steam <folder>)"
  }
  Say '  portable Python'
  $py = Get-X2DlcPython $toolsDir
  $ffmpeg = Get-Ffmpeg $toolsDir
  # learning (once per converter version; about 15-40 minutes: it reads both versions' archives)
  $data = Join-Path $work 'base_data.pkl'
  $stamp = Join-Path $work 'learn_version.txt'
  if (-not (Test-Path -LiteralPath $data) -or -not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -TotalCount 1) -ne $script:X2DlcLearnVersion) {
    Say '  learning from the base game (both versions; 15-40 minutes)'
    $rc = Invoke-X2Dlc $py @('learn', '--x360', $game, '--tu', $tuDir, '--steam', $steam, '--work', $work) (Join-Path $logs 'steamdlc_learn.log')
    if ($rc -ne 0) { throw "Learning failed; see $logs\steamdlc_learn.log" }
    Set-Content -LiteralPath $stamp -Value $script:X2DlcLearnVersion
  } else {
    Say "  learned data from an earlier run: $data"
  }
  # the DLC's bundle members that the base game has (a few minutes; again when learning is)
  $dk = Join-Path $work 'dlc_known.pkl'
  $dkStamp = Join-Path $work 'dlc_known_version.txt'
  $dkWant = "$($script:X2DlcLearnVersion) $($script:X2DlcKnownVersion)"
  if (-not (Test-Path -LiteralPath (Join-Path $work 'steam_manifest.json')) -or -not (Test-Path -LiteralPath $dk) -or -not (Test-Path -LiteralPath $dkStamp) -or (Get-Content -LiteralPath $dkStamp -TotalCount 1) -ne $dkWant) {
    Say '  finding the DLC files the base game already has (a few minutes)'
    $rc = Invoke-X2Dlc $py @('dlcknown', '--x360', $game, '--tu', $tuDir, '--steam', $steam, '--work', $work) (Join-Path $logs 'steamdlc_dlcknown.log')
    if ($rc -ne 0) { throw "Finding the base-game DLC files failed; see $logs\steamdlc_dlcknown.log" }
    Set-Content -LiteralPath $dkStamp -Value $dkWant
  }
  if ($mode -eq 'compare') {
    Say '  converting the files of your 360 DLC from the Steam version and comparing them'
    $cargs = @('compare', '--steam', $steam, '--work', $work, '--userdata', $userdata, '--ffmpeg', $ffmpeg)
    if ($script:X2DlcOnly) { $cargs += @('--only', $script:X2DlcOnly); Say "  only the files under: $($script:X2DlcOnly)" }
    $rc = Invoke-X2Dlc $py $cargs (Join-Path $logs 'steamdlc_compare.log')
    if ($rc -ne 0) { throw "The comparison failed; see $logs\steamdlc_compare.log" }
  } elseif ($mode -eq 'export') {
    # the shader programs of both versions, for the compiler checks
    Say '  exporting the shader programs of both versions (a few minutes)'
    $dl = Join-Path $HOME 'Downloads'
    $rc = Invoke-X2Dlc $py @('export', '--x360', $game, '--tu', $tuDir, '--steam', $steam, '--work', $work, '--userdata', $userdata, '--out', $dl) (Join-Path $logs 'steamdlc_export.log')
    if ($rc -ne 0) { throw "The shader export failed; see $logs\steamdlc_export.log" }
  } elseif ($mode -eq 'install') {
    Say '  refreshing the Steam-derived content inventory'
    $rc = Invoke-X2Dlc $py @('plan', '--x360', $game, '--tu', $tuDir, '--steam', $steam, '--work', $work) (Join-Path $logs 'steamdlc_inventory.log')
    if ($rc -ne 0) { throw "Steam inventory failed; see $logs\steamdlc_inventory.log" }
    Say '  decoding full Steam DLC movies and audio (prepared movies are reused)'
    $movieOut = Join-Path $out 'run\movies_steam'
    $rc = Invoke-X2Dlc $py @('movies', '--x360', $game, '--tu', $tuDir, '--steam', $steam, '--work', $work, '--out', $movieOut, '--ffmpeg', $ffmpeg) (Join-Path $logs 'steamdlc_movies.log')
    if ($rc -ne 0) { throw "Steam movie preparation failed; see $logs\steamdlc_movies.log" }
    Say '  converting and installing the Steam DLC (files unchanged since the last build are reused)'
    # Reuse only files whose conversion keys still match, including the audio headers.
    $rc = Invoke-X2Dlc $py @('build', '--steam', $steam, '--work', $work, '--out', (Join-Path $work 'packages'), '--userdata', $userdata, '--install', '--replace', '--ffmpeg', $ffmpeg) (Join-Path $logs 'steamdlc_build.log')
    if ($rc -ne 0) { throw "The conversion failed; see $logs\steamdlc_build.log" }
    # the 360 packages of the same DLC aside, so the game takes the converted ones
    $bak = Join-Path $out ("save_backups\dlc360-{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $moved = Join-Path $work 'moved_360_packages.txt'
    foreach ($d in @(Get-ChildItem -LiteralPath (Join-Path $root '00000002') -Directory -ErrorAction SilentlyContinue)) {
      if ($d.Name -like 'XIII2SteamDLC*') { continue }
      $idf = Join-Path $d.FullName 'identifier'
      if (-not (Test-Path -LiteralPath $idf)) { continue }
      $id = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($idf), 0, 7)
      if ($id -notin @('0000021', '0000023', '0000071')) { continue }
      New-Item -ItemType Directory -Force -Path $bak | Out-Null
      Move-Item -LiteralPath $d.FullName -Destination $bak
      $h = Join-Path $root "Headers\00000002\$($d.Name).header"
      if (Test-Path -LiteralPath $h) { Move-Item -LiteralPath $h -Destination $bak }
      Add-Content -LiteralPath $moved -Value "$($d.Name)`t$bak"
      Say "  360 package $($d.Name) (DLC $id) moved to $bak (-SteamDlc restore puts it back)"
    }
  }
  # the report
  $rep = Join-Path $work 'report'
  if (Test-Path -LiteralPath $rep) { Remove-Item -LiteralPath $rep -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $rep | Out-Null
  foreach ($f in @((Join-Path $work 'compare.txt'), (Join-Path $work 'build_report.json'), (Join-Path $logs 'steamdlc_learn.log'), (Join-Path $logs 'steamdlc_dlcknown.log'),
                   (Join-Path $logs 'steamdlc_compare.log'), (Join-Path $logs 'steamdlc_build.log'), (Join-Path $logs 'steamdlc_export.log'))) {
    if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination $rep }
  }
  # examples of converted files that differ from the real 360 ones (ours, 360, PC)
  $ex = Join-Path $work 'report_files'
  if (Test-Path -LiteralPath $ex) { Copy-Item -LiteralPath $ex -Destination $rep -Recurse }
  $zip = Join-Path (Join-Path $HOME 'Downloads') 'XIII-2_SteamDLC_Report.zip'
  if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
  Compress-Archive -Path (Join-Path $rep '*') -DestinationPath $zip
  Say ("Steam DLC ($mode) done. Send {0} ({1:N1} MB)." -f $zip, ((Get-Item -LiteralPath $zip).Length / 1MB)) 'Green'
  $parts = @(Get-ChildItem -LiteralPath (Join-Path $HOME 'Downloads') -Filter 'XIII-2_Shaders.part*.bin' -ErrorAction SilentlyContinue)
  if ($mode -eq 'export' -and $parts.Count) {
    Say ("Also send the {0} shader file(s) in Downloads: {1}" -f $parts.Count, (($parts | ForEach-Object { $_.Name }) -join ', ')) 'Green'
  }
}
