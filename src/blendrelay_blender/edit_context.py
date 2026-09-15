"""Capture general Blender mode and selection context and restore interaction state."""
import bpy
import bmesh
from .workflow import compact


RULES = """
MODE-AWARE WORKFLOW:
The edit_context describes the Blender mode and the current component, bone, stroke or object selection when Blender exposes it.
Use the native Blender data API appropriate for that mode. Treat sampled indices as temporary and refresh live data after topology changes.
When only_selected is true, limit changes to the selected target and dependencies required for a correct result.
Preserve surrounding data, active object, selection, mode and viewport highlighting where possible.
For mesh Edit Mode use bmesh.from_edit_mesh and never free its live BMesh. Call bmesh.update_edit_mesh after changes.
Use matrix_world for world-space measurements and account for scene unit scale and nonuniform transforms.
"""


def _base(context):
    active = context.active_object
    return dict(mode=context.mode, active_object=active.name if active else None,
                active_type=active.type if active else None,
                meters_per_blender_unit=context.scene.unit_settings.scale_length,
                units=context.scene.unit_settings.system,
                selected_objects=[obj.name for obj in context.selected_objects[:100]],
                selected_count=0, objects=[])


def capture(context, limit=256):
    if context.mode == 'OBJECT':
        return None
    result = _base(context)
    if context.mode == 'EDIT_MESH':
        result['selection_modes'] = [name for name, enabled in
            zip(('VERT', 'EDGE', 'FACE'), context.tool_settings.mesh_select_mode) if enabled]
        for obj in context.objects_in_mode_unique_data:
            if obj.type != 'MESH': continue
            bm = bmesh.from_edit_mesh(obj.data); bm.normal_update()
            for sequence in (bm.verts, bm.edges, bm.faces): sequence.ensure_lookup_table(); sequence.index_update()
            verts = [v for v in bm.verts if v.select and not v.hide]
            edges = [e for e in bm.edges if e.select and not e.hide]
            faces = [f for f in bm.faces if f.select and not f.hide]
            counts = dict(vertices=len(verts), edges=len(edges), faces=len(faces))
            result['selected_count'] += sum(counts.values())
            neighbors = {v for edge in edges[:limit] for v in edge.verts}
            neighbors.update(v for face in faces[:limit] for v in face.verts)
            normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
            result['objects'].append(dict(name=obj.name, data=obj.data.name, counts=counts,
                matrix_world=[list(row) for row in obj.matrix_world], truncated=any(value > limit for value in counts.values()),
                vertices=[dict(index=v.index, local=list(v.co), world=list(obj.matrix_world @ v.co)) for v in verts[:limit]],
                edges=[dict(index=e.index, vertices=[v.index for v in e.verts]) for e in edges[:limit]],
                faces=[dict(index=f.index, vertices=[v.index for v in f.verts], normal_local=list(f.normal),
                            normal_world=list((normal_matrix @ f.normal).normalized()), material_index=f.material_index)
                       for f in faces[:limit]],
                adjacent_vertices=[dict(index=v.index, local=list(v.co)) for v in sorted(neighbors, key=lambda item:item.index)[:limit]]))
    elif context.mode == 'EDIT_ARMATURE' and context.active_object:
        bones = [bone for bone in context.active_object.data.edit_bones if bone.select]
        result['selected_count'] = len(bones)
        result['bones'] = [dict(name=bone.name, head=list(bone.head), tail=list(bone.tail), roll=bone.roll,
                                parent=bone.parent.name if bone.parent else None) for bone in bones[:limit]]
    elif context.mode == 'POSE' and context.active_object:
        bones = list(getattr(context, 'selected_pose_bones', None) or [])
        result['selected_count'] = len(bones)
        result['pose_bones'] = [dict(name=bone.name, parent=bone.parent.name if bone.parent else None,
                                     constraints=[c.type for c in bone.constraints]) for bone in bones[:limit]]
    elif context.mode in {'EDIT_CURVE', 'EDIT_SURFACE'} and context.active_object:
        splines = []
        for spline_index, spline in enumerate(context.active_object.data.splines):
            bezier = [index for index, point in enumerate(spline.bezier_points)
                      if point.select_control_point or point.select_left_handle or point.select_right_handle]
            points = [index for index, point in enumerate(spline.points) if point.select]
            if bezier or points: splines.append({'spline': spline_index, 'type': spline.type, 'bezier_points': bezier[:limit], 'points': points[:limit]})
        result['selected_count'] = sum(len(item['bezier_points']) + len(item['points']) for item in splines)
        result['splines'] = splines[:limit]
    elif context.mode == 'EDIT_LATTICE' and context.active_object:
        selected = [index for index, point in enumerate(context.active_object.data.points) if point.select]
        result['selected_count'] = len(selected); result['points'] = selected[:limit]
    else:
        # Sculpt, paint, Grease Pencil and other modes expose enough high-level state for
        # generated Blender Python to inspect the exact mode-specific data live.
        result['selected_count'] = len(context.selected_objects)
    return compact(result)


def remember():
    context = bpy.context
    return dict(mode=context.mode, active=context.view_layer.objects.active,
                selected=list(context.selected_objects),
                objects_in_mode=list(getattr(context, 'objects_in_mode', ()) or ()),
                mesh_selection_mode=tuple(context.tool_settings.mesh_select_mode))


def restore(state):
    if not state: return
    context = bpy.context
    desired = state['mode']
    valid = [obj for obj in state['selected'] if obj and obj.name in context.view_layer.objects]
    active = state['active'] if state['active'] and state['active'].name in context.view_layer.objects else (valid[0] if valid else None)
    if context.mode != 'OBJECT' and bpy.ops.object.mode_set.poll(): bpy.ops.object.mode_set(mode='OBJECT')
    for obj in context.selected_objects: obj.select_set(False)
    for obj in valid: obj.select_set(True)
    if active: context.view_layer.objects.active = active
    mode_map = {'EDIT_MESH':'EDIT', 'EDIT_CURVE':'EDIT', 'EDIT_SURFACE':'EDIT', 'EDIT_TEXT':'EDIT',
                'EDIT_ARMATURE':'EDIT', 'EDIT_METABALL':'EDIT', 'EDIT_LATTICE':'EDIT',
                'EDIT_GREASE_PENCIL':'EDIT', 'POSE':'POSE', 'SCULPT':'SCULPT',
                'PAINT_WEIGHT':'WEIGHT_PAINT', 'PAINT_VERTEX':'VERTEX_PAINT', 'PAINT_TEXTURE':'TEXTURE_PAINT'}
    target = mode_map.get(desired)
    if target and active and bpy.ops.object.mode_set.poll():
        try: bpy.ops.object.mode_set(mode=target)
        except RuntimeError: pass
    if desired == 'EDIT_MESH': context.tool_settings.mesh_select_mode = state['mesh_selection_mode']
