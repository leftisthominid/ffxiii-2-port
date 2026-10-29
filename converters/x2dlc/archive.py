# Lightning Returns "white" archive + encrypted filelist reader/writer (360 and PC layouts).
# (from the earlier wbin.py; verified: repacking the real 360 Yuna package gives byte-identical filelists/archives)
# Crypto: inverse of scripts/wfilelist.py (port of Surihix's WhiteBinTools, GPL-3.0).
#   python3 wbin.py unpack <filelist.bin> <white.bin> <out_dir>
#   python3 wbin.py selftest <filelist.bin>      (decrypt -> encrypt must give the same bytes)
import os, struct, sys, zlib
from . import wfilelist as wf

M32 = 0xFFFFFFFF

def _block_key(counter):
    c = counter
    ab = c << 10; cd = c << 20; ef = c << 30
    cv = (cd & M32) | (ab & M32); dv = (cd >> 32) | (ab >> 32)
    ev = (ef & M32) | c | cv; fv = (ef >> 32) | dv
    ev &= M32; fv &= M32
    carry = 1 if ev > (~0xA1652347 & M32) else 0
    return ev + 0xA1652347, fv + carry

def encrypt(plain):
    """plain: full file image (32-byte header + body) as decrypt() returns it."""
    data = bytearray(plain)
    size = struct.unpack_from('>I', data, 16)[0]
    body = size + 8
    # checksum: sum of every 4th byte of the payload, stored after it
    struct.pack_into('<I', data, 32 + size + 4, sum(data[32 + 4 * i] for i in range(size // 4)) & M32)
    s = data[0:16]
    seed = (s[9] << 24) | (s[12] << 16) | (s[2] << 8) | s[0]
    if seed & 0x80000000:
        seed |= 0xFFFFFFFF00000000
    table = wf.xor_table(struct.pack('<Q', seed))
    out = bytearray(data)
    pos = 32
    for n in range(body // 8):
        counter = n * 8
        off = counter & 0xF8
        S = sum(table[off:off + 8])
        H, L = struct.unpack_from('<II', data, pos)
        xlo, xhi = struct.unpack_from('<II', table, off)
        k1, k2 = _block_key(counter)
        Lsub = (L ^ xlo ^ k1) & M32
        Hsub = (H ^ xhi ^ k2) & M32
        t = Lsub + xlo
        lo = t & M32
        hi = (Hsub + xhi + (t >> 32)) & M32
        d = list(struct.pack('<I', lo)) + list(struct.pack('<I', hi))
        unloop = lambda v: (v - 960 + S) & 0xFF
        c = [0] * 8
        c[0] = unloop(d[0]) ^ ((n ^ 69) & 255)
        for k in range(1, 8):
            c[k] = unloop(d[k]) ^ c[k - 1]
        out[pos:pos + 8] = bytes(c)
        pos += 8
    return bytes(out)

def parse_plain(raw):
    """Decrypted filelist image -> (header32, entries[(code, ftype, pos, un, cm, path)])."""
    base = 32
    e = '<'
    chunk_tab, chunk_data, count = struct.unpack_from(e + 'III', raw, base)
    ents, nchunks = [], 0
    for i in range(count):
        code, info, chunk, ftype = struct.unpack_from(e + 'IHBB', raw, base + 12 + i * 8)
        chunk = (chunk << 1) | (info >> 15)
        ents.append((code, ftype, chunk, info & 0x7FFF))
        nchunks = max(nchunks, chunk + 1)
    chunks = []
    for c in range(nchunks):
        un, cm, cp = struct.unpack_from(e + 'III', raw, base + chunk_tab + 12 * c)
        p = base + chunk_data + cp
        chunks.append(zlib.decompress(raw[p:p + cm]))
    out = []
    for code, ftype, chunk, info in ents:
        b = chunks[chunk]
        s = b[info:b.index(0, info)].decode('ascii')
        pos, un, cm, path = s.split(':', 3)
        out.append((code, ftype, int(pos, 16), int(un, 16), int(cm, 16), path))
    return raw[:32], out

def build_plain(header32, entries, chunk_limit=0x8000):
    """entries: [(code, ftype, pos2048, un, cm, path)] -> decrypted filelist image."""
    strings, chunks, cur = [], [], bytearray()
    for code, ftype, pos, un, cm, path in entries:
        s = f'{pos:x}:{un:x}:{cm:x}:{path}'.encode('ascii') + b'\0'
        if len(cur) + len(s) > chunk_limit and cur:
            chunks.append(bytes(cur)); cur = bytearray()
        strings.append((len(chunks), len(cur)))
        cur += s
    cur += b'end\0'                          # the game's lists end with this entry
    chunks.append(bytes(cur))
    count = len(entries)
    chunk_tab = 12 + 8 * count
    chunk_data = chunk_tab + 12 * len(chunks)
    body = bytearray(struct.pack('<III', chunk_tab, chunk_data, count))
    for (code, ftype, *_), (ci, off) in zip(entries, strings):
        info = off | ((ci & 1) << 15)
        body += struct.pack('<IHBB', code, info, ci >> 1, ftype)
    comp = [zlib.compress(c, 9) for c in chunks]
    cp = 0
    for c, z in zip(chunks, comp):
        body += struct.pack('<III', len(c), len(z), cp)
        cp += len(z)
    for z in comp:
        body += z
    while len(body) % 8: body += b'\0'      # whole 8-byte blocks
    hdr = bytearray(header32)
    struct.pack_into('>I', hdr, 16, len(body))
    struct.pack_into('<I', hdr, 20, 0x1DE03478)
    # After the body: its size and checksum (encrypted with it), then 8 zero bytes.
    return bytes(hdr) + bytes(body) + struct.pack('<II', len(body), 0) + bytes(8)

def pack_archive(files, compress=True):
    """files: [(code, ftype, path, data)] -> (archive bytes, entries)."""
    arc, ents = bytearray(), []
    for code, ftype, path, data in files:
        pos = len(arc) // 2048
        # Streamed sound (.scd) is stored as is, like the original packages.
        z = zlib.compress(data, 9) if compress and not path.endswith('.scd') else data
        if len(z) >= len(data) and data: z = data   # an empty file keeps its 8-byte zlib stream (XIII-2 packages)
        arc += z
        arc += bytes((-len(arc)) % 2048)
        ents.append((code, ftype, pos, len(data), len(z), path))
    return bytes(arc), ents

def unpack(filelist, white, out_dir):
    raw, ok = wf.decrypt(open(filelist, 'rb').read())
    assert ok, 'checksum'
    _, ents = parse_plain(raw)
    arc = open(white, 'rb').read()
    for code, ftype, pos, un, cm, path in ents:
        d = arc[pos * 2048:pos * 2048 + cm]
        if cm != un: d = zlib.decompress(d)
        p = os.path.join(out_dir, path); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(d)
    return ents


def read_list(path_or_bytes):
    if isinstance(path_or_bytes, (str, os.PathLike)):
        with open(path_or_bytes, 'rb') as stream:
            raw = stream.read()
    else:
        raw = path_or_bytes
    # The Japanese-voice builder emits the game's supported headerless lists.
    # Their offsets are relative to byte zero, unlike encrypted media lists.
    if len(raw) >= 12:
        table, data, count = struct.unpack_from('<III', raw)
        if table == 12 + 8 * count and table <= data <= len(raw):
            return parse_plain(bytes(32) + raw)
    plain, ok = wf.decrypt(raw)
    if not ok: raise ValueError('filelist checksum mismatch')
    return parse_plain(plain)

def read_files(filelist, white):
    """-> header32, [(code, ftype, path, data)] in list order"""
    hdr, ents = read_list(filelist)
    arc = open(white, 'rb').read()
    out = []
    for code, ftype, pos, un, cm, path in ents:
        d = arc[pos * 2048:pos * 2048 + cm]
        if cm != un: d = zlib.decompress(d)
        out.append((code, ftype, path, d))
    return hdr, out

def write_pack(files, header32, filelist_path, white_path):
    arc, ents = pack_archive(files)
    open(white_path, 'wb').write(arc)
    open(filelist_path, 'wb').write(encrypt(build_plain(header32, ents)))
    return ents
