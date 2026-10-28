# Effect models (SEDBvmdl members of .xfv): a qix object (qix.py) followed by its shader programs,
# each a block {md5 of the program[16], u32 size, u32 kind, 8 zero bytes, program} (big-endian
# on both platforms, as in shd FILE chunks), pointed to by u32 fields of the object, back to back
# at the end of the member. PC: D3D9 bytecode; 360: Xenos microcode of another length, so the
# blocks move and their pointers and the two size fields (SEDB 0x10, object +4) change. The 360
# programs come from the shader library (md5 of the PC program), as for model materials.
import hashlib, struct
from . import qix

R = 0x30

def blocks(d, big_endian=False):
    """[(pointer field offset, block offset, program size)] of the shader blocks, by offset"""
    L = qix.layout(d, big_endian)
    if L is None: return None
    E = '>' if big_endian else '<'
    out = []
    for at, kind in L['pointers']:
        t = R + struct.unpack_from(E + 'I', d, at)[0]
        if t + 0x20 > len(d): continue
        size = struct.unpack_from('>I', d, t + 16)[0]
        if size == 0 or t + 0x20 + size > len(d) or any(d[t + 0x18:t + 0x20]): continue
        if hashlib.md5(d[t + 0x20:t + 0x20 + size]).digest() != d[t:t + 16]: continue
        out.append((at, t, size))
    out.sort(key=lambda b: b[1])
    return out

def learn_pair(pc, x, lib):
    """the PC / 360 programs of an effect model pair into the shader library"""
    a, b = blocks(pc), blocks(x, True)
    if not a or not b or len(a) != len(b): return 0
    n = 0
    for (_, ta, sa), (_, tb, sb) in zip(a, b):
        k = hashlib.md5(pc[ta + 0x20:ta + 0x20 + sa]).hexdigest()
        if k not in lib:
            lib[k] = (x[tb + 0x20:tb + 0x20 + sb], struct.unpack_from('>I', x, tb + 20)[0], 'vmdl'); n += 1
    return n

def missing(pc, lib):
    bl = blocks(pc) or []
    return [hex(t) for _, t, s in bl if hashlib.md5(pc[t + 0x20:t + 0x20 + s]).hexdigest() not in lib]

def convert(pc, y, lib, ctx=None):
    """pc: the PC member; y: its learned 360 byte order in the PC layout (qix.convert / fix).
    -> the 360 member with the 360 programs (KeyError when one is not in the library and not made
    by ctx: shader.Context compiles pixel shaders, linked to this model's vertex shader)"""
    bl = blocks(pc)
    if not bl: return y
    first = bl[0][1]
    if any(t < first for _, t, _ in bl) or bl[-1][1] + 0x20 + bl[-1][2] != len(pc):
        raise ValueError('effect model shader blocks not at the end')
    progs = []
    for at, t, size in bl:
        prog = pc[t + 0x20:t + 0x20 + size]
        progs.append((prog, struct.unpack_from('>I', pc, t + 20)[0]))
    vs = [p for p, k in progs if struct.unpack_from('<I', p, 0)[0] >> 16 == 0xFFFE]
    out = bytearray(y[:first])
    for (at, t, size), (prog, pkind) in zip(bl, progs):
        k = hashlib.md5(prog).hexdigest()
        if k in lib:
            x, kind = lib[k][0], lib[k][1]
        else:
            r = None
            if ctx is not None:
                is_vs = struct.unpack_from('<I', prog, 0)[0] >> 16 == 0xFFFE
                e = dict(name='effect.vpo' if is_vs else 'effect_001.fpo', prog=prog, kind=pkind)
                r = ctx.make(e, None, vs=None if is_vs else (vs[0] if vs else None))
            if r is None: raise KeyError(f'no 360 program for the effect shader at {t:#x}')
            x, kind = r
        struct.pack_into('>I', out, at, len(out) - R)          # the pointer (R-relative)
        # the u32 after the pointer: the block's length (0x20 + program), when it is that on the PC
        if at + 8 <= first and struct.unpack_from('<I', pc, at + 4)[0] == 0x20 + size:
            struct.pack_into('>I', out, at + 4, 0x20 + len(x))
        out += hashlib.md5(x).digest() + struct.pack('>II', len(x), kind) + bytes(8) + x
    struct.pack_into('>Q', out, 0x10, len(out))
    struct.pack_into('>I', out, R + 4, len(out))
    return bytes(out)

def object_part(d, big_endian=False):
    """the member without its shader blocks, size fields set to that length (same layout on both
    platforms: the learned byte-order conversion learns and converts this part)"""
    bl = blocks(d, big_endian)
    if not bl: return d
    first = bl[0][1]
    out = bytearray(d[:first])
    E = '>' if big_endian else '<'
    struct.pack_into(E + 'Q', out, 0x10, first)
    struct.pack_into(E + 'I', out, R + 4, first)
    return bytes(out)
