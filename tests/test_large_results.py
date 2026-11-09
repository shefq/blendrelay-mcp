"""Regression tests for large results, long tasks and gateway responsiveness."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from blendrelay_blender import transport
from blendrelay_mcp import server
from blendrelay_runtime.service import Service


class ImageByPathTests(unittest.TestCase):
    def test_gateway_reads_images_from_runtime_directory_only(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            inside = Path(root) / 'workspaces' / 'w' / 'focused_views' / 'v.jpg'
            inside.parent.mkdir(parents=True)
            inside.write_bytes(b'jpeg-bytes')
            foreign = Path(outside) / 'secret.jpg'
            foreign.write_bytes(b'not for the agent')
            text = Path(root) / 'notes.txt'
            text.write_text('no')
            raw = {'operation_id': 'f', 'status': 'complete', 'result': {'views': [], 'images': [
                {'path': str(inside), 'mime_type': 'image/jpeg'},
                {'path': str(foreign), 'mime_type': 'image/jpeg'},
                {'path': str(text), 'mime_type': 'image/jpeg'},
                {'path': str(Path(root) / '..' / Path(outside).name / 'secret.jpg'), 'mime_type': 'image/jpeg'}]}}
            out = server.tool_result('capture_focused_view', raw, root)
            images = [c for c in out['content'] if c['type'] == 'image']
            self.assertEqual([base64.b64decode(c['data']) for c in images], [b'jpeg-bytes'])
            self.assertNotIn('images', out['structuredContent']['result'])

    def test_viewport_capture_by_path(self):
        with tempfile.TemporaryDirectory() as root:
            shot = Path(root) / 'captures' / 'a.png'
            shot.parent.mkdir()
            shot.write_bytes(b'png')
            raw = {'operation_id': 's', 'status': 'complete',
                   'result': {'image_path': str(shot), 'path': str(shot), 'mime_type': 'image/png'}}
            out = server.tool_result('capture_viewport', raw, root)
            self.assertEqual(out['content'][0], {'type': 'image', 'data': base64.b64encode(b'png').decode(), 'mimeType': 'image/png'})


class LongTaskTests(unittest.TestCase):
    def test_task_outlives_old_fixed_poll_count(self):
        class Client:
            polls = 0
            def call(self, method, args):
                Client.polls += 1
                return {'operation_id': 'slow', 'status': 'running' if Client.polls < 200 else 'complete', 'result': {'ok': True}}
        with mock.patch.object(server, 'poll_interval', lambda elapsed: .001):
            raw = server.wait_async('get_scene_info', {'operation_id': 'slow', 'status': 'running'},
                                    Client(), threading.Event(), time.monotonic() + 30)
        self.assertEqual(raw['status'], 'complete')

    def test_task_result_is_compacted_like_a_direct_call(self):
        class Client:
            def call(self, method, args):
                return {'operation_id': 'op', 'instance_id': 'b', 'action': 'execute', 'status': 'complete',
                        'result': {'ok': True}, 'arguments': {'code': 'x'}, 'fingerprint': 'f',
                        'created_at': 1.0, 'poll_count': 3}
        raw = server.wait_async('execute_blender_python', {'operation_id': 'op', 'status': 'running'},
                                Client(), threading.Event(), time.monotonic() + 30)
        out = server.tool_result('execute_blender_python', raw)
        self.assertFalse(out['isError'])
        self.assertEqual(out['structuredContent']['result'], {'ok': True})

    def test_poll_schedule_stays_under_runtime_status_limit(self):
        elapsed, polls = 0.0, 0
        while elapsed < server.MAX_TASK_TTL / 1000:
            elapsed += server.poll_interval(elapsed); polls += 1
        self.assertLess(polls, server.MAX_TASK_POLLS)
        self.assertLess(polls, 1200)

    def test_cancel_reaches_queued_blender_job(self):
        calls = []
        class Client:
            def call(self, method, args):
                calls.append(method)
                return {'operation_id': 'q', 'status': 'queued'}
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(server.DomainError):
            server.wait_async('get_scene_info', {'operation_id': 'q', 'status': 'queued'}, Client(), cancel, time.monotonic() + 30)
        self.assertEqual(calls, ['blender.cancel'])


class RuntimeCancelTests(unittest.TestCase):
    def test_cancel_only_affects_jobs_blender_has_not_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            service.dispatch('blender.poll', {'instance_id': 'b'})
            service.dispatch('blender.submit', {'operation_id': 'one', 'action': 'inspect'})
            self.assertEqual(service.dispatch('blender.cancel', {'operation_id': 'one'})['status'], 'cancelled')
            self.assertIsNone(service.dispatch('blender.poll', {'instance_id': 'b'})['command'])
            service.dispatch('blender.submit', {'operation_id': 'two', 'action': 'inspect'})
            service.dispatch('blender.poll', {'instance_id': 'b'})
            self.assertEqual(service.dispatch('blender.cancel', {'operation_id': 'two'})['status'], 'running')


class ResponsiveGatewayTests(unittest.TestCase):
    def test_ping_is_answered_while_a_tool_call_waits(self):
        class SlowClient:
            root = None
            def __init__(self, *args, **kwargs): pass
            def call(self, method, args):
                if method == 'budget.claim':
                    return {'enforced': False}
                time.sleep(1.5)
                return {'product': 'x', 'version': '1', 'mcp_protocol': 'p', 'general_blender': {}}
        lines = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'blendrelay_get_capabilities', 'arguments': {}}},
                 {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}]
        stdin = io.StringIO(''.join(json.dumps(m) + '\n' for m in lines))
        stdout = io.StringIO()
        with mock.patch.object(server, 'Client', SlowClient), mock.patch.object(sys, 'stdin', stdin), \
                mock.patch.object(sys, 'stdout', stdout), mock.patch.object(sys, 'argv', ['server', '--no-autostart']):
            server.main()
        order = [json.loads(line)['id'] for line in stdout.getvalue().splitlines()]
        self.assertEqual(order, [2, 1])


class CompatibilityTests(unittest.TestCase):
    def test_images_are_embedded_for_older_runtimes_within_budget(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for name in ('a', 'b', 'c'):
                path = Path(root) / (name + '.jpg'); path.write_bytes(name.encode() * 300); paths.append(str(path))
            result = {'views': [], 'images': [{'path': p, 'mime_type': 'image/jpeg'} for p in paths]}
            embedded = transport.inline_images(result, budget=900)
            self.assertEqual([base64.b64decode(i['data']) for i in embedded['images'] if 'data' in i], [b'a' * 300, b'b' * 300])
            self.assertNotIn('data', embedded['images'][2])
            self.assertNotIn('data', result['images'][0])  # caller's result is not mutated
            shot = transport.inline_images({'image_path': paths[0], 'mime_type': 'image/png'})
            self.assertEqual(base64.b64decode(shot['image']), b'a' * 300)
            # An old gateway reads the embedded data; the new one uses either.
            self.assertEqual(server.tool_result('capture_viewport', {'operation_id': 'x', 'status': 'complete', 'result': shot})['content'][0]['type'], 'image')

    def test_oversized_or_unencodable_ack_becomes_a_small_error(self):
        big = transport.bounded_ack({'operation_id': 'x', 'result': {'text': 'x' * 1_000_000}, 'error': None})
        self.assertTrue(big['result']['failed'])
        self.assertLess(len(json.dumps(big)), 2000)
        odd = transport.bounded_ack({'operation_id': 'y', 'result': {'value': object()}, 'error': None})
        json.dumps(odd)

    def test_runtime_advertises_image_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            self.assertIn(transport.IMAGE_PATHS_FEATURE, service.dispatch('blender.poll', {'instance_id': 'b'})['features'])


class PollAccountingTests(unittest.TestCase):
    def test_gateway_waits_do_not_use_the_agent_status_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            service.dispatch('blender.poll', {'instance_id': 'b'})
            service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'inspect'})
            for _ in range(1500):
                service.dispatch('blender.job', {'operation_id': 'op', 'internal': True})
            self.assertEqual(service.dispatch('blender.job', {'operation_id': 'op'})['poll_count'], 1)

    def test_agents_cannot_mark_their_own_polls_internal(self):
        out = server.handle({'method': 'tools/call', 'params': {'name': 'blendrelay_blender_job',
                             'arguments': {'operation_id': 'op', 'internal': True}}}, object())
        self.assertTrue(out['isError'])
        self.assertIn('unknown fields', out['content'][0]['text'])


class ShutdownTests(unittest.TestCase):
    def test_closing_stdin_waits_once_not_per_request(self):
        class SlowClient:
            root = None
            def __init__(self, *args, **kwargs): pass
            def call(self, method, args):
                if method == 'budget.claim':
                    return {'enforced': False}
                time.sleep(5)
                return {}
        lines = ''.join(json.dumps({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                        'params': {'name': 'blendrelay_get_capabilities', 'arguments': {}}}) + chr(10) for i in range(4))
        with mock.patch.object(server, 'Client', SlowClient), mock.patch.object(server, 'SHUTDOWN_GRACE', 1), \
                mock.patch.object(sys, 'stdin', io.StringIO(lines)), mock.patch.object(sys, 'stdout', io.StringIO()), \
                mock.patch.object(sys, 'argv', ['server', '--no-autostart']):
            started = time.monotonic()
            server.main()
        self.assertLess(time.monotonic() - started, 2.5)


if __name__ == '__main__':
    unittest.main()
