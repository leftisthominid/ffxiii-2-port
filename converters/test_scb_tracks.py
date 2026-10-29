"""Track parent IDs must not become the variable record-length flag."""
import struct
import unittest
from x2dlc import scb

class TrackHeaders(unittest.TestCase):
    def fixture(self):
        p=bytearray(96)
        p[48:54]=b'@CTRK\0';struct.pack_into('<H',p,54,3)
        struct.pack_into('<I',p,64,3)
        struct.pack_into('<hBB',p,68,-1,2,0)
        struct.pack_into('<hBBH',p,72,43,3,1,7)
        struct.pack_into('<hBB',p,80,44,4,0)
        return bytes(p)

    def test_track_parent_and_flag_have_independent_byte_order(self):
        p=self.fixture();learner=scb.Learner()
        # Reproduce the prior generic fallback choosing a whole-word swap.
        for i in (68,72,80):
            for key in scb._keys(p,i,'CTRK',48):learner.c[key]=[0,0,20,0,0]
        y=scb.convert(p,learner)
        self.assertEqual(scb.track_records(y,48,48,'>'),[(68,4),(72,8),(80,4)])
        self.assertEqual(struct.unpack_from('>hBB',y,72),(43,3,1))
        self.assertEqual(struct.unpack_from('>hBB',y,80),(44,4,0))
        self.assertEqual(struct.unpack_from('>I',y,64)[0],3)

    def test_truncated_track_extras_are_rejected(self):
        p=bytearray(self.fixture());p[75]=31
        self.assertIsNone(scb.track_records(p,48,48))

    def test_count_cannot_exceed_chunk(self):
        p=bytearray(self.fixture());struct.pack_into('<I',p,64,100)
        self.assertIsNone(scb.track_records(p,48,48))

if __name__=='__main__':unittest.main()
