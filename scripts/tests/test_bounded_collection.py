import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('bounded',ROOT/'scripts/XIII2_Collect_Diagnostics.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)

class BoundedCollection(unittest.TestCase):
 def run_collection(self,root):
  out=root/'results'
  collector.main(['--out',str(out),'--game-output',str(root/'game'),'--no-explorer'])
  files={}
  for path in (out/'Upload-these-zips').glob('*.zip'):
   self.assertLessEqual(path.stat().st_size,6<<20)
   with zipfile.ZipFile(path) as z:
    for name in z.namelist():files[name]=z.read(name)
  status=json.loads((out/'collection-status.json').read_text())
  for entry in status['files']:
   self.assertEqual(hashlib.sha256(files[entry['entry']]).hexdigest(),entry['sha256'])
  return files,status
 def test_recovers_current_logs_and_indexes_without_assets(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);game=root/'game'
   for name,data in {'logs/run.log':b'crash captured','logs/merge_update.log':b'duplicate identity','run/update_tu/filelist_patch_u.x360.bin':b'index','userdata/voice.txt':b'en','work/steamdlc/base_data.pkl':b'PRIVATE CACHE','logs/private-context.txt':b'PRIVATE','run/update_tu/white_patch_u.x360.bin':b'GAME ARCHIVE'}.items():
    path=game/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
   files,status=self.run_collection(root)
   self.assertEqual(files['logs/run.log'],b'crash captured')
   self.assertEqual(files['update-indexes/update_tu/filelist_patch_u.x360.bin'],b'index')
   self.assertFalse(any(b'PRIVATE' in b or b'GAME ARCHIVE' in b for b in files.values()))
   self.assertLess(status['raw_bytes'],1024)
 def test_log_tail_and_whole_binary_omissions_are_reported(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);p=root/'game/logs/run.log';p.parent.mkdir(parents=True);p.write_bytes(b'x'*(5<<20)+b'END')
   q=root/'game/run/update_tu/filelist_patch_u.x360.bin';q.parent.mkdir(parents=True);q.write_bytes(b'i'*(5<<20))
   files,status=self.run_collection(root)
   self.assertEqual(len(files['logs/run.log']),4<<20);self.assertTrue(files['logs/run.log'].endswith(b'END'))
   self.assertNotIn('update-indexes/update_tu/filelist_patch_u.x360.bin',files)
   self.assertEqual(len(status['omissions']),2)
 def test_budget_and_symlinks(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);logs=root/'game/logs';logs.mkdir(parents=True)
   (root/'private.txt').write_text('PRIVATE');(logs/'run-link.log').symlink_to(root/'private.txt')
   for i in range(6):(logs/('run%d.log'%i)).write_bytes(bytes([i])*(4<<20))
   with patch.object(collector,'MAX_TOTAL',6<<20):files,status=self.run_collection(root)
   self.assertLessEqual(status['raw_bytes'],6<<20)
   self.assertFalse(any(b'PRIVATE' in b for b in files.values()))
 def test_missing_inputs_still_produce_a_status(self):
  with tempfile.TemporaryDirectory() as tmp:
   files,status=self.run_collection(Path(tmp));self.assertEqual(files,{})
   self.assertTrue(status['notes']);self.assertEqual(status['parts'],0)

if __name__=='__main__':unittest.main()
