# FF XIII-2 / Lightning Returns filelist reader (port of the crypto in Surihix's
# WhiteBinTools, GPL-3.0). usage: python3 -I wfilelist.py <filelist.bin> [--be|--le]
# Prints one line per file: code|type|pos(2048-byte units):uncompressed:compressed:path
import struct, sys, zlib

M32, M64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF

def xor_table(seed8):
    seed = bytearray(seed8)[::-1]
    a = struct.unpack_from('<I', seed, 0)[0]
    b = struct.unpack_from('<I', seed, 4)[0]
    a = ((a << 8) | (a >> 24)) & M32
    b = ((b >> 16) | (b << 16)) & M32
    blk = bytearray(struct.pack('<II', b, a))
    blk[0] = (blk[0] + 0x45) & 0xFF
    for i in range(1, 8):
        t = blk[i] + 0xD4 + blk[i - 1]
        t ^= blk[i - 1] << 2
        t ^= 0x45
        blk[i] = t & 0xFF
    table = bytearray(264)
    table[0:8] = blk
    prev = struct.unpack('<Q', bytes(blk))[0]
    for i in range(1, 0x21):
        ha, hb = prev & M32, prev >> 32
        x = (5 * prev) & M64
        x ^= hb << 32
        ta = (ha ^ x) & M32
        tb = (x >> 32) & M32
        x = ha | (x & 0xFFFFFFFF00000000)
        xa = (x ^ ta) & M32
        xb = (tb ^ hb) & M32
        blk = struct.pack('<II', xa, xb)
        table[i * 8:i * 8 + 8] = blk
        prev = struct.unpack('<Q', blk)[0]
    return bytes(table)

def decrypt(data):
    data = bytearray(data)
    body = struct.unpack_from('>I', data, 16)[0] + 8
    s = data[0:16]
    seed = (s[9] << 24) | (s[12] << 16) | (s[2] << 8) | s[0]
    if seed & 0x80000000:  # int arithmetic in the game: sign-extended to 64 bits
        seed |= 0xFFFFFFFF00000000
    table = xor_table(struct.pack('<Q', seed))
    out = bytearray(data)
    pos = 32
    for n in range(body // 8):
        counter = n * 8
        block_id = counter >> 3
        cur = data[pos:pos + 8]
        off = counter & 0xF8
        def loop(v):
            for k in range(8):
                v = ((v + 120) & 0xFF) - table[off + k]
                v &= 0xFF
            return v
        d = [loop(((block_id ^ 69) & 255) ^ cur[0])] + [loop(cur[k - 1] ^ cur[k]) for k in range(1, 8)]
        arr = bytes([d[4], d[5], d[6], d[7], d[0], d[1], d[2], d[3]])
        hi = struct.unpack_from('<I', arr, 0)[0]
        lo = struct.unpack_from('<I', arr, 4)[0]
        xlo, xhi = struct.unpack_from('<II', table, off)
        # block counter key
        c = counter
        ab = c << 10; cd = c << 20; ef = c << 30
        cv = (cd & M32) | (ab & M32); dv = (cd >> 32) | (ab >> 32)
        ev = (ef & M32) | c | cv; fv = (ef >> 32) | dv
        ev &= M32; fv &= M32
        carry = 1 if ev > (~0xA1652347 & M32) else 0
        k1 = ev + 0xA1652347
        k2 = fv + carry
        carry = 1 if lo < xlo else 0
        L = lo - xlo; H = hi - xhi - carry
        L ^= k1; H ^= k2
        L ^= xlo; H ^= xhi
        out[pos:pos + 8] = struct.pack('<II', H & M32, L & M32)
        pos += 8
    size = struct.unpack_from('>I', out, 16)[0]
    chk = struct.unpack_from('<I', out, 32 + size + 4)[0]
    calc = sum(out[32 + 4 * i] for i in range(size // 4)) & M32
    return bytes(out), chk == calc

def parse(path, endian=None):
    raw = open(path, 'rb').read()
    base = 0
    if len(raw) >= 24 and struct.unpack_from('<I', raw, 20)[0] == 0x1DE03478:
        raw, ok = decrypt(raw)
        if not ok:
            raise ValueError('checksum mismatch after decrypt')
        base = 32
    if endian is None:
        endian = '<' if struct.unpack_from('<I', raw, base + 8)[0] < 0x100000 else '>'
    e = endian
    chunk_tab, chunk_data, count = struct.unpack_from(e + 'III', raw, base)
    entries = []
    nchunks = 0
    for i in range(count):
        code, info, chunk, ftype = struct.unpack_from(e + 'IHBB', raw, base + 12 + i * 8)
        chunk = (chunk << 1) | (info >> 15)
        info &= 0x7FFF
        entries.append((code, ftype, chunk, info))
        nchunks = max(nchunks, chunk + 1)
    chunks = []
    for c in range(nchunks):
        un, cm, cp = struct.unpack_from(e + 'III', raw, base + chunk_tab + 12 * c)
        p = base + chunk_data + cp
        chunks.append(zlib.decompress(raw[p:p + cm]))
    out = []
    for code, ftype, chunk, info in entries:
        b = chunks[chunk]
        end = b.index(0, info)
        out.append((code, ftype, b[info:end].decode('ascii', 'replace')))
    return out

if __name__ == '__main__':
    e = '>' if '--be' in sys.argv else '<' if '--le' in sys.argv else None
    for code, ftype, info in parse(sys.argv[1], e):
        print(f'{code}|{ftype}|{info}')
