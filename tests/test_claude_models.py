import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from blendrelay_blender import claude_model_discovery as discovery


def response(models, **extra):
    envelope = {'subtype': 'success', 'request_id': 'blendrelay-model-discovery',
                'response': {'models': models}, **extra}
    return json.dumps({'type': 'control_response', 'response': envelope})


class ClaudeModelDiscoveryTests(unittest.TestCase):
    def test_preserves_cli_models_labels_and_custom_provider_ids(self):
        output = 'not json\n' + json.dumps({'type': 'system', 'models': ['ignore']}) + '\n'
        output += response([
            {'value': 'opus', 'displayName': 'Opus from CLI', 'description': 'Complex work'},
            {'value': 'provider/model', 'displayName': 'Gateway model'},
            {'value': 'opus', 'displayName': 'Duplicate'},
            None, {'value': ''}, {'value': ['invalid']}, {'value': '__DEFAULT__'},
        ])
        self.assertEqual(discovery._models_from_response(output, 'blendrelay-model-discovery'),
                         [('opus', 'Opus from CLI', 'Complex work'),
                          ('provider/model', 'Gateway model', 'provider/model')])

    def test_ignores_unrelated_control_responses(self):
        output = response([{'value': 'wrong'}], request_id='other') + '\n'
        output += response([{'value': 'sonnet'}])
        self.assertEqual(discovery._models_from_response(output, 'blendrelay-model-discovery'),
                         [('sonnet', 'sonnet', 'sonnet')])

    def test_missing_empty_and_malformed_catalogs_fail_explicitly(self):
        for output in ('garbage', '[]', response([]), response(None),
                       response({'opus': 'model'})):
            with self.subTest(output=output), self.assertRaises(discovery.ModelDiscoveryError):
                discovery._models_from_response(output, 'blendrelay-model-discovery')

    def test_refused_initialization_reports_failure(self):
        with self.assertRaisesRegex(discovery.ModelDiscoveryError, 'refused'):
            discovery._models_from_response(response([], subtype='error'),
                                            'blendrelay-model-discovery')

    def test_only_initializes_without_a_generation_prompt_or_external_tools(self):
        completed = subprocess.CompletedProcess([], 0, response([{'value': 'sonnet'}]), '')
        with patch.object(discovery.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(discovery.discover_models('custom-claude', timeout=3),
                             [('sonnet', 'sonnet', 'sonnet')])
        arguments = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(arguments[0], 'custom-claude')
        self.assertEqual(arguments[arguments.index('--tools') + 1], '')
        self.assertEqual(json.loads(arguments[arguments.index('--mcp-config') + 1]),
                         {'mcpServers': {}})
        self.assertTrue(json.loads(arguments[arguments.index('--settings') + 1])['disableAllHooks'])
        self.assertIn('--no-session-persistence', arguments)
        messages = [json.loads(line) for line in options['input'].splitlines()]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['type'], 'control_request')
        self.assertEqual(messages[0]['request']['subtype'], 'initialize')
        self.assertEqual(options['timeout'], 3)
        self.assertFalse(Path(options['cwd']).exists(), 'discovery temp directory leaked')

    def test_timeout_does_not_hang_refresh(self):
        with patch.object(discovery.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired('claude', 3)):
            with self.assertRaisesRegex(discovery.ModelDiscoveryError, 'timed out'):
                discovery.discover_models('claude', timeout=3)

    def test_missing_cli_is_actionable(self):
        with patch.object(discovery.subprocess, 'run', side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(discovery.ModelDiscoveryError, 'CLI path'):
                discovery.discover_models('missing-claude')

    def test_cli_errors_do_not_disclose_raw_account_output(self):
        completed = subprocess.CompletedProcess([], 1, 'private account detail', 'private token')
        with patch.object(discovery.subprocess, 'run', return_value=completed):
            with self.assertRaises(discovery.ModelDiscoveryError) as caught:
                discovery.discover_models('claude')
        self.assertIn('code 1', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
