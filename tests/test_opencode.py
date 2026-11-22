"""OpenCode command construction, MCP registration and run accounting."""
import json
from pathlib import Path
import tempfile
import unittest

from blendrelay_blender.agent_permissions import (
    opencode_command, opencode_mcp_is_registered,
)
from blendrelay_blender.run_metrics import Metrics


class OpenCodeCommandTests(unittest.TestCase):
    def test_run_uses_json_stdin_and_optional_flags(self):
        args = opencode_command('opencode', resume_id='ses_1', model='opencode/demo', auto=True)
        self.assertEqual(args[:4], ['opencode', 'run', '--format', 'json'])
        self.assertIn('--auto', args)
        self.assertEqual(args[args.index('--session') + 1], 'ses_1')
        self.assertEqual(args[args.index('--model') + 1], 'opencode/demo')
        self.assertNotIn('-', args)  # the prompt is stdin, not a '-' argument

    def test_manual_run_omits_auto_session_and_model(self):
        args = opencode_command('/usr/bin/opencode', auto=False)
        self.assertEqual(args, ['/usr/bin/opencode', 'run', '--format', 'json'])

    def test_mcp_registration_reads_only_the_blendrelay_server(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'opencode.json'
            path.write_text(json.dumps({'mcp': {'servers': {
                'other': {'type': 'local', 'command': ['true']},
            }}}))
            self.assertFalse(opencode_mcp_is_registered(path))
            path.write_text(json.dumps({'mcp': {'servers': {
                'blendrelay': {'type': 'local', 'command': ['uvx', 'blendrelay-mcp', 'mcp']},
            }}}))
            self.assertTrue(opencode_mcp_is_registered(path))
            path.write_text('{not json')
            self.assertFalse(opencode_mcp_is_registered(path))


class OpenCodeMetricsTests(unittest.TestCase):
    def test_tool_and_token_events_are_counted(self):
        metrics = Metrics()
        metrics.feed(json.dumps({
            'type': 'tool_use',
            'part': {
                'id': 'call-1',
                'tool': 'blendrelay_execute_blender_python',
                'state': {'status': 'completed', 'input': {'code': 'bpy.ops.mesh.primitive_cube_add()'}},
            },
        }))
        metrics.feed(json.dumps({
            'type': 'tool_use',
            'part': {'id': 'call-1', 'tool': 'blendrelay_execute_blender_python',
                     'state': {'status': 'completed', 'input': {'code': 'again'}}},
        }))
        metrics.feed(json.dumps({
            'type': 'step_finish',
            'part': {'reason': 'tool-calls', 'cost': 0.01,
                     'tokens': {'input': 10, 'output': 4, 'reasoning': 1, 'cache': {'read': 8, 'write': 2}}},
        }))
        metrics.feed(json.dumps({
            'type': 'step_finish',
            'part': {'reason': 'stop', 'cost': 0.02,
                     'tokens': {'input': 3, 'output': 1, 'cache': {'read': 0, 'write': 0}}},
        }))
        summary = metrics.summary()
        self.assertEqual(summary['tool_calls_by_stage'], {'editing': 1})
        usage = summary['provider_usage']
        self.assertEqual(usage['input_tokens'], 13)
        self.assertEqual(usage['output_tokens'], 5)
        self.assertEqual(usage['cache_read_tokens'], 8)
        self.assertAlmostEqual(usage['total_cost_usd'], 0.03)

    def test_pending_tool_use_is_not_counted(self):
        metrics = Metrics()
        metrics.feed(json.dumps({
            'type': 'tool_use',
            'part': {'id': 'call-2', 'tool': 'blendrelay_inspect_scene',
                     'state': {'status': 'running', 'input': {}}},
        }))
        self.assertEqual(metrics.summary()['tool_calls_by_stage'], {})

    def test_code_mode_execute_is_attributed_to_the_inner_tool(self):
        metrics = Metrics()
        metrics.feed(json.dumps({
            'type': 'tool_use',
            'part': {'id': 'call-3', 'tool': 'execute', 'state': {'status': 'completed', 'input': {
                'code': 'await tools.blendrelay.build_scene_batch({operations: []})'}}},
        }))
        metrics.feed(json.dumps({
            'type': 'tool_use',
            'part': {'id': 'call-4', 'tool': 'execute', 'state': {'status': 'completed', 'input': {
                'code': 'await tools.blendrelay.capture_focused_view({name: "Pyramid"})'}}},
        }))
        metrics.feed(json.dumps({
            'type': 'tool_use',
            'part': {'id': 'call-5', 'tool': 'execute', 'state': {'status': 'completed', 'input': {
                'code': 'await tools.blendrelay.inspect_scene({})'}}},
        }))
        self.assertEqual(metrics.summary()['tool_calls_by_stage'],
                         {'editing': 1, 'verification': 1, 'inspection': 1})


if __name__ == '__main__':
    unittest.main()
