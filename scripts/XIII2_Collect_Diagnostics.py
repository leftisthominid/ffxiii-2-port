"""Bounded routine diagnostics. Never opens game archives or learning caches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

MIB = 1 << 20
MAX_TOTAL = 48 * MIB
MAX_FILE = 4 * MIB
PART_PAYLOAD = 6 * MIB - 65536

def collect_generated(folder, emit, note, progress=lambda path:None, limit=1<<20):
    """Only selected generated function definitions; never arbitrary user documents."""
    import re
    targets = {'sub_828F0CD0', 'sub_829E8E88', 'sub_829C51E0', 'sub_82B04830', 'sub_82B04E48',
               'sub_828D1010', 'sub_828E3640', 'sub_828CDE18'}
    found = set()
    marker = re.compile(rb'^DEFINE_REX_FUNC\((sub_[0-9A-Fa-f]{8})\)')
    for path in sorted(Path(folder).rglob('*.cpp')):
        progress(path.name)
        name, chunks, size, oversized = None, [], 0, False
        def finish():
            if name and not oversized:
                emit('generated-evidence/'+name+'.cpp', b''.join(chunks))
                found.add(name)
        with path.open('rb') as stream:
            for line in stream:
                match = marker.match(line)
                if match:
                    finish()
                    candidate = match.group(1).decode('ascii')
                    name = candidate if candidate in targets and candidate not in found else None
                    chunks, size, oversized = [], 0, False
                if name:
                    size += len(line)
                    if size > limit:
                        if not oversized: note('Generated function exceeds evidence limit: '+name)
                        oversized = True; chunks.clear()
                    elif not oversized: chunks.append(line)
        finish()
    missing = sorted(targets - found)
    if missing: note('Generated function evidence unavailable: '+', '.join(missing))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--game-output', default=r'C:\FFXIII-2 Test')
    parser.add_argument('--session')
    parser.add_argument('--source')
    parser.add_argument('--steam')
    parser.add_argument('--base-game')
    parser.add_argument('--no-explorer', action='store_true')
    args = parser.parse_args(argv)
    root = Path(args.game_output)
    output = Path(args.out)
    upload = output / 'Upload-these-zips'
    upload.mkdir(parents=True, exist_ok=True)
    if any(upload.iterdir()):
        raise SystemExit('Use a new output folder; existing results are retained.')
    entries, notes, omissions = [], [], []
    total = 0
    part = None
    part_size = 0
    part_number = 0
    def emit(name, data):
        nonlocal total, part, part_size, part_number
        if len(data) > MAX_FILE or total + len(data) > MAX_TOTAL:
            omissions.append(dict(path=name, reason='diagnostic size limit', bytes=len(data)))
            return
        if part is None or part_size + len(data) > PART_PAYLOAD:
            if part is not None: part.close()
            part_number += 1
            part = zipfile.ZipFile(upload / ('XIII-2_Diagnostics_%02d.zip' % part_number), 'w', zipfile.ZIP_STORED)
            part_size = 0
        part.writestr(name, data)
        entries.append(dict(zip=Path(part.filename).name, entry=name, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        total += len(data)
        part_size += len(data)
    def file(path, name, tail=False):
        if path.is_symlink():
            omissions.append(dict(path=name, reason='symbolic link excluded'))
            return
        if not path.is_file(): return
        size = path.stat().st_size
        available = min(MAX_FILE, MAX_TOTAL - total)
        if size > available and not tail:
            omissions.append(dict(path=name, reason='diagnostic size limit', bytes=size))
            return
        if available <= 0:
            omissions.append(dict(path=name, reason='total diagnostic limit', bytes=size))
            return
        with path.open('rb') as stream:
            if size > available:
                stream.seek(size - available)
                omissions.append(dict(path=name, reason='log tail only', original_bytes=size, included_bytes=available))
            emit(name, stream.read(available))
    try:
        if args.session:
            for name in ('steps.txt', 'failure.txt', 'source-version.json'):
                file(Path(args.session)/name, 'session/'+name)
        for name in ('voice.txt','text.txt','aa.txt','fps.txt','story_recaps.txt'):
            file(root/'userdata'/name, 'settings/'+name)
        # Current logs first; at most four, with a four-MiB tail per log.
        logs = sorted((root/'logs').glob('*.log'), key=lambda p:(not p.name.startswith('run'), -p.stat().st_mtime))
        for path in logs[:4]: file(path, 'logs/'+path.name, tail=True)
        mandatory_logs = ('merge_update.log', 'steamdlc_build.log', 'build.log', 'configure.log')
        for path in logs[4:]:
            if path.name not in mandatory_logs:
                omissions.append(dict(path='logs/'+path.name, reason='current log count limit'))
        for name in mandatory_logs:
            path = root/'logs'/name
            if path not in logs[:4]: file(path, 'logs/'+name, tail=True)
        file(root/'work/steamdlc/build_report.json', 'reports/build_report.json')
        for folder in ('update_tu', 'jpvoice', 'update_merged'):
            parent = root/'run'/folder
            for pattern in ('filelist_patch*.bin', 'x2_title_update.txt', 'x2_merged*.txt', 'version.txt', 'jp_lipsync.txt'):
                for path in sorted(parent.glob(pattern)):
                    file(path, 'update-indexes/'+folder+'/'+path.name)
        packages = root/'userdata/0000000000000000/53510806/00000002'
        for path in sorted(packages.glob('*/filelist_p*_u.x360.bin')):
            file(path, 'package-indexes/'+path.parent.name+'/'+path.name)
        collect_generated(root/'project/generated/default', emit, notes.append)
        # No recursive archive, cache, movie or audio reads; no Steam GameIndex.
    finally:
        if part is not None: part.close()
    status = dict(scope='bounded routine diagnostics; no game asset payloads or learning caches',
                  raw_bytes=total, max_raw_bytes=MAX_TOTAL, max_file_bytes=MAX_FILE,
                  parts=part_number, notes=notes, omissions=omissions, files=entries)
    (upload/'collection-status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    (output/'collection-status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    print('Ready: '+str(upload)+'; '+str(part_number)+' ZIPs; '+str(total//1024)+' KiB; bounded to 48 MiB of file data.', flush=True)
    if sys.platform == 'win32' and not args.no_explorer:
        os.startfile(str(upload))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
