# Learning from the base game: every file that is in both the 360 game (with its title update)
# and the Steam version is a pair (PC bytes, 360 bytes). From the pairs:
#   shaders  PC program md5 -> (360 program, kind, name)                (model / material files)
#   known    md5 of PC bytes -> 360 bytes, for files and bundle members that differ between the
#            platforms (exact: a DLC file or member that is also in the base game)
#   pairs    per member kind, (PC, 360) examples for the learned byte-order conversion
#   same     per member kind, how often PC and 360 bytes were the same / different
# Saved as base_data.pkl in the work folder. Progress at least every 10 seconds.
import os, time, pickle, hashlib, collections
from . import gameindex, trb, shader, wpd, sound, vmdl, mesh, skl, xengine, qix

PAIR_KINDS = ('trb', 'mpk', 'xfv', 'xwb', 'xwp', 'xgr', 'scd', 'wpd', 'vinsbin', 'bin')
MAX_PAIRS = 400            # per member kind
MAX_PAIR_BYTES = 24 << 20  # per member kind
MAX_KNOWN_BYTES = 300 << 20
MCB_BUNDLES = 160          # base-game motion bundles read for their motion blocks (spread over mot/)
MCB_PAIRS = 8000
MCB_BYTES = 16 << 20

def md5(b):
    return hashlib.md5(b).digest()

def bone_rule_probe(key, tp, tx, S, X, rules, log):
    """Models whose skeleton has 256 joints or more (blend indices are 8-bit on the 360): which
    bone-index rule (mesh.RULES) reproduces the 360 mesh and vertex data. -> True when probed."""
    types = [tp.type_of(i) for i in range(len(tp.res))]
    if 'skl' not in types or 'wrb' not in types: return False
    joints = skl.joints(tp.res[types.index('skl')])
    if len(joints) < 256: return False
    wi = types.index('wrb')
    xi = tx.res[wi]
    ximg = X.read(key[:-3] + 'imgb') or b''
    hit = []
    par = skl.parents(tp.res[types.index('skl')])
    for rule in mesh.RULES + ('global_parent',):
        try:
            if rule == 'global_parent': w, vb = mesh.convert(tp.res[wi], joints, 0, parents=par, rule='global')
            else: w, vb = mesh.convert(tp.res[wi], joints, 0, rule=rule)
        except Exception:
            continue
        if w == xi and ximg[:len(vb)] == vb: hit.append(rule)
    rules[(len(joints) >= 256, tuple(hit) or ('none',))] += 1
    if not hit and sum(n for (big, h), n in rules.items() if h == ('none',)) <= 3:
        log(f'  bones: {key} ({len(joints)} joints): no rule gives the 360 mesh')
    return True

def learn(x360_root, tu_dir, steam_root, work, log=print):
    t0 = last = time.time()
    os.makedirs(work, exist_ok=True)
    X = gameindex.GameIndex(x360_root, 'x360', update=tu_dir, log=log)
    S = gameindex.GameIndex(steam_root, 'win32', log=log)
    log(f'360 game: {len(X.files)} files ({", ".join(f"{n} {c}" for n, c in X.sources[:6])}...)')
    log(f'Steam version: {len(S.files)} files')
    data = dict(shaders={}, known={}, pairs=collections.defaultdict(list), same=collections.Counter(), stats=collections.Counter(),
                bone_rules=collections.Counter(), vs_sigs=collections.defaultdict(list), kinds=collections.Counter())
    rules = xengine.Rules()
    qix_counts = dict(blocks={}, targets={})
    def shader_pairs(rp, rx, path=None):
        for name, kp, kx, a, b in shader.pairs(rp, rx):
            rules.add(a, b, path)
            data['kinds'][(kp, kx)] += 1
            if name.endswith('.vpo'):
                try:
                    sig = shader.vs_signature(a)
                    lst = data['vs_sigs'][sig]
                    if len(lst) < 40: lst.append((hashlib.md5(a).hexdigest(), name))
                except Exception: pass
    bone_probes = [0]
    known_bytes = [0]
    pair_bytes = collections.Counter()
    def add_pair(kind, p, x):
        if len(data['pairs'][kind]) < MAX_PAIRS and pair_bytes[kind] + len(p) <= MAX_PAIR_BYTES:
            data['pairs'][kind].append((p, x)); pair_bytes[kind] += len(p)
    def add_known(p, x):
        if p != x and known_bytes[0] + len(x) <= MAX_KNOWN_BYTES:
            k = md5(p)
            if k not in data['known']:
                data['known'][k] = x; known_bytes[0] += len(x)
    keys = sorted(k for k in set(X.files) & set(S.files) if k.rsplit('.', 1)[-1] in PAIR_KINDS)
    log(f'files in both to learn from: {len(keys)}')
    st = data['stats']
    for n, k in enumerate(keys):
        if time.time() - last >= 10:
            last = time.time()
            log(f'  {n} of {len(keys)} ({last - t0:.0f} s): {len(data["shaders"])} shader programs, '
                f'{len(data["known"])} exact entries, {sum(len(v) for v in data["pairs"].values())} examples')
        ext = k.rsplit('.', 1)[-1]
        # sound: only the header examples are learned (the DLC's own recordings are not in the
        # base game), so once there are enough, the rest is not read
        if ext == 'scd' or (ext == 'wpd' and k.startswith('sound/')):
            if len(data['pairs']['scd_head']) >= MAX_PAIRS: continue
        try:
            p, x = S.read(k), X.read(k)
        except Exception as ex:
            st['read error'] += 1; continue
        if p is None or x is None:
            st['missing'] += 1; continue
        if p == x:
            st['same ' + ext] += 1; continue
        try:
            if ext in ('trb', 'mpk'):
                tp, tx = trb.Trb(p), trb.Trb(x)
                if len(tp.res) != len(tx.res): st['trb resource counts differ'] += 1; continue
                if ext == 'trb' and bone_probes[0] < 60:
                    if bone_rule_probe(k, tp, tx, S, X, data['bone_rules'], log): bone_probes[0] += 1
                for i, (rp, rx) in enumerate(zip(tp.res, tx.res)):
                    if rp[:7] == b'SEDBshd':
                        try: shader.learn(rp, rx, data['shaders'])
                        except (AssertionError, ValueError): st['shader sets differ'] += 1
                        try: shader_pairs(rp, rx, k)
                        except Exception: st['shader pairs unreadable'] += 1
                    elif rp != rx and rp[:7] not in (b'SEDBtxb', b'SEDBwrb'):
                        add_known(rp, rx); add_pair('res:' + tp.type_of(i), rp, rx)
                st['pairs ' + ext] += 1
            elif ext in ('xfv', 'xwb', 'xwp', 'xgr', 'bin', 'wpd'):
                if p[:4] != b'WPD\0' or x[:4] != b'WPD\0': st['not a bundle ' + ext] += 1; continue
                ep, ex = wpd.read(p), wpd.read(x)
                if [(a, b) for a, b, _ in ep] != [(a, b) for a, b, _ in ex]: st['bundle lists differ'] += 1; continue
                for (name, e, a), (_, _, b) in zip(ep, ex):
                    kind = e.rstrip(b'\0').decode('latin1')
                    data['same'][(kind, a == b)] += 1
                    # effect layouts from every base-game effect member (only counts are kept)
                    if kind in ('veff', 'vlea', 'vmdl', 'vtex'):
                        try:
                            if qix.count_pair(qix_counts, a, b): st['effect members counted'] += 1
                        except Exception: st['effect members not counted'] += 1
                    if a == b: continue
                    if kind != 'scd': add_known(a, b)
                    if kind == 'vmdl':
                        vmdl.learn_pair(a, b, data['shaders'])
                        try:
                            ba, bb = vmdl.blocks(a), vmdl.blocks(b, True)
                            for (_, ta, sa), (_, tb, sb) in zip(ba or [], bb or []):
                                rules.add(a[ta + 0x20:ta + 0x20 + sa], b[tb + 0x20:tb + 0x20 + sb], k)
                        except Exception: pass
                        add_pair('vmdl', vmdl.object_part(a), vmdl.object_part(b, True))
                    elif kind == 'scd':
                        for hp, hx in sound.head_pairs([(a, b)]): add_pair('scd_head', hp, hx)
                    elif len(a) == len(b):
                        add_pair(kind, a, b)
                st['pairs ' + ext] += 1
            elif ext == 'scd':
                for hp, hx in sound.head_pairs([(p, x)]): add_pair('scd_head', hp, hx)
                st['pairs scd'] += 1
            elif ext == 'vinsbin':
                add_known(p, x); add_pair('vins', p, x); st['pairs vinsbin'] += 1
        except Exception as ex:
            st[f'error {ext}'] += 1
            if st[f'error {ext}'] <= 5: log(f'  {k}: {type(ex).__name__}: {ex}')
    st['entries past the end of their archive (360)'] = getattr(X, 'outside', 0)
    st['entries past the end of their archive (Steam)'] = getattr(S, 'outside', 0)
    X.close(); S.close()
    data['bone_rules'] = dict(data['bone_rules'])
    eng = rules.result()
    kinds = {}
    for (kp, kx), n in data['kinds'].most_common():
        kinds.setdefault(kp, kx)
    eng['kinds'] = kinds
    data['engine'] = eng
    data['vs_sigs'] = dict(data['vs_sigs'])
    log(f'360 engine rules from {eng["pairs"]} pixel shader pairs: {len(eng["fixed_ps"])} constants at fixed registers, '
        f'{len(eng["count_ps"])} with other register counts ({", ".join(n.decode("latin1") for n in eng["count_ps"])}), '
        f'{len(eng["extra_ps"])} material kinds with 360-only constants; {len(data["vs_sigs"])} vertex declarations')
    log(f'bone-index rules of the models with 256 or more joints: {data["bone_rules"]}')
    data['qix_kb'] = qix.masks(qix_counts)
    log(f'effect layouts: {len(data["qix_kb"]["blocks"])} element layouts, {len(data["qix_kb"]["targets"])} data layouts '
        f'from {st["effect members counted"]} base-game effect members')
    data['pairs'] = dict(data['pairs'])
    data['same'] = dict(data['same']); data['stats'] = dict(st)
    data['seconds'] = round(time.time() - t0)
    out = os.path.join(work, 'base_data.pkl')
    with open(out, 'wb') as f: pickle.dump(data, f, protocol=4)
    log(f'learned in {data["seconds"]} s: {len(data["shaders"])} shader programs, {len(data["known"])} exact entries, '
        f'examples per kind: {dict((k, len(v)) for k, v in sorted(data["pairs"].items()))}')
    log(f'counts: {data["stats"]}')
    return data

def dlc_known(x360_root, tu_dir, steam_root, work, log=print):
    """The members of the Steam DLC's bundles that are also in the base game's bundle of the same
    path, all of them (md5 of the PC member -> 360 member): the known table of learn() stops at
    MAX_KNOWN_BYTES, and members past it are converted as new. Also motion blocks to learn from.
    Saved as dlc_known.pkl."""
    import gzip, json
    t0 = last = time.time()
    with gzip.open(os.path.join(os.path.dirname(__file__), 'data', 'manifests.json.gz'), 'rt') as f:
        mf = json.load(f)
    X = gameindex.GameIndex(x360_root, 'x360', update=tu_dir, log=log)
    S = gameindex.GameIndex(steam_root, 'win32', log=log)
    paths = sorted({gameindex.norm(f[2]) for m in mf.values() for l in m['lists'] for f in l['files']
                    if f[2].rsplit('.', 1)[-1] in ('xfv', 'xwb', 'xwp', 'xgr') or f[2].endswith('.white.x360.bin')})
    paths = [k for k in paths if k in X.files and k in S.files]
    log(f'DLC bundles also in the base game: {len(paths)}')
    known, st = {}, collections.Counter()
    for n, k in enumerate(paths):
        if time.time() - last >= 10:
            last = time.time()
            log(f'  {n} of {len(paths)} ({last - t0:.0f} s): {len(known)} members')
        try:
            p, x = S.read(k), X.read(k)
            if not p or not x or p[:4] != b'WPD\0' or x[:4] != b'WPD\0':
                st['not bundles'] += 1; continue
            ep, ex = wpd.read(p), wpd.read(x)
        except Exception:
            st['unreadable'] += 1; continue
        # the members by name and kind (names that are in a bundle once)
        def by_name(ents):
            c = collections.Counter((a, b) for a, b, _ in ents)
            return {(a, b): d for a, b, d in ents if c[(a, b)] == 1}
        bp, bx = by_name(ep), by_name(ex)
        for nk, a in bp.items():
            b = bx.get(nk)
            if b is None: st['DLC members not in the base game'] += 1; continue
            if a == b: st['same on both'] += 1; continue
            known.setdefault(md5(a), b); st['members'] += 1
    # motion control blocks (mcb) of the base game's motion bundles: learn() keeps the first 400
    # mcb pairs it meets, all from the event folders (before mot/), and the monster and character
    # motion blocks have chunks those do not (Sazh's motions in Heads or Tails are a monster set)
    dlc = {gameindex.norm(f[2]) for m in mf.values() for l in m['lists'] for f in l['files']}
    mot = sorted(k for k in X.files if k.startswith('mot/') and k.endswith('.white.bin') and k not in dlc and k in S.files)
    step = max(1, len(mot) // MCB_BUNDLES)
    pick = sorted(mot[::step], key=lambda k: md5(k.encode()))   # (in a mixed order: the caps below)
    mcb_pairs, size = [], 0
    for n, k in enumerate(pick):
        if time.time() - last >= 10:
            last = time.time()
            log(f'  motion bundles: {n} of {len(pick)} ({last - t0:.0f} s): {len(mcb_pairs)} motion blocks')
        if len(mcb_pairs) >= MCB_PAIRS or size >= MCB_BYTES: break
        try:
            p, x = S.read(k), X.read(k)
            if not p or not x or p[:4] != b'WPD\0' or x[:4] != b'WPD\0': continue
            ep, ex = wpd.read(p), wpd.read(x)
        except Exception:
            st['motion bundles unreadable'] += 1; continue
        if [(a, b) for a, b, _ in ep] != [(a, b) for a, b, _ in ex]: continue
        for (name, e, a), (_, _, b) in zip(ep, ex):
            if e.rstrip(b'\0') == b'mcb' and a != b and len(a) == len(b):
                mcb_pairs.append((a, b)); size += len(a)
    st['motion blocks from the base game'] = len(mcb_pairs)
    X.close(); S.close()
    with open(os.path.join(work, 'dlc_known.pkl'), 'wb') as f:
        pickle.dump(dict(known=known, pairs=dict(mcb=mcb_pairs)), f, protocol=4)
    log(f'DLC members from the base game: {len(known)}, motion blocks to learn from: {len(mcb_pairs)} '
        f'in {time.time() - t0:.0f} s ({dict(st)})')

def load(work):
    with open(os.path.join(work, 'base_data.pkl'), 'rb') as f:
        data = pickle.load(f)
    dk = os.path.join(work, 'dlc_known.pkl')
    if os.path.exists(dk):
        with open(dk, 'rb') as f:
            extra = pickle.load(f)
        if 'known' not in extra: extra = dict(known=extra)          # (the first format)
        for k, v in extra['known'].items(): data['known'].setdefault(k, v)
        for kind, lst in extra.get('pairs', {}).items():
            have = {a for a, _ in data['pairs'].get(kind, [])}
            data['pairs'][kind] = list(data['pairs'].get(kind, [])) + [(a, b) for a, b in lst if a not in have]
    return data
