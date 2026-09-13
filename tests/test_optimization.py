import json
import unittest
import tempfile
from pathlib import Path
from archforge_blender import conversation
from archforge_mcp.job_results import wait_and_compact
from archforge_mcp.server import handle
from archforge_blender.workflow import compact, choose, instructions
from archforge_blender.run_metrics import Metrics


class FakeClient:
    def __init__(self): self.calls=[]
    def call(self, method, args):
        self.calls.append((method,args))
        return dict(operation_id='test',instance_id='b',action='execute',
                    status='queued' if method=='blender.submit' else 'complete',
                    arguments={'code':'huge script'},fingerprint='internal',result={'executed':True})


class OptimizationTests(unittest.TestCase):
    def test_conversation_backend_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'conversations.json'
            conversation.save(path,'CODEX','codex-thread')
            conversation.save(path,'ANTIGRAVITY','agy-thread')
            conversation.save(path,'CODEX',None)
            self.assertEqual(conversation.load(path),{'CODEX':None,'ANTIGRAVITY':'agy-thread'})
        self.assertEqual(conversation.event_id('{"type":"thread.started","thread_id":"abc"}'),'abc')

    def test_wait_and_duplicate(self):
        c=FakeClient()
        result=wait_and_compact(c,'blender.submit',{'action':'execute','wait_seconds':1})
        self.assertEqual(result['status'],'complete')
        self.assertNotIn('arguments',result)
        self.assertNotIn('wait_seconds',c.calls[0][1])
        repeat=wait_and_compact(c,'blender.job',{'operation_id':'test'})
        self.assertTrue(repeat['unchanged'])
        self.assertNotIn('result',repeat)
        self.assertIn('result',wait_and_compact(c,'blender.job',{'operation_id':'test','repeat_result':True}))

    def test_failures_and_image_on_submit(self):
        c=FakeClient()
        c.call=lambda *args:dict(operation_id='bad',status='complete',result={'executed':False,'error':'bad mesh'})
        out=handle({'method':'tools/call','params':{'name':'archforge_blender_command','arguments':{'action':'execute'}}},c)
        self.assertTrue(out['isError'])
        c.call=lambda *args:dict(operation_id='image',status='complete',result={'image':'abc','mime_type':'image/png'})
        out=handle({'method':'tools/call','params':{'name':'archforge_blender_command','arguments':{'action':'screenshot'}}},c)
        self.assertEqual(out['content'][0]['type'],'image')

    def test_compact_context(self):
        data={'vertices':[{'index':1,'local':[.123456789],'world':[123]}],
              'adjacent_vertices':[{'index':1,'local':[.1]},{'index':2,'local':[.2]}]}
        c=compact(data)
        self.assertNotIn('world',c['vertices'][0])
        self.assertEqual(len(c['adjacent_vertices']),1)
        self.assertEqual(c['vertices'][0]['local'],[.123457])
        self.assertEqual(choose('AUTO','Bevel these edges',True),'EDIT')
        self.assertEqual(choose('AUTO','Build a house',True),'BUILD')

    def test_workflow_instructions_include_call_controls(self):
        text=instructions('EDIT','instance',False,False,True,4,1,0)
        self.assertIn('no more than 4 total ArchForge MCP tool calls',text)
        self.assertIn('no more than 1 edit attempt',text)
        self.assertIn('no more than 0 status poll',text)

    def test_metrics_count_completed_once(self):
        m=Metrics()
        event={'event':'step_update','step_update':{'step_index':1,'state':'DONE','step_type':'tool',
               'tool_name':'call_mcp_tool','tool_info':{'parameters':{'Arguments':{'action':'inspect'}}}}}
        m.feed(json.dumps(event));m.feed(json.dumps(event))
        m.feed(json.dumps({'event':'result','result':{'usage':{'input_tokens':10,'output_tokens':2,'cache_read_tokens':100}}}))
        self.assertEqual(m.summary()['tool_calls_by_stage'],{'inspection':1})
        self.assertEqual(m.summary()['provider_usage']['input_tokens'],10)


if __name__=='__main__':unittest.main()
