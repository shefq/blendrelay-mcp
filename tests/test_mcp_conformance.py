"""MCP spec conformance for tool definitions and results (GitHub issue #2).

MCP requires Tool.outputSchema to have "type": "object" and
CallToolResult.structuredContent to be a JSON object. Strict clients
(e.g. some Claude Code versions) reject the whole tool list otherwise.
"""
import json
import unittest

from blendrelay_mcp import server


class ToolSchemaConformanceTests(unittest.TestCase):
    def test_every_output_schema_is_an_object(self):
        tools = server.handle({'method': 'tools/list'}, object())['tools']
        bad = [t['name'] for t in tools if 'outputSchema' in t and t['outputSchema'].get('type') != 'object']
        self.assertEqual(bad, [])

    def test_every_input_schema_is_an_object(self):
        tools = server.handle({'method': 'tools/list'}, object())['tools']
        self.assertEqual([t['name'] for t in tools if t['inputSchema'].get('type') != 'object'], [])


class SessionsResultTests(unittest.TestCase):
    session = {'instance_id': 'abc', 'scene_name': 'Scene', 'filepath': '', 'selection': [], 'version': '0.7.8'}

    class FakeClient:
        root = None
        def __init__(self, sessions):
            self.sessions = sessions
        def call(self, method, args=None):
            return self.sessions if method == 'blender.sessions' else {}  # budget bookkeeping

    def call(self, sessions):
        return server.handle({'method': 'tools/call', 'params': {'name': 'blendrelay_blender_sessions', 'arguments': {}}},
                             self.FakeClient(sessions))

    def test_sessions_are_returned_as_an_object(self):
        result = self.call([self.session])
        self.assertEqual(result['structuredContent'], {'sessions': [self.session]})
        self.assertFalse(result['isError'])

    def test_text_content_matches_structured_content(self):
        result = self.call([self.session])
        self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])

    def test_no_connected_blender_is_an_empty_list(self):
        self.assertEqual(self.call([])['structuredContent'], {'sessions': []})


class StdioInputTests(unittest.TestCase):
    """Running the server by hand in a terminal must not answer blank lines with errors."""

    def run_gateway(self, text):
        import os, subprocess, sys, tempfile
        from pathlib import Path
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run([sys.executable, '-m', 'blendrelay_mcp.server', '--data-dir', tmp, '--no-autostart'],
                                  input=text, capture_output=True, text=True, timeout=15, env=env)

    def test_blank_lines_are_ignored(self):
        ping = json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'ping'})
        result = self.run_gateway('\n \n\t\n' + ping + '\n\n')
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses, [{'jsonrpc': '2.0', 'id': 7, 'result': {}}])

    def test_invalid_json_still_gets_a_parse_error(self):
        result = self.run_gateway('not json\n')
        self.assertEqual(json.loads(result.stdout)['error']['code'], -32700)

    def test_no_terminal_hint_when_driven_by_a_client(self):
        # Piped stdin (how MCP clients run the server) never prints the hint.
        self.assertNotIn('waiting for an MCP client', self.run_gateway('').stderr)

    def test_terminal_detection(self):
        class Tty:
            def isatty(self): return True
        class Broken:
            def isatty(self): raise ValueError('closed')
        self.assertTrue(server.interactive(Tty()))
        self.assertFalse(server.interactive(Broken()))
        self.assertFalse(server.interactive(object()))


if __name__ == '__main__':
    unittest.main()
