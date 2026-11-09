# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the agent, model and executable choices in Blender's config, not in .blend files.

The values still live on each scene (the panel and runs read them there), but the
Blender-wide copy in blendrelay_agent.json is applied to every scene whenever a file
loads, and any change in the panel is written back to it. Opening an old file
therefore never switches the agent or model the user picked.
"""
import json
from pathlib import Path
import bpy
from bpy.app.handlers import persistent

AGENTS = ('ANTIGRAVITY', 'CODEX', 'CLAUDE')
DEFAULT_AGENT = 'CODEX'
MODEL_DEFAULT = '__DEFAULT__'
MODEL_FIELDS = {f'blendrelay_{name}_model': agent
                for name, agent in (('antigravity', 'ANTIGRAVITY'), ('codex', 'CODEX'), ('claude', 'CLAUDE'))}
TEXT_FIELDS = tuple(f'blendrelay_{name}_{suffix}' for name in ('antigravity', 'codex', 'claude')
                    for suffix in ('model_custom', 'path'))
FIELDS = ('blendrelay_agent_backend', *MODEL_FIELDS, *TEXT_FIELDS)
# Applied when the user has not chosen yet, so an old file's saved agent is not used.
DEFAULTS = {'blendrelay_agent_backend': DEFAULT_AGENT, **{name: MODEL_DEFAULT for name in MODEL_FIELDS},
            **{f'blendrelay_{name}_model_custom': '' for name in ('antigravity', 'codex', 'claude')}}
MAX_TEXT = 4096
_applying = False
_pending = None
_prepare_model = None  # set by bridge_ui: makes a saved model selectable before its list is refreshed


def settings_path():
    return Path(bpy.utils.user_resource('CONFIG', create=True)) / 'blendrelay_agent.json'


def validated(values):
    if not isinstance(values, dict):
        return {}
    result = {}
    for name in FIELDS:
        value = values.get(name)
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            continue
        if name == 'blendrelay_agent_backend' and value not in AGENTS:
            continue
        if name in MODEL_FIELDS and not value.strip():
            continue
        result[name] = value
    return result


def load():
    """The Blender-wide settings, with defaults for anything never chosen."""
    try:
        saved = validated(json.loads(settings_path().read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        saved = {}
    return {**DEFAULTS, **saved}


def flush():
    global _pending
    if _pending is not None:
        values, _pending = _pending, None
        try:
            path = settings_path()
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(values, indent=2), encoding='utf-8')
            temporary.replace(path)
        except OSError as error:
            print('[BlendRelay] Could not save agent settings:', error)
    return None


def changed(scene, context):
    """Property update callback: remember the change for every file and scene."""
    global _pending
    if _applying:
        return
    values = {}
    for name in FIELDS:
        try:
            value = getattr(scene, name)
        except (AttributeError, TypeError):
            continue
        if isinstance(value, str):
            values[name] = value
    _pending = validated(values)
    if not bpy.app.timers.is_registered(flush):
        bpy.app.timers.register(flush, first_interval=.4)
    # Other scenes in this file follow at once; other files follow when loaded.
    apply(_pending, [other for other in bpy.data.scenes if other != scene])


def apply(values, scenes):
    global _applying
    _applying = True
    try:
        for scene in scenes:
            for name, value in values.items():
                if name in MODEL_FIELDS and _prepare_model:
                    _prepare_model(MODEL_FIELDS[name], value)
                try:
                    if getattr(scene, name, None) != value:
                        setattr(scene, name, value)
                except (AttributeError, TypeError, ValueError):
                    pass  # e.g. a model no longer offered; keep the scene's current choice
    finally:
        _applying = False


@persistent
def restore(_unused=None):
    flush()
    apply(load(), list(bpy.data.scenes))


@persistent
def before_save(_unused=None):
    flush()


def restore_when_ready():
    # Add-on enable runs inside Blender's _RestrictContext; scenes become
    # available only after register() returns to Blender's event loop.
    if not hasattr(bpy.data, 'scenes'):
        return .1
    restore()
    return None


def register(prepare_model=None):
    global _prepare_model
    _prepare_model = prepare_model
    if restore not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(restore)
    if before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(before_save)
    if not bpy.app.timers.is_registered(restore_when_ready):
        bpy.app.timers.register(restore_when_ready, first_interval=.1)


def unregister():
    global _prepare_model
    if bpy.app.timers.is_registered(restore_when_ready):
        bpy.app.timers.unregister(restore_when_ready)
    flush()
    if bpy.app.timers.is_registered(flush):
        bpy.app.timers.unregister(flush)
    if restore in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(restore)
    if before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(before_save)
    _prepare_model = None
