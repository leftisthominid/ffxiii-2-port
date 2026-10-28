# Endian conversion for formats without a field-level parser here (sound-table headers in .scd,
# motion ACB/MCB, vfx veff/leaf, trb BIBF).
# Model: the 360 file is the PC file with every field byte-reversed in place; fields are u8 pairs,
# u16 or u32/f32 at 2-byte alignment (u32 fields at odd half-word positions occur in the .scd
# tables). For every 2-byte position of an oracle pair (PC, 360) the field sizes consistent with
# the bytes are recorded; for a new PC file the evidence is transferred through a word alignment
# (equal-length replaced runs map position by position) with the most similar oracle
# files, and a dynamic program picks the field segmentation with the most support. Positions
# without evidence fall back to a value heuristic. The alignment is patience-style (words unique to
# both sides as anchors, longest increasing run, recursively; O(n log n)): difflib was quadratic on
# the large, repetitive members (minutes per file). This is a heuristic, not a parser; its
# leave-one-out accuracy on the Yuna data is measured by verify.py.
import bisect, struct

SIZES = (4, 2, 1)          # 1 = two single bytes (kept), 2 = u16, 4 = u32 (both reversed)

_MASK = {}
def _set(m):
    if m not in _MASK: _MASK[m] = frozenset(s for b, s in ((1, 1), (2, 2), (4, 4)) if m & b)
    return _MASK[m]

def _cands(p, x):
    """bytes, one per even byte offset: bit0 two u8 kept, bit1 u16 reversed, bit2 u32 reversed"""
    from array import array
    n = len(p)
    h = (n + 1) // 2
    pe = p + bytes(8); xe = x + bytes(8)
    def u16(b, rev):
        a = array('H', b[:2 * h]);
        if rev: a.byteswap()
        return a
    p16, x16, p16r = u16(pe, False), u16(xe, False), u16(pe, True)
    m1 = [1 if a == b else 0 for a, b in zip(p16, x16)]
    m2 = [2 if a == b else 0 for a, b in zip(p16r, x16)]
    m4 = [0] * h
    for off in (0, 2):                      # u32 fields starting at 4k and 4k+2
        cnt = (n - off) // 4
        if cnt <= 0: continue
        a = array('I', pe[off:off + 4 * cnt]); a.byteswap()
        b = array('I', xe[off:off + 4 * cnt])
        for k, (u, v) in enumerate(zip(a, b)):
            if u == v: m4[(off + 4 * k) // 2] = 4
    out = bytes(a | b | c for a, b, c in zip(m1, m2, m4))
    if n % 2:                                # last odd byte: only 'kept' makes sense
        out = out[:-1] + bytes([1 if x[n - 1] == p[n - 1] else 0])
    return out

def _lis(pairs):
    """longest increasing subsequence of pairs (i, j) sorted by i, increasing in j"""
    tails, tails_i, prev = [], [], [None] * len(pairs)
    for n, (_, j) in enumerate(pairs):
        k = bisect.bisect_left(tails, j)
        if k == len(tails): tails.append(j); tails_i.append(n)
        else: tails[k] = j; tails_i[k] = n
        prev[n] = tails_i[k - 1] if k else None
    out, n = [], tails_i[-1] if tails_i else None
    while n is not None:
        out.append(pairs[n]); n = prev[n]
    return out[::-1]

def align(a, b):
    """(ratio, opcodes) like difflib.SequenceMatcher(None, a, b): 'equal' and 'replace' runs
    (a replace run may have different lengths on the two sides)"""
    matches = []
    stack = [(0, len(a), 0, len(b))]
    while stack:
        a0, a1, b0, b1 = stack.pop()
        while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
            matches.append((a0, b0)); a0 += 1; b0 += 1
        while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
            a1 -= 1; b1 -= 1; matches.append((a1, b1))
        if a0 >= a1 or b0 >= b1: continue
        ca, cb = {}, {}
        for i in range(a0, a1): ca[a[i]] = -1 if a[i] in ca else i
        for j in range(b0, b1): cb[b[j]] = -1 if b[j] in cb else j
        anchors = sorted((i, cb[w]) for w, i in ca.items() if i >= 0 and cb.get(w, -1) >= 0)
        anchors = _lis(anchors)
        if not anchors: continue
        pa, pb = a0, b0
        for i, j in anchors:
            matches.append((i, j))
            stack.append((pa, i, pb, j)); pa, pb = i + 1, j + 1
        stack.append((pa, a1, pb, b1))
    matches.sort()
    ops, pa, pb, run = [], 0, 0, None
    for i, j in matches:
        if i != pa or j != pb:
            ops.append(('replace', pa, i, pb, j))
        if ops and ops[-1][0] == 'equal' and ops[-1][2] == i and ops[-1][4] == j:
            ops[-1] = ('equal', ops[-1][1], i + 1, ops[-1][3], j + 1)
        else:
            ops.append(('equal', i, i + 1, j, j + 1))
        pa, pb = i + 1, j + 1
    if pa < len(a) or pb < len(b): ops.append(('replace', pa, len(a), pb, len(b)))
    ratio = 2.0 * len(matches) / ((len(a) + len(b)) or 1)
    return ratio, ops

def sketch(b, k=256):
    """bottom-k hash sketch of the 4-byte words (cheap similarity prefilter)"""
    hs = {hash(b[i:i + 4]) for i in range(0, len(b) - 3, 4)}
    return frozenset(sorted(hs)[:k])

def words(b):
    return [bytes(b[i:i + 4]) for i in range(0, len(b) - 3, 4)]

class Oracle:
    def __init__(self, pairs):
        self.items = []
        self._x = {}
        seen = set()
        for p, x in pairs:
            if len(p) != len(x) or p in seen: continue
            seen.add(p)
            it = (p, _cands(p, x), sketch(p), p[:8])
            self.items.append(it)
            self._x[it] = x

    def convert(self, t, header_fix=None, exclude=None, top=4, prefilter=10, fixed=()):
        """fixed: byte ranges rewritten afterwards by the caller (not counted as uncertain)"""
        if header_fix is sedb_header and t[:4] == b'SEDB': fixed = tuple(fixed) + ((0, 0x18),)
        n2 = (len(t) + 1) // 2
        votes = [dict() for _ in range(n2)]          # half-word index -> {size: weight}
        hard = [dict() for _ in range(n2)]           # size -> weight of unambiguous oracle votes
        hardtop = [set() for _ in range(n2)]         # unambiguous sizes from the best oracle
        for it in self.items:                        # the same PC bytes seen before: its 360 bytes
            if it[0] == t and exclude is None:
                out = bytearray(self._x[it]); 
                if header_fix: header_fix(t, out)
                return bytes(out), dict(halfwords=n2, supported=n2, guessed=0, uncertain=0, identical_oracle=True)
        tw = words(t)
        tset = sketch(t)
        pool = [it for it in self.items if not (exclude is not None and it[0] == exclude)]   # leave-one-out
        same = [it for it in pool if it[3] == t[:8]]
        pool = same or pool
        if len(pool) > prefilter:                    # cheap sketch similarity first
            pool = sorted(pool, key=lambda it: -len(tset & it[2]) / (len(tset | it[2]) or 1))[:prefilter]
        cands_list = []
        for p, cands, _, _ in pool:
            ratio, ops = align(words(p), tw)
            cands_list.append((ratio, ops, cands, len(p)))
        cands_list.sort(key=lambda c: -c[0])
        for rank, (ratio, ops, cands, plen) in enumerate(cands_list[:top]):
            wgt = ratio * (3.0 if rank == 0 else 1.0)
            for op, i1, i2, j1, j2 in ops:
                if op == 'equal' or (op == 'replace' and i2 - i1 == j2 - j1):
                    for d in range(j2 - j1):
                        for h in (0, 1):
                            oi, ti = 2 * (i1 + d) + h, 2 * (j1 + d) + h
                            if oi >= len(cands) or ti >= n2: continue
                            cs = _set(cands[oi])
                            if not cs: continue
                            v = votes[ti]
                            for s in cs: v[s] = v.get(s, 0) + wgt / len(cs)
                            if len(cs) == 1:
                                (s1,) = cs
                                hard[ti][s1] = hard[ti].get(s1, 0) + wgt
                                if rank == 0: hardtop[ti].add(s1)
        # dynamic program over half-words: best segmentation by total support
        best = [0.0] * (n2 + 1)
        choice = [None] * (n2 + 1)
        for i in range(n2 - 1, -1, -1):
            opts = []
            for s in SIZES:
                span = 2 if s == 4 else 1
                if i + span > n2: continue
                sc = votes[i].get(s, 0.0) * span          # support per covered half-word
                # tiny prior: u32 at 4-byte aligned positions, else u16
                prior = 1e-6 * (3 if (s == 4 and (2 * i) % 4 == 0) else 2 if s == 2 else 1)
                opts.append((sc + prior + best[i + span], s, span))
            sc, s, span = max(opts)
            best[i] = sc; choice[i] = (s, span, votes[i].get(s, 0.0) > 0)
        out = bytearray(t)
        stats = dict(halfwords=n2, supported=0, guessed=0, uncertain=0, uncertain_at=[])
        unsup = bytearray(n2)
        def outp(i, s):
            b = 2 * i
            return bytes(t[b:b + 4][::-1]) if s == 4 else bytes(t[b:b + 2][::-1]) if s == 2 else bytes(t[b:b + 2])
        # certainty: the chosen field must be pinned by an unambiguous oracle vote or every voted
        # size must give the same bytes, and no unambiguous vote may give different bytes
        i = 0
        while i < n2:
            s_, span, sup = choice[i]
            mine = outp(i, s_)
            alts = {outp(i, s2)[:len(mine)] for s2 in votes[i]} if votes[i] else set()
            top_out = {outp(i, s2)[:len(mine)] for s2 in hardtop[i]}
            sup_w = sum(w for s2, w in hard[i].items() if outp(i, s2)[:len(mine)] == mine)
            con_w = sum(w for s2, w in hard[i].items() if outp(i, s2)[:len(mine)] != mine)
            certain = (sup and (s_ in hardtop[i] or alts == {mine}) and top_out <= {mine}
                       and con_w <= 0.5 * sup_w)
            if not certain and not any(a <= 2 * i < b for a, b in fixed):
                stats['uncertain'] += span
                stats['uncertain_at'].extend(range(2 * i, 2 * (i + span), 2))
            i += span
        i = 0
        while i < n2:
            s, span, sup = choice[i]
            b = 2 * i
            if sup:
                if s == 4: out[b:b + 4] = t[b:b + 4][::-1]
                elif s == 2: out[b:b + 2] = t[b:b + 2][::-1]
                stats['supported'] += span
            else:
                for k in range(span): unsup[i + k] = 1
            i += span
        # positions without evidence: decide per aligned 4-byte word by value
        w = 0
        while 4 * w < len(t):
            h = 2 * w
            if any(unsup[h:h + 2]):
                b = 4 * w
                if all(unsup[h:h + 2]) and b + 4 <= len(t):
                    out[b:b + 4] = guess(t[b:b + 4])
                    stats['guessed'] += 2
                else:
                    for k in (0, 1):
                        if h + k < n2 and unsup[h + k]:
                            bb = 2 * (h + k)
                            out[bb:bb + 2] = t[bb:bb + 2][::-1]
                            stats['guessed'] += 1
            w += 1
        if header_fix: header_fix(t, out)
        return bytes(out), stats

def _is_float(v):
    e = (v >> 23) & 0xFF
    return v == 0 or 0x60 <= e <= 0x9F

def guess(w):
    """no oracle evidence: text stays, small u16 pairs swap as u16, otherwise u32."""
    if all(32 <= c < 127 for c in w): return bytes(w)
    v = struct.unpack('<I', w)[0]
    lo, hi = v & 0xFFFF, v >> 16
    if 0xFF00 <= lo and hi < 0x100: return bytes(w[1::-1] + w[3:1:-1])   # (negative i16, small u16)
    if _is_float(v) or v < 0x10000: return bytes(w[::-1])
    if lo < 0x1000 and hi < 0x1000: return bytes(w[1::-1] + w[3:1:-1])
    return bytes(w[::-1])

def sedb_header(t, out):
    """SEDB resource header: u32 version, u8 endian flag (0 PC, 1 360), u8, u16 header size, u64 size."""
    if t[:4] != b'SEDB': return
    out[0x0:0x8] = t[0x0:0x8]
    out[0x8:0xC] = t[0x8:0xC][::-1]
    out[0xC] = 1
    out[0xD] = t[0xD]
    out[0xE:0x10] = t[0xE:0x10][::-1]
    out[0x10:0x18] = t[0x10:0x18][::-1]
    out[0x18:0x20] = t[0x18:0x20][::-1]    # XIII-2: a u64 (zero in most kinds)
