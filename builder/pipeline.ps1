# Windows builder. Shared renderer source is pinned to the LR builder specification.
. (Join-Path $PSScriptRoot 'tools.ps1')
. (Join-Path $PSScriptRoot 'patches.ps1')
. (Join-Path $PSScriptRoot 'x2_modules.ps1')
. (Join-Path $PSScriptRoot 'x2_compile.ps1')
. (Join-Path $PSScriptRoot 'x2_ps3.ps1')
. (Join-Path $PSScriptRoot 'x2_ui.ps1')
. (Join-Path $PSScriptRoot 'x2_jpvoice.ps1')
. (Join-Path $PSScriptRoot 'x2_steamdlc.ps1')
$script:Repo = Split-Path $PSScriptRoot
$script:Downloads['shared'] = @{
    name = 'Shared renderer source'; size = 0; filename = 'lr-shared-556d662.zip'
    url = 'https://codeload.github.com/leftisthominid/lr-ffxiii-port/zip/556d662051d3d442975b8715d81167e3ba96d6d8'
    sha256 = '0337521837AA58D64BB4FA0E8AF669A42893AB0009DD97DD2B818BCB39BAF6DB'
}

function Get-Paths($state) {
    $o = $state.output
    $game = Join-Path $o 'sources\360\game'
    if ($state.inputs.game360.kind -eq 'folder') { $game = $state.inputs.game360.path }
    return @{
        out=$o; tools=(Join-Path $o 'tools'); logs=(Join-Path $o 'work\build-output')
        work=(Join-Path $o 'work'); game=$game; gameRun=(Join-Path $o 'run\game_tu')
        tu=(Join-Path $o 'run\update_tu'); project=(Join-Path $o 'project')
        build=(Join-Path $o 'build'); install=(Join-Path $o 'run')
        userData=(Join-Path $o 'userdata'); textures=(Join-Path $o 'run\textures')
        shared=(Join-Path $o 'tools\shared-renderer'); pluginBuild=(Join-Path $o 'build-renderer')
        icon=(Join-Path $o 'work\icon')
    }
}

function Build-Converter($t, [string]$source, [string]$exe, [string[]]$extra=@()) {
    $hash = (Get-FileHash -LiteralPath $source).Hash + ($extra -join '|')
    if ((Test-Path -LiteralPath $exe) -and (Test-Path -LiteralPath "$exe.source") -and (Get-Content -LiteralPath "$exe.source" -Raw).Trim() -eq $hash) { return }
    $rc = Invoke-Logged $t.clangxx (@('-O2','-std=c++17') + $extra + @($source,'-o',$exe)) "$exe.build.txt"
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath $exe)) { throw "Could not build $source" }
    Set-Content -LiteralPath "$exe.source" -Value $hash
}

function Step-Tools($state,$p) { $script:T = Get-WindowsTools $p.tools }

function Step-Game($state,$p) {
    if ($state.inputs.game360.kind -ne 'folder') {
        $extract = Join-Path $p.work 'x2_extract360.exe'
        Build-Converter $script:T (Join-Path $script:Repo 'converters\setup\x2_extract360.cpp') $extract
        $tmp = $p.game + '.new'
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
        $rc = Invoke-Logged $extract @($state.inputs.game360.path,$tmp) (Join-Path $p.logs 'extract360.txt')
        if ($rc -ne 0) { throw 'Could not extract the Xbox 360 copy.' }
        Assert-X2Game $tmp
        if (Test-Path -LiteralPath $p.game) { Remove-Item -LiteralPath $p.game -Recurse -Force }
        New-Item -ItemType Directory -Path (Split-Path $p.game) -Force | Out-Null
        Move-Item -LiteralPath $tmp -Destination $p.game
    }
    Assert-X2Game $p.game
}

function Import-X2Content($p,[string]$source) {
    $rc = Invoke-Logged (Get-X2PowerShell) @('-NoProfile','-ExecutionPolicy','Bypass','-File',
        (Join-Path $script:Repo 'scripts\X2_Import_360_Content.ps1'),'-Source',$source,
        '-UserData',$p.userData,'-Only','dlc','-Skip','-TuOut',$p.tu,
        '-ListOut',(Join-Path $p.logs 'content.txt')) (Join-Path $p.logs 'import.txt')
    if ($rc -ne 0) { throw 'Xbox content import failed.' }
}

function Step-Update($state,$p) {
    Import-X2Content $p $state.inputs.update.path
    foreach ($name in $script:XexpSha256.Keys) {
        $patch = Join-Path $p.tu ($name + '.xexp')
        if (-not (Test-Path -LiteralPath $patch) -or (Get-FileHash -LiteralPath $patch).Hash -ne $script:XexpSha256[$name]) {
            throw "Unsupported or incomplete title update: $name.xexp. Supply update 0.0.1.13."
        }
    }
    New-Item -ItemType Directory -Path $p.gameRun -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $p.game -Force) {
        if (-not $item.PSIsContainer -and $item.Extension -eq '.xexp') { continue }
        $dest = Join-Path $p.gameRun $item.Name
        # Refresh links if the selected source was moved; never write through them.
        if (Test-Path -LiteralPath $dest) {
            $old = Get-Item -LiteralPath $dest -Force
            if ($old.Attributes -band [IO.FileAttributes]::ReparsePoint) { [IO.Directory]::Delete($dest) }
            elseif ($old.PSIsContainer) { throw "Unexpected normal folder in game link staging: $dest" }
            else { Remove-Item -LiteralPath $dest -Force }
        }
        if ($item.PSIsContainer) { New-Item -ItemType Junction -Path $dest -Target $item.FullName | Out-Null }
        else {
            try { New-Item -ItemType HardLink -Path $dest -Target $item.FullName -ErrorAction Stop | Out-Null }
            catch { Copy-Item -LiteralPath $item.FullName -Destination $dest }
        }
    }
    foreach ($name in $script:XexpSha256.Keys) {
        $dest=Join-Path $p.gameRun ($name+'.xexp')
        # Unlink first: overwriting a hard link would modify the original input.
        if (Test-Path -LiteralPath $dest) {Remove-Item -LiteralPath $dest -Force}
        Copy-Item -LiteralPath (Join-Path $p.tu ($name+'.xexp')) -Destination $dest
    }
}

function Step-Icon($state,$p) {
    $tool = Join-Path $p.work 'x2_icon.exe'
    Build-Converter $script:T (Join-Path $script:Repo 'converters\icon\x2_icon.cpp') $tool
    New-Item -ItemType Directory -Path $p.icon -Force | Out-Null
    $tile = Join-Path $p.icon 'tile.png'
    $rc = Invoke-Logged $tool @('extract',(Join-Path $p.game 'default.xex'),$tile) (Join-Path $p.logs 'icon.txt')
    if ($rc -ne 0) { throw 'Could not extract the application icon from the owned Xbox executable.' }
    $big = Join-Path $p.icon 'tile-x4.png'
    try {
        Expand-Pinned 'esrgan' (Join-Path $p.tools 'downloads') (Join-Path $p.tools 'realesrgan') 'realesrgan-ncnn-vulkan.exe'
        $rc = Invoke-Logged (Join-Path $p.tools 'realesrgan\realesrgan-ncnn-vulkan.exe') @('-i',$tile,'-o',$big,'-n','realesrgan-x4plus','-m',(Join-Path $p.tools 'realesrgan\models')) (Join-Path $p.logs 'icon-upscale.txt')
        if ($rc -ne 0) { throw 'Icon upscaler unavailable.' }
    } catch { $big=$tile }
    $rc = Invoke-Logged $tool @('compose',$big,$p.icon) (Join-Path $p.logs 'icon-compose.txt')
    if ($rc -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $p.icon 'ffxiii2.ico'))) { throw 'Application icon generation failed.' }
}

function Step-Compile($state,$p) {
    Build-X2Project $p $script:T

}

function Step-Renderer($state,$p) {
    Expand-Pinned 'shared' (Join-Path $p.tools 'downloads') $p.shared 'plugin\CMakeLists.txt'
    $cfg = @('-S',(Join-Path $p.shared 'plugin'),'-B',$p.pluginBuild) + (Get-CMakeToolArgs $script:T)
    $rc = Invoke-Logged $script:T.cmake $cfg (Join-Path $p.logs 'renderer-configure.txt')
    if ($rc -ne 0) { throw 'Could not configure the renderer.' }
    $rc = Invoke-Logged $script:T.cmake @('--build',$p.pluginBuild,'--parallel',"$([Environment]::ProcessorCount)") (Join-Path $p.logs 'renderer-build.txt')
    if ($rc -ne 0) { throw 'Could not build the renderer.' }
    $dll = Get-ChildItem -LiteralPath $p.pluginBuild -Recurse -File -Filter 'rexgpu-xenos.dll' | Select-Object -First 1
    if (-not $dll) { throw 'Renderer DLL missing after build.' }
    Copy-Item -LiteralPath $dll.FullName -Destination $p.install -Force
}

function Step-Install($state,$p) {
    foreach ($file in @('ffxiii2.exe') + @($script:X2Modules | ForEach-Object { 'ffxiii2_'+$_+'.dll' })) {
        $source = Get-ChildItem -LiteralPath $p.build -Recurse -File -Filter $file | Select-Object -First 1
        if (-not $source) { throw "Build output missing: $file" }
        Copy-Item -LiteralPath $source.FullName -Destination $p.install -Force
    }
    Copy-Item -LiteralPath (Join-Path $script:T.sdkBin 'rexruntime.dll') -Destination $p.install -Force
    # Direct EXE launch: all paths are owned output or read-only game staging.
    $launch = Join-Path $p.install 'ffxiii2_launch.txt'
    $keep = @(Get-Content -LiteralPath $launch -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*LR_' })
    [IO.File]::WriteAllLines($launch,[string[]](@("game_data_root = $($p.gameRun)","user_data_root = $($p.userData)")+$keep))
    $toml = Join-Path $p.install 'ffxiii2.toml'
    $keep = @(Get-Content -LiteralPath $toml -ErrorAction SilentlyContinue | Where-Object { $_ -notmatch '^\s*(gpu_plugin|graphics_backend|log_level|log_file|anisotropic_override|texture_cache_memory_limit_soft|texture_cache_memory_limit_hard|clear_memory_page_state|occlusion_query_enable|readback_memexport)\s*=' })
    # Windows NUL prevents the SDK's automatic sequential log-file creation.
    $defaults = @('gpu_plugin = "xenos"','graphics_backend = "d3d12"','log_level = "off"','log_file = "NUL"',
        'anisotropic_override = 5','texture_cache_memory_limit_soft = 2048','texture_cache_memory_limit_hard = 4096',
        'clear_memory_page_state = false','occlusion_query_enable = false','readback_memexport = true')
    [IO.File]::WriteAllLines($toml,[string[]]($defaults+$keep))
    $real=Join-Path $p.userData 'B13EBABEBABEBABE'; $alias=Join-Path $p.userData '00000000BABEBABE'
    New-Item -ItemType Directory -Path $real -Force | Out-Null
    if (-not (Test-Path -LiteralPath $alias)) { New-Item -ItemType Junction -Path $alias -Target $real | Out-Null }
    if ($state.shortcut) {
        $shell=New-Object -ComObject WScript.Shell
        $shortcut=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Final Fantasy XIII-2.lnk'))
        $shortcut.TargetPath=Join-Path $p.install 'ffxiii2.exe'; $shortcut.WorkingDirectory=$p.install
        $shortcut.IconLocation=$shortcut.TargetPath+',0'; $shortcut.Save()
    }
    Copy-Item -LiteralPath (Join-Path $script:Repo 'LICENSE') -Destination $p.install -Force
    Copy-Item -LiteralPath (Join-Path $script:Repo 'LICENSES') -Destination $p.install -Recurse -Force
}

function Step-Fonts($state,$p) {
    $tool=Join-Path $p.work 'lr_uiconv.exe'
    $xx=Get-ChildItem -LiteralPath (Join-Path $script:T.sdk 'include') -Recurse -Filter 'xxhash.h' | Select-Object -First 1
    if (-not $xx) { throw 'xxhash.h missing from SDK.' }
    Build-Converter $script:T (Join-Path $script:Repo 'converters\lr_uiconv.cpp') $tool @("-I$($xx.DirectoryName)")
    $fonts=Join-Path $p.textures 'replace\fonts'; $tmp=$fonts+'.new'
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path $tmp) -Force | Out-Null
    $rc=Invoke-Logged $tool @('--fonts',(Join-Path $script:Repo 'converters\x2_ui_sheets.csv'),(Join-Path $p.game 'alba_data\sys'),$tmp,'--scale','4') (Join-Path $p.logs 'fonts.txt')
    if ($rc -ne 0 -or -not @(Get-ChildItem -LiteralPath $tmp -Filter '*.dds' -ErrorAction SilentlyContinue).Count) { throw 'Font conversion did not produce textures.' }
    if (Test-Path -LiteralPath $fonts) { Remove-Item -LiteralPath $fonts -Recurse -Force }
    Move-Item -LiteralPath $tmp -Destination $fonts
    Convert-X2TitleMenu $script:T $p.game $p.textures $p.work $p.logs
}

function Step-Ps3($state,$p) {
    $ps3=$state.inputs.ps3
    $ffmpeg=Get-Ffmpeg $p.tools
    Convert-X2Ps3Movies $ps3.path $ffmpeg (Join-Path $p.install 'movies_ps3') $p.work $p.logs
    Copy-Item -LiteralPath $ffmpeg -Destination $p.install -Force
    Convert-X2Ps3Ui $script:T $ps3.path $p.game $p.textures $p.work $p.logs
}

function Restore-X2OriginalDlc($p) {
    $packages=Join-Path $p.userData '0000000000000000\53510806\00000002'
    $headers=Join-Path $p.userData '0000000000000000\53510806\Headers\00000002'
    $tracking=Join-Path $p.work 'steamdlc\moved_360_packages.txt'
    $records=@()
    foreach ($line in @(Get-Content -LiteralPath $tracking -ErrorAction SilentlyContinue)) {
        if (-not $line.Trim()) {continue}
        $fields=$line -split "`t",2
        if ($fields.Count -ne 2 -or $fields[0] -notmatch '^[0-9A-Fa-f]{40}(?:[0-9A-Fa-f]{2})?$') {throw 'Invalid original DLC backup record; no content moved.'}
        $name=$fields[0]; $source=Join-Path $fields[1] $name; $dest=Join-Path $packages $name
        if (-not (Test-Path -LiteralPath $source) -and -not (Test-Path -LiteralPath $dest)) {throw "Original DLC backup missing: $name. No content moved."}
        $records+=@{name=$name;source=$source;dest=$dest;backup=$fields[1]}
    }
    $converted=@(Get-ChildItem -LiteralPath $packages -Directory -Filter 'XIII2SteamDLC*' -ErrorAction SilentlyContinue)
    $movies=Join-Path $p.install 'movies_steam'
    if (-not $converted.Count -and -not $records.Count -and -not (Test-Path -LiteralPath $movies)) {return}
    $backup=Join-Path $p.out ('content_backups\steam-dlc-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backup,$headers -Force | Out-Null
    if (Test-Path -LiteralPath $tracking) {Copy-Item -LiteralPath $tracking -Destination $backup}
    foreach ($package in $converted) {
        Move-Item -LiteralPath $package.FullName -Destination $backup
        $header=Join-Path $headers ($package.Name+'.header')
        if (Test-Path -LiteralPath $header) {Move-Item -LiteralPath $header -Destination $backup}
    }
    if (Test-Path -LiteralPath $movies) {Move-Item -LiteralPath $movies -Destination $backup}
    foreach ($record in $records) {
        if (-not (Test-Path -LiteralPath $record.dest)) {Move-Item -LiteralPath $record.source -Destination $record.dest}
        $sourceHeader=Join-Path $record.backup ($record.name+'.header')
        $destHeader=Join-Path $headers ($record.name+'.header')
        if ((Test-Path -LiteralPath $sourceHeader) -and -not (Test-Path -LiteralPath $destHeader)) {Move-Item -LiteralPath $sourceHeader -Destination $destHeader}
    }
    if (Test-Path -LiteralPath $tracking) {Remove-Item -LiteralPath $tracking}
    Say "Converted content retained in $backup. Original Xbox DLC will be used." 'Green'
}
function Step-Dlc($state,$p) {
    Restore-X2OriginalDlc $p
    Import-X2Content $p $state.inputs.dlc360.path
}
function Step-SteamDlc($state,$p) {
    Invoke-X2SteamDlc 'install' $p.game $p.tu $state.inputs.steam.path $p.tools $p.out $p.logs -NoReport
}
function Step-Voices($state,$p) {
    $ffmpeg=Get-Ffmpeg $p.tools
    # Invalidate only this conversion's cache marker when its source digest changed.
    Remove-Item -LiteralPath (Join-Path $p.install 'jpvoice\version.txt') -Force -ErrorAction SilentlyContinue
    Convert-X2JpVoice $script:T $p.game $state.inputs.steam.path $ffmpeg (Join-Path $p.install 'jpvoice') $p.work $p.logs -LipSync
    Convert-X2JpMovieAudio $script:T $p.game $state.inputs.steam.path $ffmpeg (Join-Path $p.install 'movies_jp') $p.work $p.logs
    Copy-Item -LiteralPath $ffmpeg -Destination $p.install -Force
    $tool=Join-Path $p.work 'x2_jpvoice.exe'
    $rc=Invoke-Logged $tool @('--merge-update',$p.tu,(Join-Path $p.install 'jpvoice'),(Join-Path $p.install 'update_merged')) (Join-Path $p.logs 'merge.txt')
    if ($rc -ne 0) { throw 'Japanese voice/title-update merge failed.' }
    $preference=Join-Path $p.userData 'voice.txt'
    if (-not (Test-Path -LiteralPath $preference)) { [IO.File]::WriteAllText($preference,'jp',[Text.Encoding]::ASCII) }
}
function Step-DlcVoices($state,$p) {
    Convert-X2JpDlcVoices $script:T $state.inputs.steam.path (Get-Ffmpeg $p.tools) (Join-Path $p.userData '0000000000000000\53510806\00000002') (Join-Path $p.install 'jpvoice_dlc') $p.work $p.logs
}

# Dependencies propagate source/input changes without rebuilding unrelated content.
function Get-Steps($state) {
    $s=[ordered]@{
        tools=@{title='Build tools';run=${function:Step-Tools};always=$true;files=@('builder/tools.ps1')}
        game=@{title='Xbox 360 game files';run=${function:Step-Game};input='game360';files=@('converters/setup','builder/inputs.ps1');deps=@('tools')}
        update=@{title='Xbox 360 title update';run=${function:Step-Update};input='update';deps=@('game');files=@('scripts/X2_Import_360_Content.ps1','builder/inputs.ps1')}
        icon=@{title='Game icon';run=${function:Step-Icon};deps=@('game');files=@('converters/icon')}
        compile=@{title='Recompiling and building the game';run=${function:Step-Compile};deps=@('update','icon');files=@('project','builder/x2_compile.ps1','builder/patches.ps1','builder/x2_modules.ps1')}
        renderer=@{title='Building the renderer';run=${function:Step-Renderer};deps=@();files=@('builder/tools.ps1')}
        install=@{title='Game folder and settings';run=${function:Step-Install};deps=@('compile','renderer');files=@()}
        fonts=@{title='Sharp fonts and title menu';run=${function:Step-Fonts};deps=@('game');files=@('builder/x2_ui.ps1','converters/lr_uiconv.cpp','converters/x2_ui_sheets.csv')}
    }
    if ($state.inputs.ps3) { $s['ps3']=@{title='PS3 cinematics and buttons';run=${function:Step-Ps3};input='ps3';deps=@('fonts');files=@('builder/x2_ps3.ps1','builder/x2_ui.ps1','builder/iso.ps1')} }
    if ($state.inputs.dlc360) { $s['dlc']=@{title='Original Xbox 360 DLC';run=${function:Step-Dlc};input='dlc360';deps=@('update');files=@('scripts/X2_Import_360_Content.ps1')} }
    if ($state.inputs.steamdlc) { $s['steamdlc']=@{title='Experimental Steam DLC conversion';run=${function:Step-SteamDlc};input='steam';deps=@('update');files=@('builder/x2_steamdlc.ps1','converters/x2dlc')} }
    if ($state.inputs.steam) {
        $s['voices']=@{title='Japanese voices and lip-sync';run=${function:Step-Voices};input='steam';deps=@('update');files=@('builder/x2_jpvoice.ps1','converters/x2_jpvoice.cpp','project/src/x2_jp_dlc_provenance.h')}
        $deps=@('voices'); if ($s.Contains('dlc')) {$deps+='dlc'}; if ($s.Contains('steamdlc')) {$deps+='steamdlc'}
        $s['dlcvoices']=@{title='Japanese DLC voices';run=${function:Step-DlcVoices};input='steam';deps=$deps;files=@('builder/x2_jpvoice.ps1','converters/x2_jpvoice.cpp')}
    }
    return $s
}

function Get-StepDigest($state,$step,$hashes) {
    $parts=New-Object 'System.Collections.Generic.List[string]'
    $parts.Add($step.run.ToString())
    $parts.Add((Get-X2PowerShell))
    if ($step.input) {
        $value=$state.inputs[$step.input]
        $parts.Add(($value | ConvertTo-Json -Depth 5 -Compress))
        if ($value.packages) { $paths=@($value.packages) } else { $paths=@($value.path) }
        foreach ($path in $paths) {
            $item=Get-Item -LiteralPath $path -ErrorAction Stop
            $parts.Add("$($item.FullName)|$($item.Length)|$($item.LastWriteTimeUtc.Ticks)")
            if ($item.PSIsContainer) {
                foreach ($file in Get-ChildItem -LiteralPath $path -Recurse -File | Sort-Object FullName) {
                    $parts.Add("$($file.FullName)|$($file.Length)|$($file.LastWriteTimeUtc.Ticks)")
                }
            }
        }
    }
    foreach ($dep in $step.deps) { $parts.Add("$dep=$($hashes[$dep])") }
    foreach ($rel in $step.files) {
        foreach ($file in Get-ChildItem -LiteralPath (Join-Path $script:Repo $rel) -Recurse -File | Where-Object { $_.Extension -notin '.pyc' -and $_.FullName -notmatch '__pycache__' } | Sort-Object FullName) {
            $parts.Add($file.FullName.Substring($script:Repo.Length)+'='+(Get-FileHash -LiteralPath $file.FullName).Hash)
        }
    }
    $parts.Add('pipeline-schema-1')
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($parts -join "`n")))) } finally { $sha.Dispose() }
}

function Invoke-Pipeline($state,[string[]]$Only=@()) {
    if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) { throw 'Close Final Fantasy XIII-2 before updating the build.' }
    $p=Get-Paths $state
    New-Item -ItemType Directory -Path $p.work,$p.logs,$p.install,$p.userData -Force | Out-Null
    if (-not $state.PSObject.Properties['hashes']) { $state | Add-Member -NotePropertyName hashes -NotePropertyValue @{} }
    $steps=Get-Steps $state; $hashes=@{}
    $state.finished=$false; Save-BuildState $state
    foreach ($key in $steps.Keys) {
        $step=$steps[$key]
        $hashes[$key]=Get-StepDigest $state $step $hashes
        if (-not $step.always -and $state.done -contains $key -and $state.hashes[$key] -eq $hashes[$key] -and $Only -notcontains $key -and (Test-StepReady $key $p)) { continue }
        # A failed forced rebuild must not remain marked complete on the next run.
        $state.done=@($state.done | Where-Object {$_ -ne $key})
        Save-BuildState $state
        Say "Preparing: $($step.title)" 'Cyan'
        try { & $step.run $state $p } catch { Save-BuildState $state; throw "Stopped at $($step.title): $($_.Exception.Message). Run the builder again to resume." }
        if (-not $step.always) {
            $state.done=@($state.done | Where-Object {$_ -ne $key}) + $key
            $state.hashes[$key]=$hashes[$key]
            Save-BuildState $state
        }
    }
    $state.finished=$true; Save-BuildState $state
    Say "Builder completed. Play: $($p.install)\ffxiii2.exe" 'Green'
    Start-Process explorer.exe -ArgumentList ('"'+$p.install+'"')
}

function Test-StepReady([string]$key,$p) {
    $required=switch ($key) {
        game { @((Join-Path $p.game 'default.xex')) }
        update { @((Join-Path $p.tu 'x2_title_update.txt'),(Join-Path $p.gameRun 'default.xex')) }
        icon { @((Join-Path $p.icon 'ffxiii2.ico')) }
        compile { @((Join-Path $p.build 'ffxiii2.exe')) }
        renderer { @((Join-Path $p.install 'rexgpu-xenos.dll')) }
        install { @((Join-Path $p.install 'ffxiii2.exe'),(Join-Path $p.install 'rexruntime.dll'),(Join-Path $p.install 'ffxiii2_launch.txt'),(Join-Path $p.install 'ffxiii2.toml')) }
        fonts { @((Join-Path $p.textures 'replace\fonts')) }
        ps3 { @((Join-Path $p.install 'movies_ps3'),(Join-Path $p.textures 'ps3ui')) }
        voices { @((Join-Path $p.install 'jpvoice\filelist_patch_u.x360.bin'),(Join-Path $p.install 'update_merged\x2_merged_v2.txt')) }
        default { @() }
    }
    foreach ($path in $required) { if (-not (Test-Path -LiteralPath $path)) { return $false } }
    return $true
}

function Get-X2PowerShell {
    $name=if ($PSVersionTable.PSEdition -eq 'Core') {'pwsh'} else {'powershell.exe'}
    if ($env:OS -eq 'Windows_NT' -and $name -eq 'pwsh') { $name='pwsh.exe' }
    return Join-Path $PSHOME $name
}
