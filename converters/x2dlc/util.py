import struct

def align(v, a):
    return (v + a - 1) // a * a

def swap32(b):
    b = bytearray(b)
    n = len(b) // 4 * 4
    for i in range(0, n, 4):
        b[i:i + 4] = b[i:i + 4][::-1]
    return bytes(b)

def swap16(b):
    b = bytearray(b)
    n = len(b) // 2 * 2
    b[0:n:2], b[1:n:2] = b[1:n:2], b[0:n:2]
    return bytes(b)

def u32le(b, o): return struct.unpack_from('<I', b, o)[0]
def u32be(b, o): return struct.unpack_from('>I', b, o)[0]
def u16be(b, o): return struct.unpack_from('>H', b, o)[0]
