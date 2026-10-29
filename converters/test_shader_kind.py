"""Compiler-added constants must have a native driver allocation slot."""
import unittest
from unittest.mock import patch
from x2dlc import shader, xengine

class CompiledPixelKind(unittest.TestCase):
    def test_alpha_driver_added_for_unlearned_pc_kind(self):
        with patch.object(xengine,'container_info',return_value=({'consts':[{'name':b'alphaOffset'}]},[],0,True)):
            self.assertEqual(shader.compiled_pixel_kind(0x10011,b'compiled'),0x10111)
    def test_unused_alpha_driver_removed_and_other_flags_preserved(self):
        with patch.object(xengine,'container_info',return_value=({'consts':[]},[],0,True)):
            self.assertEqual(shader.compiled_pixel_kind(0x10131,b'compiled'),0x10031)
    def test_vertex_program_rejected(self):
        with patch.object(xengine,'container_info',return_value=({'consts':[]},[],0,False)):
            with self.assertRaises(ValueError):shader.compiled_pixel_kind(0x805,b'vertex')

if __name__=='__main__':unittest.main()
