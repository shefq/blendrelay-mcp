# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import tempfile
import unittest
import uuid
import bpy

from archforge_blender import general
from archforge_runtime.service import Service
import archforge_blender


class ClearDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.scene = bpy.context.scene
        self.old_workspace = self.scene.get('archforge_workspace_id')
        self.scene['archforge_workspace_id'] = uuid.uuid4().hex
        self.workspace = general.workspace_root(self.root, self.scene)
        for name in ('agent_runs','scene_versions','conversations','focused_views','selection_views'):
            folder = self.workspace / name; folder.mkdir(parents=True, exist_ok=True)
            (folder / 'data.bin').write_bytes(b'data')
        (self.workspace / 'sketch_viewport.jpg').write_bytes(b'data')
        (self.root / 'blender_jobs').mkdir(exist_ok=True)
        (self.root / 'blender_jobs' / 'job1.json').write_text('{"status":"complete"}')
        (self.root / 'audit.jsonl').write_text('{"event":"test"}\n')
        (self.root / 'assets' / 'thumbnails').mkdir(parents=True, exist_ok=True)
        (self.root / 'assets' / 'thumbnails' / 'thumb.png').write_bytes(b'data')

    def tearDown(self):
        if self.old_workspace is None: self.scene.pop('archforge_workspace_id', None)
        else: self.scene['archforge_workspace_id'] = self.old_workspace
        self.tmp.cleanup()

    def test_workspace_identity_is_stable_and_isolated(self):
        first = general.workspace_root(self.root, self.scene)
        self.assertEqual(first, general.workspace_root(self.root, self.scene))
        other = bpy.data.scenes.new('Independent Scene')
        try:
            second = general.workspace_root(self.root, other)
            self.assertNotEqual(first, second)
            self.assertTrue((first / 'workspace.json').is_file())
            self.assertTrue((second / 'workspace.json').is_file())
        finally: bpy.data.scenes.remove(other)

    def test_general_clear_only_current_workspace(self):
        result = general.clear_stored_data(self.root, include_assets=False)
        self.assertEqual(Path(result['root']), self.workspace)
        self.assertEqual([p for p in self.workspace.rglob('*') if p.is_file() and p.name != 'workspace.json'], [])
        self.assertTrue((self.root / 'blender_jobs' / 'job1.json').is_file())
        self.assertTrue((self.root / 'audit.jsonl').is_file())
        self.assertTrue((self.root / 'assets' / 'thumbnails' / 'thumb.png').is_file())

    def test_general_clear_can_remove_shared_assets(self):
        general.clear_stored_data(self.root, include_assets=True)
        self.assertEqual(list((self.root / 'assets').glob('*')), [])

    def test_service_can_clear_one_workspace(self):
        service = Service(self.root)
        other = self.root / 'workspaces' / ('b' * 32)
        other.mkdir(parents=True); (other / 'keep.txt').write_text('other scene')
        try:
            result = service.dispatch('clear_data', {'workspace_id': self.scene['archforge_workspace_id']})
            self.assertEqual(result['workspace_id'], self.scene['archforge_workspace_id'])
            self.assertEqual(list(self.workspace.glob('*')), [])
            self.assertTrue((other / 'keep.txt').is_file())
            self.assertIn('clear_data', (self.root / 'audit.jsonl').read_text())
        finally: service.store.close()

    def test_blender_operator_clears_current_workspace(self):
        archforge_blender.register()
        try:
            bpy.context.scene.archforge_runtime_dir = str(self.root)
            from archforge_blender.bridge_ui import STATE
            STATE['root'] = str(self.root); STATE['workspace_id'] = self.scene['archforge_workspace_id']
            STATE['versions'] = [{'version_id':'123','label':'Test'}]; STATE['agent_log'] = 'dummy.log'
            self.assertEqual(bpy.ops.archforge.clear_stored_data(include_assets=False), {'FINISHED'})
            self.assertEqual(STATE['versions'], []); self.assertIsNone(STATE['agent_log'])
            self.assertTrue((self.root / 'blender_jobs' / 'job1.json').is_file())
        finally: archforge_blender.unregister()


if __name__ == '__main__': unittest.main()
