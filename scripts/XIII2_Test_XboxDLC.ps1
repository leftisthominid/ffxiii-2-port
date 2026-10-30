param(
    [string]$Out = 'C:\FFXIII-2 Test',
    [string]$Game = 'C:\FFXIII-2\360\game',
    [string]$Content = 'C:\FFXIII-2\Content',
    [string]$Steam = 'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2',
    [string]$LrRoot = 'C:\Lightning Returns',
    [string]$SourceZip = '',
    [ValidateSet('en','jp')][string]$Voice = 'en'
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) { throw 'Close FFXIII-2 before restoring DLC.' }
$python = Join-Path $LrRoot 'tools\python\tools\python.exe'
foreach ($path in @($Game, $LrRoot, $Out)) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Required folder missing: $path" }
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python missing: $python" }
$userData = Join-Path $Out 'userdata'
$titleRoot = Join-Path $userData '0000000000000000\53510806'
$packages = Join-Path $titleRoot '00000002'
$headers = Join-Path $titleRoot 'Headers\00000002'
$tracking = Join-Path $Out 'work\steamdlc\moved_360_packages.txt'
$restore = @()
if (Test-Path -LiteralPath $tracking) {
    foreach ($line in @(Get-Content -LiteralPath $tracking)) {
        if (-not $line.Trim()) { continue }
        $fields = $line -split "`t", 2
        if ($fields.Count -ne 2 -or $fields[0] -notmatch '^[0-9A-Fa-f]{40}$') { throw 'Invalid Xbox DLC backup record; nothing restored.' }
        $destination = Join-Path $packages $fields[0]
        $backup = Join-Path $fields[1] $fields[0]
        if (-not (Test-Path -LiteralPath $destination -PathType Container) -and -not (Test-Path -LiteralPath $backup -PathType Container)) {
            throw "Original Xbox DLC backup missing: $backup. No packages have been moved."
        }
        $restore += [pscustomobject]@{Name=$fields[0];Destination=$destination;Backup=$backup}
    }
}
$downloads = Join-Path $env:USERPROFILE 'Downloads'
$session = Join-Path $downloads ('XIII-2_XboxDLC_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $session | Out-Null
$zipHash = '9f30d81e9fc05ac4f56f660559db0c406bace79f085000528d34b8b4db9c167a'
if (-not $SourceZip) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $SourceZip = Join-Path $session 'XIII-2_Test.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/leftisthominid/ffxiii-2-port/5e11bc672c8ed1e068555954183b1ab9e19c49a8/releases/v48/XIII-2_Test.zip' -OutFile $SourceZip
}
if ((Get-FileHash -LiteralPath $SourceZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $zipHash) { throw 'Source ZIP checksum mismatch; no packages moved.' }
Expand-Archive -LiteralPath $SourceZip -DestinationPath $session
$source = Join-Path $session 'XIII-2_Test'
$builder = Join-Path $source 'XIII-2_Test.ps1'
$collector = Join-Path $source 'scripts\XIII2_Collect_Diagnostics.py'
$backupRoot = Join-Path $Out ('save_backups\xbox-dlc-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
$voiceName = if ($Voice -eq 'jp') { 'Japanese' } else { 'English' }
$steps = @("Original Xbox 360 DLC baseline; $voiceName selected. Gameplay validation pending.")
if (Test-Path -LiteralPath $tracking) { Copy-Item -LiteralPath $tracking -Destination (Join-Path $backupRoot 'moved_360_packages.txt') }
Write-Host "Reversible content backup: $backupRoot" -ForegroundColor Cyan
try {
    New-Item -ItemType Directory -Path $headers -Force | Out-Null
    # Retain converted content outside the mounted package folder.
    $converted = Join-Path $backupRoot 'converted-packages'
    New-Item -ItemType Directory -Path $converted | Out-Null
    foreach ($package in @(Get-ChildItem -LiteralPath $packages -Directory -Filter 'XIII2SteamDLC*' -ErrorAction SilentlyContinue)) {
        Move-Item -LiteralPath $package.FullName -Destination $converted
        $header = Join-Path $headers ($package.Name + '.header')
        if (Test-Path -LiteralPath $header) { Move-Item -LiteralPath $header -Destination $converted }
    }
    $movies = Join-Path $Out 'run\movies_steam'
    if (Test-Path -LiteralPath $movies) { Move-Item -LiteralPath $movies -Destination (Join-Path $backupRoot 'movies_steam') }
    $missing = Join-Path $userData 'x2_missing_movies.txt'
    if (Test-Path -LiteralPath $missing) { Move-Item -LiteralPath $missing -Destination $backupRoot }
    $voicePath = Join-Path $userData 'voice.txt'
    if (Test-Path -LiteralPath $voicePath) { Copy-Item -LiteralPath $voicePath -Destination (Join-Path $backupRoot 'voice.txt') }
    [IO.File]::WriteAllText($voicePath, $Voice, [Text.Encoding]::ASCII)
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Out $Out -LrRoot $LrRoot -Game $Game -Content $Content -Steam $Steam -SteamDlc restore -SkipLegacyLogZip
    if ($LASTEXITCODE -ne 0) { throw "Xbox DLC restore failed: exit $LASTEXITCODE" }
    foreach ($item in $restore) {
        if (-not (Test-Path -LiteralPath $item.Destination -PathType Container)) { throw "Original package was not restored: $($item.Name)" }
    }
    if (@(Get-ChildItem -LiteralPath $packages -Directory -Filter 'XIII2SteamDLC*').Count) { throw 'Converted Steam DLC remains mounted.' }
    $originals = @(Get-ChildItem -LiteralPath $packages -Directory | Where-Object { $_.Name -match '^[0-9A-Fa-f]{40}$' })
    if (-not $originals.Count) { throw 'No original Xbox DLC packages found; game was not started.' }
    $steps += 'Original package folders: ' + (($originals | ForEach-Object {$_.Name}) -join ', ')
    Write-Host "Xbox DLC restored. Converted content retained in $backupRoot" -ForegroundColor Green
    Write-Host "Starting $voiceName test with original Xbox DLC. Keep the game closed during post-run collection."
    # Direct builder invocation: no Steam-derived DLC installation.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Out $Out -LrRoot $LrRoot -Game $Game -Content $Content -Steam $Steam -SkipLegacyLogZip
    if ($LASTEXITCODE -ne 0) { throw "Build/run reported exit $LASTEXITCODE" }
} catch {
    $_.ToString() | Set-Content -LiteralPath (Join-Path $session 'failure.txt') -Encoding UTF8
    throw
} finally {
    $steps | Set-Content -LiteralPath (Join-Path $session 'steps.txt') -Encoding ASCII
    @{version=48;content_source='original-xbox360';requested_voice=$Voice;windows_gameplay_validated=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $session 'source-version.json') -Encoding ASCII
    if (-not (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue)) {
        & $python -u $collector --game-output $Out --out $session --session $session
    } else { Write-Host "Game is still running; collect after closing it. Session folder: $session" }
}
