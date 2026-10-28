import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from x2dlc import sound


class BadTableOracle:
    def convert(self, data, header, **kwargs):
        out = bytearray(data)
        out[0x30:0x4C] = bytes([0xCD]) * 28
        return bytes(out), {}


def scd(codec=6, cx=None, audio=b'OggS', lip=False):
    if cx is None: cx = bytes([2,32,0x40,0]) + bytes(28)
    d = bytearray(0x80)
    d[:8] = b'SEDBSSCF'
    struct.pack_into('<H',d,14,0x30)
    struct.pack_into('<4H5I',d,0x30,0,0,1,0,0,0x60,0,0,0)
    struct.pack_into('<I',d,0x60,0x80)
    d += struct.pack('<8I',len(audio),1,48000,codec,0,0,len(cx),0) + cx + audio
    d += bytes(-len(d) % 128)
    struct.pack_into('<Q',d,16,len(d))
    if lip:
        srd = b'SRD\x01' + struct.pack('<I',31) + bytes(10) + bytes([6,1,17,34,0x34,0x12,6,1,51,68,0x78,0x56,0])
        d += srd + b'add srd\0' + bytes(4) + struct.pack('<I',len(srd))
    return bytes(d)


class SoundContainers(unittest.TestCase):
    def test_known_counts_offsets_do_not_depend_on_oracle(self):
        output,_ = sound.convert(scd(),BadTableOracle())
        parsed = sound.parse(output)
        self.assertEqual(parsed['E'],'>')
        self.assertEqual(parsed['offs'],(0,0x60,0,0,0))
        self.assertEqual(parsed['n2'],1)
        self.assertEqual(parsed['entries'][0]['data'],b'OggS')

    def test_lip_sync_is_retained_after_the_declared_audio_body(self):
        output,_ = sound.convert(scd(lip=True),BadTableOracle())
        body=struct.unpack_from('>Q',output,16)[0]
        srd=output[body:-16]
        self.assertEqual(srd[:4],b'SRD\x01')
        self.assertEqual(struct.unpack_from('>I',srd,4)[0],31)
        self.assertEqual(srd[22:24],b'\x12\x34')
        self.assertEqual(srd[28:30],b'\x78\x56') # Final time is byte order independent in this format.
        self.assertEqual(output[-16:-8],b'add srd\0')
        self.assertEqual(struct.unpack_from('>I',output,len(output)-4)[0],31)

    def test_bad_lip_sync_trailer_is_reported(self):
        data=bytearray(scd(lip=True));data[-1]=255
        with self.assertRaises(ValueError): sound.convert(bytes(data),BadTableOracle())

    @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg not on PATH')
    def test_adpcm_reencoding_produces_decodable_vorbis(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);wav=root/'source.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.3',
                            '-ar','48000','-c:a','adpcm_ms',str(wav)],check=True)
            b=wav.read_bytes();chunks={};at=12
            while at+8<=len(b):
                n=struct.unpack_from('<I',b,at+4)[0];chunks[b[at:at+4]]=b[at+8:at+8+n];at+=8+n+(n&1)
            output,_=sound.convert(scd(12,chunks[b'fmt '],chunks[b'data']),BadTableOracle(),ffmpeg=shutil.which('ffmpeg'))
            ent=sound.parse(output)['entries'][0];cx=ent['cx']
            seek,headers=struct.unpack_from('>II',cx,16)
            ogg=bytes(c^cx[2] for c in cx[32+seek:32+seek+headers])+ent['data']
            path=root/'result.ogg';path.write_bytes(ogg)
            subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],check=True)
            pages=sound.ogg_pages(ogg)
            self.assertTrue(pages)
            self.assertTrue(all(struct.unpack_from('<I',p[2],14)[0]==0 for p in pages))
            self.assertGreater(pages[-1][1],0)


if __name__=='__main__': unittest.main()
