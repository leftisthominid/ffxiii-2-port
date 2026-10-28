# Finds every movie in a FFXIII-2 Steam folder, whatever its name or container:
#  1. every file list (filelist*.bin / movielist*.bin anywhere): each entry's name, and its first
#     bytes (decompressed if stored compressed) checked for a movie header;
#  2. every file, every 2048-byte sector: movie headers (Bink 1 / 2, WMV, MP4, USM, WebM).
# Report: <out>\XIII-2_Movie_Search.txt. Progress every 15 seconds.
import os, sys, time, zlib, struct
steam, conv, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, conv)
from x2dlc import archive
WANT = ('tevb', 'vazb', 'cszb', 'clzb')
TARGETS = {'tm_tevb_050': 55, 'tm_vazb_013': 20, 'tm_vazb_038': 15, 'em_vazb_030': 60, 'em_vazb_055': 48}
lines = []
def say(s=''):
    print(s, flush=True); lines.append(s)
last = [time.time()]
def tick(msg):
    if time.time() - last[0] >= 15:
        last[0] = time.time(); print('  ... ' + msg, flush=True)
def movie_kind(h):
    if h[:3] == b'BIK': return 'Bink 1 (%s)' % chr(h[3])
    if h[:3] == b'KB2': return 'Bink 2 (%s)' % chr(h[3])
    if h[:4] == b'\x30\x26\xb2\x75': return 'WMV/ASF'
    if h[4:8] == b'ftyp': return 'MP4'
    if h[:4] == b'CRID': return 'USM'
    if h[:4] == b'\x1aE\xdf\xa3': return 'WebM/MKV'
    if h[:4] == b'RIFF' and h[8:12] == b'AVI ': return 'AVI'
    return None
def bink_info(h):
    if h[:3] not in (b'BIK', b'KB2') or len(h) < 44: return ''
    frames, w, ht, num, den = struct.unpack_from('<I', h, 8)[0], *struct.unpack_from('<IIII', h, 20)
    secs = frames * den / num if num else 0
    return f'{w}x{ht}, {frames} frames, {num}/{den} fps, {secs:.1f} s'
files = []
for d, _, fs in os.walk(steam):
    for f in fs:
        p = os.path.join(d, f)
        try: files.append((p, os.path.getsize(p)))
        except OSError: pass
total = sum(s for _, s in files)
say(f'Steam folder: {steam}')
say(f'{len(files)} files, {total / 2**30:.1f} GB')
say('')
say('== Files whose names mention the DLC scenes or are movies ==')
for p, s in files:
    n = os.path.basename(p).lower()
    if any(w in n for w in WANT) or n.endswith(('.bik', '.bk2', '.wmv', '.usm', '.mp4', '.webm', '.avi')):
        say(f'  {os.path.relpath(p, steam)}  ({s:,} bytes)')
say('')
say('== File lists: every entry named like the DLC scenes or a movie, and every entry that starts like a movie ==')
lists = [(p, s) for p, s in files if os.path.basename(p).lower().startswith(('filelist', 'movielist')) and p.lower().endswith('.bin')]
def white_for(fl):
    d, n = os.path.split(fl)
    cands = [n.replace('filelist', 'white'), n.replace('filelist', 'white_img'), n.replace('filelist', 'white_') ]
    if n.startswith('movielist'):
        cands = [os.path.join('..', 'movie', 'movie' + n[len('movielist'):])]
    for c in cands:
        q = os.path.normpath(os.path.join(d, c))
        if os.path.exists(q) and q != fl: return q
    return None
names_total = 0
for fl, _ in lists:
    try:
        hdr, ents = archive.read_list(fl)
    except Exception as ex:
        say(f'  {os.path.relpath(fl, steam)}: not readable as a file list ({type(ex).__name__})'); continue
    wh = white_for(fl)
    say(f'  {os.path.relpath(fl, steam)}: {len(ents)} entries, archive {os.path.relpath(wh, steam) if wh else "(not found)"}')
    names_total += len(ents)
    fh = open(wh, 'rb') if wh else None
    for k, (code, ftype, pos, un, cm, path) in enumerate(ents):
        tick(f'{os.path.basename(fl)} {k} of {len(ents)}')
        pl = path.lower()
        named = any(w in pl for w in WANT) or pl.endswith(('.bik', '.bk2', '.wmv', '.usm', '.mp4', '.webm'))
        kind = info = ''
        if fh and cm:
            try:
                fh.seek(pos * 2048); raw = fh.read(min(cm, 65536))
                h = raw
                if cm != un:
                    try: h = zlib.decompressobj().decompress(raw, 64)
                    except Exception: h = raw
                kind = movie_kind(h) or ''
                info = bink_info(h)
            except Exception:
                pass
        if named or kind:
            say(f'    {path}  size {un:,}{"  " + kind if kind else ""}{"  " + info if info else ""}')
    if fh: fh.close()
say('')
say('== Movie headers anywhere (every 2048-byte sector of every file) ==')
done = 0
found = 0
for p, s in files:
    if s < 4096: done += s; continue
    try:
        with open(p, 'rb') as f:
            off = 0
            while True:
                buf = f.read(8 << 20)
                if not buf: break
                for i in range(0, len(buf) - 64, 2048):
                    h = buf[i:i + 64]
                    k = movie_kind(h)
                    if k:
                        found += 1
                        say(f'  {os.path.relpath(p, steam)} at {off + i:#x}: {k}  {bink_info(h)}')
                off += len(buf); done += len(buf)
                tick(f'{done / 2**30:.1f} of {total / 2**30:.1f} GB scanned, {found} movie headers so far ({os.path.basename(p)})')
    except OSError as ex:
        say(f'  {os.path.relpath(p, steam)}: not readable ({ex})')
say(f'  {found} movie headers in all')
say('')
say('Looking for (lengths of the 360 movies): ' + ', '.join(f'{k} {v} s' for k, v in TARGETS.items()))
rep = os.path.join(out, 'XIII-2_Movie_Search.txt')
open(rep, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print(f'Report: {rep}')
