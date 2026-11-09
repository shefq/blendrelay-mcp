"""Regression tests for two failures agents hit in real spray-bottle runs."""
import base64
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from blendrelay_mcp import server
from blendrelay_mcp.validation import SchemaError, validate
from blendrelay_runtime.errors import BlendRelayError
from blendrelay_runtime.service import Service

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')


class ReadGeneratedArtifactTests(unittest.TestCase):
    def write(self, root, *parts):
        path = Path(root, *parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
        return path

    def test_workspace_previews_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            for folder in ('focused_views', 'selection_views', 'captures'):
                path = self.write(tmp, 'workspaces', 'abc123', folder, 'view-1.png')
                result = service.rpc_artifact_read(str(path))
                self.assertEqual(result['mime_type'], 'image/png')
                self.assertEqual(base64.b64decode(result['data']), PNG)

    def test_shared_previews_still_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'focused_views', 'old.png')
            self.assertEqual(Service(tmp).rpc_artifact_read(str(path))['mime_type'], 'image/png')

    def test_other_workspace_files_stay_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            for parts in (('workspaces', 'x.png'),
                          ('FORM_Studio.png',)):
                path = self.write(tmp, *parts)
                with self.assertRaises(BlendRelayError, msg=str(parts)) as caught:
                    service.rpc_artifact_read(str(path))
                self.assertEqual(caught.exception.code, 'ARTIFACT_NOT_ALLOWED')

    def test_render_saved_in_scene_workspace_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'workspaces', 'abc123', 'verdant-studio-final.png')
            self.assertEqual(Service(tmp).rpc_artifact_read(str(path))['mime_type'], 'image/png')

    def test_render_in_workspace_subfolder_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'workspaces', 'abc123', 'renders', 'ATELIER_VERDE_Studio.png')
            self.assertEqual(Service(tmp).rpc_artifact_read(str(path))['mime_type'], 'image/png')

    def test_non_image_in_scene_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, 'workspaces', 'abc123', 'scene.blend')
            with self.assertRaises(BlendRelayError) as caught:
                Service(tmp).rpc_artifact_read(str(path))
            self.assertEqual(caught.exception.code, 'UNSUPPORTED_ARTIFACT')

    def test_traversal_out_of_preview_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            self.write(tmp, 'secret.png')
            sneaky = Path(tmp, 'workspaces', 'abc123', 'captures', '..', '..', '..', 'secret.png')
            with self.assertRaises(BlendRelayError):
                service.rpc_artifact_read(str(sneaky))


class PaddingTests(unittest.TestCase):
    def schema(self):
        return server.TOOLS['capture_focused_view']['inputSchema']['properties']['padding']

    def test_scale_factor_padding_passes_validation(self):
        for value in (0, 0.15, 1.25, 1.65):
            validate(value, self.schema(), 'arguments.padding')

    def test_range_error_names_the_bounds(self):
        with self.assertRaisesRegex(SchemaError, r'minimum 0, maximum 3'):
            validate(5, self.schema(), 'arguments.padding')


if __name__ == '__main__':
    unittest.main()


class BackgroundRuntimeStartTests(unittest.TestCase):
    """Agent CLIs drop a server's tools when it is slow to answer at startup."""

    def test_starter_does_not_block_and_requests_wait_for_it(self):
        import threading, time
        from unittest import mock
        release = threading.Event()
        calls = []

        def slow_start(root):
            calls.append(root)
            release.wait(5)

        with mock.patch.object(server, 'ensure_runtime', side_effect=slow_start):
            started = time.monotonic()
            starter = server.RuntimeStarter('root')
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertFalse(starter.ready)
            waiter = threading.Thread(target=starter.wait)
            waiter.start()
            time.sleep(0.1)
            self.assertTrue(waiter.is_alive(), 'requests must wait for the runtime start')
            release.set()
            waiter.join(5)
            self.assertFalse(waiter.is_alive())
            self.assertTrue(starter.ready)
            starter.wait()
        self.assertEqual(calls, ['root'])

    def test_failed_background_start_is_retried_by_first_request(self):
        from unittest import mock
        outcomes = [OSError('first start failed'), None]

        def flaky(root):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome

        with mock.patch.object(server, 'ensure_runtime', side_effect=flaky):
            starter = server.RuntimeStarter('root')
            starter.wait()
        self.assertTrue(starter.ready)
        self.assertEqual(outcomes, [])

    def test_disabled_starter_never_starts_runtime(self):
        from unittest import mock
        with mock.patch.object(server, 'ensure_runtime') as ensure:
            server.RuntimeStarter('root', enabled=False).wait()
        ensure.assert_not_called()


class OperationIdReuseTests(unittest.TestCase):
    """Agents pick readable IDs such as spray_inspect_01 again in later runs."""

    def finish(self, service, instance, op):
        service.dispatch('blender.poll', {'instance_id': instance})
        service.dispatch('blender.ack', {'instance_id': instance, 'operation_id': op, 'result': {'ok': 1}})

    def test_finished_id_from_another_blender_session_is_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            service.dispatch('blender.poll', {'instance_id': 'old'})
            service.dispatch('blender.submit', {'operation_id': 'spray_inspect_01', 'action': 'inspect', 'instance_id': 'old'})
            self.finish(service, 'old', 'spray_inspect_01')
            service.dispatch('blender.poll', {'instance_id': 'new'})
            job = service.dispatch('blender.submit', {'operation_id': 'spray_inspect_01', 'action': 'inspect', 'instance_id': 'new'})
            self.assertEqual((job['instance_id'], job['status']), ('new', 'queued'))

    def test_same_session_reuse_with_new_arguments_is_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            service.dispatch('blender.poll', {'instance_id': 'b'})
            service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'inspect', 'instance_id': 'b'})
            self.finish(service, 'b', 'op')
            with self.assertRaises(BlendRelayError) as caught:
                service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'screenshot', 'instance_id': 'b'})
            self.assertEqual(caught.exception.code, 'ID_REUSED')

    def test_running_job_of_another_session_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            service.dispatch('blender.poll', {'instance_id': 'old'})
            service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'inspect', 'instance_id': 'old'})
            service.dispatch('blender.poll', {'instance_id': 'new'})
            with self.assertRaises(BlendRelayError) as caught:
                service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'inspect', 'instance_id': 'new'})
            self.assertEqual(caught.exception.code, 'ID_REUSED')

    def test_identical_retry_returns_the_existing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(tmp)
            service.dispatch('blender.poll', {'instance_id': 'b'})
            first = service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'inspect', 'instance_id': 'b'})
            again = service.dispatch('blender.submit', {'operation_id': 'op', 'action': 'inspect', 'instance_id': 'b'})
            self.assertEqual(first['created_at'], again['created_at'])


class RuntimeLaunchTests(unittest.TestCase):
    # Mock only the module's platform view. Mutating os.name also changes
    # pathlib globally and makes Linux try to instantiate WindowsPath.
    def test_posix_launch_keeps_native_paths_and_skips_windows_launcher(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server, 'Client') as client_cls, \
                mock.patch.object(server, 'os', SimpleNamespace(name='posix')), \
                mock.patch.object(server, '_start_outside_job') as outside, \
                mock.patch.object(server.subprocess, 'Popen') as popen:
            client_cls.return_value.call.side_effect = [OSError('offline'), {'ok': True}]
            server.ensure_runtime(tmp)
        outside.assert_not_called()
        popen.assert_called_once()

    def test_windows_prefers_launch_outside_agent_job(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server, 'Client') as client_cls, \
                mock.patch.object(server, 'os', SimpleNamespace(name='nt')), \
                mock.patch.object(server, '_start_outside_job', return_value=True) as outside, \
                mock.patch.object(server.subprocess, 'Popen') as popen:
            client_cls.return_value.call.side_effect = [OSError('offline'), {'ok': True}]
            server.ensure_runtime(tmp)
        outside.assert_called_once()
        popen.assert_not_called()

    def test_falls_back_to_child_process_when_wmi_is_unavailable(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server, 'Client') as client_cls, \
                mock.patch.object(server, 'os', SimpleNamespace(name='nt')), \
                mock.patch.object(server, '_start_outside_job', return_value=False), \
                mock.patch.object(server.subprocess, 'Popen') as popen:
            client_cls.return_value.call.side_effect = [OSError('offline'), {'ok': True}]
            server.ensure_runtime(tmp)
        popen.assert_called_once()


class LargeArtifactImageTests(unittest.TestCase):
    """Agent clients cap a tool result at ~1 MiB; full renders must still be viewable."""

    def raw(self, data, mime='image/png'):
        import base64 as b64
        return {'mime_type': mime, 'offset': 0, 'eof': True, 'total_bytes': len(data), 'data': b64.b64encode(data).decode()}

    def big_png(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow not installed')
        import io, os
        image = Image.frombytes('RGB', (1400, 1700), os.urandom(1400 * 1700 * 3))
        out = io.BytesIO(); image.save(out, 'PNG')
        self.assertGreater(out.tell(), server.MAX_INLINE_IMAGE_BYTES)
        return out.getvalue()

    def test_small_image_is_returned_unchanged(self):
        out = server.tool_result('blendrelay_read_artifact', self.raw(PNG))
        self.assertEqual(out['content'][0], {'type': 'image', 'data': base64.b64encode(PNG).decode(), 'mimeType': 'image/png'})
        self.assertNotIn('note', out['structuredContent'])

    def test_large_render_becomes_preview_under_client_limit(self):
        out = server.tool_result('blendrelay_read_artifact', self.raw(self.big_png()))
        image = out['content'][0]
        self.assertEqual((image['type'], image['mimeType']), ('image', 'image/jpeg'))
        self.assertLessEqual(len(base64.b64decode(image['data'])), server.MAX_INLINE_IMAGE_BYTES)
        self.assertIn('1400x1700', out['structuredContent']['note'])

    def test_without_pillow_large_image_returns_short_note(self):
        from unittest import mock
        big = PNG + b'\0' * (server.MAX_INLINE_IMAGE_BYTES + 1)
        with mock.patch.object(server, 'shrink_image', return_value=None):
            out = server.tool_result('blendrelay_read_artifact', self.raw(big))
        self.assertEqual([c['type'] for c in out['content']], ['text'])
        self.assertLess(len(out['content'][0]['text']), 2000)
        self.assertIn('too large', out['structuredContent']['note'])


class MinimumWaitTests(unittest.TestCase):
    """A floor on agent waits cuts "still running" turns without slowing quick jobs."""

    class FakeClient:
        def __init__(self, finish_after):
            import time
            self.start = time.monotonic(); self.finish_after = finish_after; self.calls = 0
        def call(self, method, args):
            import time
            self.calls += 1
            done = time.monotonic() - self.start >= self.finish_after
            return {'operation_id': args.get('operation_id', 'op'), 'instance_id': 'b', 'action': 'execute',
                    'status': 'complete' if done else 'running', 'error': None, 'result': {'ok': 1} if done else None}

    def wait(self, finish_after, wait_seconds, min_wait):
        import time
        from blendrelay_mcp.job_results import wait_and_compact
        client = self.FakeClient(finish_after)
        started = time.monotonic()
        out = wait_and_compact(client, 'blender.job', {'operation_id': 'op', 'wait_seconds': wait_seconds}, min_wait=min_wait)
        return out, time.monotonic() - started

    def test_quick_job_returns_as_soon_as_it_finishes(self):
        out, took = self.wait(finish_after=0.3, wait_seconds=1, min_wait=20)
        self.assertEqual(out['status'], 'complete')
        self.assertLess(took, 1.5)

    def test_running_job_is_waited_on_for_at_least_the_floor(self):
        out, took = self.wait(finish_after=60, wait_seconds=1, min_wait=2)
        self.assertEqual(out['status'], 'running')
        self.assertGreaterEqual(took, 1.9)
        self.assertLess(took, 3.5)

    def test_longer_agent_wait_is_kept(self):
        out, took = self.wait(finish_after=2.5, wait_seconds=5, min_wait=1)
        self.assertEqual(out['status'], 'complete')
        self.assertGreaterEqual(took, 2.4)

    def test_floor_applies_to_agent_calls_but_not_task_mode(self):
        from unittest import mock
        seen = []
        def fake_wait(client, method, arguments, min_wait=0):
            seen.append((method, min_wait, arguments.get('wait_seconds')))
            return {'operation_id': 'op', 'status': 'running'}
        definition = server.TOOLS['execute_blender_python']
        with mock.patch.object(server, 'wait_and_compact', side_effect=fake_wait):
            server.invoke_raw(definition, {'code': 'x=1', 'wait_seconds': 1}, object())
            server.invoke_raw(definition, {'code': 'x=1', 'wait_seconds': 1}, object(), task_mode=True)
            server.invoke_raw(server.TOOLS['blendrelay_blender_job'], {'operation_id': 'op', 'wait_seconds': 1}, object())
        self.assertEqual(seen, [('blender.submit', server.MIN_AGENT_WAIT_SECONDS, 1),
                                ('blender.submit', 0, 0),
                                ('blender.job', server.MIN_AGENT_WAIT_SECONDS, 1)])


class RunMetricsTests(unittest.TestCase):
    def test_claude_calls_with_different_arguments_are_not_flagged_as_repeats(self):
        import json as j
        from blendrelay_blender.run_metrics import Metrics
        m = Metrics()
        for i in range(3):
            block = {'type': 'tool_use', 'id': f't{i}', 'name': 'mcp__blendrelay__execute_blender_python',
                     'input': {'operation_id': f'op-{i}', 'code': f'x={i}'}}
            # The streaming start event (empty input) arrives first, then the full message.
            m.feed(j.dumps({'type': 'stream_event', 'event': {'type': 'content_block_start', 'content_block': dict(block, input={})}}))
            m.feed(j.dumps({'type': 'assistant', 'message': {'content': [block]}}))
        summary = m.summary()
        self.assertEqual(summary['warnings'], [])
        self.assertEqual(summary['tool_calls_by_stage'], {'editing': 3})

    def test_real_repeats_are_still_flagged(self):
        import json as j
        from blendrelay_blender.run_metrics import Metrics
        m = Metrics()
        for i in range(3):
            m.feed(j.dumps({'type': 'item.completed', 'item': {'id': f'i{i}', 'type': 'mcp_tool_call',
                     'tool': 'blendrelay_blender_job', 'arguments': {'operation_id': 'render-01', 'wait_seconds': 1}}}))
        self.assertEqual(len(m.summary()['warnings']), 1)
        self.assertEqual(m.summary()['tool_calls_by_stage'], {'waiting': 3})


class AntigravityMetricsTests(unittest.TestCase):
    def test_wrapped_mcp_calls_are_staged_by_their_tool_name(self):
        import json as j
        from blendrelay_blender.run_metrics import Metrics
        m = Metrics()
        for i, tool in enumerate(('execute_blender_python', 'capture_focused_view', 'inspect_scene')):
            m.feed(j.dumps({'event': 'step_update', 'step_update': {'step_index': i, 'state': 'DONE', 'step_type': 'tool',
                    'tool_name': 'call_mcp_tool', 'tool_info': {'parameters': {'ServerName': 'blendrelay', 'ToolName': tool,
                    'Arguments': {'instance_id': 'b'}}}}}))
        self.assertEqual(m.summary()['tool_calls_by_stage'], {'editing': 1, 'verification': 1, 'inspection': 1})
