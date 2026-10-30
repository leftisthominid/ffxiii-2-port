# Final Fantasy XIII-2 builder - finding and checking the game files the person owns. Every check
# only reads. Dot-sourced by builder.ps1 (uses its Say / Ask-YesNo).

$script:TitleId360 = 0x53510806

function Read-Bytes([string]$path, [long]$offset, [int]$count) {
  $fs = [IO.File]::OpenRead($path)
  try {
    if ($offset + $count -gt $fs.Length) { return $null }
    $null = $fs.Seek($offset, 'Begin')
    $b = New-Object byte[] $count
    $read = 0
    while ($read -lt $count) {
      $n = $fs.Read($b, $read, $count - $read)
      if ($n -le 0) { return $null }
      $read += $n
    }
    return ,$b
  } finally { $fs.Dispose() }
}
function BE32([byte[]]$b, [int]$o) { return [uint32](([uint32]$b[$o] -shl 24) -bor ([uint32]$b[$o + 1] -shl 16) -bor ([uint32]$b[$o + 2] -shl 8) -bor $b[$o + 3]) }

# The title ID in a default.xex (the execution info header), or 0.
function Get-XexTitleId([string]$xex) {
  $h = Read-Bytes $xex 0 0x1000
  if (-not $h -or [Text.Encoding]::ASCII.GetString($h, 0, 4) -ne 'XEX2') { return 0 }
  $count = BE32 $h 0x14
  for ($i = 0; $i -lt $count -and 0x18 + $i * 8 + 8 -le $h.Length; ++$i) {
    if ((BE32 $h (0x18 + $i * 8)) -eq 0x00040006) {
      $at = BE32 $h (0x18 + $i * 8 + 4)
      $info = Read-Bytes $xex $at 0x18
      if ($info) { return BE32 $info 0x0C }
    }
  }
  return 0
}

# What a path holds, as far as the 360 game goes: @{ kind; path; why }.
#   kind 'folder' (extracted game: default.xex + alba_data), 'iso' (disc image), 'god' (the
#   folder a 360 writes a Games on Demand / installed copy to), or '' with why.
function Test-Game360([string]$path) {
  $path = $path.Trim('"', ' ')
  if (-not (Test-Path -LiteralPath $path)) { return @{ kind = ''; why = "There is nothing at $path." } }
  $item = Get-Item -LiteralPath $path
  if ($item -is [IO.FileInfo]) {
    $header=Read-Bytes $path 0 0x364
    if ($header -and [Text.Encoding]::ASCII.GetString($header,0,4) -in 'LIVE','PIRS','CON ' -and (BE32 $header 0x360) -eq $script:TitleId360 -and (BE32 $header 0x344) -eq 0x7000 -and (Test-Path -LiteralPath ($path+'.data') -PathType Container)) {
      return @{kind='god';path=$item.FullName}
    }
    if ($item.Extension -ne '.iso') { return @{ kind = ''; why = 'That is a file, but not a disc image (.iso).' } }
    # XDVDFS ("MICROSOFT*XBOX*MEDIA") at sector 32 of the game partition: plain, XGD2, or XGD3.
    foreach ($base in 0, 0xFB20, 0x20600, 0xFD90000, 0x2080000) {
      $m = Read-Bytes $path ($base + 0x10000) 20
      if ($m -and [Text.Encoding]::ASCII.GetString($m) -eq 'MICROSOFT*XBOX*MEDIA') { return @{ kind = 'iso'; path = $item.FullName } }
    }
    return @{ kind = ''; why = 'That disc image is not an Xbox 360 game disc.' }
  }
  $xex = Join-Path $item.FullName 'default.xex'
  if (Test-Path -LiteralPath $xex) {
    $id = Get-XexTitleId $xex
    if ($id -ne $script:TitleId360) {
      return @{ kind = ''; why = ('This is not the Xbox 360 Final Fantasy XIII-2 (default.xex is for title {0:X8}, not 53510806).' -f $id) }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $item.FullName 'alba_data'))) {
      return @{ kind = ''; why = 'default.xex is here, but the alba_data folder (the game data) is not.' }
    }
    return @{ kind = 'folder'; path = $item.FullName }
  }
  # A 360 USB drive / folder copied off a 360: Content\0000000000000000\53510806\00007000.
  foreach ($sub in 'Content\0000000000000000\53510806\00007000', '0000000000000000\53510806\00007000', '53510806\00007000', '00007000') {
    $god = Join-Path $item.FullName $sub
    if (Test-Path -LiteralPath $god) { return @{ kind = 'god'; path = $item.FullName } }
  }
  # 360 content for the game, but only its DLC (00000002): say so.
  $titles = @('Content\0000000000000000\53510806', '0000000000000000\53510806', '53510806' | ForEach-Object { Join-Path $item.FullName $_ })
  if ($item.Name -eq '53510806') { $titles += $item.FullName }
  foreach ($title in $titles) {
    if (Test-Path -LiteralPath (Join-Path $title '00000002')) {
      return @{ kind = ''; why = 'This holds Final Fantasy XIII-2 DLC copied off a 360 (00000002), not the game itself (00007000). Give the game disc image (.iso) or the extracted game folder here; the DLC is asked for later.' }
    }
  }
  return @{ kind = ''; why = 'No default.xex for 53510806 here, and no Final Fantasy XIII-2 copied off a 360 (Content\0000000000000000\53510806\00007000).' }
}

function Find-Game360 {
  $found = @()
  foreach ($d in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
    foreach ($p in (Join-Path $d.Root 'FFXIII-2\360\game'), (Join-Path $d.Root 'Final Fantasy XIII-2\sources\360\game'), $d.Root) {
      try { $t = Test-Game360 $p; if ($t.kind) { $found += $t } } catch { }
    }
  }
  return $found
}

function Ask-Path([string]$question, [scriptblock]$test, [object[]]$found, [switch]$Optional) {
  foreach ($f in $found) {
    if (Ask-YesNo ("Found {0} at {1}. Use it?" -f $f.label, $f.path)) { return $f }
  }
  while ($true) {
    $a = (Read-Host $question).Trim().Trim('"')
    if (-not $a) {
      if ($Optional) { return $null }
      Say 'This one is needed. Type or paste the path (drag the file or folder into this window works too).' 'Yellow'
      continue
    }
    $t = & $test $a
    if ($t.kind) { return $t }
    Say $t.why 'Yellow'
  }
}

function Ask-Game360 {
  $found = @(Find-Game360 | ForEach-Object { $_.label = 'the Xbox 360 game'; $_ })
  return Ask-Path 'Where is the Xbox 360 game? (disc image .iso, the extracted game folder, or the USB drive / folder from a 360)' ${function:Test-Game360} $found
}

# PS3 disc: an .iso (read directly, see iso.ps1) or a folder with PS3_GAME\PARAM.SFO.
function Test-Ps3([string]$path) {
  $path = $path.Trim('"', ' ')
  if (-not (Test-Path -LiteralPath $path)) { return @{ kind = ''; why = "There is nothing at $path." } }
  $item = Get-Item -LiteralPath $path
  $t = @{ kind = 'folder'; path = $item.FullName }
  if ($item -is [IO.FileInfo]) {
    if ($item.Extension -ne '.iso') { return @{ kind = ''; why = 'That is a file, but not a disc image (.iso).' } }
    $t.kind = 'iso'
    try {
      if (([LrIso]::Locate($item.FullName, 'PS3_GAME/PARAM.SFO'))[0] -eq -1) { return @{ kind = ''; why = 'This is not a PS3 disc image (no PS3_GAME\PARAM.SFO).' } }
    } catch { return @{ kind = ''; why = "Could not read the disc image: $($_.Exception.Message)" } }
  } elseif (-not (Test-Path -LiteralPath (Join-Path $item.FullName 'PS3_GAME\PARAM.SFO'))) {
    return @{ kind = ''; why = 'This is not a PS3 disc (no PS3_GAME\PARAM.SFO).' }
  }
  if ((Read-Ps3Sfo $t) -notmatch 'FINAL FANTASY XIII-2') { return @{ kind = ''; why = 'This PS3 disc is not Final Fantasy XIII-2.' } }
  return $t
}

function Ask-Ps3([switch]$Required) {
  $found = @()
  foreach ($d in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
    foreach ($dir in (Join-Path $d.Root 'Final Fantasy XIII-2'), (Join-Path $env:USERPROFILE 'Downloads')) {
      foreach ($iso in @(Get-ChildItem -LiteralPath $dir -Filter '*Final*Fantasy*XIII-2*.iso' -File -ErrorAction SilentlyContinue)) {
        $t = Test-Ps3 $iso.FullName
        if ($t.kind -and -not ($found | Where-Object { $_.path -eq $t.path })) { $t.label = 'the PS3 disc image'; $found += $t }
      }
    }
  }
  $q = 'Where is the PS3 disc image? (Enter to skip)'
  if ($Required) { $q = 'Where is the PS3 disc image?' }
  return Ask-Path $q ${function:Test-Ps3} $found -Optional:(-not $Required)
}

# Steam: the game folder (ffxiii2.exe + alba_data).
function Test-Steam([string]$path) {
  $path = $path.Trim('"', ' ')
  if (-not (Test-Path -LiteralPath $path -PathType Container)) { return @{ kind = ''; why = "There is no folder at $path." } }
  $program=Test-Path -LiteralPath (Join-Path $path 'alba_data\prog\win\bin\ffxiii2img.exe')
  if (-not (Test-Path -LiteralPath (Join-Path $path 'alba_data\sys\filelistc.win32.bin')) -or
      (-not $program -and -not @(Get-ChildItem -LiteralPath $path -Filter 'ffxiii2*.exe' -File -ErrorAction SilentlyContinue).Count)) {
    return @{ kind = ''; why = 'This is not the Steam Final Fantasy XIII-2 folder, or its Japanese data is missing.' }
  }
  return @{ kind = 'folder'; path = (Get-Item -LiteralPath $path).FullName }
}

function Find-Steam {
  $libs = @('C:\Program Files (x86)\Steam')
  $vdf = 'C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf'
  if (Test-Path -LiteralPath $vdf) {
    foreach ($m in [regex]::Matches((Get-Content -LiteralPath $vdf -Raw), '"path"\s+"([^"]+)"')) { $libs += $m.Groups[1].Value -replace '\\\\', '\' }
  }
  $found = @()
  foreach ($lib in $libs | Select-Object -Unique) {
    # A library on a drive that is not there now (unplugged) is skipped.
    try { $t = Test-Steam (Join-Path $lib 'steamapps\common\FINAL FANTASY XIII-2 FINAL FANTASY XIII') } catch { continue }
    if ($t.kind) { $t.label = 'the Steam version'; $found += $t }
  }
  return $found
}

function Ask-Steam([switch]$Required) {
  $q = 'Where is the Steam version? (Enter to skip)'
  if ($Required) { $q = 'Where is the Steam version?' }
  return Ask-Path $q ${function:Test-Steam} @(Find-Steam) -Optional:(-not $Required)
}

# 360 DLC: the content folder off a 360 (...\53510806\00000002) or loose packages. Each package
# must be an Xbox 360 package (LIVE / PIRS / CON) for title 53510806.
function Test-Dlc360([string]$path) {
  $path = $path.Trim('"', ' ')
  if (-not (Test-Path -LiteralPath $path)) { return @{ kind = ''; why = "There is nothing at $path." } }
  $dir = $path
  foreach ($sub in 'Content\0000000000000000\53510806\00000002', '0000000000000000\53510806\00000002', '53510806\00000002', '00000002') {
    if (Test-Path -LiteralPath (Join-Path $path $sub)) { $dir = Join-Path $path $sub; break }
  }
  $files = @(Get-ChildItem -LiteralPath $dir -File -Recurse -ErrorAction SilentlyContinue)
  if ((Get-Item -LiteralPath $path) -is [IO.FileInfo]) { $files = @(Get-Item -LiteralPath $path) }
  $packs = @()
  foreach ($f in $files) {
    $h = Read-Bytes $f.FullName 0 0x364
    if (-not $h) { continue }
    $magic = [Text.Encoding]::ASCII.GetString($h, 0, 4)
    if ($magic -in 'LIVE', 'PIRS', 'CON ' -and (BE32 $h 0x360) -eq $script:TitleId360 -and (BE32 $h 0x344) -eq 2) { $packs += $f.FullName }
  }
  if (-not $packs.Count) { return @{ kind = ''; why = 'No Final Fantasy XIII-2 360 DLC packages here (Content\0000000000000000\53510806\00000002 on the drive from the 360).' } }
  return @{ kind = 'packages'; path = (Get-Item -LiteralPath $dir).FullName; packages = $packs }
}

function Ask-Dlc360([switch]$Required) {
  $found = @()
  foreach ($d in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
    $t = $null
    try { if (Test-Path -LiteralPath (Join-Path $d.Root 'Content\0000000000000000\53510806\00000002')) { $t = Test-Dlc360 $d.Root } } catch { }
    if ($t -and $t.kind) { $t.label = "the 360 DLC ($($t.packages.Count) package(s))"; $found += $t }
  }
  $q = 'Do you have the 360 DLC? (Enter to skip - you can add it any time later)'
  if ($Required) { $q = 'Where is the 360 DLC? (the drive or folder from the 360)' }
  return Ask-Path $q ${function:Test-Dlc360} $found -Optional:(-not $Required)
}

function Get-FreeGB([string]$path) {
  $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path))
  $drive = Get-PSDrive -Name $root.Substring(0, 1) -ErrorAction SilentlyContinue
  if (-not $drive) { return 0 }
  return [math]::Floor($drive.Free / 1GB)
}

function Ask-Output($state) {
  $need = 35
  # Default: "Final Fantasy XIII-2" on the drive with the most free space.
  $best = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Free } |
          Sort-Object Free -Descending | Select-Object -First 1
  $suggest = Join-Path $best.Root 'Final Fantasy XIII-2'
  # Never a folder with things the builder did not make (an install set up by hand, for one).
  if ((Test-Path -LiteralPath $suggest) -and -not (Test-Path -LiteralPath (Join-Path $suggest 'xiii2_builder_state.json')) -and
      @(Get-ChildItem -LiteralPath $suggest -Force -ErrorAction SilentlyContinue).Count) {
    $suggest = Join-Path $best.Root 'Final Fantasy XIII-2 Port'
  }
  while ($true) {
    $a = (Read-Host "Where should it go? (Enter for $suggest)").Trim().Trim('"')
    if (-not $a) { $a = $suggest }
    try { $full = [IO.Path]::GetFullPath($a) } catch { Say 'That is not a usable folder path.' 'Yellow'; continue }
    foreach ($src in $state.inputs.Values) {
      if ($src -and $src.path -and (Test-X2PathOverlap $full $src.path)) {
        Say 'Please pick a folder outside your game files (they are only read, never written).' 'Yellow'
        $full = $null
        break
      }
    }
    if (-not $full) { continue }
    if ((Test-Path -LiteralPath $full) -and -not (Test-Path -LiteralPath (Join-Path $full 'xiii2_builder_state.json')) -and
        @(Get-ChildItem -LiteralPath $full -Force -ErrorAction SilentlyContinue).Count) {
      Say 'That folder already has files in it that the builder did not make. Please pick an empty or new folder.' 'Yellow'
      continue
    }
    $free = Get-FreeGB $full
    if ($free -lt $need) { Say "That drive has $free GB free; this needs about $need GB." 'Yellow'; continue }
    try {
      New-Item -ItemType Directory -Force -Path $full | Out-Null
      $probe = Join-Path $full '.builder_write_test'
      Set-Content -LiteralPath $probe -Value 'ok'
      Remove-Item -LiteralPath $probe -Force
    } catch { Say "Cannot write to $full ($($_.Exception.Message))." 'Yellow'; continue }
    return $full
  }
}

# ---- build state (xiii2_builder_state.json in the output folder: choices + finished steps) ----
function New-BuildState([string]$target) {
  return [pscustomobject]@{ version = 1; product = 'xiii2-triumtemporate'; target = $target; output = ''; inputs = @{}; shortcut = $false; done = @(); finished = $false
                            codeHash = '' }
}
function Read-BuildState([string]$output) {
  $f = Join-Path $output 'xiii2_builder_state.json'
  if (-not (Test-Path -LiteralPath $f)) { return $null }
  $j = Get-Content -LiteralPath $f -Raw | ConvertFrom-Json
  if ($j.product -ne 'xiii2-triumtemporate' -or $j.version -ne 1) { throw 'This is not a supported XIII-2 builder state.' }
  $inputs = @{}
  foreach ($p in $j.inputs.PSObject.Properties) {
    if ($p.Value) { $h = @{}; foreach ($q in $p.Value.PSObject.Properties) { $h[$q.Name] = $q.Value }; $inputs[$p.Name] = $h }
  }
  $hashes=@{}
  if ($j.hashes) { foreach ($property in $j.hashes.PSObject.Properties) { $hashes[$property.Name]=[string]$property.Value } }
  return [pscustomobject]@{ version = $j.version; product = $j.product; target = $j.target; output = $output; inputs = $inputs; shortcut = [bool]$j.shortcut
                            done = @($j.done); finished = [bool]$j.finished; codeHash = [string]$j.codeHash; hashes=$hashes }
}

# Both directions matter: output equal to, within, or enclosing an input is unsafe.
function Test-X2PathOverlap([string]$a,[string]$b) {
  $a=[IO.Path]::GetFullPath($a).TrimEnd('\','/')
  $b=[IO.Path]::GetFullPath($b).TrimEnd('\','/')
  if ($a.Equals($b,[StringComparison]::OrdinalIgnoreCase)) { return $true }
  $sep=[IO.Path]::DirectorySeparatorChar
  return $a.StartsWith($b+$sep,[StringComparison]::OrdinalIgnoreCase) -or $b.StartsWith($a+$sep,[StringComparison]::OrdinalIgnoreCase)
}

$script:XexSha256='73D79C6D7C9BA087E33AECE7A88A9A8669D6A6D8444FE789EDD68AEF0318B66E'
$script:XexpSha256=@{
  default='D980AD1D01FA3C922E7A94A3EC29E1FCEDB913580B99BB2AF8DD41ED457EF9DD'
  z0070='98FF7BFE3B37DA6DE0ABB451515E461F17A8393DE8BFC1EFEBC7AC8FC15AE043'
  z0130='2641BF84299FEF841CB101B5C13C59737BC705352A0268CEDDCD21EC76E6580C'
  z0160='F5BC60570A54F50DDDC707CF4CD1F4641F50E2B383D625FB18F8234C37E8487D'
  z0161='A847031AB54BA58C3F45D85DDCE0A363BB574EE7548942B49A41C92689CDA3A5'
}
function Assert-X2Game([string]$game) {
  foreach ($name in @('default.xex','z0070.xex','z0130.xex','z0160.xex','z0161.xex','alba_data\sys\filelistu.x360.bin','alba_data\sys\white_imgu.x360.bin')) {
    if (-not (Test-Path -LiteralPath (Join-Path $game $name) -PathType Leaf)) { throw "Incomplete Xbox 360 game: $name missing." }
  }
  $actual=(Get-FileHash -LiteralPath (Join-Path $game 'default.xex')).Hash
  if ($actual -ne $script:XexSha256) { throw "Unsupported default.xex ($actual). Supply the original USA release, without applying the update to it." }
}
function Test-TitleUpdate([string]$path) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return @{kind='';why='Supply the title-update package file, not its containing folder.'} }
  $h=Read-Bytes $path 0 0x364
  if (-not $h -or [Text.Encoding]::ASCII.GetString($h,0,4) -notin 'LIVE','PIRS','CON ' -or (BE32 $h 0x360) -ne $script:TitleId360 -or (BE32 $h 0x344) -ne 0xB0000) {
    return @{kind='';why='This is not an Xbox 360 XIII-2 title-update package.'}
  }
  return @{kind='package';path=(Get-Item -LiteralPath $path).FullName}
}
function Ask-TitleUpdate {
  Say 'Required: Xbox 360 title update 0.0.1.13 (tu0000000d_00000000).'
  Say 'On the console: Settings > System > Storage > Games and Apps > Final Fantasy XIII-2.'
  Say 'MOVE the update to a USB flash drive; do not use Copy. Extract its package on your PC.'
  return Ask-Path 'Where is tu0000000d_00000000?' ${function:Test-TitleUpdate} @()
}
function Save-BuildState($state) {
  $file = Join-Path $state.output 'xiii2_builder_state.json'
  $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath ($file + '.new') -Encoding UTF8
  Move-Item -LiteralPath ($file + '.new') -Destination $file -Force
  try { Set-Content -LiteralPath (Join-Path $Here 'xiii2_last_output.txt') -Value $state.output } catch { }
}
# The output folder of the last build (remembered next to the builder), else a usual one.
function Find-ExistingOutput {
  $last = Join-Path $Here 'xiii2_last_output.txt'
  if (Test-Path -LiteralPath $last) {
    $p = (Get-Content -LiteralPath $last -TotalCount 1).Trim()
    if ($p -and (Test-Path -LiteralPath (Join-Path $p 'xiii2_builder_state.json'))) { return $p }
  }
  foreach ($d in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
    foreach ($name in 'Final Fantasy XIII-2', 'Final Fantasy XIII-2 Port') {
      $p = Join-Path $d.Root $name
      if (Test-Path -LiteralPath (Join-Path $p 'xiii2_builder_state.json')) { return $p }
    }
  }
  return ''
}
