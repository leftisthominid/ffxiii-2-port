"""FFXIII-2 port - the downloadable content from the Steam version, converted to the 360 packages.

python -m x2dlc learn   --x360 <360 game folder> --tu <title update folder> --steam <Steam folder> --work <dir>
    learns from the base game (every file in both versions): base_data.pkl in <work>
python -m x2dlc dlcknown --x360 <360 game folder> --tu <title update folder> --steam <Steam folder> --work <dir>
    the members of the DLC's bundles that the base game has in the bundle of the same path, with
    their 360 bytes (all of them; learn keeps 300 MB of these): dlc_known.pkl in <work>
python -m x2dlc build   --steam <Steam folder> --work <dir> --out <dir> [--packs 0000071,...] [--ffmpeg <exe>]
                        [--userdata <dir> --install] [--replace]
    converts the packages of the manifests (data/manifests.json.gz) from the Steam files; with
    --install puts them where the port finds DLC (not over a 360 package with the same number
    unless --replace)
python -m x2dlc compare --steam <Steam folder> --work <dir> --userdata <dir> [--packs ...] [--ffmpeg <exe>]
    for the 360 packages installed in <userdata>: converts each of their files from the Steam
    version and compares it with the real 360 file; report in <work>/compare.txt
python -m x2dlc export  --x360 <360 game folder> --tu <title update folder> --steam <Steam folder> --work <dir>
                        --userdata <dir> --out <dir>
    the material shader programs of both versions (export.py): XIII-2_Shaders.partNN in <out>
The game folders are only read. Progress at least every 10 seconds.
"""
import sys, os, re, time, json, gzip, collections, hashlib
from . import learn, gameindex, convert, package, archive, sound, texture, wpd, listconv

def arg(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default

def say(*a):
    print(*a, flush=True)

def manifests():
    with gzip.open(os.path.join(os.path.dirname(__file__), 'data', 'manifests.json.gz'), 'rt') as f:
        return json.load(f)

def steam_name(path):
    return path.replace('.x360.', '.win32.')

class Ticker:
    def __init__(self, what):
        self.what, self.t0, self.last = what, time.time(), time.time()
    def __call__(self, n, total, item=''):
        if time.time() - self.last >= 10:
            self.last = time.time()
            say(f'  {self.what}: {n} of {total} ({self.last - self.t0:.0f} s) {item}')

# ---- reusing the files of the last build ------------------------------------------------------
# A file's key: the converter code it depends on (by group: the sound files, the models with
# their texture files, everything else), the learned data, and the Steam bytes it is made from
# (with its texture file / the owner of a texture file). The same key again: the file of the
# last build is copied (package.Reuse).
CACHE_VERSION = '2'
SOUND_ROOTS, MODEL_ROOTS = ('sound', 'wpd'), ('model',)
CONVERTER_PARTS = ('__init__', 'file', 'scd', 'oracle', 'vs_of')

def _module_closure(roots):
    here = os.path.dirname(__file__)
    seen, todo = set(), list(roots)
    while todo:
        m = todo.pop()
        if m in seen or not os.path.exists(os.path.join(here, m + '.py')): continue
        seen.add(m)
        src = open(os.path.join(here, m + '.py'), encoding='utf-8').read()
        for a in re.findall(r'from \.(\w+) import', src): todo.append(a)
        for names in re.findall(r'from \. import ([\w, ]+)', src):
            todo += [x.strip() for x in names.split(',') if x.strip()]
    return sorted(seen)

def code_groups(work, ffmpeg):
    """group -> hash of the code and data its files are made with"""
    import inspect
    here = os.path.dirname(__file__)
    def src(mods):
        return b''.join(open(os.path.join(here, m + '.py'), 'rb').read() for m in mods)
    common = hashlib.sha1(CACHE_VERSION.encode())
    for part in CONVERTER_PARTS:
        common.update(inspect.getsource(getattr(convert.Converter, part)).encode())
    common.update(inspect.getsource(convert.kind).encode())
    # (not the build loop here: it decides where files are converted, not what they become)
    for f in ('base_data.pkl', 'dlc_known.pkl'):
        if os.path.exists(os.path.join(work, f)):
            st = os.stat(os.path.join(work, f))
            common.update(f'{f} {st.st_size} {st.st_mtime_ns}'.encode())
    fst = os.stat(ffmpeg) if ffmpeg and os.path.exists(ffmpeg) else None
    # (not the build loop, the package writer or the shader export: where files go, not what they are)
    every = sorted(f[:-3] for f in os.listdir(here) if f.endswith('.py') and f[:-3] not in ('__main__', 'package', 'export'))
    out = {}
    # the models with their texture files: also listconv (what a texture file without headers becomes)
    for g, mods in (('sound', _module_closure(SOUND_ROOTS)), ('model', _module_closure(MODEL_ROOTS) + ['listconv']), ('all', every)):
        h = common.copy(); h.update(src(mods))
        if g != 'model' and fst: h.update(f'ffmpeg {fst.st_size} {fst.st_mtime_ns}'.encode())
        out[g] = h.hexdigest()
    return out

def convert_list(conv, S, files, problems, tick, cache=None, groups=None, spawn=None, listid=None):
    """files: [(code, ftype, 360 path)] -> yields (code, ftype, path, 360 bytes) in list order.
    A .trb / bundle is converted with its .imgb; the .imgb entry takes what that produced.
    cache (package.Reuse): files whose key is the same as in the last build are copied from it.
    spawn (dict work, steam, ffmpeg) with listid (ident, kind): the models and bundles are
    converted by worker processes, each owner with its texture files in one worker; the sound
    files on threads here."""
    lc = listconv.ListConverter(conv, S, files)
    owner_of, companion_of, one, early = lc.owner_of, lc.companion_of, lc.one, lc.early
    keys = {}
    def key_of(path):
        if path not in keys:
            if path.endswith('.imgb'):
                rel = owner_of(path)[0]
                k = convert.kind(rel) if rel else 'imgb'
            else:
                rel = companion_of(path)
                k = convert.kind(path)
            g = 'sound' if k in ('scd', 'soundpack') else 'model' if k in ('trb', 'mpk') else 'all'
            h = hashlib.sha1(groups[g].encode() + path.encode())
            h.update(hashlib.sha1(S.read(gameindex.norm(path)) or b'').digest())
            if rel: h.update(hashlib.sha1(S.read(gameindex.norm(rel)) or b'').digest())
            keys[path] = h.hexdigest()
        return keys[path]
    memo = {}
    def reused(path):
        """the stored file of the last build when the key is the same (asked once per path)"""
        if cache is None: return None
        if path not in memo:
            try:
                memo[path] = cache.get(path, key_of(path))
            except Exception:
                memo[path] = None
        return memo[path]
    # The sound files (ffmpeg re-encodes) run on a thread pool here, a window ahead of the list.
    from concurrent.futures import ThreadPoolExecutor
    tpool = ThreadPoolExecutor(max_workers=max(2, min(16, os.cpu_count() or 4)))
    futures = {}
    order = [f for f in files if f[2].endswith('.scd')]
    nxt = [0]
    def fill(upto):
        while nxt[0] < len(order) and len(futures) < upto:
            p = order[nxt[0]][2]; nxt[0] += 1
            if reused(p) is None: futures[p] = tpool.submit(one, p)
    # The rest on worker processes: work units of an owner and its texture files (in list order),
    # a window of them ahead of the list.
    nw = max(1, min(8, (os.cpu_count() or 2) - 1))
    ppool = bool(spawn and listid and len(files) > 1 and nw > 1)
    pool_box = []
    def worker_pool():
        # (started when there is something to convert: each worker loads the learned data)
        if not pool_box:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor
            pool_box.append(ProcessPoolExecutor(max_workers=nw, mp_context=multiprocessing.get_context('spawn'),
                                                initializer=listconv.worker_init,
                                                initargs=(spawn['work'], spawn['steam'], spawn['ffmpeg'])))
            say(f'  {nw} worker processes')
        return pool_box[0]
    unit_of, units = {}, []
    if ppool:
        # (as in one list order here: a texture file takes what the last file before it that
        # makes it made, else its owner converts with it)
        index, producer = {}, {}
        for _, _, p in files:
            if p.endswith('.scd'): continue
            g = p
            if p.endswith('.imgb'):
                if p in producer: g = producer[p]
                else:
                    o, inside = owner_of(p)
                    if o is not None and inside and not o.endswith('.scd'): g = o
            else:
                c = companion_of(p)
                if c: producer[c] = p
            if g in unit_of: g = units[unit_of[g]][0] if units[unit_of[g]] else g
            if g not in index:
                index[g] = len(units); units.append([])
            units[index[g]].append(p); unit_of[p] = index[g]
            index.setdefault(p, index[g])
    ufut, unext, uresults = {}, [0], {}
    def ufill(upto):
        while unext[0] < len(units) and len(ufut) < upto:
            u = unext[0]; unext[0] += 1
            todo = [p for p in units[u] if reused(p) is None]
            if todo: ufut[u] = worker_pool().submit(listconv.worker_run, listid[0], listid[1], todo)
    def from_unit(path):
        u = unit_of[path]
        if u not in uresults:
            while u not in ufut and unext[0] <= u: ufill(len(ufut) + 1)
            if u not in ufut: return None
            out, added = ufut.pop(u).result()
            for k, v in added.items(): conv.stats[k] = conv.stats.get(k, 0) + v
            got = {}
            for p, d, why in out: got.setdefault(p, []).append((d, why))   # (a file listed twice: twice)
            uresults[u] = got
        got = uresults[u]
        res = got[path].pop(0) if got.get(path) else None
        if path in got and not got[path]: del got[path]
        if not got: del uresults[u]
        return res
    try:
        for n, (code, ftype, path) in enumerate(files):
            tick(n, len(files), path)
            fill(64)
            if ppool: ufill(nw * 3 + len(uresults))
            r = reused(path) if path not in futures else None
            memo.pop(path, None)
            if r is not None:
                early.pop(path, None)
                yield code, ftype, path, r
                continue
            try:
                if path in futures:
                    data = futures.pop(path).result()
                elif ppool and path in unit_of:
                    res = from_unit(path)
                    if res is None: data = one(path)
                    else:
                        data, why = res
                        if why: raise RuntimeError(why)
                else:
                    data = one(path)
                if cache is not None and data: cache.put(path, key_of(path))
            except Exception as ex:
                why = str(ex) if isinstance(ex, RuntimeError) and ppool and path in unit_of else f'{type(ex).__name__}: {ex}'
                problems.append((path, why))
                data = b''
            yield code, ftype, path, data
    finally:
        tpool.shutdown(wait=False, cancel_futures=True)
        if pool_box: pool_box[0].shutdown(wait=True, cancel_futures=True)

def cmd_learn():
    learn.learn(arg('--x360'), arg('--tu'), arg('--steam'), arg('--work'), log=say)

def cmd_dlcknown():
    learn.dlc_known(arg('--x360'), arg('--tu'), arg('--steam'), arg('--work'), log=say)

def open_steam():
    st = arg('--steam')
    if os.path.isdir(os.path.join(st, 'alba_data')):
        return gameindex.GameIndex(st, 'win32', log=say)
    return gameindex.SampleIndex(st)       # a folder of loose Steam files (the DLC sample)

def cmd_build():
    work, out = arg('--work'), arg('--out')
    data = learn.load(work)
    S = open_steam()
    say(f'Steam version: {len(S.files)} files')
    conv = convert.Converter(data, log=say, ffmpeg=arg('--ffmpeg'))
    conv.read_pc = S.read
    mf = manifests()
    packs = arg('--packs', ','.join(sorted(mf))).split(',')
    userdata = arg('--userdata')
    have = package.installed(userdata) if userdata else {}
    report = []
    missing_movies = set()
    for ident in packs:
        m = mf[ident]
        name = f'XIII2SteamDLC{ident}'
        if '--install' in sys.argv and ident in have and have[ident] != name and '--replace' not in sys.argv:
            say(f'{ident} {m["name"]}: the 360 package is installed ({have[ident]}); not converted')
            continue
        say(f'{ident} {m["name"]}: converting ' + ', '.join(f'{len(l["files"])} {l["kind"]} files' for l in m['lists']))
        problems = []
        dest = os.path.join(out, name)
        # the new build next to the last one (whose files are reused where nothing changed), then
        # in its place
        new = dest + '.new'
        import shutil
        shutil.rmtree(new, ignore_errors=True)
        groups = code_groups(work, arg('--ffmpeg'))
        # a last build from before the keys: its sound files were made by the same sound code
        # (sound.py unchanged since), so those are kept once
        boot = (lambda p: p.endswith('.scd') or (p.endswith('.wpd') and p.startswith('sound/'))) if '--reuse-sound' in sys.argv else None
        for l in m['lists']:
            files = [tuple(f) for f in l['files']]
            # (--rebuild: nothing reused; the keys are written all the same)
            cache = package.Reuse(new if '--rebuild' in sys.argv else dest, ident, l['kind'], bootstrap=boot)
            package.write(new, ident, l['kind'], bytes.fromhex(l['header']),
                          convert_list(conv, S, files, problems, Ticker(f'{ident} {l["kind"]}'), cache=cache, groups=groups,
                                       spawn=None if '--serial' in sys.argv else dict(work=work, steam=arg('--steam'), ffmpeg=arg('--ffmpeg')),
                                       listid=(ident, l['kind'])))
            cache.close(); cache.save(new)
            say(f'  {ident} {l["kind"]}: {cache.reused} of {len(files)} files unchanged since the last build (reused), {len(files) - cache.reused} converted')
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(new, dest)
        say(f'  {ident}: {len(problems)} file(s) not converted' + ''.join(f'\n    {p}: {why}' for p, why in problems[:20]))
        report.append(dict(pack=ident, name=m['name'], problems=problems))
        # a file that did not convert would be an empty file in the package: not installed then
        # (the movies are not in the Steam version: those stay empty, see README.txt)
        serious = [p for p, why in problems if not p.endswith('.bik')]
        if '--install' in sys.argv and userdata and serious and '--force' not in sys.argv:
            say(f'  {ident}: NOT installed ({len(serious)} file(s) did not convert; --force installs it anyway)')
            continue
        if '--install' in sys.argv and userdata:
            d = package.install(dest, userdata, name, m['name'], log=say)
            say(f'  installed: {d}')
            # the movies the Steam version does not have (empty in the package): the port runs
            # their cutscene clips without them (x2_movies.cpp)
            for p, why in problems:
                mm = re.match(r'movie/(.+?)(_[a-z]{2})?\.bik$', p)
                if mm: missing_movies.add(mm.group(1))
    if '--install' in sys.argv and userdata:
        mp = os.path.join(userdata, 'x2_missing_movies.txt')
        if missing_movies:
            with open(mp, 'w') as f: f.write(''.join(n + '\n' for n in sorted(missing_movies)))
            say(f'  movies not in the Steam version (their cutscenes go on without them): {", ".join(sorted(missing_movies))}')
        elif os.path.exists(mp):
            os.remove(mp)
    say('how the files were made: ' + ', '.join(f'{k} {v}' for k, v in sorted(conv.stats.items())))
    say('shaders: ' + ', '.join(f'{k} {v}' for k, v in sorted(conv.ctx.stats.items())))
    if sound.LOOPED: say(f'looped sounds re-encoded (loop over the whole sound): {len(sound.LOOPED)}')
    json.dump(dict(report=report, stats=conv.stats), open(os.path.join(work, 'build_report.json'), 'w'), indent=1)

def resource_diffs(ours, real):
    """per resource of two SEDBRES files (.trb / .mpk): the ones that differ, for the report"""
    from . import trb
    try: a, b = trb.Trb(ours), trb.Trb(real)
    except Exception as ex: return [f'    (not compared by resource: {type(ex).__name__}: {ex})']
    out = []
    if [a.type_of(i) for i in range(len(a.res))] != [b.type_of(i) for i in range(len(b.res))]:
        out.append(f'    resource types differ: ours {[a.type_of(i) for i in range(len(a.res))]} 360 {[b.type_of(i) for i in range(len(b.res))]}')
    names = b.name_map()
    for i, (x, y) in enumerate(zip(a.res, b.res)):
        if x == y: continue
        nd = sum(1 for p, q in zip(x, y) if p != q)
        first = next((j for j in range(min(len(x), len(y))) if x[j] != y[j]), min(len(x), len(y)))
        out.append(f'    {i} {b.type_of(i)} {names.get(b.index[i], "")}: {len(x)} bytes (360 {len(y)}), {nd} differ, first at {first:#x}')
    return out

def fixup_diffs(ours, real):
    """the SEDB members (the file, or the members of a bundle) whose words the game's pointer
    relocation reads differ from the real 360 ones: [(member, what)]"""
    from . import qix
    def members(d):
        if d[:4] == b'WPD\0':
            try: return {n.rstrip(b'\0').decode('latin1') + '.' + e.rstrip(b'\0').decode('latin1'): x for n, e, x in wpd.read(d)}
            except Exception: return {}
        return {'': d}
    a, b = members(ours), members(real)
    out = []
    for name, x in b.items():
        y = a.get(name)
        if y is None or x[:4] != b'SEDB' or len(x) < 0x50: continue      # (byte 0xC: 1 on most 360 members, 0 on lyb)
        if x[4:8] == b'SSCF': continue            # sound: another layout
        fx = qix.game_fixups(x)
        if isinstance(fx, str): continue          # not this format (the real file does not parse either)
        fy = qix.game_fixups(y)
        if fy == fx: continue
        if isinstance(fy, str): out.append((name, f'ours: the game would fail ({fy})')); continue
        # type / block entries wrong: the game walks wrong data (the crash kind); a pointer value
        # alone can differ where a part before it has another size (an effect model's shaders)
        wrong = [(at, v, w) for (at, v), (_, w) in zip(fx, fy) if v != w]
        tab = [x for x in wrong if isinstance(x[1], tuple)]
        what = (f'{len(tab)} table entries and {len(wrong) - len(tab)} pointers differ' if len(fx) == len(fy)
                else f'{len(fy)} words read (360 {len(fx)})')
        first = (tab or wrong or [None])[0]
        if first: what += ', first at %#x: ours %s, 360 %s' % (first[0], first[2], first[1])
        if tab or len(fx) != len(fy) or len(wrong) * 4 > len(fx): out.append((name, what))
    return out

def schedule_diffs(ours, real):
    """the cutscene schedules ('!!cutsch' scb members) of an event bundle against the real ones:
    [(member, what)] - the chunk tree, the timeline record headers, and the bytes"""
    from . import scb
    import struct as _st
    if real[:4] != b'WPD\0': return []
    try:
        a = {n: (e, x) for n, e, x in wpd.read(ours)}
        b = {n: (e, x) for n, e, x in wpd.read(real)}
    except Exception: return []
    out = []
    for n, (e, x) in b.items():
        if e.rstrip(b'\0') != b'scb' or n not in a: continue
        y = a[n][1]
        hx, _, _ = scb.chunks(x, '>'); hy, _, _ = scb.chunks(y, '>')
        tot = bad = 0
        for i, t in hx:
            if t != 'CBLK': continue
            sz = _st.unpack_from('>H', x, i + 6)[0] * 16
            for j, m in scb.records(x, i, sz, '>'):
                tot += 1; bad += y[j:j + 16] != x[j:j + 16]
        nd = sum(1 for u, v in zip(x, y) if u != v) + abs(len(x) - len(y))
        out.append((n.rstrip(b'\0').decode('latin1'), f'{nd} of {len(x)} bytes differ, chunk tree {"same" if hx == hy else "DIFFERENT"}, record headers wrong {bad} of {tot}'))
    return out

def cmd_compare():
    work, userdata = arg('--work'), arg('--userdata')
    data = learn.load(work)
    S = open_steam()
    conv = convert.Converter(data, log=say, ffmpeg=arg('--ffmpeg'))
    conv.read_pc = S.read
    mf = manifests()
    # the real 360 packages: installed, or moved aside by an install of the converted ones
    root = os.path.join(userdata, '0000000000000000', '53510806', '00000002')
    pkgdirs = {i: os.path.join(root, n) for i, n in package.installed(userdata).items() if not n.startswith('XIII2SteamDLC')}
    moved = os.path.join(work, 'moved_360_packages.txt')
    if os.path.exists(moved):
        for line in open(moved, encoding='latin1').read().splitlines():
            name, _, bak = line.partition('\t')
            ip = os.path.join(bak, name, 'identifier')
            if bak and os.path.exists(ip):
                pkgdirs.setdefault(open(ip, 'rb').read()[:7].decode('latin1'), os.path.join(bak, name))
    packs = arg('--packs', ','.join(sorted(mf))).split(',')
    # --only <prefix,prefix>: just the files under these paths, every one saved for the report
    only = [x for x in arg('--only', '').split(',') if x]
    res = collections.defaultdict(collections.Counter)
    lines = []
    saved, saved_bytes = collections.Counter(), [0]
    import shutil
    shutil.rmtree(os.path.join(work, 'report_files'), ignore_errors=True)
    for ident in packs:
        if ident not in pkgdirs or ident not in mf:
            say(f'{ident}: no 360 package installed to compare with'); continue
        pdir = pkgdirs[ident]
        say(f'{ident}: comparing with {pdir}')
        for l in mf[ident]['lists']:
            fl = os.path.join(pdir, f'filelist_p{ident}{l["kind"]}_u.x360.bin')
            wh = os.path.join(pdir, f'white_p{ident}{l["kind"]}_u.x360.bin')
            if not os.path.exists(fl): continue
            hdr, ents = archive.read_list(fl)
            real = {}
            f = open(wh, 'rb')
            def read_real(e):
                code, ftype, pos, un, cm, path = e
                f.seek(pos * 2048); d = f.read(cm)
                if cm != un:
                    import zlib; d = zlib.decompress(d)
                return d
            byp = {e[5]: e for e in ents}
            problems = []
            files = [tuple(x) for x in l['files']]
            if only: files = [x for x in files if x[2].startswith(tuple(only))]
            # re-encoded sound never matches byte for byte: the first 100 sound files are converted
            # (to check that they convert), the rest left out of the comparison
            nsound = 0; kept = []
            for x in files:
                if x[2].endswith('.scd'):
                    nsound += 1
                    if nsound > 100: res['scd scd']['left out (sound)'] += 1; continue
                kept.append(x)
            files = kept
            for code, ftype, path, d in convert_list(conv, S, files, problems, Ticker(f'{ident} {l["kind"]}')):
                k = convert.kind(path) + ' ' + (path.rsplit('.', 1)[-1] if '.' in path else '')
                if path not in byp: res[k]['not in the 360 package'] += 1; continue
                if any(p == path for p, _ in problems):
                    res[k]['not converted'] += 1; continue
                r = read_real(byp[path])
                if d[:4] == b'WPD\0' and r[:4] == b'WPD\0':
                    # the layout / texture header members, one line each (the Sazh casino crash)
                    try:
                        mo = {n: x for n, e, x in wpd.read(d)}
                        for n, e, x in wpd.read(r):
                            if e.rstrip(b'\0') in (b'lyb', b'txbh') and n in mo:
                                nd2 = sum(1 for u, v in zip(mo[n], x) if u != v) + abs(len(mo[n]) - len(x))
                                lines.append(f'MEMBER {path} {n.rstrip(chr(0).encode()).decode("latin1")}.{e.rstrip(chr(0).encode()).decode()}: {nd2} of {len(x)} bytes differ')
                    except Exception as ex:
                        lines.append(f'  (members of {path} not compared: {ex})')
                for mem, what in schedule_diffs(d, r):
                    res[k]['schedules'] += 1
                    if 'DIFFERENT' in what or 'wrong 0 of' not in what: res[k]['schedules with structure wrong'] += 1
                    lines.append(f'SCHEDULE {path} {mem}: {what}')
                for mem, what in fixup_diffs(d, r):
                    res[k]['relocation differs'] += 1
                    if res[k]['relocation differs'] <= 200: lines.append(f'RELOCATION {path} {mem}: {what}')
                if d == r: res[k]['exact'] += 1
                else:
                    res[k]['differs'] += 1
                    nd = sum(1 for a, b in zip(d, r) if a != b) + abs(len(d) - len(r))
                    res[k]['differing bytes'] += nd; res[k]['bytes'] += len(r)
                    if res[k]['differs'] <= 40: lines.append(f'{path}: {nd} of {len(r)} bytes differ (ours {len(d)})')
                    # a few examples of each kind for the report (ours, the real 360 file, the PC file)
                    if only or (k.startswith(('trb', 'mpk')) and res[k]['differs'] <= 40): lines += resource_diffs(d, r)
                    if (only and saved_bytes[0] < (400 << 20)) or (saved[k] < 3 and len(r) <= (4 << 20) and saved_bytes[0] < (40 << 20)):
                        pc = S.read(gameindex.norm(path)) or b''
                        dd = os.path.join(work, 'report_files', k.replace(' ', '_'))
                        os.makedirs(dd, exist_ok=True)
                        base = os.path.join(dd, path.replace('/', '__'))
                        for suf, b in (('.ours', d), ('.x360', r), ('.pc', pc)):
                            with open(base + suf, 'wb') as fo: fo.write(b)
                        saved[k] += 1; saved_bytes[0] += len(d) + len(r) + len(pc)
            seen_why = collections.Counter()
            for p, why in problems:
                w = why.split('[')[0] if why.startswith('MissingShaders') else why
                seen_why[w] += 1
                if seen_why[w] <= 10: lines.append(f'{p}: {why}')
                # files that did not convert: the real 360 file and the PC file, a few per reason
                # (to work out their format from the report)
                if seen_why[w] <= 4 and p in byp and saved_bytes[0] < (48 << 20) and not p.endswith(('.bik', '.scd')):
                    try:
                        r = read_real(byp[p]); pc = S.read(gameindex.norm(p)) or b''
                        if len(r) + len(pc) <= (6 << 20):
                            dd = os.path.join(work, 'report_files', 'not_converted')
                            os.makedirs(dd, exist_ok=True)
                            base = os.path.join(dd, p.replace('/', '__'))
                            for suf, b in (('.x360', r), ('.pc', pc)):
                                with open(base + suf, 'wb') as fo: fo.write(b)
                            saved_bytes[0] += len(r) + len(pc)
                    except Exception as ex:
                        lines.append(f'  (could not save {p}: {ex})')
            f.close()
            lines += [f'({n - 10} more: {w})' for w, n in seen_why.items() if n > 10]
    out = os.path.join(work, 'compare.txt')
    with open(out, 'w') as fo:
        for k, c in sorted(res.items()):
            fo.write(f'{k:24} ' + ', '.join(f'{a} {b}' for a, b in sorted(c.items())) + '\n')
        fo.write('\n' + '\n'.join(lines) + '\n')
    say(open(out).read()[:6000])
    say('how the files were made: ' + ', '.join(f'{k} {v}' for k, v in sorted(conv.stats.items())))
    say('shaders: ' + ', '.join(f'{k} {v}' for k, v in sorted(conv.ctx.stats.items())))

def cmd_export():
    from . import export
    export.export(arg('--x360'), arg('--tu'), arg('--steam'), arg('--work'), arg('--userdata'), arg('--out'), log=say)

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'learn': cmd_learn()
    elif cmd == 'dlcknown': cmd_dlcknown()
    elif cmd == 'export': cmd_export()
    elif cmd == 'build': cmd_build()
    elif cmd == 'compare': cmd_compare()
    else:
        print(__doc__); sys.exit(1)

if __name__ == '__main__':
    main()
