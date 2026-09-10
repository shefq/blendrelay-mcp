import tempfile
import unittest
from archforge_domain.model import DomainError
from archforge_runtime.service import Service


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(self.temp.name)
        self.project=self.service.rpc_project_create()['project_id']

    def tearDown(self):self.service.store.close();self.temp.cleanup()

    def plan(self):
        m=self.service.rpc_project_get(self.project)
        return self.service.rpc_plan_propose(self.project,operations=[{'kind':'material.assign','target_id':m['windows'][0]['id'],'parameters':{'material_id':'mat-black','role':'frame'}}])

    def attach_ready(self):
        self.service.rpc_session_attach(self.project,'blender')
        cmd=self.service.rpc_session_poll(self.project,'blender')['command']
        self.service.rpc_session_ack(self.project,'blender',cmd['id'],revision=1)

    def test_idempotent_commit_and_collision(self):
        p=self.plan();first=self.service.rpc_plan_apply(p['plan_id'],'op-1',1)
        second=self.service.rpc_plan_apply(p['plan_id'],'op-1',1)
        self.assertEqual(first,second);self.assertEqual(len(self.service.rpc_revision_list(self.project)),2)
        with self.assertRaises(DomainError) as e:self.service.rpc_plan_apply(p['plan_id'],'op-1',2)
        self.assertEqual(e.exception.code,'IDEMPOTENCY_CONFLICT')

    def test_stale_plan(self):
        a=self.plan();b=self.plan();self.service.rpc_plan_apply(a['plan_id'],'one',1)
        with self.assertRaises(DomainError):self.service.rpc_plan_apply(b['plan_id'],'two',1)

    def test_live_stage_then_commit(self):
        self.attach_ready();p=self.plan();op=self.service.rpc_plan_apply(p['plan_id'],'live',1)
        self.assertEqual(op['status'],'staging');self.assertEqual(self.service.rpc_project_get(self.project)['revision'],1)
        cmd=self.service.rpc_session_poll(self.project,'blender',revision=1)['command']
        self.service.rpc_session_ack(self.project,'blender',cmd['id'],revision=1)
        self.assertEqual(self.service.rpc_project_get(self.project)['revision'],2)
        cmd=self.service.rpc_session_poll(self.project,'blender',revision=1)['command']
        self.assertEqual(cmd['kind'],'commit')
        self.service.rpc_session_ack(self.project,'blender',cmd['id'],revision=2)
        self.assertEqual(self.service.rpc_job_get('live')['status'],'complete')

    def test_cancel_before_commit(self):
        self.attach_ready();p=self.plan();self.service.rpc_plan_apply(p['plan_id'],'cancel',1)
        self.service.rpc_job_cancel('cancel')
        self.assertEqual(self.service.rpc_job_get('cancel')['status'],'cancelled');self.assertEqual(self.service.rpc_project_get(self.project)['revision'],1)

    def test_projection_recovery_after_restart(self):
        self.attach_ready();p=self.plan();self.service.rpc_plan_apply(p['plan_id'],'crash',1)
        cmd=self.service.rpc_session_poll(self.project,'blender',revision=1)['command'];self.service.rpc_session_ack(self.project,'blender',cmd['id'],revision=1)
        self.service.store.close();self.service=Service(self.temp.name)
        self.assertEqual(self.service.rpc_job_get('crash')['status'],'recovery_required')
        self.service.rpc_session_attach(self.project,'again')
        cmd=self.service.rpc_session_poll(self.project,'again')['command'];self.service.rpc_session_ack(self.project,'again',cmd['id'],revision=2)
        self.assertEqual(self.service.rpc_job_get('crash')['status'],'complete')

    def test_uncommitted_restart_fails_without_mutation(self):
        self.attach_ready();p=self.plan();self.service.rpc_plan_apply(p['plan_id'],'before-crash',1)
        self.service.store.close();self.service=Service(self.temp.name)
        self.assertEqual(self.service.rpc_project_get(self.project)['revision'],1)
        self.assertEqual(self.service.rpc_job_get('before-crash')['status'],'failed')

    def test_divergence_blocks_edit(self):
        self.attach_ready();p=self.plan()
        self.service.rpc_session_poll(self.project,'blender',revision=1,diverged_ids=['wall-1'])
        with self.assertRaises(DomainError) as e:self.service.rpc_plan_apply(p['plan_id'],'bad',1)
        self.assertEqual(e.exception.code,'SCENE_DIVERGED')

    def test_restore_creates_new_revision(self):
        p=self.plan();self.service.rpc_plan_apply(p['plan_id'],'apply',1)
        restore=self.service.rpc_plan_restore(self.project,1);self.service.rpc_plan_apply(restore['plan_id'],'restore',2)
        self.assertEqual(self.service.rpc_project_get(self.project)['revision'],3)
        self.assertEqual(self.service.rpc_project_get(self.project)['windows'],self.service.rpc_project_get(self.project,1)['windows'])

    def test_artifact_read_cannot_read_secrets(self):
        from pathlib import Path
        p=Path(self.temp.name)/'connection.json';p.write_text('{"token":"secret"}')
        with self.assertRaises(DomainError):self.service.rpc_artifact_read(str(p))

    def test_saved_model_adoption_detects_conflicts(self):
        model=self.service.rpc_project_get(self.project)
        self.assertFalse(self.service.rpc_project_adopt(model)['adopted'])
        model['name']='Different saved file'
        with self.assertRaises(DomainError):self.service.rpc_project_adopt(model)
