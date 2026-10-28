# WPD package (big-endian on both platforms): 'WPD\0', u32 count, 8 pad, then count x 32-byte
# entries {name[16], u32 offset, u32 size, ext[8]}, data aligned (0x80; 0x800 for 360 sound packs).
import struct
from .util import align

def read(d):
    assert d[:4] == b'WPD\0', d[:4]
    n = struct.unpack_from('>I', d, 4)[0]
    out = []
    for i in range(n):
        e = 16 + 32 * i
        name = d[e:e + 16]
        off, size = struct.unpack_from('>II', d, e + 16)
        ext = d[e + 24:e + 32]
        out.append((name, ext, d[off:off + size]))
    return out

def write(entries, data_align=0x80, header=None, file_align=None):
    """entries: [(name16, ext8, data)]."""
    n = len(entries)
    out = bytearray(b'WPD\0' + struct.pack('>I', n) + (header or bytes(8)))
    table = bytearray()
    pos = align(16 + 32 * n, data_align)
    body = bytearray()
    for name, ext, data in entries:
        off = pos + len(body)
        table += name.ljust(16, b'\0')[:16] + struct.pack('>II', off, len(data)) + ext.ljust(8, b'\0')[:8]
        body += data
        body += bytes(align(pos + len(body), data_align) - (pos + len(body)))
    out += table
    out += bytes(pos - len(out))
    out += body
    if file_align: out += bytes(align(len(out), file_align) - len(out))
    return bytes(out)

def detect_align(d):
    offs = [struct.unpack_from('>I', d, 16 + 32 * i + 16)[0] for i in range(struct.unpack_from('>I', d, 4)[0])]
    for a in (0x800, 0x80, 0x10):
        if all(o % a == 0 for o in offs): return a
    return 0x10
