"""Run with Blender --background --factory-startup --python this_file."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import bpy
import bmesh
from archforge_domain.model import house
from archforge_domain.operations import propose
from archforge_domain.geometry import build_specs
from archforge_blender import geometry
from archforge_blender import ui

ui.register();assert bpy.app.timers.is_registered(ui.timer);ui.unregister()
assert not bpy.app.timers.is_registered(ui.timer)
model=house()
for _ in geometry.stage(model,build_specs(model)):pass
assert not geometry.managed(model['project_id'])
geometry.commit(1)
objects=geometry.managed(model['project_id']);assert len(objects)==len(build_specs(model))
assert not geometry.diverged(model['project_id'])
for obj in objects:
    bm=bmesh.new();bm.from_mesh(obj.data)
    assert all(e.is_manifold for e in bm.edges),(obj.name,'non-manifold edge')
    bm.free()
window=model['windows'][0];opening=next(o for o in model['openings'] if o['id']==window['opening_id'])
candidate,_,_=propose(model,[{'kind':'opening.resize','target_id':opening['id'],'parameters':{'width_m':opening['width_m']+.2}}])
untouched=next(o for o in objects if o.get('archforge_entity_id')==model['assets'][0]['id'])
pointer=untouched.as_pointer()
for _ in geometry.stage(candidate,build_specs(candidate)):pass
geometry.commit(2)
assert untouched.as_pointer()==pointer
assert bpy.context.scene['archforge_revision']==2
assert len(geometry.managed(model['project_id']))==len(build_specs(candidate))
assert not geometry.diverged(model['project_id'])
untouched.location.x+=.2;bpy.context.view_layer.update()
assert untouched['archforge_entity_id'] in geometry.diverged(model['project_id'])
try:
    for _ in geometry.stage(candidate,build_specs(candidate)):pass
    raise AssertionError('Diverged scene should not stage')
except RuntimeError:pass
for _ in geometry.stage(candidate,build_specs(candidate),force=True):pass
geometry.commit(2);assert not geometry.diverged(model['project_id'])
for _ in geometry.stage(candidate,build_specs(candidate)):pass
live=geometry.managed(model['project_id'])[0];live.location.x+=.1;bpy.context.view_layer.update()
try:
    geometry.commit(3)
    raise AssertionError('A manual edit during staging must stop projection')
except RuntimeError:pass
geometry.discard()
for _ in geometry.stage(candidate,build_specs(candidate),force=True):pass
geometry.commit(2)
print('ARCHFORGE_BLENDER_TESTS_OK',json.dumps({'objects':len(geometry.managed(model['project_id'])),'revision':2}))
