# Index of a whole FFXIII-2 install by file path (the platform part '.x360.' / '.win32.' taken out):
#   360  : <game>\alba_data\sys\filelistu / filelist_scru (+ white_imgu / white_scru), the zone
#          archives (alba_data\zone\white_zNNNNu_img.x360.bin, their lists inside the others), and
#          the title update's archives (filelist_patch_u / filelist_patch_scr_u), which come first.
#   Steam: <Steam folder>\alba_data\sys\filelistu / filelist_scru, alba_data\zone, and the update
#          archives in alba_data\udp (patch_u / patch_scr_u), which come first.
# Set 'u' (the English set) on both. Everything is opened read-only.
import os, re, zlib, threading
from . import archive

def norm(path):
    return path.replace('.win32.', '.').replace('.x360.', '.')

class GameIndex:
    """files: {norm path: (archive path, pos, uncompressed, compressed, real path, code, ftype)}"""
    def __init__(self, root, platform, update=None, log=print):
        self.root, self.platform = root, platform
        alba = os.path.join(root, 'alba_data')
        if not os.path.isdir(alba):
            raise FileNotFoundError(f'no alba_data folder in {root}')
        self.files, self.handles, self.sizes, self.sources = {}, {}, {}, []
        self.outside = 0
        self.lock = threading.Lock()         # one handle per archive, read from several threads
        lists = []
        if platform == 'x360':
            if update:
                for n in ('patch_', 'patch_scr_'):
                    lists.append((os.path.join(update, f'filelist_{n}u.x360.bin'), os.path.join(update, f'white_{n}u.x360.bin')))
        else:
            for n in ('patch_', 'patch_scr_'):
                lists.append((os.path.join(alba, 'udp', f'filelist_{n}u.win32.bin'), os.path.join(alba, 'udp', f'white_{n}u.win32.bin')))
        sysd = os.path.join(alba, 'sys')
        lists.append((os.path.join(sysd, f'filelistu.{platform}.bin'), os.path.join(sysd, f'white_imgu.{platform}.bin')))
        lists.append((os.path.join(sysd, f'filelist_scru.{platform}.bin'), os.path.join(sysd, f'white_scru.{platform}.bin')))
        for fl, wh in lists:
            if os.path.exists(fl) and os.path.exists(wh):
                self._add(fl, wh, os.path.basename(fl))
        # zone archives: their lists are files of the archives above
        for key, e in list(self.files.items()):
            m = re.match(r'zone/filelist_(z\d{4})u\.(?:win32|x360)\.bin$', e[4])
            if not m: continue
            arc = os.path.join(alba, 'zone', f'white_{m.group(1)}u_img.{platform}.bin')
            if not os.path.exists(arc) or os.path.getsize(arc) <= 2048: continue
            data = self.read(key)
            if data is None: continue
            try:
                self._add(data, arc, 'zone ' + m.group(1))
            except Exception as ex:
                log(f'  zone list {e[4]}: {ex}')

    def _add(self, fl, arc, name):
        hdr, ents = archive.read_list(fl)
        self.sources.append((name, len(ents)))
        for code, ftype, pos, un, cm, path in ents:
            self.files.setdefault(norm(path), (arc, pos, un, cm, path, code, ftype))

    def real_path(self, key):
        e = self.files.get(key)
        return e[4] if e else None

    def read(self, key):
        """file bytes, or None (missing, or a zero-filled entry)"""
        e = self.files.get(key)
        if e is None: return None
        arc, pos, un, cm = e[:4]
        with self.lock:
            f = self.handles.get(arc)
            if f is None:
                self.sizes[arc] = os.path.getsize(arc)
                f = self.handles[arc] = open(arc, 'rb')
            if pos * 2048 + cm > self.sizes[arc]:
                self.outside += 1            # (an entry in another archive: not read)
                return None
            f.seek(pos * 2048)
            d = f.read(cm)
        if cm != un:
            try: d = zlib.decompress(d)
            except zlib.error: return None
        elif d and not any(d[:4096]) and not any(d[-4096:]) and len(d) > 8192:
            return None
        return d

    def close(self):
        for f in self.handles.values(): f.close()
        self.handles = {}

class SampleIndex:
    """The same interface over a folder of loose files (the DLC sample: steam\\<path>)."""
    def __init__(self, folder):
        self.root, self.files = folder, {}
        for dp, dn, fn in os.walk(folder):
            for n in fn:
                p = os.path.relpath(os.path.join(dp, n), folder).replace('\\', '/')
                self.files[norm(p)] = (os.path.join(dp, n), 0, 0, 0, p, 0, 0)
    def real_path(self, key):
        e = self.files.get(key)
        return e[4] if e else None
    def read(self, key):
        e = self.files.get(key)
        return open(e[0], 'rb').read() if e else None
    def close(self):
        pass
