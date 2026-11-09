import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from blendrelay_blender import agent_preferences as prefs
except ImportError:
    prefs = None


@unittest.skipIf(prefs is None, 'Blender Python API is not available')
class AgentPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'agent.json'
        self.path_patch = patch.object(prefs, 'settings_path', return_value=self.path)
        self.path_patch.start()
        self.saved = prefs._pending, prefs._prepare_model
        prefs._pending = None

    def tearDown(self):
        prefs._pending, prefs._prepare_model = self.saved
        self.path_patch.stop()
        self.temp.cleanup()

    def scene(self, **values):
        base = {name: '' for name in prefs.FIELDS}
        base.update(blendrelay_agent_backend='ANTIGRAVITY', **{n: prefs.MODEL_DEFAULT for n in prefs.MODEL_FIELDS})
        base.update(values)
        return SimpleNamespace(**base)

    def test_codex_is_the_default_before_anything_is_chosen(self):
        self.assertEqual(prefs.DEFAULT_AGENT, 'CODEX')
        self.assertEqual(prefs.load()['blendrelay_agent_backend'], 'CODEX')

    def test_choice_is_saved_for_blender_and_applied_to_any_file(self):
        chosen = self.scene(blendrelay_agent_backend='CLAUDE', blendrelay_claude_model='claude-opus-5-5',
                            blendrelay_codex_path=r'C:\tools\codex.exe')
        fake_bpy = SimpleNamespace(data=SimpleNamespace(scenes=[chosen]),
                                   app=SimpleNamespace(timers=SimpleNamespace(is_registered=lambda f: True)))
        with patch.object(prefs, 'bpy', fake_bpy):
            prefs.changed(chosen, None)
        prefs.flush()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['blendrelay_agent_backend'], 'CLAUDE')
        self.assertEqual(saved['blendrelay_claude_model'], 'claude-opus-5-5')
        # A file saved earlier with Antigravity picks up the Blender-wide choice.
        old_file_scene = self.scene()
        prepared = []
        prefs._prepare_model = lambda agent, model: prepared.append((agent, model))
        with patch.object(prefs, 'bpy', SimpleNamespace(data=SimpleNamespace(scenes=[old_file_scene]))):
            prefs.restore()
        self.assertEqual(old_file_scene.blendrelay_agent_backend, 'CLAUDE')
        self.assertEqual(old_file_scene.blendrelay_claude_model, 'claude-opus-5-5')
        self.assertEqual(old_file_scene.blendrelay_codex_path, r'C:\tools\codex.exe')
        self.assertIn(('CLAUDE', 'claude-opus-5-5'), prepared)

    def test_old_file_without_saved_settings_opens_with_codex(self):
        old_file_scene = self.scene()
        with patch.object(prefs, 'bpy', SimpleNamespace(data=SimpleNamespace(scenes=[old_file_scene]))):
            prefs.restore()
        self.assertEqual(old_file_scene.blendrelay_agent_backend, 'CODEX')

    def test_invalid_or_corrupt_settings_are_ignored(self):
        self.assertEqual(prefs.validated(dict(blendrelay_agent_backend='GPT', blendrelay_codex_model='  ',
                                              blendrelay_codex_path=5, unrelated='x')), {})
        self.path.write_text('{broken')
        self.assertEqual(prefs.load(), prefs.DEFAULTS)

    def test_restoring_does_not_schedule_another_save(self):
        prefs._applying = True
        try:
            prefs.changed(self.scene(), None)
            self.assertIsNone(prefs._pending)
        finally:
            prefs._applying = False

    def test_unavailable_model_keeps_the_scene_value(self):
        class Strict(SimpleNamespace):
            def __setattr__(self, name, value):
                if name == 'blendrelay_codex_model' and value == 'gone-model':
                    raise TypeError('enum "gone-model" not found')
                super().__setattr__(name, value)
        scene = Strict(**vars(self.scene()))
        prefs.apply({'blendrelay_codex_model': 'gone-model', 'blendrelay_agent_backend': 'CLAUDE'}, [scene])
        self.assertEqual(scene.blendrelay_codex_model, prefs.MODEL_DEFAULT)
        self.assertEqual(scene.blendrelay_agent_backend, 'CLAUDE')
        self.assertFalse(prefs._applying)


if __name__ == '__main__':
    unittest.main()
