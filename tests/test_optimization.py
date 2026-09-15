import json
import unittest
import tempfile
from pathlib import Path
from blendrelay_blender import conversation
from blendrelay_mcp.job_results import wait_and_compact
from blendrelay_mcp.server import handle
from blendrelay_blender.workflow import compact, choose, instructions, RESOURCE_MODE_ITEMS, resource_limits, profile
from blendrelay_blender.run_metrics import Metrics


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
        out=handle({'method':'tools/call','params':{'name':'execute_blender_python','arguments':{'code':'result=True'}}},c)
        self.assertTrue(out['isError'])
        c.call=lambda *args:dict(operation_id='image',status='complete',result={'image':'abc','mime_type':'image/png'})
        out=handle({'method':'tools/call','params':{'name':'capture_viewport','arguments':{}}},c)
        self.assertEqual(out['content'][0]['type'],'image')

    def test_python_edits_do_not_checkpoint_each_stage(self):
        client=FakeClient()
        handle({'method':'tools/call','params':{'name':'execute_blender_python','arguments':{'code':'result=True'}}},client)
        submit=next(args for method,args in client.calls if method=='blender.submit')
        self.assertNotIn('save_checkpoint',submit['arguments'])

    def test_batch_scene_tool_is_public(self):
        output=handle({'method':'tools/list','params':{}},FakeClient())
        names={tool['name'] for tool in output['tools']}
        self.assertIn('build_scene_batch',names)

    def test_compact_context(self):
        data={'vertices':[{'index':1,'local':[.123456789],'world':[123]}],
              'adjacent_vertices':[{'index':1,'local':[.1]},{'index':2,'local':[.2]}]}
        c=compact(data)
        self.assertNotIn('world',c['vertices'][0])
        self.assertEqual(len(c['adjacent_vertices']),1)
        self.assertEqual(c['vertices'][0]['local'],[.123457])
        self.assertEqual(choose('AUTO','Bevel these edges',True),'EDIT')
        self.assertEqual(choose('AUTO','Create a complete character',True),'CREATE')
        self.assertEqual(choose('AUTO','Rig this selected character',True),'RIGGING')
        self.assertEqual(choose('AUTO','Add cloth simulation',False),'SIMULATION')

    def test_workflow_instructions_include_call_controls(self):
        text=instructions('EDIT','instance','EDIT_CURVE',False,True,4,1,0)
        self.assertIn('starting allowance is 4 MCP calls and 1 accepted edits',text)
        self.assertIn('up to 0 times',text)
        self.assertIn('does not consume an edit',text)

    def test_four_resource_modes(self):
        self.assertEqual(len(RESOURCE_MODE_ITEMS),4)
        self.assertEqual(resource_limits('OBJECT_MATERIAL'),(15,4,10))
        self.assertEqual(resource_limits('FULL_BUILD'),(40,10,20))
        self.assertEqual(resource_limits('BUILD_ASSETS'),(60,15,30))
        self.assertEqual(resource_limits('COMPLEX_SCENE'),(100,25,40))
        maximum=profile('COMPLEX_SCENE','MAXIMUM')
        self.assertEqual((maximum['initial_calls'],maximum['initial_edits']),(135,34))
        self.assertEqual((maximum['max_calls'],maximum['max_edits']),(250,60))

    def test_complex_maximum_instructions_are_quality_driven(self):
        limits=profile('COMPLEX_SCENE','MAXIMUM')
        text=instructions('CREATE','instance',None,True,False,
            limits['initial_calls'],limits['initial_edits'],limits['polls_per_job'],
            'COMPLEX_SCENE','MAXIMUM',limits['verification_passes'],True)
        self.assertIn('Task category=CREATE',text)
        self.assertIn('execute_blender_python freely',text)
        self.assertIn('It expands automatically',text)
        self.assertIn('at least 4 useful view group',text)
        self.assertIn('Autonomous generation',text)

    def test_metrics_count_completed_once(self):
        m=Metrics()
        event={'event':'step_update','step_update':{'step_index':1,'state':'DONE','step_type':'tool',
               'tool_name':'call_mcp_tool','tool_info':{'parameters':{'Arguments':{'action':'inspect'}}}}}
        m.feed(json.dumps(event));m.feed(json.dumps(event))
        m.feed(json.dumps({'event':'result','result':{'usage':{'input_tokens':10,'output_tokens':2,'cache_read_tokens':100}}}))
        self.assertEqual(m.summary()['tool_calls_by_stage'],{'inspection':1})
        self.assertEqual(m.summary()['provider_usage']['input_tokens'],10)


if __name__=='__main__':unittest.main()
