# SPDX-License-Identifier: GPL-3.0-or-later
"""How job results travel back to the runtime. No Blender dependency."""
import base64
import json
from pathlib import Path

# Runtime frames are capped at 1 MiB; keep headroom for the envelope.
ACK_BUDGET = 900_000
# Runtimes that advertise this feature come with a gateway that reads capture
# images from disk. Older ones only understand images embedded in the result.
IMAGE_PATHS_FEATURE = 'image_paths'
LEGACY_INLINE_BUDGET = 700_000


def _encode(path):
    try:
        return base64.b64encode(Path(path).read_bytes()).decode('ascii')
    except (OSError, TypeError):
        return None


def inline_images(result, budget=LEGACY_INLINE_BUDGET):
    """Embed capture images for an older runtime/gateway, within a size budget.

    Images beyond the budget are left as paths only, so the acknowledgement
    always fits the runtime frame; the remaining images are still on disk.
    """
    if not isinstance(result, dict):
        return result
    result = dict(result)
    used = 0
    if result.get('image_path') and not result.get('image'):
        data = _encode(result['image_path'])
        if data and len(data) <= budget:
            result['image'] = data
            used += len(data)
    if isinstance(result.get('images'), list):
        images = []
        for entry in result['images']:
            if isinstance(entry, dict) and entry.get('path') and not entry.get('data'):
                data = _encode(entry['path'])
                if data and used + len(data) <= budget:
                    entry = dict(entry, data=data)
                    used += len(data)
            images.append(entry)
        result['images'] = images
    return result


def bounded_ack(ack):
    """Make a job acknowledgement always deliverable.

    An acknowledgement that cannot be encoded, or that exceeds the runtime's
    1 MiB frame, would otherwise be retried forever and the result lost.
    """
    try:
        size = len(json.dumps(ack, ensure_ascii=True))
    except (TypeError, ValueError):
        ack = dict(ack, result=json.loads(json.dumps(ack.get('result'), default=str)))
        size = len(json.dumps(ack, ensure_ascii=True))
    if size <= ACK_BUDGET:
        return ack
    error = (f'Result too large to return ({size:,} bytes; limit {ACK_BUDGET:,}). '
             'The Blender operation itself ran; return a smaller result (summarise or page the data).')
    return dict(ack, result={'failed': True, 'truncated': True, 'error': error}, error=error)
