import tempfile
import unittest
from archforge_runtime.service import Service
from archforge_domain.model import DomainError


class GeneralBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=Service(self.tmp.name)
        self.service.dispatch('blender.poll',{'instance_id':'test','scene_name':'Sculpture'})

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
