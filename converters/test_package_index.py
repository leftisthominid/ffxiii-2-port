"""Stream order must not affect native resource-ID lookup."""
import bisect
import tempfile
import unittest
from pathlib import Path
from x2dlc import archive, package

class PackageIndex(unittest.TestCase):
    def test_unsorted_stream_is_indexed_by_type_then_unsigned_code(self):
        files=[(0xffffffff,96,'zone/z0131/scr255.clb',b'last'),
               (33,160,'sound/a.scd',b'voice'),
               (33536,96,'zone/z0131/scr000.clb',b'entry'),
               (1,16,'gui/a.bin',b'gui'),
               (33546,96,'zone/z0131/scr010.clb',b'casino')]
        with tempfile.TemporaryDirectory() as temp:
            package.write(temp,'0000071','scr',bytes(32),iter(files))
            folder=Path(temp)
            _,rows=archive.read_files(folder/'filelist_p0000071scr_u.x360.bin',folder/'white_p0000071scr_u.x360.bin')
            keys=[(typ,code) for code,typ,_,_ in rows]
            for code,typ,path,payload in files:
                i=bisect.bisect_left(keys,(typ,code))
                self.assertEqual(rows[i],(code,typ,path,payload))
            _,entries=archive.read_list(folder/'filelist_p0000071scr_u.x360.bin')
            sectors={e[-1]:e[2] for e in entries}
            self.assertEqual([sectors[f[2]]for f in files],list(range(len(files))))

if __name__=='__main__': unittest.main()
