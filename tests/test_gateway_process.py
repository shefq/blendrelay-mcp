import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


class GatewayProcessTests(unittest.TestCase):
    def test_stdio_gateway_autostarts_authenticated_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
                      {'jsonrpc':'2.0','method':'notifications/initialized'},
                      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'archforge_get_capabilities','arguments':{}}}]
            try:
                result=subprocess.run([sys.executable,'-m','archforge_mcp.server','--data-dir',tmp],input='\n'.join(json.dumps(m) for m in messages)+'\n',capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                responses=[json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual([r['id'] for r in responses],[1,2,3])
                self.assertFalse(responses[2]['result']['isError'])
                capabilities=responses[2]['result']['structuredContent']
                self.assertEqual(capabilities['mcp_protocol'],'2026-07-28')
                from archforge_runtime.protocol import receive,frame
                import socket
                cfg=json.loads((Path(tmp)/'connection.json').read_text())
                with socket.create_connection(('127.0.0.1',cfg['port']),timeout=2) as sock:
                    sock.sendall(frame({'protocol_version':1,'token':'wrong','method':'capabilities','request_id':'denied'}))
                    self.assertEqual(receive(sock)['error']['code'],'UNAUTHORIZED')
            finally:
                file=Path(tmp)/'connection.json'
                if file.exists():
                    cfg=json.loads(file.read_text())
                    try:os.kill(cfg['pid'],signal.SIGTERM)
                    except ProcessLookupError:pass
                    # Give Windows time to release SQLite/log handles.
                    import time
                    time.sleep(.2)
