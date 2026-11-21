"""Codex model list from the configured Codex binary (`codex debug models`)."""
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock
try:
    import bpy  # noqa: F401
    from blendrelay_blender import bridge_ui
except ImportError:
    bridge_ui = None

CATALOG = {'models': [
    {'slug': 'gpt-6.1-sol', 'display_name': 'GPT-6.1-Sol', 'visibility': 'list'},
    {'slug': 'gpt-6-luna', 'display_name': 'GPT-6-Luna', 'visibility': 'list'},
    {'slug': 'codex-auto-review', 'visibility': 'hide'},
]}


@unittest.skipIf(bridge_ui is None, 'Blender Python API is not available')
class CodexModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.saved = (list(bridge_ui._MODEL_ITEMS['CODEX']), set(bridge_ui._CODEX_OFFERED), bridge_ui._CODEX_SOURCE)

    def tearDown(self):
        bridge_ui._MODEL_ITEMS['CODEX'], bridge_ui._CODEX_OFFERED, bridge_ui._CODEX_SOURCE = (
            self.saved[0], self.saved[1], self.saved[2])

    def run_result(self, stdout='', returncode=0):
        return SimpleNamespace(stdout=stdout, returncode=returncode)

    def test_list_comes_from_the_configured_binary(self):
        with mock.patch.object(bridge_ui.subprocess, 'run', return_value=self.run_result(json.dumps(CATALOG))) as run:
            count = bridge_ui.discover_codex_models(r'C:\tools\codex.exe')
        self.assertEqual(run.call_args[0][0], [r'C:\tools\codex.exe', 'debug', 'models'])
        self.assertEqual(count, 2)
        self.assertEqual(bridge_ui._CODEX_SOURCE, 'live')
        self.assertEqual(bridge_ui._CODEX_OFFERED, {'gpt-6.1-sol', 'gpt-6-luna'})
        self.assertTrue(bridge_ui.codex_model_unavailable('gpt-6.2-sol'))
        self.assertFalse(bridge_ui.codex_model_unavailable(bridge_ui.MODEL_DEFAULT))

    def test_failures_fall_back_to_the_cache(self):
        for failure in (self.run_result('', 2), self.run_result('not json'), self.run_result('{"models": []}'),
                        subprocess.TimeoutExpired('codex', 20), FileNotFoundError('codex')):
            side = failure if isinstance(failure, BaseException) else None
            with mock.patch.object(bridge_ui.subprocess, 'run', side_effect=side, return_value=failure), \
                    mock.patch.object(bridge_ui.Path, 'is_file', return_value=False):
                bridge_ui.discover_codex_models('codex')
            self.assertEqual(bridge_ui._CODEX_SOURCE, 'cache', failure)

    def test_unlisted_saved_codex_model_is_not_restored(self):
        with mock.patch.object(bridge_ui.subprocess, 'run', return_value=self.run_result(json.dumps(CATALOG))):
            bridge_ui.discover_codex_models('codex')
        self.assertFalse(bridge_ui.ensure_model_item('CODEX', 'gpt-6.2-sol'))
        self.assertTrue(bridge_ui.ensure_model_item('CODEX', 'gpt-6.1-sol'))
        # Claude/Antigravity lists are only known after a refresh, so saved models stay selectable.
        self.assertTrue(bridge_ui.ensure_model_item('CLAUDE', 'claude-opus-5-5'))


if __name__ == '__main__':
    unittest.main()
