param([string]$SourceZip,[string]$Python='/usr/bin/python3')
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('xbox-dlc-test-'+[guid]::NewGuid().ToString('N'))
$oldProfile=$env:USERPROFILE
$global:X2XboxFixture=@{mode='';calls=0;out='';native='';backup='';tracking=''}
function Get-Process { if($global:X2XboxFixture.mode -eq 'game-running'){[pscustomobject]@{Name='ffxiii2'}} }
function powershell.exe {
 $f=$global:X2XboxFixture;$f.calls++
 if($args -contains 'install'){throw 'Steam DLC installation must never be invoked'}
 if($f.calls -eq 1){
  if($args -notcontains 'restore'){throw 'First invocation must restore Xbox DLC'}
  if($f.mode -eq 'restore-failure'){$global:LASTEXITCODE=7;return}
  Move-Item -LiteralPath $f.backup -Destination $f.native
  Remove-Item -LiteralPath $f.tracking
 } else {
  if($args -contains '-SteamDlc'){throw 'Game invocation must use direct builder defaults'}
  if(-not(Test-Path -LiteralPath $f.native)){throw 'Game started without restored package'}
 }
 $global:LASTEXITCODE=0
}
try {
 foreach($mode in @('success','missing-backup','game-running','restore-failure')){
  $case=Join-Path $root $mode;$out=Join-Path $case 'out';$lr=Join-Path $case 'lr';$game=Join-Path $case 'game'
  $packages=Join-Path $out 'userdata/0000000000000000/53510806/00000002'
  $old=Join-Path $out 'save_backups/old';$name='A'*40;$native=Join-Path $packages $name;$backup=Join-Path $old $name
  $tracking=Join-Path $out 'work/steamdlc/moved_360_packages.txt'
  foreach($folder in @($packages,$old,$game,(Join-Path $lr 'tools/python/tools'),(Split-Path $tracking),(Join-Path $out 'run/movies_steam'),(Join-Path $out 'userdata/saves'),(Join-Path $out 'logs'))){New-Item -ItemType Directory -Path $folder -Force|Out-Null}
  New-Item -ItemType SymbolicLink -Path (Join-Path $lr 'tools/python/tools/python.exe') -Target $Python|Out-Null
  foreach($id in @('0000021','0000023','0000071')){New-Item -ItemType Directory -Path (Join-Path $packages ('XIII2SteamDLC'+$id))|Out-Null}
  [IO.File]::WriteAllText($tracking,"$name`t$old")
  [IO.File]::WriteAllText((Join-Path $out 'userdata/voice.txt'),'jp')
  [IO.File]::WriteAllText((Join-Path $out 'userdata/saves/keep.sav'),'unchanged save fixture')
  [IO.File]::WriteAllText((Join-Path $out 'logs/run.log'),'Fixture only; no gameplay ran.')
  if($mode -ne 'missing-backup'){New-Item -ItemType Directory -Path $backup|Out-Null;[IO.File]::WriteAllText((Join-Path $backup 'identifier'),'0000071')}
  $env:USERPROFILE=$case
  $global:X2XboxFixture=@{mode=$mode;calls=0;out=$out;native=$native;backup=$backup;tracking=$tracking}
  $threw=$false
  try{& (Join-Path $PSScriptRoot '../XIII2_Test_XboxDLC.ps1') -Out $out -Game $game -LrRoot $lr -Content (Join-Path $case 'content') -Steam (Join-Path $case 'steam') -SourceZip $SourceZip}catch{$threw=$true}
  if($threw -ne ($mode -ne 'success')){throw "Unexpected failure result: $mode"}
  if([IO.File]::ReadAllText((Join-Path $out 'userdata/saves/keep.sav')) -ne 'unchanged save fixture'){throw 'Save data changed'}
  if($mode -in @('missing-backup','game-running')){
   if($global:X2XboxFixture.calls -ne 0){throw 'Preflight failure invoked builder'}
   if(@(Get-ChildItem -LiteralPath $packages -Directory -Filter 'XIII2SteamDLC*').Count -ne 3){throw 'Preflight failure moved packages'}
   if([IO.File]::ReadAllText((Join-Path $out 'userdata/voice.txt')) -ne 'jp'){throw 'Preflight failure changed voice'}
  }else{
   $stash=@(Get-ChildItem -LiteralPath (Join-Path $out 'save_backups') -Directory -Filter 'xbox-dlc-test-*')[0].FullName
   if(@(Get-ChildItem -LiteralPath (Join-Path $stash 'converted-packages') -Directory).Count -ne 3){throw 'Converted packages not retained'}
   if(-not(Test-Path -LiteralPath (Join-Path $stash 'movies_steam'))){throw 'Steam movie override not quarantined'}
   if([IO.File]::ReadAllText((Join-Path $stash 'voice.txt')) -ne 'jp'){throw 'Prior voice preference not retained'}
   $expected=if($mode -eq 'success'){2}else{1};if($global:X2XboxFixture.calls -ne $expected){throw 'Unexpected builder call count'}
  }
  Write-Output "PASS Xbox DLC $mode (builder/game mocked; source ZIP extraction and bounded collector executed)"
 }
}finally{
 $env:USERPROFILE=$oldProfile
 Remove-Variable -Scope Global -Name X2XboxFixture
 if(Test-Path -LiteralPath $root){Remove-Item -LiteralPath $root -Recurse -Force}
}
