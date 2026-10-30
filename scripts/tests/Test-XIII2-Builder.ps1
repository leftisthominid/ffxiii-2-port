# Real state/digest/pipeline tests; native build and gameplay steps are mocked.
$ErrorActionPreference='Stop'
$repo=Split-Path (Split-Path $PSScriptRoot)
. (Join-Path $repo 'builder\iso.ps1')
. (Join-Path $repo 'builder\inputs.ps1')
. (Join-Path $repo 'builder\pipeline.ps1')
function Say($text,$color) { }
function Assert($ok,$message) { if (-not $ok) { throw $message } }
$root=Join-Path ([IO.Path]::GetTempPath()) ('xiii2-builder-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$Here=$root
try {
    Assert (Test-X2PathOverlap $root $root) 'Equal paths accepted'
    Assert (Test-X2PathOverlap (Join-Path $root 'child') $root) 'Nested output accepted'
    Assert (Test-X2PathOverlap $root (Join-Path $root 'child')) 'Enclosing output accepted'
    Assert (-not (Test-X2PathOverlap ($root+'-other') $root)) 'Sibling rejected'
    $state=New-BuildState 'windows'; $state.output=Join-Path $root 'output'
    New-Item -ItemType Directory -Path $state.output | Out-Null
    foreach ($name in @('game','steam')) { New-Item -ItemType Directory -Path (Join-Path $root $name) | Out-Null }
    $update=Join-Path $root 'tu'; Set-Content -LiteralPath $update -Value 'fixture'
    $state.inputs.game360=@{kind='folder';path=(Join-Path $root 'game')}
    $state.inputs.update=@{kind='package';path=$update}
    $state.inputs.steam=@{kind='folder';path=(Join-Path $root 'steam')}
    $steps=Get-Steps $state
    Assert ($steps.Contains('voices') -and -not $steps.Contains('steamdlc')) 'Japanese audio enabled Steam DLC'
    Assert (-not $steps.Contains('dlc')) 'DLC became mandatory'
    Assert (-not $steps.Contains('android') -and -not $steps.Contains('phone')) 'Android target present'
    $state.inputs.dlc360=@{kind='packages';path=(Join-Path $root 'game');packages=@()}
    $steps=Get-Steps $state
    Assert ($steps['dlcvoices'].deps -contains 'dlc') 'DLC voice dependencies incomplete'
    $state.inputs.Remove('dlc360')
    $p=Get-Paths $state
    Assert (-not (Test-StepReady 'renderer' $p)) 'Missing renderer treated as ready'

    # A PS3 folder is read without mounting or changing its files.
    $ps3=Join-Path $root 'ps3'; New-Item -ItemType Directory -Path (Join-Path $ps3 'PS3_GAME') -Force | Out-Null
    $sfo=Join-Path $ps3 'PS3_GAME\PARAM.SFO'; Set-Content -LiteralPath $sfo -Value 'FINAL FANTASY XIII-2'
    Assert ((Test-Ps3 $ps3).kind -eq 'folder') 'PS3 folder rejected'
    $at=Get-X2Ps3Location $ps3 'PS3_GAME/PARAM.SFO'
    Assert ($at[0] -eq 0 -and $at[1] -eq (Get-Item $sfo).Length) 'Wrong PS3 file location'
    $copy=Join-Path $root 'copy.sfo'; Copy-X2Ps3File $ps3 'PS3_GAME/PARAM.SFO' $copy
    Assert ((Get-FileHash $copy).Hash -eq (Get-FileHash $sfo).Hash) 'PS3 copy differs'

    # Exercise the real resume/state logic with mocked external operations.
    $script:counts=@{}; $script:failVoice=$true
    function Mark($key) { if (-not $script:counts.ContainsKey($key)) {$script:counts[$key]=0}; $script:counts[$key]++ }
    function Step-Tools($state,$p) { Mark 'tools' }
    function Step-Game($state,$p) { Mark 'game' }
    function Step-Update($state,$p) { Mark 'update' }
    function Step-Icon($state,$p) { Mark 'icon' }
    function Step-Compile($state,$p) { Mark 'compile' }
    function Step-Renderer($state,$p) { Mark 'renderer' }
    function Step-Install($state,$p) { Mark 'install' }
    function Step-Fonts($state,$p) { Mark 'fonts' }
    function Step-Voices($state,$p) { Mark 'voices'; if ($script:failVoice) {throw 'Simulated voice failure'} }
    function Step-DlcVoices($state,$p) { Mark 'dlcvoices' }
    function Test-StepReady($key,$p) { return $true }
    function Start-Process { param($FilePath,$ArgumentList) }
    $save=Join-Path $state.output 'userdata\save-fixture'; New-Item -ItemType Directory -Path (Split-Path $save) -Force | Out-Null
    Set-Content -LiteralPath $save -Value 'do not modify'
    $saveHash=(Get-FileHash $save).Hash
    $failed=$false
    try { Invoke-Pipeline $state } catch { $failed=$true }
    Assert $failed 'Failed build reported success'
    $state=Read-BuildState $state.output
    Assert (-not $state.finished -and $state.done -notcontains 'voices') 'Failure marked complete'
    Assert ($state.hashes.ContainsKey('compile')) 'Source digests lost during state reload'
    $script:failVoice=$false; Invoke-Pipeline $state
    Assert ($script:counts.compile -eq 1 -and $script:counts.voices -eq 2) 'Resume rebuilt completed program'
    Invoke-Pipeline $state
    Assert ($script:counts.compile -eq 1 -and $script:counts.voices -eq 2) 'Unchanged build repeated content'
    function Step-Compile($state,$p) { Mark 'compile'; $null='changed program source' }
    Invoke-Pipeline $state
    Assert ($script:counts.compile -eq 2 -and $script:counts.install -eq 2 -and $script:counts.voices -eq 2) 'Code update rebuilt unrelated voices or missed install'
    $before=$script:counts.voices
    Set-Content -LiteralPath (Join-Path $root 'steam\changed-archive') -Value 'new input'
    Invoke-Pipeline $state
    Assert ($script:counts.voices -eq ($before+1) -and $script:counts.compile -eq 2) 'Steam input changes did not rebuild only voices'
    Assert ((Get-FileHash $save).Hash -eq $saveHash) 'Save was modified'
    # Exercise real installation using dummy binaries; COM/junction creation is
    # avoided by choosing no shortcut and precreating the save alias fixture.
    . (Join-Path $repo 'builder\pipeline.ps1')
    $p=Get-Paths $state
    $sdk=Join-Path $root 'sdk-bin'; New-Item -ItemType Directory -Path $sdk,$p.build -Force | Out-Null
    foreach ($name in @('ffxiii2.exe') + @($script:X2Modules | ForEach-Object {'ffxiii2_'+$_+'.dll'})) { Set-Content -LiteralPath (Join-Path $p.build $name) -Value 'binary fixture' }
    Set-Content -LiteralPath (Join-Path $sdk 'rexruntime.dll') -Value 'runtime fixture'
    New-Item -ItemType Directory -Path (Join-Path $p.userData '00000000BABEBABE') -Force | Out-Null
    $script:T=@{sdkBin=$sdk}
    Set-Content -LiteralPath (Join-Path $p.install 'ffxiii2.toml') -Value @('log_level = "info"','user_choice = 123')
    Step-Install $state $p
    $launch=Get-Content -LiteralPath (Join-Path $p.install 'ffxiii2_launch.txt') -Raw
    Assert ($launch.Contains('game_data_root = '+$p.gameRun) -and $launch.Contains('user_data_root = '+$p.userData)) 'Direct launch roots missing'
    $config=Get-Content -LiteralPath (Join-Path $p.install 'ffxiii2.toml') -Raw
    Assert ($config.Contains('log_level = "off"') -and $config.Contains('log_file = "NUL"') -and $config.Contains('user_choice = 123')) 'Public configuration incorrect'
    Assert ((Get-FileHash $save).Hash -eq $saveHash) 'Installation changed saves'
    $packageName='58D2695DF8A6B290B161A2CF02C0839B1873EEF353'
    $packages=Join-Path $p.userData '0000000000000000\53510806\00000002'
    $headers=Join-Path $p.userData '0000000000000000\53510806\Headers\00000002'
    $oldBackup=Join-Path $root 'old-backup'
    $original=Join-Path $oldBackup $packageName
    $converted=Join-Path $packages 'XIII2SteamDLC-fixture'
    $movies=Join-Path $p.install 'movies_steam'
    $tracking=Join-Path $p.work 'steamdlc\moved_360_packages.txt'
    New-Item -ItemType Directory -Path $original,$converted,$headers,$movies,(Split-Path $tracking) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $oldBackup ($packageName+'.header')) -Value 'original header'
    Set-Content -LiteralPath $tracking -Value ($packageName+"`t"+$oldBackup)
    Restore-X2OriginalDlc $p
    Assert ((Test-Path -LiteralPath (Join-Path $packages $packageName)) -and (Test-Path -LiteralPath (Join-Path $headers ($packageName+'.header')))) '42-character original package was not restored'
    Assert (-not (Test-Path -LiteralPath $converted) -and -not (Test-Path -LiteralPath $movies) -and -not (Test-Path -LiteralPath $tracking)) 'Converted content remains mounted'
    $retained=@(Get-ChildItem -LiteralPath (Join-Path $p.out 'content_backups') -Directory)
    Assert (Test-Path -LiteralPath (Join-Path $retained[0].FullName 'XIII2SteamDLC-fixture')) 'Converted files were not retained'
    New-Item -ItemType Directory -Path $converted -Force | Out-Null
    Set-Content -LiteralPath $tracking -Value (('A'*42)+"`t"+(Join-Path $root 'missing-backup'))
    $failed=$false
    try { Restore-X2OriginalDlc $p } catch { $failed=$true }
    Assert ($failed -and (Test-Path -LiteralPath $converted)) 'Missing backup preflight modified content'
    Assert ((Get-FileHash $save).Hash -eq $saveHash) 'DLC restoration changed saves'
    Write-Host 'PASS: path protection, content separation, PS3 folder reads, interrupted resume, state persistence, unchanged build, selective code/input updates, save preservation (external build/game mocked).'
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
