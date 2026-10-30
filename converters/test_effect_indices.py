"""Typed resource IDs retain their values across endian conversion."""
import struct
import unittest
from unittest.mock import patch
from x2dlc import qix

class EffectIndices(unittest.TestCase):
    def test_particle_references_require_owning_emitter_class(self):
        pc=bytearray(512)
        struct.pack_into('<II',pc,80+180,352-48,1)
        for off in (40,44,76,84): struct.pack_into('<hh',pc,352+off,1,-1)
        payload=((96,((24,0),)),352,1)
        emitter=((240,((180,0),(188,0),(160,0),(168,0))),80,1)
        for owner in ([],[emitter]):
            with patch.object(qix,'layout',return_value={'inst':owner+[payload]}):
                result,count=qix.fix_particle_references(bytes(pc),bytes(pc))
            self.assertEqual(count,8 if owner else 0)
            if owner:
                for off in (40,44,76,84):self.assertEqual(struct.unpack_from('>hh',result,352+off),(1,-1))
            else:self.assertEqual(result,bytes(pc))

    def test_particle_references_reject_payload_overrun(self):
        pc=bytearray(512);struct.pack_into('<II',pc,80+180,352-48,2)
        inst=[((240,((180,0),(188,0),(160,0),(168,0))),80,1),((96,((24,0),)),352,1)]
        with patch.object(qix,'layout',return_value={'inst':inst}):
            self.assertEqual(qix.fix_particle_references(bytes(pc),bytes(pc)),(bytes(pc),0))

    def test_adjacent_leaf_reference_lists_do_not_exchange_ids(self):
        for size,fields in [(80,((32,2),(40,3),(0,0),(8,0),(16,0),(24,0),(68,0))),
                            (84,((32,2),(40,3),(0,0),(8,0),(16,0),(24,0),(76,3),(68,0)))]:
            pc=bytearray(256)
            struct.pack_into('<4I',pc,96,192-48,1,194-48,1)
            pc[192:196]=struct.pack('<2H',0,1)
            bad=bytearray(pc);bad[192:196]=bytes(pc[192:196])[::-1]
            with patch.object(qix,'layout',return_value={'inst':[((size,fields),80,1)]}):
                result,count=qix.fix_leaf_indices(bytes(pc),bytes(bad))
            self.assertEqual(struct.unpack_from('>2H',result,192),(0,1))
            self.assertEqual(count,2)
            self.assertEqual(result[:192],bad[:192])

    def test_generate_group_ids_are_u16_including_sentinel(self):
        pc=bytearray(256)
        struct.pack_into('<II',pc,88,160-48,1)
        struct.pack_into('<II',pc,160,192-48,3)
        struct.pack_into('<3H',pc,192,1,0x1234,0xffff)
        inst=[((36,((24,0),(0,0),(8,0),(16,0))),80,1),((8,((0,0),)),160,1)]
        with patch.object(qix,'layout',return_value={'inst':inst}):
            result,count=qix.fix_generate_indices(bytes(pc),bytes(pc))
        self.assertEqual(struct.unpack_from('>3H',result,192),(1,0x1234,0xffff))
        self.assertEqual(count,3)
        self.assertEqual(result[198:],pc[198:])

    def test_generate_requires_typed_bounded_group_table(self):
        for group in [(8,()),(8,((0,0),))]:
            pc=bytearray(256);struct.pack_into('<II',pc,88,160-48,2)
            struct.pack_into('<II',pc,160,192-48,1);struct.pack_into('<H',pc,192,1)
            inst=[((36,((24,0),(0,0),(8,0),(16,0))),80,1),(group,160,1)]
            with patch.object(qix,'layout',return_value={'inst':inst}):
                self.assertEqual(qix.fix_generate_indices(bytes(pc),bytes(pc)),(bytes(pc),0))

    def test_outside_leaf_reference_array_is_unchanged(self):
        pc=bytearray(256);struct.pack_into('<4I',pc,96,254-48,2,0,0)
        sig=(80,((32,2),(40,3),(0,0),(8,0),(16,0),(24,0),(68,0)))
        with patch.object(qix,'layout',return_value={'inst':[(sig,80,1)]}):
            self.assertEqual(qix.fix_leaf_indices(bytes(pc),bytes(pc)),(bytes(pc),0))

class InlineResourceControls(unittest.TestCase):
    def test_inline_resource_keeps_group_and_index_order(self):
        pc=bytearray(256);struct.pack_into('<II',pc,80,40,0)
        pc[88:92]=bytes([1,1,0,0]);struct.pack_into('<hh',pc,92,1,0)
        bad=bytearray(pc);bad[92:96]=pc[92:96][::-1]
        with patch.object(qix,'layout',return_value={'inst':[((16,((0,0),)),80,1)]}):
            out,count=qix.fix_inline_resource_controls(bytes(pc),bytes(bad))
        self.assertEqual(count,1);self.assertEqual(struct.unpack_from('>hh',out,92),(1,0))
        self.assertEqual(out[:92],bad[:92]);self.assertEqual(out[96:],bad[96:])

    def test_literal_external_and_unknown_wrappers_are_untouched(self):
        for pointer,tag,flag,sig in [(40,0,0,(16,((0,0),))), (44,0,1,(16,((0,0),))),
                                    (40,1,1,(16,((0,0),))), (40,0,1,(16,()))]:
            pc=bytearray(256);struct.pack_into('<II',pc,80,pointer,tag);pc[89]=flag
            native=bytes([23])*256
            with patch.object(qix,'layout',return_value={'inst':[(sig,80,1)]}):
                self.assertEqual(qix.fix_inline_resource_controls(bytes(pc),native),(native,0))

    def test_truncated_wrapper_is_untouched(self):
        pc=bytes(90)
        with patch.object(qix,'layout',return_value={'inst':[((16,((0,0),)),80,1)]}):
            self.assertEqual(qix.fix_inline_resource_controls(pc,pc),(pc,0))

if __name__=='__main__':unittest.main()
