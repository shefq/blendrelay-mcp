"""Explicit conversation IDs, scoped to a BlendRelay workspace and agent backend."""
import json
from pathlib import Path


def load(path):
    try: return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError, ValueError): return {}


def save(path, backend, identifier):
    path=Path(path)
    value=load(path)
    value[backend]=identifier
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value),encoding='utf8')
    tmp.replace(path)


def event_id(line):
    try: data=json.loads(line)
    except ValueError: return None
    return (data.get('thread_id') if data.get('type')=='thread.started' else
            data.get('step_update',{}).get('conversation_id') or data.get('result',{}).get('conversation_id'))
