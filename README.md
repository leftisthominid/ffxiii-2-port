# Final Fantasy XIII-2 - Triumtemporate Edition

A builder for a native Windows port, made from three copies of the game: Xbox 360, PS3, and Steam.

This builds a native Windows version of **Final Fantasy XIII-2** from **your own copy of the
Xbox 360 game**, with what your PS3 and Steam copies add. The Xbox 360 code is recompiled to
C++ on your machine (with [ReXGlue](https://github.com/rexglue/rexglue-sdk)) and built into a
normal game program. Game executables, generated game code, data, and art are not distributed
in this repository. Without your own copy of the game there is nothing to build.

## How this was made

This project uses AI-assisted coding and investigation, maintained and directed by
[leftisthominid](https://github.com/leftisthominid). It builds on the tools and porting work in
[Lightning Returns: Final Fantasy XIII - Triumfulgurate Edition](https://github.com/leftisthominid/lr-ffxiii-port).
Source changes still need review and gameplay testing.

## What you need

- **The Xbox 360 game** (required): the USA disc image, extracted game folder, or Games on
  Demand copy from your console. The builder extracts it as needed and checks the supported executable.
- **The Xbox 360 title update** (required): version 0.0.1.13, supplied separately from the
  original game files. On the console, open **Settings > System > Storage**, find the update
  under **Games and Apps > Final Fantasy XIII-2**, and **Move it to a USB flash drive — do not
  use Copy**. Moving transfers it off the console's original storage device. On your PC,
  extract `tu0000000d_00000000` from the flash drive's Xbox content storage, under
  `Content\0000000000000000\53510806\000B0000`.
- Optional, each adds something:
  - **The PS3 disc image or extracted disc folder**: PS3 cinematics and PlayStation button / UI
    assets. The current builder takes PlayStation graphics from this copy. The recovered Steam
    controller sheets inspected so far contain Xbox and generic gamepad graphics; the complete
    Steam installation has not been exhaustively checked for unused PlayStation assets.
  - **The Steam version**: Japanese voices and experimental conversion of Steam DLC content.
    Text language is selected separately; Japanese voices do not require installing the
    Steam-derived DLC packages.
  - **Xbox 360 DLC**: the original DLC episodes, Coliseum encounters, outfits, and weapons.
    Original Xbox DLC has worked in user testing with both English and Japanese voices.
  - **PS3 or Steam saves**: import support is experimental; keep backups of your saves.
- Windows 10 or 11, 64-bit, free space for the game files and build, and an internet connection
  for the build tools.
- Microsoft's Visual Studio 2022 Build Tools with **Desktop development with C++** and the
  Windows SDK. The builder offers to install these if missing and downloads its other tools
  at pinned versions checked by SHA256. An existing Lightning Returns installation is not needed.

## Status

Experimental. The Windows build has started and been played on one PC. The user reports that
original Xbox 360 DLC works with both English and Japanese voices, and that the tested base-game
Japanese lip-sync setup works. This does not establish that every scene, battle, or save works.

**Steam-derived DLC conversion is still buggy.** Omega has crashed and Lightning and Caius have
rendered incorrectly in Valhalla with converted Steam DLC. The base game works without Xbox
360 DLC. Steam DLC conversion is a separate, explicit experimental choice; Japanese voices do
not enable it. Original Xbox DLC is the supported content source when available.

The builder enables Japanese audio and base-game lip-sync when a Steam copy is supplied.
Voice Language and Text Language remain separate settings. Some dialogue falls back to English
when a matching Japanese replacement cannot be prepared. DLC lip-sync remains experimental.

**The Android version is on hold.** Current development and testing focus on Windows.

The standalone builder still needs a confirmed complete Windows run. Xbox save package
validation, extraction, backup, and save-header generation have been checked locally;
loading the imported save in the game and native Windows save-tool operation remain unverified.

## Use

Download the **[Windows builder](https://raw.githubusercontent.com/leftisthominid/ffxiii-2-port/3ff5500291933d619dcc4e71f74504fc2f8a1136/releases/current-tools/Final-Fantasy-XIII-2-Builder.zip)**
and unpack it anywhere. The separate **[Xbox 360 save importer](https://raw.githubusercontent.com/leftisthominid/ffxiii-2-port/3ff5500291933d619dcc4e71f74504fc2f8a1136/releases/current-tools/Final-Fantasy-XIII-2-Save-Importer.zip)**
is available for existing saves. Check either download against **[SHA256SUMS.txt](https://raw.githubusercontent.com/leftisthominid/ffxiii-2-port/3ff5500291933d619dcc4e71f74504fc2f8a1136/releases/current-tools/SHA256SUMS.txt)**.
These links point to verified tool packages in the repository; a GitHub Latest release has
not yet been published.

Double-click **`Build Final Fantasy XIII-2.bat`**. It starts `builder.ps1`, explains how the
project was made, asks for your game copies and title update, and builds the Windows program.
Optional PS3 files, Steam files, and original Xbox DLC can be skipped and added later. Android
is on hold and is not offered as a build target.

Choose a new, empty output folder outside your source game folders. A stopped build resumes
from its last completed step. Running the builder again offers updates, additional content,
and rebuilding the program or everything while keeping saves. The builder extracts the app
icon from your own Xbox executable and embeds it in the Windows EXE; it can also create a
desktop shortcut.

Your source game files are only read. Build output and prepared content are written to the
output folder; rerunning the builder reuses work where the relevant checks permit it. Keep
save backups when trying a different content setup or importing saves.

To play on Windows, double-click **`ffxiii2.exe`** in the output folder's **`run`** directory,
or use the desktop shortcut. The port's
settings are under **Settings > Port Settings**, including Voice Language, Text Language,
Button Prompts, frame rate, anti-aliasing, and Story Recaps. Restart when prompted after
changing language. Higher frame rates and settings combinations still need gameplay testing.

To import an Xbox 360 save, unpack the separate **Final Fantasy XIII-2 Save Importer** download.
Close the game, double-click **`Import Final Fantasy XIII-2 Save.bat`**, and supply the original
Xbox `.dat` save package and your builder output folder (normally `C:\Final Fantasy XIII-2`).
The tool validates the game title and package, backs up existing saves and headers, and
prepares the save for the port. An existing save in the same slot is replaced after backup;
keep the backup folder shown by the tool. Final Fantasy XIII saves are rejected even though
their filenames can resemble XIII-2 saves. Start the game normally and choose **Load Game**.
In-game loading still needs verification.

## What is in this repository

| Folder | What |
|---|---|
| `Build Final Fantasy XIII-2.bat`, `builder.ps1`, `builder\` | the interactive builder, resumable pipeline, and content preparation |
| `project\` | the ReXGlue project: CMake files, the recompiler manifest and function hints, and the port's own code (`src\`) |
| `converters\` | game-format, texture, shader, movie, and Japanese voice converters |
| `scripts\` | content / save import, packaging, and verification tools |
| `releases\current-tools\` | source-only builder and save-importer downloads, release notes, and checksums |

## Credits

- **[leftisthominid](https://github.com/leftisthominid)**: project maintainer, direction, and
  testing coordination; the [Lightning Returns port](https://github.com/leftisthominid/lr-ffxiii-port)
  provides shared tools and porting work.
- **[ReXGlue SDK](https://github.com/rexglue/rexglue-sdk)**: the recompiler, runtime, and renderer
  this port is built on (BSD-3-Clause), itself derived from **[Xenia](https://github.com/xenia-project/xenia)**.
- **[bucanero](https://github.com/bucanero)**: the XIII-family save cipher (`ff13-decrypter`),
  PS3 save key derivation (`apollo-lib`), and Apollo PS3 save tools and format work followed
  by this port's Steam and PS3 save import code (GPL-3.0). The XIII-2 secure file IDs come
  from the XIII-2 entries in Apollo's `games.conf`.
- **flatz**: `pfdtool`, the PS3 `PARAM.PFD` format, and save-file decryption followed by the
  XIII-2 PS3 save implementation.
- **Surihix / WhiteBinTools**: the file-list and archive cryptography used by the converters
  (GPL-3.0).
- **AMD**: the DXBC checksum code used by the shared renderer, via Xenia.
- **Microsoft**: the GameInput header (MIT), used for the controller's Share button.
- **Sean Barrett**: `stb_image` and `stb_image_write` (public domain / MIT), used by the
  application-icon converter.
- **Baldur Karlsson**: the RenderDoc API header (MIT), used by the shared renderer.
- Downloaded or used by the Windows builder, rather than all being bundled in this repository:
  LLVM, CMake, Ninja, [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) (icon upscaling),
  FFmpeg (Gyan's Windows builds), Python, NumPy, and the ReXGlue SDK's dependencies, including
  SDL3, FFmpeg, Vulkan headers, glslang, and SPIRV-Tools, each under its own license.

Shared components retain their existing attribution. The icon and Xbox extraction tools are adapted from the Lightning Returns builder.
The icon converter uses stb image libraries under their included licenses.

## Licenses

The port's own code: GNU General Public License v3.0 (`LICENSE`). The ReXGlue / Xenia license
notices are in `LICENSES\`; every other third-party component keeps its own license. Game
content remains the property of its respective owners. Final Fantasy XIII-2 is © SQUARE ENIX;
this project is not affiliated with or endorsed by Square Enix or Microsoft.
