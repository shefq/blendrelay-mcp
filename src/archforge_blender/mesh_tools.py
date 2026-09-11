"""Selected mesh operations with parameter checks and compact geometry verification."""
import math
import bpy
import bmesh
from mathutils import Vector


def validate(names=None):
    objects = [bpy.data.objects.get(n) for n in names] if names is not None else list(bpy.context.selected_objects)
    reports = []
    for obj in objects:
        if obj is None or obj.type != 'MESH':
            continue
        editing = obj.data.is_editmode
        bm = bmesh.from_edit_mesh(obj.data) if editing else bmesh.new()
        if not editing:
            bm.from_mesh(obj.data)
        try:
            reports.append(dict(name=obj.name, vertices=len(bm.verts), edges=len(bm.edges), faces=len(bm.faces),
                nonfinite_vertices=sum(not all(math.isfinite(c) for c in v.co) for v in bm.verts),
                zero_length_edges=sum(e.calc_length()<1e-8 for e in bm.edges),
                zero_area_faces=sum(f.calc_area()<1e-12 for f in bm.faces),
                boundary_edges=sum(e.is_boundary for e in bm.edges),
                nonmanifold_edges=sum(not e.is_manifold for e in bm.edges)))
        finally:
            if not editing: bm.free()
    return dict(objects=reports, note='Local mesh checks only. Open boundaries can be intentional; these checks do not establish alignment, intersections, or visual quality.')


def edit(operation, distance=.02, segments=3, material=None):
    if bpy.context.mode != 'EDIT_MESH':
        raise ValueError('mesh_edit requires mesh Edit Mode')
    if operation not in {'bevel','extrude','inset','bridge','move_normal','assign_material'}:
        raise ValueError('Unknown mesh operation')
    if isinstance(distance,bool) or not isinstance(distance,(float,int)) or not math.isfinite(distance):
        raise ValueError('distance must be a finite number in local mesh units')
    if not isinstance(segments,int) or isinstance(segments,bool) or not 1 <= segments <= 64:
        raise ValueError('segments must be an integer from 1 to 64')
    if operation in {'bevel','inset'} and distance <= 0:
        raise ValueError('Bevel/inset distance must be positive')
    mat = bpy.data.materials.get(material) if isinstance(material,str) else None
    if operation == 'assign_material' and mat is None:
        raise ValueError('Specify an existing material name')
    targets=[]
    # Validate all selections before mutating any object.
    for obj in bpy.context.objects_in_mode_unique_data:
        bm=bmesh.from_edit_mesh(obj.data)
        verts=[v for v in bm.verts if v.select and not v.hide]
        edges=[e for e in bm.edges if e.select and not e.hide]
        faces=[f for f in bm.faces if f.select and not f.hide]
        chosen=edges if operation in {'bevel','bridge'} else verts if operation=='move_normal' else faces
        if not chosen: continue
        if operation=='bridge':
            nodes={v for e in edges for v in e.verts}
            if any(sum(e in edges for e in v.link_edges)!=2 for v in nodes):
                raise ValueError('Bridge requires two selected closed edge loops in one mesh')
            components=[]
            remaining=set(nodes)
            while remaining:
                stack=[remaining.pop()]; group=set(stack)
                while stack:
                    v=stack.pop()
                    for e in v.link_edges:
                        if e not in edges: continue
                        other=e.other_vert(v)
                        if other in remaining:
                            remaining.remove(other);group.add(other);stack.append(other)
                components.append(group)
            if len(components)!=2 or len(components[0])!=len(components[1]) or any(not e.is_boundary for e in edges):
                raise ValueError('Bridge requires two equal-size boundary loops in the same mesh')
        targets.append((obj,bm,verts,edges,faces))
    if not targets: raise ValueError('Select edges for bevel/bridge, faces for extrude/inset/material, or vertices for normal movement')
    names=[item[0].name for item in targets]
    before=validate(names)
    for obj,bm,verts,edges,faces in targets:
        bm.normal_update()
        if operation=='bevel':
            bmesh.ops.bevel(bm,geom=edges,offset=distance,segments=segments,affect='EDGES',clamp_overlap=True)
        elif operation=='extrude':
            normals={v:sum((f.normal for f in v.link_faces if f in faces),Vector()).normalized() for f in faces for v in f.verts}
            result=bmesh.ops.extrude_face_region(bm,geom=faces)
            new=[v for v in result['geom'] if isinstance(v,bmesh.types.BMVert)]
            for v in new:
                nearest=min(normals,key=lambda old:(old.co-v.co).length_squared)
                v.co += normals[nearest]*distance
            for v in bm.verts: v.select_set(False)
            for v in new: v.select_set(True)
            bm.select_flush_mode()
        elif operation=='inset':
            bmesh.ops.inset_region(bm,faces=faces,thickness=distance,depth=0,use_even_offset=True)
        elif operation=='bridge':
            bmesh.ops.bridge_loops(bm,edges=edges,use_pairs=True)
        elif operation=='move_normal':
            for v in verts: v.co += v.normal*distance
        else:
            index=obj.data.materials.find(mat.name)
            if index<0: obj.data.materials.append(mat);index=len(obj.data.materials)-1
            for face in faces: face.material_index=index
        bm.normal_update()
        bmesh.update_edit_mesh(obj.data,loop_triangles=True,destructive=operation not in {'move_normal','assign_material'})
    return dict(operation=operation, units='local mesh units', before=before, after=validate(names))
