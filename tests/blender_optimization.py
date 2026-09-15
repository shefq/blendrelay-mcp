"""Run: blender --background --factory-startup --python tests/blender_optimization.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import bpy, bmesh
from blendrelay_blender import bridge_ui as ui, mesh_tools, workflow, general
ui.refresh_models_async = lambda: None
ui.sketch.register_overlay = lambda: None
ui.viewport_hud.register_hud = lambda: None
ui.register()
previous_prompt = bpy.context.scene.blendrelay_codex_prompt
previous_backend = bpy.context.scene.blendrelay_agent_backend
bpy.context.scene.blendrelay_agent_backend = 'CODEX'
bpy.context.scene.blendrelay_codex_prompt = '   '
assert ui.viewport_hud._submit_agent_prompt(bpy.context) is False
assert ui.viewport_hud.HUD_STATE['typing'] is True
assert ui.viewport_hud.HUD_STATE['flash_msg'] == 'Enter a prompt for Codex'
ui.viewport_hud.HUD_STATE['typing'] = False
bpy.context.scene.blendrelay_codex_prompt = previous_prompt
bpy.context.scene.blendrelay_agent_backend = previous_backend
assert bpy.context.scene.blendrelay_task_mode == 'AUTO'
assert bpy.context.scene.blendrelay_resource_mode == 'FULL_BUILD'
assert bpy.context.scene.blendrelay_verification == 'AUTO'
assert bpy.context.scene.blendrelay_output_quality == 'HIGH'
assert bpy.context.scene.blendrelay_permission_mode == 'AUTONOMOUS'
assert bpy.context.scene.blendrelay_allow_python_execution is True
assert bpy.context.scene.blendrelay_allow_antigravity_mcp is True
assert bpy.context.scene.blendrelay_unattended_agent_permissions is True
assert bpy.context.scene.blendrelay_hud_adaptive is True
wide_metrics = ui.viewport_hud._hud_layout_metrics(1600, 900, 780, 1.0, adaptive=True)
assert round(wide_metrics['width']) == 1280 and round(wide_metrics['height']) == 129
metrics = ui.viewport_hud._hud_layout_metrics(600, 300, 780, 1.0, adaptive=True)
assert round(metrics['width']) == 480 and round(metrics['height']) == 79 and metrics['auto_scaled']
manual = ui.viewport_hud._hud_layout_metrics(
    1600, 900, wide_metrics['base_width'], wide_metrics['scale'], adaptive=False
)
assert manual['scale'] == wide_metrics['scale'] and round(manual['width']) == round(wide_metrics['width'])
batch = general.build_batch([
    {'type': 'collection', 'name': 'Batch Scene'},
    {'type': 'material', 'name': 'Batch Material', 'color': [.4, .3, .2, 1], 'roughness': .8},
    {'type': 'cube', 'name': 'Batch Object', 'collection': 'Batch Scene', 'dimensions': [4, 3, 5], 'material': 'Batch Material', 'bevel': .1},
    {'type': 'camera', 'name': 'Batch Camera', 'location': [8, -8, 6], 'target': [0, 0, 1]},
    {'type': 'area_light', 'name': 'Batch Key Light', 'location': [2, -3, 8], 'energy': 800},
])
assert batch['executed'] and 'Batch Object' in batch['created']
assert bpy.data.objects['Batch Object'].data.materials[0].name == 'Batch Material'
assert bpy.data.objects['Batch Object'].name in bpy.data.collections['Batch Scene'].objects

br_result = general.execute_code("br.cube('Helper Cube BR', location=(6, 0, 1), dimensions=(1, 1, 2)); result='ok'")
assert br_result['executed'] and br_result['result'] == 'ok'
assert br_result['progress']['created_object_count'] == 1

bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
result = mesh_tools.edit('bevel', distance=.1, segments=3)
assert result['after']['objects'][0]['vertices'] > 8
assert bpy.context.mode == 'EDIT_MESH'
assert result['after']['objects'][0]['zero_area_faces'] == 0
bpy.ops.mesh.select_all(action='DESELECT')
bm = bmesh.from_edit_mesh(obj.data)
bm.faces.ensure_lookup_table()
bm.faces[0].select_set(True)
bmesh.update_edit_mesh(obj.data)
for operation in ('inset', 'extrude'):
    result = mesh_tools.edit(operation, distance=.01)
    assert result['after']['objects'][0]['zero_area_faces'] == 0, result
mat = bpy.data.materials.new('Test material')
bpy.ops.mesh.select_all(action='SELECT')
mesh_tools.edit('assign_material', material=mat.name)
mesh_tools.edit('move_normal', distance=.001)
try:
    mesh_tools.edit('bevel', distance=float('nan'))
except ValueError:
    pass
else:
    raise AssertionError('Accepted NaN')
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.mesh.primitive_cube_add()
bridge_obj = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(bridge_obj.data)
bm.clear()
for height in (0, 2):
    verts = [bm.verts.new((x, y, height)) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    bm.faces.new(verts)
for edge in bm.edges:
    edge.select_set(True)
bmesh.update_edit_mesh(bridge_obj.data)
result = mesh_tools.edit('bridge')
assert result['after']['objects'][0]['faces'] == 6, result
assert result['after']['objects'][0]['boundary_edges'] == 0, result
ui.unregister()
print('BLENDRELAY_OPTIMIZATION_PASSED')
