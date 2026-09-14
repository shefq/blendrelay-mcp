import tempfile
import time
import unittest
from archforge_runtime.service import Service
from archforge_domain.model import DomainError


class GeneralBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=Service(self.tmp.name)
        self.service.dispatch('blender.poll',{'instance_id':'test','scene_name':'Sculpture','allow_python_execution':True})

    def tearDown(self):
        self.service.store.close();self.tmp.cleanup()

    def test_dispatch_once_and_receipt(self):
        args=dict(operation_id='edit-1',action='execute',arguments={'code':'import bpy\nbpy.ops.mesh.primitive_monkey_add()'})
        job=self.service.dispatch('blender.submit',args)
        self.assertEqual(job['status'],'queued')
        command=self.service.dispatch('blender.poll',{'instance_id':'test'})['command']
        self.assertEqual(command['operation_id'],'edit-1')
        self.assertIsNone(self.service.dispatch('blender.poll',{'instance_id':'test'})['command'])
        self.assertEqual(self.service.dispatch('blender.submit',args)['status'],'running')
        self.service.dispatch('blender.ack',dict(instance_id='test',operation_id='edit-1',result={'objects':1}))
        self.assertEqual(self.service.dispatch('blender.submit',args)['result'],{'objects':1})
        with self.assertRaises(DomainError):self.service.dispatch('blender.submit',{**args,'action':'restore'})

    def test_restart_never_replays_uncertain_edit(self):
        self.service.dispatch('blender.submit',dict(operation_id='uncertain',action='execute'))
        self.service.dispatch('blender.poll',{'instance_id':'test'})
        self.service.store.close();self.service=Service(self.tmp.name)
        self.assertEqual(self.service.dispatch('blender.job',{'operation_id':'uncertain'})['status'],'interrupted')
        self.assertIsNone(self.service.dispatch('blender.poll',{'instance_id':'test'})['command'])

    def test_multiple_instances_require_choice_and_correct_ack(self):
        self.service.dispatch('blender.poll',{'instance_id':'second'})
        with self.assertRaises(DomainError):self.service.dispatch('blender.submit',dict(operation_id='a',action='inspect'))
        self.service.dispatch('blender.submit',dict(operation_id='a',action='inspect',instance_id='test'))
        with self.assertRaises(DomainError):self.service.dispatch('blender.ack',dict(operation_id='a',instance_id='second'))

    def test_python_execution_requires_scene_approval(self):
        self.service.dispatch('blender.poll',{'instance_id':'test','allow_python_execution':False})
        with self.assertRaises(DomainError) as raised:
            self.service.dispatch('blender.submit',{'action':'execute','arguments':{'code':'pass'},'instance_id':'test'})
        self.assertEqual(raised.exception.code,'PYTHON_EXECUTION_DISABLED')

    def test_active_run_budget_is_enforced(self):
        self.service.dispatch('blender.poll',{'instance_id':'test','allow_python_execution':True,
            'agent_run_id':'run-a','mcp_call_limit':2,'edit_attempt_limit':1,'job_poll_limit':1,
            'max_mcp_calls':2,'max_edit_attempts':1,'auto_extend':False})
        self.service.dispatch('budget.claim',{'kind':'edit','instance_id':'test'})
        self.service.dispatch('budget.commit',{'kind':'edit','instance_id':'test'})
        with self.assertRaises(DomainError) as raised:
            self.service.dispatch('budget.claim',{'kind':'edit','instance_id':'test'})
        self.assertEqual(raised.exception.code,'EDIT_ATTEMPT_LIMIT')

    def test_budget_extends_while_run_is_active(self):
        self.service.dispatch('blender.poll',{'instance_id':'test','agent_run_id':'run-a',
            'mcp_call_limit':1,'edit_attempt_limit':1,'job_poll_limit':10,
            'max_mcp_calls':5,'max_edit_attempts':4,'auto_extend':True})
        self.service.dispatch('budget.claim',{'kind':'edit','instance_id':'test'})
        self.service.dispatch('budget.commit',{'kind':'edit','instance_id':'test'})
        result=self.service.dispatch('budget.claim',{'kind':'edit','instance_id':'test'})
        self.assertTrue(result['enforced'])
        self.assertGreaterEqual(self.service.blender_sessions['test']['mcp_call_limit'],2)

    def test_poll_budget_is_per_operation(self):
        self.service.dispatch('blender.poll',{'instance_id':'test','agent_run_id':'run-a',
            'mcp_call_limit':10,'edit_attempt_limit':2,'job_poll_limit':1,'auto_extend':False})
        self.service.dispatch('budget.claim',{'kind':'poll','instance_id':'test','operation_id':'one'})
        with self.assertRaises(DomainError) as raised:
            self.service.dispatch('budget.claim',{'kind':'poll','instance_id':'test','operation_id':'one'})
        self.assertEqual(raised.exception.code,'JOB_POLL_LIMIT')
        self.assertTrue(self.service.dispatch('budget.claim',{'kind':'poll','instance_id':'test','operation_id':'two'})['enforced'])

    def test_completed_job_can_be_polled_after_session_heartbeat_ages_out(self):
        self.service.dispatch('blender.poll',{'instance_id':'test','agent_run_id':'run-a',
            'mcp_call_limit':3,'edit_attempt_limit':1,'job_poll_limit':2})
        self.service.dispatch('blender.submit',{'operation_id':'late-result','action':'inspect','instance_id':'test'})
        self.service.dispatch('blender.poll',{'instance_id':'test','agent_run_id':'run-a',
            'mcp_call_limit':3,'edit_attempt_limit':1,'job_poll_limit':2})
        self.service.dispatch('blender.ack',{'instance_id':'test','operation_id':'late-result','result':{'objects':0}})
        self.service.blender_sessions['test']['seen']=time.monotonic()-100
        claim=self.service.dispatch('budget.claim',{'kind':'poll','instance_id':'test'})
        self.assertTrue(claim['enforced'])
        self.assertEqual(self.service.dispatch('blender.job',{'operation_id':'late-result'})['status'],'complete')

    def test_new_job_still_requires_a_live_session(self):
        self.service.blender_sessions['test']['seen']=time.monotonic()-100
        with self.assertRaises(DomainError) as raised:
            self.service.dispatch('blender.submit',{'operation_id':'offline','action':'inspect','instance_id':'test'})
        self.assertEqual(raised.exception.code,'BLENDER_OFFLINE')

    def test_capture_focused_view_action_accepted(self):
        job = self.service.dispatch('blender.submit', {
            'operation_id': 'foc-test',
            'action': 'capture_focused_view',
            'arguments': {'target_objects': ['Cube']},
            'instance_id': 'test'
        })
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['action'], 'capture_focused_view')

