# 'wrb' model resources (chunk tree, big-endian on both platforms), PC -> 360.
#   chunk: tag(4) 0(4) u32 size u32 padded-size [containers: u32 count + 12 pad, then children]
#   containers: WRB, MDLC, MDL, MESH.  MESH: HEAD STR RSID RSTP PRID PRTP STMS*3 ENVD* AABB
#   STMS: u32 n_elements, u32 count, u16 stream id, u16 stride, u16 imgb offset/128, u16 flags,
#         n x {u32 offset, u32 type, u32 components, u16 usage, u16 0}, then the data.
# (From the Lightning Returns port; XIII-2: the global rule gives the real 360 meshes of the DLC sample.)
# Differences found with the Yuna oracle:
#   * stream 3 (id & 0x1F == 3) is not stored in the 360 wrb: its data goes to the start of the
#     .imgb (128-byte aligned, in mesh order) and the STMS keeps only the header (flag 0x0100).
#   * blend indices (element type 6): PC indexes the mesh's ENVD list (a per-mesh bone palette,
#     the PC shaders take <= 48 bones in constants); 360 holds the skeleton joint index (the 360
#     shaders read a bone palette texture). Unused 0xFF -> 0x3F when the skeleton has <= 64
#     joints (weapons), else 0xFF (characters) - both seen in the Yuna data.
#   * XIII-2, skeletons of 256 joints or more (the DLC garbs c161 / c162, 270 joints): the 360 blend
#     indices are 4 x u16 (element type 4, the stream 4 bytes wider, the elements after them
#     4 bytes later), the skeleton joint index, unused 0x13F (PC: 4 x u8, type 6)
#   * the 360 exporter merged the three c000_skin meshes of c451 into one (bone palette limit
#     only exists on PC); this converter keeps the PC split (valid structure, not byte-identical).
import struct
from .util import align

CONTAINERS = {b'WRB\0', b'MDLC', b'MDL\0', b'MESH'}

def parse(d, o, end):
    out = []
    while o < end:
        tag = d[o:o + 4]
        _, s1, s2 = struct.unpack_from('>III', d, o + 4)
        if tag in CONTAINERS:
            out.append(dict(tag=tag, hdr=d[o:o + 0x20], s1=s1, s2=s2, kids=parse(d, o + 0x20, o + s1)))
            o += s1
        else:
            span = s2 if s2 else align(s1, 16)      # (a chunk without a padded size takes its size rounded to 16)
            out.append(dict(tag=tag, hdr=None, s1=s1, s2=s2, raw=d[o:o + span]))
            o += span
            if not span: break
    return out

def build(nodes):
    out = bytearray()
    for n in nodes:
        if n['tag'] in CONTAINERS:
            body = build(n['kids'])
            h = bytearray(n['hdr'])
            s1 = 0x20 + len(body)
            struct.pack_into('>II', h, 8, s1, s1 if n['s2'] else 0)
            out += h + body
        else:
            out += n['raw']
    return bytes(out)

def _envd_name(raw):
    return raw[0x20:0x60].split(b'\0')[0].decode('latin1')

def model_envd(wrb_res):
    """ENVD bone-name lists of every MESH in tree order"""
    out = []
    def walk(nodes):
        for n in nodes:
            if n['tag'] == b'MESH':
                out.append([_envd_name(k['raw']) for k in n['kids'] if k['tag'] == b'ENVD'])
            elif n['tag'] in CONTAINERS:
                walk(n['kids'])
    walk(parse(wrb_res, 0x30, len(wrb_res)))
    return out

def palettes(rule, envd_lists, joints):
    """360 blend-index value for every (mesh, PC local index), per bone-index rule:
      global          skeleton joint index (what every Yuna model uses)
      deform_sorted   position in the sorted list of all joints the model's meshes use
      deform_firstseen  order of first use over the meshes
      mesh_local      PC value kept (per-mesh palette)"""
    jindex = {n: i for i, n in enumerate(joints)}
    for names in envd_lists:
        for nm in names:
            if nm not in jindex: raise ValueError(f'ENVD bone {nm} not in skeleton')
    if rule == 'global':
        return [[jindex[n] for n in names] for names in envd_lists]
    if rule == 'mesh_local':
        return [list(range(len(names))) for names in envd_lists]
    if rule == 'deform_sorted':
        used = sorted({jindex[n] for names in envd_lists for n in names})
        pos = {j: i for i, j in enumerate(used)}
        return [[pos[jindex[n]] for n in names] for names in envd_lists]
    if rule == 'deform_firstseen':
        order = {}
        for names in envd_lists:
            for n in names: order.setdefault(n, len(order))
        return [[order[n] for n in names] for names in envd_lists]
    raise ValueError('unknown bone rule ' + rule)

RULES = ('global', 'deform_sorted', 'deform_firstseen', 'mesh_local')

def unused_value(njoints, rule='global'):
    return 0x3F if njoints <= 64 else 0xFF

def convert(wrb_res, joints, vb_start=0, parents=None, report=None, rule='global', unused=None):
    """wrb_res: PC 'SEDBwrb' resource. joints: skeleton joint names (SKL order).
    Returns (360 wrb resource, vertex data for the head of the .imgb).
    Blend indices are 8-bit: a bone with joint index >= 256 cannot be written in the format
    seen in the 360 data. With `parents` (approximation) such a bone is replaced by its nearest
    ancestor below 256 and the substitution is appended to `report`; otherwise ValueError."""
    assert wrb_res[:7] == b'SEDBwrb'
    tree = parse(wrb_res, 0x30, len(wrb_res))
    jindex = {n: i for i, n in enumerate(joints)}
    if unused is None: unused = unused_value(len(joints), rule)
    pals = iter(palettes(rule, model_envd(wrb_res), joints))
    wide = len(joints) >= 256 and rule == 'global'     # 16-bit blend indices
    vb = bytearray()
    def walk(nodes):
        for n in nodes:
            if n['tag'] == b'MESH':
                mesh(n)
            elif n['tag'] in CONTAINERS:
                walk(n['kids'])
    def mesh(m):
        names = [_envd_name(k['raw']) for k in m['kids'] if k['tag'] == b'ENVD']
        pal = next(pals)
        for k, j in enumerate(pal):
            if j < 256 or wide: continue
            if parents is None or rule != 'global':
                raise ValueError(f'bone {names[k]} has joint index {j} >= 256: not representable '
                                 'in 8-bit 360 blend indices (use the joint approximation)')
            a = j
            while a >= 256: a = parents[a]
            if report is not None: report.append(f'{names[k]} ({j}) -> {joints[a]} ({a})')
            pal[k] = a
        for k in m['kids']:
            if k['tag'] != b'STMS': continue
            raw = bytearray(k['raw'])
            nel, cnt = struct.unpack_from('>II', raw, 0x10)
            sid, stride, ioff, flags = struct.unpack_from('>HHHH', raw, 0x18)
            els = [struct.unpack_from('>IIIHH', raw, 0x20 + 16 * i) for i in range(nel)]
            dstart = 0x20 + 16 * nel
            data = bytearray(raw[dstart:dstart + cnt * stride])
            if sid & 0x1F != 1:
                for (off, typ, comp, use, _) in els:
                    if typ == 6 and not wide:         # blend indices
                        for v in range(cnt):
                            for c in range(comp):
                                p = v * stride + off + c
                                x = data[p]
                                data[p] = unused if x == 0xFF else pal[x]
                bi = [e for e in els if e[1] == 6] if wide else []
                if bi:
                    # 16-bit blend indices: the skeleton joint index of each u8, 0x13F unused
                    off6, _, comp6, _, _ = bi[0]
                    if len(bi) > 1 or comp6 != 4: raise NotImplementedError('a stream with an unusual blend-index element')
                    raw_pc = raw[dstart:dstart + cnt * stride]
                    nstride = stride + 4
                    nd = bytearray()
                    for v in range(cnt):
                        row = data[v * stride:(v + 1) * stride]
                        idx = [0x13F if raw_pc[v * stride + off6 + c] == 0xFF else pal[raw_pc[v * stride + off6 + c]] for c in range(4)]
                        nd += row[:off6] + struct.pack('>4H', *idx) + row[off6 + 4:]
                    data = nd
                    hdr = bytearray(raw[:dstart])
                    struct.pack_into('>H', hdr, 0x1A, nstride)
                    for i, (o2, t2, c2, u2, z2) in enumerate(els):
                        struct.pack_into('>III', hdr, 0x20 + 16 * i, o2 + (4 if o2 > off6 else 0), 4 if t2 == 6 else t2, c2)
                    stride = nstride
                    raw = hdr + data + bytes(align(len(hdr) + len(data), 16) - len(hdr) - len(data))
                    struct.pack_into('>II', raw, 8, len(hdr) + len(data), len(raw))
                    k['s1'], k['s2'] = len(hdr) + len(data), len(raw)
            if sid & 0x1F == 3:                       # goes to the imgb
                pos = align(vb_start + len(vb), 128)
                vb.extend(bytes(pos - vb_start - len(vb)) + data)
                hdr = raw[:dstart]
                struct.pack_into('>HH', hdr, 0x1C, pos >> 7, 0x0100)
                struct.pack_into('>II', hdr, 8, len(hdr), len(hdr))
                k['raw'] = bytes(hdr)
                k['s1'] = k['s2'] = len(hdr)
            else:
                raw[dstart:dstart + len(data)] = data
                k['raw'] = bytes(raw)
    walk(tree)
    body = build(tree)
    out = bytearray(wrb_res[:0x30]) + body
    struct.pack_into('<I', out, 0x10, len(out))
    return bytes(out), bytes(vb)
