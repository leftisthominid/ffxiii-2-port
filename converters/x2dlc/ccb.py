"""CCB camera containers: typed headers/parameters around an unchanged MTB curve."""
import struct
from .learned import sedb_header


def convert(pc):
    """Return a converted supported container, or None for an unknown layout.

    FCMT owns its padded section length; it is not a generic tagged-chunk header.
    MTB curve bytes and names are preserved. Numeric camera parameters and index
    lists are converted according to their field widths.
    """
    if len(pc) < 0x80 + 16 or pc[:8] != b'SEDBCCB\0': return None
    if pc[0x30:0x35] != b'@FCHD' or pc[0x60:0x65] != b'@FCCO' or pc[0x80:0x85] != b'@FCMT': return None
    size, padded = struct.unpack_from('<II', pc, 0x88)
    params = 0x80 + padded
    if padded < 16 or size > padded - 16 or params + 16 > len(pc): return None
    if pc[0x90:0x98] != b'SEDBmtb\0' or pc[params:params + 5] != b'@FCPA': return None
    count = struct.unpack_from('<H', pc, params + 6)[0]
    indices = params + 16 + 80 * count
    if indices + 16 > len(pc) or pc[indices:indices + 5] != b'@FCIL' or (len(pc) - indices - 16) % 2: return None
    out = bytearray(pc)
    sedb_header(pc, out)
    out[0xC] = 0
    def swap(at, width):
        out[at:at + width] = pc[at:at + width][::-1]
    swap(0x36, 2)
    swap(0x66, 2)
    for at in range(0x68, 0x80, 4): swap(at, 4)
    swap(0x88, 4)
    swap(0x8C, 4)
    for n in range(count):
        record = params + 16 + 80 * n
        for at in range(record + 32, record + 48, 4): swap(at, 4)
        for at in range(record + 52, record + 64, 4): swap(at, 4)
        for at in range(record + 64, record + 68, 2): swap(at, 2)
    for at in range(indices + 16, len(pc), 2): swap(at, 2)
    return bytes(out)
