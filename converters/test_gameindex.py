import tempfile
import unittest
from pathlib import Path
from x2dlc import archive, gameindex


def pack(folder, listname, whitename, files):
    folder.mkdir(parents=True,exist_ok=True)
    white,entries=archive.pack_archive(files,compress=False)
    listing=archive.encrypt(archive.build_plain(bytes(32),entries))
    (folder/listname).write_bytes(listing)
    (folder/whitename).write_bytes(white)
    return listing


class LanguageArchives(unittest.TestCase):
    def test_japanese_patch_and_zone_media_are_separate_from_english(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);alba=root/'alba_data';sys=alba/'sys'
            movie='movie_win/episode_us.bik';zone_movie='movie_win/zone_us.bik'
            zone_data=b'Japanese zone movie'+bytes(range(256))*16
            zone_list=pack(alba/'zone','zone-list.bin','white_z0070c_img.win32.bin',[(72,32,zone_movie,zone_data)])
            pack(sys,'filelistc.win32.bin','white_imgc.win32.bin',[
                (71,32,movie,b'Japanese base'),(99,32,'zone/filelist_z0070u.win32.bin',zone_list)])
            pack(sys,'filelistu.win32.bin','white_imgu.win32.bin',[(71,32,movie,b'English base')])
            pack(alba/'udp','filelist_patch_c.win32.bin','white_patch_c.win32.bin',[(71,32,movie,b'Japanese update')])
            before={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            J=gameindex.GameIndex(str(root),'win32',language_set='c')
            E=gameindex.GameIndex(str(root),'win32')
            try:
                self.assertEqual(J.read(movie),b'Japanese update')
                self.assertEqual(J.read(zone_movie),zone_data)
                self.assertEqual(E.read(movie),b'English base')
                self.assertIsNone(E.read(zone_movie))
            finally:J.close();E.close()
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
