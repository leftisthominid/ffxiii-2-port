# Direct3D 9 shader bytecode (vs_3_0 / ps_3_0, as in the Steam version's materials): decoding.
# Token layout from the D3D9 driver documentation (d3d9types.h): version token, then instruction
# tokens {opcode[0:16], control[16:24], length[24:28], predicated[28], coissue[30]}, comments
# (0xFFFE, length in [16:31]), end token 0x0000FFFF.
import struct

OPS = {0: 'nop', 1: 'mov', 2: 'add', 3: 'sub', 4: 'mad', 5: 'mul', 6: 'rcp', 7: 'rsq', 8: 'dp3', 9: 'dp4',
       10: 'min', 11: 'max', 12: 'slt', 13: 'sge', 14: 'exp', 15: 'log', 16: 'lit', 17: 'dst', 18: 'lrp',
       19: 'frc', 20: 'm4x4', 21: 'm4x3', 22: 'm3x4', 23: 'm3x3', 24: 'm3x2', 25: 'call', 26: 'callnz',
       27: 'loop', 28: 'ret', 29: 'endloop', 30: 'label', 31: 'dcl', 32: 'pow', 33: 'crs', 34: 'sgn',
       35: 'abs', 36: 'nrm', 37: 'sincos', 38: 'rep', 39: 'endrep', 40: 'if', 41: 'ifc', 42: 'else',
       43: 'endif', 44: 'break', 45: 'breakc', 46: 'mova', 47: 'defb', 48: 'defi', 65: 'texkill',
       66: 'texld', 78: 'expp', 79: 'logp', 80: 'cnd', 81: 'def', 88: 'cmp', 89: 'bem', 90: 'dp2add',
       91: 'dsx', 92: 'dsy', 93: 'texldd', 94: 'setp', 95: 'texldl', 96: 'breakp'}

# register types
TEMP, INPUT, CONST, ADDR, RASTOUT, ATTROUT, OUTPUT, CONSTINT, COLOROUT, DEPTHOUT, SAMPLER, \
    CONST2, CONST3, CONST4, CONSTBOOL, LOOP, TEMPFLOAT16, MISCTYPE, LABEL, PREDICATE = range(20)
REG_NAMES = {TEMP: 'r', INPUT: 'v', CONST: 'c', ADDR: 'a', RASTOUT: 'oRast', ATTROUT: 'oD', OUTPUT: 'o',
             CONSTINT: 'i', COLOROUT: 'oC', DEPTHOUT: 'oDepth', SAMPLER: 's', CONSTBOOL: 'b', LOOP: 'aL',
             MISCTYPE: 'vMisc', LABEL: 'l', PREDICATE: 'p'}
COMPARE = {1: 'gt', 2: 'eq', 3: 'ge', 4: 'lt', 5: 'ne', 6: 'le'}
SRC_MOD = {0: '', 1: 'neg', 2: 'bias', 3: 'biasneg', 4: 'sign', 5: 'signneg', 6: 'comp', 7: 'x2',
           8: 'x2neg', 9: 'dz', 10: 'dw', 11: 'abs', 12: 'absneg', 13: 'not'}
USAGES = {0: 'position', 1: 'blendweight', 2: 'blendindices', 3: 'normal', 4: 'psize', 5: 'texcoord',
          6: 'tangent', 7: 'binormal', 8: 'tessfactor', 9: 'positiont', 10: 'color', 11: 'fog',
          12: 'depth', 13: 'sample'}
SAMPLER_TYPES = {2: '2d', 3: 'cube', 4: 'volume'}

def reg_type(t):
    return ((t >> 28) & 7) | (((t >> 11) & 3) << 3)

class Reg:
    __slots__ = ('type', 'num', 'mask', 'mods', 'shift', 'swz', 'srcmod', 'rel')
    def __init__(self, t, dst):
        self.type = reg_type(t)
        self.num = t & 0x7FF
        self.rel = None
        if dst:
            self.mask = (t >> 16) & 0xF
            self.mods = (t >> 20) & 0xF          # 1 saturate, 2 partial precision, 4 centroid
            self.shift = (t >> 24) & 0xF
            self.swz = None; self.srcmod = 0
        else:
            sw = (t >> 16) & 0xFF
            self.swz = [(sw >> (2 * i)) & 3 for i in range(4)]
            self.srcmod = (t >> 24) & 0xF
            self.mask = None; self.mods = 0; self.shift = 0
    def name(self):
        n = REG_NAMES.get(self.type, 'reg%d_' % self.type)
        if self.type in (DEPTHOUT, LOOP): return n
        return f'{n}{self.num}'
    def text(self):
        s = self.name()
        if self.rel is not None: s += f'[{self.rel.name()}.{"xyzw"[self.rel.swz[0]]}]'
        if self.mask is not None:
            if self.mask != 0xF: s += '.' + ''.join('xyzw'[i] for i in range(4) if self.mask >> i & 1)
            return s
        if self.swz != [0, 1, 2, 3]: s += '.' + ''.join('xyzw'[i] for i in self.swz)
        m = SRC_MOD[self.srcmod]
        if m == 'neg': return '-' + s
        if m == 'abs': return '|' + s + '|'
        if m == 'absneg': return '-|' + s + '|'
        return f'{m}({s})' if m else s

class Instr:
    __slots__ = ('op', 'name', 'control', 'dst', 'src', 'pred', 'dcl', 'values', 'raw')
    def text(self):
        if self.name == 'dcl':
            return f'dcl_{self.dcl} {self.dst.text()}'
        if self.name in ('def', 'defi', 'defb'):
            return f'{self.name} {self.dst.text()}, ' + ', '.join(str(v) for v in self.values)
        n = self.name
        if n in ('ifc', 'breakc', 'setp'): n += '_' + COMPARE.get(self.control, '?')
        if n == 'texld' and self.control: n = {1: 'texldp', 2: 'texldb'}.get(self.control, n)
        if self.dst is not None:
            if self.dst.mods & 1: n += '_sat'
            if self.dst.mods & 2: n += '_pp'
        ops = ([self.dst.text()] if self.dst is not None else []) + [s.text() for s in self.src]
        pre = f'({"!" if self.pred.srcmod == 13 else ""}p0.{"xyzw"[self.pred.swz[0]]}) ' if self.pred is not None else ''
        return pre + n + (' ' + ', '.join(ops) if ops else '')

# instructions without a destination register
NO_DST = {'nop', 'call', 'callnz', 'loop', 'ret', 'endloop', 'label', 'rep', 'endrep', 'if', 'ifc', 'else',
          'endif', 'break', 'breakc', 'texkill', 'breakp'}

def parse(p):
    """-> (version token, [Instr], [comment bytes])"""
    n = len(p) // 4
    t = struct.unpack('<%dI' % n, p[:4 * n])
    version = t[0]
    out, comments = [], []
    i = 1
    while i < n:
        tok = t[i]
        op = tok & 0xFFFF
        if op == 0xFFFE:
            l = tok >> 16
            comments.append(p[4 * (i + 1):4 * (i + 1 + l)]); i += 1 + l; continue
        if op == 0xFFFF: break
        l = (tok >> 24) & 0xF
        args = list(t[i + 1:i + 1 + l])
        ins = Instr()
        ins.op = op; ins.name = OPS.get(op, 'op%d' % op); ins.control = (tok >> 16) & 0xFF
        ins.dst = None; ins.src = []; ins.pred = None; ins.dcl = None; ins.values = None; ins.raw = args
        if ins.name == 'dcl':
            d = args[0]; ins.dst = Reg(args[1], True)
            if ins.dst.type == SAMPLER:
                ins.dcl = SAMPLER_TYPES.get((d >> 27) & 0xF, '?')
            else:
                ins.dcl = USAGES.get(d & 0x1F, 'usage%d' % (d & 0x1F)) + (str((d >> 16) & 0xF) if (d >> 16) & 0xF else '')
            ins.values = (d & 0x1F, (d >> 16) & 0xF, (d >> 27) & 0xF)
        elif ins.name == 'def':
            ins.dst = Reg(args[0], True); ins.values = list(struct.unpack('<4f', struct.pack('<4I', *args[1:5])))
        elif ins.name == 'defi':
            ins.dst = Reg(args[0], True); ins.values = list(struct.unpack('<4i', struct.pack('<4I', *args[1:5])))
        elif ins.name == 'defb':
            ins.dst = Reg(args[0], True); ins.values = [args[1]]
        else:
            k = 0
            regs = []
            while k < len(args):
                r = args[k]; k += 1
                regs.append([r, None])
                if r & (1 << 13) and k < len(args):           # relative addressing token
                    regs[-1][1] = args[k]; k += 1
            pred = bool(tok & (1 << 28))
            has_dst = ins.name not in NO_DST
            objs = []
            for j, (r, rel) in enumerate(regs):
                is_dst = has_dst and j == 0
                o = Reg(r, is_dst)
                if rel is not None:
                    o.rel = Reg(rel, False)
                objs.append(o)
            if has_dst:
                ins.dst = objs.pop(0)
            if pred:
                ins.pred = objs.pop(0)
            ins.src = objs
        out.append(ins)
        i += 1 + l
    return version, out, comments

def disasm(p):
    v, ins, _ = parse(p)
    kind = 'ps' if v >> 16 == 0xFFFF else 'vs'
    return '\n'.join([f'{kind}_{(v >> 8) & 0xFF}_{v & 0xFF}'] + [i.text() for i in ins])
