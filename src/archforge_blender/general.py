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
from . import sketch


# ── Version / checkpoint helpers ─────────────────────────────────────────────

def version_root(root):
    scene = bpy.context.scene
    if not scene.get('archforge_workspace_id'):
        scene['archforge_workspace_id'] = uuid.uuid4().hex
    identity = scene['archforge_workspace_id']
    if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
        raise RuntimeError('Invalid scene workspace ID')
    path = Path(root) / 'scene_versions' / identity
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
    working = Path(root) / 'restored_scenes' / (uuid.uuid4().hex + '.blend')
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
    """Compact dict for one object. detail=True adds AABB, mesh counts, materials."""
    info = {
        'name': obj.name,
        'type': obj.type,
        'location': [round(float(obj.location.x), 3),
                     round(float(obj.location.y), 3),
                     round(float(obj.location.z), 3)],
        'visible': obj.visible_get(),
    }
    if detail:
        info['rotation'] = [round(float(v), 4) for v in obj.rotation_euler]
        info['scale'] = [round(float(v), 4) for v in obj.scale]
        aabb = _object_aabb(obj)
        if aabb:
            info.update(aabb)
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            info['mesh'] = {
                'vertices': len(mesh.vertices),
                'edges': len(mesh.edges),
                'polygons': len(mesh.polygons),
            }
        if hasattr(obj.data, 'materials'):
            info['materials'] = [m.name if m else None for m in obj.data.materials]
        parent = getattr(obj, 'parent', None)
        if parent:
            info['parent'] = parent.name
        modifiers = [m.type for m in (getattr(obj, 'modifiers', None) or [])[:8]]
        if modifiers:
            info['modifiers'] = modifiers
    return info


# ── Named inspect / info functions (blender-mcp pattern) ─────────────────────

def get_scene_info():
    """Ultra-compact scene summary — max 20 objects, name/type/location only.
    Use get_object_info(name) for AABB and mesh detail on specific objects.
    """
    scene = bpy.context.scene
    only_selected = getattr(scene, 'archforge_only_selected', False)
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
    only_selected = getattr(bpy.context.scene, 'archforge_only_selected', False)
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
                    mode=bpy.context.mode, total=len(all_objects),
                    only_selected=only_selected,
                    selection=[o.name for o in bpy.context.selected_objects],
                    viewport_sketch=sketch.payload(bpy.context.scene),
                    objects=result_objs)

    if object_name is not None:
        filtered = [o for o in all_objects if _names_match(o.name, object_name)]
    else:
        filtered = all_objects

    return dict(scene=bpy.context.scene.name, filepath=bpy.data.filepath,
                mode=bpy.context.mode, total=len(filtered),
                only_selected=only_selected,
                selection=[o.name for o in bpy.context.selected_objects],
                viewport_sketch=sketch.payload(bpy.context.scene),
                objects=[_object_summary(o) for o in filtered[offset:offset + limit]])


def restore_viewport_state():
    """Ensure viewport overlays remain enabled, mode is OBJECT, and objects are selectable."""
    try:
        # Return to Object Mode if left in Edit/Sculpt/Paint mode
        if hasattr(bpy.context, 'mode') and bpy.context.mode != 'OBJECT':
            if hasattr(bpy.ops.object, 'mode_set') and bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass

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
        # Ensure objects in the scene are not locked from selection
        for obj in bpy.data.objects:
            if getattr(obj, 'hide_select', False):
                obj.hide_select = False
    except Exception:
        pass


def execute_code(code, label='Prompt edit'):
    """Execute Python WITHOUT checkpointing — fast path like blender-mcp.
    No .blend saves. Returns {executed, result, output}.
    """
    if not isinstance(code, str):
        raise ValueError('code must be Python text')
    try:
        code = code.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    bpy_proxy, mathutils = _make_bpy_proxy()
    namespace = {'bpy': bpy_proxy, 'mathutils': mathutils, '__name__': '__archforge__'}
    try:
        compiled = compile(code, '<ArchForge MCP>', 'exec')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compiled, namespace, namespace)
        value = namespace.get('result')
        value = json.loads(json.dumps(value, default=str))
        if len(json.dumps(value)) > 100000:
            value = {'truncated': True, 'text': str(value)[:100000]}
        return {'executed': True, 'result': value, 'output': buf.getvalue()[-10000:]}
    except Exception:
        return {'executed': False, 'error': traceback.format_exc()[-8000:]}
    finally:
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
    namespace = {'bpy': bpy_proxy, 'mathutils': mathutils, '__name__': '__archforge__'}
    should_checkpoint = bool(save_checkpoint or kwargs.get('checkpoint', False))
    before = checkpoint(root, 'Before: ' + label) if should_checkpoint else None
    output = io.StringIO()
    try:
        compiled = compile(code, '<ArchForge MCP>', 'exec')
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compiled, namespace, namespace)
        after = checkpoint(root, label) if should_checkpoint else None
        value = namespace.get('result')
        value = json.loads(json.dumps(value, default=str))
        if len(json.dumps(value)) > 100000:
            value = {'truncated': True, 'text': str(value)[:100000]}
        res = dict(output=output.getvalue()[-100000:], result=value)
        if before:
            res['before'] = before
        if after:
            res['after'] = after
        return res
    except Exception:
        res = dict(failed=True, error=traceback.format_exc()[-12000:],
                    output=output.getvalue()[-100000:],
                    recovery='Fix the error in your script and continue. Do NOT restore the scene unless specifically instructed by the user.')
        if before:
            res['before'] = before
        return res
    finally:
        restore_viewport_state()


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


def screenshot(root, context=None):
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
    path = Path(root) / 'sketch_viewport.png'
    try:
        max_dim = 800
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
    if action == 'inspect':
        return inspect_scene(**args)
    if action == 'get_scene_info':
        return get_scene_info()
    if action == 'get_object_info':
        return get_object_info(**args)
    if action == 'execute':
        return execute(root, **args)
    if action == 'execute_code':
        return execute_code(**args)
    if action == 'versions':
        return versions(root)
    if action == 'checkpoint':
        return checkpoint(root, **args)
    if action == 'restore':
        return restore(root, **args)
    if action == 'screenshot':
        return screenshot(root)
    raise ValueError('Unknown Blender action: ' + action)
