# What the 360 engine expects of a shader, learned from the base game's PC / 360 shader pairs
# (the 360 programs were compiled from 360 variants of the same HLSL), per stage (_ps / _vs):
#   fixed   constant name -> the register it has in every 360 shader (explicitly bound there, and
#           possibly set by the engine at that register: the compiler puts it there too)
#   count   constant name -> its 360 register count where the PC one differs (shadowOffset0/1:
#           PC arrays of 4 shadow-tap offsets, one offset on the 360, where the taps are texture
#           fetch offsets)
#   extra   PC constant name set -> constants only the 360 versions have (alphaOffset: added to
#           the output alpha by the 360 programs; bone_palette_texture / JointMatParam: the bone
#           matrices of skinned vertex shaders, which the PC has in constants)
#   extra_pred  360-only constant -> (its table entry, PC names that always come with it)
#   interp  360 interpolator list -> the linkage hash the 360 containers carry
import struct, collections
from . import xcompile

def container_info(x):
    """(constant table, [(usage, index, mask)], hash, is_pixel) of a 360 compiled shader"""
    h = struct.unpack_from('>9I', x, 0)
    ct = xcompile.read_ctab(x[h[4] + 4:], '>')
    so = h[6]
    is_ps = (h[0] & 0xFF) == 0
    w = struct.unpack_from('>8I', x, so)
    if is_ps:
        n = bin(w[6] & 0xFFFF).count('1')
        ents = [struct.unpack_from('>I', x, so + 32 + 4 * k)[0] for k in range(n)]
    else:
        nv, ni = struct.unpack_from('>II', x, so + 28)
        base = so + 40 + 4 * nv
        # (ni counts the export slots, which can have a gap the 360 compiler left; the number of
        # interpolators is in the control word)
        n = min(ni, ((w[2] >> 20) & 0xF) + 1)
        ents = [struct.unpack_from('>I', x, base + 4 * k)[0] for k in range(n)]
    lst = [((v >> 4) & 0xF, v & 0xF, (v >> 12) & 0xF) for v in ents]
    return ct, lst, w[5], is_ps

class Stage:
    def __init__(self):
        self.regs = collections.defaultdict(collections.Counter)
        self.counts = collections.defaultdict(collections.Counter)
        self.pc_counts = collections.defaultdict(collections.Counter)
        self.extra = collections.defaultdict(collections.Counter)
        self.extra_ent = {}
        self.name_sets = []          # (PC names, 360-only names) per shader
        self.n = 0
        # PC-only constants (never in a 360 table: lightbit0/1, float light switches of the vertex-lit
        # shaders) -> the 360 table entries of the shaders that have them
        self.pc_seen = collections.Counter(); self.pc_kept = collections.Counter()
        self.variant = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        self.variant_ent = {}

    def add(self, pct, ct):
        self.n += 1
        pnames = {c['name'] for c in pct['consts']}
        for c in ct['consts']:
            self.regs[(c['set'], c['name'])][c['reg']] += 1
            self.counts[c['name']][c['count']] += 1
            if c['name'] not in pnames:
                self.extra[frozenset(pnames)][c['name']] += 1
                self.extra_ent[c['name']] = c
        xnames = {c['name'] for c in ct['consts']}
        for c in pct['consts']:
            self.pc_counts[c['name']][c['count']] += 1
            self.pc_seen[c['name']] += 1; self.pc_kept[c['name']] += c['name'] in xnames
            if c['name'] not in xnames:
                for x in ct['consts']:
                    k = (x['set'], x['reg'], x['count'])
                    self.variant[c['name']][x['name']][k] += 1; self.variant_ent[(x['name'], k)] = x
        self.extra[frozenset(pnames)][None] += 1
        if len(self.name_sets) < 20000:
            self.name_sets.append((frozenset(pnames), frozenset(c['name'] for c in ct['consts'] if c['name'] not in pnames)))

    def result(self):
        fixed = {}
        for (s, name), regs in self.regs.items():
            total = sum(regs.values())
            reg, n = regs.most_common(1)[0]
            if s == 2 and total >= 3 and n == total:
                fixed[name] = reg
        count = {}
        for name, cs in self.counts.items():
            x = cs.most_common(1)[0][0]
            p = self.pc_counts.get(name)
            if p and p.most_common(1)[0][0] != x and len(cs) == 1:
                count[name] = x
        extra = {}
        for names, c in self.extra.items():
            total = c[None]
            add = [self.extra_ent[n] for n, k in c.items() if n is not None and k == total]
            if add: extra[names] = add
        # 360-only constants predicted by single PC names: a name with which the 360 version has
        # that constant every time (seen at least 3 times)
        predict = collections.defaultdict(list)
        with_name = collections.Counter(); both = collections.Counter()
        for pn, xn in self.name_sets:
            for n in pn:
                with_name[n] += 1
                for e in xn: both[(n, e)] += 1
        for (n, e), k in both.items():
            if k == with_name[n] and k >= 3: predict[e].append(n)
        predicted = {e: (self.extra_ent[e], sorted(ns)) for e, ns in predict.items() if e in self.extra_ent}
        # for each PC-only constant: the 360 entries (name -> table entry) every shader with it has
        # at one place (seen at least 3 times)
        variant = {}
        for n, names in self.variant.items():
            if self.pc_kept[n] or self.pc_seen[n] < 3 or n == b'jointMatrices': continue   # (skinning: its own rule)
            v = {}
            for m, ks in names.items():
                k, c = ks.most_common(1)[0]
                if len(ks) == 1 and c == self.pc_seen[n]: v[m] = self.variant_ent[(m, k)]
            if v: variant[n] = v
        return fixed, count, extra, predicted, variant

class Rules:
    def __init__(self):
        self.ps, self.vs = Stage(), Stage()
        self.interp = {}
        self.by_dir = collections.defaultdict(collections.Counter)   # top folder -> 360-only names
        self.dir_n = collections.Counter()
        self.dir_ent = {}

    def add(self, pc, x, path=None):
        """one PC / 360 program pair (path: the file it is from)"""
        try:
            ct, lst, hsh, is_ps = container_info(x)
        except Exception:
            return
        self.interp.setdefault(tuple(lst), hsh)
        pct = xcompile.pc_ctab(pc)
        if pct is None: return
        (self.ps if is_ps else self.vs).add(pct, ct)
        if path and is_ps:
            d = path.split('/', 1)[0]
            pn = {c['name'] for c in pct['consts']}
            self.dir_n[d] += 1
            for c in ct['consts']:
                if c['name'] not in pn:
                    self.by_dir[d][c['name']] += 1; self.dir_ent[c['name']] = c

    def result(self):
        out = dict(interp=dict(self.interp), pairs=self.ps.n, pairs_vs=self.vs.n)
        for st, sfx in ((self.ps, '_ps'), (self.vs, '_vs')):
            fixed, count, extra, pred, variant = st.result()
            out['fixed' + sfx] = fixed; out['count' + sfx] = count; out['extra' + sfx] = extra
            out['variant' + sfx] = variant
            out['extra_pred' if sfx == '_ps' else 'extra_pred_vs'] = pred
        # the 360 bone palette entries of skinned vertex shaders (added whenever the PC program
        # has jointMatrices, whatever its other constants)
        out['bone_vs'] = [self.vs.extra_ent[n] for n in (b'bone_palette_texture', b'JointMatParam') if n in self.vs.extra_ent]
        # pixel shaders of a folder (chr: characters) that have a 360-only constant every time
        # (alphaOffset in every character shader), seen at least 100 times
        out['folder_ps'] = {d: [self.dir_ent[n] for n, k in c.items() if k == self.dir_n[d]]
                            for d, c in self.by_dir.items() if self.dir_n[d] >= 100}
        out['folder_ps'] = {d: v for d, v in out['folder_ps'].items() if v}
        return out
