param(
    [string]$ZipPath = '',
    [string]$Out = 'C:\FFXIII-2 Test',
    [string]$LrRoot = 'C:\Lightning Returns'
)
$ErrorActionPreference = 'Stop'
$downloads = Join-Path $env:USERPROFILE 'Downloads'
$expected = '8d05b11e81be3bf1bc569323ecf9e866938ea9388ff5aab9a3df61b206b9614d'
if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) { throw 'Close the game before running this update.' }
if (-not $ZipPath) {
    $ZipPath = Get-ChildItem -LiteralPath $downloads -File |
        Where-Object { $_.Name -match '^XIII-2_Test( \(\d+\))?\.zip$' } |
        Sort-Object LastWriteTime -Descending |
        Where-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expected } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ZipPath -or -not (Test-Path -LiteralPath $ZipPath)) { throw 'Download the v41 XIII-2_Test.zip into Downloads first.' }
if ((Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'This ZIP does not match the published v41 build. Nothing was installed.' }
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0,6)
$work = Join-Path $downloads "XIII-2_v41_$stamp"
New-Item -ItemType Directory -Path $work | Out-Null
Expand-Archive -LiteralPath $ZipPath -DestinationPath $work
$source = Join-Path $work 'XIII-2_Test'
$builder = Join-Path $source 'XIII-2_Test.ps1'
$collector = Join-Path $source 'scripts\XIII2_Collect_v41.py'
$py = Join-Path $LrRoot 'tools\python\tools\python.exe'
if (-not (Select-String -LiteralPath (Join-Path $source 'project\src\ffxiii2_app.h') -Pattern 'XIII-2 build: test v41' -Quiet)) { throw 'Extracted source version check failed.' }
$failure = $null
$steps = @('v41 session started: ' + (Get-Date -Format o))
$oldPrint = [Environment]::GetEnvironmentVariable('X2_GAME_PRINT','Process')
try {
    Write-Host 'Installing full Steam DLC: inventory, movies, audio and packages. Initial learning can take 15-40 minutes.' -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Out $Out -LrRoot $LrRoot -SteamDlc install
    if ($LASTEXITCODE -ne 0) { throw "DLC installation failed: exit $LASTEXITCODE" }
    $steps += 'DLC installation passed: ' + (Get-Date -Format o)
    $env:X2_GAME_PRINT = '1'
    Write-Host 'Building v41 and starting the game. Close the game when testing is finished; results will be collected automatically.' -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $builder -Out $Out -LrRoot $LrRoot
    if ($LASTEXITCODE -ne 0) { throw "Build/run failed: exit $LASTEXITCODE" }
    $steps += 'Builder/game command ended: ' + (Get-Date -Format o)
} catch {
    $failure = $_
    Write-Host $_ -ForegroundColor Red
} finally {
    [Environment]::SetEnvironmentVariable('X2_GAME_PRINT',$oldPrint,'Process')
    $steps | Set-Content -LiteralPath (Join-Path $work 'steps.txt') -Encoding ASCII
    if ($failure) { $failure.ToString() | Set-Content -LiteralPath (Join-Path $work 'failure.txt') -Encoding UTF8 }
    if (Test-Path -LiteralPath $py) {
        $job = Start-Job -ArgumentList $py,$collector,$Out,$work,$source -ScriptBlock {
            param($python,$script,$output,$session,$code)
            $ErrorActionPreference = 'Stop'
            & $python -u $script $output $session $code
            if ($LASTEXITCODE -ne 0) { throw "Collection failed: exit $LASTEXITCODE" }
        }
        try {
            while ($job.State -in @('NotStarted','Running')) {
                $null = Wait-Job -Job $job -Timeout 15
                Receive-Job -Job $job
                if ($job.State -in @('NotStarted','Running')) { Write-Host 'Collecting results...' }
            }
            Receive-Job -Job $job
            if ($job.State -ne 'Completed') { Write-Host "Result collection failed; logs remain in $Out\logs" -ForegroundColor Red }
        } catch { Write-Host "Result collection failed: $_" -ForegroundColor Red }
        finally { Remove-Job -Job $job -Force }
    } else { Write-Host "Python unavailable for collection; logs remain in $Out\logs" -ForegroundColor Red }
    $upload = Join-Path $work 'Upload-these-zips'
    if (Test-Path -LiteralPath $upload) {
        Write-Host "Upload every ZIP in: $upload" -ForegroundColor Green
        Start-Process explorer.exe -ArgumentList ('"' + $upload + '"')
    } else { Start-Process explorer.exe -ArgumentList ('"' + $work + '"') }
}
if ($failure) { throw "The update stopped. Details are in $work" }
