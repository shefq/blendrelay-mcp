import socket
import threading
import time
import unittest
from archforge_runtime.protocol import frame,receive
from archforge_mcp.server import handle


class ProtocolTests(unittest.TestCase):
    def test_fragmented_unicode_and_multiple_messages(self):
        a,b=socket.socketpair()
        messages=[{'text':'角色 — 動畫 🎬'},{'text':'second'}]
        raw=b''.join(frame(m) for m in messages)
        def write():
            with a:
                for n in range(0,len(raw),3):a.sendall(raw[n:n+3])
        t=threading.Thread(target=write);t.start()
        with b:self.assertEqual([receive(b),receive(b)],messages)
        t.join()

    def test_mcp_initialize_and_tools(self):
        init=handle({'method':'initialize','params':{'protocolVersion':'2025-11-25'}},None)
        self.assertEqual(init['serverInfo']['name'],'archforge-mcp')
        result=handle({'method':'tools/list'},None)
        names={t['name'] for t in result['tools']}
        self.assertIn('execute_blender_python',names)
        self.assertIn('inspect_scene',names)
        self.assertIn('inspect_blender_data', names)
        self.assertIn('mesh_edit',names)
        self.assertIn('capture_viewport',names)
        self.assertIn('capture_focused_view',names)
        self.assertIn('archforge_blender_sessions',names)
        self.assertTrue({'get_asset_policy','search_assets','import_asset','list_cached_assets','refresh_asset_cache','asset_job'} <= names)
        self.assertNotIn('project_create', names)
        command=next(t for t in result['tools'] if t['name']=='execute_blender_python')
        self.assertTrue(command['annotations']['destructiveHint'])
        self.assertEqual(command['execution']['taskSupport'],'optional')
        self.assertIn('outputSchema',command)
        focused=next(t for t in result['tools'] if t['name']=='capture_focused_view')
        self.assertFalse(focused['annotations']['destructiveHint'])
        self.assertTrue(focused['annotations']['readOnlyHint'])
        self.assertTrue(all('_method' not in t for t in result['tools']))

    def test_current_protocol_discovery(self):
        result=handle({'method':'server/discover','params':{}},None)
        self.assertEqual(result['protocolVersion'],'2026-07-28')
        self.assertIn('io.modelcontextprotocol/tasks',result['capabilities']['extensions'])

    def test_strict_tool_schema_rejects_unknown_fields(self):
        class Client:
            def call(self, method, args):raise AssertionError('invalid input reached runtime')
        result=handle({'method':'tools/call','params':{'name':'mesh_edit',
            'arguments':{'operation':'bevel','invented_option':True}}},Client())
        self.assertTrue(result['isError'])
        self.assertIn('unknown fields',result['content'][0]['text'])

    def test_optional_task_lifecycle(self):
        class Client:
            def call(self,method,args):
                if method=='budget.claim':return {'enforced':False}
                return {'operation_id':'task-op','status':'complete','result':{'objects':2}}
        created=handle({'method':'tools/call','params':{'name':'inspect_scene','arguments':{},
            'task':{'ttl':10000}}},Client())
        task_id=created['task']['taskId']
        for _ in range(100):
            status=handle({'method':'tasks/get','params':{'taskId':task_id}},Client())
            if status['status']!='working':break
            time.sleep(.01)
        self.assertEqual(status['status'],'completed')
        result=handle({'method':'tasks/result','params':{'taskId':task_id}},Client())
        self.assertEqual(result['structuredContent']['result'],{'objects':2})

    def test_capture_focused_view_tool_result_multi_images(self):
        class Client:
            def call(self, method, args):
                if method == 'budget.claim': return {'enforced': False}
                return {
                    'operation_id': 'foc-1',
                    'status': 'complete',
                    'result': {
                        'resolved_objects': ['Cube'],
                        'missing_objects': [],
                        'views': [{
                            'image_path': '/path/to/view-1.jpg',
                            'camera_matrix_world': [1.0]*16,
                            'camera_position': [0, -5, 2],
                            'target_center': [0, 0, 0],
                            'target_dimensions': [2, 2, 2],
                            'frame_coverage': 0.8,
                            'visibility_score': 1.0,
                            'fully_framed': True,
                            'occluders': [],
                            'adjustments': ['Framed with 15% margin'],
                        }],
                        'scene_state_restored': True,
                        'images': [
                            {'data': 'aW1hZ2Ux', 'mime_type': 'image/jpeg'},
                            {'data': 'aW1hZ2Uy', 'mime_type': 'image/jpeg'}
                        ]
                    }
                }
        out = handle({'method': 'tools/call', 'params': {'name': 'capture_focused_view', 'arguments': {'target_objects': ['Cube']}}}, Client())
        self.assertFalse(out['isError'])
        self.assertEqual(len(out['content']), 3)
        self.assertEqual(out['content'][0]['type'], 'image')
        self.assertEqual(out['content'][0]['data'], 'aW1hZ2Ux')
        self.assertEqual(out['content'][1]['type'], 'image')
        self.assertEqual(out['content'][1]['data'], 'aW1hZ2Uy')
        self.assertEqual(out['content'][2]['type'], 'text')
        self.assertNotIn('images', out['structuredContent']['result'])
        self.assertEqual(out['structuredContent']['result']['resolved_objects'], ['Cube'])

    def test_capture_focused_view_rejects_missing_targets(self):
        class Client:
            def call(self, method, args): raise AssertionError('Should reject before client.call')
        out = handle({'method': 'tools/call', 'params': {'name': 'capture_focused_view', 'arguments': {}}}, Client())
        self.assertTrue(out['isError'])
        self.assertIn('missing', out['content'][0]['text'])

