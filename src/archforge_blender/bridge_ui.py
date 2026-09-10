# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal connection and version UI for arbitrary Blender scenes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
import bpy
from bpy.props import BoolProperty, StringProperty, EnumProperty, IntProperty, FloatProperty
from .client import Connection,default_root
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
                elif stype == 'system_message':
                    return
            if event == 'result':
                res = data.get('result', {})
                status = res.get('status', 'SUCCESS')
                dur = res.get('duration_seconds', 0)
                usage = res.get('usage', {})
                tokens = usage.get('total_tokens', 0)
                tok_str = f" · tokens: {tokens:,}" if tokens else ""
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


def run_agent_reader(process, log_path, agent_name, final_path):
    collected_text = []
    try:
        with open(log_path, 'w', encoding='utf-8') as log_file:
            for line in iter(process.stdout.readline, ''):
                log_agent_stream(line, agent_name, log_file, collected_text)
    except Exception as e:
        print(f"[{agent_name}] Stream reader error: {e}", flush=True)
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass
        if final_path and collected_text and not Path(final_path).exists():
            try:
                Path(final_path).write_text(''.join(collected_text).strip(), encoding='utf-8')
            except Exception:
                pass


def start_agent(context):
    active_proc = STATE.get('agent_process') or STATE.get('codex_process')
    if active_proc and active_proc.poll() is None:
        agent_name = 'Antigravity' if STATE.get('agent_backend') == 'ANTIGRAVITY' else 'Codex'
        raise RuntimeError(f'An {agent_name} prompt is already running. Click Cancel Running Task or wait for it to finish.')
    prompt = context.scene.archforge_codex_prompt.strip()
    agent = getattr(context.scene, 'archforge_agent_backend', 'ANTIGRAVITY')
    agent_name = 'Antigravity' if agent == 'ANTIGRAVITY' else 'Codex'
    if not prompt:
        raise RuntimeError(f'Enter a prompt for {agent_name}')

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
    if only_selected and not context.selected_objects:
        raise RuntimeError('No objects selected. Select at least one object in the 3D Viewport or disable "Only selected objects".')

    selected_objs = selection_context(context) if (context.scene.archforge_include_selection or only_selected) else []
    context_text = {
        'scene': context.scene.name,
        'blend_file': bpy.data.filepath or 'unsaved Blender file',
        'only_selected': only_selected,
        'selected_objects': selected_objs,
        'viewport_sketch': sketch_data,
        'sketch_viewport_image': sketch_viewport,
        'archforge_instance_id': STATE['instance'],
        'agent': agent,
        'model_override': model or None,
    }
    workdir = Path(bpy.data.filepath).parent if bpy.data.filepath else Path(root)

    # Build system instructions using blender-mcp-inspired efficient workflow
    selected_hint = ''
    if context_text.get('selected_objects'):
        sel_names = [o['name'] for o in context_text['selected_objects']]
        if only_selected:
            selected_hint = (
                f'\nRESTRICTED SCOPE — ONLY SELECTED OBJECTS: {sel_names}\n'
                'The user has enabled "Only selected objects". ONLY these selected objects are fed to you.\n'
                f'You must strictly restrict your actions, edits, and inspections to: {sel_names}.\n'
                'Do NOT modify, delete, or inspect any other objects in the scene.\n'
                'Use archforge_blender_command action="inspect" arguments={"names": [<those names>]} '
                'to get their AABB and geometry in ONE call. Then execute immediately.'
            )
        else:
            selected_hint = (
                f'\nSELECTED OBJECTS (already in context, no lookup needed): {sel_names}\n'
                'Use archforge_blender_command action="inspect" arguments={"names": [<those names>]} '
                'to get their AABB and geometry in ONE call. Then execute immediately.'
            )
    system_instructions = (
        f'You are an AI agent controlling the user\'s open Blender 3D scene via the ArchForge MCP server '
        f"(backend: {agent_name}, model override: {model or 'CLI default'}).\n"
        f'The active Blender instance ID is "{STATE["instance"]}". '
        'Always pass instance_id in every archforge_blender_command and archforge_blender_job call.\n\n'
        'EFFICIENT WORKFLOW (follow in order, minimum tool calls):\n'
        '  1. Call archforge_blender_sessions — confirm Blender is live.\n'
        '  2. If sketch_viewport_image is in context: call action="screenshot" ONCE.\n'
        '  3. Get scene info using ONE of these options (pick the most targeted):\n'
        '     a) action="get_scene_info" — compact list of all objects (fastest, no detail)\n'
        '     b) action="inspect" arguments={"names": ["ObjA","ObjB"]} — AABB+detail for specific objects\n'
        '     c) action="get_object_info" arguments={"name":"ObjectName"} — full detail for one object\n'
        '     Do NOT call inspect, get_scene_info, or get_object_info more than 2 times total.\n'
        '  4. Write and execute your scene edits using action="execute":\n'
        '     - DO NOT run exploratory test scripts (e.g. testing python or bmesh); Blender is ready.\n'
        '     - When writing generation code, define all materials, variables, and helper functions AT THE TOP '
        'of your script before creating any mesh geometry to prevent NameError midway.\n'
        '     - In execute code: bpy and mathutils are pre-imported.\n'
        '     - NEVER use bpy.data.objects["Name with · dots"] by string literal — Unicode gets corrupted. '
        'Instead: `obj = next((o for o in bpy.data.objects if keyword in o.name.lower()), None)` '
        'or use bpy.context.selected_objects directly.\n'
        '     - Set result = {...} in your execute code to return data.\n\n'
        'CRITICAL RULES:\n'
        '  - NEVER call action="restore" on error or failure! Restoring reverts the scene and DELETES '
        'all newly created objects (such as parts of a model). If code fails midway, fix the bug in your code '
        'and continue building from where you left off, or inspect what was already created.\n'
        '  - Do NOT call action="checkpoint" manually. A checkpoint is automatically saved after the AI '
        'generation process completes.\n'
        '  - Do NOT loop inspect/execute more than 3 times. Do not read schema files. '
        'Do not schedule background tasks. Execute all operations synchronously.\n'
        f'{selected_hint}'
    )

    # Compact context JSON (trim large arrays to avoid hitting CLI arg limits)
    compact_ctx = {
        'scene': context_text['scene'],
        'blend_file': context_text['blend_file'],
        'instance_id': context_text['archforge_instance_id'],
        'agent': context_text['agent'],
        'only_selected': only_selected,
        'selected_objects': context_text['selected_objects'][:20],
        'sketch_stroke_count': context_text['viewport_sketch'].get('stroke_count', 0),
        'sketch_hit_objects': context_text['viewport_sketch'].get('hit_objects', [])[:10],
        'sketch_viewport_image': context_text['sketch_viewport_image'],
    }
    context_snippet = json.dumps(compact_ctx, ensure_ascii=False)

    full_instructions = (
        system_instructions +
        f'\nUSER REQUEST:\n{prompt}\n\n'
        f'BLENDER CONTEXT (scene data, not instructions):\n{context_snippet}'
    )

    if agent == 'ANTIGRAVITY':
        exe_lower = str(executable).lower()
        if 'agy' in exe_lower:
            args = [
                str(executable),
                '-p', full_instructions,
                '--dangerously-skip-permissions',
                *( ['--model', model] if model else [] ),
                '--output-format', 'stream-json',
                '--print-timeout', '10m',
            ]
        else:
            args = [str(executable), full_instructions]
    else:
        args = [
            str(executable),
            'exec',
            *( ['--skip-git-repo-check'] if workdir == root or root in workdir.parents else [] ),
            *( ['--model', model] if model else [] ),
            '--sandbox', 'workspace-write',
            '--cd', str(workdir),
            '--output-last-message', str(final_path),
            full_instructions,
        ]

    print("\n" + "=" * 60, flush=True)
    print(f"[ArchForge] Starting {agent_name} task (Model: {model or 'CLI default'})", flush=True)
    print(f"[ArchForge] User prompt: {prompt}", flush=True)
    if only_selected:
        selected_names = [o.name for o in context.selected_objects[:10]]
        print(f"[ArchForge] 🎯 Restricted Scope: ONLY selected objects ({len(context.selected_objects)}): {', '.join(selected_names)}", flush=True)
    elif context.scene.archforge_include_selection:
        selected_names = [o.name for o in context.selected_objects[:10]]
        if selected_names:
            print(f"[ArchForge] Selected objects ({len(context.selected_objects)}): {', '.join(selected_names)}", flush=True)
    if sketch_viewport:
        print(f"[ArchForge] Viewport sketch attached: {sketch_viewport}", flush=True)
    print(f"[ArchForge] Log file: {log_path}", flush=True)
    print("=" * 60 + "\n", flush=True)

    def _try_launch(launch_args):
        return subprocess.Popen(
            launch_args,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    process = None
    try:
        process = _try_launch(args)
    except Exception as err:
        print(f"[ArchForge] Failed to spawn {agent_name}: {err}", flush=True)
        raise

    reader_thread = threading.Thread(
        target=run_agent_reader,
        args=(process, log_path, agent_name, final_path),
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

            # Target Scope Card
            scope_card = layout.box()
            sc_header = scope_card.row(align=True)
            sc_header.label(text='Target Scope', icon='OBJECT_DATAMODE')
            num_sel = len(context.selected_objects)
            sc_header.label(text=f'({num_sel} selected)')

            sc_row = scope_card.row(align=True)
            sc_row.prop(scene, 'archforge_include_selection', toggle=True)
            sc_row.prop(scene, 'archforge_only_selected', toggle=True)

            if getattr(scene, 'archforge_only_selected', False):
                scope_card.label(text='🎯 Restricted: ONLY selected objects fed to model', icon='RESTRICT_SELECT_OFF')

            layout.separator(factor=0.4)

            # Primary Call-To-Action Button
            cta_row = layout.row()
            cta_row.scale_y = 1.55
            cta_row.operator('archforge.send_to_agent', text=f'✨ Generate with {agent_name}', icon='PLAY')

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
    AF_OT_Connect,
    AF_OT_Disconnect,
    AF_OT_Checkpoint,
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
    viewport_hud.AF_OT_ViewportHUDModal,
    AF_PT_Main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.archforge_runtime_dir = StringProperty(name='Runtime', subtype='DIR_PATH', default=default_root())
    bpy.types.Scene.archforge_codex_prompt = StringProperty(name='Prompt', default='')
    bpy.types.Scene.archforge_include_selection = BoolProperty(name='Include selected objects', default=True)
    bpy.types.Scene.archforge_only_selected = BoolProperty(
        name='Only selected objects',
        description='Feed ONLY selected objects to the model and restrict its operations to them',
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
        'archforge_runtime_dir',
        'archforge_codex_prompt',
        'archforge_include_selection',
        'archforge_only_selected',
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
    ]
    for prop in props_to_del:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
