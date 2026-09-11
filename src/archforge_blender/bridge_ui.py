# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal connection and version UI for arbitrary Blender scenes."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
import bpy
from mathutils import Vector
from bpy.props import BoolProperty, StringProperty, EnumProperty, IntProperty, FloatProperty, CollectionProperty
from .client import Connection,default_root
from . import edit_context, workflow, run_metrics, conversation
from . import general, sketch, viewport_hud

STATE=dict(connection=None,instance=uuid.uuid4().hex,status='Disconnected',last_poll=0,versions=[],ack=None,root=None,
           codex_process=None,codex_output=None,codex_log=None,codex_started=0,
           agent_process=None,agent_output=None,agent_log=None,agent_backend='ANTIGRAVITY',agent_model='',agent_started=0)


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()


def refresh_versions():
    STATE['versions']=general.versions(STATE['root'] or default_root());redraw()


def default_antigravity_path():
    p_agy = Path(os.environ.get('LOCALAPPDATA', '')) / 'agy' / 'bin' / 'agy.exe'
    if p_agy.is_file():
        return str(p_agy)
    which_agy = shutil.which('agy')
    if which_agy:
        return which_agy
    p_user = Path.home() / '.local' / 'bin' / 'agy.exe'
    if p_user.is_file():
        return str(p_user)
    p_ide = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Antigravity IDE' / 'bin' / 'antigravity-ide.cmd'
    if p_ide.is_file():
        return str(p_ide)
    return str(p_agy)


def default_codex_path():
    p = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'OpenAI' / 'Codex' / 'bin' / 'codex.exe'
    if p.is_file():
        return str(p)
    return r'C:\Users\mshef\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe'


# ── Optional model overrides ───────────────────────────────────────────────


MODEL_DEFAULT = '__DEFAULT__'
MODEL_CUSTOM = '__CUSTOM__'
_MODEL_ITEMS = {
    'ANTIGRAVITY': [(MODEL_DEFAULT, 'CLI default', 'Use the model configured by Antigravity', 'AUTO', 0),
                    (MODEL_CUSTOM, 'Custom…', 'Enter a model identifier', 'OPTIONS', 1)],
    'CODEX': [(MODEL_DEFAULT, 'CLI default', 'Use the model configured by Codex', 'AUTO', 0),
              (MODEL_CUSTOM, 'Custom…', 'Enter a model identifier', 'OPTIONS', 1)],
}


def _enum_items(agent, models):
    items = [(MODEL_DEFAULT, 'CLI default', f'Use the model configured by {agent}', 'AUTO', 0)]
    seen = {MODEL_DEFAULT, MODEL_CUSTOM}
    for model_id, display_name, description in models:
        if model_id and model_id not in seen:
            seen.add(model_id)
            items.append((model_id, display_name or model_id, description or model_id, 'NONE', len(items)))
    items.append((MODEL_CUSTOM, 'Custom…', 'Enter a model identifier', 'OPTIONS', len(items)))
    return items


def discover_codex_models():
    models = []
    cache_path = Path.home() / '.codex' / 'models_cache.json'
    if cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding='utf-8', errors='replace'))
            for entry in data.get('models', []):
                if entry.get('visibility') == 'hide':
                    continue
                model_id = str(entry.get('slug', '')).strip()
                if model_id:
                    models.append((model_id, entry.get('display_name') or model_id,
                                   entry.get('description') or f'Codex model: {model_id}'))
        except Exception as error:
            STATE['model_status'] = f'Could not read Codex model cache: {error}'
    _MODEL_ITEMS['CODEX'] = _enum_items('Codex', models)
    return len(models)


def discover_antigravity_models():
    models = []
    executable = default_antigravity_path()
    if executable and (Path(executable).is_file() or shutil.which(executable)):
        try:
            completed = subprocess.run(
                [executable, 'models'], capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if completed.returncode == 0:
                for line in completed.stdout.splitlines():
                    line = line.strip()
                    if not line or 'fetching' in line.lower():
                        continue
                    parts = [part.strip() for part in line.split('\t')]
                    model_id = parts[0]
                    if model_id and ' ' not in model_id:
                        models.append((model_id, parts[1] if len(parts) > 1 else model_id,
                                       f'Antigravity model: {model_id}'))
            elif completed.stderr:
                STATE['model_status'] = completed.stderr.strip().splitlines()[-1][:180]
        except Exception as error:
            STATE['model_status'] = f'Antigravity model discovery failed: {error}'
    _MODEL_ITEMS['ANTIGRAVITY'] = _enum_items('Antigravity', models)
    return len(models)


def model_items_antigravity(self, context):
    return _MODEL_ITEMS['ANTIGRAVITY']


def model_items_codex(self, context):
    return _MODEL_ITEMS['CODEX']


def selected_model(scene, agent):
    if agent == 'ANTIGRAVITY':
        choice = getattr(scene, 'archforge_antigravity_model', MODEL_DEFAULT)
        custom = getattr(scene, 'archforge_antigravity_model_custom', '').strip()
    else:
        choice = getattr(scene, 'archforge_codex_model', MODEL_DEFAULT)
        custom = getattr(scene, 'archforge_codex_model_custom', '').strip()
    if choice == MODEL_DEFAULT:
        return ''
    if choice == MODEL_CUSTOM:
        return custom
    return choice


def refresh_models_async():
    def worker():
        codex_count = discover_codex_models()
        antigravity_count = discover_antigravity_models()
        STATE['model_status'] = f'{codex_count} Codex and {antigravity_count} Antigravity models found'
        STATE['need_redraw'] = True
    threading.Thread(target=worker, daemon=True).start()


def agent_paths(root, run_id):
    folder = Path(root) / 'agent_runs'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (run_id + '.log'), folder / (run_id + '.final.txt')


def codex_paths(root, run_id):
    return agent_paths(root, run_id)


def selection_context(context):
    selected=[]
    for obj in context.selected_objects[:50]:
        selected.append({'name':obj.name,'type':obj.type,'location':[round(v,4) for v in obj.location],
                         'dimensions':[round(v,4) for v in obj.dimensions]})
    return selected


SUPPORTED_REFERENCE_IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.exr', '.hdr'}


def reference_images(scene):
    """Return existing reference images, retaining the legacy single-image field."""
    raw = getattr(scene, 'archforge_reference_images', '[]')
    try:
        paths = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        paths = []
    if not isinstance(paths, list):
        paths = []
    legacy = getattr(scene, 'archforge_reference_image', '').strip()
    if legacy:
        paths.insert(0, legacy)
    unique = []
    seen = set()
    for value in paths:
        path = Path(str(value)).expanduser()
        if path.is_file() and path.suffix.lower() in SUPPORTED_REFERENCE_IMAGE_SUFFIXES:
            resolved = str(path.resolve())
            if resolved not in seen:
                seen.add(resolved)
                unique.append(resolved)
    return unique


def set_reference_images(scene, paths):
    unique = []
    seen = set()
    for value in paths:
        path = Path(str(value)).expanduser()
        if path.is_file() and path.suffix.lower() in SUPPORTED_REFERENCE_IMAGE_SUFFIXES:
            resolved = str(path.resolve())
            if resolved not in seen:
                seen.add(resolved)
                unique.append(resolved)
    scene.archforge_reference_images = json.dumps(unique, ensure_ascii=False)
    scene.archforge_reference_image = unique[0] if unique else ''
    return unique


def add_reference_images(context, paths):
    current = reference_images(context.scene)
    combined = set_reference_images(context.scene, [*current, *paths])
    added = len(combined) - len(current)
    from . import viewport_hud
    viewport_hud.HUD_STATE['flash_msg'] = f'Attached {added} image(s) · {len(combined)} total'
    viewport_hud.HUD_STATE['flash_time'] = time.monotonic()
    redraw()
    return added, combined


def capture_selection_views(root, run_id, selected_objects, angles=(35.0, 155.0, 275.0)):
    """Render three lightweight overview images framing the complete selection."""
    objects = [obj for obj in selected_objects if obj and obj.name in bpy.context.scene.objects]
    if not objects:
        return []

    points = []
    for obj in objects:
        if hasattr(obj, 'bound_box') and obj.bound_box:
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        else:
            points.append(obj.matrix_world.translation.copy())
    if not points:
        return []

    minimum = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
    maximum = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
    target = (minimum + maximum) * 0.5
    radius = max((maximum - minimum).length * 0.5, 0.5)
    distance = max(radius * 3.0, 2.0)
    output_dir = Path(root) / 'selection_views'
    output_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    render = scene.render
    original = (scene.camera, render.engine, render.resolution_x, render.resolution_y,
                render.resolution_percentage, render.filepath, render.image_settings.file_format,
                render.image_settings.quality)
    camera_data = bpy.data.cameras.new(f'ArchForge selection camera {run_id[:8]}')
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    scene.collection.objects.link(camera)
    camera.data.lens = 50
    camera.data.clip_start = 0.01
    camera.data.clip_end = max(distance * 8.0, 1000.0)
    paths = []
    try:
        scene.camera = camera
        render.engine = 'BLENDER_WORKBENCH'
        render.resolution_x = 384
        render.resolution_y = 288
        render.resolution_percentage = 100
        render.image_settings.file_format = 'JPEG'
        render.image_settings.quality = 70
        elevation = math.radians(24.0)
        for index, azimuth_degrees in enumerate(angles, start=1):
            azimuth = math.radians(azimuth_degrees)
            location = target + Vector((
                distance * math.cos(elevation) * math.cos(azimuth),
                distance * math.cos(elevation) * math.sin(azimuth),
                distance * math.sin(elevation),
            ))
            camera.location = location
            camera.rotation_euler = (target - location).to_track_quat('-Z', 'Y').to_euler()
            path = output_dir / f'{run_id}-view-{index}.jpg'
            render.filepath = str(path)
            bpy.context.view_layer.update()
            bpy.ops.render.render(write_still=True)
            if path.is_file():
                paths.append(str(path))
    finally:
        (scene.camera, render.engine, render.resolution_x, render.resolution_y,
         render.resolution_percentage, render.filepath, render.image_settings.file_format,
         render.image_settings.quality) = original
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(camera_data)
    return paths


def capture_sketch_viewport(context):
    if not sketch.payload(context.scene)['stroke_count']:
        return None
    root=Path(STATE['root'] or context.scene.archforge_runtime_dir).resolve()
    path=general.screenshot(root, context=context)['path']
    STATE['sketch_viewport']=path
    return path


def ensure_connected(context=None):
    if STATE.get('connection'):
        return True
    try:
        root = context.scene.archforge_runtime_dir if context and hasattr(context, 'scene') else (STATE['root'] or default_root())
        STATE['root'] = root
        conn = Connection(root)
        STATE['connection'] = conn
        STATE['last_poll'] = 0
        conn.request('blender.poll', dict(
            instance_id=STATE['instance'],
            scene_name=bpy.context.scene.name,
            filepath=bpy.data.filepath,
            selection=[o.name for o in bpy.context.selected_objects],
        ), received)
        conn.tick()
        STATE['status'] = 'Connected · general Blender mode'
        refresh_versions()
        return True
    except Exception as e:
        STATE['status'] = f'Connecting to runtime failed: {e}'
        return False


def format_tool_params(params):
    if not isinstance(params, dict):
        return str(params)[:80]
    items = []
    for k, v in params.items():
        val_str = repr(v)
        if len(val_str) > 60:
            val_str = val_str[:57] + '...'
        items.append(f"{k}={val_str}")
    return ', '.join(items)


def log_agent_stream(line, agent_name, log_file, collected_text):
    log_file.write(line)
    log_file.flush()

    line_clean = line.strip()
    if not line_clean:
        return

    if agent_name == 'Codex' and line_clean.startswith('{'):
        try:
            event = json.loads(line_clean)
            item = event.get('item', {})
            if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                message = item.get('text', '')
                collected_text.append(message + '\n')
                print(message, flush=True)
            elif event.get('type') in ('error', 'turn.failed'):
                print('[Codex] ' + str(event.get('message', event.get('error', event))), flush=True)
            return
        except ValueError:
            pass

    # Handle Antigravity stream-json events
    if agent_name == 'Antigravity' and line_clean.startswith('{'):
        try:
            data = json.loads(line_clean)
            event = data.get('event')
            if event == 'init':
                init_info = data.get('init', {})
                model = init_info.get('model', '')
                print(f"[{agent_name}] 🚀 Session initialized · Model: {model}", flush=True)
                return
            if event == 'step_update':
                step = data.get('step_update', {})
                stype = step.get('step_type')
                state = step.get('state')
                if stype == 'tool':
                    tool_name = step.get('tool_name', 'tool')
                    info = step.get('tool_info', {})
                    params = info.get('parameters', {})
                    if state == 'ACTIVE':
                        p_str = format_tool_params(params)
                        print(f"\n[{agent_name}] 🔧 Tool Call: {tool_name}({p_str})", flush=True)
                    elif state == 'DONE':
                        dur = step.get('duration_seconds', 0)
                        out = info.get('output', '')
                        out_prev = f" -> {str(out)[:80]}..." if out else ""
                        print(f"[{agent_name}] ✔ Tool {tool_name} completed ({dur:.2f}s){out_prev}", flush=True)
                    return
                elif stype == 'agent_response':
                    thought = step.get('thought_delta', '')
                    if thought:
                        print(f"[{agent_name}] 💭 {thought}", end='', flush=True)
                    delta = step.get('text_delta', '')
                    if delta:
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                        collected_text.append(delta)
                    return
                elif stype == 'error_message':
                    err_msg = step.get('error_message', '') or step.get('message', '') or str(step)
                    print(f"\n[{agent_name}] ❌ Error: {err_msg[:300]}", flush=True)
                    return
                elif stype == 'system_message':
                    return
            if event == 'result':
                res = data.get('result', {})
                status = res.get('status', 'SUCCESS')
                dur = res.get('duration_seconds', 0)
                usage = res.get('usage', {})
                tokens = usage.get('total_tokens', 0)
                tok_str = f" · tokens: {tokens:,}" if tokens else ""
                err_detail = res.get('error', '') or res.get('error_message', '')
                if err_detail:
                    print(f"\n[{agent_name}] ❌ Task failed ({status} in {dur:.1f}s): {str(err_detail)[:300]}", flush=True)
                else:
                    print(f"\n[{agent_name}] ✨ Finished ({status} in {dur:.1f}s{tok_str})", flush=True)
                return
        except Exception:
            pass

    # Skip noisy glog preamble lines from the CLI runner
    if line_clean.startswith('ERROR: logging before') or line_clean.startswith('Fetching available'):
        return

    # Standard / Codex console line
    print(f"[{agent_name}] {line_clean}", flush=True)
    if agent_name != 'Antigravity':
        collected_text.append(line)


def run_agent_reader(process, log_path, agent_name, final_path, conversation_path=None, backend=None):
    collected_text = []
    metrics = run_metrics.Metrics()
    try:
        with open(log_path, 'w', encoding='utf-8') as log_file:
            for line in iter(process.stdout.readline, ''):
                identifier = conversation.event_id(line)
                if identifier and conversation_path:
                    try:
                        conversation.save(conversation_path, backend, identifier)
                    except OSError as error:
                        print('Conversation ID could not be saved:', error, flush=True)
                    conversation_path = None  # Persist immediately, once per run.
                metrics.feed(line)
                log_agent_stream(line, agent_name, log_file, collected_text)
    except Exception as e:
        print(f"[{agent_name}] Stream reader error: {e}", flush=True)
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass
        report = metrics.summary()
        try:
            report['prompt_bytes'] = Path(log_path).with_suffix('.prompt.txt').stat().st_size
        except OSError:
            pass
        STATE['last_usage'] = report
        try:
            Path(log_path).with_suffix('.usage.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        except OSError:
            pass
        if final_path and collected_text and not Path(final_path).exists():
            try:
                Path(final_path).write_text(''.join(collected_text).strip(), encoding='utf-8')
            except Exception:
                pass


def start_agent(context, prompt_override=None):
    active_proc = STATE.get('agent_process') or STATE.get('codex_process')
    if active_proc and active_proc.poll() is None:
        agent_name = 'Antigravity' if STATE.get('agent_backend') == 'ANTIGRAVITY' else 'Codex'
        raise RuntimeError(f'An {agent_name} prompt is already running. Click Cancel Running Task or wait for it to finish.')
    prompt = (prompt_override or context.scene.archforge_codex_prompt).strip()
    agent = getattr(context.scene, 'archforge_agent_backend', 'ANTIGRAVITY')
    agent_name = 'Antigravity' if agent == 'ANTIGRAVITY' else 'Codex'
    if not prompt:
        raise RuntimeError(f'Enter a prompt for {agent_name}')

    mesh_edit = edit_context.capture(context)
    if mesh_edit and not mesh_edit['selected_count']:
        raise RuntimeError('Select vertices, edges or faces before sending an Edit Mode prompt.')
    if context.mode.startswith('EDIT_') and mesh_edit is None:
        raise RuntimeError('Selection-aware prompts currently support mesh Edit Mode. Switch to Object Mode for this object type.')

    ensure_connected(context)

    if agent == 'ANTIGRAVITY':
        exe_str = getattr(context.scene, 'archforge_antigravity_path', '').strip() or default_antigravity_path()
        executable = Path(exe_str)
        agy_bin = Path(os.environ.get('LOCALAPPDATA', '')) / 'agy' / 'bin' / 'agy.exe'
        if (not executable.is_file() or 'antigravity-ide' in str(executable).lower()) and agy_bin.is_file():
            executable = agy_bin
            try:
                context.scene.archforge_antigravity_path = str(agy_bin)
            except Exception:
                pass
        if not executable.is_file() and not shutil.which(str(executable)):
            raise RuntimeError(
                f'Antigravity CLI (agy.exe) not found at: {executable}. '
                'Please install it via: irm https://antigravity.google/cli/install.ps1 | iex'
            )
        model = selected_model(context.scene, agent)
    else:
        exe_str = getattr(context.scene, 'archforge_codex_path', '').strip() or default_codex_path()
        executable = Path(exe_str)
        if not executable.is_file() and not shutil.which(str(executable)):
            raise RuntimeError(f'Codex executable not found at: {executable}. Set its path in the ArchForge panel.')
        model = selected_model(context.scene, agent)

    root = Path(STATE['root'] or context.scene.archforge_runtime_dir).resolve()
    if not context.scene.get('archforge_workspace_id'):
        context.scene['archforge_workspace_id'] = uuid.uuid4().hex
    conversation_path = root / 'conversations' / (context.scene['archforge_workspace_id'] + '.json')
    resume_id = conversation.load(conversation_path).get(agent) if context.scene.archforge_continue_conversation else None
    sketch_data = sketch.payload(context.scene)
    sketch_viewport = STATE.get('sketch_viewport')
    if sketch_data['stroke_count'] and not sketch_viewport:
        try:
            sketch_viewport = capture_sketch_viewport(context)
        except Exception as error:
            sketch_viewport = 'Capture unavailable: ' + str(error)

    run_id = f'{agent.lower()}-' + uuid.uuid4().hex
    log_path, final_path = agent_paths(root, run_id)
    only_selected = getattr(context.scene, 'archforge_only_selected', False)
    auto_verify = getattr(context.scene, 'archforge_auto_verify', True)
    if only_selected and not context.selected_objects:
        raise RuntimeError('No objects selected. Select at least one object in the 3D Viewport or disable "Only selected objects".')

    selected_objs = selection_context(context) if (mesh_edit or context.scene.archforge_include_selection or only_selected) else []
    selection_view_paths = []
    if context.selected_objects and not mesh_edit:
        try:
            selection_view_paths = capture_selection_views(root, run_id, context.selected_objects, angles=(35.0,) if workflow.choose(context.scene.archforge_task_mode, prompt, True) == 'EDIT' else (35.0,155.0,275.0))
        except Exception as error:
            STATE['status'] = f'Selected-object image capture unavailable: {error}'
    if mesh_edit:
        try:
            capture = general.screenshot(root, context=context)
            source = Path(capture['path'])
            image = root / 'agent_runs' / (run_id + '-edit-selection' + source.suffix)
            shutil.copyfile(source, image)
            selection_view_paths = [str(image)]
        except Exception as error:
            STATE['status'] = f'Edit selection capture unavailable: {error}'
    reference_image_paths = reference_images(context.scene)
    ref_image_path = Path(reference_image_paths[0]) if reference_image_paths else None
    has_ref_image = bool(ref_image_path)

    context_text = {
        'scene': context.scene.name,
        'blend_file': bpy.data.filepath or 'unsaved Blender file',
        'only_selected': only_selected,
        'selected_objects': selected_objs,
        'selected_object_view_images': selection_view_paths,
        'viewport_sketch': sketch_data,
        'sketch_viewport_image': sketch_viewport,
        'reference_images': reference_image_paths,
        'archforge_instance_id': STATE['instance'],
        'agent': agent,
        'auto_verify': auto_verify,
        'model_override': model or None,
    }
    workdir = Path(bpy.data.filepath).parent if bpy.data.filepath else Path(root)

    task_mode = workflow.choose(context.scene.archforge_task_mode, prompt, bool(context.selected_objects))
    verification = context.scene.archforge_verification
    visual = verification == 'VISUAL' or (verification == 'AUTO' and auto_verify)
    system_instructions = workflow.instructions(task_mode, STATE['instance'], bool(mesh_edit), visual, only_selected)

    # Compact context JSON (trim large arrays to avoid hitting CLI arg limits)
    compact_ctx = {
        'mode': context.mode,
        'mesh_edit': mesh_edit,
        'scene': context_text['scene'],
        'blend_file': context_text['blend_file'],
        'instance_id': context_text['archforge_instance_id'],
        'agent': context_text['agent'],
        'only_selected': only_selected,
        'selected_objects': context_text['selected_objects'][:20],
        'selected_object_view_images': context_text['selected_object_view_images'],
        'sketch_stroke_count': context_text['viewport_sketch'].get('stroke_count', 0),
        'sketch_hit_objects': context_text['viewport_sketch'].get('hit_objects', [])[:10],
        'sketch_viewport_image': context_text['sketch_viewport_image'],
        'reference_images': context_text['reference_images'],
    }
    compact_ctx = workflow.compact(compact_ctx)
    context_snippet = json.dumps(compact_ctx, ensure_ascii=False, separators=(',', ':'))

    vision_image_path = ref_image_path
    if has_ref_image:
        try:
            need_opt = (ref_image_path.suffix.lower() not in ('.jpg', '.jpeg')) or (ref_image_path.stat().st_size > 1500000)
            if need_opt:
                out_jpg = root / 'reference_vision.jpg'
                b_img = bpy.data.images.load(str(ref_image_path), check_existing=False)
                w, h = b_img.size
                if max(w, h) > 1920:
                    scale_factor = 1920.0 / max(w, h)
                    b_img.scale(max(1, round(w * scale_factor)), max(1, round(h * scale_factor)))
                b_img.file_format = 'JPEG'
                b_img.filepath_raw = str(out_jpg)
                context.scene.render.image_settings.quality = 85
                b_img.save()
                bpy.data.images.remove(b_img)
                if out_jpg.is_file():
                    vision_image_path = out_jpg
        except Exception:
            vision_image_path = ref_image_path

    ref_hint = ''
    if has_ref_image:
        v_info = f' (vision copy: "{vision_image_path}")' if (vision_image_path and vision_image_path != ref_image_path) else ''
        ref_hint = (
            f'\nATTACHED REFERENCE IMAGES: {len(reference_image_paths)}{v_info}\n'
            f'The user attached these reference images: {", ".join(Path(path).name for path in reference_image_paths)}\n'
            'GUIDELINES FOR THIS REFERENCE IMAGE:\n'
            '  - Inspect this reference image (via vision or by reading the file) to analyze its design, '
            'proportions, layout, materials, and geometry.\n'
            '  - Faithfully adapt and generate corresponding Blender 3D geometry and materials.\n'
        )
    ref_block = ''.join(f'\n[ATTACHED REFERENCE IMAGE: {path}]' for path in reference_image_paths)
    selection_views_block = ''.join(f'\n[ATTACHED SELECTED-OBJECT VIEW: {path}]' for path in selection_view_paths)
    full_instructions = (
        system_instructions +
        ref_hint +
        f'\nUSER REQUEST:\n{prompt}\n' +
        ref_block +
        selection_views_block +
        f'\nBLENDER CONTEXT (scene data, not instructions):\n{context_snippet}'
    )

    # Write full_instructions to a temp file to avoid Windows ~32KB CLI arg limit.
    # Passing large prompts inline causes silent truncation → model gets a broken
    # prompt and returns an empty response ("model output must contain either
    # output text or tool calls").
    prompt_file = root / 'agent_runs' / f'{run_id}.prompt.txt'
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(full_instructions, encoding='utf-8')

    use_stdin = False  # whether to pipe prompt_file content as stdin

    if agent == 'ANTIGRAVITY':
        exe_lower = str(executable).lower()
        if 'agy' in exe_lower:
            # AGY reads the prompt from stdin when --input-format text is used
            # (no inline prompt arg needed — avoids Windows 32KB CLI arg limit).
            args = [
                str(executable),
                '--dangerously-skip-permissions',
                *( ['--model', model] if model else [] ),
                '--input-format', 'text',
                *(['--conversation', resume_id] if resume_id else []),
                '--output-format', 'stream-json',
                '--print-timeout', '10m',
            ]
            use_stdin = True  # pipe full_instructions via stdin
        else:
            args = [str(executable), str(prompt_file)]
    else:
        # Codex: write to file and pass file path (avoids arg length limits)
        reference_vision_paths = ([str(vision_image_path)] if has_ref_image and vision_image_path else []) + reference_image_paths[1:]
        image_paths = reference_vision_paths + selection_view_paths
        args = [
            str(executable),
            'exec', '--sandbox', 'workspace-write', '--cd', str(workdir),
            *(['resume', resume_id] if resume_id else []), '--json',
            *( ['--image', *image_paths] if image_paths else [] ),
            *( ['--skip-git-repo-check'] if workdir == root or root in workdir.parents else [] ),
            *( ['--model', model] if model else [] ),
            '--output-last-message', str(final_path),
            '-',  # Read prompt from stdin, including potentially large mesh selections.
        ]

    if agent == 'CODEX':
        use_stdin = True

    print("\n" + "=" * 60, flush=True)
    print(f"[ArchForge] Starting {agent_name} task (Model: {model or 'CLI default'})", flush=True)
    print(f"[ArchForge] User prompt: {prompt}", flush=True)
    print(f"[ArchForge] Prompt file: {prompt_file} ({prompt_file.stat().st_size:,} bytes)", flush=True)
    if only_selected:
        selected_names = [o.name for o in context.selected_objects[:10]]
        print(f"[ArchForge] 🎯 Restricted Scope: ONLY selected objects ({len(context.selected_objects)}): {', '.join(selected_names)}", flush=True)
    elif context.scene.archforge_include_selection:
        selected_names = [o.name for o in context.selected_objects[:10]]
        if selected_names:
            print(f"[ArchForge] Selected objects ({len(context.selected_objects)}): {', '.join(selected_names)}", flush=True)
    if sketch_viewport:
        print(f"[ArchForge] Viewport sketch attached: {sketch_viewport}", flush=True)
    if selection_view_paths:
        print(f"[ArchForge] Attached {len(selection_view_paths)} selected-object reference views", flush=True)
    print(f"[ArchForge] Log file: {log_path}", flush=True)
    print("=" * 60 + "\n", flush=True)

    def _try_launch(launch_args, stdin_text=None):
        return subprocess.Popen(
            launch_args,
            cwd=str(workdir),
            stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    stdin_content = full_instructions if use_stdin else None
    process = None
    try:
        process = _try_launch(args, stdin_text=stdin_content)
        # If piping via stdin, write the content and close stdin so the process
        # knows there's no more input — do this in a background thread to avoid
        # blocking the main thread while the process reads.
        if stdin_content:
            def _write_stdin(proc, text):
                try:
                    proc.stdin.write(text)
                    proc.stdin.close()
                except Exception:
                    pass
            threading.Thread(target=_write_stdin, args=(process, stdin_content), daemon=True).start()
    except Exception as err:
        print(f"[ArchForge] Failed to spawn {agent_name}: {err}", flush=True)
        raise

    reader_thread = threading.Thread(
        target=run_agent_reader,
        args=(process, log_path, agent_name, final_path, conversation_path if context.scene.archforge_continue_conversation else None, agent),
        daemon=True,
    )
    reader_thread.start()

    STATE.update(
        agent_process=process,
        agent_output=final_path,
        agent_log=log_path,
        agent_backend=agent,
        agent_model=model,
        agent_started=time.monotonic(),
        codex_process=process,
        codex_output=final_path,
        codex_log=log_path,
    )
    STATE['status'] = f'{agent_name} ({model}) is working on the Blender prompt…'
    redraw()


start_codex = start_agent


def check_agent():
    process = STATE.get('agent_process') or STATE.get('codex_process')
    if not process:
        return
    code = process.poll()
    if code is None:
        return
    output_path = STATE.get('agent_output') or STATE.get('codex_output')
    output = Path(output_path) if output_path else None
    message = output.read_text(encoding='utf-8', errors='replace').strip() if output and output.exists() else ''
    agent_name = 'Antigravity' if STATE.get('agent_backend') == 'ANTIGRAVITY' else 'Codex'
    log_path = STATE.get('agent_log') or STATE.get('codex_log')
    STATE['agent_process'] = None
    STATE['codex_process'] = None
    if code == 0:
        if not message and log_path and Path(log_path).exists():
            log_text = Path(log_path).read_text(encoding='utf-8', errors='replace').strip()
            clean_lines = [
                l.strip() for l in log_text.splitlines()
                if l.strip() and not l.startswith('ERROR: logging before') and not l.startswith('Fetching available') and not l.startswith('{')
            ]
            message = clean_lines[-1] if clean_lines else 'Completed'
        STATE['status'] = f'{agent_name} finished' + (f': {message[:200]}' if message else '')
        print(f"\n[ArchForge] {agent_name} task finished successfully (code 0).", flush=True)
        try:
            root = STATE.get('root') or default_root()
            user_prompt = getattr(bpy.context.scene, 'archforge_codex_prompt', '').strip() or 'AI edit'
            cp_entry = general.checkpoint(root, f"AI: {user_prompt[:80]}")
            print(f"[ArchForge] 💾 Checkpoint saved for completed task (version: {cp_entry['version_id'][:8]}).", flush=True)
        except Exception as cp_err:
            print(f"[ArchForge] Note: could not save post-generation checkpoint: {cp_err}", flush=True)
    else:
        log_name = Path(log_path).name if log_path else 'log'
        STATE['status'] = f'{agent_name} failed (exit {code}). Open log: {log_name}'
        print(f"\n[ArchForge] ❌ {agent_name} task failed (exit code {code}). Log: {log_path}", flush=True)
    print("=" * 60 + "\n", flush=True)
    refresh_versions()
    redraw()


check_codex = check_agent


def received(response):
    if 'error' in response:
        print(f"[ArchForge Bridge] ❌ Error from runtime: {response['error']['message']}", flush=True)
        raise RuntimeError(response['error']['message'])
    job = response['result'].get('command')
    STATE['status'] = 'Connected · general Blender mode'
    if job:
        action = job.get('action', 'command')
        op_id = job.get('operation_id', '')
        print(f"\n[ArchForge Bridge] ⚡ Executing job '{action}' (op_id: {op_id})...", flush=True)
        STATE['status'] = f'Running: {action}'
        redraw()
        try:
            result = general.run(STATE['root'], job)
            error = result.get('error') if isinstance(result, dict) and result.get('failed') else None
            if error:
                print(f"[ArchForge Bridge] ❌ Job '{action}' failed: {error}", flush=True)
            else:
                summary = str(result)[:120] if result else 'Done'
                print(f"[ArchForge Bridge] ✔ Job '{action}' completed: {summary}", flush=True)
        except Exception as e:
            result = None
            error = str(e)
            print(f"[ArchForge Bridge] ❌ Exception in job '{action}': {e}", flush=True)
        STATE['ack'] = dict(instance_id=STATE['instance'], operation_id=job['operation_id'], result=result, error=error)
        STATE['status'] = 'Edit failed · inspect version history' if error else 'Connected · operation finished'
        refresh_versions()
    redraw()


def timer():
    if STATE.get('need_redraw'):
        STATE['need_redraw'] = False
        redraw()
    check_codex()
    viewport_hud.ensure_hud_modal()
    c=STATE['connection']
    if c:
        try:
            c.tick()
            if not c.pending and STATE['ack']:
                payload=STATE['ack']
                def acknowledged(response):
                    if 'error' in response:raise RuntimeError(response['error']['message'])
                    STATE['ack']=None
                c.request('blender.ack',payload,acknowledged)
            elif not c.pending and time.monotonic()-STATE['last_poll']>.35:
                STATE['last_poll']=time.monotonic()
                c.request('blender.poll',dict(instance_id=STATE['instance'],scene_name=bpy.context.scene.name,
                          filepath=bpy.data.filepath,selection=[o.name for o in bpy.context.selected_objects]),received)
        except Exception as e:
            c.close();STATE['connection']=None;STATE['status']=str(e);redraw()
    else:
        if time.monotonic() - STATE['last_poll'] > 3.0:
            STATE['last_poll'] = time.monotonic()
            ensure_connected()
    return .05


class AF_OT_Connect(bpy.types.Operator):
    bl_idname='archforge.refresh';bl_label='Connect / Refresh'
    def execute(self,context):
        if STATE['connection']:STATE['connection'].close()
        STATE['root']=context.scene.archforge_runtime_dir
        STATE['connection']=Connection(STATE['root']);STATE['last_poll']=0
        STATE['status']='Connecting…';refresh_versions()
        return {'FINISHED'}


class AF_OT_Disconnect(bpy.types.Operator):
    bl_idname='archforge.disconnect';bl_label='Disconnect'
    def execute(self,context):
        if STATE['connection']:STATE['connection'].close()
        STATE['connection']=None;STATE['status']='Disconnected';return {'FINISHED'}


class AF_OT_Checkpoint(bpy.types.Operator):
    bl_idname='archforge.checkpoint';bl_label='Save version'
    def execute(self,context):
        try:general.checkpoint(STATE['root'] or context.scene.archforge_runtime_dir,'Manual checkpoint');refresh_versions()
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_VerifyAndFix(bpy.types.Operator):
    bl_idname = 'archforge.verify_and_fix'
    bl_label = 'Verify & Fix Scene'
    bl_description = 'Capture current 3D viewport, visually verify against prompt, and execute fixes'

    def execute(self, context):
        try:
            root = Path(STATE['root'] or context.scene.archforge_runtime_dir).resolve()
            shot = general.screenshot(root, context=context)
            shot_path = Path(shot['path'])
            if shot_path.is_file():
                add_reference_images(context, [shot_path])

            user_prompt = getattr(context.scene, 'archforge_codex_prompt', '').strip()
            if user_prompt:
                verify_prompt = (
                    f"VISUAL AUDIT & REPAIR PASS:\n"
                    f"User goal: {user_prompt}\n"
                    f"A fresh render of the 3D Viewport has been attached as a reference view. "
                    f"Carefully evaluate this render against the user's goal. "
                    f"Identify all defects (missing objects, floating geometry, clipping, distorted proportions, or material issues). "
                    f"Write and execute targeted Python code to fix all issues and bring the scene to perfection. "
                    f"Take a final screenshot to confirm the repair."
                )
            else:
                verify_prompt = (
                    "VISUAL AUDIT & REPAIR PASS:\n"
                    "A fresh render of the 3D Viewport has been attached as a reference view. "
                    "Carefully inspect the current 3D scene for visual and geometric defects "
                    "(floating meshes, colliding/intersecting objects, unnatural proportions, untextured/missing materials). "
                    "Write and execute targeted Python code to fix all issues and polish the scene. "
                    "Take a final screenshot to confirm the repair."
                )
            start_agent(context, prompt_override=verify_prompt)
            self.report({'INFO'}, 'AI Visual Verification & Repair task launched')
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_SendToAgent(bpy.types.Operator):
    bl_idname = 'archforge.send_to_agent'
    bl_label = 'Send to AI Agent'
    bl_description = 'Run a local Antigravity or Codex task with the prompt and selected-object context'
    def execute(self, context):
        try:
            start_agent(context)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_SendToCodex(bpy.types.Operator):
    bl_idname = 'archforge.send_to_codex'
    bl_label = 'Send to Codex'
    bl_description = 'Run a local Codex task with the prompt and selected-object context'
    def execute(self, context):
        try:
            start_agent(context)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_CancelAgent(bpy.types.Operator):
    bl_idname = 'archforge.cancel_agent'
    bl_label = 'Cancel AI Agent Task'
    bl_description = 'Terminate currently running Antigravity or Codex task'
    def execute(self, context):
        proc = STATE.get('agent_process') or STATE.get('codex_process')
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            STATE['agent_process'] = None
            STATE['codex_process'] = None
            STATE['status'] = 'Task cancelled by user'
            print("\n[ArchForge] 🛑 Task cancelled by user.\n" + "=" * 60 + "\n", flush=True)
            redraw()
        return {'FINISHED'}


class AF_OT_RefreshModels(bpy.types.Operator):
    bl_idname = 'archforge.refresh_models'
    bl_label = 'Refresh available models'
    bl_description = 'Reload models exposed by the selected CLI and the Codex local cache'

    def execute(self, context):
        refresh_models_async()
        self.report({'INFO'}, 'Refreshing model list…')
        return {'FINISHED'}

class AF_OT_AttachReferenceImage(bpy.types.Operator):
    bl_idname = 'archforge.attach_reference_image'
    bl_label = 'Attach Reference Image'
    bl_description = 'Attach an image file to guide the ArchForge AI prompt'

    filepath: StringProperty(subtype='FILE_PATH')

    def execute(self, context):
        if not self.filepath:
            return {'CANCELLED'}
        p = Path(bpy.path.abspath(self.filepath))
        if not p.is_file():
            self.report({'WARNING'}, f'File not found: {p}')
            return {'CANCELLED'}
        added, all_paths = add_reference_images(context, [p])
        self.report({'INFO'}, f'Attached {added} image(s); {len(all_paths)} total')
        return {'FINISHED'}


class AF_OT_BrowseReferenceImage(bpy.types.Operator):
    bl_idname = 'archforge.browse_reference_image'
    bl_label = 'Browse Reference Image'
    bl_description = 'Select a design or reference image to guide scene generation'

    directory: StringProperty(subtype='DIR_PATH')
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.exr;*.hdr', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        selected = ([Path(self.directory) / item.name for item in self.files]
                    if self.files else ([Path(self.filepath)] if self.filepath else []))
        if not selected:
            return {'CANCELLED'}
        added, all_paths = add_reference_images(context, selected)
        self.report({'INFO'}, f'Attached {added} image(s); {len(all_paths)} total')
        return {'FINISHED'}


class AF_OT_ClearReferenceImage(bpy.types.Operator):
    bl_idname = 'archforge.clear_reference_image'
    bl_label = 'Clear Reference Image'
    bl_description = 'Remove the attached reference image'

    def execute(self, context):
        set_reference_images(context.scene, [])
        from . import viewport_hud
        viewport_hud.HUD_STATE['flash_msg'] = 'All reference images removed'
        viewport_hud.HUD_STATE['flash_time'] = time.monotonic()
        for area in (context.screen.areas if context.screen else []):
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


class AF_OT_RemoveReferenceImage(bpy.types.Operator):
    bl_idname = 'archforge.remove_reference_image'
    bl_label = 'Remove Reference Image'
    bl_description = 'Remove one attached reference image'

    index: IntProperty(default=-1)

    def execute(self, context):
        paths = reference_images(context.scene)
        if 0 <= self.index < len(paths):
            paths.pop(self.index)
            set_reference_images(context.scene, paths)
        redraw()
        return {'FINISHED'}


if hasattr(bpy.types, 'FileHandler'):
    class AF_FH_ImageDrop(bpy.types.FileHandler):
        bl_idname = 'AF_FH_ImageDrop'
        bl_label = 'ArchForge AI Reference Images'
        bl_import_operator = 'archforge.attach_reference_image'
        bl_file_extensions = '.png;.jpg;.jpeg;.webp;.bmp'

        @classmethod
        def poll_drop(cls, context):
            return bool(context.area and context.area.type == 'VIEW_3D')
else:
    AF_FH_ImageDrop = None



class AF_OT_DrawSketch(bpy.types.Operator):
    bl_idname='archforge.draw_viewport_sketch';bl_label='Draw viewport sketch'
    bl_description='Draw freehand notes in the 3D Viewport; they are included in the next AI agent prompt and scene inspection'
    def invoke(self,context,event):
        if context.area.type!='VIEW_3D':self.report({'ERROR'},'Open the operator from a 3D Viewport');return {'CANCELLED'}
        self.area=context.area;self.stroke=[];self.hits=[];context.window.cursor_modal_set('CROSSHAIR')
        context.window_manager.modal_handler_add(self);self.report({'INFO'},'Draw with left mouse. Press Enter or right-click when finished.');return {'RUNNING_MODAL'}
    def point(self,event):
        region=next((r for r in self.area.regions if r.type=='WINDOW'),None)
        if region is None:return None
        x=(event.mouse_x-region.x)/region.width;y=(event.mouse_y-region.y)/region.height
        return [round(x,5),round(y,5)] if 0<=x<=1 and 0<=y<=1 else None
    def hit(self,context,event,point):
        region=next((r for r in self.area.regions if r.type=='WINDOW'),None)
        space=self.area.spaces.active
        if region is None or space is None or not getattr(space,'region_3d',None):return None
        try:
            from bpy_extras import view3d_utils
            coordinate=(event.mouse_x-region.x,event.mouse_y-region.y)
            origin=view3d_utils.region_2d_to_origin_3d(region,space.region_3d,coordinate)
            direction=view3d_utils.region_2d_to_vector_3d(region,space.region_3d,coordinate)
            hit,location,_,_,obj,_=context.scene.ray_cast(context.evaluated_depsgraph_get(),origin,direction)
            if hit:return {'object':obj.name,'world':[round(value,4) for value in location],'viewport':point}
        except Exception:pass
        return None
    def finish(self,context):
        sketch.set_preview([]);context.window.cursor_modal_restore()
        try:
            path=capture_sketch_viewport(context)
            if path:STATE['status']='Sketch viewport ready: '+Path(path).name
        except Exception as error:STATE['status']='Sketch saved, but viewport capture failed: '+str(error)
        redraw();return {'FINISHED'}
    def modal(self,context,event):
        point=self.point(event)
        if event.type in {'ESC'}:
            self.stroke=[];return self.finish(context)
        if event.type in {'RET','NUMPAD_ENTER','RIGHTMOUSE'}:
            return self.finish(context)
        if event.type=='LEFTMOUSE' and event.value=='PRESS':
            self.stroke=[point] if point else [];sketch.set_preview(self.stroke);return {'RUNNING_MODAL'}
        if event.type=='MOUSEMOVE' and self.stroke and point:
            if abs(point[0]-self.stroke[-1][0])+abs(point[1]-self.stroke[-1][1])>=.002:
                self.stroke.append(point)
                if len(self.stroke)%8==0:
                    hit=self.hit(context,event,point)
                    if hit:self.hits.append(hit)
                sketch.set_preview(self.stroke);redraw()
            return {'RUNNING_MODAL'}
        if event.type=='LEFTMOUSE' and event.value=='RELEASE':
            if len(self.stroke)>=2:
                sketch.append(context.scene,self.stroke);sketch.append_targets(context.scene,self.hits)
            self.stroke=[];sketch.set_preview([])
            return self.finish(context)
        return {'PASS_THROUGH'}


class AF_OT_ClearSketch(bpy.types.Operator):
    bl_idname='archforge.clear_viewport_sketch';bl_label='Clear viewport sketch'
    def execute(self,context):
        sketch.clear(context.scene);sketch.set_preview([])
        path=STATE.pop('sketch_viewport',None)
        if path:
            try:Path(path).unlink(missing_ok=True)
            except OSError:pass
        context.scene.update_tag();context.view_layer.update();redraw()
        STATE['status']='Viewport sketch cleared';return {'FINISHED'}


class AF_OT_NewSketch(bpy.types.Operator):
    bl_idname='archforge.new_viewport_sketch';bl_label='Start a new sketch'
    def execute(self,context):
        sketch.clear(context.scene)
        return bpy.ops.archforge.draw_viewport_sketch('INVOKE_DEFAULT')


class AF_OT_RestoreVersion(bpy.types.Operator):
    bl_idname='archforge.restore_version';bl_label='Restore version'
    version_id:StringProperty()
    def invoke(self,context,event):return context.window_manager.invoke_confirm(self,event)
    def execute(self,context):
        try:general.restore(STATE['root'] or context.scene.archforge_runtime_dir,self.version_id);refresh_versions()
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_AppendPromptTag(bpy.types.Operator):
    bl_idname = 'archforge.append_prompt_tag'
    bl_label = 'Append tag to prompt'
    tag: StringProperty(name='Tag', default='')

    def execute(self, context):
        current = context.scene.archforge_codex_prompt.strip()
        if not current:
            context.scene.archforge_codex_prompt = self.tag
        elif self.tag.lower() not in current.lower():
            context.scene.archforge_codex_prompt = f"{current}, {self.tag}"
        return {'FINISHED'}


class AF_OT_NewConversation(bpy.types.Operator):
    bl_idname = 'archforge.new_conversation'
    bl_label = 'New Conversation'
    bl_description = 'Start a fresh conversation for this workspace and backend'

    def execute(self, context):
        process = STATE.get('agent_process') or STATE.get('codex_process')
        if process and process.poll() is None:
            self.report({'WARNING'}, 'Wait for the current task to finish')
            return {'CANCELLED'}
        workspace = context.scene.get('archforge_workspace_id')
        if workspace:
            root = Path(STATE['root'] or context.scene.archforge_runtime_dir)
            conversation.save(root / 'conversations' / (workspace + '.json'), context.scene.archforge_agent_backend, None)
        self.report({'INFO'}, 'The next prompt will start a new conversation')
        return {'FINISHED'}


class AF_OT_ClearPrompt(bpy.types.Operator):
    bl_idname = 'archforge.clear_prompt'
    bl_label = 'Clear prompt'

    def execute(self, context):
        context.scene.archforge_codex_prompt = ''
        return {'FINISHED'}


class AF_OT_ToggleViewportHUD(bpy.types.Operator):
    bl_idname = 'archforge.toggle_viewport_hud'
    bl_label = 'Toggle Viewport HUD'
    bl_description = 'Show or hide the floating AI Viewport HUD bar'

    def execute(self, context):
        context.scene.archforge_show_viewport_hud = not context.scene.archforge_show_viewport_hud
        if context.scene.archforge_show_viewport_hud:
            viewport_hud.ensure_hud_modal()
        else:
            viewport_hud.HUD_STATE['modal_active'] = False
            viewport_hud.HUD_STATE['typing'] = False
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


class AF_OT_ResetHUDTransform(bpy.types.Operator):
    bl_idname = 'archforge.reset_hud_transform'
    bl_label = 'Reset HUD Layout'
    bl_description = 'Reset floating HUD to default position, width, and scale'

    def execute(self, context):
        context.scene.archforge_hud_width = 780
        context.scene.archforge_hud_scale = 1.0
        context.scene.archforge_hud_x_offset = 0.0
        context.scene.archforge_hud_y_offset = 0.0
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


class AF_PT_Main(bpy.types.Panel):
    bl_label = 'ArchForge AI'
    bl_idname = 'AF_PT_Main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'ArchForge'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # ── 1. Top Header Banner & Connection Status ────────────────────────
        header_box = layout.box()
        header_row = header_box.row(align=True)
        header_row.label(text='ArchForge Studio', icon='AUTO')
        header_row.operator(
            'archforge.toggle_viewport_hud',
            text='HUD',
            icon='WINDOW',
            depress=getattr(scene, 'archforge_show_viewport_hud', False),
        )
        header_row.label(text='v0.2.3')

        conn_row = header_box.row(align=True)
        conn = STATE.get('connection')
        if conn:
            conn_row.label(text='● Connected', icon='CHECKMARK')
            conn_row.operator('archforge.refresh', text='', icon='FILE_REFRESH')
            conn_row.operator('archforge.disconnect', text='', icon='X')
        else:
            conn_row.alert = True
            conn_row.label(text='● Disconnected', icon='ERROR')
            conn_row.operator('archforge.refresh', text='Connect', icon='PLAY')

        # ── 2. Segmented Pill Navigation Bar ────────────────────────────────
        nav_row = layout.row(align=True)
        nav_row.scale_y = 1.25
        nav_row.prop(scene, 'archforge_ui_tab', expand=True)

        layout.separator(factor=0.4)

        tab = getattr(scene, 'archforge_ui_tab', 'GENERATE')

        # ── TAB: GENERATE ───────────────────────────────────────────────────
        if tab == 'GENERATE':
            agent = getattr(scene, 'archforge_agent_backend', 'ANTIGRAVITY')
            agent_name = 'Antigravity' if agent == 'ANTIGRAVITY' else 'Codex'
            model = selected_model(scene, agent) or 'CLI default'

            # Active Task Running Banner
            active_proc = STATE.get('agent_process') or STATE.get('codex_process')
            if active_proc:
                task_box = layout.box()
                task_row = task_box.row(align=True)
                task_row.scale_y = 1.3
                task_row.alert = True
                task_row.label(text=f'⚡ {agent_name} is generating…', icon='TIME')
                task_row.operator('archforge.cancel_agent', text='Cancel', icon='CANCEL')
            elif STATE.get('status') and STATE['status'] != 'Disconnected':
                status_box = layout.box()
                s_row = status_box.row(align=True)
                s_row.label(text=f'Engine: {agent_name}', icon='CONSOLE')
                s_row.label(text=f'{model[:22]}', icon='RADIOBUT_ON')

            # Prompt Card
            prompt_card = layout.box()
            p_header = prompt_card.row(align=True)
            p_header.label(text='Prompt Instruction', icon='TEXT')
            if scene.archforge_codex_prompt:
                p_header.operator('archforge.clear_prompt', text='', icon='X', emboss=False)

            prompt_card.prop(scene, 'archforge_codex_prompt', text='')

            # Quick Tag Chips
            tags_row = prompt_card.row(align=True)
            tags_row.scale_y = 0.85
            tags = [
                ('+ Realistic Mats', 'with realistic materials and smooth shading'),
                ('+ Low Poly', 'stylized low poly aesthetic'),
                ('+ Bevel', 'with bevel modifiers on sharp edges'),
            ]
            for label, val in tags:
                op = tags_row.operator('archforge.append_prompt_tag', text=label)
                op.tag = val

            layout.separator(factor=0.3)

            # Engine and dynamically discovered model
            engine_card = layout.box()
            e_header = engine_card.row(align=True)
            e_header.label(text='Model & Engine', icon='AUTO')
            e_header.operator('archforge.refresh_models', text='', icon='FILE_REFRESH')
            e_row = engine_card.row(align=True)
            e_row.scale_y = 1.15
            e_row.prop(scene, 'archforge_agent_backend', expand=True)
            model_prop = 'archforge_antigravity_model' if agent == 'ANTIGRAVITY' else 'archforge_codex_model'
            custom_prop = 'archforge_antigravity_model_custom' if agent == 'ANTIGRAVITY' else 'archforge_codex_model_custom'
            engine_card.prop(scene, model_prop, text='Model')
            if getattr(scene, model_prop, MODEL_DEFAULT) == MODEL_CUSTOM:
                engine_card.prop(scene, custom_prop, text='Custom model')

            layout.separator(factor=0.3)

            # Reference Image Card
            ref_images = reference_images(scene)
            img_box = layout.box()
            i_head = img_box.row(align=True)
            i_head.label(text=f'Reference Images ({len(ref_images)})', icon='IMAGE_DATA')
            if ref_images:
                i_head.operator('archforge.clear_reference_image', text='', icon='X', emboss=False)
                for index, image_path in enumerate(ref_images):
                    i_row = img_box.row(align=True)
                    i_row.label(text=Path(image_path).name[:48], icon='CHECKMARK')
                    remove = i_row.operator('archforge.remove_reference_image', text='', icon='X', emboss=False)
                    remove.index = index
                img_box.operator('archforge.browse_reference_image', text='Add Images…', icon='ADD')
            else:
                img_box.operator('archforge.browse_reference_image', text='Add Images…', icon='FILE_FOLDER')
            img_box.label(text='Drop one or more images onto the 3D Viewport, or use Add Images.', icon='INFO')

            layout.separator(factor=0.3)

            layout.prop(scene, 'archforge_task_mode')
            layout.prop(scene, 'archforge_verification')
            row = layout.row(align=True)
            row.prop(scene, 'archforge_continue_conversation')
            row.operator('archforge.new_conversation', text='New Conversation')
            usage = STATE.get('last_usage', {})
            if usage:
                summary = usage.get('provider_usage', {})
                card = layout.box()
                card.label(text='Last run usage (provider reported)')
                for key in ('input_tokens','output_tokens','cache_read_tokens','cached_input_tokens','thinking_tokens'):
                    if key in summary:
                        card.label(text=key.replace('_',' ') + ': ' + format(summary[key], ','))
                card.label(text='Tool calls: ' + str(sum(usage.get('tool_calls_by_stage', {}).values())))
                if usage.get('warnings'):
                    card.label(text='Repeated requests detected; see .usage.json', icon='INFO')
            # Target Scope Card
            scope_card = layout.box()
            sc_header = scope_card.row(align=True)
            sc_header.label(text='Mesh Selection' if context.mode == 'EDIT_MESH' else 'Target Scope', icon='EDITMODE_HLT' if context.mode == 'EDIT_MESH' else 'OBJECT_DATAMODE')
            num_sel = len(context.selected_objects)
            if context.mode == 'EDIT_MESH':
                meshes = [obj.data for obj in context.objects_in_mode_unique_data if obj.type == 'MESH']
                counts = [sum(getattr(mesh, prop, 0) for mesh in meshes) for prop in ('total_vert_sel', 'total_edge_sel', 'total_face_sel')]
                sc_header.label(text=f'{counts[0]} V / {counts[1]} E / {counts[2]} F')
            else:
                sc_header.label(text=f'({num_sel} selected)')

            sc_row = scope_card.row(align=True)
            include_row = sc_row.row(align=True)
            include_row.enabled = context.mode != 'EDIT_MESH'
            include_row.prop(scene, 'archforge_include_selection', text='Mesh context included' if context.mode == 'EDIT_MESH' else 'Include selection', toggle=True)
            sc_row.prop(scene, 'archforge_only_selected', text='Only selected geometry' if context.mode == 'EDIT_MESH' else 'Only selected objects', toggle=True)

            if getattr(scene, 'archforge_only_selected', False):
                scope_card.label(text='Target: selected vertices / edges / faces' if context.mode == 'EDIT_MESH' else 'Target: selected objects', icon='RESTRICT_SELECT_OFF')

            layout.separator(factor=0.4)

            # Primary Call-To-Action Button
            ver_row = layout.row(align=True)
            ver_row.prop(scene, 'archforge_auto_verify', text='Auto-Verify & Fix (vision loop)', icon='VIEWZOOM')

            cta_row = layout.row(align=True)
            cta_row.scale_y = 1.55
            cta_row.operator('archforge.send_to_agent', text=f'✨ Generate with {agent_name}', icon='PLAY')
            cta_row.operator('archforge.verify_and_fix', text='👁 Verify & Fix', icon='IMAGE_DATA')

            sub_row = layout.row(align=True)
            if hasattr(bpy.ops.wm, 'console_toggle'):
                sub_row.operator('wm.console_toggle', text='Toggle Console', icon='CONSOLE')
            last_log = STATE.get('agent_log') or STATE.get('codex_log')
            if last_log and Path(last_log).exists():
                sub_row.label(text=f'Log: {Path(last_log).name[:20]}', icon='FILE_TEXT')

        # ── TAB: SKETCH ─────────────────────────────────────────────────────
        elif tab == 'SKETCH':
            sketch_box = layout.box()
            sketch_data = sketch.payload(context.scene)
            s_header = sketch_box.row(align=True)
            s_header.label(text='Viewport Sketching', icon='BRUSH_DATA')
            s_header.label(text=f'{sketch_data["stroke_count"]} strokes')

            if sketch_data['hit_objects']:
                hits_str = ', '.join(sketch_data['hit_objects'][:4])
                sketch_box.label(text=f'Targeting: {hits_str}', icon='RESTRICT_SELECT_OFF')

            btn_row = sketch_box.row(align=True)
            btn_row.scale_y = 1.3
            btn_row.operator('archforge.draw_viewport_sketch', text='Draw Stroke', icon='BRUSH_DATA')
            btn_row.operator('archforge.new_viewport_sketch', text='New', icon='FILE_NEW')
            btn_row.operator('archforge.clear_viewport_sketch', text='Clear', icon='TRASH')

            if STATE.get('sketch_viewport'):
                vp_box = sketch_box.box()
                vp_box.label(text='Snapshot: ' + Path(STATE['sketch_viewport']).name, icon='IMAGE_DATA')

        # ── TAB: HISTORY (Checkpoints) ──────────────────────────────────────
        elif tab == 'HISTORY':
            hist_box = layout.box()
            h_row = hist_box.row(align=True)
            h_row.label(text='Scene Versions', icon='RECOVER_LAST')
            h_row.label(text=f'{len(STATE["versions"])} saved')

            create_row = layout.row()
            create_row.scale_y = 1.2
            create_row.operator('archforge.checkpoint', text='💾 Save Checkpoint', icon='ADD')

            if not STATE['versions']:
                empty_card = layout.box()
                empty_card.label(text='No checkpoints saved yet.', icon='INFO')
                empty_card.label(text='Checkpoints are saved automatically after AI tasks.')
            else:
                list_box = layout.box()
                for entry in reversed(STATE['versions'][:15]):
                    row_v = list_box.row(align=True)
                    op = row_v.operator('archforge.restore_version', text=entry['label'][:38], icon='LOOP_BACK')
                    op.version_id = entry['version_id']

        # ── TAB: SETTINGS ───────────────────────────────────────────────────
        elif tab == 'SETTINGS':
            settings_box = layout.box()
            settings_box.label(text='Agent Backend & Models', icon='PREFERENCES')

            b_row = settings_box.row(align=True)
            b_row.scale_y = 1.15
            b_row.prop(scene, 'archforge_agent_backend', expand=True)

            agent = getattr(scene, 'archforge_agent_backend', 'ANTIGRAVITY')

            col = settings_box.column()
            col.use_property_split = True
            col.use_property_decorate = False

            if agent == 'ANTIGRAVITY':
                row = col.row(align=True)
                row.prop(scene, 'archforge_antigravity_model', text='Model')
                row.operator('archforge.refresh_models', text='', icon='FILE_REFRESH')
                if scene.archforge_antigravity_model == MODEL_CUSTOM:
                    col.prop(scene, 'archforge_antigravity_model_custom', text='Custom model')
                col.prop(scene, 'archforge_antigravity_path', text='CLI Path')
            else:
                row = col.row(align=True)
                row.prop(scene, 'archforge_codex_model', text='Model')
                row.operator('archforge.refresh_models', text='', icon='FILE_REFRESH')
                if scene.archforge_codex_model == MODEL_CUSTOM:
                    col.prop(scene, 'archforge_codex_model_custom', text='Custom model')
                col.prop(scene, 'archforge_codex_path', text='CLI Path')
            col.label(text=STATE.get('model_status', 'Models are loaded from the active CLI account.'), icon='INFO')

            layout.separator(factor=0.5)

            hud_box = layout.box()
            hud_box.label(text='Floating Viewport HUD', icon='WINDOW')
            col_hud = hud_box.column()
            col_hud.use_property_split = True
            col_hud.use_property_decorate = False
            col_hud.prop(scene, 'archforge_show_viewport_hud', text='Show HUD')
            col_hud.prop(scene, 'archforge_hud_width', text='Width (px)', slider=True)
            col_hud.prop(scene, 'archforge_hud_scale', text='Scale', slider=True)
            hud_box.operator('archforge.reset_hud_transform', text='Reset HUD Layout', icon='LOOP_BACK')

            layout.separator(factor=0.5)

            runtime_box = layout.box()
            runtime_box.label(text='Runtime Bridge', icon='NETWORK_DRIVE')
            col_rt = runtime_box.column()
            col_rt.use_property_split = True
            col_rt.use_property_decorate = False
            col_rt.prop(scene, 'archforge_runtime_dir', text='Data Directory')


CLASSES = (
    AF_OT_NewConversation,
    AF_OT_Connect,
    AF_OT_Disconnect,
    AF_OT_Checkpoint,
    AF_OT_VerifyAndFix,
    AF_OT_SendToAgent,
    AF_OT_SendToCodex,
    AF_OT_CancelAgent,
    AF_OT_DrawSketch,
    AF_OT_ClearSketch,
    AF_OT_NewSketch,
    AF_OT_RestoreVersion,
    AF_OT_AppendPromptTag,
    AF_OT_ClearPrompt,
    AF_OT_ToggleViewportHUD,
    AF_OT_ResetHUDTransform,
    AF_OT_RefreshModels,
    AF_OT_AttachReferenceImage,
    AF_OT_BrowseReferenceImage,
    AF_OT_ClearReferenceImage,
    AF_OT_RemoveReferenceImage,
    *( [AF_FH_ImageDrop] if AF_FH_ImageDrop else [] ),
    viewport_hud.AF_OT_ViewportHUDModal,
    AF_PT_Main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.archforge_continue_conversation = BoolProperty(name='Continue conversation', description='Resume this workspace conversation with fresh scene context', default=False)
    bpy.types.Scene.archforge_task_mode = EnumProperty(name='Workflow', items=[('AUTO','Auto','Choose based on selection and request'),('EDIT','Focused Edit','Targeted edits with corrections when needed'),('BUILD','Build','Larger scene work')],default='AUTO')
    bpy.types.Scene.archforge_verification = EnumProperty(name='Verification',items=[('AUTO','Auto','Geometry checks and relevant visuals when Auto-Verify is enabled'),('GEOMETRY','Geometry','Geometry checks without final images'),('VISUAL','Geometry + Visual','Geometry checks and targeted visual verification')],default='AUTO')
    bpy.types.Scene.archforge_runtime_dir = StringProperty(name='Runtime', subtype='DIR_PATH', default=default_root())
    bpy.types.Scene.archforge_codex_prompt = StringProperty(name='Prompt', default='')
    bpy.types.Scene.archforge_auto_verify = BoolProperty(
        name='Auto-verify & fix',
        description='After editing, automatically take a viewport render, verify against prompt, and execute fixes',
        default=True,
    )
    bpy.types.Scene.archforge_include_selection = BoolProperty(name='Include selected objects', default=True)
    bpy.types.Scene.archforge_only_selected = BoolProperty(
        name='Only selected objects',
        description='Restrict edits to selected objects, or selected mesh elements in Edit Mode',
        default=False,
    )
    bpy.types.Scene.archforge_ui_tab = EnumProperty(
        name='Panel Tab',
        description='Switch between ArchForge interface views',
        items=[
            ('GENERATE', 'Generate', 'AI prompt, target scope, and generation', 'AUTO', 0),
            ('SKETCH', 'Sketch', '2D viewport target sketch', 'BRUSH_DATA', 1),
            ('HISTORY', 'Versions', 'Scene checkpoints & history', 'RECOVER_LAST', 2),
            ('SETTINGS', 'Settings', 'Backend, model, and engine settings', 'PREFERENCES', 3),
        ],
        default='GENERATE',
    )

    bpy.types.Scene.archforge_agent_backend = EnumProperty(
        name='AI Agent',
        description='Choose which AI agent to run for Blender edits',
        items=[
            ('ANTIGRAVITY', 'Antigravity', 'Run Google Antigravity to edit the Blender scene'),
            ('CODEX', 'Codex', 'Run OpenAI Codex CLI to edit the Blender scene'),
        ],
        default='ANTIGRAVITY',
    )
    discover_codex_models()
    bpy.types.Scene.archforge_antigravity_model = EnumProperty(
        name='Antigravity model',
        description='Models reported by Antigravity; refresh after signing in',
        items=model_items_antigravity,
    )
    bpy.types.Scene.archforge_antigravity_model_custom = StringProperty(
        name='Custom model',
        default='',
        description='Custom Antigravity model identifier',
    )
    bpy.types.Scene.archforge_antigravity_path = StringProperty(
        name='Antigravity executable',
        subtype='FILE_PATH',
        default=default_antigravity_path(),
    )

    bpy.types.Scene.archforge_codex_model = EnumProperty(
        name='Codex model',
        description='Models available in the current Codex account cache',
        items=model_items_codex,
    )
    bpy.types.Scene.archforge_codex_model_custom = StringProperty(
        name='Custom model',
        default='',
        description='Custom Codex model identifier passed with --model',
    )
    bpy.types.Scene.archforge_codex_path = StringProperty(
        name='Codex executable',
        subtype='FILE_PATH',
        default=default_codex_path(),
    )
    bpy.types.Scene.archforge_reference_image = StringProperty(
        name='Reference Image',
        description='Path to attached reference image for AI prompt',
        subtype='FILE_PATH',
        default='',
    )
    bpy.types.Scene.archforge_reference_images = StringProperty(
        name='Reference Images',
        description='JSON list of image paths attached to the next AI prompt',
        default='[]',
    )

    bpy.types.Scene.archforge_show_viewport_hud = BoolProperty(
        name='Floating Viewport HUD',
        description='Display modern floating AI command bar directly in the 3D Viewport',
        default=True,
    )
    bpy.types.Scene.archforge_hud_width = IntProperty(
        name='HUD Width',
        description='Width of the floating AI command bar in pixels',
        default=780,
        min=500,
        max=1500,
    )
    bpy.types.Scene.archforge_hud_scale = FloatProperty(
        name='HUD Scale',
        description='Overall size multiplier for the floating AI command bar',
        default=1.0,
        min=0.6,
        max=1.6,
        step=5,
        precision=2,
    )
    bpy.types.Scene.archforge_hud_x_offset = FloatProperty(
        name='HUD X Offset',
        description='Horizontal position offset in the viewport',
        default=0.0,
    )
    bpy.types.Scene.archforge_hud_y_offset = FloatProperty(
        name='HUD Y Offset',
        description='Vertical position offset in the viewport',
        default=0.0,
    )

    sketch.register_overlay()
    viewport_hud.register_hud()
    refresh_models_async()
    bpy.app.timers.register(timer, first_interval=.1, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)
    if STATE['connection']:
        STATE['connection'].close()
    STATE['connection'] = None
    sketch.unregister_overlay()
    viewport_hud.unregister_hud()
    props_to_del = [
        'archforge_task_mode',
        'archforge_continue_conversation',
        'archforge_verification',
        'archforge_runtime_dir',
        'archforge_codex_prompt',
        'archforge_include_selection',
        'archforge_only_selected',
        'archforge_auto_verify',
        'archforge_show_viewport_hud',
        'archforge_hud_width',
        'archforge_hud_scale',
        'archforge_hud_x_offset',
        'archforge_hud_y_offset',
        'archforge_ui_tab',
        'archforge_agent_backend',
        'archforge_antigravity_model',
        'archforge_antigravity_model_custom',
        'archforge_antigravity_path',
        'archforge_codex_model',
        'archforge_codex_model_custom',
        'archforge_codex_path',
        'archforge_reference_image',
        'archforge_reference_images',
    ]
    for prop in props_to_del:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
