<# Final Fantasy XIII-2 - Triumtemporate Edition. Double-click Build Final Fantasy XIII-2.bat. #>
param([string]$Output='')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Here=$PSScriptRoot
. (Join-Path $Here 'builder\iso.ps1')
. (Join-Path $Here 'builder\inputs.ps1')
. (Join-Path $Here 'builder\pipeline.ps1')
. (Join-Path $Here 'builder\moogle.ps1')

function Say([string]$text,[string]$color='') {
    $text = Format-MogMessage $text
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
}
function Ask-YesNo([string]$question) {
    while ($true) {
        $answer=(Read-Host "$question (y/n)").Trim().ToLowerInvariant()
        if ($answer -in 'y','yes') { return $true }
        if ($answer -in 'n','no') { return $false }
        Say 'Please answer y or n.' 'Yellow'
    }
}
function Ask-Choice([string]$question,[string[]]$options) {
    Say $question
    for ($i=0;$i -lt $options.Count;$i++) { Say ("  [{0}] {1}" -f ($i+1),$options[$i]) }
    while ($true) {
        $number=0
        if ([int]::TryParse((Read-Host 'Choose a number').Trim(),[ref]$number) -and $number -ge 1 -and $number -le $options.Count) { return $number }
        Say "Please choose 1 through $($options.Count)." 'Yellow'
    }
}
function Show-Opening {
    Clear-Host
    Say 'Final Fantasy XIII-2 - Triumtemporate Edition - builder' 'Cyan'
    Say ''
    if (Ask-YesNo 'Lightning Farron is bae, kupo!') { Say 'Correct, kupo!' 'Green' }
    elseif (Ask-YesNo 'Was that a typo, kupo? Lightning Farron is bae.') { Say 'Correct now, kupo!' 'Green' }
    else { Say 'Hopefully playing this game changes your mind, kupo!' 'Magenta' }
    Say ''
    Say 'Before we begin, let me tell you how this was made, kupo.'
    Say 'This project uses generative AI coding, including Claude (Anthropic) and ChatGPT (OpenAI).'
    Say 'leftisthominid directs the work and tests the game. It builds on the Lightning Returns port.'
    if (-not (Ask-YesNo 'Are you OK with how this project was made, kupo?')) { Say 'Understood, kupo. Nothing was changed or written. Goodbye!'; exit 10 }
    Say "Then let's get started!"
    Say ''
    Say 'Windows build. Android is on hold.'
    Say 'Original Xbox 360 DLC and Japanese voices/lip-sync have worked in user testing.'
    Say 'The base game runs without Xbox DLC. Steam DLC conversion is still buggy (including Omega crashes and Valhalla textures).'
}
function Ask-SteamDlc($state) {
    if (-not $state.inputs.steam) { return }
    if ($state.inputs.dlc360) {
        Say 'Using original Xbox 360 DLC. Steam supplies Japanese voices independently.'
        return
    }
    if (Ask-YesNo 'Enable the EXPERIMENTAL Steam DLC conversion? It still has crashes and broken textures; Japanese voices do not require it.') {
        $state.inputs.steamdlc=@{kind='experimental';path=$state.inputs.steam.path}
    }
}

try {
    Show-Opening
    if ($env:OS -ne 'Windows_NT') { throw 'This builder requires 64-bit Windows 10 or 11.' }
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    if (-not $Output) { $Output=Find-ExistingOutput }
    $state=if ($Output) { Read-BuildState $Output } else { $null }
    if (-not $state) {
        $state=New-BuildState 'windows'
        $state.inputs.game360=Ask-Game360
        $state.inputs.update=Ask-TitleUpdate
        $state.inputs.ps3=Ask-Ps3
        $state.inputs.steam=Ask-Steam
        $state.inputs.dlc360=Ask-Dlc360
        Ask-SteamDlc $state
        if ($Output) {
            # Explicit output is still subject to all source-overlap/nonempty checks.
            $candidate=[IO.Path]::GetFullPath($Output)
            foreach ($sourceInput in $state.inputs.Values) { if ($sourceInput -and (Test-X2PathOverlap $candidate $sourceInput.path)) { throw 'Output overlaps an input. Choose a separate empty folder.' } }
            if ((Test-Path -LiteralPath $candidate) -and @(Get-ChildItem -LiteralPath $candidate -Force).Count) { throw 'Choose an empty output folder.' }
            $state.output=$candidate
            New-Item -ItemType Directory -Path $candidate -Force | Out-Null
        } else { $state.output=Ask-Output $state }
        $state.shortcut=Ask-YesNo 'Shall I put a Final Fantasy XIII-2 shortcut on your desktop, kupo?'
        Invoke-Pipeline $state
    } elseif (-not $state.finished) {
        Say "Welcome back! Your choices are saved. Picking up where we left off, kupo!"
        Say "Build folder: $($state.output)" 'Cyan'
        Invoke-Pipeline $state
    } else {
        $choice=Ask-Choice 'Final Fantasy XIII-2 is already built. What do you want to do?' @(
            'Update the game and builder components', 'Add or update original Xbox 360 DLC',
            'Add the PS3 disc (cinematics and buttons)', 'Add the Steam version (Japanese voices and lip-sync)',
            'Rebuild the program and renderer', 'Rebuild everything (keep saves)', 'Nothing - close')
        switch ($choice) {
            1 { Invoke-Pipeline $state }
            2 {
                $state.inputs.dlc360=Ask-Dlc360 -Required
                $state.inputs.Remove('steamdlc')
                Invoke-Pipeline $state -Only @('dlc','dlcvoices')
            }
            3 { $state.inputs.ps3=Ask-Ps3 -Required; Invoke-Pipeline $state -Only @('ps3') }
            4 { $state.inputs.steam=Ask-Steam -Required; Ask-SteamDlc $state; Invoke-Pipeline $state -Only @('voices','dlcvoices') }
            5 { Invoke-Pipeline $state -Only @('compile','renderer','install') }
            6 {
                $state.done=@()
                Remove-Item -LiteralPath (Join-Path $state.output 'project\codegen.inputs') -Force -ErrorAction SilentlyContinue
                Invoke-Pipeline $state
            }
            7 { }
        }
    }
} catch { Say $_.Exception.Message 'Red'; exit 1 }
