# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread imports into dedicated collections, without replacing the scene."""
import hashlib
import json
from pathlib import Path
import struct
from urllib.parse import unquote
import bpy
from .asset_rules import AssetError


def local_file(directory, name):
    if not isinstance(name,str) or ':' in name or '\\' in name or Path(name).is_absolute(): raise AssetError('Invalid cached file path')
    path=(directory/name).resolve()
    if not path.is_relative_to(directory.resolve()): raise AssetError('Asset dependency escapes cache')
    return path


def validate_gltf(path):
    if path.suffix.lower()=='.glb':
        with path.open('rb') as stream:
            magic,version,total=struct.unpack('<4sII',stream.read(12))
            length,kind=struct.unpack('<II',stream.read(8))
            if magic!=b'glTF' or version!=2 or total!=path.stat().st_size or kind!=0x4E4F534A or length>16*1024*1024: raise AssetError('Invalid GLB header')
            data=json.loads(stream.read(length))
    else:
        if path.stat().st_size>16*1024*1024: raise AssetError('GLTF metadata is too large')
        data=json.loads(path.read_text(encoding='utf-8'))
    for record in data.get('buffers',[])+data.get('images',[]):
        uri=record.get('uri','')
        if uri and not uri.startswith('data:'):
            if not local_file(path.parent,unquote(uri)).is_file(): raise AssetError('Missing GLTF dependency')


def ensure_gltf_importer():
    """Ensure Blender's glTF importer has a usable NumPy module.

    Some Blender installations retain a partial NumPy package after an update.
    ArchForge keeps its per-user repair in Blender's add-on modules directory so
    it survives independently of the extension and does not alter a scene.
    """
    import os
    import sys

    def usable_numpy():
        try:
            import numpy
        except ImportError:
            return False
        return hasattr(numpy, 'ndarray')

    if usable_numpy():
        return

    appdata = os.environ.get('APPDATA')
    user_modules = None
    if appdata:
        user_modules = os.path.join(
            appdata, 'Blender Foundation', 'Blender', bpy.app.version_string.split('.')[0] + '.' + bpy.app.version_string.split('.')[1],
            'scripts', 'addons', 'modules',
        )
    if user_modules and os.path.isfile(os.path.join(user_modules, 'numpy', '__init__.py')):
        # A broken namespace package may already be cached.  Prefer the known
        # good user module, then load NumPy again from that location.
        while user_modules in sys.path:
            sys.path.remove(user_modules)
        sys.path.insert(0, user_modules)
        sys.modules.pop('numpy', None)
        if usable_numpy():
            return

    raise AssetError(
        'Blender glTF import is unavailable because NumPy is incomplete. '
        'Restart Blender, then reinstall its bundled Python components if this persists.'
    )


def import_cached(root, asset_id, asset_scene, collection_name=None, location=None, scale=1.0):
    from .asset_ui import policy, scene_key
    scene=bpy.context.scene
    if scene_key(scene)!=asset_scene: raise AssetError('Active scene changed; submit the import again')
    if bpy.context.mode!='OBJECT': raise AssetError('Switch to Object Mode before importing an asset')
    provider,_,key=asset_id.partition(':')
    if provider not in ('poly_haven','poly_pizza') or not key or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in key): raise AssetError('Invalid asset ID')
    directory=(Path(root)/'assets'/provider/key).resolve()
    if not directory.is_relative_to((Path(root)/'assets').resolve()): raise AssetError('Invalid cache location')
    record=json.loads((directory/'metadata.json').read_text(encoding='utf-8'))
    if record.get('asset_id')!=asset_id: raise AssetError('Cache metadata ID mismatch')
    policy(scene).check(record,cached=True)
    for item in record['files']:
        path=local_file(directory,item['path'])
        with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=item['sha256']: raise AssetError('Cached file changed; import cancelled')
    source=local_file(directory,record['entry_file'])
    suffix=source.suffix.lower()
    if suffix in ('.glb','.gltf'):
        validate_gltf(source)
        ensure_gltf_importer()
    elif suffix not in ('.blend','.hdr','.exr'): raise AssetError('Unsupported asset format')
    before_objects=set(bpy.data.objects)
    before_images=set(bpy.data.images)
    before_materials=set(bpy.data.materials)
    before_collections=set(bpy.data.collections)
    before_worlds=set(bpy.data.worlds)
    selected=list(bpy.context.selected_objects)
    active=bpy.context.view_layer.objects.active
    auto_scripts=bpy.context.preferences.filepaths.use_scripts_auto_execute
    try:
        bpy.context.preferences.filepaths.use_scripts_auto_execute=False
        bpy.ops.object.select_all(action='DESELECT')
        if suffix in ('.glb','.gltf'):
            bpy.ops.import_scene.gltf(filepath=str(source))
        elif suffix=='.blend':
            with bpy.data.libraries.load(str(source),link=False) as (available,loaded):
                if record['kind']=='material': loaded.materials=available.materials
                else: loaded.objects=available.objects
        else:
            world=bpy.data.worlds.new(record['name'])
            world.use_nodes=True
            nodes=world.node_tree.nodes
            environment=nodes.new('ShaderNodeTexEnvironment')
            environment.image=bpy.data.images.load(str(source),check_existing=False)
            world.node_tree.links.new(environment.outputs['Color'],nodes.get('Background').inputs['Color'])
            world.use_fake_user=True
        imported=list(set(bpy.data.objects)-before_objects)
        for obj in list(imported):
            if obj.type in ('CAMERA','LIGHT'):
                bpy.data.objects.remove(obj,do_unlink=True);imported.remove(obj)
        def child(parent, label):
            found=next((c for c in parent.children if c.name==label and c.get('archforge_asset_container')),None)
            if found: return found
            value=bpy.data.collections.new(label);value['archforge_asset_container']=True;parent.children.link(value)
            return value
        parent=child(child(scene.collection,'ArchForge Assets'),'Poly Haven' if provider=='poly_haven' else 'Poly Pizza')
        collection=bpy.data.collections.new(collection_name or record['name']);parent.children.link(collection)
        # One root transform preserves relationships within multi-object assets.
        if imported:
            anchor=bpy.data.objects.new(record['name']+' placement',None);collection.objects.link(anchor)
            anchor.location=location or (0,0,0);anchor.scale=(scale,)*3
            for obj in imported:
                for old in list(obj.users_collection): old.objects.unlink(obj)
                collection.objects.link(obj)
                if obj.parent not in imported: obj.parent=anchor
                obj.hide_select=False;obj.hide_viewport=False;obj.hide_render=False;obj.hide_set(False)
        metadata={k:record.get(k) for k in ('provider','asset_id','name','licence','creator','source_url','licence_url','download_date','original_file_name','tags')}
        materials=list(set(bpy.data.materials)-before_materials)
        worlds=list(set(bpy.data.worlds)-before_worlds)
        for block in [collection]+imported+materials+worlds:
            block['archforge_asset_metadata']=json.dumps(metadata,ensure_ascii=False)
            block['archforge_asset_id']=asset_id
            if hasattr(block,'animation_data') and block.animation_data:
                for driver in list(block.animation_data.drivers): block.driver_remove(driver.data_path,driver.array_index)
            if getattr(block,'node_tree',None): block.node_tree.animation_data_clear()
        for material in materials: material.use_fake_user=True
        for image in set(bpy.data.images)-before_images:
            if image.filepath and not image.packed_file:
                path=Path(bpy.path.abspath(image.filepath,start=str(directory))).resolve()
                if not path.is_relative_to(directory): raise AssetError('Texture path is outside the asset cache')
                image.filepath=str(path)
        for empty in set(bpy.data.collections)-before_collections:
            if empty!=collection and not empty.get('archforge_asset_container') and not empty.objects and not empty.children:
                bpy.data.collections.remove(empty)
        bpy.context.view_layer.update()
        for obj in imported: obj.select_set(True)
        if imported: bpy.context.view_layer.objects.active=imported[0]
        return dict(asset_id=asset_id,collection=collection.name,objects=[o.name for o in imported],materials=[m.name for m in materials],worlds=[w.name for w in worlds],cache_directory=str(directory))
    except Exception:
        for obj in set(bpy.data.objects)-before_objects: bpy.data.objects.remove(obj,do_unlink=True)
        for coll in set(bpy.data.collections)-before_collections: bpy.data.collections.remove(coll)
        for world in set(bpy.data.worlds)-before_worlds: bpy.data.worlds.remove(world)
        for material in set(bpy.data.materials)-before_materials: bpy.data.materials.remove(material)
        for image in set(bpy.data.images)-before_images: bpy.data.images.remove(image)
        for obj in selected: obj.select_set(True)
        bpy.context.view_layer.objects.active=active
        raise
    finally:
        bpy.context.preferences.filepaths.use_scripts_auto_execute=auto_scripts
