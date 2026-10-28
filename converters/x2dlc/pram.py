# (verified byte-identical on all 10 Lightning Returns Yuna materials and on the XIII-2 DLC sample)
# Material parameter block (PRAM) PC -> 360.
# Layout (offsets relative to the block, table offsets relative to +0x10):
#   0x10 u16 flags (bit order mirrored on 360), u8 0xff, u8 0
#   0x14 u32 x7: f32 param, nvals, valtab, valname, ntex, textab, texname
#   value record: 16-byte header {u8 type, 0, u8 count, 0, 12 x 0} then count x 4-byte values
#   texture record: byte data plus "bxt\0"/"brt\0" type tags (reversed on 360)
import struct
def mirror16(v):
    return int(f'{v:016b}'[::-1], 2)
def swap32(o, p, a, n):
    for i in range(a, a + 4 * n, 4):
        o[i:i + 4] = p[i:i + 4][::-1]
def convert(p):
    o = bytearray(p)
    struct.pack_into('>H', o, 0x10, mirror16(struct.unpack_from('<H', p, 0x10)[0]))
    swap32(o, p, 0x14, 7)
    _, nvals, valtab, valname, ntex, textab, texname = struct.unpack_from('<7I', p, 0x14)
    for tab, n in ((valtab, nvals), (valname, nvals), (textab, ntex), (texname, ntex)):
        swap32(o, p, 0x10 + tab, n)
    for k in range(nvals):
        r = 0x10 + struct.unpack_from('<I', p, 0x10 + valtab + 4 * k)[0]
        swap32(o, p, r + 4, 3)
        swap32(o, p, r + 16, p[r + 2])
    for k in range(ntex):
        r = 0x10 + struct.unpack_from('<I', p, 0x10 + textab + 4 * k)[0]
        nxt = (0x10 + struct.unpack_from('<I', p, 0x10 + textab + 4 * (k + 1))[0] if k + 1 < ntex
               else 0x10 + struct.unpack_from('<I', p, 0x10 + texname)[0])
        for tag in (b'bxt\x00', b'brt\x00'):
            j = p.find(tag, r)
            while 0 <= j < nxt:
                o[j:j + 4] = tag[::-1]
                j = p.find(tag, j + 4)
    return bytes(o)
