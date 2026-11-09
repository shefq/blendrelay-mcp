# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
from pathlib import Path
import unittest
try:
    import bpy
    from blendrelay_blender import focused_view, general
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, "Blender Python API (bpy) is not available")
class FocusedViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Clean existing scene mesh objects
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 1.0))
        self.cube = bpy.context.object
        self.cube.name = 'TestCube'

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_object_returns_failure_early(self):
        result = focused_view.capture(self.tmp.name, target_objects=['NonExistentObject'])
        self.assertTrue(result.get('failed'))
        self.assertIn('NonExistentObject', result['missing_objects'])
        self.assertEqual(result['resolved_objects'], [])
        self.assertEqual(result['views'], [])
        self.assertTrue(result['scene_state_restored'])

    def test_missing_object_with_partial_resolved(self):
        result = focused_view.capture(self.tmp.name, target_objects=['TestCube', 'GhostObject'])
        self.assertTrue(result.get('failed'))
        self.assertEqual(result['resolved_objects'], ['TestCube'])
        self.assertEqual(result['missing_objects'], ['GhostObject'])
        self.assertEqual(result['views'], [])
        self.assertTrue(result['scene_state_restored'])

    def test_scale_factor_padding_becomes_margin(self):
        self.assertAlmostEqual(focused_view.normalize_padding(1.25), 0.25)
        self.assertAlmostEqual(focused_view.normalize_padding(0.15), 0.15)
        self.assertEqual(focused_view.normalize_padding(-1), 0.0)
        result = focused_view.capture(self.tmp.name, target_objects=['TestCube'], padding=1.25,
                                      render_mode='workbench', max_dimension=128)
        self.assertFalse(result.get('failed'), result)

    def test_empty_targets_raises_value_error(self):
        with self.assertRaises(ValueError):
            focused_view.capture(self.tmp.name, target_objects=[])

    def test_successful_focused_capture_default(self):
        result = focused_view.capture(
            self.tmp.name,
            target_objects=['TestCube'],
            context_mode='targets_only',
            padding=0.15,
            max_dimension=256,
        )
        self.assertFalse(result.get('failed', False))
        self.assertEqual(result['resolved_objects'], ['TestCube'])
        self.assertEqual(result['missing_objects'], [])
        self.assertEqual(len(result['views']), 1)
        self.assertTrue(result['scene_state_restored'])

        view = result['views'][0]
        self.assertIn('image_path', view)
        self.assertEqual(len(view['camera_matrix_world']), 16)
        self.assertEqual(len(view['camera_position']), 3)
        self.assertEqual(len(view['target_center']), 3)
        self.assertEqual(len(view['target_dimensions']), 3)
        self.assertGreater(view['frame_coverage'], 0.0)
        self.assertGreater(view['visibility_score'], 0.0)
        self.assertTrue(view['fully_framed'])
        self.assertEqual(len(result['images']), 1)
        self.assertEqual(result['images'][0]['mime_type'], 'image/jpeg')
        self.assertNotIn('data', result['images'][0])
        self.assertGreater(Path(result['images'][0]['path']).stat().st_size, 0)

    def test_matrix_world_replay(self):
        first = focused_view.capture(
            self.tmp.name,
            target_objects=['TestCube'],
            max_dimension=256,
        )
        saved_matrix = first['views'][0]['camera_matrix_world']

        replayed = focused_view.capture(
            self.tmp.name,
            target_objects=['TestCube'],
            camera_matrix_world=saved_matrix,
            max_dimension=256,
        )
        self.assertFalse(replayed.get('failed', False))
        self.assertEqual(len(replayed['views']), 1)
        # Check that replayed matrix matches within rounding
        for v1, v2 in zip(saved_matrix, replayed['views'][0]['camera_matrix_world']):
            self.assertAlmostEqual(v1, v2, places=4)
        self.assertIn('Replayed exact camera_matrix_world', replayed['views'][0]['adjustments'][0])

    def test_multi_viewpoint_hints(self):
        hints = [
            [5.0, 5.0, 3.0],
            [-5.0, -5.0, 3.0],
            [-5.0, 5.0, 3.0],
        ]
        result = focused_view.capture(
            self.tmp.name,
            target_objects=['TestCube'],
            camera_position_hints=hints,
            max_dimension=256,
        )
        self.assertFalse(result.get('failed', False))
        self.assertEqual(len(result['views']), 3)
        self.assertEqual(len(result['images']), 3)
        for view in result['views']:
            self.assertTrue(view['fully_framed'])

    def test_dispatcher_integration(self):
        job = {
            'action': 'capture_focused_view',
            'arguments': {
                'target_objects': ['TestCube'],
                'max_dimension': 256,
            }
        }
        res = general.run(self.tmp.name, job)
        self.assertFalse(res.get('failed', False))
        self.assertEqual(res['resolved_objects'], ['TestCube'])
        self.assertEqual(len(res['views']), 1)

    def test_eevee_render_mode(self):
        result = focused_view.capture(
            self.tmp.name,
            target_objects=['TestCube'],
            render_mode='eevee',
            max_dimension=256,
        )
        self.assertFalse(result.get('failed', False))
        self.assertEqual(len(result['views']), 1)
        self.assertTrue(result['scene_state_restored'])

    def test_cycles_max_dimension_capped_at_512(self):
        result = focused_view.capture(
            self.tmp.name,
            target_objects=['TestCube'],
            render_mode='cycles',
            max_dimension=1024,
            cycles_samples=1,
        )
        self.assertFalse(result.get('failed', False))
        self.assertEqual(len(result['views']), 1)
        # Blender's bundled Python has no Pillow; read the size with bpy.
        img = bpy.data.images.load(result['views'][0]['image_path'], check_existing=False)
        try:
            self.assertLessEqual(max(img.size), 512)
        finally:
            bpy.data.images.remove(img)
        self.assertTrue(result['scene_state_restored'])


