import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from blendrelay_blender import hud_preferences as prefs
except ImportError:
    prefs = None


@unittest.skipIf(prefs is None, 'Blender Python API is not available')
class HudPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'hud.json'
        self.path_patch = patch.object(prefs, 'settings_path', return_value=self.path)
        self.path_patch.start()
        self.saved_pending = prefs._pending
        prefs._pending = None

    def tearDown(self):
        prefs._pending = self.saved_pending
        self.path_patch.stop()
        self.temp.cleanup()

    def test_preferences_restore_after_a_new_session(self):
        values = dict(blendrelay_hud_width=1100, blendrelay_hud_height=320,
                      blendrelay_hud_scale=1.4, blendrelay_hud_adaptive=False,
                      blendrelay_hud_x_offset=40., blendrelay_hud_y_offset=70.)
        scene = SimpleNamespace(**values)
        with patch.object(prefs.bpy.app.timers, 'is_registered', return_value=True):
            prefs.changed(scene, None)
        prefs.flush()
        self.assertEqual(json.loads(self.path.read_text()), values)
        fresh = SimpleNamespace()
        with patch.object(prefs, 'bpy', SimpleNamespace(data=SimpleNamespace(scenes=[fresh]))):
            prefs.restore()
        self.assertEqual(vars(fresh), values)

    def test_corrupt_settings_are_ignored(self):
        self.path.write_text('{broken')
        prefs.restore()
        self.assertFalse(prefs._applying)

    def test_invalid_values_are_rejected_or_clamped(self):
        result = prefs.validated(dict(blendrelay_hud_scale=float('nan'),
                                     blendrelay_hud_width=99999,
                                     blendrelay_hud_adaptive='false', unrelated=1))
        self.assertEqual(result, {'blendrelay_hud_width':4000})

    def test_restoring_does_not_schedule_another_save(self):
        prefs._applying = True
        try:
            prefs.changed(SimpleNamespace(), None)
            self.assertIsNone(prefs._pending)
        finally:
            prefs._applying = False


    def test_registration_defers_scene_access_under_restricted_context(self):
        from unittest.mock import Mock
        timers = SimpleNamespace(is_registered=Mock(return_value=False), register=Mock())
        handlers = SimpleNamespace(load_post=[], save_pre=[])
        fake = SimpleNamespace(data=SimpleNamespace(), app=SimpleNamespace(timers=timers, handlers=handlers))
        with patch.object(prefs, 'bpy', fake), patch.object(prefs, 'restore') as restore:
            prefs.register()
            restore.assert_not_called()
            timers.register.assert_called_once_with(prefs.restore_when_ready, first_interval=.1)
            self.assertEqual(prefs.restore_when_ready(), .1)
            fake.data.scenes = []
            self.assertIsNone(prefs.restore_when_ready())
            restore.assert_called_once_with()

    def test_unregister_cancels_deferred_restore(self):
        from unittest.mock import Mock
        timers = SimpleNamespace(is_registered=Mock(return_value=True), unregister=Mock())
        handlers = SimpleNamespace(load_post=[prefs.restore], save_pre=[prefs.before_save])
        fake = SimpleNamespace(app=SimpleNamespace(timers=timers, handlers=handlers))
        with patch.object(prefs, 'bpy', fake):
            prefs.unregister()
        timers.unregister.assert_any_call(prefs.restore_when_ready)
        self.assertEqual(handlers.load_post, [])
