# A downloadable content package for the port: the folder the 360 copies to a drive, with its
# file lists and archives rebuilt (archive.py reproduces the real packages byte for byte), and the
# content header the port reads (as the 360 DLC importer writes it: content type 2, title
# 53510806, full license).
#   <userdata>\0000000000000000\53510806\00000002\<name>\filelist_pNNNNNNN{img,scr}_u.x360.bin,
#       white_pNNNNNNN{img,scr}_u.x360.bin, identifier ("NNNNNNN\0\0")
#   <userdata>\0000000000000000\53510806\Headers\00000002\<name>.header
import os, struct, shutil, time, tempfile, uuid
from . import archive

TITLE = 0x53510806

def header(name, display):
    h = bytearray(0x14C)
    struct.pack_into('>I', h, 0, 1)            # device: HDD
    struct.pack_into('>I', h, 4, 2)            # content type: downloadable content
    disp = display.encode('utf-16-be')[:0x100]
    h[8:8 + len(disp)] = disp
    n = name.encode('ascii')[:42]
    h[0x108:0x108 + len(n)] = n
    struct.pack_into('>I', h, 0x140, TITLE)
    struct.pack_into('>I', h, 0x148, 0xFFFFFFFF)   # license mask: every bit
    return bytes(h)

def write(out_dir, ident, kind, hdr, files):
    """one list of a package, streamed: files = iterable of (code, ftype, path, data). The archive
    is written as the files come (zlib level 9, sound stored as it is: archive.pack_archive)."""
    import zlib
    os.makedirs(out_dir, exist_ok=True)
    ents = []
    wpath = os.path.join(out_dir, f'white_p{ident}{kind}_u.x360.bin')
    with open(wpath, 'wb') as w:
        pos = 0
        for code, ftype, path, data in files:
            if isinstance(data, tuple):
                # ('stored', bytes as stored, size): a file of the last build, copied as it is
                _, z, un = data
            else:
                z = zlib.compress(data, 9) if not path.endswith('.scd') else data
                if len(z) >= len(data) and data: z = data
                un = len(data)
            w.write(z)
            pad = (-len(z)) % 2048
            w.write(bytes(pad))
            ents.append((code, ftype, pos // 2048, un, len(z), path))
            pos += len(z) + pad
    with open(os.path.join(out_dir, f'filelist_p{ident}{kind}_u.x360.bin'), 'wb') as f:
        # The native reader searches by (type, code), not pathname or archive
        # position. The Steam inventory is in pathname order. Keep the streamed
        # archive intact and sort only its index, retaining every sector offset.
        ents.sort(key=lambda e: (e[1], e[0]))
        f.write(archive.encrypt(archive.build_plain(hdr, ents)))
    with open(os.path.join(out_dir, 'identifier'), 'wb') as f:
        f.write(ident.encode('ascii') + b'\0\0')
    return ents

def install(src_dir, userdata, name, display, log=print):
    root = os.path.join(userdata, '0000000000000000', '53510806')
    dest = os.path.join(root, '00000002', name)
    heads = os.path.join(root, 'Headers', '00000002')
    # Finish copying before replacing the installed package. A failed copy or
    # header write must not remove the version the player already has.
    staging = os.path.join(userdata, '..', 'work', 'steamdlc', 'install-staging')
    os.makedirs(staging, exist_ok=True)
    temp = tempfile.mkdtemp(prefix='package-', dir=staging)
    new = os.path.join(temp, 'new')
    old = None
    placed = False
    headtemp = None
    try:
        shutil.copytree(src_dir, new)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.makedirs(heads, exist_ok=True)
        headtemp = os.path.join(heads, name + '.header.' + uuid.uuid4().hex + '.new')
        with open(headtemp, 'wb') as stream: stream.write(header(name, display))
        if os.path.exists(dest):
            if name.startswith('XIII2SteamDLC'):
                old = os.path.join(temp, 'old')
            else:
                old = os.path.join(userdata, '..', 'save_backups',
                                   'dlc-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8], name)
                os.makedirs(os.path.dirname(old), exist_ok=True)
            os.replace(dest, old)
        os.replace(new, dest)
        placed = True
        os.replace(headtemp, os.path.join(heads, name + '.header'))
        if old and not name.startswith('XIII2SteamDLC'):
            log(f'  the earlier {name} moved to {os.path.normpath(old)}')
    except Exception:
        if placed: shutil.rmtree(dest)
        if old and os.path.exists(old): os.replace(old, dest)
        raise
    finally:
        if headtemp and os.path.exists(headtemp): os.remove(headtemp)
        shutil.rmtree(temp, ignore_errors=True)
    return dest

def installed(userdata):
    """identifier -> package folder name of the DLC packages already installed"""
    root = os.path.join(userdata, '0000000000000000', '53510806', '00000002')
    out = {}
    if os.path.isdir(root):
        for n in os.listdir(root):
            p = os.path.join(root, n, 'identifier')
            if os.path.exists(p):
                with open(p, 'rb') as stream: out[stream.read()[:7].decode('latin1')] = n
    return out

class Reuse:
    """the files of the last build of a package list (out_dir), with the key each was made with
    (x2dlc_keys_<kind>.json): a file whose key is the same again is copied from there"""
    def __init__(self, old_dir, ident, kind, bootstrap=None):
        import json
        self.kind, self.new, self.reused, self.f, self.ents, self.keys = kind, {}, 0, None, {}, {}
        self.bootstrap = bootstrap      # (path -> bool): files of a build without keys that are kept
        fl = os.path.join(old_dir, f'filelist_p{ident}{kind}_u.x360.bin')
        wh = os.path.join(old_dir, f'white_p{ident}{kind}_u.x360.bin')
        kp = os.path.join(old_dir, f'x2dlc_keys_{kind}.json')
        if not (os.path.exists(fl) and os.path.exists(wh)): return
        try:
            _, ents = archive.read_list(fl)
        except Exception:
            return
        self.ents = {e[5]: e for e in ents}
        self.f = open(wh, 'rb')
        if os.path.exists(kp):
            with open(kp) as stream: self.keys = json.load(stream)
            self.bootstrap = None
    def get(self, path, key):
        e = self.ents.get(path)
        if e is None or self.f is None: return None
        if self.keys.get(path) != key and not (self.bootstrap and not self.keys and self.bootstrap(path)):
            return None
        code, ftype, pos, un, cm, _ = e
        if un == 0: return None                 # (a file that did not convert last time)
        self.f.seek(pos * 2048)
        z = self.f.read(cm)
        if len(z) != cm: return None
        self.new[path] = key
        self.reused += 1
        return ('stored', z, un)
    def put(self, path, key):
        self.new[path] = key
    def close(self):
        if self.f: self.f.close(); self.f = None
    def save(self, out_dir):
        import json
        with open(os.path.join(out_dir, f'x2dlc_keys_{self.kind}.json'), 'w') as f:
            json.dump(self.new, f)
