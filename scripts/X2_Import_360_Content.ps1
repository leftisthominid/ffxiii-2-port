<#
  FINAL FANTASY XIII-2 ReXGlue port - import Xbox 360 saves and downloadable content.

  Accepts either form of a 360 save:
    - the package file as it sits on the console drive (ff13-00.dat, a "CON" package;
      Modio's plain copy gives this), or
    - a folder holding the files already taken out of the package (APP.DAT, Q*.DAH, ...;
      Modio's Extract gives this).
  STFS packages are signed, not encrypted, so nothing needs decrypting: the files inside
  are the game's own 360 save files, which the port reads as they are.

  Installs where the port looks (title 53510806 = XIII-2, 535107E4 = FFXIII):
    <UserData>\B13EBABEBABEBABE\<title>\00000001\ff13-NN.dat\<files>
    <UserData>\B13EBABEBABEBABE\<title>\Headers\00000001\ff13-NN.dat.header

  Title update (content type 000B0000, title 53510806), with -TuOut: its files are unpacked into
  that folder (the port mounts it as the update folder; XIII-2's downloadable content needs it).

  DLC (content type 2, LIVE / PIRS packages, title 53510806):
    <UserData>\0000000000000000\53510806\00000002\<package file name>\<files>
    <UserData>\0000000000000000\53510806\Headers\00000002\<package file name>.header
  (the header carries a full license mask, as a purchased package would). Other content types
  are skipped.

  The source is only read. An existing slot or package is backed up first.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\X2_Import_360_Content.ps1 -Source "Z:\FFXIII-2\Content"
    add -WhatIf to only print what would be installed.
#>
param(
  [Parameter(Mandatory = $true)][string]$Source,
  [string]$UserData = 'C:\FFXIII-2 Test\userdata',
  [ValidateSet('all', 'saves', 'dlc')][string]$Only = 'all',   # dlc: leave the saves alone
  [switch]$Skip,       # with -Only dlc: a package already installed is not installed again
  [string]$TuOut = '', # the title update (content type 000B0000) is unpacked into this folder
  [string]$ListOut = '',  # every package found, and the files inside the DLC / title update, are listed here
  [string]$ExcludeList = '',  # DLC packages not to install: the first column of each line (the 360 packages the Steam DLC replaced)
  [switch]$WhatIf
)
$ErrorActionPreference = 'Stop'
$ProfileXuid = 'B13EBABEBABEBABE'
$Titles = @{ 0x53510806 = 'FINAL FANTASY XIII-2'; 0x535107E4 = 'FINAL FANTASY XIII' }
$sha1 = [Security.Cryptography.SHA1]::Create()

function U32BE([byte[]]$b, [int]$o) { ([uint32]$b[$o] -shl 24) -bor ([uint32]$b[$o + 1] -shl 16) -bor ([uint32]$b[$o + 2] -shl 8) -bor [uint32]$b[$o + 3] }
function U24LE([byte[]]$b, [int]$o) { [int]$b[$o] -bor ([int]$b[$o + 1] -shl 8) -bor ([int]$b[$o + 2] -shl 16) }
function U24BE([byte[]]$b, [int]$o) { ([int]$b[$o] -shl 16) -bor ([int]$b[$o + 1] -shl 8) -bor [int]$b[$o + 2] }
function U16LE([byte[]]$b, [int]$o) { [int]$b[$o] -bor ([int]$b[$o + 1] -shl 8) }
function S16BE([byte[]]$b, [int]$o) { $v = ([int]$b[$o] -shl 8) -bor [int]$b[$o + 1]; if ($v -ge 0x8000) { $v - 0x10000 } else { $v } }
function Same-Bytes([byte[]]$a, [byte[]]$b, [int]$o) { for ($i = 0; $i -lt 20; $i++) { if ($a[$i] -ne $b[$o + $i]) { return $false } }; return $true }

# Reads an STFS package: title, content type, display name, and every file (path relative
# to the package root, bytes). Each data block is checked against its SHA-1 in the hash
# table; files are followed block by block through the table, so fragmented files work.
function Read-Package([string]$path) {
  $fs = [IO.File]::OpenRead($path); $h = New-Object byte[] 0x400
  try { $got = $fs.Read($h, 0, 0x400) } finally { $fs.Dispose() }
  if ($got -lt 0x400) { throw 'not an STFS package' }
  if (-not $Titles.ContainsKey([int](U32BE $h 0x360))) { throw ('not an XIII-2 or FFXIII package (title {0:X8})' -f (U32BE $h 0x360)) }
  $d = [IO.File]::ReadAllBytes($path)
  $magic = [Text.Encoding]::ASCII.GetString($d, 0, 4)
  if ($magic -notin 'CON ', 'LIVE', 'PIRS') { throw 'not an STFS package' }
  $headerSize = U32BE $d 0x340
  $contentType = U32BE $d 0x344
  $title = U32BE $d 0x360
  $display = [Text.Encoding]::BigEndianUnicode.GetString($d, 0x411, 0x80).Split([char]0)[0]
  $sex = (-bnot $d[0x37B]) -band 1
  $tables = 1 -shl $sex
  $firstTable = ($headerSize + 0xFFF) -band 0xFFFFF000
  $blockOff = {
    param([int]$n)
    $r = ((([math]::Floor(($n + 0xAA) / 0xAA)) -shl $sex)) + $n
    if ($n -ge 0xAA) {
      $r += (([math]::Floor(($n + 0x70E4) / 0x70E4)) -shl $sex)
      if ($n -ge 0x70E4) { $r += (1 -shl $sex) }
    }
    return $firstTable + $r * 0x1000
  }
  # Offset of the level-0 hash table that covers data block n.
  $tableOff = {
    param([int]$n)
    if ($n -lt 0xAA) { $t = 0 }
    else {
      $t = [math]::Floor($n / 0xAA) * (0xAA + $tables) + ([math]::Floor($n / 0x70E4) + 1) * $tables
      if ($n -ge 0x70E4) { $t += $tables }
    }
    return $firstTable + $t * 0x1000
  }
  # Returns the hash entry offset for block n whose SHA-1 matches the block (either table copy).
  $entryFor = {
    param([int]$n)
    $bo = & $blockOff $n
    if ($bo + 0x1000 -gt $d.Length) { throw "block $n lies past the end of the package" }
    $h = $sha1.ComputeHash($d, $bo, 0x1000)
    $t0 = & $tableOff $n
    for ($c = 0; $c -lt $tables; $c++) {
      $e = $t0 + $c * 0x1000 + ($n % 0xAA) * 0x18
      if (Same-Bytes $h $d $e) { return $e }
    }
    throw "block $n does not match its hash; the package is damaged"
  }
  $ftBlocks = U16LE $d (0x379 + 3)
  $ftBlock = U24LE $d (0x379 + 5)
  $entries = New-Object System.Collections.Generic.List[object]
  $blk = $ftBlock
  for ($k = 0; $k -lt [math]::Max(1, $ftBlocks); $k++) {
    $ftOff = & $blockOff $blk
    for ($i = 0; $i -lt 0x1000; $i += 0x40) {
      $e = $ftOff + $i
      $flags = $d[$e + 0x28]
      $len = $flags -band 0x3F
      if ($len -eq 0) { continue }
      $entries.Add([pscustomobject]@{
        Name = [Text.Encoding]::ASCII.GetString($d, $e, $len); IsDir = [bool]($flags -band 0x80)
        Blocks = U24LE $d ($e + 0x29); Start = U24LE $d ($e + 0x2F)
        Parent = S16BE $d ($e + 0x32); Size = [int](U32BE $d ($e + 0x34)) })
    }
    if ($k + 1 -lt $ftBlocks) { $blk = U24BE $d ((& $entryFor $blk) + 0x15) }
  }
  $files = New-Object System.Collections.Generic.List[object]
  $last = [DateTime]::Now
  foreach ($en in $entries) {
    if ($en.IsDir) { continue }
    $rel = $en.Name; $p = $en.Parent; $guard = 0
    while ($p -ge 0 -and $p -lt $entries.Count -and $guard++ -lt 32) { $rel = $entries[$p].Name + '\' + $rel; $p = $entries[$p].Parent }
    $bytes = New-Object byte[] $en.Size
    $done = 0; $n = $en.Start
    for ($k = 0; $k -lt $en.Blocks -and $done -lt $en.Size; $k++) {
      if (($k -band 0xFFF) -eq 0 -and ([DateTime]::Now - $last).TotalSeconds -ge 10) {
        Write-Host ("     ... {0}: {1:N0} of {2:N0} MB" -f $rel, ($done / 1MB), ($en.Size / 1MB)); $last = [DateTime]::Now
      }
      $e = & $entryFor $n
      $c = [math]::Min(0x1000, $en.Size - $done)
      [Array]::Copy($d, (& $blockOff $n), $bytes, $done, $c)
      $done += $c
      $n = U24BE $d ($e + 0x15)
    }
    if ($done -ne $en.Size) { throw "$rel is shorter in the package than its listed size" }
    $files.Add([pscustomobject]@{ Path = $rel; Bytes = $bytes })
    if (([DateTime]::Now - $last).TotalSeconds -ge 10) { Write-Host "     ... read $($files.Count) file(s)"; $last = [DateTime]::Now }
  }
  [pscustomobject]@{ Title = $title; ContentType = $contentType; Display = $display; Files = $files }
}

# A folder of files already taken out of a package (Modio Extract).
function Read-Folder([string]$path) {
  $title = 0x53510806
  if ($path -match '535107E4') { $title = 0x535107E4 }
  $files = New-Object System.Collections.Generic.List[object]
  $root = (Get-Item -LiteralPath $path).FullName.TrimEnd('\') + '\'
  foreach ($f in Get-ChildItem -LiteralPath $path -File -Recurse) {
    $files.Add([pscustomobject]@{ Path = $f.FullName.Substring($root.Length); Bytes = [IO.File]::ReadAllBytes($f.FullName) })
  }
  [pscustomobject]@{ Title = $title; ContentType = 1; Display = 'Save Data'; Files = $files }
}

function New-Header([string]$fileName, [string]$display, [uint32]$contentType, [uint32]$title, [string]$xuidHex, [bool]$license) {
  $h = New-Object byte[] ($(if ($license) { 0x14C } else { 0x148 }))
  $h[3] = 1                                   # device_id = HDD (big-endian 1)
  $h[7] = [byte]$contentType                  # content_type
  $dn = [Text.Encoding]::BigEndianUnicode.GetBytes($display)
  [Array]::Copy($dn, 0, $h, 8, [math]::Min($dn.Length, 254))
  $fn = [Text.Encoding]::ASCII.GetBytes($fileName)
  [Array]::Copy($fn, 0, $h, 0x108, [math]::Min($fn.Length, 42))
  $x = [BitConverter]::GetBytes([uint64]::Parse($xuidHex, 'HexNumber')); [Array]::Reverse($x)
  [Array]::Copy($x, 0, $h, 0x138, 8)
  $t = [BitConverter]::GetBytes($title); [Array]::Reverse($t)
  [Array]::Copy($t, 0, $h, 0x140, 4)
  if ($license) { for ($i = 0x148; $i -lt 0x14C; $i++) { $h[$i] = 0xFF } }   # license mask: all bits
  return ,$h
}

function Add-List([string]$line) { if ($ListOut) { Add-Content -LiteralPath $ListOut -Value $line } }

# Title, content type, and display name from a package's header, without reading the rest.
function Peek-Package([string]$path) {
  $fs = [IO.File]::OpenRead($path); $h = New-Object byte[] 0x500
  try { $got = $fs.Read($h, 0, 0x500) } finally { $fs.Dispose() }
  if ($got -lt 0x500) { return $null }
  $magic = [Text.Encoding]::ASCII.GetString($h, 0, 4)
  if ($magic -notin 'CON ', 'LIVE', 'PIRS') { return $null }
  [pscustomobject]@{ Title = U32BE $h 0x360; ContentType = U32BE $h 0x344
                     Display = [Text.Encoding]::BigEndianUnicode.GetString($h, 0x411, 0x80).Split([char]0)[0] }
}

if (-not (Test-Path -LiteralPath $Source)) { throw "Source not found: $Source" }
$items = @()
if (Test-Path -LiteralPath (Join-Path $Source 'APP.DAT') -PathType Leaf) { $items = @(Get-Item -LiteralPath $Source) }
elseif (Test-Path -LiteralPath $Source -PathType Container) {
  # Packages first, then extracted folders (APP.DAT inside); files inside those folders are not packages.
  $folders = @(Get-ChildItem -LiteralPath $Source -Directory -Recurse | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'APP.DAT') -PathType Leaf })
  $inFolder = { param($f) foreach ($x in $folders) { if ($f.FullName.StartsWith($x.FullName.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar)) { return $true } }; return $false }
  $items = @(Get-ChildItem -LiteralPath $Source -File -Recurse | Where-Object { $_.Length -ge 0xB000 -and -not (& $inFolder $_) })
  $items += $folders
} else { $items = @(Get-Item -LiteralPath $Source) }
Write-Host "Source:    $Source"
Write-Host "User data: $UserData"
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$installed = 0
$done = @{}
$excluded = @{}
if ($ExcludeList -and (Test-Path -LiteralPath $ExcludeList)) {
  foreach ($line in @(Get-Content -LiteralPath $ExcludeList)) { $n0 = ($line -split "`t")[0]; if ($n0) { $excluded[$n0] = $true } }
}
if ($ListOut) { Set-Content -LiteralPath $ListOut -Value "Packages in $Source" }
foreach ($it in $items) {
  if (-not $it.PSIsContainer) {
    $peek = Peek-Package $it.FullName
    if ($peek) {
      Add-List ("{0}  title {1:X8}  type {2:X8}  {3:N0} bytes  '{4}'" -f $it.FullName, $peek.Title, $peek.ContentType, $it.Length, $peek.Display)
      # A DLC package already installed is not read again (they are hundreds of MB).
      if ($peek.ContentType -eq 2 -and $excluded.ContainsKey($it.Name)) { Write-Host "  DLC $($it.Name) left out (the converted Steam DLC replaces it)"; continue }
      if ($Skip -and $peek.ContentType -eq 2 -and $peek.Title -eq 0x53510806 -and
          (Test-Path -LiteralPath (Join-Path $UserData "0000000000000000\53510806\Headers\00000002\$($it.Name).header"))) {
        Write-Host "  DLC $($it.Name) already installed"; continue
      }
      if ($peek.ContentType -eq 0xB0000 -and $peek.Title -eq 0x53510806) {
        if (-not $TuOut) { Write-Host "  title update $($it.Name) found (not unpacked: no -TuOut)"; continue }
        $stampFile = Join-Path $TuOut 'x2_title_update.txt'
        $id = "$($it.Name) $($it.Length) $($it.LastWriteTimeUtc.Ticks)"
        if ((Test-Path -LiteralPath $stampFile) -and (Get-Content -LiteralPath $stampFile -TotalCount 1) -eq $id) {
          Write-Host "  title update $($it.Name) already unpacked"; continue
        }
      }
    }
  }
  Write-Host "  reading $($it.FullName)"
  try {
    if ($it.PSIsContainer) { $pkg = Read-Folder $it.FullName } else { $pkg = Read-Package $it.FullName }
  } catch { Write-Host "  skip $($it.Name): $($_.Exception.Message)"; continue }
  if (-not $Titles.ContainsKey([int]$pkg.Title)) { Write-Host ('  skip {0}: title {1:X8} is neither XIII-2 nor FFXIII' -f $it.Name, $pkg.Title); continue }
  if ($pkg.Files.Count -eq 0) { Write-Host "  skip $($it.Name): no files found inside"; continue }
  $titleHex = '{0:X8}' -f $pkg.Title
  if ($pkg.ContentType -eq 2 -and $pkg.Title -eq 0x53510806) {
    # Downloadable content: <UserData>\0000000000000000\53510806\00000002\<package file name>
    if ($Only -eq 'saves') { continue }
    $name = $it.Name
    if ($name.Length -gt 42) { Write-Host "  skip $($it.Name): package file name longer than 42 characters"; continue }
    $xuid = '0000000000000000'
    $dir = Join-Path $UserData "$xuid\$titleHex\00000002\$name"
    $headerRoot = Join-Path $UserData "$xuid\$titleHex\Headers\00000002"
    if ($excluded.ContainsKey($name)) { Write-Host "  DLC $name left out (the converted Steam DLC replaces it)"; continue }
    if ($Skip -and (Test-Path -LiteralPath (Join-Path $headerRoot "$name.header"))) { Write-Host "  DLC $name already installed"; continue }
    $total = 0; foreach ($f in $pkg.Files) { $total += $f.Bytes.Length; Add-List ('    {0}  {1:N0} bytes' -f $f.Path, $f.Bytes.Length) }
    Write-Host ("  DLC -> {0}  ('{1}', {2} file(s), {3:N0} bytes)" -f $dir, $pkg.Display, $pkg.Files.Count, $total)
    if ($WhatIf) { continue }
    if (Test-Path -LiteralPath $dir) {
      $bak = Join-Path $UserData "content-backup_$stamp\00000002\$name"
      New-Item -ItemType Directory -Force -Path (Split-Path $bak) | Out-Null
      Copy-Item -LiteralPath $dir -Destination $bak -Recurse
      Remove-Item -LiteralPath $dir -Recurse -Force
      Write-Host "     existing copy backed up to $bak"
    }
    New-Item -ItemType Directory -Force -Path $dir, $headerRoot | Out-Null
    foreach ($file in $pkg.Files) {
      $target = Join-Path $dir $file.Path
      New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
      [IO.File]::WriteAllBytes($target, $file.Bytes)
    }
    [IO.File]::WriteAllBytes((Join-Path $headerRoot "$name.header"), (New-Header $name $pkg.Display 2 $pkg.Title $xuid $true))
    $installed++
    $pkg = $null; [GC]::Collect()
    continue
  }
  if ($pkg.ContentType -eq 0xB0000 -and $pkg.Title -eq 0x53510806) {
    foreach ($f in $pkg.Files) { Add-List ('    {0}  {1:N0} bytes' -f $f.Path, $f.Bytes.Length) }
    Write-Host ("  title update -> {0}  ('{1}', {2} file(s))" -f $TuOut, $pkg.Display, $pkg.Files.Count)
    if ($WhatIf) { continue }
    if (Test-Path -LiteralPath $TuOut) { Remove-Item -LiteralPath $TuOut -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $TuOut | Out-Null
    foreach ($file in $pkg.Files) {
      $target = Join-Path $TuOut $file.Path
      New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
      [IO.File]::WriteAllBytes($target, $file.Bytes)
    }
    Set-Content -LiteralPath (Join-Path $TuOut 'x2_title_update.txt') -Value "$($it.Name) $($it.Length) $($it.LastWriteTimeUtc.Ticks)"
    $installed++
    $pkg = $null; [GC]::Collect()
    continue
  }
  if ($pkg.ContentType -ne 1) { Write-Host ('  skip {0}: content type {1:X8} is neither a save nor DLC' -f $it.Name, $pkg.ContentType); continue }
  if ($Only -eq 'dlc') { continue }
  $app = $pkg.Files | Where-Object { $_.Path -eq 'APP.DAT' } | Select-Object -First 1
  if ($pkg.Title -eq 0x53510806) {
    if (-not $app -or $app.Bytes.Length -lt 0x40 -or $app.Bytes[0] -ne 0x0D -or
        [Text.Encoding]::ASCII.GetString($app.Bytes, 0x10, 19) -ne 'Total hours played:') {
      Write-Host "  skip $($it.Name): APP.DAT does not look like a FINAL FANTASY XIII-2 save"; continue
    }
    Write-Host ('     ' + [Text.Encoding]::ASCII.GetString($app.Bytes, 0x10, 29))
  }
  if ($it.Name -match '^ff13(_2)?-(\d\d)\.dat$') { $slot = [int]$Matches[2] }
  elseif ($pkg.Display -match '(\d+)\s*$') { $slot = [int]$Matches[1] - 1 }
  else { $slot = 0 }
  $name = 'ff13-{0:D2}.dat' -f $slot
  if ($done.ContainsKey("$titleHex\$name")) { Write-Host "  skip $($it.Name): $titleHex $name already installed from $($done["$titleHex\$name"])"; continue }
  $done["$titleHex\$name"] = $it.Name
  $dir = Join-Path $UserData "$ProfileXuid\$titleHex\00000001\$name"
  $headerRoot = Join-Path $UserData "$ProfileXuid\$titleHex\Headers\00000001"
  $total = 0; foreach ($f in $pkg.Files) { $total += $f.Bytes.Length }
  Write-Host ("  {0} save -> {1}  ('{2}', {3} file(s), {4:N0} bytes)" -f $Titles[[int]$pkg.Title], $dir, $pkg.Display, $pkg.Files.Count, $total)
  if ($WhatIf) { continue }
  if (Test-Path -LiteralPath $dir) {
    $bak = Join-Path $UserData "save-backup_$stamp\$titleHex\$name"
    New-Item -ItemType Directory -Force -Path (Split-Path $bak) | Out-Null
    Copy-Item -LiteralPath $dir -Destination $bak -Recurse
    Remove-Item -LiteralPath $dir -Recurse -Force
    Write-Host "     existing slot backed up to $bak"
  }
  New-Item -ItemType Directory -Force -Path $dir, $headerRoot | Out-Null
  foreach ($file in $pkg.Files) {
    $target = Join-Path $dir $file.Path
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    [IO.File]::WriteAllBytes($target, $file.Bytes)
  }
  [IO.File]::WriteAllBytes((Join-Path $headerRoot "$name.header"), (New-Header $name $pkg.Display 1 $pkg.Title $ProfileXuid $false))
  $installed++
}
if ($WhatIf) { Write-Host '(-WhatIf: nothing was written)' } else { Write-Host "Done: $installed save(s) / DLC package(s) / title update installed." }
