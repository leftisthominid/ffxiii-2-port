# SEDBRES container (.trb), the same little-endian layout on PC and 360:
#   0x00 'SEDBRES ', 0x10 u32 total size, 0x30 u32 n, 0x34 u32 name-table offset, 0x38 u32 name count (= n), 0x3C 'brt\0'
#   0x40 n x {u32 index, u32 offset, u32 size, u32 log2 alignment}   (offsets relative to 0x40 + 16n)
#   resources (absolute-file alignment), then the two table resources:
#     RESOURCE_TYPE: n x 4-byte type tag (reversed, e.g. 'bxt\0' = txb; 0xFFFFFFFF for the tables)
#     RESOURCE_ID:   (16-byte aligned) n x 16-byte id, directly followed by the name strings ('F0A\\c451A_01.win32', ...)
#   360 files are zero-padded to a 2048-byte multiple; the header size field excludes that padding.
import struct
from .util import align

class Trb:
    def __init__(self, data):
        d = data
        assert d[:8] == b'SEDBRES ', d[:8]
        self.header = bytearray(d[:0x40])
        n, nt = struct.unpack_from('<II', d, 0x30)
        base = 0x40 + 16 * n
        table = [struct.unpack_from('<IIII', d, 0x40 + 16 * i) for i in range(n)]
        self.index = [t[0] for t in table]
        self.align = [t[3] for t in table]
        self.decl = [t[2] for t in table]
        self.res = [d[base + o:base + o + s] for (_, o, s, _) in table[:n - 2]]
        to, io = table[n - 2][1], table[n - 1][1]
        assert io == align(base + to + 4 * n, 16) - base and nt == io + 16 * n, 'unexpected table layout'
        self.types = [d[base + to + 4 * i:base + to + 4 * i + 4] for i in range(n)]
        self.ids = d[base + io:base + io + 16 * n]
        end = struct.unpack_from('<I', d, 0x10)[0]
        self.names = d[base + nt:end].split(b'\0')[:-1]

    def type_of(self, i):
        return self.types[i][::-1].strip(b'\0').decode('latin1')

    def name_map(self):
        """resource index (as in the table) -> name without platform suffix"""
        out = {}
        for s in self.names:
            s = s.decode('latin1')
            if s.startswith('F') and '\\' in s:
                k, v = s[1:].split('\\', 1)
                try: out[int(k, 16)] = v.rsplit('.', 1)[0] if v.endswith(('.win32', '.x360')) else v
                except ValueError: pass
        return out

    def build(self, x360=True, pad=True):
        pad_file = pad
        n = len(self.index)
        hdr = self.header
        base = 0x40 + 16 * n
        body = bytearray()
        offs = []
        for i, r in enumerate(self.res):
            a = 1 << self.align[i]
            pad = align(base + len(body), a) - (base + len(body))
            body += bytes(pad)
            offs.append(len(body))
            body += r
        to = align(base + len(body), 4) - base         # type table: 4-byte aligned (absolute)
        body += bytes(to - len(body))
        io = align(base + to + 4 * n, 16) - base      # the id table is 16-byte aligned (absolute)
        nt = io + 16 * n
        body += b''.join(self.types)
        body += bytes(io - len(body)) + self.ids
        names = []
        for s in self.names:
            if x360: s = s.replace(b'.win32', b'.x360')
            names.append(s)
        body += b'\0'.join(names) + b'\0'
        out = bytearray(hdr)
        struct.pack_into('<I', out, 0x10, base + len(body))
        # 0x38: name count; the game makes its name list from it (a stale count after adding a
        # resource leaves the last names null: crash when the model loads)
        struct.pack_into('<III', out, 0x30, n, nt, len(self.names))
        for i in range(n):
            o = offs[i] if i < n - 2 else (to if i == n - 2 else io)
            size = len(self.res[i]) if i < n - 2 else self.decl[i]
            out += struct.pack('<IIII', self.index[i], o, size, self.align[i])
        out += body
        if x360 and pad_file:   # .trb files are padded to 2 KB on the 360; .mpk files are not
            out += bytes(align(len(out), 2048) - len(out))
        return bytes(out)
