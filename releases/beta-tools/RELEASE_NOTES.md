Windows beta tools for Final Fantasy XIII-2 - Triumtemporate Edition.

- Unpack **Final-Fantasy-XIII-2-Builder.zip** and run **Build Final Fantasy XIII-2.bat**. Supply your own Xbox 360 game and title update; PS3, Steam, and Xbox DLC inputs are optional. Setup resumes after an interrupted build.
- Unpack **Final-Fantasy-XIII-2-Save-Importer.zip**, close the game, and run **Import Final Fantasy XIII-2 Save.bat**. Supply your original Xbox XIII-2 save package and builder output folder. Existing saves and headers are backed up before import.
- Verify both downloads against **SHA256SUMS.txt**.

Original Xbox DLC has worked in user testing with English and Japanese voices. Japanese base-game lip-sync has worked in the tested gameplay. These results do not establish complete scene or battle coverage.

**Known issues:** Steam DLC conversion remains buggy, including Omega crashes and Valhalla character texture problems. Japanese voices do not require converted Steam DLC. **Android is on hold.**

The complete standalone Windows build and imported save loading still need verification. Save extraction, backup, and header generation were tested locally; Windows junction creation and Explorer opening were mocked in the save-wrapper check.

Includes the corrected third-party acknowledgments. Source tools only: no game files, saves, or private documents are distributed.
