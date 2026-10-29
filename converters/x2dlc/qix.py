# Qix effect data (SEDBveff / SEDBleaf members of .xfv), the parts that can be read exactly.
# After the 0x30-byte SEDB header (R = 0x30; all offsets below are relative to R):
#   R+0x00 u32 0, u32 total size, u16 type count, u16, 4 x u8
#   R+0x10 u32, u32, u32 (first object), u32 instance-block list offset
#   R+0x20 type table: per type {u32 field-list offset, u32 block count, u16 field count, u16,
#     u32 end of its block list (the last type: u16 0x20, u16 0)}
#   field lists: per field {u16 offset in the element, u8 kind, u8}: the element's pointer fields
#     (u32 offsets from R that the game turns into pointers when it loads the data; kind 3 points
#     at a string, kind 2 at a string or data, kind 0 at data)
#   block list (from the u32 at R+0x1C), the blocks of every type in type order:
#     {u32 offset, u32 element count, u16 element size, u16}
# The rest of the data (floats, counts, u16 / u8 fields) has no description in the file and stays
# with the learned converter; this module rewrites the parts above, whose layout is known, so the
# game's pointer relocation sees exactly the 360 layout (a wrong guess there left a pointer
# unrelocated: crash in the effect code, guest read of a small address).
import struct

R = 0x30

def layout(d, big_endian=False):
    """-> dict(types, blocks, fields, pointers, inst) of a PC (little-endian) veff/leaf, or None
    when anything does not fit the file (then only the learned converter is used). big_endian:
    read a 360 file instead (to check the format on the 360 files)."""
    E = '>' if big_endian else '<'
    if len(d) < R + 0x20 or d[:4] != b'SEDB' or d[0xC] != (1 if big_endian else 0): return None
    _, size, ntypes = struct.unpack_from(E + 'IIH', d, R)
    h = struct.unpack_from(E + '4I', d, R + 0x10)
    if h[0] != 0x1C: return None          # the type table offset (the game reads it there)
    # the size need not be the member's length: it counts data kept elsewhere too (vtex: its
    # texels, in the .imgb), and a member can be padded past it (the event bundles' vins)
    if R + 0x20 + 16 * ntypes > len(d) or R + h[3] > len(d): return None
    types = [struct.unpack_from(E + 'IIHHI', d, R + 0x20 + 16 * i) for i in range(ntypes)]
    # a type's block list: the word before its entry (R+0x1C for the first), as the game reads it
    # (sub_829C51E0); not necessarily right after the previous type's
    starts = [h[3]] + [t[4] for t in types[:-1]]
    if any(R + st + 12 * t[1] > len(d) for st, t in zip(starts, types)): return None
    fields, blocks, pointers, inst = [], [], [], []
    for i, (fo, nb, nf, _, _) in enumerate(types):
        pos = R + starts[i]
        if R + fo + 4 * nf > len(d): return None
        fl = [struct.unpack_from(E + 'HBB', d, R + fo + 4 * k) for k in range(nf)]
        fields.append((R + fo, nf))
        sig = None
        for k in range(nb):
            o, c, s, _ = struct.unpack_from(E + 'IIHH', d, pos)
            blocks.append(pos); pos += 12
            # the last element of a block may run past the end of the file (not stored in full)
            if (c and not s) or R + o + (c - 1) * s > len(d): return None
            if any(off + 4 > s for off, _, _ in fl): return None
            if len(pointers) + c * len(fl) > len(d) // 4: return None
            if sig is None or sig[0] != s: sig = (s, tuple((a, b) for a, b, _ in fl))
            inst.append((sig, R + o, c))
            for e in range(c):
                for off, kind, _ in fl:
                    a = R + o + e * s + off
                    if a + 4 <= len(d): pointers.append((a, kind))
    return dict(ntypes=ntypes, types=R + 0x20, fields=fields, blocks=blocks, pointers=pointers, inst=inst)

def game_fixups(d, big_endian=True):
    """the words the game's pointer relocation reads and rewrites in a SEDB member (as
    sub_829C51E0 walks it: the type table at R + u32[R+0x10], per type {block list, field list,
    block count, u16 field count}, per block {offset, count, u16 element size}, the pointer field
    of every element, then the u16[R+0xA] words listed at R + u32[R+0x14]) -> list of
    (address, value), or a string saying what the game would trip over"""
    E = '>' if big_endian else '<'
    n = len(d)
    def u32(a):
        if a < 0 or a + 4 > n: raise ValueError(f'reads past the end ({a:#x})')
        return struct.unpack_from(E + 'I', d, a)[0]
    def u16(a):
        if a < 0 or a + 2 > n: raise ValueError(f'reads past the end ({a:#x})')
        return struct.unpack_from(E + 'H', d, a)[0]
    try:
        out = []
        nt, nrel = u16(R + 8), u16(R + 10)
        tt = R + u32(R + 0x10)
        for i in range(nt):
            e = tt + 16 * i
            blk, fld, nb, nf = R + u32(e), R + u32(e + 4), u32(e + 8), u16(e + 12)
            if nb > 0x10000 or nf > 0x400: raise ValueError(f'type {i}: {nb} blocks, {nf} fields')
            out.append((e, (blk, fld, nb, nf)))
            for b in range(nb):
                off, cnt, sz = u32(blk + 12 * b), u32(blk + 12 * b + 4), u16(blk + 12 * b + 8)
                if cnt > 0x100000: raise ValueError(f'type {i} block {b}: {cnt} elements')
                out.append((blk + 12 * b, (off, cnt, sz)))
                for k in range(cnt):
                    for f in range(nf):
                        a = R + off + k * sz + u16(fld + 4 * f)
                        if a + 4 <= n: out.append((a, u32(a)))
        rl = R + u32(R + 0x14)
        for k in range(nrel): out.append((rl + 4 * k, u32(rl + 4 * k)))
        return out
    except ValueError as ex:
        return str(ex)

def game_layout(d):
    """layout() as the game's relocation walk reads a member (game_fixups), for the SEDB members
    whose header layout() does not take (lyb, ...): dict(inst, pointers, ...) or None"""
    if len(d) < R + 0x20 or d[:4] != b'SEDB': return None
    walk = game_fixups(d, big_endian=False)
    if isinstance(walk, str) or not walk: return None
    u32 = lambda a: struct.unpack_from('<I', d, a)[0]
    u16 = lambda a: struct.unpack_from('<H', d, a)[0]
    nt = u16(R + 8)
    tt = R + u32(R + 0x10)
    inst, pointers, blocks, fields = [], [], [], []
    for i in range(nt):
        e = tt + 16 * i
        blk, fld, nb, nf = R + u32(e), R + u32(e + 4), u32(e + 8), u16(e + 12)
        fl = tuple((u16(fld + 4 * f), d[fld + 4 * f + 2]) for f in range(nf))
        fields.append((fld, nf))
        for b in range(nb):
            off, cnt, size = u32(blk + 12 * b), u32(blk + 12 * b + 4), u16(blk + 12 * b + 8)
            blocks.append(blk + 12 * b)
            if not cnt or not size: continue
            inst.append(((size, fl), R + off, cnt))
            for k in range(cnt):
                for o, kind in fl:
                    a = R + off + k * size + o
                    if a + 4 <= len(d): pointers.append((a, kind))
    return dict(ntypes=nt, types=tt, fields=fields, blocks=blocks, pointers=pointers, inst=inst)

def split_words(d, L):
    """(word offsets outside the typed entries, word offsets inside) from R on"""
    import bisect
    spans = sorted((b, b + c * s[0]) for s, b, c in L['inst'])
    starts = [a for a, _ in spans]
    out, inside = [], []
    for i in range(R, len(d) - 3, 4):
        k = bisect.bisect_right(starts, i) - 1
        (inside if k >= 0 and spans[k][0] <= i < spans[k][1] else out).append(i)
    return out, inside

def keep_text(pc, x, L=None, min_run=5):
    """x with the NUL-terminated names outside the typed elements (the name pools of lyb and the
    like, packed at any alignment) put back as the PC bytes: a name is the same on both"""
    import bisect
    spans = sorted((b, b + c * s[0]) for s, b, c in L['inst']) if L else []
    starts = [a for a, _ in spans]
    def in_elem(i):
        k = bisect.bisect_right(starts, i) - 1
        return k >= 0 and spans[k][0] <= i < spans[k][1]
    out = bytearray(x)
    i, n = R, len(pc)
    while i < n:
        c = pc[i]
        if 32 <= c < 127 and (i == R or pc[i - 1] == 0):
            j = i
            while j < n and 32 <= pc[j] < 127: j += 1
            run = pc[i:j]
            if j - i >= min_run and j < n and pc[j] == 0 and any(65 <= b <= 90 or 97 <= b <= 122 for b in run) \
                    and not in_elem(i) and not in_elem(j):
                out[i:j + 1] = pc[i:j + 1]
            i = max(j, i + 1)
        else:
            i += 1
    return bytes(out)

def fix_game(pc, x):
    """x: a 360 conversion of the SEDB member pc -> x with every word the game's pointer
    relocation reads (see game_fixups: the header counts and offsets, the type table, the block
    lists, the field lists, the pointer fields, and the relocation list) converted exactly, the
    strings the pointers lead to kept as bytes; (bytes, number of words set). Any SEDB member
    the game relocates has these (lyb, mcb, vins, vtex, veff, ...), whatever its other header
    words; a member the walk does not fit is left as it is."""
    if len(pc) < R + 0x20 or pc[:4] != b'SEDB' or len(x) != len(pc): return x, 0
    walk = game_fixups(pc, big_endian=False)
    if isinstance(walk, str) or not walk: return x, 0      # (no types, no relocations: phb, ...)
    out = bytearray(x)
    n = [0]
    def sw(at, fmt):
        _swap(out, pc, at, fmt); n[0] += 1
    sw(R, 'II')                           # (a tag or 0), the size
    sw(R + 8, 'HH')                       # type count, relocation count
    sw(R + 0x10, 'IIII')                  # type table, relocation list, two more offsets
    u32 = lambda a: struct.unpack_from('<I', pc, a)[0]
    u16 = lambda a: struct.unpack_from('<H', pc, a)[0]
    nt, nrel = u16(R + 8), u16(R + 10)
    tt = R + u32(R + 0x10)
    for i in range(nt):
        e = tt + 16 * i
        sw(e, 'IIIHH')
        blk, fld, nb, nf = R + u32(e), R + u32(e + 4), u32(e + 8), u16(e + 12)
        for f in range(nf): sw(fld + 4 * f, 'HBB')
        kinds = [pc[fld + 4 * f + 2] for f in range(nf)]
        for b in range(nb):
            sw(blk + 12 * b, 'IIHH')
            off, cnt, size = u32(blk + 12 * b), u32(blk + 12 * b + 4), u16(blk + 12 * b + 8)
            for k in range(cnt):
                for f in range(nf):
                    a = R + off + k * size + u16(fld + 4 * f)
                    if a + 4 > len(pc): continue
                    sw(a, 'I')
                    if kinds[f] in (2, 3):
                        t = R + u32(a)
                        if R < t < len(pc):
                            z = pc.find(b'\0', t)
                            st = pc[t:z] if z > t else b''
                            if st and (kinds[f] == 3 or all(32 <= c < 127 for c in st)):
                                out[t:z + 1] = pc[t:z + 1]
    rl = R + u32(R + 0x14)
    for k in range(nrel): sw(rl + 4 * k, 'I')
    # the result must walk as the PC member does
    if game_fixups(bytes(out)) != [(a, v) for a, v in walk]:
        return x, 0
    return bytes(out), n[0]

def exact_fit(d):
    """layout() and the stricter shape of the effect data (veff / vlea): the size is the
    member's own, every block list right after the previous type's (which members take the
    effect-layout conversion)"""
    L = layout(d)
    if L is None or struct.unpack_from('<I', d, R + 4)[0] != len(d): return None
    end = struct.unpack_from('<I', d, R + 0x1C)[0]
    for i in range(L['ntypes'] - 1):
        at = L['types'] + 16 * i
        end += 12 * struct.unpack_from('<I', d, at + 4)[0]
        if struct.unpack_from('<I', d, at + 12)[0] != end: return None
    return L

def _swap(out, src, at, fmt):
    out[at:at + struct.calcsize(fmt)] = struct.pack('>' + fmt, *struct.unpack_from('<' + fmt, src, at))

def fix(pc, x, dec=None):
    """x: the learned 360 conversion of pc -> x with header, type table, field lists, block list
    and pointer fields converted exactly, and pointed-to strings kept as bytes; (bytes, count of
    words written)"""
    L = layout(pc)
    if L is None: return x, 0
    from .learned import sedb_header
    out = bytearray(x)
    sedb_header(pc, out); out[0x20:R] = pc[0x20:R]
    out[0x18:0x20] = pc[0x18:0x20][::-1]      # XIII-2: a u64 (LR: the same bytes on both)
    _swap(out, pc, R, 'IIHH'); out[R + 12:R + 16] = pc[R + 12:R + 16]
    _swap(out, pc, R + 0x10, '4I')
    end = struct.unpack_from('<I', pc, R + 0x1C)[0]
    for i in range(L['ntypes']):
        at = L['types'] + 16 * i
        end += 12 * struct.unpack_from('<I', pc, at + 4)[0]
        # the last word is the next type's block list; after the last type, two u16 (0x20, 0 in
        # every file seen: PC 20 00 00 00, 360 00 20 00 00)
        last_is_end = i < L['ntypes'] - 1 or struct.unpack_from('<I', pc, at + 12)[0] == end
        _swap(out, pc, at, 'IIHHI' if last_is_end else 'IIHHHH')
    for at, n in L['fields']:
        for k in range(n): _swap(out, pc, at + 4 * k, 'HBB')
    for at in L['blocks']: _swap(out, pc, at, 'IIHH')
    if dec is not None:
        dec[:R + 0x20 + 16 * L['ntypes']] = b'\1' * (R + 0x20 + 16 * L['ntypes'])
        for at, n in L['fields']: dec[at:at + 4 * n] = b'\1' * (4 * n)
        for at in L['blocks']: dec[at:at + 12] = b'\1' * 12
    n = 0
    for at, kind in L['pointers']:
        _swap(out, pc, at, 'I'); n += 1
        if dec is not None: dec[at:at + 4] = b'\1' * 4
        if kind in (2, 3):
            t = R + struct.unpack_from('<I', pc, at)[0]
            if R < t < len(pc):
                e = pc.find(b'\0', t)
                s = pc[t:e] if e > t else b''
                if s and (kind == 3 or all(32 <= c < 127 for c in s)):
                    out[t:e + 1] = pc[t:e + 1]
                    if dec is not None: dec[t:e + 1] = b'\1' * (e + 1 - t)
    return bytes(out), n


# ---- element layouts learned per type signature ---------------------------------------------
# A type's element layout is fixed by the game code, so the same signature (element size and
# pointer fields) has the same field sizes in every file. Per 2-byte position of an element the
# number of instances is counted, and how many of them each field size fits (learned._cands bits:
# 1 two bytes kept, 2 u16, 4 u32 starting here); the sizes that fit every instance are the layout.
# Counts rather than intersections, so one file can be taken out again (leave-one-out check).

def _cands_np(p, x):
    """learned._cands with numpy: per 2-byte position the field sizes consistent with the bytes"""
    import numpy as np
    n = len(p); h = (n + 1) // 2
    pe = np.frombuffer(bytes(p) + bytes(8), np.uint8); xe = np.frombuffer(bytes(x) + bytes(8), np.uint8)
    p16 = pe[:2 * h].view('<u2'); x16 = xe[:2 * h].view('<u2')
    m = (p16 == x16).astype(np.uint8) | ((p16.byteswap() == x16).astype(np.uint8) << 1)
    for off in (0, 2):
        cnt = (n - off) // 4
        if cnt <= 0: continue
        a = pe[off:off + 4 * cnt].view('<u4').byteswap(); b = xe[off:off + 4 * cnt].view('<u4')
        m[(off // 2)::2][:cnt] |= (a == b).astype(np.uint8) << 2
    if n % 2: m[-1] = 1 if x[n - 1] == p[n - 1] else 0
    return m

def _count(kb, key, period, m, start, n):
    import numpy as np
    a = kb.get(key)
    if a is None: a = kb[key] = np.zeros((period, 4), np.int64)
    seg = m[start:start + n]
    rows = len(seg) // period
    if rows:
        blk = seg[:rows * period].reshape(rows, period)
        a[:, 0] += rows
        for b, col in ((1, 1), (2, 2), (4, 3)): a[:, col] += ((blk & b) != 0).sum(0)
    rem = seg[rows * period:]
    if len(rem):
        a[:len(rem), 0] += 1
        for b, col in ((1, 1), (2, 2), (4, 3)): a[:len(rem), col] += (rem & b) != 0

def count_pair(kb, p, x, lay=None):
    """add one PC / 360 member pair to the counts kb (dict(blocks={}, targets={}))"""
    if len(p) != len(x): return False
    L = (lay or layout)(p)
    if L is None: return False
    m = _cands_np(p, x)
    for sig, base, c in L['inst']:
        s = sig[0]
        if s % 2 or base % 2 or base + c * s > len(p): continue
        _count(kb['blocks'], sig, s // 2, m, base // 2, c * (s // 2))
    for t, n, keys in _targets(p, L):
        for key in keys:
            period = key[3] // 2 if key[2] == 'E' else (n + 1) // 2
            _count(kb['targets'], key, period, m, t // 2, (n + 1) // 2)
    return True

def learn_counts(pairs, log=None):
    import time
    kb = dict(blocks={}, targets={})
    t0 = last = time.time()
    for i, (p, x) in enumerate(pairs):
        if log and time.time() - last >= 10:
            last = time.time(); log(f'    effect layouts: {i} of {len(pairs)} base-game members read ({last - t0:.0f} s)')
        count_pair(kb, p, x)
    if log: log(f'    effect layouts: {len(pairs)} base-game members read ({time.time() - t0:.0f} s)')
    return kb

def minus(kb, kb2):
    """kb without the counts of kb2 (both from learn_counts)"""
    out = dict(blocks={}, targets={})
    for part in ('blocks', 'targets'):
        for key, a in kb[part].items():
            b = kb2[part].get(key)
            if b is None: out[part][key] = a; continue
            r = a - b
            if r[:, 0].any(): out[part][key] = r
    return out

def masks(kb):
    """counts -> per position the bit mask of the field sizes that fit every instance"""
    def f(a):
        t = a[:, 0]
        m = (a[:, 1] == t) * 1 | (a[:, 2] == t) * 2 | (a[:, 3] == t) * 4
        m[t == 0] = 7
        return m.tolist()
    return {part: {key: f(a) for key, a in kb[part].items()} for part in ('blocks', 'targets')}

def apply(pc, x, kb, dec=None, text=None, lay=None):
    """x with the element fields of known signatures converted by the learned layouts; (bytes,
    half-words decided, element half-words of unknown signatures)"""
    L = (lay or layout)(pc)
    if L is None or not kb: return x, 0, 0
    out = bytearray(x)
    done = unknown = 0
    for sig, base, c in L['inst']:
        s = sig[0]
        a = kb.get(sig)
        if a is None or base % 2:
            unknown += c * (s // 2); continue
        for e in range(c):
            o = base + e * s
            j = 0
            while j < len(a):
                at = o + 2 * j
                if at + 2 > len(pc): break
                m = a[j]
                if text is not None and text[at]: j += 1; continue
                if m & 4 and (j % 2 == 0 or not m & 3) and j + 1 < len(a) and at + 4 <= len(pc) and not (text is not None and text[at + 2]):
                    out[at:at + 4] = pc[at:at + 4][::-1]; done += 2; j += 2
                    if dec is not None: dec[at:at + 4] = b'\1' * 4
                    continue
                if m & 2: out[at:at + 2] = pc[at:at + 2][::-1]; done += 1
                elif m & 1: out[at:at + 2] = pc[at:at + 2]; done += 1
                if m and dec is not None: dec[at:at + 2] = b'\1\1'
                j += 1
    return bytes(out), done, unknown

# ---- data the pointer fields lead to (outside the typed blocks) -------------------------------
# Keyed by the owner (signature, field offset) and the region: its length (blobs of one kind have
# one length) or, from the count in the word after the pointer, its element size.

def _targets(p, L):
    import bisect
    covered = []
    for sig, base, c in L['inst']: covered.append((base, base + c * sig[0]))
    covered.sort()
    starts = [a for a, b in covered]
    def inside(t):
        k = bisect.bisect_right(starts, t) - 1
        return k >= 0 and covered[k][0] <= t < covered[k][1]
    tg = {}
    for sig, base, c in L['inst']:
        s = sig[0]
        for e in range(c):
            for off, kind in sig[1]:
                if kind != 0: continue
                at = base + e * s + off
                if at + 8 > len(p): continue
                v, nxt = struct.unpack_from('<II', p, at)
                t = R + v
                if v and t < len(p) and t % 2 == 0 and not inside(t): tg.setdefault(t, (sig, off, nxt))
    bounds = sorted(set(list(tg) + starts + [len(p)]))
    out = []
    for t, (sig, off, nxt) in tg.items():
        end = bounds[bisect.bisect_right(bounds, t)]
        n = end - t
        if n <= 0 or n > 8192: continue
        keys = [(sig, off, 'len', n)]
        cnt = nxt & 0xFFFF
        if 0 < cnt <= n and n // cnt >= 2:
            E = n // cnt
            if E % 2 == 0 and E * cnt >= n - 3: keys.append((sig, off, 'E', E))
        out.append((t, n, keys))
    return out

def apply_targets(pc, x, kb, dec=None, text=None, lay=None):
    L = (lay or layout)(pc)
    if L is None or not kb: return x, 0
    out = bytearray(x)
    done = 0
    for t, n, keys in _targets(pc, L):
        a = next((kb[k] for k in keys if k in kb), None)
        if a is None: continue
        period = len(a)
        j = 0
        while j < (n + 1) // 2:
            at = t + 2 * j
            m = a[j % period]
            if text is not None and at < len(text) and text[at]: j += 1; continue
            if m & 4 and (j % 2 == 0 or not m & 3) and j + 1 < (n + 1) // 2 and (j % period) + 1 < period and at + 4 <= len(pc) and not (text is not None and text[at + 2]):
                out[at:at + 4] = pc[at:at + 4][::-1]; done += 2; j += 2
                if dec is not None: dec[at:at + 4] = b'\1' * 4
                continue
            if at + 2 <= len(pc):
                if m & 2: out[at:at + 2] = pc[at:at + 2][::-1]; done += 1
                elif m & 1: out[at:at + 2] = pc[at:at + 2]; done += 1
                if m and dec is not None: dec[at:at + 2] = b'\1\1'
            j += 1
    return bytes(out), done

def convert(pc, x, kb):
    """learned conversion x of pc -> refined: learned element layouts, learned target layouts,
    then the exact parts (last, so nothing overrides them)"""
    if layout(pc) is None: return x, None
    dec = bytearray(len(pc))
    y, nb, unk = apply(pc, x, kb.get('blocks', {}), dec)
    y, nt = apply_targets(pc, y, kb.get('targets', {}), dec)
    # data that starts in the middle of a word (u16 arrays): the half before it is a u16 as well
    # (the end of the array before; a u32 guess over both halves would mix two values)
    y = bytearray(y)
    L = layout(pc)
    for t, n, keys in _targets(pc, L):
        if t % 4 == 2 and not dec[t - 2] and not dec[t - 1]:
            y[t - 2:t] = pc[t - 2:t][::-1]; dec[t - 2:t] = b'\1\1'
    y = bytes(y)
    y, npt = fix(pc, y, dec)
    return y, dict(block_halfwords=nb, unknown_signature_halfwords=unk, target_halfwords=nt, pointers=npt,
                   undecided_bytes=dec.count(0), decided=dec)

def learn_all(pairs, log=None):
    return masks(learn_counts(pairs, log))

def contextual_layout(d):
    """Distinguish classes that happen to have the same size and pointer fields.

    A leaf contains two different eight-byte classes with a pointer at zero.
    One has two u16 fields after the pointer; the other has a u32. Pooling them
    erases the evidence for both. Their positions in the type schema distinguish
    them without depending on resource names, array counts, or DLC samples.
    """
    L = layout(d)
    if L is None: return
    groups, schema, pos = [], [], 0
    for i in range(L['ntypes']):
        nb = struct.unpack_from('<I', d, L['types'] + 16 * i + 4)[0]
        group = L['inst'][pos:pos + nb]
        pos += nb
        groups.append(group)
        schema.append(group[0][0] if group else None)
    schema = tuple(schema)
    for i, group in enumerate(groups):
        for sig, base, count in group:
            yield (sig, i, schema), base, count

def fix_leaf_indices(pc, x):
    """BaseLeaf's two reference arrays contain signed 16-bit object IDs.

    Cutscene VINS nodes have 80-byte records; effect leaves use 84-byte
    records. The native 828ED1E0 reader passes both +16/+24 lists to the same
    828ED040 helper. A four-byte swap can exchange adjacent one-ID lists.
    """
    L = layout(pc)
    if L is None or len(pc) != len(x): return x, 0
    nodes = {(80, ((32, 2), (40, 3), (0, 0), (8, 0), (16, 0), (24, 0), (68, 0))),
             (84, ((32, 2), (40, 3), (0, 0), (8, 0), (16, 0), (24, 0), (76, 3), (68, 0)))}
    out, indices = bytearray(x), set()
    for sig, at, num in L['inst']:
        if sig not in nodes: continue
        for i in range(num):
            for field in (16, 24):
                target, count = struct.unpack_from('<II', pc, at + sig[0] * i + field)
                target += R
                if not count or target < R or target + count * 2 > len(pc): continue
                for k in range(count):
                    word = target + 2 * k
                    out[word:word + 2] = pc[word:word + 2][::-1]
                    indices.add(word)
    return bytes(out), len(indices)


def fix_generate_indices(pc, x):
    """Generate-control resource references are u16 indices, not byte flags.

    The native 828F0F18/828F0F60 accessors read a unit's +8 group table,
    then index its referenced resource list with lhz. Require both serialized
    class signatures and bounded tables before changing any target bytes.
    """
    L = layout(pc)
    if L is None or len(pc) != len(x): return x, 0
    unit = (36, ((24, 0), (0, 0), (8, 0), (16, 0)))
    group = (8, ((0, 0),))
    ranges = [(at, at + num * 8) for sig, at, num in L['inst'] if sig == group]
    out, indices = bytearray(x), set()
    for sig, at, num in L['inst']:
        if sig != unit: continue
        for i in range(num):
            target, count = struct.unpack_from('<II', pc, at + 36 * i + 8)
            target += R
            if not count or not any(lo <= target and target + count * 8 <= hi for lo, hi in ranges): continue
            for j in range(count):
                offset, size = struct.unpack_from('<II', pc, target + 8 * j)
                offset += R
                if not size or offset < R or offset + size * 2 > len(pc): continue
                for k in range(size):
                    word = offset + 2 * k
                    out[word:word + 2] = pc[word:word + 2][::-1]
                    indices.add(word)
    return bytes(out), len(indices)


def fix_particle_references(pc, x):
    """Particle emitter parameters carry pairs of signed 16-bit resource IDs.

    Native 829D6078 reads the emitter's +180 payload and passes its +40,
    +44, +76 and +84 pairs to 828F0CD0. The 96-byte payload signature is
    shared by other classes, where +84 can be a float. Require the owning
    240-byte emitter class and its typed payload range, not just payload size.
    """
    L = layout(pc)
    if L is None or len(pc) != len(x): return x, 0
    emitter = (240, ((180, 0), (188, 0), (160, 0), (168, 0)))
    payload = (96, ((24, 0),))
    ranges = [(at, at + num * 96) for sig, at, num in L['inst'] if sig == payload]
    out, indices = bytearray(x), set()
    for sig, at, num in L['inst']:
        if sig != emitter: continue
        for i in range(num):
            target, count = struct.unpack_from('<II', pc, at + 240 * i + 180)
            target += R
            if not count or not any(lo <= target and (target-lo) % 96 == 0 and target + count * 96 <= hi for lo, hi in ranges): continue
            for j in range(count):
                for field in (40, 44, 76, 84):
                    word = target + 96 * j + field
                    _swap(out, pc, word, 'hh')
                    indices.update((word, word + 2))
    return bytes(out), len(indices)


def fix_curves(pc, x):
    """Effect curve records: {u8 mode, u8 components, u16 zero, floats[]}.

    Modes 0/1/2 store one/two/three floats per component. A target must
    consist entirely of valid records before any bytes are changed. Float
    values are retained from Steam, including authored platform differences.
    """
    L = layout(pc)
    if L is None: return x, 0
    out, count = bytearray(x), 0
    for target, length, keys in _targets(pc, L):
        if keys[0][0] != (4, ((0, 0),)): continue
        end, at, records = target + length, target, []
        while at + 4 <= end:
            mode, components, z0, z1 = pc[at:at + 4]
            if mode > 2 or not 1 <= components <= 4 or z0 or z1: break
            size = 4 + 4 * (mode + 1) * components
            if at + size > end: break
            records.append((at, size))
            at += size
        if at != end: continue
        for at, size in records:
            out[at:at + 4] = pc[at:at + 4]
            for word in range(at + 4, at + size, 4): out[word:word + 4] = pc[word:word + 4][::-1]
            count += 1
    return bytes(out), count

def learn_contexts(pairs):
    counts = {}
    for pc, x in pairs:
        if len(pc) != len(x): continue
        candidates = _cands_np(pc, x)
        for key, base, count in contextual_layout(pc):
            size = key[0][0]
            if size % 2 or base % 2 or base + count * size > len(pc): continue
            _count(counts, key, size // 2, candidates, base // 2, count * (size // 2))
    return masks(dict(blocks=counts, targets={}))['blocks']

def property_targets(pc, L):
    """32-byte property payloads, distinguished by the serialized field tag.

    The same pointer class carries strings and numeric records. Its word
    after the payload pointer, together with its type schema, distinguishes
    variants; it is not a byte count.
    Shared targets with conflicting tags keep the existing conversion.
    """
    signature = (36, ((24, 0), (0, 0), (8, 0), (16, 0)))
    tags = {}
    for (sig, position, schema), base, count in contextual_layout(pc):
        if sig != signature: continue
        for i in range(count):
            at = base + i * sig[0] + 24
            if at + 8 > len(pc): continue
            offset, tag = struct.unpack_from('<II', pc, at)
            tags.setdefault(R + offset, set()).add((tag, position, schema))
    for target, length, keys in _targets(pc, L):
        if keys[0][:2] == (signature, 24) and length == 32 and len(tags.get(target, ())) == 1:
            yield target, length, [(signature, 24, 'variant', (next(iter(tags[target])), length))]

def learn_property_targets(pairs):
    counts = {}
    for pc, x in pairs:
        if len(pc) != len(x): continue
        L = layout(pc)
        if L is None: continue
        candidates = None
        for target, length, keys in property_targets(pc, L):
            if candidates is None: candidates = _cands_np(pc, x)
            _count(counts, keys[0], length // 2, candidates, target // 2, length // 2)
    # Zero/symmetric examples cannot choose a byte order. Those halfwords
    # keep the existing conversion, rather than preferring a guessed u32.
    return {key: [m if m in (1, 2, 4) else 0 for m in mask]
            for key, mask in masks(dict(blocks={}, targets=counts))['targets'].items()}

def apply_property_targets(pc, x, kb):
    L = layout(pc)
    if L is None or not kb: return x
    out = bytearray(x)
    for target, length, keys in property_targets(pc, L):
        mask = kb.get(keys[0])
        if mask is None: continue
        half = 0
        while half < length // 2:
            at = target + 2 * half
            choice = mask[half]
            if choice == 4 and half % 2 == 0 and half + 1 < len(mask) and mask[half + 1] == 4:
                out[at:at + 4] = pc[at:at + 4][::-1]
                half += 2
                continue
            if choice == 2: out[at:at + 2] = pc[at:at + 2][::-1]
            elif choice == 1: out[at:at + 2] = pc[at:at + 2]
            half += 1
    return bytes(out)

def apply_contexts(pc, x, contexts):
    for key, base, count in contextual_layout(pc):
        mask = contexts.get(key)
        if mask is None: continue
        sig = key[0]
        x, _, _ = apply(pc, x, {sig: mask},
                        lay=lambda _: dict(inst=[(sig, base, count)]))
    return x

def self_check(pairs, oracle, samples=4, seed=1, max_bytes=16384, log=print, seconds=180):
    """leave-one-out on PC/360 pairs: each sampled file converted with the oracle and the layouts
    learned without it -> dict(sampled, exact, wrong_bytes, total_bytes, learned_only_wrong_bytes)"""
    import random, time
    from . import learned
    t0 = time.time()
    kbc = learn_counts(pairs)
    cand = [(p, x) for p, x in pairs if len(p) == len(x) and len(p) <= max_bytes and layout(p) is not None]
    smp = random.Random(seed).sample(cand, min(samples, len(cand)))
    st = dict(sampled=len(smp), exact=0, wrong_bytes=0, total_bytes=0, learned_only_wrong_bytes=0)
    st['sampled'] = 0
    for k, (p, x) in enumerate(smp):
        if time.time() - t0 > seconds:
            log(f'    (time limit reached after {k} file(s))'); break
        log(f'    file {k + 1} of {len(smp)} ({len(p)} bytes)')
        st['sampled'] += 1
        kb = masks(minus(kbc, learn_counts([(p, x)])))
        c, _ = oracle.convert(p, learned.sedb_header, exclude=p)
        y, _ = convert(p, c, kb)
        w = sum(1 for a, b in zip(y, x) if a != b)
        st['exact'] += w == 0; st['wrong_bytes'] += w; st['total_bytes'] += len(x)
        st['learned_only_wrong_bytes'] += sum(1 for a, b in zip(c, x) if a != b)
    return st


# ---- words no layout covers: value rules measured on the effect data -------------------------
# (on the words of the effect members that neither the exact parts nor a learned layout decide,
# PC vs 360: text and words of two leading bytes and two zero bytes are byte fields, kept; four-
# letter type tags inside names are reversed; the rest as learned.guess)

_TXT = set(range(32, 127))

def _text_spans(d, start, min_run=6):
    """[(a, b)]: runs of printable bytes of at least min_run (names, type and file names)"""
    out = []
    i, n = start, len(d)
    while i < n:
        if d[i] in _TXT:
            j = i
            while j < n and d[j] in _TXT: j += 1
            if j - i >= min_run: out.append((i, j))
            i = j
        else:
            i += 1
    return out

def _wclass(w):
    from .learned import _is_float
    if len(w) < 4 or w == b'\0\0\0\0': return 'zero'
    v = struct.unpack('<I', w)[0]
    if _is_float(v): return 'float'
    lo, hi = v & 0xFFFF, v >> 16
    if hi == 0 and lo < 0x100: return 'small'
    if hi < 0x100 and lo < 0x100: return 'pair'
    return 'big'

# (word before, word after) -> what a (b, 0, 0, 0) word is on the 360 (kept: bytes)
_ZX = {('zero', 'zero'): 'kept', ('pair', 'zero'): 'kept', ('pair', 'pair'): 'u32',
       ('zero', 'small'): 'u16x2', ('big', 'pair'): 'u16x2', ('small', 'zero'): 'u16x2',
       ('zero', 'pair'): 'u16x2', ('small', 'big'): 'u16x2', ('small', 'pair'): 'u16x2',
       ('small', 'small'): 'u16x2', ('float', 'small'): 'u16x2', ('pair', 'small'): 'u16x2',
       ('big', 'zero'): 'u32', ('zero', 'float'): 'u32', ('big', 'big'): 'u32', ('pair', 'big'): 'u32',
       ('big', 'float'): 'u32', ('small', 'float'): 'u32', ('zero', 'big'): 'u32', ('big', 'small'): 'u32',
       ('float', 'zero'): 'kept'}

def text_mask(pc, start=R):
    """per byte 1 where a word holds text (names: the same bytes on both platforms)"""
    t = bytearray(len(pc))
    for a, b in _text_spans(pc, start, 8):
        for w in range(a - a % 4, b, 4):
            x = pc[w:w + 4]
            if len(x) == 4 and all(c in _TXT or c == 0 for c in x): t[w:w + 4] = b'\1\1\1\1'
    return t

def string_bytes(pc):
    """per byte 1 for the strings the string pointers (kinds 2 and 3) lead to, with their NULs
    (empty strings too)"""
    t = bytearray(len(pc))
    L = layout(pc)
    if L is None: return t
    for at, kind in L['pointers']:
        if kind not in (2, 3): continue
        a = R + struct.unpack_from('<I', pc, at)[0]
        if not R < a < len(pc): continue
        e = pc.find(b'\0', a)
        if e < 0: continue
        sb = pc[a:e]
        if kind == 2 and not all(c in _TXT for c in sb): continue     # (kind 2: a string or data)
        t[a:e + 1] = b'\1' * (e + 1 - a)
    return t

def guess_effect(pc, start=R):
    from .learned import guess, _is_float
    from .convert import TAGS
    out = bytearray(pc)
    n = len(pc) // 4 * 4
    text = string_bytes(pc)
    for a, b in _text_spans(pc, start):
        text[a:b] = b'\1' * (b - a)
    for w in range(start - start % 4, n, 4):
        if w < start: continue
        x = pc[w:w + 4]
        if any(text[w:w + 4]):
            continue                                  # text (and the bytes sharing its word): kept
        if x[3] == 0 and x[2] == 0 and x[1] != 0:
            continue                                  # (b0, b1, 0, 0) / (0, b1, 0, 0): byte fields
        if x[3] == 0 and all(c in _TXT for c in x[:3]):
            continue                                  # three letters and the NUL of a name
        v = struct.unpack('<I', x)[0]
        if x[0] and not x[1] and not x[2] and not x[3]:
            # (b, 0, 0, 0): a u32, two u16 or bytes - by the words around it (as measured)
            k = _ZX.get((_wclass(pc[w - 4:w]), _wclass(pc[w + 4:w + 8])), 'u32')
            if k == 'u16x2': out[w:w + 4] = x[1::-1] + x[3:1:-1]
            elif k == 'u32': out[w:w + 4] = x[::-1]
            continue
        if all(c in _TXT for c in x) and _is_float(v):
            out[w:w + 4] = x[::-1]; continue          # four printable bytes outside text: a float
        out[w:w + 4] = guess(x)
    for t in TAGS:                                    # type tags in names: u32
        r = t[::-1]
        k = pc.find(r, start)
        while k >= 0:
            if k % 4 == 0: out[k:k + 4] = t
            k = pc.find(r, k + 1)
    return bytes(out)

# ---- layout rules learned per type (lyb) ------------------------------------------------------
# What a pointer field of a type leads to (data outside the typed elements, up to the next thing
# pointed at) is either kept as the PC bytes on the 360 (byte streams: the AI programs, the NPC
# entries) or swapped word by word (float tables), the same for every member with that type
# (base-game pairs). So is each word of an element at a given offset.

def _word_class(p, x):
    if p == p[::-1]: return None
    return 'k' if p == x else 'W' if p[::-1] == x else '?'

def layout_blocks(d, L):
    """[(start, end, (size, fields, offset))]: the data outside the typed elements that their
    pointer fields lead to, each up to the next such start, element or the end"""
    import bisect
    spans = sorted((b, b + c * s[0]) for s, b, c in L['inst'])
    starts = [a for a, _ in spans]
    def in_elem(i):
        k = bisect.bisect_right(starts, i) - 1
        return k >= 0 and spans[k][0] <= i < spans[k][1]
    tg = {}
    for (size, fl), b, cnt in L['inst']:
        for k in range(cnt):
            for o, kind in fl:
                a = b + k * size + o
                if kind != 0 or a + 4 > len(d): continue
                v = struct.unpack_from('<I', d, a)[0]
                if v and R + v < len(d) and not in_elem(R + v): tg.setdefault(R + v, (size, fl, o))
    ends = sorted(set(list(tg) + starts + [len(d)]))
    return [(t, ends[bisect.bisect_right(ends, t)], sig) for t, sig in sorted(tg.items())]

def count_layout(cnt, p, x, L):
    """adds the word classes of a (PC, 360) pair to cnt (dict blocks / words of Counters)"""
    import collections
    bl, wd = cnt.setdefault('blocks', {}), cnt.setdefault('words', {})
    for a, b, sig in layout_blocks(p, L):
        c = bl.setdefault(sig, collections.Counter())
        for w in range(a & ~3, b - 3, 4):
            k = _word_class(p[w:w + 4], x[w:w + 4])
            if k: c[k] += 1
    for (size, fl), b, n in L['inst']:
        ptr = {o for o, _ in fl}
        for o in range(0, size - 3, 4):
            if o in ptr: continue
            c = wd.setdefault((size, fl, o), collections.Counter())
            for k in range(n):
                w = b + k * size + o
                if w + 4 > len(p): break
                kk = _word_class(p[w:w + 4], x[w:w + 4])
                if kk: c[kk] += 1

def layout_rules(cnt, share=0.98, least=8):
    """the decisive classes: dict(blocks={sig: 'k' / 'W'}, words={(size, fields, offset): ...})"""
    out = {}
    for part in ('blocks', 'words'):
        r = {}
        for sig, c in cnt.get(part, {}).items():
            n = sum(c.values())
            for k in ('k', 'W'):
                if n >= least and c[k] >= share * n: r[sig] = k
        out[part] = r
    # (the element words the base game has enough of, decisive or not)
    out['seen'] = {sig for sig, c in cnt.get('words', {}).items() if sum(c.values()) >= least}
    return out

def apply_layout_rules(pc, x, L, rules):
    """x with the learned block and element word classes put in"""
    out = bytearray(x)
    def put(w, k):
        out[w:w + 4] = pc[w:w + 4] if k == 'k' else pc[w:w + 4][::-1]
    for a, b, sig in layout_blocks(pc, L):
        k = rules['blocks'].get(sig)
        if k == 'k': out[a:b] = pc[a:b]
        elif k == 'W':
            for w in range(a & ~3, b - 3, 4): put(w, k)
    for (size, fl), b, n in L['inst']:
        for o in range(0, size - 3, 4):
            k = rules['words'].get((size, fl, o))
            if not k: continue
            for i in range(n):
                w = b + i * size + o
                if w + 4 <= len(pc): put(w, k)
    return bytes(out)

def layout_post(pc, x, L, rules):
    """the last pass over a converted layout (after the relocation words are exact):
    - an element of 12 bytes with one pointer (a name, a flag byte, a count): the flag word is
      the PC bytes (a byte and its padding), unless the base game says otherwise for that type
    - outside the elements and the tables the game walks: a word that is not a reordering of
      the PC word's bytes (written across words by the passes before) is the PC bytes
    - a name outside the elements kept as text: the NULs after it up to the end of its word too"""
    import bisect
    out = bytearray(x)
    spans = sorted((b, b + c * s[0], s) for s, b, c in L['inst'])
    starts = [a for a, _, _ in spans]
    def elem(i):
        k = bisect.bisect_right(starts, i) - 1
        return spans[k] if k >= 0 and spans[k][0] <= i < spans[k][1] else None
    u32 = lambda a: struct.unpack_from('<I', pc, a)[0]
    u16 = lambda a: struct.unpack_from('<H', pc, a)[0]
    tables = set(range(R, R + 0x20, 4))
    tt, nt = R + u32(R + 0x10), u16(R + 8)
    for t in range(nt):
        e = tt + 16 * t
        tables |= set(range(e, e + 16, 4))
        blk, fld, nb, nf = R + u32(e), R + u32(e + 4), u32(e + 8), u16(e + 12)
        tables |= set(range(blk, blk + 12 * nb, 4)) | set(range(fld, fld + 4 * nf, 4))
    rl, nrel = R + u32(R + 0x14), u16(R + 0xA)
    tables |= set(range(rl, rl + 4 * nrel, 4))
    perms = lambda w: {bytes(w[i] for i in pm) for pm in ((0, 1, 2, 3), (3, 2, 1, 0), (1, 0, 3, 2), (1, 0, 2, 3), (0, 1, 3, 2))}
    flag = (12, ((0, 0),), 4)
    use_flag = flag not in rules.get('seen', ()) and flag not in rules.get('words', {})
    for w in range(R, len(pc) - 3, 4):
        if w in tables: continue
        e = elem(w)
        if e is not None:
            b, _, (size, fl) = e
            if use_flag and size == 12 and fl == ((0, 0),) and (w - b) % size == 4: out[w:w + 4] = pc[w:w + 4]
            continue
        if bytes(out[w:w + 4]) not in perms(pc[w:w + 4]): out[w:w + 4] = pc[w:w + 4]
    out = bytearray(keep_text(pc, bytes(out), L))
    for i in range(R + 4, len(pc) - 1):
        j = i + 1
        if out[i] == pc[i] and 32 <= pc[i] < 127 and pc[j] == 0 and j % 4 and not elem(i) and pc[i - 4:i].isascii():
            end = (j + 3) // 4 * 4
            if all(pc[t] == 0 for t in range(j, end)): out[j:end] = pc[j:end]
    return bytes(out)
