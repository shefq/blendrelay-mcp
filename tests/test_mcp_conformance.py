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


if __name__ == '__main__':
    unittest.main()
