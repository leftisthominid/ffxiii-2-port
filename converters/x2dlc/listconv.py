# The conversion of one list of a package (the files' order, texture files with their owners),
# used by the build in this process and by its worker processes (convert_list: the models and
# bundles of a list are converted in parallel, each owner with its texture files in one worker).
import os, re, sys, traceback
from . import gameindex, texture

class ListConverter:
    def __init__(self, conv, S, files):
        self.conv, self.S = conv, S
        paths = {p for _, _, p in files}
        made = {}
        OWNER_EXTS = ('.x360.trb', '.x360.xfv', '.x360.xgr', '.x360.xwb', '.x360.bin', '.x360.xwp')
        def owner_of(imgb):
            """(owner path, in the package) of a texture file: the .trb / bundle with its headers, in
            the package or else in the Steam version (the DLC replaces only the texels of a base-game
            model, e.g. the face pictures)"""
            stem = imgb[:-len('.x360.imgb')] if imgb.endswith('.x360.imgb') else imgb[:-5]
            cands = [stem + e for e in OWNER_EXTS]
            # an event's DataSet/b00.white.x360.imgb: the texture headers are in b00_us.white.x360.bin
            if stem.endswith('.white'): cands.append(stem[:-6] + '_us.white.x360.bin')
            for c in cands:
                if c in paths: return c, True
            for c in cands:
                if gameindex.norm(c) in S.files: return c, False
            return None, False
        def companion_of(path):
            """the texture file converted with a .trb / bundle (or None)"""
            stem = path.rsplit('.x360.', 1)[0] if '.x360.' in path else None
            if stem and stem + '.x360.imgb' in paths: return stem + '.x360.imgb'
            if stem and stem.endswith('_us.white') and stem[:-9] + '.white.x360.imgb' in paths:
                return stem[:-9] + '.white.x360.imgb'
            if stem and path not in paths and gameindex.norm(stem + '.x360.imgb') in S.files:
                return stem + '.x360.imgb'       # an owner from the Steam version: its texels
            return None
        early = {}                          # owners converted ahead of their place in the list
        def one(path):
            if path in early: return early.pop(path)
            key = gameindex.norm(path)
            pc = S.read(key)
            if pc is None:
                alternate = 'movie_win/' + key[6:] if key.startswith('movie/') else None
                if alternate and alternate in S.files:
                    folder = getattr(conv, 'movie_dir', None)
                    transport = os.path.join(folder, os.path.basename(key)) if folder else None
                    if transport and os.path.isfile(transport):
                        with open(transport, 'rb') as stream: data = stream.read()
                        if data[:3] != b'BIK': raise ValueError('invalid prepared movie transport')
                        conv.note('Steam movie (decoded overlay with verified Bink transport)')
                        return data
                    raise NotImplementedError(f'Steam movie is indexed at {alternate}; run the movies command before building packages')
                raise FileNotFoundError('not in the Steam version')
            comp = None
            if not path.endswith('.imgb'):
                imgb = companion_of(path)
                if imgb: comp = S.read(gameindex.norm(imgb))
                data, img = conv.file(path, pc, comp)
                if imgb and img is not None: made[imgb] = img
                return data
            # an empty Steam texture file: the 360 one holds what the owner's conversion moves there
            # (the vertex data of a model whose Steam mesh keeps it in the .trb: c161, c162), else
            # it stays empty (empty in both versions: bg/loc161 block000, block460)
            if not pc:
                o, inside = owner_of(path)
                if o is not None and path not in made and o not in early:
                    d = one(o)               # (an owner that fails is reported as itself)
                    if inside: early[o] = d
                return made.pop(path, b'')
            o, inside = owner_of(path)
            if o is None and re.match(r'scene/lay/scene\d+/bin/scene\d+_\d+_split_\d+\.x360\.imgb$', path):
                # scene split textures have no headers: 512 x 512 DXT1, one level, in both versions
                # (tiled on the 360; the same bytes as the 360 files)
                if len(pc) != 512 * 512 // 2:
                    raise NotImplementedError(f'a scene split texture of {len(pc)} bytes (512 x 512 DXT1 is 131072)')
                return texture.to_360([pc], 512, 512, 0x18)[0]
            if o is None and re.match(r'gui/resident/face/face\d+\.x360\.imgb$', path):
                # the menu face pictures have no headers: 256 x 64 DXT5 in both versions (tiled and
                # padded to 32 block rows with 0xCC on the 360; the same bytes as the 360 files)
                if len(pc) != 256 * 64:
                    raise NotImplementedError(f'a face picture of {len(pc)} bytes (256 x 64 DXT5 is 16384)')
                return texture.to_360([pc], 256, 64, 0x1A, base_fill=0xCC)[0]
            if o is None:
                raise NotImplementedError('a texture file without its owner (no .trb or bundle with its headers)')
            if path not in made:
                try:
                    d = one(o)               # the owner comes later in the list, or is not in the package
                    if inside: early[o] = d
                except Exception as ex:
                    raise RuntimeError(f'its owner {o} did not convert ({type(ex).__name__}: {ex})')
            if path not in made:
                raise RuntimeError(f'its owner {o} made no texture file')
            img = made.pop(path)
            if not img and pc:
                # the owner has no texture headers for it (ev_cszb_010 DataSet/b00: the bundle holds
                # only filter effects): the Steam texels in the 360 byte order (16-bit words), so the
                # file is not empty
                conv.note('texture file its owner has no headers for (Steam texels, 16-bit swapped)')
                img = bytes(b for i in range(0, len(pc) - 1, 2) for b in (pc[i + 1], pc[i])) + pc[len(pc) & ~1:]
            return img
        self.paths, self.made, self.early = paths, made, early
        self.owner_of, self.companion_of, self.one = owner_of, companion_of, one

# ---- worker processes ----
_W = {}

def worker_init(work, steam, ffmpeg):
    """a worker process: the learned data, the Steam index and a converter of its own"""
    from . import learn, convert
    data = learn.load(work)
    if os.path.isdir(os.path.join(steam, 'alba_data')):
        S = gameindex.GameIndex(steam, 'win32', log=lambda *a: None)
    else:
        S = gameindex.SampleIndex(steam)
    conv = convert.Converter(data, log=lambda *a: None, ffmpeg=ffmpeg)
    conv.movie_dir = os.path.join(work, 'movies', 'placeholders')
    conv.read_pc = S.read
    _W.update(conv=conv, S=S, lists={}, work=work)

def worker_run(ident, kind, todo):
    """converts the paths todo (an owner and its texture files, in list order) -> ([(path, data,
    problem)], stats added)"""
    conv, S = _W['conv'], _W['S']
    lc = _W['lists'].get((ident, kind))
    if lc is None:
        from . import manifest
        mf = manifest.load(_W['work'])
        files = next([tuple(x) for x in l['files']] for l in mf[ident]['lists'] if l['kind'] == kind)
        lc = _W['lists'][(ident, kind)] = ListConverter(conv, S, files)
    before = dict(conv.stats)
    out = []
    for p in todo:
        try:
            out.append((p, lc.one(p), None))
        except Exception as ex:
            out.append((p, b'', f'{type(ex).__name__}: {ex}\n' + traceback.format_exc(limit=12)))
    lc.made.clear(); lc.early.clear()
    added = {k: v - before.get(k, 0) for k, v in conv.stats.items() if v != before.get(k, 0)}
    return out, added
