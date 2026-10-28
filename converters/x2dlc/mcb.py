# Motion control blocks (mcb members of the motion and event DataSet bundles): after the SEDB
# header, a chunk list - '@RST' (the resource offsets), '@RES' resources by name (motionC,
# RIDTBL, Effect, String, ClipGid, ...), '@ATT', '@CPT', '@ACT', '@TRK', '@BKT', '@BLK' - each
# a tag, its length in words and a u16, then its fields. A word's byte order on the 360 is set by
# where it is: the chunk, the resource name, the offset in the chunk (and for the records of
# '@BLK', the chunk length and its first record type). Learned from the base game's pairs, the
# most specific place first: 0 wrong words of 98,303 in the event blocks when each event's own
# blocks are left out of the learning. The String resource: its u16 offsets (as many bytes as
# the first one says), then the names, kept as text.
import re, struct
from .scb import PERMS, ORDER

R = 0x30
TAG = re.compile(rb'@[A-Z]{3}')

def fits(p, x):
    return [k for k in ORDER if bytes(p[i] for i in PERMS[k]) == x]

def places(d):
    """{word offset: [keys, the most specific first]} for the words after the header"""
    out = {}
    tag, at, res, clen, first = None, 0, '', 0, 0
    u16 = lambda a: struct.unpack_from('<H', d, a)[0] if a + 2 <= len(d) else 0
    rec = {}                  # '@BLK' record words: offset -> (type, size, offset in the record)
    for i in range(R, len(d) - 3, 4):
        if TAG.fullmatch(d[i:i + 4]):
            tag, at, clen, first = d[i:i + 4].decode(), i, u16(i + 4), u16(i + 20)
            if tag == '@RES':
                m = re.match(rb'[ -~]+', d[i + 8:i + 40])
                res = m.group().decode() if m else ''
            if tag == '@BLK':
                # after its header (24 bytes), records: a u16 size in bytes, ..., the type at +8
                r, end = i + 24, min(i + clen * 4, len(d))
                while r + 12 <= end:
                    size = u16(r)
                    if size < 12 or size % 4 or r + size > end: break
                    t = (u16(r + 8), d[r + 10])
                    for w in range(r, r + size, 4): rec[w] = (t, size, w - r)
                    r += size
        o = i - at
        r = res if tag == '@RES' else ''
        ks = []
        if tag == '@RES' and r == 'String':
            tbl = u16(at + 28)
            ks.append((tag, r, 'tbl' if 28 <= o < 28 + tbl else 'text' if o >= 28 + tbl else o))
        if tag == '@BLK' and i in rec:
            t, size, ro = rec[i]
            ks += [(tag, 'rec', t, size, ro), (tag, 'rec', t, ro) if ro < 12 or size <= 64 else (tag, 'rec', t, 'payload'),
                   (tag, 'rec', ro) if ro < 12 else (tag, 'rec', t, 'any')]
        else:
            ks += [(tag, r, o, clen, first), (tag, r, o, clen), (tag, r, o)]
        ks.append((tag, r, 'any'))
        out[i] = ks
    return out

def header_words(d):
    """the chunk tags and their length words (never text)"""
    out = set()
    for i in range(R, len(d) - 3, 4):
        if TAG.fullmatch(d[i:i + 4]): out |= {i, i + 4}
    return out

class Learner:
    def __init__(self):
        self.c = {}           # key -> {order: count, '#': words}
        self.pairs = 0
    def add(self, p, x):
        if len(p) != len(x) or p[:8] != b'SEDBMCB\0': return False
        for i, ks in places(p).items():
            f = fits(p[i:i + 4], x[i:i + 4])
            if not f or len(f) == len(ORDER): continue
            for k in ks:
                c = self.c.setdefault(k, {'#': 0})
                c['#'] += 1
                for kk in f: c[kk] = c.get(kk, 0) + 1
        self.pairs += 1
        return True
    def pick(self, ks):
        """the order every example of the most specific known place agrees on; else the
        commonest at the most specific place; None for a place never seen"""
        for k in ks:
            c = self.c.get(k)
            if c:
                best = max(ORDER, key=lambda kk: c.get(kk, 0))
                if c.get(best, 0) == c['#']: return best
        for k in ks:
            c = self.c.get(k)
            if c: return max(ORDER, key=lambda kk: c.get(kk, 0))
        return None
    def sure(self, ks):
        """the order every example agrees on at the most specific known place, else None"""
        for k in ks:
            c = self.c.get(k)
            if c:
                best = max(ORDER, key=lambda kk: c.get(kk, 0))
                return best if c.get(best, 0) == c['#'] else None
        return None
    def apply(self, pc, y, only_sure=False):
        """y (a conversion of pc) with every word at a known place in its learned order
        (only_sure: only where the examples at that place all agree; the rest as y has them)"""
        if not self.pairs or pc[:8] != b'SEDBMCB\0': return y
        out = bytearray(y)
        for i, ks in places(pc).items():
            k = self.sure(ks) if only_sure else self.pick(ks)
            if k: out[i:i + 4] = bytes(pc[i + j] for j in PERMS[k])
        return bytes(out)
