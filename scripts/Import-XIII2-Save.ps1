# Standalone Windows save importer. Original Xbox files are only read.
param([string]$Source='', [string]$Output='')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
try {
    Write-Host 'Final Fantasy XIII-2 - Triumtemporate Edition - save importer' -ForegroundColor Cyan
    if (Get-Process -Name ffxiii2 -ErrorAction SilentlyContinue) {throw 'Close Final Fantasy XIII-2 before importing, kupo!'}
    if (-not $Source) {$Source=(Read-Host 'Where is your Xbox 360 save package, kupo? (ff13-00.dat or another slot)').Trim().Trim('"')}
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {throw 'Save package not found. Supply the original Xbox .dat file, kupo.'}
    # Identify the game from its STFS header, never from the ambiguous filename.
    $stream=[IO.File]::OpenRead($Source)
    try {
        $header=New-Object byte[] 0x364
        if ($stream.Read($header,0,$header.Length) -ne $header.Length) {throw 'This file is too short to be an Xbox save package.'}
    } finally {$stream.Dispose()}
    if ([Text.Encoding]::ASCII.GetString($header,0,4) -notin 'CON ','LIVE','PIRS') {throw 'This is not an Xbox save package. Supply the original .dat package.'}
    $title=([uint32]$header[0x360] -shl 24) -bor ([uint32]$header[0x361] -shl 16) -bor ([uint32]$header[0x362] -shl 8) -bor $header[0x363]
    $type=([uint32]$header[0x344] -shl 24) -bor ([uint32]$header[0x345] -shl 16) -bor ([uint32]$header[0x346] -shl 8) -bor $header[0x347]
    if ($title -eq 0x535107E4) {throw 'This save belongs to Final Fantasy XIII, kupo. It cannot be imported into XIII-2.'}
    if ($title -ne 0x53510806 -or $type -ne 1) {throw 'This is not a Final Fantasy XIII-2 saved-game package.'}
    if (-not $Output) {
        $suggest='C:\Final Fantasy XIII-2'
        $answer=(Read-Host "Where is your built game folder, kupo? (Enter for $suggest)").Trim().Trim('"')
        $Output=if ($answer) {$answer} else {$suggest}
    }
    $Output=[IO.Path]::GetFullPath($Output)
    $userdata=Join-Path $Output 'userdata'
    if (-not (Test-Path -LiteralPath $userdata -PathType Container)) {throw 'No game user-data folder here. Choose the output folder used by the builder, kupo.'}
    $importer=Join-Path $PSScriptRoot 'X2_Import_360_Content.ps1'
    $expected='36f2ebd8cfce77a8749228c6a3e47ea2e3ff1200f20e03ae6e9de494451319c0'
    # ZIPs use CRLF; raw source uses LF. Verify identical source text in either form.
    $canonical=[Text.Encoding]::UTF8.GetBytes([IO.File]::ReadAllText($importer).Replace("`r`n","`n"))
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$actual=([BitConverter]::ToString($sha.ComputeHash($canonical))).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
    if ($actual -ne $expected) {throw 'Importer checksum mismatch. Unpack a fresh copy of the save tool.'}
    $shellName=if ($PSVersionTable.PSEdition -eq 'Core') {if ($env:OS -eq 'Windows_NT') {'pwsh.exe'} else {'pwsh'}} else {'powershell.exe'}
    $shell=Join-Path $PSHOME $shellName
    # Validate all package blocks and APP.DAT before any existing save is changed.
    $validation=@(& $shell -NoProfile -ExecutionPolicy Bypass -File $importer -Source $Source -UserData $userdata -Only saves -WhatIf)
    $validation | ForEach-Object {Write-Host $_}
    if ($LASTEXITCODE -ne 0 -or -not ($validation | Where-Object {$_ -match 'FINAL FANTASY XIII-2 save ->'})) {throw 'Save validation failed; existing saves were not changed.'}
    $profile=Join-Path $userdata 'B13EBABEBABEBABE'
    $titleRoot=Join-Path $profile '53510806'
    $alias=Join-Path $userdata '00000000BABEBABE'
    $aliasItem=Get-Item -LiteralPath $alias -Force -ErrorAction SilentlyContinue
    if ($aliasItem -and $aliasItem.LinkType -ne 'Junction') {throw 'Save-folder setup is incomplete. Keep the existing folders and repair the builder setup before importing.'}
    $backup=Join-Path $Output ('save_backups\save-import-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    if (Test-Path -LiteralPath $titleRoot) {Copy-Item -LiteralPath $titleRoot -Destination $backup -Recurse}
    Write-Host "Existing-save backup: $backup"
    New-Item -ItemType Directory -Path $profile -Force | Out-Null
    if (-not $aliasItem) {New-Item -ItemType Junction -Path $alias -Target $profile | Out-Null}
    & $shell -NoProfile -ExecutionPolicy Bypass -File $importer -Source $Source -UserData $userdata -Only saves
    if ($LASTEXITCODE -ne 0) {throw "Import failed. Your previous saves are retained in $backup"}
    Write-Host 'Import complete, kupo! Start the game normally and select Load Game.' -ForegroundColor Green
    Start-Process explorer.exe -ArgumentList ('"'+(Join-Path $titleRoot '00000001')+'"')
} catch {Write-Host $_.Exception.Message -ForegroundColor Red; exit 1}
