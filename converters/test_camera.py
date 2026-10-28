"""Synthetic CCB container tests; no game data is embedded."""
import struct
import unittest
from x2dlc import ccb


def camera(count):
    params = 0xA0
    indices = params + 16 + 80 * count
    data = bytearray(indices + 32)
    data[:8] = b'SEDBCCB\0'
    struct.pack_into('<I', data, 0x10, len(data))
    for at, tag in ((0x30, b'@FCHD'), (0x60, b'@FCCO'), (0x80, b'@FCMT'),
                    (params, b'@FCPA'), (indices, b'@FCIL')):
        data[at:at + 5] = tag
    struct.pack_into('<II', data, 0x88, 8, 32)
    data[0x90:0x98] = b'SEDBmtb\0'
    struct.pack_into('<H', data, params + 6, count)
    for n in range(count):
        record = params + 16 + 80 * n
        data[record:record + 4] = b'cam\0'
        struct.pack_into('<f', data, record + 32, 1.25 + n)
        data[record + 48:record + 52] = b'\x01\x02\x01\0'
        struct.pack_into('<I', data, record + 52, 0x1234 + n)
        struct.pack_into('<HH', data, record + 64, 13, 14)
    struct.pack_into('<HH', data, indices + 16, 15, 17)
    return bytes(data), params, indices


class CameraTests(unittest.TestCase):
    def test_multiple_camera_records_and_index_list(self):
        pc, params, indices = camera(10)
        out = ccb.convert(pc)
        self.assertIsNotNone(out)
        for n in range(10):
            record = params + 16 + n * 80
            self.assertEqual(out[record:record + 32], pc[record:record + 32])
            self.assertEqual(struct.unpack_from('>f', out, record + 32)[0], 1.25 + n)
            self.assertEqual(out[record + 48:record + 52], pc[record + 48:record + 52])
            self.assertEqual(struct.unpack_from('>I', out, record + 52)[0], 0x1234 + n)
            self.assertEqual(struct.unpack_from('>HH', out, record + 64), (13, 14))
        self.assertEqual(struct.unpack_from('>HH', out, indices + 16), (15, 17))

    def test_curve_bytes_and_padding_are_preserved(self):
        pc, _, _ = camera(1)
        self.assertEqual(ccb.convert(pc)[0x90:0xA0], pc[0x90:0xA0])

    def test_unknown_layout_falls_back(self):
        pc, params, _ = camera(1)
        bad = bytearray(pc)
        bad[params:params + 5] = b'@OTHER'
        self.assertIsNone(ccb.convert(bytes(bad)))

    def test_truncated_or_out_of_range_sections_fall_back(self):
        pc, _, _ = camera(1)
        self.assertIsNone(ccb.convert(pc[:-40]))
        bad = bytearray(pc)
        struct.pack_into('<I', bad, 0x8C, 0xFFFFFF00)
        self.assertIsNone(ccb.convert(bytes(bad)))


if __name__ == '__main__':
    unittest.main()
