# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread geometry projection with staged, incremental object replacement."""
import hashlib
import json
import bpy

PENDING=None


def key(obj):return (obj.get('archforge_entity_id'),obj.get('archforge_part_role'))


def signature(obj):
    data={'matrix':[round(v,6) for row in obj.matrix_world for v in row],
          'vertices':[[round(v,6) for v in vertex.co] for vertex in obj.data.vertices] if obj.type=='MESH' else [],
          'faces':[list(p.vertices) for p in obj.data.polygons] if obj.type=='MESH' else [],
          'modifiers':[(m.name,m.type) for m in obj.modifiers],
          'materials':[]}
    for material in obj.data.materials if obj.type=='MESH' else []:
        if material is None:data['materials'].append(None);continue
        nodes=[]
        if material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type=='BSDF_PRINCIPLED':
                    nodes.append([list(node.inputs['Base Color'].default_value),float(node.inputs['Roughness'].default_value)])
        data['materials'].append([material.name,list(material.diffuse_color),nodes])
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def managed(project):
    return [o for o in bpy.context.scene.objects if o.get('archforge_project_id')==project and not o.get('archforge_staging')]


def diverged(project):
    found={};bad=set()
    for obj in managed(project):
        k=key(obj)
        if k in found:bad.add(k[0])
        found[k]=obj
        if signature(obj)!=obj.get('archforge_baseline'):bad.add(k[0])
    try:expected=json.loads(bpy.context.scene.get('archforge_bindings','[]'))
    except (ValueError,TypeError):expected=[]
    for eid,role in expected:
        if (eid,role) not in found:bad.add(eid)
    return sorted(bad)


def material(spec):
    rgba=spec.get('rgba',[.7,.7,.7,1]);name='AF '+spec['id']+' '+hashlib.sha256(json.dumps(rgba).encode()).hexdigest()[:8]
    mat=bpy.data.materials.get(name)
    if mat:return mat
    mat=bpy.data.materials.new(name);mat.diffuse_color=rgba;mat.use_nodes=True
    node=mat.node_tree.nodes.get('Principled BSDF');node.inputs['Base Color'].default_value=rgba;node.inputs['Roughness'].default_value=.45
    if spec['id']=='mat-glass':
        node.inputs['Transmission Weight'].default_value=.8;node.inputs['Roughness'].default_value=.08
    return mat


def discard():
    global PENDING
    if PENDING:
        for obj in list(PENDING['collection'].objects):
            mesh=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if mesh.users==0:bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(PENDING['collection']);PENDING=None


def stage(model,specs,force=False):
    """Yield after each mesh so callers can keep Blender's UI responsive."""
    global PENDING
    discard()
    project=model['project_id'];scene=bpy.context.scene
    existing_project=scene.get('archforge_project_id')
    if existing_project and existing_project!=project and managed(existing_project):
        raise RuntimeError('Open a new Blender file before attaching a different ArchForge project')
    bad=diverged(project)
    if bad and not force:raise RuntimeError('Managed geometry was edited: '+', '.join(bad[:5]))
    originals=managed(project)
    current={key(o):o for o in originals}
    collection=bpy.data.collections.new('ArchForge · staging');scene.collection.children.link(collection)
    collection.hide_render=True;collection.hide_viewport=True
    desired={(s['entity_id'],s['role']) for s in specs}
    PENDING={'model':model,'collection':collection,'replace':[],'desired':desired,'current':current,
             'originals':originals,'fingerprints':{o.as_pointer():signature(o) for o in originals}}
    for spec in specs:
        k=(spec['entity_id'],spec['role']);old=current.get(k)
        if old and old.get('archforge_spec_hash')==spec['hash'] and signature(old)==old.get('archforge_baseline'):
            continue
        mesh=bpy.data.meshes.new('AF mesh '+spec['entity_id']+' '+spec['role'])
        mesh.from_pydata(spec['vertices'],[],spec['faces']);mesh.update()
        obj=bpy.data.objects.new(spec['label']+' · '+spec['role'],mesh);collection.objects.link(obj)
        obj.data.materials.append(material(spec['material']))
        obj['archforge_project_id']=project;obj['archforge_entity_id']=spec['entity_id'];obj['archforge_part_role']=spec['role']
        obj['archforge_spec_hash']=spec['hash'];obj['archforge_staging']=True;obj['archforge_ownership']='managed'
        obj['archforge_baseline']=signature(obj)
        PENDING['replace'].append(k)
        yield spec['entity_id']


def verify_stage():
    if PENDING is None:raise RuntimeError('No staged geometry')
    current={o.as_pointer():signature(o) for o in managed(PENDING['model']['project_id'])}
    if current!=PENDING['fingerprints']:raise RuntimeError('Scene changed during staging; preserve manual edits and replan')


def commit(revision):
    global PENDING
    if PENDING is None:raise RuntimeError('No staged geometry; reconnect to recover')
    verify_stage()
    scene=bpy.context.scene;project=PENDING['model']['project_id']
    selected={o.get('archforge_entity_id') for o in bpy.context.selected_objects}
    destination=next((c for c in scene.collection.children if c.get('archforge_project_id')==project),None)
    if destination is None:
        destination=bpy.data.collections.new('ArchForge · '+PENDING['model'].get('name','House'));scene.collection.children.link(destination);destination['archforge_project_id']=project
    remove=set(PENDING['replace'])|(set(PENDING['current'])-PENDING['desired'])
    for old in PENDING['originals']:
        k=key(old)
        if k in remove or old is not PENDING['current'].get(k):
            mesh=old.data;bpy.data.objects.remove(old,do_unlink=True)
            if mesh.users==0:bpy.data.meshes.remove(mesh)
    for obj in list(PENDING['collection'].objects):
        destination.objects.link(obj);PENDING['collection'].objects.unlink(obj)
        obj['archforge_staging']=False;obj['archforge_revision']=revision
        if obj.get('archforge_entity_id') in selected:obj.select_set(True);bpy.context.view_layer.objects.active=obj
    model=PENDING['model'];model['revision']=revision
    scene['archforge_project_id']=project;scene['archforge_revision']=revision
    scene['archforge_bindings']=json.dumps(sorted(PENDING['desired']))
    text=bpy.data.texts.get('ArchForge model.json') or bpy.data.texts.new('ArchForge model.json');text.clear();text.write(json.dumps(model,indent=2))
    bpy.data.collections.remove(PENDING['collection']);PENDING=None
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1.0
    apply_cutaway(scene.get('archforge_cutaway',False))


def apply_cutaway(enabled):
    bpy.context.scene['archforge_cutaway']=enabled
    for obj in managed(bpy.context.scene.get('archforge_project_id','')):
        role=obj.get('archforge_part_role','')
        if role=='ceiling' or role.startswith('roof'):
            obj.hide_set(enabled);obj.hide_render=enabled
