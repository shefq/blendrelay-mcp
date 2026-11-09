"""Wait at the gateway, leaving the runtime free to receive Blender acknowledgements."""
import hashlib
import json
import threading
import time

TERMINAL = {'complete', 'failed', 'interrupted', 'cancelled'}
# Short jobs usually finish within a few hundred milliseconds, so check early
# and often, then settle at a moderate cadence that keeps long Blender
# operations well below the runtime's per-job status limit.
POLL_STEPS = (.05, .05, .1, .1)
POLL_FAST, POLL_FAST_SECONDS = .25, 3
POLL_SETTLED = .5
_DELIVERED_LOCK = threading.Lock()
# Agents often ask for 1 s waits, so a 50 s render costs many "still running"
# replies, each a full model turn. Waiting returns as soon as the job finishes,
# so a floor only affects jobs that are genuinely still running. It stays well
# below agent clients' tool time limits (Codex: 60 s).
MIN_AGENT_WAIT_SECONDS = 20


def wait_and_compact(client, method, arguments, min_wait=0):
    """Submit or look up a job and wait up to max(wait_seconds, min_wait) for it to finish."""
    args = dict(arguments)
    wait = args.pop('wait_seconds', 8)
    repeat = args.pop('repeat_result', False)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 <= wait <= 30:
        raise ValueError('wait_seconds must be between 0 and 30')
    wait = max(wait, min_wait)
    job = client.call(method, args)
    started = time.monotonic()
    deadline = started + wait
    polls = 0
    while job.get('status') in {'queued', 'running'} and time.monotonic() < deadline:
        step = (POLL_STEPS[polls] if polls < len(POLL_STEPS) else
                POLL_FAST if time.monotonic() - started < POLL_FAST_SECONDS else POLL_SETTLED)
        time.sleep(min(step, max(0, deadline-time.monotonic())))
        polls += 1
        job = client.call('blender.job', {'operation_id': job['operation_id'], 'instance_id': job.get('instance_id'),
                                          'internal': True})
    compact = {k: job[k] for k in ('operation_id', 'instance_id', 'action', 'status', 'error', 'result') if k in job}
    failed = isinstance(compact.get('result'), dict) and (compact['result'].get('failed') or compact['result'].get('executed') is False)
    if failed:
        compact['status'] = 'failed'
    key = (job.get('instance_id'), job['operation_id'])
    fingerprint = hashlib.sha256(json.dumps(compact, sort_keys=True, default=str).encode()).hexdigest()
    with _DELIVERED_LOCK:
        delivered = getattr(client, '_blendrelay_delivered', None)
        if delivered is None:
            delivered = client._blendrelay_delivered = {}
        already = delivered.get(key) == fingerprint
        delivered[key] = fingerprint
        if len(delivered) > 256:
            delivered.pop(next(iter(delivered)))
    if not repeat and already:
        compact.pop('result', None)
        compact['unchanged'] = True
        compact['next_step'] = ('Result already delivered. Do not poll a terminal job; use repeat_result=true only if needed.'
                                if compact.get('status') in TERMINAL else 'Still running. Wait before checking again; do not resubmit the edit.')
    return compact
