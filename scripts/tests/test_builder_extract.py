"""Real extraction/icon-format checks with a generated, non-game XDVDFS fixture."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

class Extract360(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = os.environ.get('XIII2_EXTRACT_TOOL')
        if not cls.tool:
            raise unittest.SkipTest('Set XIII2_EXTRACT_TOOL to the compiled converter')

    def test_extracts_disc_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            disc = bytearray(38 * 2048)
            disc[32*2048:32*2048+20] = b'MICROSOFT*XBOX*MEDIA'
            struct.pack_into('<II', disc, 32*2048+20, 34, 2048)
            name, data = b'default.xex', b'synthetic fixture, not game code'
            struct.pack_into('<HHIIBB', disc, 34*2048, 0, 0, 36, len(data), 0, len(name))
            disc[34*2048+14:34*2048+14+len(name)] = name
            disc[36*2048:36*2048+len(data)] = data
            source = root/'game.iso'
            source.write_bytes(disc)
            subprocess.run([self.tool, str(source), str(root/'out')], check=True, capture_output=True)
            self.assertEqual((root/'out/default.xex').read_bytes(), data)
            self.assertEqual(source.read_bytes(), disc)

    def test_rejects_non_disc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'bad.iso'; source.write_bytes(b'not a disc')
            result = subprocess.run([self.tool, str(source), str(root/'out')], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root/'out').exists())

if __name__ == '__main__':
    unittest.main()
