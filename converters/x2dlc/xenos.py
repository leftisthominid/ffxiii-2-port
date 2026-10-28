# Xbox 360 (Xenos) shader microcode and the compiled-shader container, decoding and encoding.
# Instruction formats from Xenia's src/xenia/gpu/ucode.h (BSD); the container layout was worked out
# from the base-game pairs (see container()).
#   microcode: big-endian dwords. Control flow (CF) instructions come first, two 48-bit CF
#   instructions per 3 dwords; ALU / fetch instructions are 3 dwords each, addressed by slot
#   (slot n = dwords 3n..3n+2 of the microcode, CF slots included).
import struct

# ---- bit fields ----------------------------------------------------------------------------

def bits(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)

def sbits(v, lo, n):
    x = bits(v, lo, n)
    return x - (1 << n) if x & (1 << (n - 1)) else x

class Fields:
    """little-endian bit field packing over 32-bit words: [(name, word, lo, n)]"""
    def __init__(self, spec):
        self.spec = spec
    def unpack(self, words):
        return {name: bits(words[w], lo, n) for name, w, lo, n in self.spec}
    def pack(self, d, nwords):
        words = [0] * nwords
        for name, w, lo, n in self.spec:
            v = d.get(name, 0) & ((1 << n) - 1)
            words[w] |= v << lo
        return words

# ---- control flow ---------------------------------------------------------------------------

CF_NAMES = {0: 'nop', 1: 'exec', 2: 'exece', 3: 'cexec', 4: 'cexece', 5: 'pexec', 6: 'pexece',
            7: 'loop', 8: 'endloop', 9: 'ccall', 10: 'ret', 11: 'cjmp', 12: 'alloc',
            13: 'cexec_pc', 14: 'cexece_pc', 15: 'vfetch_done'}
EXEC_OPS = {1, 2, 3, 4, 5, 6, 13, 14}
END_OPS = {2, 4, 6, 14}

# a CF instruction as two words: w0 = 32 bits, w1 = 16 bits
CF_EXEC = Fields([('address', 0, 0, 12), ('count', 0, 12, 3), ('yield', 0, 15, 1), ('sequence', 0, 16, 12),
                  ('vc_hi', 0, 28, 4), ('vc_lo', 1, 0, 2), ('bool_address', 1, 2, 8), ('condition', 1, 10, 1),
                  ('predicate_clean', 1, 9, 1), ('address_mode', 1, 11, 1), ('opcode', 1, 12, 4)])
CF_LOOP = Fields([('address', 0, 0, 13), ('repeat', 0, 13, 1), ('loop_id', 0, 16, 5),
                  ('predicated_break', 0, 21, 1), ('condition', 1, 10, 1), ('address_mode', 1, 11, 1), ('opcode', 1, 12, 4)])
CF_JMP = Fields([('address', 0, 0, 13), ('unconditional', 0, 13, 1), ('predicated', 0, 14, 1),
                 ('direction', 1, 1, 1), ('bool_address', 1, 2, 8), ('condition', 1, 10, 1),
                 ('address_mode', 1, 11, 1), ('opcode', 1, 12, 4)])
CF_ALLOC = Fields([('size', 0, 0, 3), ('unserialized', 1, 8, 1), ('alloc_type', 1, 9, 2), ('opcode', 1, 12, 4)])

def cf_decode(w0, w1):
    op = bits(w1, 12, 4)
    if op in EXEC_OPS or op == 0 or op == 15:
        d = CF_EXEC.unpack([w0, w1])
        if op in (3, 4, 13, 14):
            d.pop('predicate_clean')
        elif op in (5, 6):
            d.pop('bool_address')
        else:
            d.pop('bool_address'); d.pop('condition')
    elif op in (7, 8):
        d = CF_LOOP.unpack([w0, w1])
    elif op in (9, 11):
        d = CF_JMP.unpack([w0, w1])
    elif op == 10:
        d = dict(opcode=10, address_mode=bits(w1, 11, 1))
    else:
        d = CF_ALLOC.unpack([w0, w1])
    d['opcode'] = op
    return d

def cf_encode(d):
    op = d['opcode']
    if op in EXEC_OPS or op in (0, 15):
        w = CF_EXEC.pack(d, 2)
    elif op in (7, 8):
        w = CF_LOOP.pack(d, 2)
    elif op in (9, 11):
        w = CF_JMP.pack(d, 2)
    elif op == 10:
        w = [0, (d.get('address_mode', 0) << 11) | (10 << 12)]
    else:
        w = CF_ALLOC.pack(d, 2)
    return w[0], w[1] & 0xFFFF

def cf_unpack_pair(d0, d1, d2):
    return (d0, d1 & 0xFFFF), ((d1 >> 16) | ((d2 & 0xFFFF) << 16), d2 >> 16)

def cf_pack_pair(a, b):
    (a0, a1), (b0, b1) = a, b
    return [a0, (a1 & 0xFFFF) | ((b0 & 0xFFFF) << 16), (b0 >> 16) | (b1 << 16)]

# ---- fetch ----------------------------------------------------------------------------------

VFETCH = Fields([('opcode', 0, 0, 5), ('src_reg', 0, 5, 6), ('src_reg_am', 0, 11, 1), ('dst_reg', 0, 12, 6),
                 ('dst_reg_am', 0, 18, 1), ('must_be_one', 0, 19, 1), ('const_index', 0, 20, 5),
                 ('const_index_sel', 0, 25, 2), ('prefetch_count', 0, 27, 3), ('src_swiz', 0, 30, 2),
                 ('dst_swiz', 1, 0, 12), ('format_comp_all', 1, 12, 1), ('num_format_all', 1, 13, 1),
                 ('signed_rf_mode_all', 1, 14, 1), ('is_index_rounded', 1, 15, 1), ('format', 1, 16, 6),
                 ('reserved2', 1, 22, 2), ('exp_adjust', 1, 24, 6), ('is_mini_fetch', 1, 30, 1),
                 ('is_predicated', 1, 31, 1), ('stride', 2, 0, 8), ('offset', 2, 8, 23), ('pred_condition', 2, 31, 1)])
TFETCH = Fields([('opcode', 0, 0, 5), ('src_reg', 0, 5, 6), ('src_reg_am', 0, 11, 1), ('dst_reg', 0, 12, 6),
                 ('dst_reg_am', 0, 18, 1), ('fetch_valid_only', 0, 19, 1), ('const_index', 0, 20, 5),
                 ('tx_coord_denorm', 0, 25, 1), ('src_swiz', 0, 26, 6),
                 ('dst_swiz', 1, 0, 12), ('mag_filter', 1, 12, 2), ('min_filter', 1, 14, 2), ('mip_filter', 1, 16, 2),
                 ('aniso_filter', 1, 18, 3), ('arbitrary_filter', 1, 21, 3), ('vol_mag_filter', 1, 24, 2),
                 ('vol_min_filter', 1, 26, 2), ('use_comp_lod', 1, 28, 1), ('use_reg_lod', 1, 29, 1), ('unk', 1, 30, 1),
                 ('is_predicated', 1, 31, 1), ('use_reg_gradients', 2, 0, 1), ('sample_location', 2, 1, 1),
                 ('lod_bias', 2, 2, 7), ('unused', 2, 9, 5), ('dimension', 2, 14, 2), ('offset_x', 2, 16, 5),
                 ('offset_y', 2, 21, 5), ('offset_z', 2, 26, 5), ('pred_condition', 2, 31, 1)])
FETCH_NAMES = {0: 'vfetch', 1: 'tfetch', 16: 'getBCF', 17: 'getCompTexLOD', 18: 'getGradients',
               19: 'getWeights', 24: 'setTexLOD', 25: 'setGradientH', 26: 'setGradientV'}

def fetch_decode(w):
    op = bits(w[0], 0, 5)
    d = (VFETCH if op == 0 else TFETCH).unpack(w)
    d['kind'] = 'fetch'
    return d

def fetch_encode(d):
    return (VFETCH if d['opcode'] == 0 else TFETCH).pack(d, 3)

# ---- ALU ------------------------------------------------------------------------------------

ALU = Fields([('vector_dest', 0, 0, 6), ('vector_dest_rel', 0, 6, 1), ('abs_constants', 0, 7, 1),
              ('scalar_dest', 0, 8, 6), ('scalar_dest_rel', 0, 14, 1), ('export_data', 0, 15, 1),
              ('vector_write_mask', 0, 16, 4), ('scalar_write_mask', 0, 20, 4), ('vector_clamp', 0, 24, 1),
              ('scalar_clamp', 0, 25, 1), ('scalar_opc', 0, 26, 6),
              ('src3_swiz', 1, 0, 8), ('src2_swiz', 1, 8, 8), ('src1_swiz', 1, 16, 8), ('src3_reg_negate', 1, 24, 1),
              ('src2_reg_negate', 1, 25, 1), ('src1_reg_negate', 1, 26, 1), ('pred_condition', 1, 27, 1),
              ('is_predicated', 1, 28, 1), ('const_address_register_relative', 1, 29, 1), ('const_1_rel_abs', 1, 30, 1),
              ('const_0_rel_abs', 1, 31, 1),
              ('src3_reg', 2, 0, 8), ('src2_reg', 2, 8, 8), ('src1_reg', 2, 16, 8), ('vector_opc', 2, 24, 5),
              ('src3_sel', 2, 29, 1), ('src2_sel', 2, 30, 1), ('src1_sel', 2, 31, 1)])

VEC_NAMES = ['add', 'mul', 'max', 'min', 'seq', 'sgt', 'sge', 'sne', 'frc', 'trunc', 'floor', 'mad', 'cndeq',
             'cndge', 'cndgt', 'dp4', 'dp3', 'dp2add', 'cube', 'max4', 'setp_eq_push', 'setp_ne_push',
             'setp_gt_push', 'setp_ge_push', 'kill_eq', 'kill_gt', 'kill_ge', 'kill_ne', 'dst', 'maxa', 'op30', 'op31']
VEC_NSRC = [2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 3, 3, 3, 3, 2, 2, 3, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0]
SCL_NAMES = ['adds', 'adds_prev', 'muls', 'muls_prev', 'muls_prev2', 'maxs', 'mins', 'seqs', 'sgts', 'sges', 'snes',
             'frcs', 'truncs', 'floors', 'exp', 'logc', 'log', 'rcpc', 'rcpf', 'rcp', 'rsqc', 'rsqf', 'rsq', 'maxas',
             'maxasf', 'subs', 'subs_prev', 'setp_eq', 'setp_ne', 'setp_gt', 'setp_ge', 'setp_inv', 'setp_pop',
             'setp_clr', 'setp_rstr', 'kills_eq', 'kills_gt', 'kills_ge', 'kills_ne', 'kills_one', 'sqrt', 'op41',
             'mulsc', 'mulsc', 'addsc', 'addsc', 'subsc', 'subsc', 'sin', 'cos', 'retain_prev'] + ['op%d' % i for i in range(51, 64)]
# scalar ops: number of operands (0, 1 = one source register, 2 = constant + temporary)
SCL_NSRC = [1] * 64
for _i in (33, 50): SCL_NSRC[_i] = 0
for _i in range(42, 48): SCL_NSRC[_i] = 2
for _i in (41,) + tuple(range(51, 64)): SCL_NSRC[_i] = 0
# two-component single-source scalar ops (a = W component, b = X or Z)
SCL_TWO = {0, 2, 4, 5, 6, 23, 24, 25}

def alu_decode(w):
    d = ALU.unpack(w)
    d['kind'] = 'alu'
    return d

def alu_encode(d):
    return ALU.pack(d, 3)

# ---- microcode program ----------------------------------------------------------------------

def words_of(ucode):
    return list(struct.unpack('>%dI' % (len(ucode) // 4), ucode[:len(ucode) // 4 * 4]))

def parse(ucode):
    """-> (cfs [dict], slots {slot: dict}); slot dicts are ALU or fetch per the exec sequence bits"""
    w = words_of(ucode)
    nslots = len(w) // 3
    cfs = []
    bound = nslots
    i = 0
    while i < bound:
        a, b = cf_unpack_pair(*w[3 * i:3 * i + 3])
        for x in (a, b):
            d = cf_decode(*x)
            cfs.append(d)
            if d['opcode'] in EXEC_OPS:
                bound = min(bound, d['address'])
        i += 1
    slots = {}
    for d in cfs:
        if d['opcode'] not in EXEC_OPS: continue
        seq = d['sequence']
        for k in range(d['count']):
            s = d['address'] + k
            ww = w[3 * s:3 * s + 3]
            if len(ww) < 3: raise ValueError(f'exec past the end of the microcode (slot {s})')
            is_fetch = (seq >> (2 * k)) & 1
            slots[s] = fetch_decode(ww) if is_fetch else alu_decode(ww)
            slots[s]['serialize'] = (seq >> (2 * k + 1)) & 1
    return cfs, slots

SWZ = 'xyzw'

def _src_swiz(sw, n=4):
    # ALU source swizzle: 2 bits per component, relative to the component itself
    return ''.join(SWZ[((sw >> (2 * c)) + c) & 3] for c in range(n))

def _fdst_swiz(sw):
    return ''.join('xyzw01?_'[(sw >> (3 * c)) & 7] for c in range(4))

def _mask(m, comps='xyzw'):
    return ''.join(comps[c] if m & (1 << c) else '_' for c in range(4))

def alu_text(d):
    out = []
    def src(i):
        reg = d[f'src{i}_reg']; sel = d[f'src{i}_sel']; neg = d[f'src{i}_reg_negate']
        sw = _src_swiz(d[f'src{i}_swiz'])
        if sel:
            s = ('|r%d|' if reg & 0x80 else 'r%d') % (reg & 0x3F) + ('[aL]' if reg & 0x40 else '')
        else:
            s = 'c%d' % reg
            s = '|' + s + '|' if d['abs_constants'] else s
        return ('-' if neg else '') + s + ('.' + sw if sw != 'xyzw' else '')
    vop = d['vector_opc']
    pred = ('(p0) ' if d['pred_condition'] else '(!p0) ') if d['is_predicated'] else ''
    if d['vector_write_mask'] or vop in range(20, 28):
        dst = ('oExp%d' if d['export_data'] else 'r%d') % d['vector_dest']
        out.append(f"{pred}{VEC_NAMES[vop]}{'_sat' if d['vector_clamp'] else ''} {dst}.{_mask(d['vector_write_mask'])}, " +
                   ', '.join(src(i) for i in range(1, VEC_NSRC[vop] + 1)))
    sop = d['scalar_opc']
    if d['scalar_write_mask'] or sop not in (50,) and (sop in range(27, 40)):
        dst = ('oExp%d' if d['export_data'] else 'r%d') % d['scalar_dest']
        out.append(f"{pred}{SCL_NAMES[sop]}{'_sat' if d['scalar_clamp'] else ''} {dst}.{_mask(d['scalar_write_mask'])}, " +
                   (src(3) if SCL_NSRC[sop] == 1 else
                    f"c{d['src3_reg']}, r{(sop & 1) | (d['src3_sel'] << 1) | (d['src3_swiz'] & 0x3C)}" if SCL_NSRC[sop] == 2 else ''))
    return ' + '.join(out) if out else 'nop'

def fetch_text(d):
    pred = ('(p0) ' if d['pred_condition'] else '(!p0) ') if d['is_predicated'] else ''
    if d['opcode'] == 0:
        return (f"{pred}vfetch{'_mini' if d['is_mini_fetch'] else '_full'} r{d['dst_reg']}.{_fdst_swiz(d['dst_swiz'])}, "
                f"r{d['src_reg']}.{SWZ[d['src_swiz']]}, vf{d['const_index'] * 3 + d['const_index_sel']} "
                f"fmt={d['format']} stride={d['stride']} off={d['offset']} signed={d['format_comp_all']} "
                f"norm={1 - d['num_format_all']} exp={d['exp_adjust']}")
    sw = ''.join(SWZ[(d['src_swiz'] >> (2 * c)) & 3] for c in range(3))
    return (f"{pred}{FETCH_NAMES.get(d['opcode'], 'fetch%d' % d['opcode'])} r{d['dst_reg']}.{_fdst_swiz(d['dst_swiz'])}, "
            f"r{d['src_reg']}.{sw}, tf{d['const_index']} dim={d['dimension']} lodbias={d['lod_bias']} "
            f"filt={d['mag_filter']}{d['min_filter']}{d['mip_filter']}{d['aniso_filter']} reglod={d['use_reg_lod']} complod={d['use_comp_lod']}"
            f" off={d['offset_x']},{d['offset_y']},{d['offset_z']} fvo={d['fetch_valid_only']}")

def disasm(ucode):
    cfs, slots = parse(ucode)
    lines = []
    for n, d in enumerate(cfs):
        op = d['opcode']
        name = CF_NAMES[op]
        extra = ''
        if op in EXEC_OPS:
            extra = f" addr={d['address']} cnt={d['count']}"
            if 'bool_address' in d: extra += f" b{d['bool_address']}={d['condition']}"
            elif op in (5, 6): extra += f" p0={d['condition']}"
        elif op in (7, 8):
            extra = f" addr={d['address']} i{d['loop_id']}" + (f" pbreak={d['condition']}" if op == 8 and d['predicated_break'] else '')
        elif op in (9, 11):
            extra = f" addr={d['address']} uncond={d['unconditional']} pred={d['predicated']} b{d['bool_address']}={d['condition']}"
        elif op == 12:
            extra = f" type={d['alloc_type']} size={d['size']}"
        lines.append(f'cf{n:3d}: {name}{extra}')
        if op in EXEC_OPS:
            for k in range(d['count']):
                s = slots[d['address'] + k]
                lines.append(f"      {d['address'] + k:3d} " + (fetch_text(s) if s['kind'] == 'fetch' else alu_text(s)))
        if op in END_OPS: pass
    return '\n'.join(lines)
