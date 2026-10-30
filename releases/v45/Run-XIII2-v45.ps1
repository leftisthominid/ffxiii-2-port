param(
    [string]$ZipPath = '',
    [string]$Out = 'C:\FFXIII-2 Test',
    [string]$LrRoot = 'C:\Lightning Returns',
    [string]$Game = 'C:\FFXIII-2\360\game',
    [string]$Steam = 'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2',
    [switch]$CollectOnly,
    [ValidateRange(1,8)][int]$GameSessions = 1
)
$ErrorActionPreference = 'Stop'
$downloads = Join-Path $env:USERPROFILE 'Downloads'
$expected = 'cd7ee7a95730188e9bc269a71aca31a132eda711286a3dd222941822c6fc5443'
if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) { throw 'Close the game before installing or collecting files.' }
if (-not $ZipPath) {
    $ZipPath = Get-ChildItem -LiteralPath $downloads -File |
        Where-Object { $_.Name -match '^XIII-2_Test( \(\d+\))?\.zip$' } |
        Sort-Object LastWriteTime -Descending |
        Where-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expected } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ZipPath -or -not (Test-Path -LiteralPath $ZipPath)) { throw 'Download the v45 XIII-2_Test.zip into Downloads first.' }
if ((Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Source ZIP checksum mismatch. Nothing was installed.' }
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0,6)
$work = Join-Path $downloads "XIII-2_v45_$stamp"
New-Item -ItemType Directory -Path $work | Out-Null
Expand-Archive -LiteralPath $ZipPath -DestinationPath $work
$source = Join-Path $work 'XIII-2_Test'
$builder = Join-Path $source 'XIII-2_Test.ps1'
$collector = Join-Path $source 'scripts\XIII2_Collect_CompleteDLC.py'
$py = Join-Path $LrRoot 'tools\python\tools\python.exe'
if (-not (Select-String -LiteralPath (Join-Path $source 'project\src\ffxiii2_app.h') -Pattern 'XIII-2 build: test v45' -Quiet)) { throw 'Extracted source version check failed.' }
$failure = $null
$collectionFailure = $null
$steps = @('v45 source candidate session started: ' + (Get-Date -Format o))
@{version=45; game_sessions=$GameSessions; source_zip_sha256=$expected; mode=$(if ($CollectOnly) {'collect-only'} else {'install-build-run'}); windows_gameplay_validated=$false} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $work 'source-version.json') -Encoding ASCII
$oldPrint = [Environment]::GetEnvironmentVariable('X2_GAME_PRINT','Process')
try {
    if (-not $CollectOnly) {
        Write-Host 'Installing Steam-derived DLC. Initial learning and conversion can take 15-40 minutes.' -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Out $Out -LrRoot $LrRoot -Game $Game -Steam $Steam -SteamDlc install -SkipLegacyLogZip
        if ($LASTEXITCODE -ne 0) { throw "DLC installation failed: exit $LASTEXITCODE" }
        $steps += 'DLC installation passed: ' + (Get-Date -Format o)
        $env:X2_GAME_PRINT = '1'
        for ($session = 1; $session -le $GameSessions; $session++) {
            Write-Host "Building/running v45 source candidate: game session $session of $GameSessions. Gameplay fixes remain unverified." -ForegroundColor Cyan
            if ($session -lt $GameSessions) { Write-Host 'If testing Japanese next, select Japanese voices in settings before closing this game window. The next session starts before collection.' }
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Out $Out -LrRoot $LrRoot -Game $Game -Steam $Steam -SkipLegacyLogZip
            if ($LASTEXITCODE -ne 0) { throw "Build/run failed in session ${session}: exit $LASTEXITCODE" }
            $steps += "Builder/game session $session ended: " + (Get-Date -Format o)
        }
    } else { $steps += 'Collection only; no installation, build or gameplay requested.' }
} catch {
    $failure = $_
    Write-Host $_ -ForegroundColor Red
} finally {
    [Environment]::SetEnvironmentVariable('X2_GAME_PRINT',$oldPrint,'Process')
    $steps | Set-Content -LiteralPath (Join-Path $work 'steps.txt') -Encoding ASCII
    if ($failure) { $failure.ToString() | Set-Content -LiteralPath (Join-Path $work 'failure.txt') -Encoding UTF8 }
    if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) {
        $collectionFailure = 'Game is still running. Files must stay stable during collection.'
    } elseif (Test-Path -LiteralPath $py) {
        $job = Start-Job -ArgumentList $py,$collector,$Out,$work,$source,$Game,$Steam -ScriptBlock {
            param($python,$script,$output,$session,$code,$game,$steam)
            $ErrorActionPreference = 'Stop'
            & $python -u $script --source $code --out $session --game-output $output --session $session --base-game $game --steam $steam --no-explorer
            if ($LASTEXITCODE -ne 0) { throw "Collection incomplete: exit $LASTEXITCODE" }
        }
        try {
            while ($job.State -in @('NotStarted','Running')) {
                $null = Wait-Job -Job $job -Timeout 15
                Receive-Job -Job $job -ErrorAction Continue
                if ($job.State -in @('NotStarted','Running')) { Write-Host 'Collecting comprehensive results; keep installed files unchanged...' }
            }
            Receive-Job -Job $job -ErrorAction Continue
            if ($job.State -ne 'Completed') { $collectionFailure = 'Collection incomplete; upload available ZIPs and collection-status.json.' }
        } catch { $collectionFailure = "Collection failed: $_" }
        finally { Remove-Job -Job $job -Force }
    } else { $collectionFailure = "Python unavailable for collection; logs remain in $Out\logs" }
    $upload = Join-Path $work 'Upload-these-zips'
    if (Test-Path -LiteralPath $upload) {
        $status = Join-Path $work 'collection-status.json'
        if (Test-Path -LiteralPath $status) { Copy-Item -LiteralPath $status -Destination $upload }
        Write-Host "Upload every ZIP and collection-status.json from: $upload" -ForegroundColor Green
        Start-Process explorer.exe -ArgumentList ('"' + $upload + '"')
    } else { Start-Process explorer.exe -ArgumentList ('"' + $work + '"') }
}
if ($collectionFailure) { Write-Host $collectionFailure -ForegroundColor Red }
if ($failure -or $collectionFailure) { throw "The session has failures. Results and status are in $work" }
