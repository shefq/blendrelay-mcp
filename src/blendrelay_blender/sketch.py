# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport sketch capture stored as compact, normalized 2D strokes."""
import json
import bpy

PROPERTY = 'blendrelay_viewport_sketch'
TARGET_PROPERTY = 'blendrelay_viewport_sketch_targets'
MAX_STROKES = 100
MAX_POINTS_PER_STROKE = 2_000
_handler = None
_preview = []


def strokes(scene):
    try:
        raw = scene.get(PROPERTY, '[]')
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    clean = []
    for stroke in value[:MAX_STROKES]:
        if not isinstance(stroke, list):
            continue
        points = []
        for point in stroke[:MAX_POINTS_PER_STROKE]:
            if isinstance(point, list) and len(point) == 2 and all(isinstance(v, (int, float)) for v in point):
                points.append([round(min(1.0, max(0.0, float(point[0]))), 5), round(min(1.0, max(0.0, float(point[1]))), 5)])
        if len(points) >= 2:
            clean.append(points)
    return clean


def save(scene, value):
    scene[PROPERTY] = json.dumps(value, separators=(',', ':'))


def append(scene, stroke):
    value = strokes(scene)
    if len(stroke) >= 2:
        value.append(stroke[:MAX_POINTS_PER_STROKE])
    save(scene, value[-MAX_STROKES:])


def targets(scene):
    try:
        raw = scene.get(TARGET_PROPERTY, '[]')
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value[:500] if isinstance(item, dict) and isinstance(item.get('object'), str)
            and isinstance(item.get('world'), list) and len(item['world']) == 3]


def append_targets(scene, value):
    existing = targets(scene)
    seen = {(item['object'], tuple(item['world'])) for item in existing}
    for item in value:
        key = (item['object'], tuple(item['world']))
        if key not in seen:
            existing.append(item);seen.add(key)
    scene[TARGET_PROPERTY] = json.dumps(existing[-500:], separators=(',', ':'))


def clear(scene):
    # Remove, rather than merely overwrite, both saved values. This prevents a
    # stale custom-property value from surviving a Blender reload or an older
    # extension module still holding a cached scene reference.
    for property_name in (PROPERTY, TARGET_PROPERTY):
        if property_name in scene:
            del scene[property_name]


def payload(scene):
    value = strokes(scene)
    hit_targets = targets(scene)
    return {
        'coordinate_space': 'normalized viewport 2D; origin is lower-left',
        'stroke_count': len(value),
        'point_count': sum(len(stroke) for stroke in value),
        'strokes': value,
        'hit_objects': sorted({item['object'] for item in hit_targets}),
        'world_samples': hit_targets,
    }


def set_preview(stroke):
    global _preview
    _preview = stroke


def _draw_strokes():
    region = bpy.context.region
    if region is None or region.type != 'WINDOW':
        return
    value = strokes(bpy.context.scene) + ([_preview] if len(_preview) >= 2 else [])
    if not value:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(3.0)
    shader.bind()
    shader.uniform_float('color', (0.16, 0.72, 1.0, 0.95))
    for stroke in value:
        points = [(x * region.width, y * region.height) for x, y in stroke]
        if len(points) >= 2:
            batch_for_shader(shader, 'LINE_STRIP', {'pos': points}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def register_overlay():
    global _handler
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw_strokes, (), 'WINDOW', 'POST_PIXEL')


def unregister_overlay():
    global _handler, _preview
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    _preview = []
