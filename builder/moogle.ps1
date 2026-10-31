# Builder narration and prompts. Keep this separate from build/cache fingerprints.
function Add-MogEnding([string]$text) {
    if (-not $text.Trim() -or $text -match '(?i)\bkupo\b') { return $text }
    $ending=if ($text.EndsWith('?')) {'?'} elseif ($text.EndsWith('!')) {'!'} else {'.'}
    return $text.TrimEnd('.','!','?') + ', kupo' + $ending
}
function Format-MogPrompt([string]$text) {
    if ($text -match '(?i)\bkupo\b') { return $text }
    # Place the voice before answer hints, preserving every path and hint verbatim.
    $hint=$text.IndexOf(' (')
    if ($hint -ge 0) { return (Add-MogEnding $text.Substring(0,$hint)) + $text.Substring($hint) }
    return Add-MogEnding $text
}
function Format-MogMessage([string]$text) {
    switch -Exact ($text) {
        'Please answer y or n.' { return 'Please answer y or n, kupo!' }
        'MOVE the update to a USB flash drive; do not use Copy. Extract its package on your PC.' {
            return 'Move the update to your USB flash drive - choose Move, not Copy, kupo! Extract its package on your PC.'
        }
        'Using original Xbox 360 DLC. Steam supplies Japanese voices independently.' {
            return 'Original Xbox 360 DLC it is, kupo! Steam supplies Japanese voices independently.'
        }
        'Preparing: Build tools' { return 'Gathering the tools we need, kupo!' }
        'Preparing: Game icon' { return 'Making your game icon shine, kupo!' }
        'Preparing: Recompiling and building the game' { return 'Building the game now. This part takes a while, kupo!' }
        'Preparing: PS3 cinematics and buttons' { return 'Getting your PS3 cinematics and buttons ready, kupo!' }
        'Preparing: Japanese voices and lip-sync' { return 'Preparing Japanese voices and lip-sync. Almost time to hear them, kupo!' }
        'Preparing: Original Xbox 360 DLC' { return 'Preparing your original Xbox 360 DLC, kupo!' }
    }
    if ($text.StartsWith('Builder completed. Play: ')) {
        return 'The build is ready, kupo! Opening your game folder now.' + "`n" + $text.Substring('Builder completed. '.Length)
    }
    # Raw technical lines pass through. Indented tool output is preserved unless
    # it starts with one of the builder's own routine narration phrases.
    $trimmed=$text.TrimStart()
    if (-not $trimmed -or $trimmed -match '^(?:[A-Za-z]:[\\/]|/|SHA256\b|[0-9a-fA-F]{40,}\b|\[\d+/\d+\]|error\b|fatal\b|FAILED:)') {return $text}
    if ($text -match '^\s' -and $trimmed -notmatch '^(?:downloading|unpacking|building|recompiling|already|checking|converting|copying|refreshing|decoding|preparing|no |skipped|the download|the AI|the icon|the conversion|the game|the sample|the voice)\b') {return $text}
    return Add-MogEnding $text
}
