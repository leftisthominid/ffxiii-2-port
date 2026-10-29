param(
    [Parameter(Mandatory=$true)][string]$ReleaseDir,
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$ScratchRoot = [IO.Path]::GetTempPath()
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $ScratchRoot ('release-launcher-test-' + [guid]::NewGuid().ToString('N'))
$launcher = Join-Path $ReleaseDir 'Run-XIII2-v44.ps1'
$sourceZip = Join-Path $ReleaseDir 'XIII-2_Test.zip'
$oldProfile = $env:USERPROFILE
if (Test-Path variable:global:XIII2LauncherTestState) { throw 'Launcher test state already exists; use an isolated PowerShell process.' }
$oldExitCode = $global:LASTEXITCODE
$global:XIII2LauncherTestState = @{mode='';calls=0;opened=0}
function Get-Process { if ($global:XIII2LauncherTestState.mode -eq 'game-running') { return [pscustomobject]@{Name='ffxiii2'} } }
function Start-Process { param($FilePath,$ArgumentList) $global:XIII2LauncherTestState.opened++ }
function powershell.exe {
    # Only installation/game execution is mocked. ZIP validation, extraction,
    # background collection, status handling and folder-opening control flow run.
    if ($args -notcontains '-SkipLegacyLogZip') { throw 'Launcher must use the comprehensive collector without a duplicate legacy ZIP' }
    $global:XIII2LauncherTestState.calls++
    if ($global:XIII2LauncherTestState.mode -eq 'install-failure' -and $global:XIII2LauncherTestState.calls -eq 1) { $global:LASTEXITCODE = 7 }
    elseif ($global:XIII2LauncherTestState.mode -eq 'game-failure' -and $global:XIII2LauncherTestState.calls -eq 2) { $global:LASTEXITCODE = 8 }
    else { $global:LASTEXITCODE = 0 }
}
try {
    foreach ($mode in @('collect-only','success','two-runs','install-failure','game-failure','wrong-zip','game-running')) {
        $global:XIII2LauncherTestState.mode = $mode; $global:XIII2LauncherTestState.calls = 0; $global:XIII2LauncherTestState.opened = 0
        $case = Join-Path $root $mode
        $downloads = Join-Path $case 'Downloads'
        $gameOut = Join-Path $case 'output'
        $steam = Join-Path $case 'steam'
        $lr = Join-Path $case 'lr'
        $py = Join-Path $lr 'tools/python/tools/python.exe'
        foreach ($dir in @($downloads,(Join-Path $gameOut 'work/steamdlc'),(Join-Path $gameOut 'logs'),(Join-Path $steam 'alba_data'),(Split-Path $py))) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        New-Item -ItemType SymbolicLink -Path $py -Target $Python | Out-Null
        [IO.File]::WriteAllText((Join-Path $gameOut 'work/steamdlc/steam_manifest.json'),' {"packs": {}} ')
        [IO.File]::WriteAllText((Join-Path $gameOut 'logs/run.log'),'Launcher fixture only; no gameplay ran.')
        $generated = Join-Path $gameOut 'project/generated/default'
        New-Item -ItemType Directory -Path $generated -Force | Out-Null
        $definitions = (@('828F0CD0','829E8E88','829C51E0','82B04830','82B04E48') | ForEach-Object { "DEFINE_REX_FUNC(sub_$_) {`nreturn;`n}`n" }) -join ''
        [IO.File]::WriteAllText((Join-Path $generated 'fixture.cpp'),$definitions)
        $zip = Join-Path $downloads 'XIII-2_Test (2).zip'
        Copy-Item -LiteralPath $sourceZip -Destination $zip
        [IO.File]::WriteAllText((Join-Path $downloads 'XIII-2_Test (3).zip'),'wrong checksum fixture')
        $env:USERPROFILE = $case
        $threw = $false
        try {
            $params = @{Out=$gameOut;LrRoot=$lr;Steam=$steam;Game=(Join-Path $case 'base')}
            if ($mode -eq 'collect-only') { $params.CollectOnly = $true }
            if ($mode -eq 'two-runs') { $params.GameSessions = 2 }
            if ($mode -eq 'wrong-zip') { $params.ZipPath = Join-Path $downloads 'XIII-2_Test (3).zip' }
            & $launcher @params
        } catch { $threw = $true }
        $expectedFailure = $mode -in @('install-failure','game-failure','wrong-zip','game-running')
        if ($threw -ne $expectedFailure) { throw "Unexpected launcher failure status: $mode" }
        $expectedCalls = switch ($mode) { 'success' {2} 'two-runs' {3} 'install-failure' {1} 'game-failure' {2} default {0} }
        if ($global:XIII2LauncherTestState.calls -ne $expectedCalls) { throw "Unexpected builder invocation count: $mode" }
        if ($mode -in @('wrong-zip','game-running')) {
            if ($global:XIII2LauncherTestState.opened -ne 0) { throw 'Rejected input opened a result folder' }
        } else {
            if ($global:XIII2LauncherTestState.opened -ne 1) { throw 'Result folder did not open exactly once' }
            $sessions = @(Get-ChildItem -LiteralPath $downloads -Directory -Filter 'XIII-2_v44_*')
            if ($sessions.Count -ne 1) { throw 'Expected one result session' }
            $upload = Join-Path $sessions[0].FullName 'Upload-these-zips'
            if (-not (Test-Path -LiteralPath (Join-Path $upload 'XIII-2_DLC_Complete_Index.zip'))) { throw 'Comprehensive result index missing' }
            $status = Get-Content -LiteralPath (Join-Path $upload 'collection-status.json') -Raw | ConvertFrom-Json
            if ($status.fatal) { throw 'Fixture collection unexpectedly failed' }
            $source = Get-Content -LiteralPath (Join-Path $sessions[0].FullName 'source-version.json') -Raw | ConvertFrom-Json
            if ($source.windows_gameplay_validated) { throw 'Launcher falsely claimed gameplay validation' }
        }
        Write-Output "PASS release launcher $mode (builder/game execution mocked)"
    }
} finally {
    $env:USERPROFILE = $oldProfile
    $global:LASTEXITCODE = $oldExitCode
    Remove-Variable -Scope Global -Name XIII2LauncherTestState
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
