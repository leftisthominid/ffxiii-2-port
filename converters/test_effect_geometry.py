"""Embedded geometry keeps its own endian marker and typed stream counts."""
import hashlib
import struct
import unittest
from unittest.mock import patch
from x2dlc import vmdl, convert, wpd

class EmbeddedGeometry(unittest.TestCase):
    def fixture(self,tag=b'mxfv'):
        pc=bytearray(256)
        struct.pack_into('<II',pc,32,80,96)
        pc[128:132]=b'\1\0\0\0';pc[132:136]=tag
        struct.pack_into('<I',pc,136,4)
        struct.pack_into('<I',pc,128+56,1)
        out=bytearray(pc)
        for at in range(128,224,2):out[at:at+2]=pc[at:at+2][::-1]
        return bytes(pc),bytes(out)

    def test_vfxm_and_vins_keep_original_self_describing_bytes(self):
        for tag in (b'mxfv',b'axfv',b'sniv'):
            pc,bad=self.fixture(tag)
            with patch.object(vmdl.qix,'layout',return_value={'pointers':[(32,0)]}):
                fixed=vmdl.preserve_geometry(pc,bad)
            self.assertEqual(fixed[128:224],pc[128:224])
            self.assertEqual(fixed[:128],bad[:128])
            self.assertEqual(fixed[224:],bad[224:])
            self.assertEqual(fixed[128],1)
            self.assertEqual(struct.unpack_from('<I',fixed,128+56)[0],1)

    def test_truncated_unknown_or_non_pointer_payload_is_not_rewritten(self):
        for tag,kind,size in ((b'????',0,96),(b'mxfv',2,96),(b'mxfv',0,500)):
            pc,bad=self.fixture(tag);pc=bytearray(pc);struct.pack_into('<I',pc,36,size)
            with patch.object(vmdl.qix,'layout',return_value={'pointers':[(32,kind)]}):
                self.assertEqual(vmdl.preserve_geometry(bytes(pc),bad),bad)

    def test_models_without_shader_blocks_still_preserve_geometry(self):
        pc,bad=self.fixture()
        with patch.object(vmdl.qix,'layout',return_value={'pointers':[(32,0)]}),patch.object(vmdl,'blocks',return_value=[]):
            self.assertEqual(vmdl.convert(pc,bad,{})[128:224],pc[128:224])

    def test_shader_blocks_with_pc_cache_words_are_identified_by_digest(self):
        payload=b'PC shader program'
        pc=bytearray(128)+hashlib.md5(payload).digest()+struct.pack('>II',len(payload),1)+b'cached!!'+payload
        struct.pack_into('<II',pc,64,128-48,len(payload)+32)
        native=b'Xenos program'
        with patch.object(vmdl.qix,'layout',return_value={'pointers':[(64,0)]}):
            self.assertEqual(vmdl.blocks(pc),[(64,128,len(payload))])
            result=vmdl.convert(bytes(pc),bytes(pc),{hashlib.md5(payload).hexdigest():(native,3,'test')})
        self.assertEqual(result[128:144],hashlib.md5(native).digest())
        self.assertEqual(result[152:160],bytes(8))
        self.assertEqual(result[160:],native)

    def test_nonzero_cache_words_do_not_bypass_shader_digest_validation(self):
        payload=b'PC shader program'
        pc=bytearray(128)+bytes(16)+struct.pack('>II',len(payload),1)+b'cached!!'+payload
        struct.pack_into('<II',pc,64,128-48,len(payload)+32)
        with patch.object(vmdl.qix,'layout',return_value={'pointers':[(64,0)]}):
            self.assertEqual(vmdl.blocks(pc),[])

    def test_animated_model_bundle_uses_geometry_converter(self):
        pc,bad=self.fixture(b'axfv')
        source=wpd.write([(b'animated',b'vanm',pc)])
        c=convert.Converter({},log=lambda *a:None)
        with patch.object(c,'learned_member',return_value=bad) as learned, \
             patch.object(vmdl.qix,'layout',return_value={'pointers':[(32,0)]}), \
             patch.object(vmdl,'blocks',return_value=[]):
            result,_=c.bundle(source,'event/animated.white.x360.bin')
        learned.assert_called_once_with('vanm',pc,'event/animated.white.x360.bin')
        self.assertEqual(wpd.read(result)[0][2][128:224],pc[128:224])

if __name__=='__main__':unittest.main()
