# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread Blender operations and durable full-file checkpoints."""
import contextlib
import io
import json
import shutil
from pathlib import Path
import time
import traceback
import uuid
import bpy


def version_root(root):
    scene=bpy.context.scene
    if not scene.get('archforge_workspace_id'):
        scene['archforge_workspace_id']=uuid.uuid4().hex
    # Only generated IDs are accepted as directory components.
    identity=scene['archforge_workspace_id']
    if not isinstance(identity,str) or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):
        raise RuntimeError('Invalid scene workspace ID')
    path=Path(root)/'scene_versions'/identity
    path.mkdir(parents=True,exist_ok=True)
    return path


def versions(root):
    path=version_root(root)/'index.json'
    return json.loads(path.read_text()) if path.exists() else []


def checkpoint(root,label):
    directory=version_root(root)
    entries=versions(root)
    key=uuid.uuid4().hex
    path=directory/(key+'.blend')
    original=bpy.data.filepath
    result=bpy.ops.wm.save_as_mainfile(filepath=str(path),copy=True,check_existing=False)
    if 'FINISHED' not in result or not path.exists():raise RuntimeError('Checkpoint could not be saved; edit was not started')
    entry=dict(version_id=key,label=str(label)[:160],created_at=time.time(),path=str(path),original_filepath=original)
    entries.append(entry)
    tmp=directory/'index.tmp';tmp.write_text(json.dumps(entries,indent=2));tmp.replace(directory/'index.json')
    return entry


def restore(root,version_id):
    entries=versions(root)
    entry=next((e for e in entries if e['version_id']==version_id),None)
    if entry is None:raise RuntimeError('Unknown version for this scene')
    path=Path(entry['path']).resolve()
    if path.parent!=version_root(root).resolve() or not path.is_file():raise RuntimeError('Version file is unavailable')
    backup=checkpoint(root,'Before restoring '+entry['label'])
    working=Path(root)/'restored_scenes'/(uuid.uuid4().hex+'.blend')
    working.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(path,working)
    bpy.ops.wm.open_mainfile(filepath=str(working),load_ui=False,use_scripts=False)
    return dict(restored_version=version_id,backup=backup,working_copy=str(working),message='Restored into a new working copy. Use Save As for your preferred file destination.')


def inspect_scene(offset=0,limit=100,object_name=None):
    if not isinstance(offset,int) or offset<0 or not isinstance(limit,int) or not 1<=limit<=500:
        raise ValueError('Use offset >= 0 and limit 1–500')
    objects=list(bpy.context.scene.objects)
    if object_name is not None:
        objects=[o for o in objects if o.name==object_name]
    return dict(scene=bpy.context.scene.name,filepath=bpy.data.filepath,mode=bpy.context.mode,
                total=len(objects),selection=[o.name for o in bpy.context.selected_objects],
                objects=[dict(name=o.name,type=o.type,location=list(o.location),dimensions=list(o.dimensions),
                              rotation=list(o.rotation_euler),materials=[m.name if m else None for m in o.data.materials] if hasattr(o.data,'materials') else [])
                         for o in objects[offset:offset+limit]])


def execute(root,code,label='Prompt edit'):
    if not isinstance(code,str):raise ValueError('code must be Python text')
    compiled=compile(code,'<ArchForge MCP>','exec')
    before=checkpoint(root,'Before: '+label)
    output=io.StringIO();namespace={'bpy':bpy,'__name__':'__archforge__'}
    try:
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            exec(compiled,namespace,namespace)
        after=checkpoint(root,label)
        value=namespace.get('result')
        # A large/non-JSON result must not cause the acknowledgement to disappear.
        value=json.loads(json.dumps(value,default=str))
        if len(json.dumps(value))>100000:value={'truncated':True,'text':str(value)[:100000]}
        return dict(output=output.getvalue()[-100000:],result=value,before=before,after=after)
    except Exception:
        # Code can partially mutate Blender before failing. Never claim rollback.
        return dict(failed=True,error=traceback.format_exc()[-12000:],output=output.getvalue()[-100000:],
                    before=before,recovery='Scene may be partially modified. Restore the before checkpoint if needed.')


def screenshot(root):
    import base64
    from mathutils import Vector
    area=next((a for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type=='VIEW_3D'),None)
    if area is None:raise RuntimeError('Open a 3D Viewport for a screenshot')
    window=next(w for w in bpy.context.window_manager.windows if area in list(w.screen.areas))
    region=next(r for r in area.regions if r.type=='WINDOW')
    scene=bpy.context.scene;r=scene.render
    old=(r.filepath,r.resolution_x,r.resolution_y,r.resolution_percentage,r.image_settings.file_format)
    path=Path(root)/'viewport.png'
    try:
        r.filepath=str(path);r.resolution_x=800;r.resolution_y=600;r.resolution_percentage=100;r.image_settings.file_format='PNG'
        with bpy.context.temp_override(window=window,area=area,region=region):
            bpy.ops.render.opengl(write_still=True,view_context=True)
        data=path.read_bytes()
        if len(data)>650000:raise RuntimeError('Viewport image exceeds transport size; lower viewport detail')
        return dict(image=base64.b64encode(data).decode(),mime_type='image/png')
    finally:
        r.filepath,r.resolution_x,r.resolution_y,r.resolution_percentage,r.image_settings.file_format=old


def run(root,job):
    action=job['action'];args=job.get('arguments',{})
    if action=='inspect':return inspect_scene(**args)
    if action=='execute':return execute(root,**args)
    if action=='versions':return versions(root)
    if action=='checkpoint':return checkpoint(root,**args)
    if action=='restore':return restore(root,**args)
    if action=='screenshot':return screenshot(root)
    raise ValueError('Unknown Blender action')
