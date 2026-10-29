import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/XIII2_Collect_CompleteDLC.py'
spec = importlib.util.spec_from_file_location('complete_collector', SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CompleteCollection(unittest.TestCase):
    def test_collection_preserves_failure_logs_without_inventory_or_steam(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); game = root/'game'; session = root/'session'
            (game/'logs').mkdir(parents=True); session.mkdir()
            (game/'logs/build.log').write_text('compiler failure fixture')
            history = game/'logs/run-history/earlier-run'
            history.mkdir(parents=True)
            (history/'run.log').write_text('earlier run retained')
            (session/'failure.txt').write_text('installation failed fixture')
            (session/'private-context.txt').write_text('PRIVATE')
            result = subprocess.run([sys.executable, str(SCRIPT), '--source', str(ROOT), '--out', str(session),
                                     '--session', str(session), '--game-output', str(game), '--steam', str(root/'missing'),
                                     '--no-explorer'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            upload = session/'Upload-these-zips'
            with zipfile.ZipFile(upload/'XIII-2_DLC_Complete_Index.zip') as z:
                manifest = json.loads(z.read('manifest.json'))
            chunks = []
            for part in manifest:
                with zipfile.ZipFile(upload/part['zip']) as z: chunk = z.read(part['entry'])
                self.assertEqual(hashlib.sha256(chunk).hexdigest(), part['sha256'])
                chunks.append(chunk)
            with tarfile.open(fileobj=io.BytesIO(b''.join(chunks))) as t:
                self.assertEqual(t.extractfile('logs/build.log').read(), b'compiler failure fixture')
                self.assertEqual(t.extractfile('logs/run-history/earlier-run/run.log').read(), b'earlier run retained')
                self.assertEqual(t.extractfile('session/failure.txt').read(), b'installation failed fixture')
                self.assertFalse(any('private-context' in n for n in t.getnames()))
            status = json.loads((session/'collection-status.json').read_text())
            self.assertTrue(status['fatal'])
            self.assertTrue(any('inventory unavailable' in n for n in status['notes']))

    def test_windows_opens_the_upload_folder_without_a_shell(self):
        with patch.object(collector.sys, 'platform', 'win32'), patch.object(collector.os, 'startfile', create=True) as start:
            collector.open_upload_folder('C:/test/Upload-these-zips')
            start.assert_called_once_with('C:/test/Upload-these-zips')

    def test_only_target_definitions_and_bounded_generated_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'generated.cpp').write_bytes(
                b'// sub_829E8E88 is merely mentioned here\n'
                b'DEFINE_REX_FUNC(sub_828F0CD0) {\nreturn;\n}\n'
                b'DEFINE_REX_FUNC(sub_12345678) {\nprivate_example();\n}\n')
            (root/'private.txt').write_text('personal context, never collect')
            emitted, notes = {}, []
            found = collector.collect_generated(root, emitted.__setitem__, notes.append)
            self.assertEqual(found, {'sub_828F0CD0'})
            self.assertEqual(list(emitted), ['generated-evidence/sub_828F0CD0.cpp'])
            self.assertNotIn(b'private_example', next(iter(emitted.values())))
            self.assertTrue(any('sub_829E8E88' in note for note in notes))
            emitted.clear()
            collector.collect_generated(root, emitted.__setitem__, notes.append, limit=8)
            self.assertFalse(emitted)
            self.assertTrue(any('exceeds evidence limit' in note for note in notes))

    def test_streamed_collection_hashes_deduplication_and_private_file_exclusion(self):
        sys.path.insert(0, str(ROOT/'converters'))
        from x2dlc import archive
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); game = root/'game'; out = root/'collection'
            (root/'steam/alba_data').mkdir(parents=True)
            work = game/'work/steamdlc'; work.mkdir(parents=True)
            (work/'steam_manifest.json').write_text('{"packs": {}}')
            data = os.urandom(7 << 20)
            white, entries = archive.pack_archive([(1, 16, 'vfx/example.trb', data)])
            filelist = archive.encrypt(archive.build_plain(bytes(32), entries))
            for folder in (game/'userdata/0000000000000000/53510806/00000002/XIII2SteamDLC0000071',
                           game/'run/jpvoice_dlc/0000071'):
                folder.mkdir(parents=True)
                (folder/'filelist_p0000071img_u.x360.bin').write_bytes(filelist)
                (folder/'white_p0000071img_u.x360.bin').write_bytes(white)
                (folder/'identifier').write_bytes(b'0000071\0\0')
                (folder/'private-context.txt').write_text('DO NOT INCLUDE PRIVATE CONTEXT')
            (game/'userdata/voice.txt').write_text('English')
            (game/'userdata/private-context.txt').write_text('DO NOT INCLUDE PRIVATE CONTEXT')
            (work/'private-context.txt').write_text('DO NOT INCLUDE PRIVATE CONTEXT')
            generated = game/'project/generated/default'; generated.mkdir(parents=True)
            targets = ('828F0CD0','829E8E88','829C51E0','82B04830','82B04E48')
            (generated/'functions.cpp').write_text(''.join('DEFINE_REX_FUNC(sub_'+a+') {\nreturn;\n}\n' for a in targets))
            result = subprocess.run([sys.executable, str(SCRIPT), '--source', str(ROOT), '--out', str(out),
                                     '--game-output', str(game), '--steam', str(root/'steam'), '--base-game', str(root/'base')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            upload = out/'Upload-these-zips'
            with zipfile.ZipFile(upload/'XIII-2_DLC_Complete_Index.zip') as z:
                manifest = json.loads(z.read('manifest.json'))
            self.assertGreaterEqual(len(manifest), 2)
            chunks = []; offset = 0
            for part in manifest:
                with zipfile.ZipFile(upload/part['zip']) as z: chunk = z.read(part['entry'])
                self.assertEqual(part['offset'], offset)
                self.assertLessEqual(len(chunk), 6 << 20)
                self.assertEqual(part['size'], len(chunk))
                self.assertEqual(part['sha256'], hashlib.sha256(chunk).hexdigest())
                chunks.append(chunk); offset += len(chunk)
            with tarfile.open(fileobj=io.BytesIO(b''.join(chunks)), mode='r:gz') as t:
                names = t.getnames()
                self.assertFalse(any('private' in n for n in names))
                self.assertEqual(sum(n.startswith('objects/') for n in names), 1)
                records = json.load(t.extractfile('assets.json'))
                self.assertEqual({a['origin'] for a in records}, {'installed','Japanese-active'})
                self.assertEqual(t.extractfile('objects/'+hashlib.sha256(data).hexdigest()+'.bin').read(), data)
                self.assertEqual(sum(n.startswith('generated-evidence/') for n in names), 5)
            self.assertEqual((game/'userdata/voice.txt').read_text(), 'English')
            self.assertEqual((game/'userdata/private-context.txt').read_text(), 'DO NOT INCLUDE PRIVATE CONTEXT')
            self.assertIsNone(json.loads((out/'collection-status.json').read_text())['fatal'])


if __name__ == '__main__':
    unittest.main()
