# One Steam (PC) file -> its Xbox 360 form, by kind. Uses the data learned from the base game
# (learn.py): the shader library, the exact table of files and bundle members whose PC bytes also
# occur in the base game, and the learned field layouts.
#   exact      textures, meshes, model and material containers (model.py), archives
#   copied     kinds with the same bytes on both (text, scripts, data tables, ...)
#   table      a PC file / bundle member that is in the base game: its 360 bytes from there
#   learned    effect (qix layouts), motion and other headers (learned.Oracle): field sizes learned
#              from base-game pairs; reported per file as 'learned'
#   re-encoded sound: MS-ADPCM -> Ogg Vorbis (the 360 has XMA2, which cannot be made here)
import hashlib, struct, threading, time
from . import model, wpd, texture, qix, learned, sound, vmdl, scb, mcb, ccb
from .util import align

SAME_KINDS = ('.ztr', '.clb', '.wdb', '.dat', '.wpb', '.bik', '.txt')
MIN_LAYOUT_PAIRS = 8
MAX_ORACLE = 1500         # field-pair examples per member kind
GAME_LAYOUT_KINDS = {'lyb'}     # members converted with the word / entry layout learners (checked on lyb)
SAME_MEMBERS = {b'mtb', b'txt', b'phb', b'ykd', b'srb', b'geb'}   # identical in the DLC sample

def kind(path):
    n = path.rsplit('/', 1)[-1]
    if n.endswith('.imgb'): return 'imgb'
    if n.endswith('.trb'): return 'trb'
    if n.endswith('.mpk'): return 'mpk'
    if n.endswith('.scd'): return 'scd'
    if n.endswith('.wpd') and path.startswith('sound/'): return 'soundpack'
    if n.endswith(('.xfv', '.xwb', '.xwp', '.xgr')) or n.endswith('.white.x360.bin') or n.endswith('.white.win32.bin'): return 'bundle'
    if n.endswith(('.vinsbin', '.lyb')): return 'single'
    if n.endswith(SAME_KINDS): return 'same'
    if n.endswith('.bin') and '.x360.' not in n and '.win32.' not in n: return 'same'
    return 'unknown'

def md5(b):
    return hashlib.md5(b).digest()

# Four-letter type tags (resource kinds) are u32 values: reversed on the PC ('ldmv'), in reading
# order on the 360 ('vmdl'); the names next to them are plain text on both.
TAGS = (b'vmdl', b'vtex', b'vfxm', b'veff', b'vlea', b'leaf', b'aefb', b'vins', b'txb\0', b'trb\0', b'shd\0',
        b'wrb\0', b'mpk\0', b'scd\0', b'mcb\0', b'mtb\0')
_PRINT = set(b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-')

def keep_text(pc, out, start=0x30):
    """runs of names (printable text with NULs, at least 8 bytes, with a word of 4 letters or more)
    are the same bytes on both; then the type tags inside them are reversed"""
    n = len(pc)
    i = start
    while i < n:
        if pc[i] in _PRINT:
            j = i
            longest = cur = 0
            while j < n and (pc[j] in _PRINT or pc[j] == 0):
                cur = cur + 1 if pc[j] in _PRINT else 0
                longest = max(longest, cur)
                j += 1
            while j > i and pc[j - 1] == 0: j -= 1       # the NULs after the text: to the next word
            end = min(n, j + (-j) % 4 if j % 4 else j + 1)
            if end - i >= 8 and longest >= 4 and i % 4 == 0:
                out[i:end] = pc[i:end]
            i = max(j, i + 1)
        else:
            i += 1
    for t in TAGS:
        r = t[::-1]
        k = pc.find(r, start)
        while k >= 0:
            if k % 4 == 0: out[k:k + 4] = t
            k = pc.find(r, k + 1)

def guess_all(pc):
    """every u32 by the value heuristic (learned.guess); a last partial word kept"""
    n = len(pc) // 4 * 4
    return b''.join(learned.guess(pc[i:i + 4]) for i in range(0, n, 4)) + pc[n:]

def cut_member_fix(e, pc, y):
    """the cutscene data members (event DataSet bundles), after the learned words:
    mcb/acb: resource names stay as text; binary record payloads do not;
    ccb: camera curves - the embedded motion table (after '@FCMT', its size there) is the same
    bytes on the 360, and the SEDB byte 0xC is 0 (all 22 members of ev_cszb_010)"""
    y = bytearray(y)
    if e in (b'mcb', b'acb'):
        # Printable record bytes can be floats or packed fields, not names.
        for w in scb.text_words(pc) & mcb.resource_words(pc):
            y[w:w + 4] = pc[w:w + 4]
        return mcb.structural(pc, y)
    else:
        y[0xC] = 0
        i = 0
        while True:
            i = pc.find(b'SEDBmtb\0', i + 1)
            if i < 0: break
            if i % 16 or pc[i - 16:i - 10] != b'@FCMT\0': continue
            n = struct.unpack_from('<I', pc, i - 8)[0]
            n = min(n + (-n) % 4, len(pc) - i)       # through the end of its last word
            if 0x30 <= n: y[i:i + n] = pc[i:i + n]
        return bytes(y)

class Converter:
    def __init__(self, data, log=print, ffmpeg=None):
        self.d = data
        self.log = log
        self.ffmpeg = ffmpeg
        self.shaders = data.get('shaders', {})
        self.known = data.get('known', {})            # md5(PC bytes) -> 360 bytes (files and members)
        self.pairs = data.get('pairs', {})            # member ext -> [(pc, 360)] (learned formats)
        self.qix_kb = None
        self._qix_contexts = None
        self._qix_properties = None
        self._scb = None
        self._mcb = None
        self._acb = None
        self._layout_words = {}
        self._header_rules = {}
        self._qix_vins = {}
        self._oracles = {}
        self.stats = {}
        self.lock = threading.Lock()
        # the bone-index rule for skeletons of 256 joints or more, from the base game's models
        votes = {}
        for (big, hit), n in data.get('bone_rules', {}).items():
            for r in hit:
                if big and r != 'none': votes[r] = votes.get(r, 0) + n
        self.bone_rule = max(votes, key=votes.get) if votes else 'global_parent'
        from . import shader as _shader
        self.read_pc = None                         # set by the caller: Steam file reader (norm path)
        self.cur_path = None
        self._vs_logged = set()
        self.ctx = _shader.Context(self.shaders, data.get('engine'), data.get('vs_sigs'), self.vs_of, log)

    def note(self, what, n=1):
        with self.lock:
            self.stats[what] = self.stats.get(what, 0) + n

    def vs_of(self, material):
        """the PC vertex program of a material of the file being converted (an .mpk variant's
        vertex shader is in the model's .trb: w160_def.mpk -> w160.trb)"""
        from . import shader as _shader, gameindex, trb as _trb
        if self.read_pc is None or self.cur_path is None: return None
        d, n = self.cur_path.rsplit('/', 1)
        stem = n.split('.x360.', 1)[0]
        found = []
        for s in (stem, stem.rsplit('_', 1)[0]):
            pc = self.read_pc(gameindex.norm(f'{d}/{s}.x360.trb'))
            if not pc: continue
            try: t = _trb.Trb(pc)
            except Exception: continue
            for r in t.res:
                if r[:7] != b'SEDBshd': continue
                for e in _shader.needed(_shader.chunks(r)):
                    if e['name'].endswith('.vpo'): found.append((_shader.material_of(e['name']), e['name'], e['prog']))
            if found: break
        for m, n, prog in found:
            if m == material: return prog
        for m, n, prog in found:
            if m.lower() == material.lower(): return prog
        if len({prog for _, _, prog in found}) == 1: return found[0][2]     # (one vertex program for the model)
        key = (self.cur_path, material)
        if key not in self._vs_logged:
            self._vs_logged.add(key)
            self.log(f'  no vertex program for {material} in the model of {self.cur_path}: ' +
                     (', '.join(n for _, n, _ in found) or 'model not found or without vertex programs'))
        return None

    # ---- learned formats ----------------------------------------------------------------
    def mcb_learner(self):
        """the motion control block word orders by chunk place, learned once"""
        with self.lock:
            if self._mcb is None:
                self._mcb = mcb.Learner()
                for p, x in self.pairs.get('mcb', []): self._mcb.add(p, x)
                self.log(f'  mcb: chunk places learned from {self._mcb.pairs} base-game motion blocks')
            return self._mcb

    def acb_learner(self):
        with self.lock:
            if self._acb is None:
                self._acb = mcb.Learner()
                for p, x in self.pairs.get('acb', []): self._acb.add(p, x)
            return self._acb

    def oracle(self, ext):
        with self.lock:
            if ext not in self._oracles:
                prs = self.pairs.get(ext, [])
                # (the field pairs compare a member with each example: at most MAX_ORACLE of them,
                # spread over all)
                if len(prs) > MAX_ORACLE: prs = prs[::-(-len(prs) // MAX_ORACLE)]
                self._oracles[ext] = learned.Oracle(prs)
            return self._oracles[ext]

    def scb_learner(self):
        """the cutscene schedule word orders, learned once from the base game's schedules"""
        with self.lock:
            if self._scb is None:
                t0 = time.time()
                self._scb = scb.Learner()
                n = sum(1 for p, x in self.pairs.get('scb', []) if self._scb.add(p, x))
                self.log(f'  cutscene schedules: learned from {n} base-game schedules ({time.time() - t0:.0f} s)')
            return self._scb

    def qix_for(self, ext):
        """the element layouts for a member kind: the leaf instances (vins / .vinsbin) from their
        own base-game pairs (the effect counts do not hold them), the rest from the effect data"""
        if ext in ('vins', 'vinsbin') and (len(self.pairs.get('vins', [])) >= 20 or
                (ext == 'vinsbin' and len(self.pairs.get('vinsbin', [])) >= MIN_LAYOUT_PAIRS)):
            with self.lock:
                if ext not in self._qix_vins:
                    kb = qix.learn_all([(a, b) for a, b in self.pairs.get('vins', []) if len(a) == len(b)])
                    roots = self.pairs.get('vinsbin', []) if ext == 'vinsbin' else []
                    if len(roots) >= MIN_LAYOUT_PAIRS:
                        # Root scene-instance files and embedded effect members
                        # reuse element sizes for different fields. The root
                        # files need their own base-disc layout evidence.
                        own = qix.learn_all([(a, b) for a, b in roots if len(a) == len(b)])
                        for part in kb: kb[part].update(own[part])
                    self._qix_vins[ext] = kb
            return self._qix_vins[ext]
        return self.qix()

    def qix(self):
        if self.qix_kb is None:
            pairs = [p for e in (b'veff', b'vlea', b'vmdl', b'vtex') for p in self.pairs.get(e.decode(), [])]
            # The retained vmdl examples contain only the object, without the
            # platform-specific shader blobs. The old aggregate mixed complete
            # models into the counts, including data with different layouts.
            # Rebuild covered signatures from these base-game examples. Retain
            # aggregate knowledge only for signatures absent from the examples.
            rebuilt = qix.learn_all([(a, b) for a, b in pairs if len(a) == len(b)])
            old = self.d.get('qix_kb') or {}
            self.qix_kb = {part: dict(old.get(part, {})) for part in ('blocks', 'targets')}
            for part in self.qix_kb: self.qix_kb[part].update(rebuilt[part])
            self.log('  effect layouts: rebuilt from retained base-game object examples')
        return self.qix_kb

    def qix_contexts(self):
        if self._qix_contexts is None:
            self._qix_contexts = qix.learn_contexts(self.pairs.get('vlea', []))
        return self._qix_contexts

    def learned_member(self, ext, pc, label):
        """field-by-field byte order, learned"""
        if (ext in ('veff', 'vlea', 'vmdl', 'vtex') and qix.layout(pc) is not None) or qix.exact_fit(pc) is not None:
            # words no layout decides: the value rules measured on the effect data (text and byte
            # fields kept, tags reversed, floats and u16 pairs swapped)
            first = bytearray(qix.guess_effect(pc)); learned.sedb_header(pc, first)
            y, st = qix.convert(pc, bytes(first), self.qix_for(ext))
            if ext == 'vlea':
                y = qix.apply_contexts(pc, y, self.qix_contexts())
            if ext == 'veff':
                with self.lock:
                    if self._qix_properties is None:
                        self._qix_properties = qix.learn_property_targets(self.pairs.get('veff', []))
                y = qix.apply_property_targets(pc, y, self._qix_properties)
            y = qix.keep_text(pc, y, qix.layout(pc))
            y, _ = qix.fix(pc, bytes(y))           # the exact parts last
            y, _ = qix.fix_game(pc, y)
            if ext in ('veff', 'vlea'):
                y, curves = qix.fix_curves(pc, y)
                if curves: self.note('effect curves (typed)', curves)
            self.note('learned (effect layouts)')
            return y
        if len(pc) <= 0x20000 and self.pairs.get(ext):
            c, st = self.oracle(ext).convert(pc, learned.sedb_header)
            self.note('learned (field pairs)')
        else:
            c = bytearray(guess_all(pc))
            learned.sedb_header(pc, c)
            self.note('guessed (no base-game evidence)')
        L = qix.game_layout(pc) if ext in GAME_LAYOUT_KINDS else None
        lw = self.layout_words(ext) if L is not None else None
        if lw is not None:
            # the words by context (outside and inside the typed entries), then the learned entry
            # layouts, the names as they are (lyb: 3.6 % of the bytes wrong with 9 examples to
            # learn from, against 36 % for the field pairs)
            w_out, w_in, kb, _ = lw
            out_w, in_w = qix.split_words(pc, L)
            c = w_out.apply(pc, bytes(c), out_w)
            c = w_in.apply(pc, c, in_w)
            c = qix.apply(pc, c, kb['blocks'], lay=qix.game_layout)[0]
            c = qix.keep_text(pc, c, L)
            c = qix.apply_layout_rules(pc, c, L, lw[3])
            self.note('learned (words and entry layouts)')
        # the parts the game relocates (type table, block lists, field lists, pointers) exactly,
        # whenever the member has them (vtex: the size counts its texels, so not the effect path)
        c, n = qix.fix(pc, bytes(c))
        c, m = qix.fix_game(pc, bytes(c))
        if n or m: self.note('relocation table made exact')
        if lw is not None: c = qix.layout_post(pc, bytes(c), L, lw[3])
        return self.header_rule(ext, pc, bytes(c))

    def layout_words(self, ext):
        """(words outside the entries, words inside, entry layouts) learned from the base game's
        members of this kind; None with too few"""
        prs = [(a, b) for a, b in self.pairs.get(ext, []) if len(a) == len(b)]
        if len(prs) < MIN_LAYOUT_PAIRS: return None
        with self.lock:
            if ext not in self._layout_words:
                from . import scb
                w_out, w_in, kb = scb.Words(ext), scb.Words(ext + ':entry'), dict(blocks={}, targets={})
                kl = {}
                for a, b in prs:
                    L = qix.game_layout(a)
                    if L is None: continue
                    o, i = qix.split_words(a, L)
                    w_out.add(a, b, o); w_in.add(a, b, i)
                    qix.count_pair(kb, a, b, lay=qix.game_layout)
                    qix.count_layout(kl, a, b, L)
                self._layout_words[ext] = (w_out, w_in, qix.masks(kb), qix.layout_rules(kl))
                self.log(f'  {ext}: words and entry layouts learned from {len(prs)} base-game members')
            return self._layout_words[ext]

    def header_rule(self, ext, pc, y):
        """the SEDB header (0x20 bytes) as the 360 members of this kind have it: kept as the PC
        bytes (lyb, txbh, mtb, phb), or converted with the byte order flag the 360 ones carry
        (CCB: 0 where the PC has 1) - learned from the base-game pairs, only when they agree"""
        if pc[:4] != b'SEDB' or len(pc) < 0x20 or len(y) < 0x20: return y
        rule = self._header_rules.get(ext)
        if rule is None:
            kept = conv = 0; flags = set()
            for a, b in self.pairs.get(ext, [])[:100]:
                if a[:4] != b'SEDB' or len(b) < 0x20: continue
                if b[:0x20] == a[:0x20]: kept += 1; continue
                t = bytearray(a[:0x20]); learned.sedb_header(a, t)
                if b[:0xC] == bytes(t[:0xC]) and b[0xD:0x20] == bytes(t[0xD:0x20]): conv += 1; flags.add(b[0xC])
                else: conv = kept = -10 ** 6
            rule = 'keep' if kept > 0 and conv == 0 else (('flag', flags.pop()) if conv > 0 and kept == 0 and len(flags) == 1 else 'as is')
            self._header_rules[ext] = rule
        if rule == 'as is': return y
        out = bytearray(y)
        if rule == 'keep':
            out[:0x20] = pc[:0x20]
        else:
            t = bytearray(pc[:0x20]); learned.sedb_header(pc, t); t[0xC] = rule[1]
            out[:0x20] = t
        return bytes(out)

    # ---- bundles (WPD) --------------------------------------------------------------------
    def bundle(self, pc, path, pc_imgb=None):
        """WPD bundle (+ its companion .imgb: the texels of its texture headers). -> (360 bundle, 360 imgb or None)"""
        ents = wpd.read(pc)
        out = []
        tex = []          # (index in out, convert_txb result)
        for name, ext, data in ents:
            e = ext.rstrip(b'\0')
            k = self.known.get(md5(data))
            # (texture headers with texels: converted with them even when the headers are known -
            # a known header alone left the texture file empty: ev_vazb_010 veffs.uk/ru/ck)
            if k is not None and not (e in (b'txbh', b'vtex') and pc_imgb is not None and b'GTEX' in data):
                out.append([name, ext, k]); self.note('table (member)'); continue
            if e in SAME_MEMBERS:
                out.append([name, ext, data]); self.note('copied (member)'); continue
            g = data.find(b'GTEX')
            if e in (b'txbh', b'vtex') and g >= 0 and pc_imgb is not None:
                hdr = self.learned_member(e.decode(), data, path) if e == b'vtex' else bytearray(data)
                hdr = bytearray(hdr)
                hdr[g:] = data[g:]              # the GTEX header itself: converted with its texels
                r = texture.convert_txb(bytes(hdr), pc_imgb, g)
                tex.append((len(out), r, e))
                out.append([name, ext, None]); continue
            if e == b'vmdl':
                part = vmdl.object_part(data)
                y = self.learned_member('vmdl', part, path)
                try:
                    out.append([name, ext, vmdl.convert(data, y + data[len(part):], self.shaders, self.ctx)])
                    self.note('effect model (learned, shaders from the library or compiled)')
                except KeyError:
                    out.append([name, ext, y + data[len(part):]])
                    self.note('effect model with a shader not in the library (PC shader kept)')
                continue
            if e == b'scb':
                y = scb.convert(data, self.scb_learner(), header=learned.sedb_header)
                if y is not None:
                    out.append([name, ext, y]); self.note('cutscene schedule (chunks by rule, words learned)'); continue
            if e == b'ccb':
                y = ccb.convert(data)
                if y is not None:
                    out.append([name, ext, y]); self.note('camera curves (typed container, MTB preserved)'); continue
            y = self.learned_member(e.decode(), data, path)
            if e == b'mcb' and y is not None and len(y) == len(data):
                # the words at a chunk place where every base-game example agrees
                y = self.mcb_learner().apply(data, y, only_sure=True)
            if e == b'acb' and y is not None and len(y) == len(data):
                y = self.acb_learner().apply(data, y, only_sure=True)
            if e in (b'mcb', b'acb', b'ccb') and y is not None and len(y) == len(data):
                y = cut_member_fix(e, data, y)
            out.append([name, ext, y])
        img = None
        if tex and all(e == b'vtex' for _, _, e in tex):
            # effect textures (.xfv): one after another in member order, each the full blob; the mip
            # entries {base offset, 0} (as every texture of the 360 effect files)
            buf = bytearray()
            for i, r, e in tex:
                t, blob, g = bytearray(r[0]), r[1], r[6]
                start = len(buf); buf += blob
                buf += bytes(texture.align(len(buf), 4096) - len(buf))
                struct.pack_into('>II', t, g + 0x18, start, len(blob))
                if len(r) > 7 and r[7] and r[7][0] == 'cube':
                    _, pn, rel = r[7]
                    for k in range(1, 6 * pn):
                        o, sz = rel[k]
                        if k % pn: o += start if k < pn - 1 else 0
                        struct.pack_into('>II', t, g + 0x18 + 8 * k, o, sz)
                for lv in range(1, t[g + 7]):
                    struct.pack_into('>II', t, g + 0x18 + 8 * lv, start, 0)
                out[i][2] = bytes(t)
            img = bytes(buf)
            self.note('texture (bundle)', len(tex))
        elif tex:
            data, txbs = texture.place_textures(0, [r for _, r, _ in tex])
            for (i, r, e), t in zip(tex, txbs):
                t = bytearray(t)
                if e == b'vtex':
                    g = r[6]
                    for lv in range(1, t[g + 7]):
                        struct.pack_into('>II', t, g + 0x18 + 8 * lv, struct.unpack_from('>I', t, g + 0x18)[0], 0)
                out[i][2] = bytes(t)
            img = data
            self.note('texture (bundle)', len(tex))
        elif pc_imgb is not None:
            img = b''
        # (the 360 file ends at the alignment the PC file ends at: 0x800 for the event, motion and
        # scene bundles, 0x80 for the others - as in every pair of the compare report)
        return wpd.write([tuple(o) for o in out], wpd.detect_align(pc), pc[8:16],
                         file_align=0x800 if len(pc) % 0x800 == 0 else 0x80), img

    # ---- files ----------------------------------------------------------------------------
    def file(self, path, pc, companion=None):
        """path: the 360 path. pc: the Steam bytes. companion: the Steam .imgb of a .trb / bundle.
        -> (360 bytes, 360 companion imgb or None)"""
        k = kind(path)
        if k in ('trb', 'mpk'): self.cur_path = path       # (sound runs on other threads)
        if k not in ('scd', 'soundpack'): self.ctx.folder = path.split('/', 1)[0]   # (not on the sound threads)
        hit = self.known.get(md5(pc))
        if hit is not None and k not in ('trb', 'bundle'):
            self.note('table (file)'); return hit, None
        if k == 'same':
            self.note('copied'); return pc, None
        if k == 'trb':
            t, img = model.convert(pc, companion or b'', self.shaders, bone_rule=self.bone_rule, ctx=self.ctx)
            self.note('model'); return t, img
        if k == 'mpk':
            t, _ = model.convert(pc, b'', self.shaders, pad=False, ctx=self.ctx)
            self.note('materials'); return t, None
        if k == 'bundle':
            return self.bundle(pc, path, companion)
        if k == 'scd':
            return self.scd(pc, path), None
        if k == 'soundpack':
            out = []
            for name, ext, data in wpd.read(pc):
                out.append((name, ext, self.scd(data, path) if ext.rstrip(b'\0') == b'scd' else data))
            return wpd.write(out, 0x800, pc[8:16]), None
        if k == 'single':
            return self.learned_member(path.rsplit('.', 1)[-1], pc, path), None
        raise NotImplementedError(f'{path}: no converter for this kind of file')

    def scd(self, pc, path):
        k = self.known.get(md5(pc))
        if k is not None:
            self.note('table (sound)'); return k
        if not sound.parse(pc)['entries']:
            pass
        out, st = sound.convert(pc, self.oracle('scd_head'), ffmpeg=self.ffmpeg)
        self.note('sound')
        return out
