"""Run with Blender --background --factory-startup --python tests/blender_general.py."""
import json
from pathlib import Path
import sys
import uuid
from contextlib import nullcontext
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import bpy
from blendrelay_blender import general, register, unregister

test_root = Path(__file__).resolve().parents[1] / '.test-output'
test_root.mkdir(exist_ok=True)
with nullcontext(test_root / ('general-' + uuid.uuid4().hex)) as root:
    register()
    before_names = set(bpy.data.objects.keys())
    result = general.execute(root, "bpy.ops.mesh.primitive_monkey_add(location=(2,3,4))\nbpy.context.object.name='General sculpture'\nresult={'created':bpy.context.object.name}", 'Sculpture', save_checkpoint=True)
    assert result['result']['created'] == 'General sculpture'
    assert before_names.issubset(set(bpy.data.objects.keys()))
    original = result['before']['version_id']
    assert len(general.versions(root)) == 2
    failed = general.execute(root, "bpy.data.objects['General sculpture'].location.z=20\nraise ValueError('deliberate failure')", 'Failed edit', save_checkpoint=True)
    assert failed['failed'] and bpy.data.objects['General sculpture'].location.z == 20
    general.restore(root, original)
    assert 'General sculpture' not in bpy.data.objects
    assert len(general.versions(root)) == 4
    assert bpy.app.timers.is_registered(__import__('blendrelay_blender.bridge_ui', fromlist=['timer']).timer)
    unregister()
    print('BLENDRELAY_GENERAL_OK: arbitrary mesh, preservation, failure checkpoint, full-scene restore, persistent timer')
