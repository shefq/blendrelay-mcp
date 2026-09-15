import tempfile
import unittest
from pathlib import Path
from blendrelay_runtime.errors import BlendRelayError
from blendrelay_runtime.service import Service


class RuntimeTests(unittest.TestCase):
    def test_general_capabilities_have_no_specialized_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(directory)
            capabilities = service.rpc_capabilities()
            self.assertTrue(capabilities['general_blender']['mode_aware_prompts'])
            self.assertIn('animation', capabilities['workflows'])
            self.assertEqual(capabilities['schema_version'], '2.0.0')

    def test_reference_registration_is_root_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'reference.webp'; source.write_bytes(b'RIFF')
            service = Service(Path(directory) / 'runtime', [directory])
            result = service.rpc_source_register(str(source))
            self.assertTrue(Path(result['path']).is_file())
            with self.assertRaises(BlendRelayError):
                service.rpc_source_register(str(Path(directory).parent / 'outside.png'))


if __name__ == '__main__': unittest.main()
