"""Voice override lists use the game's unencrypted, headerless form."""
import struct
import unittest
from x2dlc import archive

class HeaderlessLists(unittest.TestCase):
    def test_voice_builder_list_and_encrypted_media_list_have_same_entries(self):
        entries=[(123,16,4,100,50,'gui/scene/s00131.x360.xgr'),
                 (456,160,5,64,64,'sound/pack/usa/voice.x360.scd')]
        plain=archive.build_plain(bytes(32),entries)
        length=struct.unpack_from('>I',plain,16)[0]
        headerless=plain[32:32+length]
        self.assertEqual(archive.read_list(headerless),(bytes(32),entries))
        self.assertEqual(archive.read_list(archive.encrypt(plain))[1],entries)

    def test_encrypted_checksum_failure_is_not_accepted_as_plain(self):
        image=bytearray(archive.encrypt(archive.build_plain(bytes(32),[(1,16,0,3,3,'a.bin')])))
        image[40]^=1
        with self.assertRaises(ValueError):archive.read_list(image)

if __name__=='__main__':unittest.main()
