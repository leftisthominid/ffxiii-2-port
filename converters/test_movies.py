import struct
import unittest
import tempfile
import shutil
import wave
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from x2dlc import movies


def fixture():
    # Synthetic Bink container, with two audio packets and one video packet.
    header = bytearray(44)
    header[:4] = b'BIKi'
    audio = struct.pack('<I', 3) + b'abc' + struct.pack('<I', 2) + b'de'
    video = b'video-keyframe!'
    packet = audio + video
    packet += bytes(len(packet) % 2)
    start = 44 + 24 + 8
    data = header + bytes(24) + struct.pack('<II', start | 1, start + len(packet)) + packet
    struct.pack_into('<10I', data, 4, len(data) - 8, 1, len(packet), 1, 640, 360, 30, 1, 0, 2)
    return bytes(data), video


class MovieContainers(unittest.TestCase):
    def test_cached_decoder_recovers_missing_or_damaged_native_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);steam=root/'steam';steam.mkdir()
            (steam/'bink2w32.dll').write_bytes(b'user Steam library')
            windows=root/'windows'
            compiler=windows/'Microsoft.NET/Framework/v4.0.30319/csc.exe'
            compiler.parent.mkdir(parents=True);compiler.write_bytes(b'compiler')
            def compile(command,log):
                Path(next(str(p)[5:] for p in command if str(p).startswith('/out:'))).write_bytes(b'compiled helper')
            with patch.object(movies,'os',SimpleNamespace(name='nt',environ={'WINDIR':str(windows)})), \
                 patch.object(movies,'run',side_effect=compile) as compiler_run:
                exe,stamp=movies.helper(steam,root/'work')
                movies.helper(steam,root/'work');self.assertEqual(compiler_run.call_count,1)
                native=exe.with_name('bink2w32.dll');native.unlink()
                movies.helper(steam,root/'work');self.assertEqual(compiler_run.call_count,2)
                native.write_bytes(b'damaged')
                movies.helper(steam,root/'work');self.assertEqual(compiler_run.call_count,3)
                self.assertEqual(native.read_bytes(),b'user Steam library')

    def test_japanese_movie_audio_matches_code_and_rebuilds_damaged_cache(self):
        en,_=fixture();jp=bytearray(en);jp[-2]^=1;jp=bytes(jp)
        class Index:
            def __init__(self,key,data): self.files={key:('',0,0,0,key,71,32)};self.data=data
            def read(self,key): return self.data if key in self.files else None
        S=Index('movie_win/episode_us.bik',en)
        J=Index('movie_win/episode_jp.bik',jp)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def decode(data,exe,ffmpeg,output,name,log,audio_only=False):
                self.assertTrue(audio_only)
                (output/(name+'.wav')).write_bytes(b'Japanese audio')
            with patch.object(movies,'decode',side_effect=decode) as decoder:
                args=(S,J,'episode_us',en,movies.info(en),'decoder','stamp','ffmpeg',root,lambda *a:None)
                self.assertTrue(movies.prepare_japanese(*args))
                self.assertTrue(movies.prepare_japanese(*args))
                self.assertEqual(decoder.call_count,1)
                (root/'episode_us_jp.wav').write_bytes(b'damaged')
                self.assertTrue(movies.prepare_japanese(*args))
                self.assertEqual(decoder.call_count,2)
                J.data=None
                self.assertFalse(movies.prepare_japanese(*args))
                self.assertFalse((root/'episode_us_jp.wav').exists())

    def test_different_japanese_movie_timing_is_not_played_over_english_video(self):
        en,_=fixture();jp=bytearray(en);struct.pack_into('<I',jp,28,24);jp=bytes(jp)
        class Index:
            files={'movie_win/episode.bik':('',0,0,0,'',71,32)}
            def read(self,key):return jp
        with tempfile.TemporaryDirectory() as temp, patch.object(movies,'decode') as decoder:
            self.assertFalse(movies.prepare_japanese(Index(),Index(),'episode',en,movies.info(en),
                'decoder','stamp','ffmpeg',Path(temp),lambda *a:None))
            decoder.assert_not_called()

    @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg not on PATH')
    def test_audio_only_decoder_output_is_mixed_without_encoding_video(self):
        data,_=fixture();data=bytearray(data);struct.pack_into('<I',data,40,1);data=bytes(data)
        native_run=movies.run
        def run(command,log=print,stdout=None,stderr=None):
            if str(command[0])!='decoder':return native_run(command,log,stdout,stderr)
            self.assertEqual(command[-1],'--audio-only')
            folder=Path(command[2])
            (folder/'movie-info.bin').write_bytes(struct.pack('<10I',640,360,1,30,1,1,48000,16,1,4096))
            (folder/'audio-0.pcm').write_bytes(bytes(1600*2))
        with tempfile.TemporaryDirectory() as temp, patch.object(movies,'run',side_effect=run):
            root=Path(temp)
            movies.decode(data,'decoder',shutil.which('ffmpeg'),root,'episode_jp',log=lambda *a:None,audio_only=True)
            self.assertFalse((root/'episode_jp.mp4').exists())
            with wave.open(str(root/'episode_jp.wav')) as wav:
                self.assertEqual((wav.getnchannels(),wav.getframerate(),wav.getnframes()),(2,48000,1600))

    def test_invalid_decoder_metadata_keeps_existing_movie_outputs(self):
        data,_=fixture()
        variants=[b'',bytes(23),struct.pack('<6I',640,360,1,30,1,2),
                  struct.pack('<14I',640,360,1,30,1,2,48000,8,2,4096,48000,16,2,4096)]
        for description in variants:
            with self.subTest(metadata=description),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'movie.wav').write_bytes(b'previous audio')
                def run(command,*args,**kwargs): (Path(command[2])/'movie-info.bin').write_bytes(description)
                with patch.object(movies,'run',side_effect=run),self.assertRaises(ValueError):
                    movies.decode(data,'decoder','ffmpeg',root,'movie',audio_only=True)
                self.assertEqual((root/'movie.wav').read_bytes(),b'previous audio')

    def test_interrupted_movie_conversion_reaps_native_process(self):
        with patch.object(movies.subprocess,'Popen') as spawn,patch.object(movies.time,'sleep',side_effect=KeyboardInterrupt):
            process=spawn.return_value;process.poll.return_value=None
            with self.assertRaises(KeyboardInterrupt):movies.run(['decoder'])
            process.kill.assert_called_once();process.wait.assert_called_once()

    def test_audio_decoder_error_survives_temporary_directory_cleanup(self):
        data,_=fixture()
        def fail(command,log,stdout=None,stderr=None):
            stderr.write(b'Cannot open movie audio track');stderr.flush()
            raise RuntimeError('decoder exited 1')
        with tempfile.TemporaryDirectory() as temp,patch.object(movies,'run',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'Cannot open movie audio track'):
                movies.decode(data,'decoder','ffmpeg',Path(temp),'episode',audio_only=True)

    def test_movie_preparation_searches_past_unsupported_small_clips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template, _ = fixture()
            template=bytearray(template)
            struct.pack_into('<II',template,20,1280,720)
            template=bytes(template)
            steam = bytearray(template); steam[:4] = b'KB2j'
            class Index:
                def __init__(self, source, files): self.root=source; self.files=files
                def read(self, name):
                    if name.startswith('movie_win/'): return bytes(steam)
                    if name.endswith('bad00.bik'): return fixture()[0] # Valid small logo, unsuitable for HD video.
                    return template if name.endswith('valid.bik') else b'not a movie'
            S=Index(root/'steam', {})
            files={'movie/bad%02d.bik'%n: ('',0,n+1) for n in range(16)}
            files['movie/valid.bik']=('',0,len(template))
            X=Index(root/'disc', files)
            def decode(data, exe, ffmpeg, output, name, log):
                (output/(name+'.mp4')).write_bytes(b'converted video')
                (output/(name+'.wav')).write_bytes(b'converted audio')
                return movies.info(data)
            with patch.object(movies,'helper',return_value=('decoder','stamp')), \
                 patch.object(movies,'decode',side_effect=decode), \
                 patch.object(movies,'run') as verify, patch.object(movies,'NAMES',('episode',)):
                count=movies.prepare(S,X,root/'work',root/'output','ffmpeg',log=lambda *a:None)
            self.assertEqual(count,1)
            verify.assert_called_once()
            keyframe=(root/'work/movies/base-keyframe.bik').read_bytes()
            self.assertEqual(movies.info(keyframe)['frames'],1)
            self.assertEqual(movies.info(keyframe)['width'],1280)
            self.assertTrue((root/'output/index.txt').is_file())

    def test_keyframe_removes_every_audio_packet(self):
        data, video = fixture()
        _, extracted = movies.keyframe(data)
        self.assertEqual(extracted, video)

    def test_transport_preserves_complete_movie_timing(self):
        data, _ = fixture()
        spec = dict(frames=450, rate=5000000, divisor=166833)
        out = movies.transport(data, spec, pad=64)
        meta = movies.info(out)
        self.assertEqual((meta['frames'],meta['rate'],meta['divisor'],meta['tracks']), (450,5000000,166833,0))
        self.assertEqual((meta['width'],meta['height']), (640,360))
        offsets = struct.unpack_from('<451I', out, 44)
        original = movies.keyframe(data)[1]
        original += bytes(len(original) % 2)
        for i in range(450):
            self.assertEqual(offsets[i] & 1, 1)
            self.assertEqual(out[offsets[i] & ~1:offsets[i+1] & ~1], original)
        self.assertEqual(len(out), meta['bytes'])

    def test_rejects_truncated_or_non_keyframe_templates(self):
        data, _ = fixture()
        with self.assertRaises(ValueError): movies.keyframe(data[:-1])
        data = bytearray(data)
        at = 44 + 24
        struct.pack_into('<I', data, at, struct.unpack_from('<I',data,at)[0] & ~1)
        with self.assertRaises(ValueError): movies.keyframe(data)

    def test_rejects_audio_packet_outside_frame(self):
        data, _ = fixture()
        data = bytearray(data)
        struct.pack_into('<I',data,76,0xFFFFFFFF)
        with self.assertRaises(ValueError): movies.keyframe(data)


if __name__ == '__main__': unittest.main()
