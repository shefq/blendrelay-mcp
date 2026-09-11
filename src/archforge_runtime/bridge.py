"""General Blender jobs, independent of architectural schemas."""
import hashlib
import json
import re
import time
from archforge_domain.model import DomainError


class Bridge:
    def bridge_init(self):
        self.blender_sessions = {}
        self.bridge_dir = self.store.root / 'blender_jobs'
        self.bridge_dir.mkdir(exist_ok=True)
        for path in self.bridge_dir.glob('*.json'):
            job = json.loads(path.read_text())
            if job['status'] in ('queued', 'running'):
                job.update(status='interrupted', error='Runtime restarted. Inspect the scene and versions before submitting a new operation; this operation will not be replayed.')
                self._write_job(job)

    def _write_job(self, job):
        path = self.bridge_dir / (job['operation_id'] + '.json')
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job), encoding='utf-8')
        tmp.replace(path)

    def rpc_blender_job(self, operation_id, instance_id=None, **kwargs):
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise DomainError('INVALID_ID', 'Use an opaque alphanumeric operation ID')
        path = self.bridge_dir / (operation_id + '.json')
        if not path.exists():
            raise DomainError('UNKNOWN_JOB', operation_id)
        return json.loads(path.read_text())

    def rpc_blender_sessions(self):
        return [dict(instance_id=k, **{f:v for f,v in s.items() if f != 'seen'})
                for k,s in self.blender_sessions.items() if time.monotonic()-s['seen'] < 30]

    def rpc_blender_poll(self, instance_id, scene_name='', filepath='', selection=None, version='0.2.1'):
        if not isinstance(instance_id, str) or not instance_id:
            raise DomainError('INVALID_INSTANCE', 'Instance ID is required')
        self.blender_sessions[instance_id] = dict(seen=time.monotonic(), scene_name=scene_name,
                                                 filepath=filepath, selection=selection or [], version=version)
        # A dispatched job is never automatically executed twice.
        for path in sorted(self.bridge_dir.glob('*.json'), key=lambda p:p.stat().st_mtime_ns):
            job=json.loads(path.read_text())
            if job['instance_id']==instance_id and job['status']=='queued':
                job['status']='running';self._write_job(job)
                return {'command':job}
        return {'command':None}

    def rpc_blender_submit(self, operation_id=None, action=None, arguments=None, instance_id=None):
        if action not in ('inspect','execute','execute_code','get_scene_info','get_object_info','versions','checkpoint','restore','screenshot','mesh_edit','validate_selection'):
            raise DomainError('UNKNOWN_ACTION',action)
        # Auto-generate a unique operation_id if not supplied
        if not operation_id:
            operation_id = 'auto-' + hashlib.sha256(
                json.dumps({'action': action, 'arguments': arguments or {}, 't': time.time()}, sort_keys=True).encode()
            ).hexdigest()[:16]
        if not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation_id):
            raise DomainError('INVALID_ID','Use an opaque alphanumeric operation ID')
        if arguments is not None and not isinstance(arguments,dict):
            raise DomainError('INVALID_ARGUMENTS','Expected an object')
        payload=dict(action=action,arguments=arguments or {},instance_id=instance_id)
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        path=self.bridge_dir/(operation_id+'.json')
        if path.exists():
            old=self.rpc_blender_job(operation_id)
            if old['fingerprint']!=fingerprint:raise DomainError('ID_REUSED','Operation ID was already used with different arguments')
            return old
        sessions=self.rpc_blender_sessions()
        if instance_id is None:
            if len(sessions)!=1:raise DomainError('SELECT_INSTANCE','Connect Blender; if several instances are open, specify instance_id')
            instance_id=sessions[0]['instance_id']
        if not any(s['instance_id']==instance_id for s in sessions):raise DomainError('BLENDER_OFFLINE','Reconnect the ArchForge panel')
        job=dict(operation_id=operation_id,instance_id=instance_id,action=action,arguments=arguments or {},
                 fingerprint=fingerprint,status='queued',created_at=time.time())
        self._write_job(job)
        return job

    def rpc_blender_ack(self, instance_id, operation_id, result=None, error=None):
        job=self.rpc_blender_job(operation_id)
        if job['instance_id']!=instance_id:raise DomainError('WRONG_INSTANCE','Job belongs to another Blender instance')
        if job['status'] in ('complete','failed'):return job
        job.update(status='failed' if error else 'complete',result=result,error=error)
        self._write_job(job)
        return {'operation_id':operation_id,'status':job['status']}
