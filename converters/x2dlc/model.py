# Model files (.trb SEDBRES + .imgb texel / vertex data), Steam (PC) -> Xbox 360.
# Checked against the real 360 DLC files (weapon packs 21 and 23):
#   txb   GTEX header + texels: texture.convert_txb (Xenos tiling, byte swap); the 360 header holds
#         the blob's full size (Lightning Returns: size - one block)
#   wrb   mesh.convert with the 'global' bone rule (skeleton joint indices); stream 3 vertex data
#         goes to the head of the .imgb
#   shd   shader.convert: the 360 programs come from a library keyed by the md5 of the PC program
#         (base-game pairs); resource alignment 128 bytes
#   jeb   u32 values byte-swapped, the names after them as they are
#   skl, elb, mpm and the other resources: the same bytes on both
#   (a character the 360 exporter made one mesh of stays two meshes, as split on PC: c226)
#   .mpk  (material variants: a SEDBRES of shd resources) the same, without the 2 KB padding
#   .imgb: vertex data, then the textures from the next 4 KB boundary (texture.place_textures)
import struct
from .util import align, swap32
from . import trb, texture, mesh, skl, shader

class MissingShaders(Exception):
    pass

def string_tail(d):
    """start of the trailing run of NUL-terminated names (4-byte aligned)"""
    i = len(d)
    while i > 0 and (32 <= d[i - 1] < 127 or d[i - 1] == 0):
        i -= 1
    while i < len(d) and d[i] == 0:
        i += 1
    return i - i % 4

def swap_until_strings(d):
    """jeb: u32 values (byte-swapped on the 360) followed by plain names"""
    n = string_tail(d)
    return swap32(d[:n]) + d[n:]

def convert(pc_trb, pc_imgb, shaders, report=None, pad=True, bone_rule='global_parent', ctx=None):
    """Returns (360 trb bytes, 360 imgb bytes or None when the model has no imgb data).
    bone_rule: for a skeleton of 256 joints or more (the 360 blend indices are 8-bit), the rule
    learned from the base game (learn.bone_rule_probe); 'global_parent' = a joint >= 256 is
    replaced by its nearest ancestor below 256."""
    t = trb.Trb(pc_trb)
    types = [t.type_of(i) for i in range(len(t.res))]
    si = [i for i, ty in enumerate(types) if ty == 'skl']
    joints = skl.joints(t.res[si[0]]) if si else []
    out = list(t.res)
    vb = b''
    tex_items, tex_idx = [], []
    missing = []
    for i, ty in enumerate(types):
        r = t.res[i]
        if ty == 'wrb':
            if vb:
                raise NotImplementedError('more than one mesh resource')
            if len(joints) < 256 or bone_rule == 'global':
                out[i], vb = mesh.convert(r, joints, 0, rule='global')
            elif bone_rule == 'global_parent':
                par = skl.parents(t.res[si[0]])
                out[i], vb = mesh.convert(r, joints, 0, parents=par, report=report, rule='global')
            else:
                out[i], vb = mesh.convert(r, joints, 0, rule=bone_rule)
        elif ty == 'txb':
            tex_items.append(texture.convert_txb(r, pc_imgb)); tex_idx.append(i)
        elif ty == 'shd' or r[:7] == b'SEDBshd':
            m = shader.missing(r, shaders, ctx)
            if m:
                missing += m
                continue
            out[i] = shader.convert(r, shaders, ctx)
            t.align[i] = 7
        elif ty == 'jeb':
            out[i] = swap_until_strings(r)
    if missing:
        raise MissingShaders(missing)
    img = None
    if vb or tex_items:
        start = align(len(vb), 4096) if vb else 0
        img = bytes(vb)
        if tex_items:
            data, txbs = texture.place_textures(start, tex_items)
            for k, i in enumerate(tex_idx):
                out[i] = txbs[k]
            img = bytes(vb) + bytes(start - len(vb)) + data
    t.res = out
    return t.build(x360=True, pad=pad), img
