# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import tempfile
import unittest
import bpy

from archforge_blender import general
from archforge_runtime.service import Service
import archforge_blender


class ClearDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Create mock data structure
        (self.root / 'blender_jobs').mkdir(parents=True, exist_ok=True)
        (self.root / 'blender_jobs' / 'job1.json').write_text('{"status":"complete"}')
        (self.root / 'agent_runs').mkdir(parents=True, exist_ok=True)
        (self.root / 'agent_runs' / 'run1.log').write_text('agent log data')
        (self.root / 'scene_versions').mkdir(parents=True, exist_ok=True)
        (self.root / 'scene_versions' / 'v1.blend').write_text('blend file')
        (self.root / 'conversations').mkdir(parents=True, exist_ok=True)
        (self.root / 'conversations' / 'conv1.json').write_text('{}')
        (self.root / 'focused_views').mkdir(parents=True, exist_ok=True)
        (self.root / 'focused_views' / 'view1.jpg').write_text('jpg data')
        (self.root / 'selection_views').mkdir(parents=True, exist_ok=True)
        (self.root / 'selection_views' / 'sel1.jpg').write_text('jpg data')
        (self.root / 'sketch_viewport.jpg').write_text('sketch data')
        (self.root / 'audit.jsonl').write_text('{"event":"test"}\n')
        (self.root / 'audit.previous.jsonl').write_text('{"event":"old"}\n')
        (self.root / 'assets' / 'thumbnails').mkdir(parents=True, exist_ok=True)
        (self.root / 'assets' / 'thumbnails' / 'thumb.png').write_text('thumb data')

    def tearDown(self):
        self.tmp.cleanup()

    def test_general_clear_stored_data_preserves_assets_by_default(self):
        result = general.clear_stored_data(self.root, include_assets=False)
        self.assertTrue(result.get('cleared'))
        self.assertEqual(list((self.root / 'blender_jobs').glob('*')), [])
        self.assertEqual(list((self.root / 'agent_runs').glob('*')), [])
        self.assertEqual(list((self.root / 'scene_versions').glob('*')), [])
        self.assertEqual(list((self.root / 'conversations').glob('*')), [])
        self.assertEqual(list((self.root / 'focused_views').glob('*')), [])
        self.assertEqual(list((self.root / 'selection_views').glob('*')), [])
        self.assertFalse((self.root / 'sketch_viewport.jpg').exists())
        self.assertEqual((self.root / 'audit.jsonl').stat().st_size, 0)
        self.assertEqual((self.root / 'audit.previous.jsonl').stat().st_size, 0)
        # Assets should be preserved
        self.assertTrue((self.root / 'assets' / 'thumbnails' / 'thumb.png').exists())

    def test_general_clear_stored_data_clears_assets_when_requested(self):
        result = general.clear_stored_data(self.root, include_assets=True)
        self.assertTrue(result.get('cleared'))
        self.assertEqual(list((self.root / 'assets').glob('*')), [])

    def test_service_rpc_clear_data(self):
        service = Service(self.root)
        try:
            service.rpc_blender_poll(instance_id='test-inst')
            res = service.dispatch('clear_data', {'include_assets': True})
            self.assertTrue(res.get('cleared'))
            self.assertEqual(list((self.root / 'blender_jobs').glob('*')), [])
            # Audit log only contains the clear_data operation itself
            lines = (self.root / 'audit.jsonl').read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn('clear_data', lines[0])
        finally:
            service.store.close()

    def test_blender_operator_clear_stored_data(self):
        archforge_blender.register()
        try:
            bpy.context.scene.archforge_runtime_dir = str(self.root)
            from archforge_blender.bridge_ui import STATE
            STATE['versions'] = [{'version_id': '123', 'label': 'Test'}]
            STATE['agent_log'] = 'dummy.log'

            ret = bpy.ops.archforge.clear_stored_data(include_assets=False)
            self.assertEqual(ret, {'FINISHED'})
            self.assertEqual(STATE['versions'], [])
            self.assertIsNone(STATE['agent_log'])
            self.assertEqual(list((self.root / 'blender_jobs').glob('*')), [])
        finally:
            archforge_blender.unregister()
