"""A completed compare command must have compared files without conversion failures."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from x2dlc import archive
from x2dlc import __main__ as cli


class CompareTests(unittest.TestCase):
    def compare(self, steam_bytes, empty=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            userdata, work = root / 'userdata', root / 'work'
            work.mkdir()
            package = userdata / '0000000000000000/53510806/00000002/RealPackage'
            package.mkdir(parents=True)
            white, entries = archive.pack_archive([(1, 0, 'value.dat', b'reference')])
            if empty: white, entries = archive.pack_archive([])
            (package / 'white_p0000071img_u.x360.bin').write_bytes(white)
            (package / 'filelist_p0000071img_u.x360.bin').write_bytes(
                archive.encrypt(archive.build_plain(bytes(32), entries)))
            class Steam:
                files = {'value.dat': None}
                def read(self, key): return steam_bytes
            args = ['x2dlc', 'compare', '--work', str(work), '--userdata', str(userdata)]
            with patch.object(cli.sys, 'argv', args), patch.object(cli.learn, 'load', return_value={}), \
                 patch.object(cli, 'open_steam', return_value=Steam()), \
                 patch.object(cli.package, 'installed', return_value={'0000071': 'RealPackage'}), contextlib.redirect_stdout(io.StringIO()):
                status = cli.main()
            return status, (work / 'compare.txt').read_text()

    def test_conversion_failure_is_nonzero(self):
        status, report = self.compare(None)
        self.assertEqual(status, 1)
        self.assertIn('not converted 1', report)

    def test_zero_file_comparison_is_nonzero(self):
        status, _ = self.compare(None, empty=True)
        self.assertEqual(status, 1)

    def test_successful_comparison_is_zero(self):
        status, report = self.compare(b'reference')
        self.assertEqual(status, 0)
        self.assertIn('exact 1', report)


if __name__ == '__main__':
    unittest.main()
