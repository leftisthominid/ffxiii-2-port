# GTEX textures (txb resources in .trb, texel data in .imgb), PC -> Xbox 360.
# PC: linear rows, little-endian, every mip level stored separately (offset, size per level).
# 360: 32x32-block macro tiling (Xenos 2D tiling), 16-bit (DXTn) or 32-bit (ARGB) byte swap,
#      levels >= the 'packed' level share one tail tile; GTEX mip table holds
#      (imgb offset of the blob, blob size - one block) then (offset of the mip area, 0) for each mip.
# texel placement is from the earlier texconv.py (verified on the UI sheet, and here on every
# Yuna model texture). XIII-2: every texel byte of the DLC sample's model textures identical.
import struct
import numpy as np
from .util import align

FORMATS = {0x18: (4, 8, 2), 0x19: (4, 16, 2), 0x1A: (4, 16, 2), 0x04: (1, 4, 4), 0x03: (1, 4, 4)}

def tiled_offset(x, y, pitch, bpb):
    log_bpp = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[bpb]
    macro = ((x >> 5) + (y >> 5) * (pitch >> 5)) << (log_bpp + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << log_bpp
    off = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + log_bpp)) + ((y & 1) << 4)
    return ((((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7) +
             (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> log_bpp)

def tiled_offsets(x, y, pitch, bpb):
    """tiled_offset for numpy arrays of x, y"""
    log_bpp = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[bpb]
    macro = ((x >> 5) + (y >> 5) * (pitch >> 5)) << (log_bpp + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << log_bpp
    off = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + log_bpp)) + ((y & 1) << 4)
    return ((((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7) +
             (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> log_bpp)

# byte order of one block on the 360: 16-bit swap (DXTn) or 32-bit swap (ARGB)
SWAPS = {(2, b): [i ^ 1 for i in range(b)] for b in (8, 16)}
SWAPS.update({(4, b): [i ^ 3 for i in range(b)] for b in (4, 8, 16)})

def _log2_ceil(v): return max(0, (v - 1).bit_length())
def _next_pow2(v): return 1 << _log2_ceil(v)

def packed_mip_level(w, h):
    s = _log2_ceil(min(w, h))
    return s - 4 if s > 4 else 0

def packed_mip_offset(w, h, mip, block):
    lw, lh = _log2_ceil(w), _log2_ceil(h)
    ls = min(lw, lh)
    base = ls - 4 if ls > 4 else 0
    pm = mip - base
    if pm < 3:
        x, y = (0, 16 >> pm) if lw > lh else (16 >> pm, 0)
    else:
        if lw > lh: x, y = (1 << (lw - base)) >> (pm - 2), 0
        else: x, y = 0, (1 << (lh - base)) >> (pm - 2)
    return x // block, y // block

def to_360(levels, width, height, fmt, base_fill=0, mip_fill=0xCC):
    """levels: PC level data (level 0 first). Returns (blob, mip_area_offset, used_mask)."""
    block, bpb, swap = FORMATS[fmt]
    nlev = len(levels)
    bw0, bh0 = (width + block - 1) // block, (height + block - 1) // block
    pitch0 = align(bw0, 32)
    packed = packed_mip_level(width, height) if nlev > 1 else None
    if packed == 0: base_fill = mip_fill   # the base tile is the mip tail tile
    sizes = [align(pitch0 * align(bh0, 32) * bpb, 4096)]
    pitches = [pitch0]
    if packed != 0 and nlev > 1:
        for lv in range(1, min(nlev - 1, packed) + 1):
            rp = align((max(_next_pow2(width) >> lv, 1) + block - 1) // block, 32)
            rows = align((max(_next_pow2(height) >> lv, 1) + block - 1) // block, 32)
            sizes.append(align(rp * rows * bpb, 4096)); pitches.append(rp)
    total = sum(sizes)
    out_a = np.full(total, mip_fill, np.uint8); out_a[:sizes[0]] = base_fill
    used_a = np.zeros(total, np.uint8)
    starts = [sum(sizes[:i]) for i in range(len(sizes))]
    def place(buf_i, src, w, h, ox, oy):
        pitch, b0 = pitches[buf_i], starts[buf_i]
        bw, bh = (w + block - 1) // block, (h + block - 1) // block
        a = np.frombuffer(bytes(src[:bw * bh * bpb]).ljust(bw * bh * bpb, b'\0'), np.uint8).reshape(-1, bpb)
        a = a[:, SWAPS[(swap, bpb)]]
        y, x = np.divmod(np.arange(bw * bh, dtype=np.int64), bw)
        o = b0 + tiled_offsets(x + ox, y + oy, pitch, bpb) * bpb
        idx = (o[:, None] + np.arange(bpb)).ravel()
        out_a[idx] = a.ravel()
        used_a[idx] = 1
    if packed == 0:                       # every level lives in the base tile
        for lv in range(nlev):
            ox, oy = packed_mip_offset(width, height, lv, block)
            place(0, levels[lv], max(width >> lv, 1), max(height >> lv, 1), ox, oy)
        return out_a.tobytes(), 0, used_a.tobytes()
    place(0, levels[0], width, height, 0, 0)
    for bi, lv in enumerate(range(1, len(sizes)), start=1):
        if lv == packed:
            for sub in range(packed, nlev):
                ox, oy = packed_mip_offset(width, height, sub, block)
                place(bi, levels[sub], max(width >> sub, 1), max(height >> sub, 1), ox, oy)
        else:
            place(bi, levels[lv], max(width >> lv, 1), max(height >> lv, 1), 0, 0)
    return out_a.tobytes(), (sizes[0] if nlev > 1 else total), used_a.tobytes()

def convert_txb(txb, pc_imgb, g=None):
    """PC texture header (GTEX at offset g; 0x40 in trb txb resources) + PC imgb ->
    (360 header template, blob, mip_off, used, bpb, pc offset, g). The imgb offset is patched in
    later by place_textures()."""
    t = bytearray(txb)
    if g is None: g = 0x40 if txb[0x40:0x44] == b'GTEX' else txb.find(b'GTEX')
    assert t[g:g + 4] == b'GTEX', 'not a GTEX texture'
    fmt, nlev = t[g + 6], t[g + 7]
    if fmt not in FORMATS: raise NotImplementedError(f'GTEX format {fmt:#x}')
    w, h = struct.unpack_from('>HH', t, g + 10)
    if t[g + 9] & 1:
        return convert_cube(t, pc_imgb, g, fmt, nlev, w, h)
    levels = []
    for i in range(nlev):
        o, s = struct.unpack_from('>II', t, g + 0x18 + 8 * i)
        levels.append(pc_imgb[o:o + s])
    blob, mip_off, used = to_360(levels, w, h, fmt)
    t[g + 5] = 3            # platform: 1 = PC, 3 = 360
    if w & (w - 1) == 0 and h & (h - 1) == 0:
        t[g + 9] |= 4       # (power-of-two size: set on the 360 only, in every texture of the data)
    return t, blob, mip_off, used, FORMATS[fmt][1], min(o for o, s in (struct.unpack_from('>II', txb, g + 0x18 + 8 * i) for i in range(nlev))), g

def convert_cube(t, pc_imgb, g, fmt, nlev, w, h):
    """cube map (GTEX flag 1): the PC has 6 faces x nlev levels in its level table (face by face);
    the 360 files keep level 0 of each face only (one level), tiled, the faces one after another.
    Level table of the 360 headers: entry 0 = the blob; the others the PC offsets relative to the
    PC base, with 0 for each face's level 0 (as the 360 data has them; only entry 0 is used)."""
    ents = [struct.unpack_from('>II', t, g + 0x18 + 8 * k) for k in range(6 * nlev)]
    faces = [pc_imgb[ents[f * nlev][0]:ents[f * nlev][0] + ents[f * nlev][1]] for f in range(6)]
    blob, used = b'', b''
    for face in faces:
        b, _, u = to_360([face], w, h, fmt, base_fill=0xCC)   # (cube faces: padded with 0xCC)
        blob += b; used += u
    pc_base = ents[0][0]
    t[g + 5] = 3
    t[g + 7] = 1
    if w & (w - 1) == 0 and h & (h - 1) == 0: t[g + 9] |= 4
    rel = [(0, 0) if k % nlev == 0 else (o - pc_base, s) for k, (o, s) in enumerate(ents)]
    return t, blob, len(blob), used, FORMATS[fmt][1], pc_base, g, ('cube', nlev, rel)

def place_textures(start, items):
    """items: list of (txb bytearray, blob, mip_off, used_mask, bpb, pc_offset[, gtex_off]).
    Layout of the real 360 data (reproduces every Yuna texture offset): textures are taken in PC
    imgb order and each goes to the lowest 4 KB boundary >= start where none of its used texel
    bytes collide with texel bytes already placed (small mips leave holes that later textures
    fill). Padding of a later texture never overwrites bytes written earlier.
    Returns (imgb bytes from `start`, [360 txb resources in input order])."""
    order = sorted(range(len(items)), key=lambda i: items[i][5])
    cap = sum(len(it[1]) for it in items) + 4096
    occ = np.zeros(cap, bool)          # texel byte placed
    written = np.zeros(cap, bool)      # any byte written
    buf = np.zeros(cap, np.uint8)
    res = [None] * len(items)
    end = 0
    for i in order:
        it = items[i]
        t, blob, mip_off, used, bpb = it[:5]
        g = it[6] if len(it) > 6 else 0x40
        n = len(used)
        u = np.frombuffer(used, np.uint8).astype(bool)
        p = 0
        while (occ[p:p + n] & u).any(): p += 4096
        wm = u | ~written[p:p + n]
        seg = buf[p:p + n]
        seg[wm] = np.frombuffer(blob[:n], np.uint8)[wm]
        occ[p:p + n] |= u
        written[p:p + n] = True
        nlev = t[g + 7]
        struct.pack_into('>II', t, g + 0x18, start + p, len(blob))  # XIII-2: the full size (LR: - one block)
        if len(it) > 7 and it[7] and it[7][0] == 'cube':
            _, pn, rel = it[7]
            for k in range(1, 6 * pn):
                o, sz = rel[k]
                if k % pn: o += start + p if k < pn - 1 else 0
                struct.pack_into('>II', t, g + 0x18 + 8 * k, o, sz)
        for lv in range(1, nlev):
            struct.pack_into('>II', t, g + 0x18 + 8 * lv, start + p + mip_off, 0)
        res[i] = bytes(t)
        end = max(end, p + n)
    return buf[:end].tobytes(), res
