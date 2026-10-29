# 'shd' material shader resources, PC -> 360.
#
# Layout (both platforms): SEDB header (0x30, LE size at 0x10, alignment byte at 0x0D: PC 2, 360 7)
#   'SHD\0' u32(LE)1 u32 total u32 0 | u32 n_chunks + 12 pad          (big-endian sizes)
#   FILE chunks: 'FILE' 0 u32 size u32 padded | u32 data_size, u32 0xC (name offset), u32 data offset
#                (both relative to +0x10), name (NUL padded), data = md5(program)[16], u32 size,
#                u32 kind (5 = vertex, 0x..81x = pixel), 8 zero bytes, program.
#   VCAP / PCAP: capability tables, 16-byte records {u16 4-bit-field mask, u16 0, u32 pass, 0, 0}
#   PRAM: material parameters (values + texture bindings), converted by pram.py.
# XIII-2: PC and 360 hold the same programs under the same names (vpo, fpo 000..00N; Lightning
#   Returns' PC data has extra fpo 004-007 the 360 does not); the kind of a weapon pixel program
#   differs (PC 0x...231, 360 0x...331), so the 360 kind comes from the library.
# A 360 program is taken from a library keyed by the md5 of the PC program it was built from: pairs
# from the base game (learn.py); verified on the XIII-2 DLC sample: rebuilding each shd from its PC
# shd + library gives the real 360 bytes (weapons, monsters, characters, backgrounds). A PC pixel
# program that is not in the library is compiled (xcompile.py, Context below).
import struct, hashlib, os, pickle
from .util import align
from . import pram


def chunks(d):
    assert d[:7] == b'SEDBshd' and d[0x30:0x34] == b'SHD\0'
    n = struct.unpack_from('>I', d, 0x40)[0]
    p, out = 0x50, []
    for _ in range(n):
        tag = d[p:p + 4]
        _, s1, s2 = struct.unpack_from('>III', d, p + 4)
        e = dict(tag=tag, raw=d[p:p + s1])
        if tag == b'FILE':
            a, b, c = struct.unpack_from('>III', d, p + 16)
            data = d[p + 0x10 + c:p + 0x10 + c + a]
            size, kind = struct.unpack_from('>II', data, 16)
            e.update(name=d[p + 0x10 + b:p + 0x10 + c].split(b'\0')[0].decode(), md5=data[:16],
                     kind=kind, prog=data[32:32 + size])
        out.append(e)
        p += s2 if s2 else s1
    return out

def file_chunk(name, kind, prog):
    nb = name.encode() + b'\0'
    c = align(0xC + len(nb), 16)
    data = hashlib.md5(prog).digest() + struct.pack('>IIII', len(prog), kind, 0, 0) + prog
    s1 = 0x10 + c + len(data)
    hdr = b'FILE' + struct.pack('>IIIIII', 0, s1, align(s1, 16), len(data), 0xC, c)
    body = hdr + nb + bytes(c - 0xC - len(nb)) + data
    return body + bytes(align(s1, 16) - s1)

def cap_record_to_360(r):
    """16-byte VCAP/PCAP record: 4-bit fields of the first u32 are reversed (bitfield order),
    the rest are plain u32 values."""
    v = struct.unpack_from('<I', r, 0)[0]
    nib = [(v >> (4 * i)) & 0xF for i in range(8)]
    w = 0
    for i, x in enumerate(nib): w |= x << (4 * (7 - i))
    return struct.pack('>I', w) + b''.join(struct.pack('>I', struct.unpack_from('<I', r, o)[0]) for o in (4, 8, 12))

def cap_chunk(tag, body_records, last=False):
    body = b''.join(body_records)
    s1 = 0x10 + len(body)
    return tag + struct.pack('>III', 0, s1, 0 if last else align(s1, 16)) + body + (b'' if last else bytes(align(s1, 16) - s1))

def x360_file_name(pc_name):
    """'.w793_mat_002.fpo' -> 'def.w793_mat_002.fpo'; vpo and fpo 000 keep their names."""
    if pc_name.endswith('.fpo') and pc_name.startswith('.') and not pc_name.endswith('_000.fpo'):
        return 'def' + pc_name
    return pc_name

def needed(pc_chunks):
    """PC FILE chunks that have a 360 counterpart. XIII-2: every program, same names on both
    (Lightning Returns' PC data has extra fpo 004-007 the 360 does not)."""
    return [e for e in pc_chunks if e['tag'] == b'FILE']

def pairs(pc_res, x_res):
    """[(name, PC kind, 360 kind, PC program, 360 program)] of one material"""
    pc = needed(chunks(pc_res)); xx = [e for e in chunks(x_res) if e['tag'] == b'FILE']
    if [x360_file_name(e['name']) for e in pc] != [e['name'] for e in xx]: return []
    return [(a['name'], a['kind'], b['kind'], a['prog'], b['prog']) for a, b in zip(pc, xx)]

def learn(pc_res, x_res, lib):
    """Add PC->360 program pairs of one material to lib {md5(pc program): (360 program, kind, source)}."""
    pc = needed(chunks(pc_res)); xx = [e for e in chunks(x_res) if e['tag'] == b'FILE']
    assert [x360_file_name(e['name']) for e in pc] == [e['name'] for e in xx], 'program sets differ'
    for a, b in zip(pc, xx):
        k = hashlib.md5(a['prog']).hexdigest()
        if k in lib and lib[k][0] != b['prog']:
            raise ValueError('same PC program maps to two different 360 programs: ' + a['name'])
        lib[k] = (b['prog'], b['kind'], b['name'])
    return lib

def missing(pc_res, lib, ctx=None):
    """programs that cannot be made: not in the library and (with a compile context) not compiled"""
    out = []
    for e in needed(chunks(pc_res)):
        if hashlib.md5(e['prog']).hexdigest() in lib: continue
        if ctx is not None and ctx.can_make(e, pc_res): continue
        out.append(e['name'])
    return out

def material_of(name):
    """'def.w160_body_002.fpo' / '.w160_body_000.fpo' / 'w160_body_000.vpo' -> 'w160_body'"""
    n = name.split('.', 1)[1] if name.count('.') >= 2 else name.lstrip('.')
    n = n.rsplit('.', 1)[0]
    return n.rsplit('_', 1)[0] if n[-4:-3] == '_' and n[-3:].isdigit() else n

def compiled_pixel_kind(kind, prog):
    """Reserve the alpha driver that the compiled program actually uses.

    PixelShader's constructor counts bit 0x100 to allocate driver slots; its
    loader independently creates that driver when CTAB contains alphaOffset.
    Compilation can introduce this 360-only constant even for PC kinds absent
    from the learned kind map. Keep the two native decisions consistent.
    """
    from . import xengine
    ct, _, _, is_pixel = xengine.container_info(prog)
    if not is_pixel: raise ValueError('pixel kind requires a pixel shader')
    return (kind & ~0x100) | (0x100 if any(c['name'] == b'alphaOffset' for c in ct['consts']) else 0)

class Context:
    """programs not in the library: pixel shaders compiled (xcompile), linked to the 360 vertex
    shader of the same material; a vertex shader not in the library: the library's vertex shader
    with the same declarations (inputs and outputs), from the same model where possible.
    vs_of(material) -> the PC vertex program of a material (for .mpk variants, from the .trb)."""
    def __init__(self, lib, engine, vs_sigs, vs_of=None, log=None):
        self.lib, self.engine, self.vs_sigs, self.vs_of = lib, engine or {}, vs_sigs or {}, vs_of
        self.log = log
        self.made = {}
        self.stats = {}
        self.folder = None             # top folder of the file being converted (chr, bg, vfx..)
        self.failed = {}

    def note(self, k):
        self.stats[k] = self.stats.get(k, 0) + 1

    def vs_360(self, pc_vs, name=''):
        """the 360 vertex program for a PC one: library, else compiled (xcompile), else the
        library's vertex shader with the same declarations"""
        k = hashlib.md5(pc_vs).hexdigest()
        if k in self.lib: return self.lib[k][0], self.lib[k][1]
        if k in self.made: return self.made[k]
        from . import xcompile
        try:
            prog, _ = xcompile.compile_vs(pc_vs, None, self.engine)
            self.made[k] = (prog, self.engine.get('kinds', {}).get(0x805, 0x805))
            self.note('vertex shader compiled')
            return self.made[k]
        except Exception as ex:
            if self.log and len(self.failed) <= 20: self.log(f'  vertex shader {name}: {type(ex).__name__}: {ex}')
        sig = vs_signature(pc_vs)
        cands = self.vs_sigs.get(sig, [])
        if not cands: return None
        model = name.split('_', 1)[0]
        best = sorted(cands, key=lambda c: (0 if c[1].startswith(model) else 1, c[1]))[0]
        r = self.lib.get(best[0])
        if r is None: return None
        self.made[k] = (r[0], r[1])
        self.note('vertex shader substituted (same declarations)')
        return self.made[k]

    def make(self, e, pc_res, vs=None):
        """(360 program, kind) for a FILE chunk not in the library, or None. vs: the PC vertex
        program of the material when it is not in pc_res (effect models)"""
        k = hashlib.md5(e['prog']).hexdigest()
        key = (k, e['name'], hashlib.md5(vs).hexdigest() if vs else None, self.folder)
        if key in self.made: return self.made[key]
        if key in self.failed: return None
        from . import xcompile, xengine
        res = None
        try:
            if e['name'].endswith('.vpo'):
                res = self.vs_360(e['prog'], e['name'])
            else:
                mat = material_of(e['name'])
                if pc_res is not None:
                    for f in needed(chunks(pc_res)):
                        if f['name'].endswith('.vpo') and material_of(f['name']) == mat: vs = f['prog']
                if vs is None and self.vs_of is not None: vs = self.vs_of(mat)
                interp, hsh = None, 0
                if vs is not None:
                    v = self.vs_360(vs, mat + '_000.vpo')
                    if v is not None:
                        _, interp, hsh, _ = xengine.container_info(v[0])
                if interp is None:
                    raise xcompile.CompileError('no vertex shader for ' + mat)
                prog = xcompile.compile_ps(e['prog'], interp, hsh, self.engine, folder=self.folder)
                kind = self.engine.get('kinds', {}).get(e['kind'], e['kind'] + 0x100 if (e['kind'] & 0xF00) == 0x200 else e['kind'])
                kind = compiled_pixel_kind(kind, prog)
                res = (prog, kind)
                self.note('pixel shader compiled')
        except Exception as ex:
            self.failed[key] = f'{type(ex).__name__}: {ex}'
            if self.log and len(self.failed) <= 20: self.log(f'  shader {e["name"]}: {self.failed[key]}')
            self.note('shader not made')
            res = None
        if res is not None: self.made[key] = res
        return res

    def can_make(self, e, pc_res):
        return self.make(e, pc_res) is not None

def vs_signature(prog):
    """the declarations of a vertex program: (inputs, outputs) as (usage, index, mask)"""
    from . import d3d9
    v, ins, _ = d3d9.parse(prog)
    return tuple(sorted((i.dst.type, i.values[0], i.values[1], i.dst.mask) for i in ins if i.name == 'dcl'))

def convert(pc_res, lib, ctx=None):
    """PC shd -> 360 shd; a KeyError lists the programs missing from lib (and not made by ctx)."""
    ch = chunks(pc_res)
    out = []
    miss = []
    for e in needed(ch):
        k = hashlib.md5(e['prog']).hexdigest()
        if k in lib:
            prog, kind = lib[k][0], lib[k][1]   # the 360 kind (pixel programs: PC 0x...231, 360 0x...331)
        else:
            r = ctx.make(e, pc_res) if ctx is not None else None
            if r is None:
                miss.append(e['name']); continue
            prog, kind = r
        out.append(file_chunk(x360_file_name(e['name']), kind, prog))
    if miss:
        raise KeyError('no 360 program for: ' + ', '.join(miss))
    for e in ch:
        if e['tag'] in (b'VCAP', b'PCAP'):
            recs = [e['raw'][16 + i:32 + i] for i in range(0, len(e['raw']) - 16, 16)]
            recs = [r for r in recs if not (struct.unpack_from('<I', r, 4)[0] & 0x100)]
            out.append(cap_chunk(e['tag'], [cap_record_to_360(r) for r in recs]))
        elif e['tag'] == b'PRAM':
            out.append(pram.convert(e['raw']))
        elif e['tag'] != b'FILE':
            raise NotImplementedError('shd chunk ' + repr(e['tag']))
    body = b''.join(out)
    shd = b'SHD\0' + struct.pack('<I', 1) + struct.pack('>II', 0x20 + len(body), 0) + struct.pack('>IIII', len(out), 0, 0, 0)
    res = bytearray(pc_res[:0x30])
    res[0x0D] = 7
    res += shd + body
    struct.pack_into('<I', res, 0x10, len(res))
    return bytes(res)


