"""Wait at the gateway, leaving the runtime free to receive Blender acknowledgements."""
import hashlib
import json
import time

TERMINAL = {'complete', 'failed', 'interrupted', 'cancelled'}


def wait_and_compact(client, method, arguments):
    args = dict(arguments)
    wait = args.pop('wait_seconds', 8)
    repeat = args.pop('repeat_result', False)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 <= wait <= 30:
        raise ValueError('wait_seconds must be between 0 and 30')
    job = client.call(method, args)
    deadline = time.monotonic() + wait
    while job.get('status') in {'queued', 'running'} and time.monotonic() < deadline:
        # A moderate cadence keeps long Blender operations below the runtime's
        # abuse guard while still returning short edits promptly.
        time.sleep(min(.5, max(0, deadline-time.monotonic())))
        job = client.call('blender.job', {'operation_id': job['operation_id'], 'instance_id': job.get('instance_id')})
    compact = {k: job[k] for k in ('operation_id', 'instance_id', 'action', 'status', 'error', 'result') if k in job}
    failed = isinstance(compact.get('result'), dict) and (compact['result'].get('failed') or compact['result'].get('executed') is False)
    if failed:
        compact['status'] = 'failed'
    delivered = getattr(client, '_archforge_delivered', {})
    key = (job.get('instance_id'), job['operation_id'])
    fingerprint = hashlib.sha256(json.dumps(compact, sort_keys=True, default=str).encode()).hexdigest()
    if not repeat and delivered.get(key) == fingerprint:
        compact.pop('result', None)
        compact['unchanged'] = True
        compact['next_step'] = ('Result already delivered. Do not poll a terminal job; use repeat_result=true only if needed.'
                                if compact.get('status') in TERMINAL else 'Still running. Wait before checking again; do not resubmit the edit.')
    delivered[key] = fingerprint
    if len(delivered) > 256:
        delivered.pop(next(iter(delivered)))
    client._archforge_delivered = delivered
    return compact
