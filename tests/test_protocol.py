import socket
import threading
import unittest
from archforge_runtime.protocol import frame,receive
from archforge_mcp.server import handle


class ProtocolTests(unittest.TestCase):
    def test_fragmented_unicode_and_multiple_messages(self):
        a,b=socket.socketpair()
        messages=[{'text':'房屋 — façade 🏡'},{'text':'second'}]
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
        self.assertIn('archforge_blender_command',names)
        self.assertIn('archforge_blender_sessions',names)
        self.assertTrue({'get_asset_policy','search_assets','import_asset','list_cached_assets','refresh_asset_cache','asset_job'} <= names)
        self.assertNotIn('archforge_create_house',names)
        command=next(t for t in result['tools'] if t['name']=='archforge_blender_command')
        self.assertFalse(command['annotations']['destructiveHint'])
        self.assertTrue(all('_method' not in t for t in result['tools']))
