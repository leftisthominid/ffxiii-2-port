# SQEX sound containers (SEDBSSCF .scd), PC (little-endian) -> 360 (big-endian).
# Layout: SEDB header (0x0C endian flag), table header at 0x30 {u16 n0,n1,n2,id; u32 off0..off4},
#   n2 sound entry offsets at off1; header tables (sound/track parameters) up to the first entry;
#   entry: 8 x u32 {stream size, channels, rate, codec, loop start, loop end, extradata size,
#   aux chunk count}, aux chunks ('MARK' ...), codec extradata, stream data.
# Codecs seen: PC 12 = MS-ADPCM (voices, sound effects), 6 = Ogg Vorbis (fanfare music);
#   360 6 = Ogg Vorbis (voices, music), 11 = XMA2 (sound effects in .wpd).
# Ogg (codec 6, "version 2") extradata: {u8 2, u8 0x20, u8 xor, u8 0, u32 0, u16 a, u16 b,
#   f32 seek interval (0.1 s), u32 seek table bytes, u32 vorbis header bytes, u32 c, u32 0},
#   seek table (u32 byte offset of the first page whose granule >= k * interval * rate),
#   the header Ogg pages XOR'ed with the xor byte, then the audio pages (= stream size).
# PC Ogg entries are converted exactly (header fields swapped). PC MS-ADPCM entries are decoded
# and re-encoded to Ogg Vorbis with ffmpeg/libvorbis (lossy, 48000 Hz) because no XMA2 encoder
# is available; on the 360 the sound-effect banks use XMA2, so Ogg there is an assumption that
# the game's SCD player accepts Ogg in those banks as it does for voices (not verified).
# The header tables (mixed u8/u16/u32/f32 fields) go through the learned converter
# (learned.py, oracle = the Yuna SCDs).
import struct, subprocess, tempfile, os, math, hashlib
from .util import align
from . import learned

def parse(d):
    E = '>' if d[0xC] == 1 else '<'
    hs = struct.unpack_from(E + 'H', d, 0xE)[0]
    n0, n1, n2, sid = struct.unpack_from(E + '4H', d, hs)
    offs = struct.unpack_from(E + '5I', d, hs + 8)
    eo = [struct.unpack_from(E + 'I', d, offs[1] + 4 * i)[0] for i in range(n2)]   # (as vgmstream)
    ents = []
    for o in eo:
        size, ch, rate, codec, ls, le, extra, aux = struct.unpack_from(E + '8I', d, o)
        ex = d[o + 0x20:o + 0x20 + extra]
        auxb, p = [], 0
        for _ in range(aux):
            ln = struct.unpack_from(E + 'I', ex, p + 4)[0]
            auxb.append(ex[p:p + ln]); p += ln
        ents.append(dict(off=o, size=size, ch=ch, rate=rate, codec=codec, loop=(ls, le), aux=auxb,
                         cx=ex[p:], data=d[o + 0x20 + extra:o + 0x20 + extra + size]))
    head = d[:min(eo)] if eo else d
    return dict(E=E, hs=hs, offs=offs, n2=n2, head=head, entries=ents)

def aux_to_be(chunk):
    out = bytearray(chunk)
    for i in range(4, len(chunk) - 3, 4):
        out[i:i + 4] = chunk[i:i + 4][::-1]
    return bytes(out)

def ogg_cx_to_be(cx):
    """PC Ogg extradata -> 360 (header fields + seek table swapped, pages unchanged)."""
    h = bytearray(cx[:0x20])
    seek = struct.unpack_from('<I', cx, 0x10)[0]
    o = bytearray(cx)
    o[4:8] = h[4:8][::-1]
    o[8:10] = h[8:10][::-1]; o[10:12] = h[10:12][::-1]
    for i in range(0xC, 0x20, 4): o[i:i + 4] = h[i:i + 4][::-1]
    for i in range(0x20, 0x20 + seek, 4): o[i:i + 4] = cx[i:i + 4][::-1]
    return bytes(o)

def ogg_pages(b):
    out, i = [], 0
    while i + 27 <= len(b) and b[i:i + 4] == b'OggS':
        nseg = b[i + 26]
        size = 27 + nseg + sum(b[i + 27:i + 27 + nseg])
        out.append((i, struct.unpack_from('<q', b, i + 6)[0], b[i:i + size]))
        i += size
    return out

LOOPED = []         # loop points of re-encoded looped streams (reported)
FFMPEG = 'ffmpeg'   # set by the caller (the builder's ffmpeg.exe)

# Size target for a re-encoded stream: the 360 voices are Ogg at about 0.12-0.22 of the size of
# the Steam MS-ADPCM stream of the same take (the DLC sample); an area's voice banks are loaded
# whole into fixed game memory, so a converted bank must not grow (Japanese voices: larger banks
# made allocations fail - Serah missing, then a crash). Tried in order until one fits.
BUDGET = 0.22   # of the MS-ADPCM bytes, plus 2 KB for the Vorbis headers of a short clip
STEPS = ((48000, '-1'), (24000, '-1'), (16000, '0'), (11025, '0'), (8000, '0'))

def msadpcm_to_ogg(ent, quality=2):
    """decode the PC MS-ADPCM stream with ffmpeg, re-encode Vorbis -> (cx, data, samples, rate)."""
    cx = ent['cx']
    cb = struct.unpack_from('<H', cx, 16)[0]
    fmt = cx[:18 + cb]
    data = ent['data']
    wav = (b'RIFF' + struct.pack('<I', 4 + 8 + len(fmt) + 8 + len(data)) + b'WAVE' + b'fmt ' +
           struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data)
    budget = max(int(len(data) * BUDGET) + 2048, 8192)   # short effect sounds: not below 8 KB
    with tempfile.TemporaryDirectory() as td:
        wi, wo = os.path.join(td, 'in.wav'), os.path.join(td, 'out.ogg')
        open(wi, 'wb').write(wav)
        for rate, q in STEPS:
            try:
                r = subprocess.run([FFMPEG, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', wi,
                                    '-ar', str(rate), '-c:a', 'libvorbis', '-q:a', q, '-map_metadata', '-1',
                                    '-fflags', '+bitexact', '-flags:a', '+bitexact', wo], capture_output=True, text=True)
            except FileNotFoundError:
                raise RuntimeError('ffmpeg not found (the sound files need it)')
            if r.returncode: raise RuntimeError('ffmpeg: ' + r.stderr)
            ogg = open(wo, 'rb').read()
            if len(ogg) <= budget: break
    pages = ogg_pages(ogg)
    nh = 0
    while nh < len(pages) and pages[nh][1] == 0: nh += 1
    hdr = b''.join(p[2] for p in pages[:nh])
    audio_pages = pages[nh:]
    audio = b''.join(p[2] for p in audio_pages)
    total = audio_pages[-1][1] if audio_pages else 0
    step = rate // 10                               # 0.1 s
    seek, base = [], audio_pages[0][0] if audio_pages else 0
    for k in range(max(1, math.ceil(total / step))):
        tgt = k * step
        off = next((p[0] - base for p in audio_pages if p[1] >= tgt), audio_pages[-1][0] - base)
        seek.append(off)
    xor = 0x40 + (hashlib.md5(data).digest()[0] & 0x3F)  # any non-zero byte works
    hx = bytes(c ^ xor for c in hdr)
    st = b''.join(struct.pack('>I', s) for s in seek)
    head = bytes([2, 0x20, xor, 0]) + struct.pack('>IHHfIIII', 0, 0, 0, 0.1, len(st), len(hx), 0, 0)
    return head + st + hx, audio, total, rate

def convert(d, head_oracle, quality=2, ffmpeg=None):
    global FFMPEG
    if ffmpeg: FFMPEG = ffmpeg
    s = parse(d)
    assert s['E'] == '<', 'not a PC scd'
    t1 = s['offs'][1]
    fixed = [(t1, t1 + 4 * s['n2'])]                                     # rewritten below
    if any(e['codec'] != 6 for e in s['entries']): fixed.append((s['hs'] + 0x1C, s['hs'] + 0x20))
    head, st = head_oracle.convert(s['head'], learned.sedb_header, fixed=fixed)
    head = bytearray(head)
    # u16 at table header + 0x1C: kept for Ogg entries (fanfare: same value on both platforms),
    # but differs for re-encoded entries with no visible relation (PC small even numbers, 360
    # other values in voices and 0 in sound-effect banks); written as 0 when re-encoding.
    if any(e['codec'] != 6 for e in s['entries']):
        struct.pack_into('>I', head, s['hs'] + 0x1C, 0)
    ents = []
    for e in s['entries']:
        aux = b''.join(aux_to_be(a) for a in e['aux'])
        if e['codec'] == 6:
            cx, data, rate, ch, loop = ogg_cx_to_be(e['cx']), e['data'], e['rate'], e['ch'], e['loop']
        elif e['codec'] == 12:
            cx, data, _, rate = msadpcm_to_ogg(e, quality)
            ch, loop = e['ch'], (0, 0)
            if e['loop'] != (0, 0):
                # loop points are byte positions in the stream; a re-encoded stream has other ones:
                # the whole stream loops (the usual case: a loop over all of it)
                loop = (0, len(data))
                LOOPED.append(e['loop'])
        else:
            raise NotImplementedError(f'SCD codec {e["codec"]}')
        hdr = struct.pack('>8I', len(data), ch, rate, 6, loop[0], loop[1], len(aux) + len(cx), len(e['aux']))
        ents.append(hdr + aux + cx + data)
    out = bytearray(head)
    offs = []
    for i, b in enumerate(ents):
        if i: out += bytes(align(len(out), 0x10) - len(out))
        offs.append(len(out))
        out += b
    out += bytes(align(len(out), 0x80) - len(out))
    t2 = s['offs'][1]
    for i, o in enumerate(offs): struct.pack_into('>I', out, t2 + 4 * i, o)
    struct.pack_into('>Q', out, 0x10, len(out))
    return bytes(out), st

def head_pairs(pairs):
    """(pc scd, 360 scd) -> (pc head, 360 head) with equal length (oracle input)."""
    out = []
    for p, x in pairs:
        a, b = parse(p)['head'], parse(x)['head']
        if len(a) == len(b): out.append((a, b))
    return out
