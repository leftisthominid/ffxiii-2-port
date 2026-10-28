# Direct3D 9 shader bytecode (ps_3_0 of the Steam version's materials) -> Xbox 360 compiled shader
# (the container the 360 game loads: header, constant table, literal table, shader info, literal
# values and Xenos microcode; see xenos.py for the instruction formats).
# The program is translated instruction by instruction (no optimisation): every D3D9 temporary,
# input and output gets its own Xenos register, D3D9 macros (nrm, lrp, pow, ...) are expanded,
# structured control flow becomes conditional jumps / loops on the same bool and integer
# constants. The constant table keeps the names, types and default values of the PC table; the
# registers of named constants that the 360 engine sets at fixed registers come from the 360 data
# (engine), the others keep their PC registers; literal constants move to the top registers as in
# the 360 compiler's output.
import re, struct
from . import d3d9, xenos

class CompileError(Exception):
    pass

# ---- constant tables (D3DXSHADER_CONSTANTTABLE, little-endian on PC, big-endian on the 360) --------

def read_ctab(b, e):
    size, creator, version, n, cinfo, flags, target = struct.unpack_from(e + '7I', b, 0)
    def s(o): return b[o:b.index(b'\0', o)]
    def typeinfo(o):
        cls, typ, rows, cols, elems, nmem, moff = struct.unpack_from(e + '7H', b, o)
        members = []
        for k in range(nmem):
            mn, mt = struct.unpack_from(e + '2I', b, moff + 8 * k)
            members.append((s(mn), typeinfo(mt)))
        return (cls, typ, rows, cols, elems, tuple(members))
    out = []
    for i in range(n):
        name, rset, ridx, rcnt, res, tinfo, dflt = struct.unpack_from(e + 'IHHHHII', b, cinfo + 20 * i)
        ti = typeinfo(tinfo)
        dv = None
        if dflt:
            dv = b[dflt:dflt + 16 * rcnt] if rset == 2 else b[dflt:dflt + 4 * rcnt * 4]
        out.append(dict(name=s(name), set=rset, reg=ridx, count=rcnt, type=ti, default=dv))
    return dict(version=version, creator=s(creator), target=s(target), flags=flags, consts=out)

def pc_ctab(prog):
    i = 4
    while i < len(prog):
        t = struct.unpack_from('<I', prog, i)[0]
        if t & 0xFFFF != 0xFFFE: return None
        if prog[i + 4:i + 8] == b'CTAB':
            return read_ctab(prog[i + 8:i + 4 + 4 * (t >> 16)], '<')
        i += 4 + 4 * (t >> 16)
    return None

def _swap_default(dv, rset):
    """default values: 32-bit words, byte order of the target"""
    return b''.join(dv[i:i + 4][::-1] for i in range(0, len(dv) - 3, 4))

def write_ctab(ct, creator=b'2.0.11626.0', flags=0x400):
    """big-endian constant table, layout as the 360 compiler writes it: header, constant infos,
    then per constant: name, type info (+ members), default value; creator and target last"""
    e = '>'
    consts = ct['consts']
    hdr_size = 0x1C
    out = bytearray(hdr_size + 20 * len(consts))
    def add(b, align=1):
        while len(out) % align: out.append(0xAB)
        o = len(out); out.extend(b); return o
    def add_type(ti):
        cls, typ, rows, cols, elems, members = ti
        moffs = []
        for mn, mt in members:
            moffs.append((add(mn + b'\0'), add_type(mt)))
        mo = 0
        if moffs:
            mo = add(b''.join(struct.pack(e + '2I', a, b) for a, b in moffs), 4)
        return add(struct.pack(e + '7H', cls, typ, rows, cols, elems, len(members), mo) + b'\0\0', 4)
    for i, c in enumerate(consts):
        no = add(c['name'] + b'\0')
        to = add_type(c['type'])
        do = 0
        if c.get('default') is not None:
            do = add(_swap_default(c['default'], c['set']), 4)
        struct.pack_into(e + 'IHHHHII', out, hdr_size + 20 * i, no, c['set'], c['reg'], c['count'], 0, to, do)
    to = add(ct['target'] + b'\0', 4)
    co = add(creator + b'\0')
    while len(out) % 4: out.append(0xAB)
    struct.pack_into(e + '7I', out, 0, hdr_size, co, ct['version'], len(consts), hdr_size, flags, to)
    return bytes(out)

# ---- the compiler -------------------------------------------------------------------------------

SCL = {n: i for i, n in enumerate(xenos.SCL_NAMES[:51])}
VEC = {n: i for i, n in enumerate(xenos.VEC_NAMES)}

class Opnd:
    """a Xenos ALU source: temp register (with abs) or float constant"""
    __slots__ = ('temp', 'reg', 'swz', 'neg', 'abs', 'rel')
    def __init__(self, temp, reg, swz=(0, 1, 2, 3), neg=False, abs_=False, rel=None):
        self.temp, self.reg, self.swz, self.neg, self.abs, self.rel = temp, reg, tuple(swz), neg, abs_, rel
    def with_swz(self, swz):
        return Opnd(self.temp, self.reg, swz, self.neg, self.abs, self.rel)
    def negated(self):
        return Opnd(self.temp, self.reg, self.swz, not self.neg, self.abs, self.rel)

def _rel_swz(swz):
    return sum(((s - c) & 3) << (2 * c) for c, s in enumerate(swz))

def _abs_fetch_swz(swz, n=3):
    return sum((swz[c] & 3) << (2 * c) for c in range(n))

class Ps:
    """the compiled program before layout: a tree of code blocks and control nodes"""

class Compiler:
    def __init__(self, prog, interp=None, engine=None, folder=None):
        """prog: PC ps_3_0 bytecode. interp: the 360 interpolator list of the material's vertex
        shader [(usage, index, mask)] in register order (None: the PC inputs, sorted by semantic,
        all components). engine: the 360 engine's constant rules (see engine_rules). folder: the top
        folder of the file the shader is from (chr, bg, vfx..), for rules per folder."""
        self.folder = folder
        self.version, self.ins, self.comments = d3d9.parse(prog)
        self.is_ps = self.version >> 16 == 0xFFFF
        self.sfx = '_ps' if self.is_ps else '_vs'
        if (self.version & 0xFFFF) != 0x0300:
            raise CompileError('shader model %x' % (self.version & 0xFFFF))
        self.ct = pc_ctab(prog)
        if self.ct is None:
            raise CompileError('no constant table')
        self.engine = engine or {}
        self.prog = prog
        self.interp = interp
        self.code = []                 # the current list of Xenos instructions
        self.tree = [('code', self.code)]
        self.stack = []

    # ---- registers ----
    def setup(self):
        ins = self.ins
        self.inputs = {}               # v# -> (usage, index, mask)
        self.samplers = {}             # s# -> '2d' / 'cube' / 'volume'
        self.misc = set()
        self.defs = {}                 # c# -> 4 floats
        self.defi = {}                 # i# -> 4 ints
        self.loops = []                # (360 loop constant, PC i#) per rep
        self.defb = {}
        for i in ins:
            if i.name == 'dcl':
                r = i.dst
                if r.type == d3d9.INPUT: self.inputs[r.num] = (i.values[0], i.values[1], r.mask)
                elif r.type == d3d9.SAMPLER: self.samplers[r.num] = i.dcl
                elif r.type == d3d9.MISCTYPE: self.misc.add(r.num)
                elif r.type == d3d9.OUTPUT and not self.is_ps: pass
                else: raise CompileError('dcl of register type %d' % r.type)
            elif i.name == 'def': self.defs[i.dst.num] = i.values
            elif i.name == 'defi': self.defi[i.dst.num] = i.values
            elif i.name == 'defb': self.defb[i.dst.num] = i.values[0]
        if not self.is_ps:
            return self.setup_vs()
        # interpolators: PS input registers R0.. in the order of the vertex shader's list
        if self.interp is None:
            self.interp = sorted((u, x, 0xF) for (u, x, m) in self.inputs.values())
        pos = {(u, x): n for n, (u, x, m) in enumerate(self.interp)}
        self.vmap = {}
        for v, (u, x, m) in self.inputs.items():
            if (u, x) not in pos:
                raise CompileError(f'input {d3d9.USAGES.get(u, u)}{x} not written by the vertex shader')
            self.vmap[v] = pos[(u, x)]
        nreg = len(self.interp)
        self.param_gen = None
        if self.misc:
            self.param_gen = nreg; nreg += 1
        temps = sorted({r.num for i in ins for r in ([i.dst] if i.dst is not None else []) + i.src
                        if r.type == d3d9.TEMP})
        self.tmap = {}
        for t in temps:
            self.tmap[t] = nreg; nreg += 1
        self.outs = {}                 # ('c', n) / ('depth', 0) -> register
        for i in ins:
            if i.dst is not None and i.dst.type in (d3d9.COLOROUT, d3d9.DEPTHOUT) and i.name != 'dcl':
                k = ('c', i.dst.num) if i.dst.type == d3d9.COLOROUT else ('depth', 0)
                if k not in self.outs: self.outs[k] = nreg; nreg += 1
        self.first_scratch = nreg
        self.scratch_next = nreg
        self.max_reg = nreg - 1
        self.constants()

    def setup_vs(self):
        """vertex shader: inputs fetched into R1.. at the start (R0.x holds the vertex index until
        the fetches are done); outputs in registers, exported at the end: the position, then the
        interpolators in (usage, index) order"""
        ins = self.ins
        self.vin = []                  # [(v#, usage, index, mask)] in declaration order
        self.vouts = {}                # o# -> (usage, index, mask)
        for i in ins:
            if i.name != 'dcl': continue
            r = i.dst
            if r.type == d3d9.INPUT: self.vin.append((r.num, i.values[0], i.values[1], r.mask))
            elif r.type == d3d9.OUTPUT: self.vouts[r.num] = (i.values[0], i.values[1], r.mask)
        nreg = 1
        self.vmap = {}
        for v, u, x, m in self.vin:
            self.vmap[v] = nreg; nreg += 1
        temps = sorted({r.num for i in ins for r in ([i.dst] if i.dst is not None else []) + i.src
                        if r.type == d3d9.TEMP})
        self.tmap = {}
        for t in temps:
            self.tmap[t] = nreg; nreg += 1
        self.outs = {}
        for o in sorted(self.vouts):
            self.outs[('o', o)] = nreg; nreg += 1
        pos = [o for o, (u, x, m) in self.vouts.items() if u == 0 and x == 0]
        if len(pos) != 1: raise CompileError('vertex shader without one position output')
        self.pos_out = pos[0]
        others = sorted((u, x, m, o) for o, (u, x, m) in self.vouts.items() if o != self.pos_out)
        for u, x, m, o in others:
            if u in (4, 11): raise CompileError('vertex shader output ' + d3d9.USAGES[u])
        self.interp = [(u, x, m) for u, x, m, o in others]
        self.interp_out = [o for u, x, m, o in others]
        self.param_gen = None
        self.a0 = None                 # register holding the mova source (rounded), per component
        self.bones = None              # skinning: (u, v) registers of the bone palette texels
        self.first_scratch = nreg
        self.scratch_next = nreg
        self.max_reg = nreg - 1
        self.constants()

    def scratch(self):
        r = self.scratch_next; self.scratch_next += 1
        self.max_reg = max(self.max_reg, r)
        return r

    def release_scratch(self):
        self.scratch_next = self.first_scratch

    # ---- constants ----
    def constants(self):
        """registers of named constants and literals: a constant the 360 engine has at a fixed
        register goes there; the others keep their PC registers unless taken, else the first free
        ones; 360-only constants of this kind of material are added; literals (def) go to the top
        registers in groups of four, as the 360 compiler puts them"""
        sfx = self.sfx
        fixed = self.engine.get('fixed' + sfx, {})
        counts = self.engine.get('count' + sfx, {})
        names = frozenset(c['name'] for c in self.ct['consts'])
        extra = list(self.engine.get('extra' + sfx, {}).get(names, []))
        bone = {b'bone_palette_texture', b'JointMatParam'}
        skinned = not self.is_ps and any(c['set'] == 2 and c['name'] == b'jointMatrices' for c in self.ct['consts'])
        for e, (ent, preds) in self.engine.get('extra_pred' if self.is_ps else 'extra_pred_vs', {}).items():
            if e in bone: continue
            if e not in {x['name'] for x in extra} and names & set(preds): extra.append(ent)
        extra = [x for x in extra if x['name'] not in bone]
        if self.is_ps and self.folder:
            for ent in self.engine.get('folder_ps', {}).get(self.folder, []):
                if ent['name'] not in {x['name'] for x in extra} | names: extra.append(dict(ent))
        if skinned:
            extra += [dict(x) for x in self.engine.get('bone_vs', [])]
        # PC-only constants (no 360 table has them: lightbit0/1 of the vertex-lit shaders, float light
        # switches where the 360 has bools): the 360 layout of the shaders that have them (8-light
        # arrays at their registers, the light4..6 bools), and the switches read as those bools
        vmap = {k: v for k, v in self.engine.get('variant' + sfx, {}).items() if k != b'jointMatrices'}
        self.pc_only = {}                                 # PC c# -> name
        variant = {}
        for nm in sorted(names & set(vmap)): variant.update(vmap[nm])
        if variant:
            fixed = dict(fixed); counts = dict(counts)
            for m, ent in variant.items():
                if ent['set'] == 2: fixed[m] = ent['reg']; counts[m] = ent['count']
            have = {x['name'] for x in extra}
            for m, ent in variant.items():
                if m not in names and m not in have and m not in bone: extra.append(dict(ent))
        self.cmap = {}                                    # PC c# -> 360 c#
        self.consts = []
        self.tap_consts = {}                              # PC c# -> (360 base register, tap index)
        lits = sorted(self.defs)
        groups = (len(lits) + 3) // 4
        fixed_regs = set()
        for c in self.ct['consts']:
            if c['set'] == 2 and c['name'] in fixed:
                fixed_regs |= set(range(fixed[c['name']], fixed[c['name']] + counts.get(c['name'], c['count'])))
        base = 256 - 4 * groups
        while base > 0 and set(range(base, base + 4 * groups)) & fixed_regs: base -= 4
        used = set(range(base, base + 4 * groups))
        todo = []
        self.joint = None
        for c in self.ct['consts']:
            c = dict(c)
            if c['name'] in vmap:
                self.pc_only[c['reg']] = c['name']; continue
            if c['set'] == 2 and not self.is_ps and c['name'] == b'jointMatrices':
                # skinning: the 360 engine has the bone matrices in a texture (bone_palette_texture,
                # JointMatParam), not in constants
                self.joint = (c['reg'], c['count'])
                continue
            if c['set'] == 2:
                n360 = counts.get(c['name'], c['count'])
                if c['name'] in fixed:
                    c['reg360'] = fixed[c['name']]; c['n360'] = n360
                    regs = set(range(c['reg360'], c['reg360'] + n360))
                    if regs & used: raise CompileError(f'fixed register of {c["name"]} taken')
                    used |= regs
                else:
                    c['n360'] = n360; todo.append(c)
            self.consts.append(c)
        if self.joint:
            have = {c['name'] for c in extra}
            if b'JointMatParam' not in have or b'bone_palette_texture' not in have:
                raise CompileError('skinned vertex shader without the 360 bone palette constants')
        for c in extra:
            if c['set'] == 0 and c['name'] not in {x['name'] for x in self.consts}:
                c = dict(c); c['extra'] = True; self.consts.append(c)
            if c['set'] == 3 and c['name'] not in {x['name'] for x in self.consts}:
                c = dict(c); c['extra'] = True; self.consts.append(c)
            if c['set'] == 2 and c['name'] not in {x['name'] for x in self.consts}:
                c = dict(c); c['n360'] = c['count']; c['extra'] = True
                if c['name'] in fixed: c['reg360'] = fixed[c['name']]; used |= set(range(c['reg360'], c['reg360'] + c['count']))
                else: todo.append(c)
                self.consts.append(c)
        for c in todo:
            n = c['n360']
            want = c['reg']
            if not (set(range(want, want + n)) & used) and want + n <= 256:
                c['reg360'] = want
            else:
                r = 0
                while set(range(r, r + n)) & used: r += 1
                if r + n > 256: raise CompileError('out of constant registers')
                c['reg360'] = r
            used |= set(range(c['reg360'], c['reg360'] + n))
        for c in self.consts:
            if c['set'] != 2 or c.get('extra'): continue
            for k in range(c['count']):
                if c['n360'] < c['count']:              # an array the 360 engine sets as one value
                    self.cmap[c['reg'] + k] = c['reg360']
                    self.tap_consts[c['reg'] + k] = k
                else:
                    self.cmap[c['reg'] + k] = c['reg360'] + k
        self.extra_consts = {c['name']: c.get('reg360', c['reg']) for c in self.consts if c.get('extra')}
        for c in self.consts:
            if c['set'] == 2:
                c['reg'] = c['reg360']; c['count'] = c['n360']
        self.lit_base = base
        for n, c in enumerate(lits):
            self.cmap[c] = base + n
        self.literals = [self.defs[c] for c in lits] + [[0.0] * 4] * (4 * groups - len(lits))
        self.lit_groups = groups

    def creg(self, n):
        if n not in self.cmap:
            raise CompileError(f'c{n} is neither a named constant nor a literal')
        return self.cmap[n]

    # ---- operands ----
    def src(self, r, allow_const=True):
        """D3D9 source register -> Opnd"""
        neg = r.srcmod in (1, 12)
        ab = r.srcmod in (11, 12)
        if r.srcmod not in (0, 1, 11, 12):
            raise CompileError('source modifier ' + d3d9.SRC_MOD[r.srcmod])
        swz = tuple(r.swz)
        if r.type == d3d9.TEMP:
            if r.num in self.taps: self.cur_taps.add(self.taps[r.num])
            return Opnd(True, self.tmap[r.num], swz, neg, ab)
        if r.type == d3d9.INPUT:
            if r.rel is not None: raise CompileError('relative input register')
            return Opnd(True, self.vmap[r.num], swz, neg, ab)
        if r.type == d3d9.CONST and r.rel is not None:
            return self.rel_const(r, swz, neg, ab)
        if r.type == d3d9.CONST:
            if r.num in self.tap_consts: self.cur_taps.add(self.tap_consts[r.num])
            o = Opnd(False, self.creg(r.num), swz, neg, False)
            if ab or not allow_const:
                t = self.scratch()
                self.vop('max', t, 0xF, [Opnd(False, o.reg), Opnd(False, o.reg)])
                return Opnd(True, t, swz, neg, ab)
            return o
        if r.type == d3d9.MISCTYPE:
            return self.misc_src(r, swz, neg, ab)
        raise CompileError('source register type %d' % r.type)

    def rel_const(self, r, swz, neg, ab):
        """c[a0.c + n] in a vertex shader: a bone matrix row (jointMatrices -> a fetch from the
        bone palette texture, as the 360 shaders do) or a relative constant read through a0"""
        if self.is_ps or r.rel.type != d3d9.ADDR: raise CompileError('relative addressing')
        comp = r.rel.swz[0]
        if self.a0 is None: raise CompileError('relative read before mova')
        if self.joint and self.joint[0] <= r.num < self.joint[0] + 3:
            row = r.num - self.joint[0]
            u, v = self.bones
            coord = self.scratch()
            self.vop('max', coord, 0x1, [Opnd(True, u, (comp,) * 4), Opnd(True, u, (comp,) * 4)])
            self.vop('max', coord, 0x2, [Opnd(True, v, (comp,) * 4), Opnd(True, v, (comp,) * 4)])
            t = self.scratch()
            smp = self.extra_consts[b'bone_palette_texture']
            self.tfetch(t, 0xF, (0, 1, 2, 3), Opnd(True, coord, (0, 1, 0, 0)), 16 + smp, 1,
                        use_comp_lod=0, fetch_valid_only=1, offset_x=2 * row + 1)
            return Opnd(True, t, swz, neg, ab)
        if self.joint and self.joint[0] <= r.num < self.joint[0] + self.joint[1]:
            raise CompileError('jointMatrices read at an unexpected offset')
        # a0 = the rounded component, then the constant read relative to a0
        self.sop('maxas', 0, 0, Opnd(True, self.a0, (comp,) * 4), 3, comp_b=3)
        o = Opnd(False, self.creg(r.num), swz, neg, False, rel='a0')
        if ab:
            t = self.scratch()
            self.vop('max', t, 0xF, [o.with_swz((0, 1, 2, 3)), o.with_swz((0, 1, 2, 3))])
            return Opnd(True, t, swz, neg, ab)
        return o

    def misc_src(self, r, swz, neg, ab):
        """vFace (1): the sign of the param-gen register's x; vPos (0): its xy"""
        pg = self.param_gen
        if r.num == 1:          # vFace: ps_param_gen.x is negative for back faces -> +1 / -1
            t = self.scratch()
            one = self.literal(1.0)
            # t = cndge(pg.x, 1, -1): front facing when x >= 0
            self.vop('cndge', t, 0xF, [Opnd(True, pg, (0, 0, 0, 0)), one, one.negated()])
            return Opnd(True, t, swz, neg, ab)
        if r.num == 0:          # vPos: |pg.xy|
            t = self.scratch()
            self.vop('max', t, 0xF, [Opnd(True, pg, (0, 1, 2, 3), abs_=True), Opnd(True, pg, (0, 1, 2, 3), abs_=True)])
            return Opnd(True, t, swz, neg, ab)
        raise CompileError('misc register %d' % r.num)

    def literal(self, v):
        """an Opnd for a scalar literal (replicated): an existing literal component or a new one"""
        for k, vec in enumerate(self.literals):
            for c in range(4):
                if vec[c] == v and struct.pack('<f', vec[c]) == struct.pack('<f', v):
                    return Opnd(False, self.lit_base + k, (c, c, c, c))
        for k, x in enumerate(self.extra_literals):
            if struct.pack('<f', x) == struct.pack('<f', v):
                return Opnd(False, ('extra', k), (0, 0, 0, 0))
        self.extra_literals.append(v)
        k = len(self.extra_literals) - 1
        return Opnd(False, ('extra', k), (0, 0, 0, 0))

    # ---- instruction emission ----
    def alu(self, d):
        self.code.append(d)

    def _place(self, d, opnds, slots):
        """put operands into src1..src3 (max two constants)"""
        nconst = 0
        for o in opnds:
            if not o.temp: nconst += 1
        if nconst > 2:
            raise CompileError('three constants in one instruction')
        nc = 0
        for slot, o in zip(slots, opnds):
            if not o.temp:
                if o.rel == 'a0':
                    d['const_0_rel_abs' if nc == 0 else 'const_1_rel_abs'] = 1
                    d['const_address_register_relative'] = 1
                nc += 1
            d[f'src{slot}_sel'] = 1 if o.temp else 0
            if o.temp:
                d[f'src{slot}_reg'] = (o.reg & 0x3F) | (0x80 if o.abs else 0)
            else:
                d[f'src{slot}_reg'] = o.reg
            d[f'src{slot}_swiz'] = _rel_swz(o.swz)
            d[f'src{slot}_reg_negate'] = 1 if o.neg else 0

    def vop(self, name, dst, mask, srcs, sat=False, export=False):
        """vector ALU op (scalar unit idle)"""
        srcs = self.limit_consts(srcs)
        d = dict(vector_opc=VEC[name], vector_dest=dst, vector_write_mask=mask, vector_clamp=1 if sat else 0,
                 scalar_opc=SCL['retain_prev'], scalar_write_mask=0, scalar_dest=0, export_data=1 if export else 0)
        slots = [1, 2, 3][:len(srcs)]
        self._place(d, srcs, slots)
        for s in (1, 2, 3):
            if f'src{s}_sel' not in d:
                d[f'src{s}_sel'] = 1; d[f'src{s}_reg'] = 0; d[f'src{s}_swiz'] = 0; d[f'src{s}_reg_negate'] = 0
        d['kind'] = 'alu'
        self.alu(d)

    def sop(self, name, dst, mask, src, comp, sat=False, export=False, comp_b=None):
        """scalar ALU op on component `comp` of src (and `comp_b` for two-operand ops) (vector unit idle)"""
        if not src.temp:
            t = self.scratch()
            self.vop('max', t, 0xF, [Opnd(False, src.reg), Opnd(False, src.reg)])
            src = Opnd(True, t, src.swz, src.neg, src.abs)
        a = src.swz[comp]
        b = src.swz[comp_b] if comp_b is not None else 0
        # src3 swizzle: a = component (3 + bits[6:8]) & 3, b = (0 + bits[0:2]) & 3
        swz = ((a - 3) & 3) << 6 | (b & 3)
        d = dict(vector_opc=VEC['max'], vector_dest=0, vector_write_mask=0, vector_clamp=0,
                 scalar_opc=SCL[name], scalar_write_mask=mask, scalar_dest=dst, scalar_clamp=1 if sat else 0,
                 export_data=1 if export else 0,
                 src1_sel=1, src1_reg=0, src1_swiz=0, src1_reg_negate=0,
                 src2_sel=1, src2_reg=0, src2_swiz=0, src2_reg_negate=0,
                 src3_sel=1, src3_reg=(src.reg & 0x3F) | (0x80 if src.abs else 0), src3_swiz=swz,
                 src3_reg_negate=1 if src.neg else 0, kind='alu')
        self.alu(d)

    def limit_consts(self, srcs):
        n = sum(1 for o in srcs if not o.temp)
        out = list(srcs)
        k = len(out) - 1
        while n > 2:
            if not out[k].temp:
                o = out[k]
                t = self.scratch()
                self.vop('max', t, 0xF, [Opnd(False, o.reg), Opnd(False, o.reg)])
                out[k] = Opnd(True, t, o.swz, o.neg, False)
                n -= 1
            k -= 1
        return out

    def tfetch(self, dst, mask, chan, coord, tf, dim, opcode=1, **kw):
        """coord: Opnd (temp, no modifiers); chan: the texture channel for each destination component"""
        if not coord.temp or coord.neg or coord.abs:
            t = self.scratch()
            self.vop('max', t, 0xF, [coord, coord])
            coord = Opnd(True, t, coord.swz)
        dsw = 0
        for c in range(4):
            dsw |= ((chan[c] if mask >> c & 1 else 7) & 7) << (3 * c)
        d = dict(opcode=opcode, src_reg=coord.reg, src_reg_am=0, dst_reg=dst, dst_reg_am=0, fetch_valid_only=0,
                 const_index=tf, tx_coord_denorm=0, src_swiz=_abs_fetch_swz(coord.swz), dst_swiz=dsw,
                 mag_filter=3, min_filter=3, mip_filter=3, aniso_filter=7, arbitrary_filter=0,
                 vol_mag_filter=3, vol_min_filter=3, use_comp_lod=1, use_reg_lod=0, unk=0, is_predicated=0,
                 use_reg_gradients=0, sample_location=0, lod_bias=0, unused=0, dimension=dim,
                 offset_x=0, offset_y=0, offset_z=0, pred_condition=0, kind='fetch')
        d.update(kw)
        self.code.append(d)

    # ---- D3D9 instructions ----
    def dst(self, r):
        """-> (register, mask, export?)"""
        if r.type == d3d9.TEMP: return self.tmap[r.num], r.mask
        if r.type == d3d9.COLOROUT: return self.outs[('c', r.num)], r.mask
        if r.type == d3d9.OUTPUT: return self.outs[('o', r.num)], r.mask
        if r.type == d3d9.DEPTHOUT: return self.outs[('depth', 0)], 0x1
        raise CompileError('destination register type %d' % r.type)

    def translate(self, i):
        """one D3D9 instruction (with the shadow-tap bookkeeping: a value computed from element k
        of a tap-offset array carries tap k to the texture fetch that uses it)"""
        self.cur_taps = set()
        self.translate1(i)
        if i.dst is not None and i.dst.type == d3d9.TEMP and i.name not in ('dcl', 'def', 'defi', 'defb'):
            if len(self.cur_taps) == 1: self.taps[i.dst.num] = next(iter(self.cur_taps))
            elif i.dst.mask == 0xF or not self.cur_taps: self.taps.pop(i.dst.num, None)

    TAP_OFFSETS = ((-1, -1), (1, -1), (-1, 1), (1, 1))     # half texels: the four taps at +-0.5 texel

    def translate1(self, i):
        n = i.name
        if n in ('dcl', 'def', 'defi', 'defb', 'nop'): return
        if i.pred is not None:
            return self.predicated(i)
        if i.dst is not None and i.dst.shift: raise CompileError('destination shift')
        sat = bool(i.dst.mods & 1) if i.dst is not None else False
        if n in ('mov', 'add', 'sub', 'mul', 'mad', 'min', 'max', 'slt', 'sge', 'frc', 'abs', 'dp3', 'dp4',
                 'dp2add', 'cmp'):
            d, m = self.dst(i.dst)
            s = [self.src(x) for x in i.src]
            if n == 'mov': self.vop('max', d, m, [s[0], s[0]], sat)
            elif n == 'abs':
                a = s[0]
                if not a.temp: a = self.to_temp(a)
                a = Opnd(True, a.reg, a.swz, False, True)
                self.vop('max', d, m, [a, a], sat)
            elif n == 'add': self.vop('add', d, m, s, sat)
            elif n == 'sub': self.vop('add', d, m, [s[0], s[1].negated()], sat)
            elif n == 'mul': self.vop('mul', d, m, s, sat)
            elif n == 'mad': self.vop('mad', d, m, s, sat)
            elif n == 'min': self.vop('min', d, m, s, sat)
            elif n == 'max': self.vop('max', d, m, s, sat)
            elif n == 'slt': self.vop('sgt', d, m, [s[1], s[0]], sat)
            elif n == 'sge': self.vop('sge', d, m, s, sat)
            elif n == 'frc': self.vop('frc', d, m, s, sat)
            elif n == 'dp3': self.vop('dp3', d, m, s, sat)
            elif n == 'dp4': self.vop('dp4', d, m, s, sat)
            elif n == 'dp2add':
                c = s[2]; s[2] = c.with_swz((c.swz[0],) * 4)
                self.vop('dp2add', d, m, s, sat)
            elif n == 'cmp': self.vop('cndge', d, m, s, sat)
            self.release_scratch(); return
        if n in ('rcp', 'rsq', 'exp', 'log'):
            d, m = self.dst(i.dst)
            s = self.src(i.src[0])
            name = {'rcp': 'rcp', 'rsq': 'rsq', 'exp': 'exp', 'log': 'log'}[n]
            if n in ('rsq', 'log'):
                if not s.temp: s = self.to_temp(s)
                s = Opnd(True, s.reg, s.swz, s.neg, True)
            self.sop(name, d, m, s, 3, sat)
            self.release_scratch(); return
        if n == 'pow':
            d, m = self.dst(i.dst)
            a = self.src(i.src[0]); b = self.src(i.src[1])
            if not a.temp: a = self.to_temp(a)
            t = self.scratch()
            self.sop('log', t, 0x8, Opnd(True, a.reg, a.swz, False, True), 3)
            self.vop('mul', t, 0x8, [Opnd(True, t, (3, 3, 3, 3)), b.with_swz((b.swz[3],) * 4)])
            self.sop('exp', d, m, Opnd(True, t, (3, 3, 3, 3)), 3, sat)
            self.release_scratch(); return
        if n == 'nrm':
            d, m = self.dst(i.dst)
            s = self.src(i.src[0])
            if not s.temp: s = self.to_temp(s)
            t = self.scratch()
            self.vop('dp3', t, 0x8, [s, s])
            self.sop('rsq', t, 0x8, Opnd(True, t, (3, 3, 3, 3), abs_=True), 3)
            self.vop('mul', d, m, [s, Opnd(True, t, (3, 3, 3, 3))], sat)
            self.release_scratch(); return
        if n == 'lrp':
            d, m = self.dst(i.dst)
            s0, s1, s2 = [self.src(x) for x in i.src]
            t = self.scratch()
            self.vop('add', t, 0xF, [s1, s2.negated()])
            self.vop('mad', d, m, [s0, Opnd(True, t), s2], sat)
            self.release_scratch(); return
        if n == 'sincos':
            d, m = self.dst(i.dst)
            s = self.src(i.src[0])
            if m & 1: self.sop('cos', d, 0x1, s, 3, sat)
            if m & 2: self.sop('sin', d, 0x2, s, 3, sat)
            self.release_scratch(); return
        if n == 'texld':
            if i.control not in (0, 1, 2): raise CompileError('texld control %d' % i.control)
            d, m = self.dst(i.dst)
            c = self.src(i.src[0])
            s = i.src[1]
            kind = self.samplers.get(s.num, '2d')
            if i.control == 1:                      # texldp: divide by w
                t = self.scratch()
                if not c.temp: c = self.to_temp(c)
                self.sop('rcp', t, 0x8, c, 3)
                self.vop('mul', t, 0x7, [c, Opnd(True, t, (3, 3, 3, 3))])
                c = Opnd(True, t)
            lod = {}
            if i.control == 2:                      # texldb: bias in w
                if not c.temp: c = self.to_temp(c)
                self.tfetch(0, 0, (0, 0, 0, 0), c.with_swz((c.swz[3],) * 4), s.num, 1, opcode=24, use_comp_lod=0)
                lod = dict(use_reg_lod=1)
            if kind == 'cube':
                c = self.cube_coords(c)
                dim = 3
            elif kind == 'volume': dim = 2
            else: dim = 1
            if self.cur_taps and dim == 1:
                ox, oy = self.TAP_OFFSETS[min(self.cur_taps) % 4]
                lod.update(offset_x=ox & 0x1F, offset_y=oy & 0x1F)
            self.tfetch(d, m, tuple(s.swz), c, s.num, dim, **lod)
            if sat: self.vop('max', d, m, [Opnd(True, d), Opnd(True, d)], True)
            self.release_scratch(); return
        if n == 'texldl':
            d, m = self.dst(i.dst)
            c = self.src(i.src[0]); s = i.src[1]
            if not c.temp: c = self.to_temp(c)
            kind = self.samplers.get(s.num, '2d')
            self.tfetch(0, 0, (0, 0, 0, 0), c.with_swz((c.swz[3],) * 4), s.num, 1, opcode=24, use_comp_lod=0)
            if kind == 'cube':
                c = self.cube_coords(c); dim = 3
            else: dim = 2 if kind == 'volume' else 1
            self.tfetch(d, m, tuple(s.swz), c, s.num, dim, use_comp_lod=0, use_reg_lod=1)
            self.release_scratch(); return
        if n == 'texkill':
            s = self.src(i.src[0])
            zero = self.literal(0.0)
            # D3D9: discard when a component (x, y, z, w within the register's mask) < 0
            mask = i.src[0].mask if i.src[0].mask is not None else 0xF
            self.vop('kill_gt', 0, 0, [zero, s])
            self.release_scratch(); return
        if n == 'mova':
            if self.is_ps: raise CompileError('mova in a pixel shader')
            s0 = self.src(i.src[0])
            if self.a0 is None: self.a0 = self.first_scratch; self.first_scratch += 1; self.scratch_next = max(self.scratch_next, self.first_scratch)
            m = i.dst.mask
            # D3D9 mova rounds to the nearest integer: floor(x + 0.5)
            half = self.literal(0.5)
            self.vop('add', self.a0, m, [s0, half])
            self.vop('floor', self.a0, m, [Opnd(True, self.a0)])
            self.max_reg = max(self.max_reg, self.a0)
            if self.joint:
                # bone palette texel coordinates of a0 (texel index = a0, 3 texels per bone):
                # u = frac(a0 * JointMatParam.x), v = floor(a0 * JointMatParam.x) * .y + .z
                if self.bones is None:
                    self.bones = (self.first_scratch, self.first_scratch + 1)
                    self.first_scratch += 2; self.scratch_next = max(self.scratch_next, self.first_scratch)
                    self.max_reg = max(self.max_reg, self.first_scratch - 1)
                u, v = self.bones
                jmp = self.extra_consts[b'JointMatParam']
                self.vop('mul', u, m, [Opnd(True, self.a0), Opnd(False, jmp, (0, 0, 0, 0))])
                self.vop('floor', v, m, [Opnd(True, u)])
                self.vop('add', u, m, [Opnd(True, u), Opnd(True, v, neg=True)])
                self.vop('mad', v, m, [Opnd(True, v), Opnd(False, jmp, (1, 1, 1, 1)), Opnd(False, jmp, (2, 2, 2, 2))])
            self.release_scratch(); return
        if n in ('m4x4', 'm4x3', 'm3x4', 'm3x3', 'm3x2'):
            d, m = self.dst(i.dst)
            a = self.src(i.src[0])
            rows = {'m4x4': 4, 'm4x3': 3, 'm3x4': 4, 'm3x3': 3, 'm3x2': 2}[n]
            op = 'dp4' if n.startswith('m4') else 'dp3'
            if a.temp and a.reg == d:
                a = self.to_temp(a)
            for k in range(rows):
                rr = i.src[1]
                b = d3d9.Reg.__new__(d3d9.Reg)
                for f in d3d9.Reg.__slots__: setattr(b, f, getattr(rr, f))
                b.num = rr.num + k
                self.vop(op, d, 1 << k, [a, self.src(b)], sat)
            self.release_scratch(); return
        if n == 'crs':
            d, m = self.dst(i.dst)
            a, b = self.src(i.src[0]), self.src(i.src[1])
            t = self.scratch()
            # a.yzx * b.zxy - a.zxy * b.yzx
            sw = lambda o, p: o.with_swz(tuple(o.swz[c] for c in p) + (o.swz[3],))
            self.vop('mul', t, 0x7, [sw(a, (2, 0, 1)), sw(b, (1, 2, 0))])
            self.vop('mad', d, m & 0x7, [sw(a, (1, 2, 0)), sw(b, (2, 0, 1)), Opnd(True, t).negated()], sat)
            self.release_scratch(); return
        if n == 'sgn':
            d, m = self.dst(i.dst)
            a = self.src(i.src[0])
            one, zero = self.literal(1.0), self.literal(0.0)
            t = self.scratch()
            self.vop('sgt', t, m, [a, zero])                    # (a > 0)
            u = self.scratch()
            self.vop('sgt', u, m, [zero, a])                    # (a < 0)
            self.vop('add', d, m, [Opnd(True, t), Opnd(True, u).negated()], sat)
            self.release_scratch(); return
        if n in ('dsx', 'dsy'):
            d, m = self.dst(i.dst)
            a = self.src(i.src[0])
            if not a.temp or a.neg or a.abs: a = self.to_temp(a)
            g = self.scratch()
            comps = [c for c in range(4) if m >> c & 1]
            for k in range(0, len(comps), 2):
                pair = comps[k:k + 2]
                c0 = a.swz[pair[0]]; c1 = a.swz[pair[1]] if len(pair) > 1 else c0
                # getGradients: x = ddx(src.x), y = ddy(src.x), z = ddx(src.y), w = ddy(src.y)
                self.tfetch(g, 0xF, (0, 1, 2, 3), Opnd(True, a.reg, (c0, c1, c0, c0)), 0, 1, opcode=18,
                            use_comp_lod=0)
                base = 0 if n == 'dsx' else 1
                for j, c in enumerate(pair):
                    src_c = base + 2 * j
                    self.vop('max', d, 1 << c, [Opnd(True, g, (src_c,) * 4), Opnd(True, g, (src_c,) * 4)], sat)
            self.release_scratch(); return
        if n == 'texldd':
            d, m = self.dst(i.dst)
            c = self.src(i.src[0]); s = i.src[1]
            gx = self.src(i.src[2]); gy = self.src(i.src[3])
            if not gx.temp or gx.neg or gx.abs: gx = self.to_temp(gx)
            if not gy.temp or gy.neg or gy.abs: gy = self.to_temp(gy)
            kind = self.samplers.get(s.num, '2d')
            self.tfetch(0, 0, (0, 0, 0, 0), gx, s.num, 1, opcode=25, use_comp_lod=0)
            self.tfetch(0, 0, (0, 0, 0, 0), gy, s.num, 1, opcode=26, use_comp_lod=0)
            if kind == 'cube':
                c = self.cube_coords(c); dim = 3
            else: dim = 2 if kind == 'volume' else 1
            self.tfetch(d, m, tuple(s.swz), c, s.num, dim, use_comp_lod=0, use_reg_gradients=1)
            self.release_scratch(); return
        if n == 'setp':
            # p0 is kept as 1 / 0 per component in a register (Xenos has one predicate bit)
            if self.preg is None:
                self.preg = self.first_scratch; self.first_scratch += 1
                self.scratch_next = max(self.scratch_next, self.first_scratch); self.max_reg = max(self.max_reg, self.preg)
            a, b = self.src(i.src[0]), self.src(i.src[1])
            cmpk = d3d9.COMPARE.get(i.control)
            op, x, y = {'gt': ('sgt', a, b), 'ge': ('sge', a, b), 'eq': ('seq', a, b), 'ne': ('sne', a, b),
                        'lt': ('sgt', b, a), 'le': ('sge', b, a)}[cmpk]
            self.vop(op, self.preg, i.dst.mask, [x, y])
            self.release_scratch(); return
        if n == 'ifc':
            for side in (0, 1):
                r = i.src[side]
                if r.type == d3d9.CONST and r.rel is None and r.num in self.pc_only:
                    # a PC float light switch (lightbitN.c > 0) -> the 360 bool light<4+c>bitN, as the
                    # 360 versions of these shaders branch (same nesting as the PC compares)
                    nm = self.pc_only[r.num]
                    m = re.match(rb'(.*)bit(\d)$', nm)
                    bname = m.group(1) + str(4 + r.swz[0]).encode() + b'bit' + m.group(2) if m else None
                    breg = self.extra_consts.get(bname)
                    cmpk = d3d9.COMPARE.get(i.control)
                    if breg is None or (cmpk, side) not in (('lt', 1), ('gt', 0)):
                        raise CompileError(f'comparison with {nm.decode()} ({cmpk})')
                    node = ['if', (128 if self.is_ps else 0) + breg, [], None]
                    self.push(node, node[2]); return
            a, b = self.src(i.src[0]), self.src(i.src[1])
            cmpk = d3d9.COMPARE.get(i.control)
            t = self.scratch()
            # predicate = (a cmp b), from the difference of the (replicated) components
            if cmpk in ('lt', 'le'): x, y = b, a
            else: x, y = a, b
            self.vop('add', t, 0x8, [x.with_swz((x.swz[0],) * 4), y.with_swz((y.swz[0],) * 4).negated()])
            sop = {'gt': 'setp_gt', 'lt': 'setp_gt', 'ge': 'setp_ge', 'le': 'setp_ge', 'eq': 'setp_eq', 'ne': 'setp_ne'}[cmpk]
            self.sop(sop, 0, 0, Opnd(True, t, (3, 3, 3, 3)), 3)
            node = ['ifp', 0, [], None]
            self.release_scratch()
            self.push(node, node[2]); return
        if n == 'if' and i.src[0].type == d3d9.PREDICATE:
            if self.preg is None: raise CompileError('if on p0 before setp')
            p = i.src[0]
            comp = p.swz[0]
            self.sop('setp_ne', 0, 0, Opnd(True, self.preg, (comp,) * 4), 3)
            # 'if !p0' (srcmod not) runs the body when the predicate is false
            node = ['ifp', 1 if p.srcmod == 13 else 0, [], None]
            self.push(node, node[2]); return
        if n == 'if':
            b = i.src[0]
            if b.type != d3d9.CONSTBOOL: raise CompileError('if on register type %d' % b.type)
            node = ['if', (128 if self.is_ps else 0) + b.num, [], None]
            self.push(node, node[2]); return
        if n == 'else':
            node = self.stack[-1][0]
            node[3] = []
            self.code = []; node[3].append(('code', self.code))
            self.cur_body = node[3]; return
        if n == 'endif':
            self.pop(); return
        if n == 'rep':
            ireg = i.src[0]
            if ireg.type != d3d9.CONSTINT: raise CompileError('rep on register type %d' % ireg.type)
            if ireg.num not in self.defi: raise CompileError('rep i%d without a defi' % ireg.num)
            # one loop constant per loop, from the top down (i31, i30.. in pixel shaders, i15, i14..
            # in vertex shaders), as the 360 compiler allocates them; set from the definition table
            lid = (31 if self.is_ps else 15) - len(self.loops)
            if lid < (16 if self.is_ps else 0): raise CompileError('too many loops')
            self.loops.append((lid, ireg.num))
            node = ['rep', lid, []]
            self.push(node, node[2]); return
        if n == 'endrep':
            self.pop(); return
        if n == 'ret': return
        raise CompileError('instruction ' + n)

    def predicated(self, i):
        """(p0.c) op dst, ...: the result to a scratch register, then dst = p0.c ? result : dst"""
        if self.preg is None: raise CompileError('predicated instruction before setp')
        if i.dst is None or i.dst.type not in (d3d9.TEMP, d3d9.COLOROUT, d3d9.OUTPUT):
            raise CompileError('predicated ' + i.name)
        d, m = self.dst(i.dst)
        p = i.pred; comp = p.swz[0]
        t = self.first_scratch + 30
        self.max_reg = max(self.max_reg, t)
        saved = (i.dst.type, i.dst.num)
        tmp_reg = d3d9.Reg.__new__(d3d9.Reg)
        for f in d3d9.Reg.__slots__: setattr(tmp_reg, f, getattr(i.dst, f))
        tmp_reg.type = d3d9.TEMP; tmp_reg.num = -1
        self.tmap[-1] = t
        i2 = d3d9.Instr()
        for f in d3d9.Instr.__slots__: setattr(i2, f, getattr(i, f))
        i2.pred = None; i2.dst = tmp_reg
        self.translate1(i2)
        pv = Opnd(True, self.preg, (comp,) * 4)
        if p.srcmod == 13:   # !p0: dst = p ? dst : result
            self.vop('cndeq', d, m, [pv, Opnd(True, t), Opnd(True, d)])
        else:
            self.vop('cndeq', d, m, [pv, Opnd(True, d), Opnd(True, t)])

    def to_temp(self, o):
        t = self.scratch()
        self.vop('max', t, 0xF, [Opnd(o.temp, o.reg, (0, 1, 2, 3), False, o.abs), Opnd(o.temp, o.reg, (0, 1, 2, 3), False, o.abs)])
        return Opnd(True, t, o.swz, o.neg, False)

    def cube_coords(self, c):
        """D3D9 cube coordinates (direction xyz) -> Xenos (sc, tc, face) as the 360 compiler does:
        cube t, v.zzxy, v.yxzz; u.z = t.w; u.w = 1 / |t.z|; u.xy = t.yx * u.w + 1.5"""
        if not c.temp or c.neg or c.abs: c = self.to_temp(c)
        sw = c.swz
        t = self.scratch(); u = self.scratch()
        self.vop('cube', t, 0xF, [c.with_swz((sw[2], sw[2], sw[0], sw[1])), c.with_swz((sw[1], sw[0], sw[2], sw[2]))])
        self.vop('max', u, 0x4, [Opnd(True, t, (3, 3, 3, 3)), Opnd(True, t, (3, 3, 3, 3))])
        self.sop('rcp', u, 0x8, Opnd(True, t, (2, 2, 2, 2), abs_=True), 3)
        self.vop('mad', u, 0x3, [Opnd(True, t, (1, 0, 0, 0)), Opnd(True, u, (3, 3, 3, 3)), self.literal(1.5)])
        return Opnd(True, u, (0, 1, 2, 2))

    # ---- structure ----
    def push(self, node, body):
        self.stack.append((node, self.cur_body))
        self.cur_body.append(node)
        self.cur_body = body
        self.code = []; body.append(('code', self.code))

    def pop(self):
        node, parent = self.stack.pop()
        self.cur_body = parent
        self.code = []; parent.append(('code', self.code))

    # ---- whole program ----
    def compile(self):
        self.extra_literals = []
        self.taps = {}
        self.cur_taps = set()
        self.preg = None
        self.setup()
        self.cur_body = self.tree
        if not self.is_ps:
            self.fetch_inputs()
        for i in self.ins:
            self.translate(i)
        if self.stack: raise CompileError('unterminated control flow')
        self.exports()
        self.finish_literals()
        return self.layout()

    def exports(self):
        """the outputs at the end: one export of every written output register (PS: colors and
        depth; VS: the position, then the interpolators)"""
        if not self.is_ps:
            pos = []
            self.code = pos
            self.vop('max', 62, 0xF, [Opnd(True, self.outs[('o', self.pos_out)]), Opnd(True, self.outs[('o', self.pos_out)])], export=True)
            its = []
            self.code = its
            for k, o in enumerate(self.interp_out):
                r = self.outs[('o', o)]
                self.vop('max', k, 0xF, [Opnd(True, r), Opnd(True, r)], export=True)
                its[-1]['export_interp'] = k
            self.cur_body.append(('exports', [(1, 0, pos)] + ([(2, 0, its)] if its else [])))
            return
        code = []
        self.code = code
        if 'alphaOffset' in {n.decode() if isinstance(n, bytes) else n for n in self.extra_consts} and ('c', 0) in self.outs:
            reg = self.extra_consts.get(b'alphaOffset', self.extra_consts.get('alphaOffset'))
            r = self.outs[('c', 0)]
            self.vop('add', r, 0x8, [Opnd(True, r), Opnd(False, reg, (0, 0, 0, 0))])
        for (k, n), r in sorted(self.outs.items()):
            if k == 'c':
                self.vop('max', n, 0xF, [Opnd(True, r), Opnd(True, r)], export=True)
            else:
                self.vop('max', 61, 0x1, [Opnd(True, r), Opnd(True, r)], export=True)
        if not self.outs:
            raise CompileError('no outputs')
        ncol = sum(1 for k in self.outs if k[0] == 'c')
        self.cur_body.append(('exports', [(2, max(0, ncol - 1), code)]))

    def fetch_inputs(self):
        """vertex shader: one vfetch per input (fetch constant, format, offset and stride are
        patched in by the 360 runtime from the vertex declaration, at the slots the container lists)"""
        for v, u, x, m in self.vin:
            dsw = 0
            for c in range(4):
                dsw |= (c if (m >> c) & 1 else 7) << (3 * c)
            d = dict(opcode=0, src_reg=0, src_reg_am=0, dst_reg=self.vmap[v], dst_reg_am=0, must_be_one=1,
                     const_index=31, const_index_sel=2, prefetch_count=0, src_swiz=0, dst_swiz=dsw,
                     format_comp_all=0, num_format_all=0, signed_rf_mode_all=0, is_index_rounded=0, format=0,
                     reserved2=0, exp_adjust=0, is_mini_fetch=0, is_predicated=0, stride=0, offset=0,
                     pred_condition=0, kind='fetch', velement=(u, x))
            self.code.append(d)

    def finish_literals(self):
        """literals made by the compiler (1.5, 0, ...) go after the PC ones, in the same block"""
        if not self.extra_literals: return
        vals = self.extra_literals
        add = [vals[k:k + 4] + [0.0] * (4 - len(vals[k:k + 4])) for k in range(0, len(vals), 4)]
        n = len(self.defs) + len(add)
        groups = (n + 3) // 4
        taken = {c['reg'] + k for c in self.consts if c['set'] == 2 for k in range(c['count'])}
        base = 256 - 4 * groups
        while base > 0 and set(range(base, base + 4 * groups)) & taken: base -= 4
        # move the PC literals to the new base
        shift = self.lit_base - base
        for c in list(self.cmap):
            if c in self.defs: self.cmap[c] -= shift
        old_lits = [self.defs[c] for c in sorted(self.defs)]
        self.literals = old_lits + add
        self.literals += [[0.0] * 4] * (4 * groups - len(self.literals))
        extra_base = base + len(old_lits)
        def fix(d):
            if d.get('kind') != 'alu': return
            for s in (1, 2, 3):
                r = d.get(f'src{s}_reg')
                if d.get(f'src{s}_sel') == 0:
                    if isinstance(r, tuple):
                        k = r[1]
                        d[f'src{s}_reg'] = extra_base + k // 4
                        d[f'src{s}_swiz'] = _rel_swz(((k % 4),) * 4) if s != 3 or d['scalar_write_mask'] == 0 else d[f'src{s}_swiz']
                    elif self.lit_base <= r < 256: d[f'src{s}_reg'] = r - shift
        for d in self.all_instrs(): fix(d)
        used = {c['reg'] + k for c in self.consts if c['set'] == 2 for k in range(c['count'])}
        if any(base + k in used for k in range(4 * groups)):
            raise CompileError('literal registers collide with named constants')
        self.lit_base = base; self.lit_groups = groups

    def all_instrs(self):
        def walk(body):
            for node in body:
                if node[0] == 'code':
                    yield from node[1]
                elif node[0] == 'exports':
                    for _, _, ins in node[1]: yield from ins
                elif node[0] in ('if', 'ifp'):
                    yield from walk(node[2])
                    if node[3]: yield from walk(node[3])
                elif node[0] == 'rep':
                    yield from walk(node[2])
        yield from walk(self.tree)

    # ---- layout: control flow + microcode ----
    def layout(self):
        cfs = []          # dicts; exec 'instrs' filled in
        def emit_code(instrs):
            for k in range(0, len(instrs), 6):
                cfs.append(dict(opcode=1, instrs=instrs[k:k + 6]))
        def walk(body):
            for node in body:
                if node[0] == 'code':
                    emit_code(node[1])
                elif node[0] == 'exports':
                    for atype, size, ins in node[1]:
                        cfs.append(dict(opcode=12, size=size, alloc_type=atype, unserialized=0))
                        emit_code(ins)
                elif node[0] in ('if', 'ifp'):
                    if node[0] == 'if':
                        j1 = dict(opcode=11, unconditional=0, predicated=0, bool_address=node[1], condition=0, direction=0)
                    else:   # jump to the else part when the predicate is not the wanted value
                        j1 = dict(opcode=11, unconditional=0, predicated=1, bool_address=0, condition=node[1], direction=0)
                    cfs.append(j1)
                    walk(node[2])
                    if node[3]:
                        j2 = dict(opcode=11, unconditional=1, predicated=0, bool_address=0, condition=1, direction=0)
                        cfs.append(j2)
                        j1['target'] = len(cfs)
                        walk(node[3])
                        j2['target'] = len(cfs)
                    else:
                        j1['target'] = len(cfs)
                elif node[0] == 'rep':
                    ls = dict(opcode=7, loop_id=node[1], repeat=0)
                    cfs.append(ls)
                    start = len(cfs)
                    walk(node[2])
                    le = dict(opcode=8, loop_id=node[1], predicated_break=0, condition=0, address=start)
                    cfs.append(le)
                    ls['address'] = len(cfs)
        walk(self.tree)
        # drop empty execs, keep jump targets right: rebuild with index map
        keep = [c for c in cfs if not (c['opcode'] == 1 and not c['instrs'])]
        idx = {}
        n = 0
        for i, c in enumerate(cfs):
            idx[i] = n
            if not (c['opcode'] == 1 and not c['instrs']): n += 1
        idx[len(cfs)] = n
        for c in keep:
            if 'target' in c: c['address'] = idx[c.pop('target')]
            elif c['opcode'] in (7, 8): c['address'] = idx[c['address']]
        cfs = keep
        if not cfs or cfs[-1]['opcode'] != 1:
            raise CompileError('the program does not end with an exec')
        cfs[-1]['opcode'] = 2                     # exece
        # jumps to the end (past the last CF): make them point at the exece
        for c in cfs:
            if c.get('address') is not None and c['opcode'] in (7, 8, 11) and c['address'] >= len(cfs):
                c['address'] = len(cfs) - 1
        ncf = len(cfs)
        slot = (ncf + 1) // 2
        words = []
        instr_words = []
        self.slot_of = {}
        for c in cfs:
            if c['opcode'] in (1, 2):
                ins = c.pop('instrs')
                c.update(address=slot, count=len(ins), yield_=0, predicate_clean=1, address_mode=0)
                seq = 0
                for k, d in enumerate(ins):
                    is_fetch = d['kind'] == 'fetch'
                    if 'velement' in d: self.slot_of[('v', d['velement'])] = slot + k
                    if 'export_interp' in d: self.slot_of[('e', d['export_interp'])] = slot + k
                    seq |= (1 if is_fetch else 0) << (2 * k)
                    seq |= 1 << (2 * k + 1)          # serialize every instruction (correct, not fast)
                    instr_words += (xenos.fetch_encode(d) if is_fetch else xenos.alu_encode(d))
                c['sequence'] = seq
                slot += len(ins)
        cf_words = []
        encoded = [xenos.cf_encode(c) for c in cfs]
        if len(encoded) % 2: encoded.append((0, 0))
        for k in range(0, len(encoded), 2):
            cf_words += xenos.cf_pack_pair(encoded[k], encoded[k + 1])
        ucode = struct.pack('>%dI' % (len(cf_words) + len(instr_words)), *(cf_words + instr_words))
        self.cfs = cfs
        return ucode

def compile_ps(prog, interp=None, interp_hash=0, engine=None, masks=None, folder=None):
    """-> 360 compiled pixel shader (container)"""
    c = Compiler(prog, interp, engine, folder)
    ucode = c.compile()
    return container(c, ucode, interp_hash)

def compile_vs(prog, interp_hash=None, engine=None):
    """-> (360 compiled vertex shader (container), its interpolator list)"""
    c = Compiler(prog, None, engine)
    ucode = c.compile()
    if interp_hash is None:
        interp_hash = (engine or {}).get('interp', {}).get(tuple(c.interp), interp_list_hash(c.interp))
    return container(c, ucode, interp_hash), c.interp

def interp_list_hash(lst):
    """a linkage hash for an interpolator list the 360 data does not have (only compared between
    this vertex shader and its pixel shaders, which are made with the same list)"""
    import zlib
    return (zlib.crc32(repr(lst).encode()) & 0x7FFF) | 0x8000

def container(c, ucode, interp_hash):
    """the 360 compiled-shader container of a compiled shader"""
    e = '>'
    ct = dict(version=c.version, target=c.ct['target'], consts=c.consts)
    ctab = write_ctab(ct)
    ct_block = struct.pack(e + 'I', len(ctab)) + ctab
    # literal (definition) table: the float block at the top registers (PS registers are 256 + n)
    nfl = 4 * 4 * c.lit_groups
    recs = struct.pack(e + 'HHI', (256 if c.is_ps else 0) + c.lit_base, nfl, 0) if nfl else b''
    group_mask = (1 << c.lit_groups) - 1
    # loop constants (count | start << 8 | step << 16): records {u16 register address 0x2320 + 4 *
    # loop constant, u16 count, values}, one per run of consecutive constants; the header has a
    # mask with bit 31 - n for loop constant n (as in the 360 data: pixel 'rep' i31 -> 239c0001,
    # vertex i14 + i15 -> 23580002, mask 0x30000)
    ints = b''
    n_int = 0
    vals = {lid: c.defi[i] for lid, i in c.loops}
    ids = sorted(vals)
    k = 0
    while k < len(ids):
        j = k
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1: j += 1
        ints += struct.pack(e + 'HH', 0x2320 + 4 * ids[k], j - k + 1)
        for lid in ids[k:j + 1]:
            v = vals[lid]
            # (pixel shaders: step at least 1, vertex shaders: as defined - both as in the 360 data:
            # PS (2, 0, 0) -> 00010002, VS (6, 0, 0) -> 00000006)
            step = max(1, v[2]) if c.is_ps else v[2]
            ints += struct.pack(e + 'I', (v[0] & 0xFF) | ((v[1] & 0xFF) << 8) | ((step & 0xFF) << 16))
        k = j + 1
    for lid in ids: n_int |= 1 << (31 - lid)
    body = recs + bytes(4) + ints + bytes(8 if n_int else 4)
    if not n_int: body = recs + bytes(12)
    dt = struct.pack(e + '5I', 0, group_mask, n_int, 0, len(body)) + body
    # shader info
    literals = b''.join(struct.pack(e + '4f', *v) for v in c.literals)
    nreg = c.max_reg
    if not c.is_ps:
        return container_vs(c, ucode, interp_hash, ct_block, dt, literals)
    cntl = 0x10000000 | (nreg << 8) | (0x40000 if c.param_gen is not None else 0)
    misc = 4 if c.param_gen is None else (c.param_gen << 8) | 6
    flags = (1 if c.loops else 0) | (0x20000 if any(x['set'] == 0 for x in c.consts) else 0)
    imask = (1 << len(c.interp)) - 1
    # (the upper half: the interpolators the program reads; the 360 compiler sets nearly all of them,
    # and a superset is harmless)
    info = struct.pack(e + '8I', len(literals), len(ucode), cntl, misc, flags, interp_hash, imask | (imask << 16), 1)
    info += b''.join(struct.pack(e + 'I', (m << 12) | (r << 8) | (u << 4) | x) for r, (u, x, m) in enumerate(c.interp))
    hdr_size = 0x24
    ct_off = hdr_size
    dt_off = ct_off + len(ct_block)
    sh_off = dt_off + len(dt)
    virt = sh_off + len(info)
    phys = literals + ucode
    hdr = struct.pack(e + '9I', 0x102A1100, virt, len(phys), 0, ct_off, dt_off, sh_off, 0, 0)
    return hdr + ct_block + dt + info + phys

def container_vs(c, ucode, interp_hash, ct_block, dt, literals):
    """vertex shader info: SQ_PROGRAM_CNTL-like word (registers, export count), flags, linkage
    hash, the vertex elements {first/last marks, usage index, usage, vfetch slot}, the interpolators
    {export, mask, register, usage, index} and the slot of each interpolator export (the runtime
    patches those to link with the pixel shader)"""
    e = '>'
    n = len(c.interp)
    cntl = ((max(n, 1) - 1) << 20) | 0x10000 | c.max_reg
    flags = (0x80000 if any(x['set'] == 0 for x in c.consts) else 0) | (0x100 if c.loops else 0)
    els = []
    for k, (v, u, x, m) in enumerate(c.vin):
        mark = (1 if k == 0 else 0) | (2 if k == len(c.vin) - 1 else 0)
        els.append((mark << 20) | (x << 16) | (u << 12) | c.slot_of[('v', (u, x))])
    info = struct.pack(e + '10I', len(literals), len(ucode), cntl, 0, flags, interp_hash, 1, len(els), n, 0x290)
    info += b''.join(struct.pack(e + 'I', v) for v in els)
    info += b''.join(struct.pack(e + 'I', (k << 16) | (m << 12) | (k << 8) | (u << 4) | x) for k, (u, x, m) in enumerate(c.interp))
    info += b''.join(struct.pack(e + 'I', 0x1000 | c.slot_of[('e', k)]) for k in range(n))
    hdr_size = 0x24
    ct_off = hdr_size
    dt_off = ct_off + len(ct_block)
    sh_off = dt_off + len(dt)
    virt = sh_off + len(info)
    phys = literals + ucode
    hdr = struct.pack(e + '9I', 0x102A1101, virt, len(phys), 0, ct_off, dt_off, sh_off, 0, 0)
    return hdr + ct_block + dt + info + phys
