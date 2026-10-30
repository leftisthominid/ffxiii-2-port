param(
    [string]$Out = 'C:\FFXIII-2 Test',
    [string]$Game = 'C:\FFXIII-2\360\game',
    [string]$Steam = 'C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XIII-2',
    [string]$ReuseSession = ''
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) { throw 'Close FFXIII-2 first.' }
$run = Join-Path $Out 'run'
$converter = Join-Path $Out 'work\x2_jpvoice.exe'
$ffmpeg = Join-Path $run 'ffmpeg.exe'
$exe = Join-Path $run 'ffxiii2.exe'
$tu = Join-Path $run 'update_tu'
$gameRoot = Join-Path $run 'game_tu'
$voice = Join-Path $Out 'userdata\voice.txt'
foreach ($file in @($converter, $ffmpeg, $exe, (Join-Path $gameRoot 'default.xex'), (Join-Path $tu 'x2_title_update.txt'), (Join-Path $Game 'default.xex'), (Join-Path $Steam 'alba_data\sys\filelistc.win32.bin'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required input missing: $file" }
}
foreach ($folder in @('jpvoice', 'update_merged')) {
    if (-not (Test-Path -LiteralPath (Join-Path $run $folder) -PathType Container)) { throw "Baseline missing: $folder" }
}
$session = Join-Path $Out ('work\base-lipsync-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $session | Out-Null
Write-Host "Test and recovery folder: $session"
$staged = Join-Path $session 'jpvoice'
$merged = Join-Path $session 'update_merged'
# Build separate archives before changing any mounted content. DLC is never modified.
if ($ReuseSession) {
    $previous = Join-Path $ReuseSession 'jpvoice-tested'
    foreach ($name in @('jp_lipsync.txt', 'filelist_patch_u.x360.bin', 'white_patch_u.x360.bin', 'filelist_patch_scr_u.x360.bin', 'white_patch_scr_u.x360.bin')) {
        if (-not (Test-Path -LiteralPath (Join-Path $previous $name) -PathType Leaf)) { throw "Incomplete previous conversion: $previous" }
    }
    Copy-Item -LiteralPath $previous -Destination $staged -Recurse
} else {
    & $converter --lipsync --ffmpeg $ffmpeg $Game $Steam $staged *> (Join-Path $session 'conversion.txt')
    if ($LASTEXITCODE -ne 0) { throw "Conversion failed ($LASTEXITCODE); baseline unchanged. See $session" }
}
$declarations = Join-Path $staged 'jp_lipsync.txt'
if (-not (Test-Path -LiteralPath $declarations) -or -not (Get-Item -LiteralPath $declarations).Length) { throw 'No lip-sync replacements produced; baseline unchanged.' }
& $converter --merge-update $tu $staged $merged *> (Join-Path $session 'merge.txt')
if ($LASTEXITCODE -ne 0) { throw "Merge failed; baseline unchanged. See $session" }
$hadVoice = Test-Path -LiteralPath $voice
if ($hadVoice) { Copy-Item -LiteralPath $voice -Destination (Join-Path $session 'voice.txt') }
$swapped = @()
try {
    foreach ($folder in @('jpvoice', 'update_merged')) {
        $active = Join-Path $run $folder
        $backup = Join-Path $session ($folder + '-baseline')
        Move-Item -LiteralPath $active -Destination $backup
        $swapped += $folder
        Move-Item -LiteralPath (Join-Path $session $folder) -Destination $active
    }
    [IO.File]::WriteAllText($voice, 'jp', [Text.Encoding]::ASCII)
    Write-Host 'Japanese lip-sync test: load the same base-game save, check Serah appears and moves, then check a voiced scene. Do not save. Close the game to restore the baseline.'
    # Match the working builder's roots and graphics configuration. Quote complete
    # arguments because Start-Process joins ArgumentList with spaces on Windows.
    $gameArgs = '"--game_data_root={0}" "--user_data_root={1}" --gpu_plugin=xenos --graphics_backend=d3d12' -f $gameRoot, (Join-Path $Out 'userdata')
    Start-Process -FilePath $exe -ArgumentList $gameArgs -WorkingDirectory $run -Wait
} finally {
    if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) {
        throw "Game is still running; close it before restoring the *-baseline folders from $session."
    }
    foreach ($folder in $swapped) {
        $active = Join-Path $run $folder
        if (Test-Path -LiteralPath $active) { Move-Item -LiteralPath $active -Destination (Join-Path $session ($folder + '-tested')) }
        Move-Item -LiteralPath (Join-Path $session ($folder + '-baseline')) -Destination $active
    }
    if ($hadVoice) { Copy-Item -LiteralPath (Join-Path $session 'voice.txt') -Destination $voice -Force }
    elseif (Test-Path -LiteralPath $voice) { Remove-Item -LiteralPath $voice }
    Write-Host "Baseline restored. Test files retained: $session"
    Start-Process explorer.exe -ArgumentList ('"' + $session + '"')
}
