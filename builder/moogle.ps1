# Selected builder narration only. Never decorate arbitrary tool output, paths,
# checksums, or exception text. Keep this separate from build/cache fingerprints.
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
        'Preparing: Recompiling and building the game' { return 'Building the game now. This part takes a while!' }
        'Preparing: PS3 cinematics and buttons' { return 'Getting your PS3 cinematics and buttons ready, kupo!' }
        'Preparing: Japanese voices and lip-sync' { return 'Preparing Japanese voices and lip-sync. Almost time to hear them, kupo!' }
        'Preparing: Original Xbox 360 DLC' { return 'Preparing your original Xbox 360 DLC, kupo!' }
    }
    if ($text.StartsWith('Builder completed. Play: ')) {
        return 'The build is ready, kupo! Opening your game folder now.' + "`n" + $text.Substring('Builder completed. '.Length)
    }
    return $text
}
