# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread Blender operations and durable full-file checkpoints."""
import contextlib
from array import array
import io
import json
import shutil
from pathlib import Path
import time
import traceback
import unicodedata
import uuid
import bpy
from . import sketch, edit_context, scene_helpers


# ── Version / checkpoint helpers ─────────────────────────────────────────────

def workspace_id(scene=None, root=None):
    """Return the persistent scene identity, recovering it by file path when possible."""
    scene = scene or bpy.context.scene
    identity = scene.get('blendrelay_workspace_id')
    if not identity and root is not None and bpy.data.filepath:
        index_path = Path(root).resolve() / 'workspace_index.json'
        try:
            index = json.loads(index_path.read_text(encoding='utf-8'))
            identity = index.get(str(Path(bpy.data.filepath).resolve()).casefold())
        except (OSError, ValueError, TypeError):
            identity = None
    if not identity:
        identity = uuid.uuid4().hex
    if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
        raise RuntimeError('Invalid scene workspace ID')
    scene['blendrelay_workspace_id'] = identity
    return identity


def workspace_root(root, scene=None):
    """Resolve and initialize this scene's data directory below the shared runtime root."""
    scene = scene or bpy.context.scene
    base = Path(root).resolve()
    identity = workspace_id(scene, base)
    path = base / 'workspaces' / identity
    path.mkdir(parents=True, exist_ok=True)

    # Migrate checkpoints and conversation state created by releases before scene workspaces.
    old_versions = base / 'scene_versions' / identity
    new_versions = path / 'scene_versions'
    if old_versions.is_dir() and not new_versions.exists():
        new_versions.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_versions), str(new_versions))
    old_conversation = base / 'conversations' / (identity + '.json')
    new_conversation = path / 'conversations' / 'state.json'
    if old_conversation.is_file() and not new_conversation.exists():
        new_conversation.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_conversation), str(new_conversation))

    metadata = {'workspace_id': identity, 'scene_name': scene.name,
                'blend_file': bpy.data.filepath or None}
    metadata_path = path / 'workspace.json'
    try:
        current = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else None
    except (OSError, ValueError):
        current = None
    if current != metadata:
        temporary = metadata_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        temporary.replace(metadata_path)
    if bpy.data.filepath:
        index_path = base / 'workspace_index.json'
        try:
            index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
        except (OSError, ValueError, TypeError):
            index = {}
        key = str(Path(bpy.data.filepath).resolve()).casefold()
        if index.get(key) != identity:
            index[key] = identity
            temporary = index_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(index, indent=2), encoding='utf-8')
            temporary.replace(index_path)
    return path


def version_root(root):
    path = workspace_root(root) / 'scene_versions'
    path.mkdir(parents=True, exist_ok=True)
    return path

def versions(root):
    path = version_root(root) / 'index.json'
    return json.loads(path.read_text()) if path.exists() else []


def checkpoint(root, label):
    directory = version_root(root)
    entries = versions(root)
    key = uuid.uuid4().hex
    path = directory / (key + '.blend')
    original = bpy.data.filepath
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, check_existing=False)
    if 'FINISHED' not in result or not path.exists():
        raise RuntimeError('Checkpoint could not be saved; edit was not started')
    entry = dict(version_id=key, label=str(label)[:160], created_at=time.time(),
                 path=str(path), original_filepath=original)
    entries.append(entry)
    tmp = directory / 'index.tmp'
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(directory / 'index.json')
    return entry


def restore(root, version_id):
    entries = versions(root)
    entry = next((e for e in entries if e['version_id'] == version_id), None)
    if entry is None:
        raise RuntimeError('Unknown version for this scene')
    path = Path(entry['path']).resolve()
    if path.parent != version_root(root).resolve() or not path.is_file():
        raise RuntimeError('Version file is unavailable')
    backup = checkpoint(root, 'Before restoring ' + entry['label'])
    working = workspace_root(root) / 'restored_scenes' / (uuid.uuid4().hex + '.blend')
    working.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, working)
    bpy.ops.wm.open_mainfile(filepath=str(working), load_ui=False, use_scripts=False)
    return dict(restored_version=version_id, backup=backup, working_copy=str(working),
                message='Restored into a new working copy. Use Save As for your preferred file destination.')


# ── Unicode-safe object lookup ────────────────────────────────────────────────

def _normalise_variants(s):
    """Yield Unicode normalisations + Latin-1/UTF-8 re-encode variants."""
    yield s
    for form in ('NFC', 'NFD', 'NFKC', 'NFKD'):
        try:
            yield unicodedata.normalize(form, s)
        except Exception:
            pass
    try:
        yield s.encode('latin-1').decode('utf-8')
    except Exception:
        pass
    try:
        yield s.encode('utf-8').decode('latin-1')
    except Exception:
        pass


def _names_match(a, b):
    variants_b = set(_normalise_variants(b))
    for v in _normalise_variants(a):
        if v in variants_b:
            return True
    return False


def _find_object(name):
    """Unicode-safe bpy.data.objects lookup."""
    obj = bpy.data.objects.get(name)
    if obj:
        return obj
    for variant in _normalise_variants(name):
        obj = bpy.data.objects.get(variant)
        if obj:
            return obj
    for obj in bpy.data.objects:
        if _names_match(obj.name, name):
            return obj
    return None


def _make_safe_lookup():
    class _SafeObjects:
        def __getitem__(self, key):
            obj = _find_object(key)
            if obj is None:
                raise KeyError(f'Object not found (tried Unicode normalisations): {key!r}')
            return obj
        def __getattr__(self, attr):
            return getattr(bpy.data.objects, attr)
        def __iter__(self):
            return iter(bpy.data.objects)
        def __len__(self):
            return len(bpy.data.objects)
        def get(self, key, default=None):
            return _find_object(key) or default
    return _SafeObjects()


def _make_bpy_proxy():
    import mathutils as _mu
    safe_objects = _make_safe_lookup()

    class _BpyDataProxy:
        def __getattr__(self, attr):
            return safe_objects if attr == 'objects' else getattr(bpy.data, attr)

    class _BpyProxy:
        _d = _BpyDataProxy()
        def __getattr__(self, attr):
            return self._d if attr == 'data' else getattr(bpy, attr)

    return _BpyProxy(), _mu


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _object_aabb(obj):
    """World-space AABB from bound_box — O(8) cost regardless of poly count."""
    import mathutils
    bound_box = getattr(obj, 'bound_box', None)
    if not bound_box:
        return None
    try:
        mw = obj.matrix_world
        xs, ys, zs = [], [], []
        for corner in bound_box:
            world = mw @ mathutils.Vector(corner)
            xs.append(world.x); ys.append(world.y); zs.append(world.z)
        return {
            'aabb_min': [round(min(xs), 3), round(min(ys), 3), round(min(zs), 3)],
            'aabb_max': [round(max(xs), 3), round(max(ys), 3), round(max(zs), 3)],
            'dimensions': [round(float(obj.dimensions.x), 3),
                           round(float(obj.dimensions.y), 3),
                           round(float(obj.dimensions.z), 3)],
        }
    except Exception:
        return None


def _object_summary(obj, detail=False):
    info = {'name': obj.name, 'type': obj.type,
            'location': [round(float(v), 3) for v in obj.location],
            'visible': obj.visible_get(), 'hide_viewport': obj.hide_viewport,
            'hide_render': obj.hide_render}
    if not detail: return info
    info.update(rotation=[round(float(v), 4) for v in obj.rotation_euler],
                scale=[round(float(v), 4) for v in obj.scale],
                mode=getattr(obj, 'mode', 'OBJECT'))
    bounds = _object_aabb(obj)
    if bounds: info.update(bounds)
    data = getattr(obj, 'data', None)
    if obj.type == 'MESH' and data:
        info['mesh'] = {'vertices': len(data.vertices), 'edges': len(data.edges),
                        'polygons': len(data.polygons), 'uv_layers': [layer.name for layer in data.uv_layers],
                        'shape_keys': [key.name for key in data.shape_keys.key_blocks] if data.shape_keys else []}
        info['vertex_groups'] = [group.name for group in obj.vertex_groups[:50]]
    elif obj.type in {'CURVE', 'SURFACE', 'FONT'} and data:
        info['curve'] = {'splines': len(getattr(data, 'splines', [])),
                         'dimensions': getattr(data, 'dimensions', None),
                         'resolution_u': getattr(data, 'resolution_u', None)}
        if obj.type == 'FONT': info['curve']['text'] = data.body[:2000]
    elif obj.type == 'ARMATURE' and data:
        info['armature'] = {'bones': len(data.bones), 'bone_names': [bone.name for bone in data.bones[:100]],
                            'display_type': obj.data.display_type}
    elif obj.type == 'CAMERA' and data:
        info['camera'] = {'type': data.type, 'lens': data.lens, 'clip_start': data.clip_start,
                          'clip_end': data.clip_end, 'dof': data.dof.use_dof}
    elif obj.type == 'LIGHT' and data:
        info['light'] = {'type': data.type, 'energy': data.energy, 'color': list(data.color),
                         'use_shadow': getattr(data, 'use_shadow', None)}
    if data and hasattr(data, 'materials'):
        info['materials'] = [material.name if material else None for material in data.materials]
    if obj.parent: info['parent'] = obj.parent.name
    info['children'] = [child.name for child in obj.children[:50]]
    info['modifiers'] = [{'name': modifier.name, 'type': modifier.type} for modifier in obj.modifiers[:30]]
    info['constraints'] = [{'name': constraint.name, 'type': constraint.type,
                            'target': getattr(getattr(constraint, 'target', None), 'name', None)}
                           for constraint in obj.constraints[:30]]
    animation = getattr(obj, 'animation_data', None)
    if animation:
        info['animation'] = {'action': animation.action.name if animation.action else None,
                             'nla_tracks': [track.name for track in animation.nla_tracks[:30]],
                             'drivers': len(animation.drivers)}
    return info


def inspect_blender_data(data_type, offset=0, limit=50):
    if not isinstance(offset, int) or offset < 0: raise ValueError('offset must be >= 0')
    limit = max(1, min(200, int(limit)))
    sources = {
        'objects': bpy.data.objects, 'collections': bpy.data.collections, 'materials': bpy.data.materials,
        'node_groups': bpy.data.node_groups, 'actions': bpy.data.actions, 'armatures': bpy.data.armatures,
        'images': bpy.data.images, 'worlds': bpy.data.worlds, 'cameras': bpy.data.cameras,
        'lights': bpy.data.lights, 'scenes': bpy.data.scenes,
    }
    if data_type not in sources: raise ValueError('Unsupported Blender data type')
    values = list(sources[data_type]); page = values[offset:offset + limit]; items = []
    for item in page:
        row = {'name': item.name, 'users': item.users, 'library': item.library.filepath if item.library else None}
        if data_type == 'objects': row = _object_summary(item, detail=True)
        elif data_type == 'collections': row.update(objects=[obj.name for obj in item.objects[:100]], children=[c.name for c in item.children[:50]])
        elif data_type == 'materials': row.update(use_nodes=item.use_nodes, node_count=len(item.node_tree.nodes) if item.node_tree else 0)
        elif data_type == 'node_groups': row.update(tree_type=item.bl_idname, nodes=len(item.nodes), links=len(item.links))
        elif data_type == 'actions': row.update(frame_range=list(item.frame_range), fcurves=len(getattr(item, 'fcurves', [])))
        elif data_type == 'armatures': row.update(bones=[bone.name for bone in item.bones[:100]])
        elif data_type == 'images': row.update(size=list(item.size), filepath=item.filepath, source=item.source)
        elif data_type == 'worlds': row.update(use_nodes=item.use_nodes, node_count=len(item.node_tree.nodes) if item.node_tree else 0)
        elif data_type == 'cameras': row.update(type=item.type, lens=item.lens, clip=[item.clip_start, item.clip_end])
        elif data_type == 'lights': row.update(type=item.type, energy=item.energy, color=list(item.color))
        elif data_type == 'scenes': row.update(frame=[item.frame_start, item.frame_end, item.frame_current], engine=item.render.engine,
                                               objects=len(item.objects), world=item.world.name if item.world else None)
        items.append(row)
    return {'data_type': data_type, 'total': len(values), 'offset': offset,
            'truncated': offset + limit < len(values), 'items': items}


# ── Named inspect / info functions (blender-mcp pattern) ─────────────────────

def get_scene_info():
    """Ultra-compact scene summary — max 20 objects, name/type/location only.
    Use get_object_info(name) for AABB and mesh detail on specific objects.
    """
    scene = bpy.context.scene
    only_selected = getattr(scene, 'blendrelay_only_selected', False)
    all_objects = list(bpy.context.selected_objects) if only_selected else list(scene.objects)
    selected = [o.name for o in bpy.context.selected_objects]
    note = (f'Scope: {len(all_objects)} selected objects only.' if only_selected else
            f'{len(all_objects)} objects total. '
            'Use get_object_info(name) for AABB/detail, '
            'or inspect with names=[...] for targeted lookup.')
    return {
        'scene': scene.name,
        'filepath': bpy.data.filepath,
        'object_count': len(all_objects),
        'only_selected': only_selected,
        'selected': selected,
        'mode': bpy.context.mode,
        'active_object': bpy.context.active_object.name if bpy.context.active_object else None,
        'frame': {'start': scene.frame_start, 'end': scene.frame_end, 'current': scene.frame_current},
        'render': {'engine': scene.render.engine, 'resolution': [scene.render.resolution_x, scene.render.resolution_y],
                   'camera': scene.camera.name if scene.camera else None},
        'world': scene.world.name if scene.world else None,
        'collections': [c.name for c in bpy.data.collections[:100]],
        'viewport_sketch': sketch.payload(scene),
        'objects': [_object_summary(o) for o in all_objects[:20]],
        'note': note,
    }


def get_object_info(name):
    """Detailed info for ONE object — AABB, mesh counts, materials, modifiers.
    Name lookup is Unicode-normalised (handles the Â· vs · corruption).
    """
    obj = _find_object(name)
    if obj is None:
        similar = [o.name for o in bpy.data.objects if name.lower()[:5] in o.name.lower()][:10]
        return {'error': f'Object not found: {name!r}', 'similar_names': similar}
    return _object_summary(obj, detail=True)


def inspect_scene(offset=0, limit=20, object_name=None, names=None):
    """Scene inspect — compact list (default 20, max 200 objects).
    - names: list of object names for targeted detailed lookup (most efficient)
    - object_name: filter by single name (Unicode-safe)
    - offset / limit: pagination
    """
    if not isinstance(offset, int) or offset < 0:
        raise ValueError('offset must be >= 0')
    limit = max(1, min(200, int(limit)))
    only_selected = getattr(bpy.context.scene, 'blendrelay_only_selected', False)
    all_objects = list(bpy.context.selected_objects) if only_selected else list(bpy.context.scene.objects)

    if names is not None:
        result_objs = []
        for n in names[:50]:
            obj = _find_object(n)
            if obj:
                result_objs.append(_object_summary(obj, detail=True))
            else:
                result_objs.append({'name': n, 'error': 'not found'})
        return dict(scene=bpy.context.scene.name, filepath=bpy.data.filepath,
                    mode=bpy.context.mode, edit_context=edit_context.capture(bpy.context), total=len(all_objects),
                    only_selected=only_selected,
                    selection=[o.name for o in bpy.context.selected_objects],
                    viewport_sketch=sketch.payload(bpy.context.scene),
                    objects=result_objs)

    if object_name is not None:
        filtered = [o for o in all_objects if _names_match(o.name, object_name)]
    else:
        filtered = all_objects

    return dict(scene=bpy.context.scene.name, filepath=bpy.data.filepath,
                mode=bpy.context.mode, edit_context=edit_context.capture(bpy.context), total=len(filtered),
                only_selected=only_selected,
                selection=[o.name for o in bpy.context.selected_objects],
                viewport_sketch=sketch.payload(bpy.context.scene),
                objects=[_object_summary(o) for o in filtered[offset:offset + limit]])


def restore_viewport_state():
    """Ensure viewport overlays and selection remain visible without changing editing mode."""
    try:
        # Ensure Viewport Overlays and Selection Highlighting are active across all 3D viewports
        wm = getattr(bpy.context, 'window_manager', None)
        if wm:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == 'VIEW_3D':
                        for space in area.spaces:
                            if space.type == 'VIEW_3D':
                                if not space.overlay.show_overlays:
                                    space.overlay.show_overlays = True
                                if not space.overlay.show_outline_selected:
                                    space.overlay.show_outline_selected = True
                        area.tag_redraw()
    except Exception:
        pass

    try:
        for obj in bpy.data.objects:
            if getattr(obj, 'hide_select', False):obj.hide_select=False
    except Exception:
        pass


def _scene_snapshot():
    return {'objects':set(bpy.data.objects.keys()),'materials':set(bpy.data.materials.keys()),
            'collections':set(bpy.data.collections.keys())}


def _scene_progress(before):
    current=_scene_snapshot();points=[]
    for obj in bpy.context.scene.objects:
        try:points.extend(tuple(obj.matrix_world @ __import__('mathutils').Vector(corner)) for corner in obj.bound_box)
        except Exception:pass
    bounds=[0,0,0]
    if points:
        bounds=[round(max(p[i] for p in points)-min(p[i] for p in points),3) for i in range(3)]
    return {'created_objects':sorted(current['objects']-before['objects'])[:200],
            'created_object_count':len(current['objects']-before['objects']),
            'created_materials':sorted(current['materials']-before['materials'])[:100],
            'created_collection_count':len(current['collections']-before['collections']),
            'scene_object_count':len(current['objects']),'scene_bounds':bounds}

def execute_code(code, label='Prompt edit'):
    """Execute Python WITHOUT checkpointing — fast path like blender-mcp.
    No .blend saves. Returns {executed, result, output}.
    """
    if not isinstance(code, str):
        raise ValueError('code must be Python text')
    before_state=_scene_snapshot()
    try:
        code = code.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    bpy_proxy, mathutils = _make_bpy_proxy()
    editing_state = edit_context.remember()
    namespace = {'bpy': bpy_proxy, 'mathutils': mathutils, 'br': scene_helpers, '__name__': '__blendrelay__'}
    try:
        compiled = compile(code, '<BlendRelay MCP>', 'exec')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compiled, namespace, namespace)
        value = namespace.get('result')
        value = json.loads(json.dumps(value, default=str))
        if len(json.dumps(value)) > 100000:
            value = {'truncated': True, 'text': str(value)[:100000]}
        return {'executed': True, 'result': value, 'output': buf.getvalue()[-10000:],
                'progress':_scene_progress(before_state)}
    except Exception:
        return {'executed': False, 'error': traceback.format_exc()[-8000:],
                'progress':_scene_progress(before_state)}
    finally:
        try:
            edit_context.restore(editing_state)
        except Exception as error:
            print("BlendRelay could not restore editing mode:", error)
        restore_viewport_state()


def execute(root, code, label='Prompt edit', save_checkpoint=False, **kwargs):
    """Execute Python code in Blender.
    Checkpoints are not saved continuously on every step by default.
    A checkpoint is saved automatically after the AI generation process finishes,
    or when save_checkpoint=True is explicitly requested.
    """
    if not isinstance(code, str):
        raise ValueError('code must be Python text')
    try:
        code = code.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    bpy_proxy, mathutils = _make_bpy_proxy()
    editing_state = edit_context.remember()
    namespace = {'bpy': bpy_proxy, 'mathutils': mathutils, 'br': scene_helpers, '__name__': '__blendrelay__'}
    before_state=_scene_snapshot()
    should_checkpoint = bool(save_checkpoint or kwargs.get('checkpoint', False))
    before = checkpoint(root, 'Before: ' + label) if should_checkpoint else None
    output = io.StringIO()
    try:
        compiled = compile(code, '<BlendRelay MCP>', 'exec')
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compiled, namespace, namespace)
        after = checkpoint(root, label) if should_checkpoint else None
        value = namespace.get('result')
        value = json.loads(json.dumps(value, default=str))
        if len(json.dumps(value)) > 100000:
            value = {'truncated': True, 'text': str(value)[:100000]}
        res = dict(output=output.getvalue()[-100000:], result=value,progress=_scene_progress(before_state))
        if before:
            res['before'] = before
        if after:
            res['after'] = after
        return res
    except Exception:
        res = dict(failed=True, error=traceback.format_exc()[-12000:],
                    output=output.getvalue()[-100000:],
                    progress=_scene_progress(before_state),
                    recovery='Fix the error in your script and continue. Do NOT restore the scene unless specifically instructed by the user.')
        if before:
            res['before'] = before
        return res
    finally:
        try:
            edit_context.restore(editing_state)
        except Exception as error:
            print("BlendRelay could not restore editing mode:", error)
        restore_viewport_state()


def build_batch(operations, label='Scene batch'):
    if not isinstance(operations, list) or not operations: raise ValueError('operations must be a non-empty list')
    created, materials = [], []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict): raise ValueError(f'operation {index} must be an object')
        kind = operation.get('type'); name = operation.get('name', f'BlendRelay {index + 1}')
        target = scene_helpers.collection(operation['collection']) if operation.get('collection') else None
        material = bpy.data.materials.get(operation.get('material', ''))
        obj = None
        if kind == 'collection': obj = scene_helpers.collection(name)
        elif kind == 'material':
            obj = scene_helpers.material(name, operation.get('color', (.8,.8,.8,1)), operation.get('metallic', 0), operation.get('roughness', .5)); materials.append(obj.name)
        elif kind == 'cube': obj = scene_helpers.cube(name, operation.get('location', (0,0,0)), operation.get('dimensions', (1,1,1)), material, target, operation.get('bevel', 0))
        elif kind == 'cylinder': obj = scene_helpers.cylinder(name, operation.get('location', (0,0,0)), operation.get('radius', 1), operation.get('depth', 2), operation.get('vertices', 32), material, target)
        elif kind in {'sphere','cone','plane','torus'}:
            operators = {'sphere': bpy.ops.mesh.primitive_uv_sphere_add, 'cone': bpy.ops.mesh.primitive_cone_add,
                         'plane': bpy.ops.mesh.primitive_plane_add, 'torus': bpy.ops.mesh.primitive_torus_add}
            kwargs = {'location': operation.get('location', (0,0,0)), 'rotation': operation.get('rotation', (0,0,0))}
            if kind == 'sphere': kwargs.update(segments=operation.get('vertices', 32), radius=operation.get('radius', 1))
            elif kind == 'cone': kwargs.update(vertices=operation.get('vertices', 32), radius1=operation.get('radius', 1), depth=operation.get('depth', 2))
            elif kind == 'plane': kwargs.update(size=operation.get('size', 2))
            else: kwargs.update(major_radius=operation.get('radius', 1), major_segments=operation.get('vertices', 48))
            operators[kind](**kwargs); obj = bpy.context.object; obj.name = name
            if operation.get('dimensions'): obj.dimensions = operation['dimensions']
            if target: scene_helpers._move_to(obj, target)
            if material: scene_helpers.assign(obj, material)
        elif kind == 'empty':
            obj = bpy.data.objects.new(name, None); (target or bpy.context.scene.collection).objects.link(obj); obj.location = operation.get('location', (0,0,0))
        elif kind == 'text':
            data = bpy.data.curves.new(name, 'FONT'); data.body = operation.get('text', '')
            obj = bpy.data.objects.new(name, data); (target or bpy.context.scene.collection).objects.link(obj); obj.location = operation.get('location', (0,0,0))
        elif kind in {'area_light','point_light','sun_light'}:
            light_type = {'area_light':'AREA','point_light':'POINT','sun_light':'SUN'}[kind]
            data = bpy.data.lights.new(name, light_type); data.energy = operation.get('energy', 1000); data.color = operation.get('color', (1,1,1))[:3]
            if light_type == 'AREA': data.shape = 'DISK'; data.size = operation.get('size', 5)
            obj = bpy.data.objects.new(name, data); (target or bpy.context.scene.collection).objects.link(obj)
            obj.location = operation.get('location', (0,0,5)); obj.rotation_euler = operation.get('rotation', (0,0,0))
        elif kind == 'camera': obj = scene_helpers.camera(name, operation.get('location', (10,-10,8)), operation.get('target', (0,0,0)), operation.get('lens', 45))
        elif kind == 'world': obj = scene_helpers.world(operation.get('color', (.05,.05,.05,1)), operation.get('strength', .5))
        elif kind == 'assign_material':
            obj = bpy.data.objects.get(operation.get('object', ''))
            if not obj or not material: raise ValueError(f'operation {index}: object and material must exist')
            scene_helpers.assign(obj, material)
        elif kind == 'transform':
            obj = bpy.data.objects.get(operation.get('object', ''))
            if not obj: raise ValueError(f'operation {index}: object does not exist')
            if 'location' in operation: obj.location = operation['location']
            if 'rotation' in operation: obj.rotation_euler = operation['rotation']
            if 'scale' in operation: obj.scale = operation['scale']
            if 'dimensions' in operation: obj.dimensions = operation['dimensions']
        elif kind == 'parent':
            obj = bpy.data.objects.get(operation.get('object', '')); parent = bpy.data.objects.get(operation.get('parent', ''))
            if not obj or not parent: raise ValueError(f'operation {index}: object and parent must exist')
            world = obj.matrix_world.copy(); obj.parent = parent; obj.matrix_world = world
        elif kind == 'duplicate':
            source = bpy.data.objects.get(operation.get('source', ''))
            if not source: raise ValueError(f'operation {index}: source does not exist')
            obj = source.copy(); obj.data = source.data.copy() if source.data else None; obj.name = name
            (target or bpy.context.scene.collection).objects.link(obj)
            if 'location' in operation: obj.location = operation['location']
        elif kind == 'delete':
            obj = bpy.data.objects.get(operation.get('object', name))
            if not obj: raise ValueError(f'operation {index}: object does not exist')
            bpy.data.objects.remove(obj, do_unlink=True); obj = None
        elif kind == 'modifier':
            obj = bpy.data.objects.get(operation.get('object', ''))
            if not obj: raise ValueError(f'operation {index}: object does not exist')
            obj.modifiers.new(name, operation.get('modifier_type', 'BEVEL'))
        elif kind == 'keyframe':
            obj = bpy.data.objects.get(operation.get('object', ''))
            if not obj: raise ValueError(f'operation {index}: object does not exist')
            obj.keyframe_insert(data_path=operation.get('data_path', 'location'), frame=operation.get('frame', bpy.context.scene.frame_current))
        else: raise ValueError(f'operation {index}: unsupported type {kind!r}')
        if obj is not None and hasattr(obj, 'rotation_euler'):
            if 'rotation' in operation and kind not in {'transform'}: obj.rotation_euler = operation['rotation']
            if 'scale' in operation and kind not in {'transform'}: obj.scale = operation['scale']
        if hasattr(obj, 'name') and kind not in {'material','world','assign_material','transform','parent','modifier','keyframe'}: created.append(obj.name)
    restore_viewport_state()
    return {'executed': True, 'label': label, 'operations': len(operations), 'created': created, 'materials': materials}


# ── Viewport capture ──────────────────────────────────────────────────────────

def draw_sketch_on_image(path, strokes):
    """Composite normalized viewport strokes onto an OpenGL viewport capture."""
    if not strokes:
        return
    image = bpy.data.images.load(str(path), check_existing=False)
    width, height = image.size
    pixels = array('f', [0.0]) * (width * height * 4)
    image.pixels.foreach_get(pixels)

    def pixel(x, y):
        if 0 <= x < width and 0 <= y < height:
            index = 4 * (y * width + x)
            pixels[index:index + 4] = array('f', (0.16, 0.72, 1.0, 1.0))

    def line(first, last):
        x0, y0 = round(first[0] * (width - 1)), round(first[1] * (height - 1))
        x1, y1 = round(last[0] * (width - 1)), round(last[1] * (height - 1))
        dx = abs(x1 - x0); step_x = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0); step_y = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    pixel(x0 + ox, y0 + oy)
            if x0 == x1 and y0 == y1:
                break
            doubled = 2 * error
            if doubled >= dy: error += dy; x0 += step_x
            if doubled <= dx: error += dx; y0 += step_y

    for stroke in strokes:
        for first, last in zip(stroke, stroke[1:]):
            line(first, last)
    image.pixels.foreach_set(pixels)
    image.filepath_raw = str(path)
    image.file_format = 'JPEG' if str(path).lower().endswith(('.jpg', '.jpeg')) else 'PNG'
    image.save()
    bpy.data.images.remove(image)


def screenshot(root, context=None, max_dimension=512):
    import base64
    area = (getattr(context, 'area', None)
            if context and getattr(context, 'area', None) and context.area.type == 'VIEW_3D'
            else None)
    if area is None:
        area = next((a for w in bpy.context.window_manager.windows
                     for a in w.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        raise RuntimeError('Open a 3D Viewport for a screenshot')
    window = (getattr(context, 'window', None)
              if context and getattr(context, 'window', None)
              and area in list(context.window.screen.areas) else None)
    if window is None:
        window = next(w for w in bpy.context.window_manager.windows if area in list(w.screen.areas))
    region = (getattr(context, 'region', None)
              if context and getattr(context, 'region', None)
              and context.region.type == 'WINDOW' else None)
    if region is None:
        region = next(r for r in area.regions if r.type == 'WINDOW')
    scene = bpy.context.scene
    r = scene.render
    old = (r.filepath, r.resolution_x, r.resolution_y,
           r.resolution_percentage, r.image_settings.file_format)
    path = workspace_root(root) / 'sketch_viewport.png'
    try:
        max_dim = max(128, min(1920, int(max_dimension)))
        rw, rh = max(1, region.width), max(1, region.height)
        if rw >= rh:
            r.resolution_x = max_dim
            r.resolution_y = max(1, round(max_dim * rh / rw))
        else:
            r.resolution_y = max_dim
            r.resolution_x = max(1, round(max_dim * rw / rh))
        r.resolution_percentage = 100
        r.filepath = str(path)
        r.image_settings.file_format = 'PNG'
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.render.opengl(write_still=True, view_context=True)
        draw_sketch_on_image(path, sketch.strokes(scene))
        data = path.read_bytes()
        mime_type = 'image/png'

        # Auto-optimize for AI transport if uncompressed PNG exceeds transport budget
        max_safe_bytes = 450000
        if len(data) > max_safe_bytes:
            jpg_path = path.with_suffix('.jpg')
            img = bpy.data.images.load(str(path), check_existing=False)
            try:
                w, h = img.size
                if max(w, h) > 720:
                    scale_factor = 720.0 / max(w, h)
                    img.scale(max(1, round(w * scale_factor)), max(1, round(h * scale_factor)))
                img.file_format = 'JPEG'
                img.filepath_raw = str(jpg_path)
                r.image_settings.quality = 85
                img.save()
            finally:
                bpy.data.images.remove(img)

            if jpg_path.is_file():
                jpg_data = jpg_path.read_bytes()
                if len(jpg_data) > max_safe_bytes:
                    img2 = bpy.data.images.load(str(jpg_path), check_existing=False)
                    try:
                        w2, h2 = img2.size
                        img2.scale(max(1, round(w2 * 0.75)), max(1, round(h2 * 0.75)))
                        r.image_settings.quality = 70
                        img2.save()
                    finally:
                        bpy.data.images.remove(img2)
                    jpg_data = jpg_path.read_bytes()

                data = jpg_data
                path = jpg_path
                mime_type = 'image/jpeg'

        return dict(image=base64.b64encode(data).decode(), mime_type=mime_type,
                    path=str(path), sketch_overlay=bool(sketch.strokes(scene)))
    finally:
        (r.filepath, r.resolution_x, r.resolution_y,
         r.resolution_percentage, r.image_settings.file_format) = old
        restore_viewport_state()


# ── Dispatcher ────────────────────────────────────────────────────────────────

def run(root, job):
    action = job['action']
    args = job.get('arguments', {})
    if action == 'asset_import':
        from .asset_import import import_cached
        return import_cached(root, **args)
    if action == 'inspect':
        return inspect_scene(**args)
    if action == 'inspect_data':
        return inspect_blender_data(**args)
    if action == 'get_scene_info':
        return get_scene_info()
    if action == 'get_object_info':
        return get_object_info(**args)
    if action == 'execute':
        return execute(root, **args)
    if action == 'execute_code':
        return execute_code(**args)
    if action == 'build_batch':
        return build_batch(**args)
    if action == 'versions':
        return versions(root)
    if action == 'checkpoint':
        return checkpoint(root, **args)
    if action == 'restore':
        return restore(root, **args)
    if action == 'screenshot':
        return screenshot(root, **args)
    if action == 'capture_focused_view':
        from . import focused_view
        return focused_view.capture(workspace_root(root), **args)
    if action == 'clear_data':
        return clear_stored_data(root, **args)
    if action in ('mesh_edit', 'validate_selection'):
        from . import mesh_tools
        return mesh_tools.edit(**args) if action == 'mesh_edit' else mesh_tools.validate(**args)
    raise ValueError('Unknown Blender action: ' + action)


def clear_stored_data(root, include_assets=False):
    """Clear data belonging to the current scene while preserving other scene workspaces."""
    base = Path(root).resolve()
    current = workspace_root(base)
    identity = workspace_id()
    if current.is_dir():
        shutil.rmtree(current, ignore_errors=True)
    current.mkdir(parents=True, exist_ok=True)
    (current / 'workspace.json').write_text(json.dumps({
        'workspace_id': identity, 'scene_name': bpy.context.scene.name,
        'blend_file': bpy.data.filepath or None}, indent=2), encoding='utf-8')
    if include_assets:
        assets = base / 'assets'
        if assets.is_dir(): shutil.rmtree(assets, ignore_errors=True)
        assets.mkdir(parents=True, exist_ok=True)
    return {'cleared': True, 'root': str(current), 'workspace_id': identity}
