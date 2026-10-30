"""Package the public builder's tracked source; never include uploaded game inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_NAME = 'Final-Fantasy-XIII-2-Builder'

def package(destination):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    exact = {'Build Final Fantasy XIII-2.bat', 'builder.ps1', 'README.md', 'LICENSE',
             'scripts/X2_Import_360_Content.ps1'}
    prefixes = ('builder/', 'project/', 'converters/', 'LICENSES/')
    forbidden = {'.xex', '.xexp', '.bik', '.pkl', '.trb', '.imgb', '.xfv', '.scd', '.wpd', '.mpk', '.zip'}
    files = {}
    for name in sorted(filter(None, tracked)):
        if name not in exact and not name.startswith(prefixes):
            continue
        if name == 'builder/x2_runlogs.ps1' or name == 'converters/x2dlc/data/manifests.json.gz':
            continue
        if name.startswith('project/generated/') and name != 'project/generated/rexglue.cmake':
            raise ValueError('Generated guest code must stay private: ' + name)
        path = ROOT / name
        if path.suffix.lower() in forbidden or any(p in path.parts for p in ('__pycache__', 'objects', 'build', 'out')):
            raise ValueError('Game data or generated output in source selection: ' + name)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Not a regular source file: ' + name)
        blob = path.read_bytes()
        if path.suffix.lower() in ('.ps1', '.bat'):
            if not blob.isascii():
                raise ValueError('Windows scripts must be ASCII: ' + name)
            blob = blob.replace(b'\r\n', b'\n').replace(b'\r', b'\n').replace(b'\n', b'\r\n')
        files[name] = blob
    missing = exact - files.keys()
    if missing:
        raise ValueError('Required sources are not tracked: ' + ', '.join(sorted(missing)))
    manifest = {name: {'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}
                for name, blob in files.items()}
    files['source-files.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Refusing to replace an existing builder package: ' + str(destination))
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, blob in files.items():
            info = zipfile.ZipInfo(ROOT_NAME + '/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, blob, compresslevel=9)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_name(destination.name + '.sha256').write_text(digest + '  ' + destination.name + '\n')
    print(f'{len(manifest)} source files; SHA256 {digest}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    package(parser.parse_args().out)
