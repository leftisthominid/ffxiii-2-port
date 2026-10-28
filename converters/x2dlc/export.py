# Material shader programs of the base game (and of the 360 DLC packages installed here) with their
# Steam (PC) counterparts, for working out the PC -> 360 shader compilation offline:
#   records (source, name, PC kind, 360 kind, PC program, 360 program), one per distinct PC program,
#   plus a few learned member pairs per kind (base_data.pkl), lzma-compressed and split into parts
#   of at most 24 MB (XIII-2_Shaders.partNN). The game folders are only read.
import os, time, pickle, lzma, hashlib, collections
from . import gameindex, trb, shader, wpd, vmdl, archive

PART = 24 << 20
MAX_RAW = 600 << 20

def _programs(pc_res, x_res):
    a = [e for e in shader.chunks(pc_res) if e['tag'] == b'FILE']
    b = [e for e in shader.chunks(x_res) if e['tag'] == b'FILE']
    if [shader.x360_file_name(e['name']) for e in a] != [e['name'] for e in b]:
        return []
    return [(e['name'], e['kind'], f['kind'], e['prog'], f['prog']) for e, f in zip(a, b)]

def pairs_of(path, p, x):
    """(name, pc kind, 360 kind, pc program, 360 program) of a file pair"""
    out = []
    ext = path.rsplit('.', 1)[-1]
    if ext in ('trb', 'mpk'):
        tp, tx = trb.Trb(p), trb.Trb(x)
        if len(tp.res) != len(tx.res): return out
        for rp, rx in zip(tp.res, tx.res):
            if rp[:7] == b'SEDBshd' and rx[:7] == b'SEDBshd':
                out += _programs(rp, rx)
    elif p[:4] == b'WPD\0' and x[:4] == b'WPD\0':
        ep, ex = wpd.read(p), wpd.read(x)
        if [(n, e) for n, e, _ in ep] != [(n, e) for n, e, _ in ex]: return out
        for (name, e, a), (_, _, b) in zip(ep, ex):
            if e.rstrip(b'\0') != b'vmdl' or a == b: continue
            ba, bb = vmdl.blocks(a), vmdl.blocks(b, True)
            if not ba or not bb or len(ba) != len(bb): continue
            for (_, ta, sa), (_, tb, sb) in zip(ba, bb):
                out.append((name.rstrip(b'\0').decode('latin1') + '.vmdl', int.from_bytes(a[ta + 20:ta + 24], 'big'),
                            int.from_bytes(b[tb + 20:tb + 24], 'big'), a[ta + 0x20:ta + 0x20 + sa], b[tb + 0x20:tb + 0x20 + sb]))
    return out

def export(x360_root, tu_dir, steam_root, work, userdata, out_dir, log=print):
    t0 = last = time.time()
    recs, seen = [], {}
    raw = [0]
    st = collections.Counter()
    def add(src, path, p, x):
        try:
            got = pairs_of(path, p, x)
        except Exception:
            st['unreadable'] += 1; return
        for name, kp, kx, a, b in got:
            k = hashlib.md5(a).digest()
            if k in seen:
                if seen[k] != hashlib.md5(b).digest(): st['same PC program, other 360 program'] += 1
                continue
            if raw[0] + len(a) + len(b) > MAX_RAW: st['left out (size)'] += 1; continue
            seen[k] = hashlib.md5(b).digest()
            recs.append((src, path + ':' + name, kp, kx, a, b)); raw[0] += len(a) + len(b)
            st[src] += 1
    S = gameindex.GameIndex(steam_root, 'win32', log=log)
    # the 360 DLC packages installed here first (the programs the DLC needs)
    root = os.path.join(userdata, '0000000000000000', '53510806', '00000002')
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if d.startswith('XIII2SteamDLC'): continue
        for fn in sorted(os.listdir(os.path.join(root, d))):
            if not (fn.startswith('filelist_p') and fn.endswith('.x360.bin')): continue
            wh = os.path.join(root, d, 'white_' + fn[len('filelist_'):])
            if not os.path.exists(wh): continue
            hdr, ents = archive.read_list(os.path.join(root, d, fn))
            with open(wh, 'rb') as f:
                for code, ftype, pos, un, cm, path in ents:
                    if path.rsplit('.', 1)[-1] not in ('trb', 'mpk', 'xfv', 'xwb', 'xgr', 'bin'): continue
                    if time.time() - last >= 10:
                        last = time.time(); log(f'  DLC {d}: {len(recs)} programs ({last - t0:.0f} s)')
                    f.seek(pos * 2048); x = f.read(cm)
                    if cm != un:
                        import zlib
                        try: x = zlib.decompress(x)
                        except zlib.error: continue
                    p = S.read(gameindex.norm(path))
                    if p is None or p == x: continue
                    add('dlc', path, p, x)
    log(f'360 DLC: {st["dlc"]} programs')
    X = gameindex.GameIndex(x360_root, 'x360', update=tu_dir, log=log)
    keys = sorted(k for k in set(X.files) & set(S.files) if k.rsplit('.', 1)[-1] in ('trb', 'mpk', 'xfv', 'xwb', 'xgr', 'bin'))
    for n, k in enumerate(keys):
        if time.time() - last >= 10:
            last = time.time(); log(f'  base game: {n} of {len(keys)} files, {len(recs)} programs ({last - t0:.0f} s)')
        p, x = S.read(k), X.read(k)
        if p is None or x is None or p == x: continue
        add('base', k, p, x)
    X.close(); S.close()
    # a few learned member pairs per kind (the effect / motion layouts)
    members = {}
    try:
        with open(os.path.join(work, 'base_data.pkl'), 'rb') as f:
            pairs = pickle.load(f).get('pairs', {})
        for kind, lst in pairs.items():
            keep, size = [], 0
            for a, b in lst:
                if len(keep) >= 80 or size + len(a) > (3 << 20): break
                keep.append((a, b)); size += len(a)
            members[kind] = keep
    except Exception as ex:
        log(f'  member pairs left out: {ex}')
    log(f'programs: {len(recs)} ({raw[0] >> 20} MB), counts: {dict(st)}; compressing')
    blob = lzma.compress(pickle.dumps(dict(records=recs, members=members, stats=dict(st)), protocol=4),
                         preset=6)
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith('XIII-2_Shaders.part'): os.remove(os.path.join(out_dir, f))
    parts = []
    for i in range(0, len(blob), PART):
        fn = os.path.join(out_dir, f'XIII-2_Shaders.part{len(parts) + 1:02d}.bin')
        with open(fn, 'wb') as f: f.write(blob[i:i + PART])
        parts.append(fn)
    log(f'shader export: {len(blob) >> 20} MB in {len(parts)} part(s) ({time.time() - t0:.0f} s)')
    return parts
