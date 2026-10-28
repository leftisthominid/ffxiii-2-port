"""Steam Bink 2 decoding and full-length movie overlays, using the user's DLL.

The guest still controls movie timing, buffers, seeks, and close. Its Bink 1
transport repeats a verified base-game keyframe; every displayed frame and all
movie audio come from the decoded Steam movie through the port's existing movie
overlay. No Xbox DLC movie or DLC reference data is read.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time

NAMES = ('tm_vazb_038_us', 'tm_vazb_013_us', 'em_vazb_030_us',
         'em_vazb_055_us', 'tm_tevb_050_us')
VERSION = 'steam-movies-1'


def info(data):
    if len(data) < 44 or data[:3] not in (b'BIK', b'KB2'):
        raise ValueError('not a Bink movie')
    size, frames, largest, again, width, height, rate, divisor, flags, tracks = struct.unpack_from('<10I', data, 4)
    if not frames or not width or not height or not rate or not divisor or tracks > 16:
        raise ValueError('invalid Bink header')
    if size + 8 != len(data): raise ValueError('truncated or oversized Bink container')
    return dict(frames=frames, width=width, height=height, rate=rate,
                divisor=divisor, flags=flags, tracks=tracks, bytes=len(data))


def keyframe(data):
    meta = info(data)
    if data[:3] != b'BIK' or data[3:4] not in (b'f', b'g', b'h', b'i'):
        raise ValueError('a supported base-game Bink 1 movie is required')
    table = 44 + 12 * meta['tracks']
    if table + 4 * (meta['frames'] + 1) > len(data): raise ValueError('truncated frame index')
    start, end = struct.unpack_from('<II', data, table)
    if not start & 1: raise ValueError('first frame is not a keyframe')
    start &= ~1
    end &= ~1
    if not table < start < end <= len(data): raise ValueError('invalid first frame offsets')
    for _ in range(meta['tracks']):
        if start + 4 > end: raise ValueError('truncated audio packet')
        length = struct.unpack_from('<I', data, start)[0]
        start += 4 + length
        if start > end: raise ValueError('audio packet extends past the frame')
    if start == end: raise ValueError('base movie has no video packet')
    return data[:44], data[start:end]


def transport(template, movie, pad=0):
    header, packet = keyframe(template)
    packet += bytes(len(packet) % 2)
    header = bytearray(header)
    frames = movie['frames']
    table_end = 44 + 4 * (frames + 1)
    total = table_end + frames * len(packet) + pad
    struct.pack_into('<4I', header, 4, total - 8, frames, len(packet), frames)
    struct.pack_into('<II', header, 28, movie['rate'], movie['divisor'])
    struct.pack_into('<I', header, 40, 0)  # The real audio is the external Steam WAV.
    offsets = [table_end + i * len(packet) for i in range(frames + 1)]
    index = b''.join(struct.pack('<I', p | (i < frames)) for i, p in enumerate(offsets))
    return bytes(header) + index + packet * frames + bytes(pad)


def run(command, log=print, stdout=None, stderr=None):
    process = subprocess.Popen([str(a) for a in command], stdout=stdout, stderr=stderr)
    try:
        last = time.monotonic()
        while process.poll() is None:
            if time.monotonic() - last >= 15:
                log('  movie conversion still running: ' + Path(command[0]).name)
                last = time.monotonic()
            time.sleep(0.2)
        if process.returncode: raise RuntimeError(f'{Path(command[0]).name} exited {process.returncode}')
    finally:
        if process.poll() is None: process.kill(); process.wait()


def helper(steam, work, log=print):
    if os.name != 'nt': raise RuntimeError('Steam movie decoding requires Windows and the Steam 32-bit Bink DLL')
    dlls = sorted(Path(steam).rglob('bink2w32.dll'))
    if not dlls: raise FileNotFoundError('bink2w32.dll was not found in the Steam installation')
    dll = dlls[0]
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    if not compiler.is_file(): raise FileNotFoundError('the Windows .NET Framework C# compiler is unavailable')
    source = Path(__file__).resolve().parents[1] / 'x2_bink_decode.cs'
    cache = Path(work) / 'movie-helper'
    cache.mkdir(parents=True, exist_ok=True)
    native = dll.read_bytes()
    stamp = hashlib.sha256(source.read_bytes() + native).hexdigest()
    exe = cache / 'X2BinkDecode.exe'
    mark = cache / 'version.txt'
    local_dll = cache / 'bink2w32.dll'
    if not exe.is_file() or not mark.is_file() or mark.read_text() != stamp or \
            not local_dll.is_file() or local_dll.read_bytes() != native:
        local_dll.write_bytes(native)
        run([compiler, '/nologo', '/optimize+', '/platform:x86', '/target:exe', '/out:' + str(exe), source], log)
        mark.write_text(stamp)
    return exe, stamp


def decode(data, exe, ffmpeg, output, name, log=print, audio_only=False):
    meta = info(data)
    width, height = meta['width'], meta['height']
    visible = {736: 720, 1088: 1080}.get(height, height)
    duration = meta['frames'] * meta['divisor'] / meta['rate']
    with tempfile.TemporaryDirectory(prefix='decode-', dir=output) as temp:
        temp = Path(temp)
        source = temp / 'input.bik'
        source.write_bytes(data)
        video = temp / 'video.mp4'
        with (temp / 'decoder.log').open('wb') as de, (temp / 'ffmpeg.log').open('wb') as fe:
            if audio_only:
                try:
                    run([exe, source, temp, '--audio-only'], log, stdout=subprocess.DEVNULL, stderr=de)
                except RuntimeError as ex:
                    raise RuntimeError('movie audio decode failed: ' +
                        (temp / 'decoder.log').read_text(errors='replace')[-3000:]) from ex
            else:
                decoder = subprocess.Popen([str(exe), str(source), str(temp)], stdout=subprocess.PIPE, stderr=de)
                encoder = None
                try:
                    encoder = subprocess.Popen([str(ffmpeg), '-y', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'bgra',
                        '-video_size', f'{width}x{height}', '-framerate', f'{meta["rate"]}/{meta["divisor"]}',
                        '-i', 'pipe:0', '-vf', f'crop={width}:{visible}:0:0', '-frames:v', str(meta['frames']),
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-an', str(video)],
                        stdin=decoder.stdout, stdout=subprocess.DEVNULL, stderr=fe)
                    decoder.stdout.close()
                    last = time.monotonic()
                    while decoder.poll() is None and encoder.poll() is None:
                        if time.monotonic() - last >= 15:
                            log('  decoding Steam movie: ' + name)
                            last = time.monotonic()
                        time.sleep(0.2)
                    if encoder.poll() not in (None, 0) and decoder.poll() is None: decoder.terminate()
                    dr = decoder.wait()
                    er = encoder.wait()
                    if dr or er:
                        raise RuntimeError(f'movie decode failed ({dr}, {er}): ' +
                            (temp / 'decoder.log').read_text(errors='replace')[-3000:] +
                            (temp / 'ffmpeg.log').read_text(errors='replace')[-3000:])
                finally:
                    if decoder.poll() is None: decoder.kill(); decoder.wait()
                    if decoder.stdout and not decoder.stdout.closed: decoder.stdout.close()
                    if encoder is not None and encoder.poll() is None: encoder.kill(); encoder.wait()
        description = (temp / 'movie-info.bin').read_bytes()
        if len(description) < 24 or len(description) % 4:
            raise ValueError('truncated decoder metadata')
        fields = struct.unpack('<' + 'I' * (len(description) // 4), description)
        if tuple(fields[:5]) != (width, height, meta['frames'], meta['rate'], meta['divisor']):
            raise ValueError('decoder metadata does not match the Steam movie')
        if fields[5] != meta['tracks'] or len(fields) != 6 + 4 * fields[5]:
            raise ValueError('decoder audio track count does not match the Steam movie')
        tracks = [fields[6 + 4 * i:10 + 4 * i] for i in range(fields[5])]
        if any(bits != 16 or not 1 <= channels <= 8 or not 8000 <= rate <= 192000
               for rate, bits, channels, maximum in tracks):
            raise ValueError('unsupported decoded movie audio format')
        wav = temp / 'audio.wav'
        if tracks:
            command = [str(ffmpeg), '-y', '-v', 'error']
            for i, (rate, bits, channels, maximum) in enumerate(tracks):
                command += ['-f', 's16le', '-ar', str(rate), '-ac', str(channels), '-i', str(temp / f'audio-{i}.pcm')]
            if [t[2] for t in tracks] == [2, 1, 1, 2]:
                filters = 'join=inputs=4:channel_layout=5.1:map=0.0-FL|0.1-FR|1.0-FC|2.0-LFE|3.0-BL|3.1-BR, aformat=channel_layouts=stereo,apad'
            else:
                filters = f'amix=inputs={len(tracks)}:duration=longest,aformat=channel_layouts=stereo,apad'
            command += ['-filter_complex', filters, '-t', f'{duration:.9f}', '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', str(wav)]
            run(command, log)
        if not audio_only:
            shutil.copyfile(video, output / (name + '.mp4.new'))
            os.replace(output / (name + '.mp4.new'), output / (name + '.mp4'))
        if wav.is_file():
            shutil.copyfile(wav, output / (name + '.wav.new'))
            os.replace(output / (name + '.wav.new'), output / (name + '.wav'))
        else:
            (output / (name + '.wav')).unlink(missing_ok=True)
    return meta


def prepare(S, X, work, output, ffmpeg, log=print, japanese=None):
    output = Path(output)
    for source in (S.root, X.root):
        for destination in (work, output):
            if Path(destination).resolve().is_relative_to(Path(source).resolve()):
                raise ValueError('movie output/cache must be outside the source installation')
    output.mkdir(parents=True, exist_ok=True)
    placeholders = Path(work) / 'movies/placeholders'
    placeholders.mkdir(parents=True, exist_ok=True)
    exe, helper_stamp = helper(S.root, work, log)
    choices = sorted((e[2], p) for p, e in X.files.items() if p.startswith('movie/') and p.endswith('.bik') and e[2])
    template = None
    log('Selecting a base-game movie keyframe for the guest timing/buffer transport')
    for number, (_, path) in enumerate(choices):
        # Normally the smallest sixteen clips provide a compact keyframe.
        # Keep searching if those happen to be unsupported logos/credit clips.
        if number >= 16 and template is not None: break
        blob = X.read(path)
        if blob:
            try:
                dimensions = info(blob)
                # The overlay renders into the guest's movie-sized surface.
                # A small logo would also downscale every HD Steam frame.
                if (dimensions['width'], dimensions['height']) not in ((1280,720),(1280,736)): continue
                keyframe(blob)
                if template is None or len(keyframe(blob)[1]) < len(keyframe(template)[1]): template = blob
            except ValueError: pass
    if template is None: raise RuntimeError('no usable base-game Bink 1 keyframe was found')
    (Path(work) / 'movies/base-keyframe.bik').write_bytes(transport(template,
        dict(frames=1, rate=info(template)['rate'], divisor=info(template)['divisor'])))
    used_sizes = {e[2] for p, e in X.files.items() if p.startswith('movie/')}
    index = []
    for name in NAMES:
        data = S.read('movie_win/' + name + '.bik')
        if not data: raise FileNotFoundError('Steam movie not found: ' + name)
        meta = info(data)
        signature = hashlib.sha256(Path(__file__).read_bytes() + VERSION.encode() + helper_stamp.encode() +
                                   data + template[:44] + keyframe(template)[1]).hexdigest()
        mark = output / (name + '.json')
        try: cached = json.loads(mark.read_text()) if mark.is_file() else {}
        except (ValueError, OSError): cached = {}
        target = placeholders / (name + '.bik')
        reuse = cached.get('signature') == signature and target.is_file() and (output / (name + '.mp4')).is_file()
        if meta['tracks']: reuse = reuse and (output / (name + '.wav')).is_file()
        if reuse:
            files = [target, output / (name + '.mp4')]
            if meta['tracks']: files.append(output / (name + '.wav'))
            reuse = all(cached.get('files', {}).get(p.name) == hashlib.sha256(p.read_bytes()).hexdigest() for p in files)
        if not reuse:
            log('Decoding full Steam movie and all audio tracks: ' + name)
            decode(data, exe, ffmpeg, output, name, log)
            pad = 0
            container = transport(template, meta, pad)
            while any(abs(len(container) - size) <= 32 for size in used_sizes):
                pad += 64
                container = transport(template, meta, pad)
            check = output / (name + '.transport-check.bik')
            try:
                check.write_bytes(container)
                # Decode its first keyframe using the user's Bink implementation
                # before allowing this transport to be installed in a package.
                run([exe, check, '--verify'], log, stdout=subprocess.DEVNULL)
                target.write_bytes(container)
            finally: check.unlink(missing_ok=True)
            temporary = mark.with_suffix('.json.new')
            files = [target, output / (name + '.mp4')]
            if meta['tracks']: files.append(output / (name + '.wav'))
            fingerprints = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
            temporary.write_text(json.dumps(dict(signature=signature, metadata=meta, transport_bytes=len(container), files=fingerprints), indent=2))
            os.replace(temporary, mark)
        prepare_japanese(S, japanese, name, data, meta, exe, helper_stamp, ffmpeg, output, log)
        size = target.stat().st_size
        used_sizes.add(size)
        index.append(f'{name} {size}\n')
        log('  Steam movie ready: ' + name)
    (output / 'index.txt.new').write_text(''.join(index), encoding='ascii')
    os.replace(output / 'index.txt.new', output / 'index.txt')
    return len(index)


def prepare_japanese(S, J, name, english, metadata, exe, helper_stamp, ffmpeg, output, log=print):
    """Optional Japanese movie audio, matched by Steam path or code/type.

    The transport and displayed English video keep their timing. A Japanese
    variant must have the same frame count and rate before its audio is used.
    """
    target=output/(name+'_jp.wav')
    mark=output/(name+'_jp.json')
    data=None
    if J is not None:
        key='movie_win/'+name+'.bik'
        if key not in J.files and key in S.files:
            source=S.files[key]
            key=next((p for p,e in J.files.items() if p.startswith('movie_win/') and e[5:7]==source[5:7]),None)
        if key is not None: data=J.read(key)
    reason=None
    if not data: reason='not present in the Japanese Steam archives'
    elif data==english: reason='identical to the English movie'
    else:
        jp=info(data)
        if jp['frames']!=metadata['frames'] or jp['rate']*metadata['divisor']!=metadata['rate']*jp['divisor']:
            reason='Japanese movie timing differs'
        elif not jp['tracks']: reason='Japanese movie has no audio tracks'
    if reason:
        target.unlink(missing_ok=True);mark.unlink(missing_ok=True)
        log('  Steam movie '+name+': '+reason+'; shared movie audio is used')
        return False
    signature=hashlib.sha256(Path(__file__).read_bytes()+helper_stamp.encode()+data).hexdigest()
    try: cached=json.loads(mark.read_text()) if mark.is_file() else {}
    except (ValueError,OSError): cached={}
    reuse=target.is_file() and cached.get('signature')==signature and cached.get('sha256')==hashlib.sha256(target.read_bytes()).hexdigest()
    if not reuse:
        log('Decoding Japanese Steam movie audio: '+name)
        decode(data,exe,ffmpeg,output,name+'_jp',log,audio_only=True)
        temporary=mark.with_suffix('.json.new')
        temporary.write_text(json.dumps(dict(signature=signature,sha256=hashlib.sha256(target.read_bytes()).hexdigest()),indent=2))
        os.replace(temporary,mark)
    return True
