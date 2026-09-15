"""General Blender jobs and durable operation receipts."""
import hashlib
import json
import re
import time
from blendrelay_runtime.errors import DomainError
from blendrelay_blender.version import VERSION

LIVE_SESSION_SECONDS = 90
BUSY_SESSION_SECONDS = 15 * 60


class Bridge:
    def bridge_init(self):
        self.blender_sessions = {}
        self.bridge_dir = self.store.root / 'blender_jobs'
        self.bridge_dir.mkdir(exist_ok=True)
        self._cleanup_jobs(mark_uncertain=True)

    def _cleanup_jobs(self, mark_uncertain=False):
        """Expire old receipts and make restart-uncertain edits explicit."""
        cutoff = time.time() - 24 * 60 * 60
        for path in self.bridge_dir.glob('*.json'):
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if job.get('status') in ('complete', 'failed', 'interrupted', 'cancelled') and job.get('created_at', 0) < cutoff:
                path.unlink()
                continue
            if mark_uncertain and job.get('status') in ('queued', 'running'):
                job.update(status='interrupted', error='Runtime restarted. Inspect the scene and versions before submitting a new operation; this operation will not be replayed.')
                self._write_job(job)

    def _write_job(self, job):
        path = self.bridge_dir / (job['operation_id'] + '.json')
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job), encoding='utf-8')
        tmp.replace(path)

    def _job(self, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise DomainError('INVALID_ID', 'Use an opaque alphanumeric operation ID')
        path = self.bridge_dir / (operation_id + '.json')
        if not path.exists():
            raise DomainError('UNKNOWN_JOB', operation_id)
        return json.loads(path.read_text())

    def rpc_blender_job(self, operation_id, instance_id=None, **kwargs):
        job = self._job(operation_id)
        job['poll_count'] = job.get('poll_count', 0) + 1
        if job['poll_count'] > 1200 and job.get('status') in ('queued', 'running'):
            raise DomainError('POLL_LIMIT', 'This job exceeded the long-operation status limit; inspect Blender')
        self._write_job(job)
        return job

    def rpc_blender_sessions(self):
        return [dict(instance_id=k, **{f: v for f, v in s.items() if f != 'seen'})
                for k, s in self.blender_sessions.items() if time.monotonic() - s['seen'] < LIVE_SESSION_SECONDS]

    def rpc_blender_poll(self, instance_id, scene_name='', filepath='', selection=None, version=VERSION,
                         asset_policy=None, asset_scene=None, workspace_id=None, allow_python_execution=None,
                         agent_run_id=None, mcp_call_limit=40, edit_attempt_limit=10, job_poll_limit=20,
                         max_mcp_calls=120, max_edit_attempts=30, auto_extend=True, **kwargs):
        if not isinstance(instance_id, str) or not instance_id:
            raise DomainError('INVALID_INSTANCE', 'Instance ID is required')
        previous = self.blender_sessions.get(instance_id, {})
        same_run = bool(agent_run_id and previous.get('agent_run_id') == agent_run_id)
        self.blender_sessions[instance_id] = dict(seen=time.monotonic(), scene_name=scene_name,
                                                 filepath=filepath, selection=selection or [], version=version,
                                                 asset_policy=asset_policy, asset_scene=asset_scene, workspace_id=workspace_id,
                                                 allow_python_execution=(previous.get('allow_python_execution', False)
                                                     if allow_python_execution is None else bool(allow_python_execution)),
                                                 agent_run_id=agent_run_id,
                                                 mcp_call_limit=max(1, min(250, int(mcp_call_limit))),
                                                 edit_attempt_limit=max(1, min(60, int(edit_attempt_limit))),
                                                 job_poll_limit=max(1, min(120, int(job_poll_limit))),
                                                 max_mcp_calls=max(1, min(250, int(max_mcp_calls))),
                                                 max_edit_attempts=max(1, min(60, int(max_edit_attempts))),
                                                 auto_extend=bool(auto_extend),
                                                 mcp_calls=previous.get('mcp_calls', 0) if same_run else 0,
                                                 edit_attempts=previous.get('edit_attempts', 0) if same_run else 0,
                                                 job_polls=previous.get('job_polls', 0) if same_run else 0,
                                                 poll_counts=previous.get('poll_counts', {}) if same_run else {})
        # A dispatched job is never automatically executed twice.
        for path in sorted(self.bridge_dir.glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
            job = json.loads(path.read_text())
            if job['instance_id'] == instance_id and job['status'] == 'queued':
                job['status'] = 'running'
                self._write_job(job)
                return {'command': job}
        return {'command': None}

    def rpc_budget_claim(self, kind='other', instance_id=None, operation_id=None):
        if kind not in ('read', 'edit', 'poll', 'asset'):
            raise DomainError('INVALID_BUDGET_KIND', 'Unknown MCP budget category')
        sessions = self.rpc_blender_sessions()
        if instance_id is None:
            if len(sessions) == 1:
                instance_id = sessions[0]['instance_id']
            elif kind == 'poll':
                active = [key for key, value in self.blender_sessions.items() if value.get('agent_run_id')]
                if len(active) == 1:
                    instance_id = active[0]
            if instance_id is None:
                return {'enforced': False, 'reason': 'Select a Blender instance to enforce its run budget'}
        session = self.blender_sessions.get(instance_id)
        # Polls read durable job receipts, which remain valid while Blender is busy
        # or briefly disconnected. New work still requires a live session in submit.
        if not session or (kind != 'poll' and time.monotonic() - session['seen'] >= LIVE_SESSION_SECONDS):
            raise DomainError('BLENDER_OFFLINE', 'Reconnect the BlendRelay panel')
        if not session.get('agent_run_id'):
            return {'enforced': False, 'reason': 'No add-on agent task is active'}
        if session['mcp_calls'] >= session['mcp_call_limit'] and session.get('auto_extend'):
            session['mcp_call_limit'] = min(session['max_mcp_calls'], max(session['mcp_call_limit'] + 10, round(session['mcp_call_limit'] * 1.5)))
        if session['mcp_calls'] >= session['mcp_call_limit']:
            raise DomainError('MCP_CALL_LIMIT', 'The emergency MCP call ceiling was reached')
        if kind in ('edit', 'asset') and session['edit_attempts'] >= session['edit_attempt_limit'] and session.get('auto_extend'):
            session['edit_attempt_limit'] = min(session['max_edit_attempts'], max(session['edit_attempt_limit'] + 3, round(session['edit_attempt_limit'] * 1.5)))
        if kind in ('edit', 'asset') and session['edit_attempts'] >= session['edit_attempt_limit']:
            raise DomainError('EDIT_ATTEMPT_LIMIT', 'The emergency edit ceiling was reached')
        if kind == 'poll' and operation_id:
            count = session['poll_counts'].get(operation_id, 0)
            if count >= session['job_poll_limit']:
                raise DomainError('JOB_POLL_LIMIT', 'This operation reached its status-check limit')
            session['poll_counts'][operation_id] = count + 1
        session['mcp_calls'] += 1
        if kind == 'poll':
            session['job_polls'] += 1
        return {'enforced': True, 'remaining_calls': session['mcp_call_limit'] - session['mcp_calls'],
                'remaining_edits': session['edit_attempt_limit'] - session['edit_attempts'],
                'remaining_polls': max(0, session['job_poll_limit'] - session['poll_counts'].get(operation_id, 0))}

    def rpc_budget_commit(self, kind='edit', instance_id=None):
        if kind not in ('edit', 'asset'):
            raise DomainError('INVALID_BUDGET_KIND', 'Only accepted edits are committed')
        session = self.blender_sessions.get(instance_id)
        if not session or not session.get('agent_run_id'):
            return {'enforced': False}
        session['edit_attempts'] += 1
        return {'enforced': True, 'edit_attempts': session['edit_attempts']}

    def rpc_blender_submit(self, operation_id=None, action=None, arguments=None, instance_id=None):
        self._cleanup_jobs()
        if action not in ('inspect', 'inspect_data', 'execute', 'execute_code', 'build_batch', 'get_scene_info', 'get_object_info', 'versions', 'checkpoint', 'restore', 'screenshot', 'capture_focused_view', 'mesh_edit', 'validate_selection'):
            raise DomainError('UNKNOWN_ACTION', action)
        # Auto-generate a unique operation_id if not supplied
        if not operation_id:
            operation_id = 'auto-' + hashlib.sha256(
                json.dumps({'action': action, 'arguments': arguments or {}, 't': time.time()}, sort_keys=True).encode()
            ).hexdigest()[:16]
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise DomainError('INVALID_ID', 'Use an opaque alphanumeric operation ID')
        if arguments is not None and not isinstance(arguments, dict):
            raise DomainError('INVALID_ARGUMENTS', 'Expected an object')
        sessions = self.rpc_blender_sessions()
        if instance_id is None:
            candidates = [s['instance_id'] for s in sessions]
            if not candidates:
                candidates = [key for key in self.blender_sessions if any(
                    job.get('instance_id') == key and job.get('status') == 'running' and time.time() - job.get('created_at', 0) < BUSY_SESSION_SECONDS
                    for job in (json.loads(path.read_text()) for path in self.bridge_dir.glob('*.json')))]
            if len(candidates) != 1:
                raise DomainError('SELECT_INSTANCE', 'Connect Blender; if several instances are open, specify instance_id')
            instance_id = candidates[0]
        live = any(s['instance_id'] == instance_id for s in sessions)
        busy = any(job.get('instance_id') == instance_id and job.get('status') == 'running' and time.time() - job.get('created_at', 0) < BUSY_SESSION_SECONDS
                 for job in (json.loads(path.read_text()) for path in self.bridge_dir.glob('*.json')))
        if not live and not busy:
            raise DomainError('BLENDER_OFFLINE', 'Reconnect the BlendRelay panel')
        session = self.blender_sessions[instance_id]
        if action in ('execute', 'execute_code') and not session.get('allow_python_execution'):
            raise DomainError('PYTHON_EXECUTION_DISABLED', 'Enable “Allow arbitrary Python execution” in BlendRelay Settings for this scene')
        payload = dict(action=action, arguments=arguments or {}, instance_id=instance_id)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        path = self.bridge_dir / (operation_id + '.json')
        if path.exists():
            old = self._job(operation_id)
            if old['fingerprint'] != fingerprint:
                raise DomainError('ID_REUSED', 'Operation ID was already used with different arguments')
            return old
        job = dict(operation_id=operation_id, instance_id=instance_id, action=action, arguments=arguments or {},
                 fingerprint=fingerprint, status='queued', created_at=time.time(), poll_count=0)
        self._write_job(job)
        return job

    def rpc_blender_ack(self, instance_id, operation_id, result=None, error=None):
        job = self._job(operation_id)
        if job['instance_id'] != instance_id:
            raise DomainError('WRONG_INSTANCE', 'Job belongs to another Blender instance')
        if job['status'] in ('complete', 'failed'):
            return job
        job.update(status='failed' if error else 'complete', result=result, error=error)
        self._write_job(job)
        return {'operation_id': operation_id, 'status': job['status']}
