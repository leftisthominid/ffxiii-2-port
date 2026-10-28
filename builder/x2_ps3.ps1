# FFXIII-2 PS3 cinematics: the video of the 97 movies, copied (no re-encoding) out of the PS3
# disc's movie pack into <game folder>\movies_ps3\<name>.mp4, with ffmpeg.exe next to the game
# (x2_movies.cpp plays them through it over the 360 Bink frames).
# name, position in movieu.ps3.bin (2048-byte units), size - from movielistu.ps3.bin.
$script:X2Ps3Movies = @(
  @('hm_hstr_300_us', 0x0, 0x160d000), @('hm_hstr_311_us', 0x2c1a, 0xbab800), @('hm_hstr_310_us', 0x4371, 0xada000),
  @('hm_hstr_320_us', 0x5925, 0xb08800), @('hm_hstr_331_us', 0x6f36, 0xdff800), @('hm_hstr_330_us', 0x8b35, 0xeb1000),
  @('hm_hstr_230_us', 0xa897, 0xfbb800), @('hm_hstr_231_us', 0xc80e, 0x1044000), @('hm_hstr_340_us', 0xe896, 0xb54800),
  @('hm_hstr_220_us', 0xff3f, 0xc06800), @('hm_hstr_351_us', 0x1174c, 0xdff800), @('hm_hstr_350_us', 0x1334b, 0xd76000),
  @('hm_hstr_210_us', 0x14e37, 0xc57800), @('hm_hstr_211_us', 0x166e6, 0xbaf800), @('hm_hstr_450_us', 0x17e45, 0x1483800),
  @('hm_hstr_360_us', 0x1a74c, 0xb95000), @('hm_hstr_200_us', 0x1be76, 0xebc000), @('hm_hstr_160_us', 0x1dbee, 0xa57800),
  @('hm_hstr_440_us', 0x1f09d, 0xd52800), @('hm_hstr_371_us', 0x20b42, 0x935800), @('hm_hstr_370_us', 0x21dad, 0x9bf000),
  @('hm_hstr_270_us', 0x2312b, 0xe7e000), @('hm_hstr_171_us', 0x24e27, 0x1489000), @('hm_hstr_170_us', 0x27739, 0x15d1000),
  @('hm_hstr_470_us', 0x2a2db, 0x15e1800), @('hm_hstr_471_us', 0x2ce9e, 0x14f4800), @('hm_hstr_380_us', 0x2f887, 0xf7f800),
  @('hm_hstr_381_us', 0x31786, 0x111c000), @('hm_hstr_261_us', 0x339be, 0xca3800), @('hm_hstr_260_us', 0x35305, 0x975800),
  @('hm_hstr_140_us', 0x365f0, 0xb00800), @('hm_hstr_460_us', 0x37bf1, 0xd61000), @('hm_hstr_390_us', 0x396b3, 0xa30000),
  @('hm_hstr_250_us', 0x3ab13, 0xe4b800), @('hm_hstr_151_us', 0x3c7aa, 0x13d4000), @('hm_hstr_150_us', 0x3ef52, 0x150b000),
  @('hm_hstr_090_us', 0x41968, 0xebe000), @('hm_hstr_091_us', 0x436e4, 0xe94800), @('hm_hstr_410_us', 0x4540d, 0xc07000),
  @('hm_hstr_240_us', 0x46c1b, 0x13cd800), @('hm_hstr_120_us', 0x493b6, 0xda5800), @('hm_hstr_080_us', 0x4af01, 0xa0a000),
  @('hm_hstr_401_us', 0x4c315, 0xa8b800), @('hm_hstr_400_us', 0x4d82c, 0xc96800), @('hm_hstr_131_us', 0x4f159, 0xc00000),
  @('hm_hstr_130_us', 0x50959, 0x9b8800), @('hm_hstr_430_us', 0x51cca, 0x12ea000), @('hm_hstr_100_us', 0x5429e, 0x75f000),
  @('hm_hstr_421_us', 0x5515c, 0xd51800), @('hm_hstr_420_us', 0x56bff, 0xd52800), @('hm_hstr_290_us', 0x586a4, 0x143e800),
  @('hm_hstr_111_us', 0x5af21, 0xb29800), @('hm_hstr_110_us', 0x5c574, 0xa4d800), @('hm_hstr_281_us', 0x5da0f, 0xf55000),
  @('hm_hstr_280_us', 0x5f8b9, 0xfef800), @('hm_hstr_010_us', 0x61898, 0x1320000), @('hm_hstr_011_us', 0x63ed8, 0x14e7800),
  @('hm_hstr_490_us', 0x668a7, 0x15a6800), @('hm_hstr_480_us', 0x693f4, 0xd2d800), @('hm_hstr_030_us', 0x6ae4f, 0xc37000),
  @('hm_hstr_180_us', 0x6c6bd, 0x13e0000), @('hm_hstr_021_us', 0x6ee7d, 0x1402000), @('hm_hstr_020_us', 0x71681, 0x13fb000),
  @('hm_hstr_191_us', 0x73e77, 0xd30000), @('hm_hstr_190_us', 0x758d7, 0xbf0800), @('hm_hstr_050_us', 0x770b8, 0x158c000),
  @('hm_hstr_041_us', 0x79bd0, 0x1422000), @('hm_hstr_040_us', 0x7c414, 0x1685000), @('hm_hstr_070_us', 0x7f11e, 0x14c2000),
  @('hm_hstr_071_us', 0x81aa2, 0x171a000), @('hm_hstr_060_us', 0x848d6, 0x1669000), @('hm_hstr_520_us', 0x875a8, 0xd31800),
  @('hm_hstr_531_us', 0x8900b, 0xf48800), @('hm_hstr_530_us', 0x8ae9c, 0xf62800), @('hm_hstr_500_us', 0x8cd61, 0x153a800),
  @('hm_hstr_501_us', 0x8f7d6, 0x14e4000), @('hm_hstr_510_us', 0x9219e, 0xd22000), @('hm_hstr_560_us', 0x93be2, 0xd48800),
  @('hm_hstr_570_us', 0x95673, 0x1207800), @('hm_hstr_540_us', 0x97a82, 0xad5800), @('hm_hstr_551_us', 0x9902d, 0x144f000),
  @('hm_hstr_550_us', 0x9b8cb, 0x150f800), @('hm_hstr_580_us', 0x9e2ea, 0x11f7800), @('hm_hstr_590_us', 0xa06d9, 0x1281000),
  @('hm_hstr_620_us', 0xa2bdb, 0x11f7000), @('hm_hstr_610_us', 0xa4fc9, 0x14ed800), @('hm_hstr_611_us', 0xa79a4, 0x1644800),
  @('hm_hstr_601_us', 0xaa62d, 0x13b1000), @('hm_hstr_600_us', 0xacd8f, 0x1406800), @('tm_abst_080_us', 0xaf59c, 0x2082000),
  @('tm_abst_060_us', 0xb36a0, 0x247d800), @('tm_abst_070_us', 0xb7f9b, 0x25fe800), @('tm_abst_040_us', 0xbcb98, 0x22ad800),
  @('tm_abst_050_us', 0xc10f3, 0x24b9000), @('tm_abst_020_us', 0xc5a65, 0x23d8800), @('tm_abst_030_us', 0xca216, 0x23e5800),
  @('tm_abst_010_us', 0xce9e1, 0x23a1000)
)
$script:X2Ps3MoviePack = 'PS3_GAME/USRDIR/alba_data/movie/movieu.ps3.bin'
$script:X2Ps3MoviePackSize = 1770592256

function Convert-X2Ps3Movies([string]$iso, [string]$ffmpeg, [string]$outDir, [string]$work, [string]$logs) {
  New-Item -ItemType Directory -Force -Path $outDir, $work, $logs | Out-Null
  $todo = @($script:X2Ps3Movies | Where-Object { -not ((Test-Path -LiteralPath (Join-Path $outDir "$($_[0]).mp4")) -and (Get-Item -LiteralPath (Join-Path $outDir "$($_[0]).mp4")).Length -gt 0) })
  if (-not $todo.Count) { Say "  all $($script:X2Ps3Movies.Count) PS3 movies already in $outDir"; return }
  $at = [LrIso]::Locate($iso, $script:X2Ps3MoviePack)
  $file = $iso; $offset = $at[0]
  if ($at[1] -ne $script:X2Ps3MoviePackSize) {
    throw ("movieu.ps3.bin on this PS3 disc is {0:N0} bytes, not the {1:N0} of the USA disc (BLUS30776), the only one supported so far." -f $at[1], $script:X2Ps3MoviePackSize)
  }
  if ($at[0] -eq -2) {
    # Stored in pieces that are not one after the other: copied out once (1.8 GB).
    $copy = Join-Path $work 'movieu.ps3.bin'
    if (-not ((Test-Path -LiteralPath $copy) -and (Get-Item -LiteralPath $copy).Length -eq $at[1])) {
      Say '  copying movieu.ps3.bin out of the PS3 disc image (it is stored in pieces)...'
      $job = Start-Job -ScriptBlock { param($i, $r, $d, $isoPs1) . $isoPs1; [LrIso]::Extract($i, $r, $d) } -ArgumentList $iso, $script:X2Ps3MoviePack, $copy, (Join-Path $PSScriptRoot 'iso.ps1')
      try {
        Wait-WithProgress { [bool](Wait-Job $job -Timeout 15) } { '{0:N1} of {1:N1} GB' -f ((Get-Item -LiteralPath $copy -ErrorAction SilentlyContinue).Length / 1GB), ($at[1] / 1GB) }
        Receive-Job $job -ErrorAction Stop | Out-Null
      } finally { Remove-Job $job -Force }
    }
    $file = $copy; $offset = 0
  }
  $tmp = Join-Path $work 'movie.pam'
  $buf = New-Object byte[] (4MB)
  $i = 0; $failed = @(); $last = [DateTime]::Now
  foreach ($m in $script:X2Ps3Movies) {
    $i++
    $name, $sector, $size = $m
    $out = Join-Path $outDir "$name.mp4"
    if ((Test-Path -LiteralPath $out) -and (Get-Item -LiteralPath $out).Length -gt 0) { continue }
    $src = [IO.File]::Open($file, 'Open', 'Read', 'Read')
    $dst = [IO.File]::Create($tmp)
    try {
      $src.Position = $offset + [int64]$sector * 2048
      $left = [int64]$size; $first = $true; $header = 0
      while ($left -gt 0) {
        $n = $src.Read($buf, 0, [int][math]::Min($buf.Length, $left))
        if ($n -le 0) { throw "Short read in movieu.ps3.bin at $name" }
        if ($first -and [Text.Encoding]::ASCII.GetString($buf, 0, 4) -ne 'PAMF') { throw "$name does not start where expected; this PS3 disc is not the supported one" }
        if ($first) { $header = (([int64]$buf[8] -shl 24) -bor ([int64]$buf[9] -shl 16) -bor ([int64]$buf[10] -shl 8) -bor [int64]$buf[11]) * 2048 }
        $first = $false
        $dst.Write($buf, 0, $n)
        $left -= $n
      }
    } finally { $src.Close(); $dst.Close() }
    $ok = $false
    $log = Join-Path $logs "movie_$name.log"
    foreach ($a in @(@('-f', 'mpeg', '-i', $tmp, '-map', '0:v:0'),
                     @('-probesize', '1G', '-analyzeduration', '600M', '-f', 'mpeg', '-i', $tmp, '-map', '0:v:0'),
                     @('-skip_initial_bytes', "$header", '-f', 'mpeg', '-i', $tmp, '-map', '0:v:0'),
                     @('-skip_initial_bytes', "$header", '-probesize', '1G', '-analyzeduration', '600M', '-f', 'mpeg', '-i', $tmp, '-map', '0:v:0'))) {
      if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
      $rc = Invoke-Logged $ffmpeg (@('-hide_banner', '-loglevel', 'error', '-nostdin', '-y') + $a + @('-c', 'copy', '-an', '-sn', '-movflags', '+faststart', $out)) $log -Quiet
      if ($rc -eq 0 -and (Test-Path -LiteralPath $out) -and (Get-Item -LiteralPath $out).Length -gt 16KB) { $ok = $true; break }
    }
    Remove-Item -LiteralPath $tmp -Force
    if (-not $ok) {
      if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
      $failed += $name; Say ("  [{0,2}/{1}] {2}: could not convert (stays the 360 version); see {3}" -f $i, $script:X2Ps3Movies.Count, $name, $log) 'Yellow'
    } elseif (([DateTime]::Now - $last).TotalSeconds -ge 10 -or $i -eq $script:X2Ps3Movies.Count) {
      Say ("  [{0,2}/{1}] {2}" -f $i, $script:X2Ps3Movies.Count, $name); $last = [DateTime]::Now
    }
  }
  if ($failed.Count -eq $script:X2Ps3Movies.Count) { throw "None of the PS3 movies converted; see $logs\movie_*.log" }
  Say ("  {0} of {1} PS3 movies in {2}" -f ($script:X2Ps3Movies.Count - $failed.Count), $script:X2Ps3Movies.Count, $outDir)
}
