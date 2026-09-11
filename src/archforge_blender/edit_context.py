"""Live Edit Mode selection context and execution-mode preservation."""
import bpy
import bmesh
from .workflow import compact


RULES = '''
EDIT MODE WORKFLOW:
The mesh_edit context describes live selected mesh elements, not whole-object selection.
Use import bmesh; bm = bmesh.from_edit_mesh(obj.data) for each object in Edit Mode.
Do not free that BMesh. Use bmesh.update_edit_mesh(obj.data) after changes.
Selected indices are a snapshot: verify current selection before editing and refresh after
topology changes. Never reuse stale indices. If truncated, inspect the live BMesh for the
complete selection; do not treat the sample as the entire target.
Keep changes on selected geometry by default. Shared boundary vertices affect adjacent
faces; preserve surrounding shape and only change connections necessary for the request.
When only_selected is true, do not modify unrelated elements or objects; read adjacent
geometry as needed to maintain connectivity. Do not replace the entire mesh or object.
Use local coordinates with matrix_world for world-space measurements. For nonuniform
scale, transform normals with the inverse-transpose and account for scene unit scale.
Remain in Edit Mode, preserve the mesh selection mode and keep resulting edited elements
selected where possible. Do not switch to Object Mode unless an operation requires it;
return to Edit Mode afterward. Keep overlays and selection highlighting enabled.
'''


def capture(context, limit=256):
    if context.mode != 'EDIT_MESH':
        return None
    result = dict(mode=context.mode, selection_modes=[n for n, yes in
        zip(('VERT', 'EDGE', 'FACE'), context.tool_settings.mesh_select_mode) if yes],
        active_object=context.active_object.name if context.active_object else None,
        meters_per_blender_unit=context.scene.unit_settings.scale_length,
        units=context.scene.unit_settings.system, objects=[], selected_count=0)
    for obj in context.objects_in_mode_unique_data:
        if obj.type != 'MESH':
            continue
        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        for seq in (bm.verts, bm.edges, bm.faces):
            seq.ensure_lookup_table()
            seq.index_update()
        verts = [v for v in bm.verts if v.select and not v.hide]
        edges = [e for e in bm.edges if e.select and not e.hide]
        faces = [f for f in bm.faces if f.select and not f.hide]
        counts = dict(vertices=len(verts), edges=len(edges), faces=len(faces))
        result['selected_count'] += sum(counts.values())
        neighbors = {v for e in edges[:limit] for v in e.verts}
        neighbors.update(v for f in faces[:limit] for v in f.verts)
        neighbors.update(v for selected in verts[:limit] for e in selected.link_edges for v in e.verts)
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        result['objects'].append(dict(name=obj.name, mesh=obj.data.name,
            materials=[slot.material.name if slot.material else None for slot in obj.material_slots],
            matrix_world=[list(row) for row in obj.matrix_world], counts=counts,
            truncated=any(n>limit for n in counts.values()),
            adjacent_vertices_truncated=len(neighbors)>limit,
            vertices=[dict(index=v.index, local=list(v.co), world=list(obj.matrix_world @ v.co)) for v in verts[:limit]],
            edges=[dict(index=e.index, vertices=[v.index for v in e.verts]) for e in edges[:limit]],
            faces=[dict(index=f.index, vertices=[v.index for v in f.verts],
                        normal_local=list(f.normal), normal_world=list((normal_matrix @ f.normal).normalized()),
                        material_index=f.material_index) for f in faces[:limit]],
            adjacent_vertices=[dict(index=v.index, local=list(v.co)) for v in sorted(neighbors,key=lambda v:v.index)[:limit]],
            active_face=bm.faces.active.index if bm.faces.active else None))
    return compact(result)


def remember():
    ctx = bpy.context
    return dict(mode=ctx.mode, active=ctx.view_layer.objects.active,
                objects=list(ctx.objects_in_mode) if ctx.mode == 'EDIT_MESH' else [],
                selection_mode=tuple(ctx.tool_settings.mesh_select_mode))


def restore(state):
    if not state:
        return
    ctx = bpy.context
    if state['mode'] == 'OBJECT':
        if ctx.mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')
        return
    if state['mode'] != 'EDIT_MESH':
        return
    # Preserve surviving BMesh selection; do not reapply old indices after topology edits.
    valid = []
    for obj in state['objects']:
        try:
            if obj.name in ctx.view_layer.objects:
                valid.append(obj)
        except ReferenceError:
            continue
    if not valid:
        return
    if ctx.mode != 'EDIT_MESH' or set(ctx.objects_in_mode) != set(valid):
        if ctx.mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in ctx.selected_objects:
            obj.select_set(False)
        for obj in valid:
            obj.select_set(True)
        ctx.view_layer.objects.active = state['active'] if state['active'] in valid else valid[0]
        bpy.ops.object.mode_set(mode='EDIT')
    ctx.tool_settings.mesh_select_mode = state['selection_mode']
