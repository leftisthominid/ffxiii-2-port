# Motion/action blocks (mcb/acb members of motion and event DataSet bundles): after the SEDB
# header, a chunk list - '@RST' (the resource offsets), '@RES' resources by name (motionC,
# RIDTBL, Effect, String, ClipGid, ...), '@ATT', '@CPT', '@ACT', '@TRK', '@BKT', '@BLK' - each
# a tag, its length in eight-byte units and a u16, then its fields. A word's byte order is set by
# where it is: the chunk, the resource name, the offset in the chunk (and for the records of
# '@BLK' and '@ACT', the record type, length, and field offset). Learned from base-game pairs,
# most specific place first. Fixed layouts cover record headers, String offset tables,
# resource type codes, and expanded CTrkPro masks. String tables start at resource +24.
import re, struct
from .scb import PERMS as SCB_PERMS, ORDER as SCB_ORDER

# Some packed record fields are u8/u16/u8, so their halfword starts at byte one.
PERMS = dict(SCB_PERMS, middle=(0, 2, 1, 3))
ORDER = SCB_ORDER + ('middle',)

R = 0x30
TAG = re.compile(rb'@[A-Z]{3}')

def fits(p, x):
    return [k for k in ORDER if bytes(p[i] for i in PERMS[k]) == x]

def places(d):
    """{word offset: [keys, the most specific first]} for the words after the header"""
    out = {}
    tag, at, res, clen, first = None, 0, '', 0, 0
    u16 = lambda a: struct.unpack_from('<H', d, a)[0] if a + 2 <= len(d) else 0
    rec = {}                  # record words: offset -> (type, size, offset in the record)
    for i in range(R, len(d) - 3, 4):
        if TAG.fullmatch(d[i:i + 4]):
            tag, at, clen, first = d[i:i + 4].decode(), i, u16(i + 4), u16(i + 20)
            if tag == '@RES':
                m = re.match(rb'[ -~]+', d[i + 8:i + 40])
                res = m.group().decode() if m else ''
            if tag in ('@BLK', '@ACT'):
                # BLK records follow 24 header bytes; actor records follow eight.
                r, end = i + (24 if tag == '@BLK' else 8), min(i + clen * 8, len(d))
                minimum = 12 if tag == '@BLK' else 8
                while r + minimum <= end:
                    size = u16(r)
                    if size < minimum or size % 4 or r + size > end: break
                    t = (u16(r + 8), d[r + 10]) if tag == '@BLK' else (u16(r + 4), d[r + 6])
                    for w in range(r, r + size, 4): rec[w] = (t, size, w - r)
                    r += size
        o = i - at
        r = res if tag == '@RES' else ''
        ks = []
        if tag == '@RES' and r == 'String':
            tbl = u16(at + 24)
            ks.append((tag, r, 'tbl' if 24 <= o < 24 + tbl else 'text' if o >= 24 + tbl else o))
        if tag in ('@BLK', '@ACT') and i in rec:
            t, size, ro = rec[i]
            ks += [(tag, 'rec', t, size, ro, d[i:i + 4]), (tag, 'rec', t, ro, d[i:i + 4])]
            if tag == '@BLK' and t == (3, 160) and size >= 20 and ro >= 20:
                # These command records have numeric parameters or inline
                # names, distinguished by the selector at record +16.
                # Mixing the two makes a single text example erase otherwise
                # unanimous evidence for a packed u8/u16/u8 numeric field.
                selector = d[i - ro + 16]
                ks.append((tag, 'rec', t, size, 'variant', selector, ro))
            ks += [(tag, 'rec', t, size, ro), (tag, 'rec', t, ro) if tag == '@ACT' or ro < 12 or size <= 64 else (tag, 'rec', t, 'payload'),
                   (tag, 'rec', ro) if ro < 12 else (tag, 'rec', t, 'any')]
            if tag == '@ACT' and ro >= 12: ks.pop()  # no untyped fallback for actor references
            # Preserve the chunk-position evidence when a record layout is unseen.
            ks += [(tag, r, o, clen, first), (tag, r, o, clen), (tag, r, o)]
        else:
            ks += [(tag, r, o, clen, first), (tag, r, o, clen), (tag, r, o)]
        if not (tag == '@ACT' and i in rec and rec[i][2] >= 12): ks.append((tag, r, 'any'))
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
        if len(p) != len(x) or p[:8] != x[:8] or p[:8] not in (b'SEDBMCB\0', b'SEDBACB\0'): return False
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
        if not self.pairs or pc[:8] not in (b'SEDBMCB\0', b'SEDBACB\0'): return y
        out = bytearray(y)
        for i, ks in places(pc).items():
            k = self.sure(ks) if only_sure else self.pick(ks)
            if k: out[i:i + 4] = bytes(pc[i + j] for j in PERMS[k])
        return structural(pc, out)

def structural(pc, y):
    """String offset tables and expanded track properties have defined field layouts."""
    if len(pc) != len(y): return y
    out = bytearray(y)
    for i in range(R, len(pc) - 7, 4):
        tag = pc[i:i + 4]
        if tag not in (b'@ACT', b'@BLK'): continue
        end = i + struct.unpack_from('<H', pc, i + 4)[0] * 8
        if end > len(pc): continue
        at = i + (8 if tag == b'@ACT' else 24)
        while at + 8 <= end:
            size = struct.unpack_from('<H', pc, at)[0]
            if size < 8 or size % 4 or at + size > end: break
            out[at:at + 2] = pc[at:at + 2][::-1]
            out[at + 2:at + 4] = pc[at + 2:at + 4]
            if tag == b'@BLK' and size >= 12:
                out[at + 4:at + 8] = pc[at + 4:at + 8][::-1]
                out[at + 8:at + 10] = pc[at + 8:at + 10][::-1]
                out[at + 10:at + 12] = pc[at + 10:at + 12]
            elif tag == b'@ACT' and size == 8:
                out[at + 4:at + 6] = pc[at + 4:at + 6][::-1]
                out[at + 6:at + 8] = pc[at + 6:at + 8]
            at += size
    for i in range(R, len(pc) - 27, 4):
        if pc[i:i + 4] != b'@RES': continue
        end = i + struct.unpack_from('<H', pc, i + 4)[0] * 8
        if end > len(pc) or end < i + 26: continue
        name = pc[i + 8:i + 16].split(b'\0')[0]
        if name == b'String':
            length = struct.unpack_from('<H', pc, i + 24)[0]
            if length < 2 or length % 2 or i + 24 + length > end: continue
            for at in range(i + 24, i + 24 + length, 2):
                out[at:at + 2] = pc[at:at + 2][::-1]
            out[i + 24 + length:end] = pc[i + 24 + length:end]
        elif name == b'CTrkPro':
            for at in range(i + 24, end - 7, 4):
                lo, hi = struct.unpack_from('<HH', pc, at)
                if hi in (0xFF, 0x7FF) and pc[at + 4:at + 8] == bytes(4):
                    out[at:at + 8] = struct.pack('>HHHH', lo, 0, hi, 0)
        elif name == b'RIDTBL':
            for at in range(i + 24, end - 15, 4):
                if pc[at:at + 6] not in (b'RIDI\0\0', b'RIDT\0\0'): continue
                for off, order in ((0, 'k'), (4, 'hh'), (8, 'W'), (12, 'h.')):
                    out[at + off:at + off + 4] = bytes(pc[at + off + j] for j in PERMS[order])
                if pc[at:at + 4] == b'RIDI':
                    count = struct.unpack_from('<H', pc, at + 12)[0]
                    if at + 16 + 4 * count <= end:
                        for off in range(at + 16, at + 16 + 4 * count, 4):
                            out[off:off + 4] = pc[off:off + 4][::-1]
    return bytes(out)

def resource_words(pc):
    """Only resource chunks contain text; binary record payloads may look printable."""
    out = set()
    for i in range(R, len(pc) - 7, 4):
        if pc[i:i + 4] != b'@RES': continue
        end = i + struct.unpack_from('<H', pc, i + 4)[0] * 8
        if i + 8 <= end <= len(pc): out.update(range(i + 8, end, 4))
    return out


def preserve_embedded_mtb(pc, converted):
    """FCurve records own little-endian MTB blobs normalized by the game.

    Require the named, aligned record, its bounded length and valid section
    offsets. Learned action-block word rules must not partially swap its MTB.
    """
    if len(pc) != len(converted) or pc[:8] not in (b'SEDBACB\0', b'SEDBMCB\0'):
        return converted
    out = bytearray(converted)
    start = 0
    while True:
        at = pc.find(b'SEDBmtb\0', start)
        if at < 0: break
        start = at + 8
        if at < R + 32 or at % 16 or at + 0x40 > len(pc): continue
        if pc.rfind(b'@FCURVE\0', R, at - 32) < 0: continue
        name = pc[at - 32:at - 16].split(b'\0', 1)[0]
        if not re.fullmatch(rb'FCurve_[A-Za-z0-9_]+', name): continue
        size = struct.unpack_from('<I', pc, at - 16)[0]
        if size < 0x40 or at + size > len(pc): continue
        table = struct.unpack_from('<H', pc, at + 14)[0]
        if table < 0x30 or table + 4 > size: continue
        count = struct.unpack_from('<I', pc, at + table)[0]
        if count > 256 or table + 4 + count * 4 > size: continue
        offsets = struct.unpack_from('<' + 'I' * count, pc, at + table + 4)
        if any(offset + 4 > size for offset in offsets): continue
        out[at:at + size] = pc[at:at + size]
    return bytes(out)
