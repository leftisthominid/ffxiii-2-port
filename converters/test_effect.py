"""Synthetic effect records: no game files or reference DLC are required."""
import struct
import unittest
from unittest.mock import patch
from x2dlc import qix, convert


def leaf(counts=(1, 1), values=(0x01020304, 0x00210007)):
    # Two distinct classes both serialize as {pointer, four bytes}. Their
    # second fields are respectively u32 and two u16 values.
    p = bytearray(0x180)
    p[:8] = b'SEDBleaf'
    p[0xE:0x10] = struct.pack('<H', 0x30)
    struct.pack_into('<Q', p, 0x10, len(p))
    struct.pack_into('<IIHH4s4I', p, 0x30, 0, len(p), 2, 0, bytes(4),
                     0x1C, 0, 0, 0x60)
    bases = (0xC0, 0x100)
    for i, (base, count, value) in enumerate(zip(bases, counts, values)):
        struct.pack_into('<IIHHI', p, 0x50 + i * 16,
                         0x40 + i * 4, 1, 1, 0, 0x6C if i == 0 else 0x20)
        struct.pack_into('<HBB', p, 0x70 + i * 4, 0, 0, 0)
        struct.pack_into('<IIHH', p, 0x90 + i * 12, base - 0x30, count, 8, 0)
        for j in range(count): struct.pack_into('<II', p, base + j * 8, 0, value + j)
    x = bytearray(p)
    for i, (base, count) in enumerate(zip(bases, counts)):
        for j in range(count):
            at = base + j * 8 + 4
            fmt = 'I' if i == 0 else 'HH'
            struct.pack_into('>' + fmt, x, at, *struct.unpack_from('<' + fmt, p, at))
    return bytes(p), bytes(x), bases

def curve_object(curve):
    p=bytearray(leaf()[0][:0x120])+curve
    struct.pack_into('<Q',p,0x10,len(p))
    struct.pack_into('<I',p,0x34,len(p))
    struct.pack_into('<H',p,0x38,1)
    struct.pack_into('<I',p,0x5C,0x20)
    struct.pack_into('<H',p,0x98,4)
    struct.pack_into('<I',p,0xC0,0x120-0x30)
    return bytes(p)


class EffectContexts(unittest.TestCase):
    def test_property_tag_separates_text_and_numeric_payloads(self):
        sig=(36,((24,0),(0,0),(8,0),(16,0)))
        L={'inst':[(sig,0x80,1)]}
        def record(tag,payload):
            p=bytearray(0x150)
            struct.pack_into('<II',p,0x98,0x100,tag)
            p[0x130:]=payload
            return bytes(p)
        text=record(1,b'ABCD'*8)
        number=record(2,bytes.fromhex('34127856')*8)
        native=number[:0x130]+bytes.fromhex('12345678')*8
        with patch.object(qix,'layout',return_value=L), patch.object(qix,'contextual_layout',return_value=[((sig,0,(sig,)),0x80,1)]):
            kb=qix.learn_property_targets([(text,text),(number,native)])
            self.assertEqual(qix.apply_property_targets(text,bytes(len(text)),kb)[0x130:],text[0x130:])
            self.assertEqual(qix.apply_property_targets(number,bytes(len(number)),kb)[0x130:],native[0x130:])
            unknown=record(3,number[0x130:])
            self.assertEqual(qix.apply_property_targets(unknown,unknown,kb),unknown)
            with patch.object(qix,'contextual_layout',return_value=[((sig,0,(sig,None)),0x80,1)]):
                self.assertEqual(qix.apply_property_targets(number,number,kb),number)

    def test_shared_property_target_with_conflicting_tags_is_not_learned(self):
        sig=(36,((24,0),(0,0),(8,0),(16,0)))
        p=bytearray(0x150)
        for i in range(2):struct.pack_into('<II',p,0x98+36*i,0x100,i+1)
        p[0x130:]=b'ABCD'*8;p=bytes(p)
        with patch.object(qix,'layout',return_value={'inst':[(sig,0x80,2)]}), \
             patch.object(qix,'contextual_layout',return_value=[((sig,0,(sig,)),0x80,2)]):
            self.assertEqual(qix.learn_property_targets([(p,p)]),{})

    def test_property_examples_with_no_byte_order_evidence_keep_existing_result(self):
        sig=(36,((24,0),(0,0),(8,0),(16,0)))
        p=bytearray(0x150);struct.pack_into('<II',p,0x98,0x100,1);p=bytes(p)
        with patch.object(qix,'layout',return_value={'inst':[(sig,0x80,1)]}), patch.object(qix,'contextual_layout',return_value=[((sig,0,(sig,)),0x80,1)]):
            kb=qix.learn_property_targets([(p,p)])
            result=bytes([0xA5])*len(p)
            self.assertEqual(qix.apply_property_targets(p,result,kb),result)

    def test_curve_modes_preserve_headers_and_convert_every_float(self):
        source=bytearray();expected=bytearray()
        for mode,components in ((0,1),(1,3),(2,4)):
            header=bytes((mode,components,0,0))
            source+=header;expected+=header
            for i in range((mode+1)*components):
                source+=struct.pack('<f',i+0.25)
                expected+=struct.pack('>f',i+0.25)
        p=curve_object(source)
        out,count=qix.fix_curves(p,bytes(len(p)))
        self.assertEqual(count,3)
        self.assertEqual(out[0x120:],expected)

    def test_curve_float_that_looks_like_text_still_changes_byte_order(self):
        p=curve_object(bytes((0,1,0,0))+b'ABCD')
        out,count=qix.fix_curves(p,p)
        self.assertEqual(count,1)
        self.assertEqual(out[0x124:],b'DCBA')

    def test_incomplete_or_unknown_curve_target_is_left_whole(self):
        for curve in (bytes((0,1,0,0))+b'ABCD'+bytes((2,4,0,0)),bytes((3,1,0,0))+b'ABCD'):
            p=curve_object(curve)
            self.assertEqual(qix.fix_curves(p,p),(p,0))

    def test_identical_pointer_signatures_keep_separate_scalar_types(self):
        p, x, bases = leaf()
        kb = qix.learn_contexts([(p, x)])
        out = qix.apply_contexts(p, bytes(len(p)), kb)
        for base in bases: self.assertEqual(out[base:base + 8], x[base:base + 8])

    def test_context_does_not_depend_on_element_count_or_resource_values(self):
        p, x, _ = leaf()
        kb = qix.learn_contexts([(p, x)])
        p, x, bases = leaf((3, 2), (0x05060708, 0x00330009))
        out = qix.apply_contexts(p, bytes(len(p)), kb)
        for base, count in zip(bases, (3, 2)):
            self.assertEqual(out[base:base + 8 * count], x[base:base + 8 * count])

    def test_effect_with_appended_data_still_uses_its_typed_layout(self):
        p, _, _ = leaf()
        p = bytearray(p)
        struct.pack_into('<I', p, 0x34, len(p) - 32)
        p = bytes(p)
        self.assertIsNone(qix.exact_fit(p))
        self.assertIsNotNone(qix.layout(p))
        c = convert.Converter({}, log=lambda *a: None)
        with patch.object(c, 'oracle', side_effect=AssertionError('untyped oracle used')):
            self.assertEqual(len(c.learned_member('vlea', p, 'synthetic')), len(p))

    def test_instance_layout_keeps_its_own_base_game_learning_path(self):
        p, _, _ = leaf()
        self.assertIsNotNone(qix.exact_fit(p))
        c = convert.Converter({}, log=lambda *a: None)
        with patch.object(c, 'oracle', side_effect=AssertionError('untyped oracle used')), \
             patch.object(c, 'qix_for', return_value={'blocks':{},'targets':{}}) as layouts:
            self.assertEqual(len(c.learned_member('vinsbin', p, 'synthetic')),len(p))
        layouts.assert_called_once_with('vinsbin')

    def test_root_instances_can_learn_without_embedded_effect_members(self):
        def root(values):
            p,x,bases=leaf(values=values)
            p,x=bytearray(p),bytearray(x)
            for data in (p,x): struct.pack_into('<H',data,0xA4,12)
            return bytes(p),bytes(x),bases
        pairs=[root((0x01020304+i,0x00210007+i))[:2] for i in range(8)]
        c=convert.Converter({'pairs':{'vinsbin':pairs}},log=lambda *a:None)
        p,x,bases=root((0x05060708,0x00330009))
        out=c.learned_member('vinsbin',p,'synthetic')
        for base in bases:self.assertEqual(out[base:base+8],x[base:base+8])

    def test_unknown_schema_leaves_existing_conversion(self):
        p, x, _ = leaf()
        self.assertEqual(qix.apply_contexts(p, x, {}), x)


if __name__ == '__main__': unittest.main()
