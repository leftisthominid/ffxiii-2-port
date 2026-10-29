"""Package tracked source only; refuse to overwrite a committed release."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', type=int, required=True)
    args = parser.parse_args()
    release = ROOT/'releases'/('v'+str(args.version))
    target = release/'XIII-2_Test.zip'
    history = subprocess.check_output(['git','log','-1','--format=%H','--',str(target.relative_to(ROOT))], cwd=ROOT)
    if history.strip():
        raise SystemExit('Release ZIP already exists in commit history; use a new version.')
    launcher = release/('Run-XIII2-v'+str(args.version)+'.ps1')
    if not launcher.is_file():
        raise SystemExit('Create and validate the release launcher first.')
    attributes = release/'.gitattributes'
    attribute_text = '*.ps1 -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n'
    if attributes.exists() and attributes.read_text() not in ('*.ps1 -text\n', attribute_text):
        raise SystemExit('Unexpected release attributes; preserve and review them.')
    attributes.write_text(attribute_text)
    if ('XIII-2 build: test v'+str(args.version)).encode() not in (ROOT/'project/src/ffxiii2_app.h').read_bytes():
        raise SystemExit('Application/source release version mismatch.')
    names = subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    files = {}
    allowed = ('builder/','converters/','project/','scripts/','LICENSES/')
    top = {'.gitattributes','.gitignore','LICENSE','XIII-2_Test.ps1'}
    for name in sorted(n for n in names if n):
        if name not in top and not name.startswith(allowed):
            continue
        # Obsolete Xbox-DLC manifest data is not a dependency of this release.
        if name == 'converters/x2dlc/data/manifests.json.gz':
            continue
        if name.startswith('project/generated/') and name != 'project/generated/rexglue.cmake':
            raise SystemExit('Generated guest code must remain private: '+name)
        if Path(name).suffix.lower() in ('.xex','.xexp','.bik','.pkl','.trb','.imgb','.xfv','.scd','.wpd','.mpk'):
            raise SystemExit('Game media or caches must not be packaged: '+name)
        path = ROOT/name
        if path.is_symlink() or not path.is_file():
            raise SystemExit('Source entry is not a regular file: '+name)
        if any(p in ('__pycache__','out','build','effect-objects','objects') for p in path.relative_to(ROOT).parts):
            raise SystemExit('Generated data must not be packaged: '+name)
        blob = path.read_bytes()
        if path.suffix.lower() == '.ps1':
            if not blob.isascii():
                raise SystemExit('PowerShell must be ASCII: '+name)
            blob = blob.replace(b'\r\n',b'\n').replace(b'\r',b'\n').replace(b'\n',b'\r\n')
        files[name] = blob
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,blob in files.items():
            info = zipfile.ZipInfo('XIII-2_Test/'+name, date_time=(1980,1,1,0,0,0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info,blob,compresslevel=9)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    text = launcher.read_text(encoding='ascii')
    text,count = re.subn(r"(?m)^\$expected = '[^']+'$", "$expected = '"+digest+"'",text)
    if count != 1:
        raise SystemExit('Launcher source checksum field missing or ambiguous.')
    launcher.write_bytes(text.replace('\r\n','\n').replace('\r','\n').replace('\n','\r\n').encode('ascii'))
    manifest = dict(version=args.version,kind='source-candidate',windows_gameplay_validated=False,
                    files={name:hashlib.sha256(blob).hexdigest() for name,blob in files.items()})
    (release/'source-files.json').write_text(json.dumps(manifest,indent=2)+'\n')
    lines = [hashlib.sha256((release/name).read_bytes()).hexdigest()+'  '+name for name in
             ('XIII-2_Test.zip',launcher.name,'source-files.json','.gitattributes')]
    (release/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n')
    print('Packaged',len(files),'source files;',target.stat().st_size,'bytes; SHA256',digest)

if __name__ == '__main__':
    main()
