import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from x2dlc import package


class PackageInstallation(unittest.TestCase):
    def setup_paths(self, root):
        user=root/'userdata';source=root/'source'
        source.mkdir();(source/'asset').write_bytes(b'new')
        dest=user/'0000000000000000/53510806/00000002/XIII2SteamDLC0000071'
        dest.mkdir(parents=True);(dest/'asset').write_bytes(b'old')
        return user,source,dest

    def test_copy_failure_keeps_installed_package(self):
        with tempfile.TemporaryDirectory() as temp:
            user,source,dest=self.setup_paths(Path(temp))
            with patch.object(package.shutil,'copytree',side_effect=OSError('copy failure')):
                with self.assertRaises(OSError): package.install(source,user,dest.name,'test')
            self.assertEqual((dest/'asset').read_bytes(),b'old')

    def test_header_publish_failure_rolls_back_package(self):
        with tempfile.TemporaryDirectory() as temp:
            user,source,dest=self.setup_paths(Path(temp))
            real=package.os.replace
            def fail_header(a,b):
                if str(b).endswith('.header'): raise OSError('header failure')
                return real(a,b)
            with patch.object(package.os,'replace',side_effect=fail_header):
                with self.assertRaises(OSError): package.install(source,user,dest.name,'test')
            self.assertEqual((dest/'asset').read_bytes(),b'old')

    def test_success_replaces_complete_package_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            user,source,dest=self.setup_paths(Path(temp))
            package.install(source,user,dest.name,'test')
            self.assertEqual((dest/'asset').read_bytes(),b'new')
            self.assertEqual((source/'asset').read_bytes(),b'new')
            self.assertEqual(list((Path(temp)/'work/steamdlc/install-staging').iterdir()),[])


if __name__=='__main__':unittest.main()
