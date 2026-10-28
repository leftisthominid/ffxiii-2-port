"""Motion format regressions; fixtures are synthetic, with no game bytes."""
import io
import pickle
import struct
import tempfile
import unittest
import subprocess
import os
import sys
from pathlib import Path
from unittest.mock import patch

from x2dlc import convert, learn, mcb, wpd, listconv
from x2dlc.__main__ import resource_diffs


def resource(name, length=48):
    data = bytearray(48 + length)
    data[:8] = b'SEDBACB\0'
    data[48:52] = b'@RES'
    struct.pack_into('<HH', data, 52, length // 8, 0)
    data[56:56 + len(name)] = name
    return data


class MotionTests(unittest.TestCase):
    def test_steam_movie_is_reported_as_unsupported_not_absent(self):
        class Steam:
            files = {'movie_win/tm_tevb_050_us.bik': None}
            def read(self, key): return None
        lc = listconv.ListConverter(None, Steam(), [])
        with self.assertRaisesRegex(NotImplementedError, 'movie_win/tm_tevb_050_us.bik'):
            lc.one('movie/tm_tevb_050_us.bik')

    def test_packed_halfword_at_byte_one_is_supported(self):
        self.assertIn('middle', mcb.fits(b'\x01\x0f\0\0', b'\x01\0\x0f\0'))

    def test_numeric_and_inline_name_command_variants_learn_separately(self):
        def command(selector, value):
            data=bytearray(48+24+48)
            data[:8]=b'SEDBMCB\0';data[48:52]=b'@BLK'
            struct.pack_into('<H',data,52,9)
            struct.pack_into('<H',data,72,48)
            struct.pack_into('<HB',data,80,3,160)
            struct.pack_into('<I',data,88,selector)
            data[112:116]=value
            return bytes(data)
        learner=mcb.Learner()
        learner.add(command(0,b'\x01\x07\0\0'),command(0,b'\x01\0\x07\0'))
        learner.add(command(1,b'name'),command(1,b'name'))
        numeric=bytearray(command(0,b'\x01\x0b\0\0'))
        numeric[89]=1 # Adjacent flag byte is independent of the variant selector.
        numeric=bytes(numeric)
        output=learner.apply(numeric,numeric,only_sure=True)
        self.assertEqual(output[112:116],b'\x01\0\x0b\0')
        text=command(1,b'node')
        self.assertEqual(learner.apply(text,text,only_sure=True)[112:116],b'node')

    def test_oracle_sketch_is_stable_across_worker_hash_seeds(self):
        script = "from x2dlc.learned import sketch; print(sorted(sketch(bytes(range(256))*4, 16)))"
        values = []
        for seed in ('1', '2'):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            values.append(subprocess.check_output([sys.executable, '-c', script], env=env))
        self.assertEqual(*values)

    def test_block_length_uses_eight_byte_units(self):
        data = bytearray(48 + 88)
        data[:8] = b'SEDBMCB\0'
        data[48:52] = b'@BLK'
        struct.pack_into('<H', data, 52, 11)
        for i in range(4):
            at = 48 + 24 + 16 * i
            struct.pack_into('<H', data, at, 16)
            struct.pack_into('<HB', data, at + 8, 3, 160)
        keys = mcb.places(bytes(data))[48 + 24 + 3 * 16 + 12]
        self.assertTrue(any(k[:3] == ('@BLK', 'rec', (3, 160)) for k in keys))

    def test_actor_header_keeps_id_bytes(self):
        data = bytearray(48 + 24)
        data[:8] = b'SEDBMCB\0'
        data[48:52] = b'@ACT'
        struct.pack_into('<H', data, 52, 3)
        data[56:60] = b'\x10\0\x01\x02'
        out = mcb.structural(bytes(data), bytes(data))
        self.assertEqual(out[56:60], b'\0\x10\x01\x02')

    def test_string_offsets_start_at_resource_plus_24(self):
        data = resource(b'String')
        data[72:76] = struct.pack('<HH', 4, 9)
        data[76:87] = b'first\0next\0'
        out = mcb.structural(bytes(data), bytes(len(data)))
        self.assertEqual(out[72:76], struct.pack('>HH', 4, 9))
        self.assertEqual(out[76:], data[76:])

    def test_track_masks_expand_including_zero_id(self):
        for ident, mask in ((0, 0xFF), (7, 0x7FF)):
            data = resource(b'CTrkPro')
            struct.pack_into('<HH', data, 76, ident, mask)
            out = mcb.structural(bytes(data), bytes(data))
            self.assertEqual(out[76:84], struct.pack('>HHHH', ident, 0, mask, 0))

    def test_track_counts_are_not_expanded_as_masks(self):
        data = resource(b'CTrkPro')
        struct.pack_into('<HH', data, 76, 6, 1)
        self.assertEqual(mcb.structural(bytes(data), bytes(data))[76:84], data[76:84])

    def test_resource_type_codes_are_not_treated_as_names(self):
        data = resource(b'RIDTBL', 64)
        data[72:76] = b'RIDI'
        struct.pack_into('<HHIH', data, 76, 0, 16, 24, 2)
        data[88:96] = b'fcssfcss'
        out = convert.cut_member_fix(b'acb', bytes(data), bytes(data))
        self.assertEqual(out[88:96], b'sscfsscf')

    def test_printable_record_payload_is_still_binary(self):
        data = bytearray(48 + 48)
        data[:8] = b'SEDBACB\0'
        data[48:52] = b'@BLK'
        struct.pack_into('<H', data, 52, 6)
        struct.pack_into('<H', data, 72, 24)
        data[84:92] = b'ABCDE\0\0\0'
        converted = bytearray(data)
        converted[84:92] = b'DCBA\0\0\0E'
        out = convert.cut_member_fix(b'acb', bytes(data), bytes(converted))
        self.assertEqual(out[84:92], converted[84:92])

    def test_bundle_report_uses_member_names_and_types(self):
        a = wpd.write([(b'walk', b'mcb', b'abcd')])
        b = wpd.write([(b'walk', b'mcb', b'abce')])
        self.assertEqual(resource_diffs(a, b), ['    MEMBER walk.mcb: 1 of 4 bytes differ'])

    def test_motion_sampler_covers_monsters_and_characters(self):
        keys = [f'mot/mon/m{i}/s1.white.bin' for i in range(500)]
        keys += [f'mot/pc/c{i}/s1.white.bin' for i in range(80)]
        keys += ['event/example.white.bin']
        selected = learn.motion_paths(keys)
        self.assertEqual(selected, learn.motion_paths(reversed(keys)))
        self.assertEqual(len(selected), len(set(selected)))
        self.assertTrue(any(k.startswith('mot/pc/') for k in selected))
        self.assertNotIn('event/example.white.bin', selected)

    def test_motion_collection_works_without_shared_dlc_bundles(self):
        pc = wpd.write([(b'walk', b'mcb', b'pc-motion'), (b'attack', b'acb', b'pc-action')])
        xbox = wpd.write([(b'walk', b'mcb', b'xb-motion'), (b'attack', b'acb', b'xb-action')])
        class Index:
            def __init__(self, root, platform, **kwargs):
                self.platform = platform
                path = 'mot/mon/example/s1.white.bin'
                self.files = {path: ('base-or-steam.bin', 0, 1, 1, path, 1, 32)}
                scene = 'scene/lay/example/VLeafInstance.vinsbin'
                self.files[scene] = ('base-or-steam.bin',0,8,8,scene,2,32)
                self.sources = []
            def read(self, key):
                if key.endswith('.vinsbin'): return b'SEDBpc00' if self.platform == 'win32' else b'SEDBxb00'
                return pc if self.platform == 'win32' else xbox
            def close(self): pass
        with tempfile.TemporaryDirectory() as work:
            with patch('x2dlc.gameindex.GameIndex', Index):
                learn.dlc_known('disc', 'title-update', 'steam', work, log=lambda _: None)
            with open(Path(work) / 'dlc_known.pkl', 'rb') as stream:
                data = pickle.load(stream)
            self.assertEqual(data['pairs']['mcb'], [(b'pc-motion', b'xb-motion')])
            self.assertEqual(data['pairs']['acb'], [(b'pc-action', b'xb-action')])
            self.assertEqual(data['pairs']['vinsbin'],[(b'SEDBpc00',b'SEDBxb00')])


if __name__ == '__main__':
    unittest.main()
