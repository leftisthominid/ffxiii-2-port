# Cutscene schedules (SEDBSCB, the '!!cutsch' member of the event bundles), PC -> 360.
#   After the 0x30-byte SEDB header: a tree of chunks, each 16-byte aligned:
#     tag (6 bytes, '@CRST\0'), u16 size in 16-byte units (the header included), u8 flag,
#     u8 byte order (PC 0, 360 1), u16 count, 4 bytes 0; then the payload, or the child chunks
#     when the payload starts with a chunk header (@CBKT: @CBLK, @CCNT: @CBIS).
#   @CRES chunks carry whole SEDB files (camera curves CCB, motion tables mtb, light models
#   RES), converted as the files they are.
# The chunk headers are converted by rule. The other words: the byte order each 4-byte word
# takes on the 360 (kept, u32, two u16, u16 + 2 bytes, 2 bytes + u16), learned from the
# base game's schedules with the word's context (its chunk, the zero / nonzero shape of it and
# of its neighbours, its value), the most specific context seen deciding.
import struct, collections

R = 0x30
PERMS = {'k': (0, 1, 2, 3), 'W': (3, 2, 1, 0), 'hh': (1, 0, 3, 2), 'h.': (1, 0, 2, 3), '.h': (0, 1, 3, 2)}
ORDER = ('k', 'hh', 'W', 'h.', '.h')       # ties: the commonest first

def _is_hdr(d, i):
    return d[i:i + 2] == b'@C' and len(d) >= i + 16 and d[i + 5] == 0 and all(65 <= c <= 90 for c in d[i + 2:i + 5])

def chunks(d, E='<'):
    """-> (headers [(offset, tag)], embedded SEDB spans [(start, end)], owner tag per 16-byte row)"""
    hdrs, emb = [], []
    owner = {}
    def walk(start, end, parent):
        i = start
        while i + 16 <= end:
            if not _is_hdr(d, i): return
            sz = struct.unpack_from(E + 'H', d, i + 6)[0] * 16
            if sz < 16 or i + sz > end: return
            tag = d[i + 1:i + 5].decode('latin1')
            hdrs.append((i, tag))
            for r in range(i, i + sz, 16): owner[r] = tag
            if sz > 16 and _is_hdr(d, i + 16):
                walk(i + 16, i + sz, tag)
            elif tag == 'CRES':
                j = i + 16
                while j + 0x30 <= i + sz:
                    if d[j:j + 4] == b'SEDB':
                        n = struct.unpack_from(E + 'I', d, j + 0x10)[0]
                        if 0x30 <= n <= i + sz - j:
                            emb.append((j, j + n)); j += (n + 15) // 16 * 16; continue
                    j += 16
            i += sz
    walk(R, len(d), None)
    return hdrs, emb, owner

def pc_kind(d, a):
    """the SEDB kind of an embedded file ('CCB', 'mtb', 'RES')"""
    return d[a + 4:a + 8].rstrip(b'\0 ').decode('latin1')

def _shape(w):
    return ''.join('0' if b == 0 else ('a' if 32 <= b < 127 else 'n') for b in w)

def _fits(p, x):
    return [k for k in ORDER if bytes(p[i] for i in PERMS[k]) == x]

def _keys(d, i, tag, cstart):
    w = d[i:i + 4]
    prv = d[i - 4:i] if i >= 4 else b''
    nxt = d[i + 4:i + 8]
    rel = i - cstart
    return [('s5', tag, rel if rel < 0x40 else -1, _shape(prv), _shape(w), _shape(nxt)),
            ('v1', tag, w), ('s3', tag, _shape(prv), _shape(w), _shape(nxt)), ('s1', tag, _shape(w)), ('s0', _shape(w))]

def records(d, i, sz, E='<'):
    """the timeline records of a @CBLK at i (size sz): [(offset, length)]. Each: u16 length, u8,
    u8, u32, u16 kind, u8 class, u8, u16, u16, then its data."""
    out = []
    j = i + 0x40
    while j + 16 <= i + sz:
        n = struct.unpack_from(E + 'H', d, j)[0]
        if n < 16 or n % 4 or j + n > i + sz: break
        out.append((j, n)); j += n
    return out

# the block header (after the chunk header) and the record header, by rule (all 2628 records
# and 142 blocks of the base-game samples): byte offsets -> order
BLOCK_HEAD = {0x24: 'W'}                    # 0x10 name (20 bytes), 0x24 u32, 0x28 u16
RECORD_HEAD = {0: 'h.', 4: 'W', 8: 'h.', 12: 'h.'}

def _rkeys(d, j, n, o):
    code = struct.unpack_from('<H', d, j + 8)[0]
    w = d[j + o:j + o + 4]
    # a small word before this one is a value type (2: u32, 0x7F3: u16 + 2 bytes in code 0x6D / 0x88)
    t = struct.unpack_from('<I', d, j + o - 4)[0] if o >= 20 else None
    # header byte 2: the record's type; with the class, length and offset it decides the layout
    # (99.6% of the record words in the pairs; the rest by the value type word before them). The
    # code is an id that differs between files.
    rt = d[j + 2]
    ks = []
    if t and t <= 0xFFFF:
        # a value whose layout its type word decides: not by the record type alone (a type not seen
        # with this record falls to the general contexts)
        ks += [('rt5', rt, d[j + 10], n, o, t), ('r5b', d[j + 10], n, o, t), ('r5', code, d[j + 10], n, o, t)]
    else:
        ks += [('rt', rt, d[j + 10], n, o)]
    ks += [('r4', code, d[j + 10], n, o)]
    return ks + [('r3', d[j + 10], n, o), ('r2', d[j + 10], o), ('r1', _shape(w))]

def text_words(d, start=R):
    """word offsets inside NUL-terminated names (5+ printable bytes, then NULs up to the word end):
    kept as they are"""
    out = set()
    i, n = start, len(d)
    while i < n:
        if 32 <= d[i] < 127 and (i == start or d[i - 1] == 0 or i % 4 == 0):
            j = i
            while j < n and 32 <= d[j] < 127: j += 1
            # 5+ characters: a name (a run within one word can be a number or a type tag)
            if j - i >= 5 and j < n and d[j] == 0:
                end = j + 1
                while end < n and end % 4 and d[end] == 0: end += 1
                # the word the name starts in: only when the bytes before the name are NULs (an
                # actor entry header '74 00 00 25' before the name 'f575a' is not text)
                w0 = i - i % 4 if not any(d[i - i % 4:i]) else i - i % 4 + 4
                for w in range(w0, end, 4):
                    if all(32 <= c < 127 or c == 0 for c in d[w:w + 4]): out.add(w)
            i = max(j, i + 1)
        else:
            i += 1
    return out

def _words(d):
    """(word offset, chunk tag, chunk start) of every word outside the chunk headers and embedded files"""
    hdrs, emb, owner = chunks(d)
    hset = {o for o, _ in hdrs}
    starts = sorted(o for o, _ in hdrs)
    inemb = {}
    for a, b in emb:
        kind = 'E' + pc_kind(d, a)
        for j in range(a, b, 4): inemb[j] = (kind, a)
    out = []
    cur, cstart = 'top', R
    hi = 0
    for i in range(R, len(d) - 3, 4):
        r = i - i % 16
        if r in hset:
            if i == r:
                cur = owner.get(r, 'top'); cstart = r
            continue
        if i in inemb:
            out.append((i,) + inemb[i]); continue
        out.append((i, owner.get(r, 'top'), cstart))
    return out, hdrs, emb

def _block_words(d, hdrs):
    """word offset -> ('B', rule) / ('R', j, n, o) for the @CBLK headers and records"""
    out = {}
    for i, tag in hdrs:
        if tag != 'CBLK': continue
        sz = struct.unpack_from('<H', d, i + 6)[0] * 16
        if sz < 0x40: continue
        for o in range(0x10, 0x40, 4): out[i + o] = ('B', BLOCK_HEAD.get(o - 0x10 + 0x10, None))
        # (0x24 / 0x28 relative to the chunk start)
        out[i + 0x24] = ('B', 'W'); out[i + 0x28] = ('B', 'h.')
        for j, n in records(d, i, sz):
            for o in range(0, 16, 4): out[j + o] = ('B', RECORD_HEAD[o])
            for o in range(16, n, 4): out[j + o] = ('R', j, n, o)
            # 24-byte records starting 0x8001: u16 0x8001, u16, u16 count, u16 (all 268 in the pairs)
            if n == 24 and d[j + 16:j + 20] == b'\x01\x80\0\0':
                out[j + 16] = ('B', 'hh'); out[j + 20] = ('B', 'hh')
            # class 160, 64 bytes: the last three words are bytes (all 441 in the pairs)
            if n == 64 and d[j + 10] == 160:
                for o in (52, 56, 60): out[j + o] = ('B', 'k')
    # @CRES resource id table: 'RIDI', u16, u16, u32 size, u16 count, then count u32 type codes
    # ('mtb\0', 'sscf'; text-like, so by rule)
    for i, tag in hdrs:
        if tag != 'CRES': continue
        sz = struct.unpack_from('<H', d, i + 6)[0] * 16
        name = d[i + 16:i + 32].split(b'\0')[0]
        # clip ids, elements: u32 lists (all 6,368 words of the pairs); track properties: u16 pairs
        if name in (b'ClipGid', b'Element'):
            for w in range(i + 32, i + sz, 4): out[w] = ('B', 'W')
        elif name == b'CTrkPro':
            for w in range(i + 32, i + sz, 4): out[w] = ('B', 'hh')
        for k in range(i + 16, i + sz - 15, 16):
            if d[k:k + 6] == b'RIDT\0\0':      # the name table's header (count u16)
                out[k] = ('B', 'k'); out[k + 4] = ('B', 'hh'); out[k + 8] = ('B', 'W'); out[k + 12] = ('B', 'h.')
            if d[k:k + 6] != b'RIDI\0\0': continue
            cnt = struct.unpack_from('<H', d, k + 12)[0]
            if k + 16 + 4 * cnt > i + sz: continue
            out[k] = ('B', 'k'); out[k + 4] = ('B', 'hh'); out[k + 8] = ('B', 'W'); out[k + 12] = ('B', 'h.')
            for w in range(cnt): out[k + 16 + 4 * w] = ('B', 'W')
    # embedded motion tables ('SEDBmtb', no size in their header): the same bytes on the 360 (all
    # 179 in the pairs). Size: the u32 of the '@FCMT' row before them, or of the row before that
    # (after the name)
    i = 0
    while True:
        i = d.find(b'SEDBmtb\0', i + 1)
        if i < 0: break
        if i % 16 or i < 32: continue
        if d[i - 16:i - 10] == b'@FCMT\0': n = struct.unpack_from('<I', d, i - 8)[0]
        else: n = struct.unpack_from('<I', d, i - 16)[0]
        if not 0x30 <= n <= len(d) - i: continue
        for w in range(i, i + n - n % 4, 4): out[w] = ('B', 'k')
    return out

IDX = {f: n for n, f in enumerate(ORDER)}

class Learner:
    def __init__(self):
        self.c = {}                          # context -> counts per order (ORDER)
    def add(self, p, x):
        if len(p) != len(x) or p[:4] != b'SEDB': return False
        words, hdrs, emb = _words(p)
        if not hdrs: return False
        bw = _block_words(p, hdrs)
        for i, tag, cs in words:
            fits = _fits(p[i:i + 4], x[i:i + 4])
            if not fits: continue
            b = bw.get(i)
            keys = _rkeys(p, b[1], b[2], b[3]) if b and b[0] == 'R' else _keys(p, i, tag, cs)
            for k in keys:
                a = self.c.get(k)
                if a is None: a = self.c[k] = [0, 0, 0, 0, 0]
                for f in fits: a[IDX[f]] += 1
        return True
    def pick(self, d, i, tag, cs, b=None, text=()):
        if b and b[0] == 'B': return b[1] or 'k'
        if i in text and not (b and b[0] == 'R'): return 'k'
        cands = list(ORDER)
        for k in (_rkeys(d, b[1], b[2], b[3]) if b else _keys(d, i, tag, cs)):
            cnt = self.c.get(k)
            if not cnt: continue
            tot = max(cnt[IDX[f]] for f in cands)
            if not tot: continue
            best = [f for f in cands if cnt[IDX[f]] == tot]
            if len(best) == 1: return best[0]
            # several orders fit every example (zero or equal bytes): a more general context
            # decides between those
            cands = best
        return cands[0]

def convert(pc, learner, member=None, header=None):
    """PC schedule -> 360 (None when it does not parse as one). member(sedb bytes) -> 360 bytes for
    the embedded files (or None: learned words)."""
    words, hdrs, emb = _words(pc)
    if not hdrs: return None
    out = bytearray(pc)
    if header: header(pc, out)
    for o, tag in hdrs:
        struct.pack_into('>H', out, o + 6, struct.unpack_from('<H', pc, o + 6)[0])
        out[o + 9] = 1
        struct.pack_into('>H', out, o + 10, struct.unpack_from('<H', pc, o + 10)[0])
    bw = _block_words(pc, hdrs)
    text = text_words(pc)
    for i, tag, cs in words:
        f = learner.pick(pc, i, tag, cs, bw.get(i), text)
        out[i:i + 4] = bytes(pc[i + j] for j in PERMS[f])
    # track properties: an entry (id, 0x7FF) is (id, 0, 0x7FF, 0) on the 360, over two words (all
    # in the pairs)
    for o, tag in hdrs:
        if tag != 'CRES' or pc[o + 16:o + 32].split(b'\0')[0] != b'CTrkPro': continue
        sz = struct.unpack_from('<H', pc, o + 6)[0] * 16
        for w in range(o + 32, o + sz - 4, 4):
            lo, hi = struct.unpack_from('<HH', pc, w)
            if hi == 0x7FF and lo and pc[w + 4:w + 8] == bytes(4):
                out[w:w + 8] = struct.pack('>HHHH', lo, 0, 0x7FF, 0)
    for a, b in emb:
        y = member(pc[a:b]) if member else None
        if y is not None and len(y) == b - a: out[a:b] = y
        elif header and pc_kind(pc, a) == 'CCB':
            # camera curves: the SEDB header (u64 size), byte 0xC 0 on the 360 (all 54 in the pairs)
            h = bytearray(out[a:a + 0x20]); header(pc[a:a + 0x20], h); h[0xC] = 0
            out[a:a + 0x20] = h
    return bytes(out)

# ---- the same word-order learning for other members (lyb: the words outside the typed elements) ----
def _wkeys(d, i, kind):
    w = d[i:i + 4]
    prv = d[i - 4:i] if i >= 4 else b''
    nxt = d[i + 4:i + 8]
    return [('v1', kind, w), ('s3', kind, _shape(prv), _shape(w), _shape(nxt)), ('s1', kind, _shape(w)), ('s0', _shape(w))]

class Words:
    """word orders of one member kind by context (value, zero / nonzero shape of it and its
    neighbours), learned from base-game pairs at the word offsets given"""
    def __init__(self, kind):
        self.kind, self.c = kind, {}
    def add(self, p, x, offsets):
        for i in offsets:
            if i + 4 > len(p): continue
            fits = _fits(p[i:i + 4], x[i:i + 4])
            if not fits: continue
            for k in _wkeys(p, i, self.kind):
                a = self.c.get(k)
                if a is None: a = self.c[k] = [0, 0, 0, 0, 0]
                for f in fits: a[IDX[f]] += 1
    min_support = 1
    def pick(self, d, i):
        cands = list(ORDER)
        for k in _wkeys(d, i, self.kind):
            cnt = self.c.get(k)
            if not cnt: continue
            tot = max(cnt[IDX[f]] for f in cands)
            if not tot: continue
            best = [f for f in cands if cnt[IDX[f]] == tot]
            if len(best) == 1: return best[0] if tot >= self.min_support else None
            cands = best
        return None
    def apply(self, d, x, offsets):
        out = bytearray(x)
        for i in offsets:
            if i + 4 > len(d): continue
            f = self.pick(d, i)
            if f: out[i:i + 4] = bytes(d[i + j] for j in PERMS[f])
        return bytes(out)
