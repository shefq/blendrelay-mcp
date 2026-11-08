# SPDX-License-Identifier: GPL-3.0-or-later
"""Persist HUD appearance independently of individual .blend scenes."""
import json
import math
from pathlib import Path
import bpy
from bpy.app.handlers import persistent

FIELDS = {
    'blendrelay_hud_width': (int, 500, 4000),
    'blendrelay_hud_height': (int, 90, 900),
    'blendrelay_hud_scale': (float, .6, 2.5),
    'blendrelay_hud_adaptive': (bool, None, None),
    'blendrelay_hud_x_offset': (float, -100000, 100000),
    'blendrelay_hud_y_offset': (float, -100000, 100000),
}
_applying = False
_pending = None


def settings_path():
    return Path(bpy.utils.user_resource('CONFIG', create=True)) / 'blendrelay_hud.json'


def validated(values):
    if not isinstance(values, dict):
        return {}
    result = {}
    for name, (kind, low, high) in FIELDS.items():
        value = values.get(name)
        if kind is bool:
            if isinstance(value, bool):
                result[name] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[name] = kind(max(low, min(high, value)))
    return result


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
            print('[BlendRelay] Could not save HUD settings:', error)
    return None


def changed(scene, context):
    global _pending
    if _applying:
        return
    _pending = {name: getattr(scene, name) for name in FIELDS}
    if not bpy.app.timers.is_registered(flush):
        bpy.app.timers.register(flush, first_interval=.4)


@persistent
def restore(_unused=None):
    global _applying
    flush()
    try:
        values = validated(json.loads(settings_path().read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return
    _applying = True
    try:
        for scene in bpy.data.scenes:
            for name, value in values.items():
                setattr(scene, name, value)
    finally:
        _applying = False


@persistent
def before_save(_unused=None):
    flush()


def restore_when_ready():
    # Add-on enable runs inside Blender's _RestrictContext. Scene data becomes
    # available only after register() returns to Blender's event loop.
    if not hasattr(bpy.data, 'scenes'):
        return .1
    restore()
    return None


def register():
    if restore not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(restore)
    if before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(before_save)
    if not bpy.app.timers.is_registered(restore_when_ready):
        bpy.app.timers.register(restore_when_ready, first_interval=.1)


def unregister():
    if bpy.app.timers.is_registered(restore_when_ready):
        bpy.app.timers.unregister(restore_when_ready)
    flush()
    if bpy.app.timers.is_registered(flush):
        bpy.app.timers.unregister(flush)
    if restore in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(restore)
    if before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(before_save)
