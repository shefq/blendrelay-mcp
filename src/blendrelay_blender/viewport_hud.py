# SPDX-License-Identifier: GPL-3.0-or-later
"""Modern Viewport Floating AI Command HUD for BlendRelay.

Rendered at 60 FPS in 3D Viewport 2D screen space via Blender's GPU & BLF modules.
Designed with a unique dark obsidian interface and electric cyan accents.
Features interactive width/height resizing (all edges & corner grip), titlebar drag-to-move,
scale zoom buttons (+/-), Ctrl+Wheel zoom, and rock-solid click hit-testing.
"""
import math
import time
from pathlib import Path
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from .prompt_editor import Editor

# ── Global HUD State ─────────────────────────────────────────────────────────

HUD_STATE = {
    'handler': None,
    'modal_active': False,
    'hover': None,
    'typing': False,
    'editor': Editor(),
    'text_region': None,
    'text_drag': None,
    'text_pointer': False,
    'region_bounds': {},
    'dragging': None,
    'drag_start_mouse': (0, 0),
    'drag_start_width': 780.0,
    'drag_start_height': 122.0,
    'drag_start_scale': 1.0,
    'drag_start_offset': (0.0, 0.0),
    'cursor_time': 0.0,
    'bounds': {},
    'last_mouse': (0, 0),
    'flash_msg': '',
    'flash_time': 0.0,
}


HUD_BASE_HEIGHT = 122.0
HUD_DESIGN_WIDTH = 780.0
HUD_VIEWPORT_MARGIN = 24.0
HUD_AUTO_WIDTH_FRACTION = 0.80
HUD_AUTO_SCALE_MIN = 0.50
HUD_AUTO_SCALE_MAX = 1.35


def _hud_layout_metrics(viewport_width, viewport_height, manual_width, user_scale, adaptive=True, manual_height=HUD_BASE_HEIGHT):
    """Return a HUD size that remains usable in the available viewport.

    Automatic mode uses an adaptive width (taking 80% of the active 3D viewport width)
    and an adaptive scale derived from the viewport dimensions. Manual mode
    preserves the user's chosen width. Both modes always fit within the
    available width and height.
    """
    rw = max(1.0, float(viewport_width))
    rh = max(1.0, float(viewport_height))
    # Adaptive mode treats this as a fine-tuning multiplier. Manual mode can
    # retain the larger effective scale produced by a high-resolution viewport.
    scale_limit = 1.6 if adaptive else 2.5
    user_scale = max(0.65, min(scale_limit, float(user_scale)))
    available_w = max(1.0, rw - HUD_VIEWPORT_MARGIN)
    available_h = max(1.0, rh - HUD_VIEWPORT_MARGIN)

    if adaptive:
        # Both width and scale are adaptive; width takes 80% of viewport width
        target_width = min(available_w, rw * HUD_AUTO_WIDTH_FRACTION)
        viewport_scale = max(
            HUD_AUTO_SCALE_MIN,
            min(HUD_AUTO_SCALE_MAX, (rw / 1440.0) ** 0.5),
        )
        requested_scale = viewport_scale * user_scale
        fit = min(1.0, available_w / target_width, available_h / (HUD_BASE_HEIGHT * requested_scale))
        effective_scale = requested_scale * fit
        width = min(available_w, target_width * fit)
        base_width = width / max(0.001, effective_scale)
    else:
        base_width = max(500.0, min(4000.0, float(manual_width)))
        requested_scale = user_scale
        fit = min(1.0, available_w / (base_width * requested_scale), available_h / (HUD_BASE_HEIGHT * requested_scale))
        effective_scale = requested_scale * fit
        width = base_width * effective_scale

    return {
        'base_width': base_width,
        'scale': effective_scale,
        'width': width,
        'height': min(available_h, max(90.0, min(900.0, float(manual_height))) * effective_scale),
        'auto_scaled': adaptive or fit < 0.999,
    }


# ── Geometry & Shader Utilities ──────────────────────────────────────────────

def _make_rounded_fan(x, y, w, h, r, segs=6):
    """Generate CCW vertices for a convex triangle fan representing a rounded rect."""
    r = max(0.0, min(r, min(w, h) / 2.0))
    cx, cy = x + w / 2.0, y + h / 2.0
    verts = [(cx, cy)]
    corners = [
        (x + w - r, y + h - r, 0.0, math.pi / 2.0),
        (x + r, y + h - r, math.pi / 2.0, math.pi),
        (x + r, y + r, math.pi, 3.0 * math.pi / 2.0),
        (x + w - r, y + r, 3.0 * math.pi / 2.0, 2.0 * math.pi),
    ]
    for ccx, ccy, a0, a1 in corners:
        for i in range(segs + 1):
            theta = a0 + (a1 - a0) * (i / segs)
            verts.append((ccx + r * math.cos(theta), ccy + r * math.sin(theta)))
    verts.append(verts[1])
    return verts


def _draw_rounded_box(x, y, w, h, r, fill_color, border_color=None, border_width=1.0, segs=6):
    """Draw a smooth rounded rectangle with optional border."""
    verts = _make_rounded_fan(x, y, w, h, r, segs)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')

    # Fill
    shader.bind()
    shader.uniform_float('color', fill_color)
    batch_for_shader(shader, 'TRI_FAN', {'pos': verts}).draw(shader)

    # Border
    if border_color and border_width > 0:
        gpu.state.line_width_set(border_width)
        shader.bind()
        shader.uniform_float('color', border_color)
        batch_for_shader(shader, 'LINE_STRIP', {'pos': verts[1:]}).draw(shader)
        gpu.state.line_width_set(1.0)

    gpu.state.blend_set('NONE')


def _draw_top_accent_line(x, y, w, h, r, color=(0.0, 0.85, 1.0, 0.9), width=2.0):
    """Draw a glowing cyan accent line along the top edge of the chassis."""
    r = max(0.0, min(r, min(w, h) / 2.0))
    pts = [
        (x + r + 4.0, y + h),
        (x + w - r - 4.0, y + h),
    ]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float('color', color)
    batch_for_shader(shader, 'LINE_STRIP', {'pos': pts}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_edge_highlight(x, y, h, scale, color=(0.0, 0.85, 1.0, 0.9)):
    """Draw glowing vertical bar when hovering left/right resize borders."""
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(max(1.5, 3.0 * scale))
    shader.bind()
    shader.uniform_float('color', color)
    pts = [(x, y + 10.0 * scale), (x, y + h - 10.0 * scale)]
    batch_for_shader(shader, 'LINE_STRIP', {'pos': pts}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_grip_lines(x, y, scale, color=(0.0, 0.85, 1.0, 0.9)):
    """Draw 3 sleek diagonal resize grip lines in the bottom right corner."""
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(max(1.0, 1.5 * scale))
    shader.bind()
    shader.uniform_float('color', color)
    lines = [
        (x + 14.0 * scale, y + 3.0 * scale),
        (x + 3.0 * scale, y + 14.0 * scale),
        (x + 14.0 * scale, y + 7.0 * scale),
        (x + 7.0 * scale, y + 14.0 * scale),
        (x + 14.0 * scale, y + 11.0 * scale),
        (x + 11.0 * scale, y + 14.0 * scale),
    ]
    batch_for_shader(shader, 'LINES', {'pos': lines}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_circle(cx, cy, r, color, segs=16):
    """Draw a smooth filled circle."""
    verts = [(cx, cy)]
    for i in range(segs + 1):
        theta = 2.0 * math.pi * (i / segs)
        verts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float('color', color)
    batch_for_shader(shader, 'TRI_FAN', {'pos': verts}).draw(shader)
    gpu.state.blend_set('NONE')


def _draw_text(text, x, y, size=13, color=(0.95, 0.95, 0.95, 1.0)):
    """Draw text at absolute 2D pixel coordinates."""
    font_id = 0
    blf.size(font_id, max(9, int(size)))
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def _draw_text_centered(text, bx, by, bw, bh, size=12, color=(0.95, 0.95, 0.95, 1.0)):
    """Draw text perfectly centered inside a bounding box."""
    font_id = 0
    blf.size(font_id, max(9, int(size)))
    tw, th = blf.dimensions(font_id, text)
    tx = bx + (bw - tw) / 2.0
    ty = by + (bh - th) / 2.0 + 1.0
    blf.color(font_id, *color)
    blf.position(font_id, tx, ty, 0)
    blf.draw(font_id, text)


def _truncate_left(text, max_w, size=13):
    """Truncate text keeping the end visible while typing."""
    font_id = 0
    blf.size(font_id, max(9, int(size)))
    if blf.dimensions(font_id, text)[0] <= max_w:
        return text
    ellipsis = '...'
    ew = blf.dimensions(font_id, ellipsis)[0]
    while text and blf.dimensions(font_id, text)[0] + ew > max_w:
        text = text[1:]
    return ellipsis + text


def _prompt_measure(text, size):
    blf.size(0, max(9, int(size)))
    return blf.dimensions(0, text.replace('\t', '    '))[0]


def _prompt_editor(scene):
    editor = HUD_STATE['editor']
    identity = scene.as_pointer() if hasattr(scene, 'as_pointer') else id(scene)
    editor.sync(getattr(scene, 'blendrelay_codex_prompt', ''), identity)
    return editor


def _prompt_geometry(editor, geometry):
    size = geometry['size']
    measure = lambda text: _prompt_measure(text, size)
    editor.layout(geometry['width'], measure, geometry['visible'], size)
    return measure


def _draw_prompt_editor(scene, bounds, x, y, width, height, scale, region):
    editor = _prompt_editor(scene)
    size = 13 * scale
    line_height = max(16 * scale, max(9, int(size)) + 3 * scale)
    geometry = dict(x=x + 12 * scale, top=y + height - 22 * scale,
                    width=max(1, width - 48 * scale), size=size, line_height=line_height,
                    visible=max(1, int((height - 20 * scale) / line_height)),
                    bottom=y + 6 * scale, region=region.as_pointer())
    bounds['prompt_editor'] = geometry
    measure = _prompt_geometry(editor, geometry)
    focused = HUD_STATE['typing'] and HUD_STATE.get('text_region') == region.as_pointer()
    if not editor.text and not focused:
        _draw_text('Describe what you want to create or change…', geometry['x'], geometry['top'],
                   size=size, color=(.42, .48, .58, 1))
        _draw_text('Enter for a new line · Ctrl+Enter to run', geometry['x'],
                   geometry['top'] - line_height, size=10 * scale, color=(.30, .36, .46, 1))
        return
    a, b = editor.selection
    blf.enable(0, blf.CLIPPING)
    blf.clipping(0, geometry['x'], geometry['bottom'],
                 geometry['x'] + geometry['width'] + 3 * scale, y + height - 6 * scale)
    try:
        for row in range(editor.scroll, min(len(editor.lines), editor.scroll + editor.visible)):
            line = editor.lines[row]
            baseline = geometry['top'] - (row - editor.scroll) * line_height
            if focused and a != b and a <= line.end and b > line.start:
                left = measure(editor.text[line.start:max(line.start, a)])
                right = measure(editor.text[line.start:min(line.end, b)])
                if b > line.end:
                    right += 4 * scale
                _draw_rounded_box(geometry['x'] + left, baseline - 3 * scale,
                                  max(2 * scale, min(geometry['width'], right) - left),
                                  line_height, 1 * scale, fill_color=(.08, .42, .68, .75))
            _draw_text(editor.text[line.start:line.end].replace('\t', '    '), geometry['x'],
                       baseline, size=size, color=(.95, .97, 1, 1))
        row = editor.row()
        if focused and editor.scroll <= row < editor.scroll + editor.visible:
            if (time.monotonic() - HUD_STATE['cursor_time']) % 1.0 < .6:
                left = measure(editor.text[editor.lines[row].start:editor.cursor])
                _draw_rounded_box(geometry['x'] + left,
                                  geometry['top'] - (row - editor.scroll) * line_height - 2 * scale,
                                  max(1, scale), max(9, int(size)) + 3 * scale, 0,
                                  fill_color=(.05, .9, 1, 1))
    finally:
        blf.disable(0, blf.CLIPPING)
    if len(editor.lines) > editor.visible:
        track_x, track_y = x + width - 11 * scale, y + 8 * scale
        track_h = max(8 * scale, height - 38 * scale)
        thumb_h = max(8 * scale, track_h * editor.visible / len(editor.lines))
        thumb_y = track_y + (track_h - thumb_h) * (1 - editor.scroll / (len(editor.lines) - editor.visible))
        bounds['prompt_scrollbar'] = (track_x - 4 * scale, track_y, 12 * scale, track_h)
        geometry['scroll_track'] = (track_y, track_h, thumb_h)
        _draw_rounded_box(track_x, track_y, 4 * scale, track_h, 2 * scale,
                          fill_color=(.17, .22, .30, .65))
        _draw_rounded_box(track_x, thumb_y, 4 * scale, thumb_h, 2 * scale,
                          fill_color=(.15, .7, .82, .9))


def _handle_prompt_event(context, event, bounds, rx, ry, region_key):
    """Return a modal result when the editor consumes an event, else None."""
    def inside(rect):
        return bool(rect) and rect[0] <= rx <= rect[0] + rect[2] and rect[1] <= ry <= rect[1] + rect[3]

    geometry = bounds.get('prompt_editor')
    if not geometry:
        return None
    editor = _prompt_editor(context.scene)
    measure = _prompt_geometry(editor, geometry)
    focused = HUD_STATE['typing'] and HUD_STATE.get('text_region') == region_key
    pressed = event.value == 'PRESS'
    altgr = event.ctrl and getattr(event, 'alt', False) and bool(getattr(event, 'unicode', ''))
    command = (event.ctrl or getattr(event, 'oskey', False)) and not altgr

    def changed():
        context.scene.blendrelay_codex_prompt = editor.text
        HUD_STATE['cursor_time'] = time.monotonic()
        return {'RUNNING_MODAL'}

    def mouse_position():
        row = editor.scroll + int(math.floor((geometry['top'] + geometry['line_height'] * .8 - ry)
                                            / geometry['line_height']))
        return row, editor.hit(row, rx - geometry['x'], measure)

    def scrollbar_position():
        y, height, thumb = geometry['scroll_track']
        fraction = (y + height - thumb / 2 - ry) / max(1, height - thumb)
        editor.scroll = round(max(0, min(1, fraction)) * max(0, len(editor.lines) - editor.visible))
        editor.reveal = False

    if event.type in ('WHEELUPMOUSE', 'WHEELDOWNMOUSE') and inside(bounds.get('prompt')) and not command:
        editor.scroll_by(-3 if event.type == 'WHEELUPMOUSE' else 3)
        return {'RUNNING_MODAL'}
    if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and HUD_STATE.get('text_drag'):
        HUD_STATE['text_drag'] = None
        return {'RUNNING_MODAL'}
    if event.type in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE') and HUD_STATE.get('text_drag') and focused:
        if HUD_STATE['text_drag'] == 'scroll':
            scrollbar_position()
        else:
            if ry > geometry['top'] + geometry['line_height']:
                editor.scroll_by(-1)
            elif ry < geometry['bottom']:
                editor.scroll_by(1)
            row, position = mouse_position()
            editor.place(position, extend=True)
            editor.row_hint = max(0, min(len(editor.lines) - 1, row))
        return changed()
    if event.type == 'LEFTMOUSE' and event.value in ('PRESS', 'DOUBLE_CLICK') and inside(bounds.get('prompt')):
        HUD_STATE['typing'] = True
        HUD_STATE['text_region'] = region_key
        if inside(bounds.get('prompt_clear')):
            editor.select_all()
            editor.replace('')
            HUD_STATE['text_drag'] = None
        elif inside(bounds.get('prompt_scrollbar')):
            scrollbar_position()
            HUD_STATE['text_drag'] = 'scroll'
        else:
            row, position = mouse_position()
            if event.value == 'DOUBLE_CLICK':
                editor.select_word(position)
            else:
                editor.place(position, extend=event.shift)
                editor.row_hint = max(0, min(len(editor.lines) - 1, row))
            HUD_STATE['text_drag'] = 'select'
        return changed()
    if focused and event.type in ('LEFTMOUSE', 'RIGHTMOUSE') and pressed and not inside(bounds.get('prompt')):
        HUD_STATE['typing'] = False
        HUD_STATE['text_drag'] = None
        return None
    if not focused:
        return None
    if not pressed:
        return None
    if event.type == 'ESC':
        HUD_STATE['typing'] = False
        HUD_STATE['text_drag'] = None
        return {'RUNNING_MODAL'}
    if event.type == 'TAB':
        HUD_STATE['typing'] = False
        HUD_STATE['text_drag'] = None
        return {'RUNNING_MODAL'}
    if command and event.type == 'A':
        editor.select_all()
    elif command and event.type in ('C', 'X'):
        if editor.selected_text:
            context.window_manager.clipboard = editor.selected_text
            if event.type == 'X':
                editor.replace('')
    elif command and event.type == 'V':
        clipboard = getattr(context.window_manager, 'clipboard', '')
        try:
            path = Path(clipboard.strip().strip('"\''))
            if path.is_file() and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'):
                from . import bridge_ui
                bridge_ui.add_reference_images(context, [path])
                return {'RUNNING_MODAL'}
        except (OSError, ValueError):
            pass
        editor.replace(clipboard)
    elif command and event.type in ('Z', 'Y'):
        editor.undo(redo=event.type == 'Y' or event.shift)
    elif event.type in ('LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END', 'PAGE_UP', 'PAGE_DOWN'):
        direction = event.type.replace('_ARROW', '').replace('_', '')
        editor.move(direction, extend=event.shift, word=command, measure=measure)
    elif event.type in ('BACK_SPACE', 'BACKSPACE', 'DEL'):
        editor.delete(backward=event.type != 'DEL', word=command)
    elif event.type in ('RET', 'NUMPAD_ENTER'):
        if command:
            _submit_agent_prompt(context)
            return {'RUNNING_MODAL'}
        editor.replace('\n')
    elif not command and (not getattr(event, 'alt', False) or altgr):
        text = getattr(event, 'unicode', '') or getattr(event, 'ascii', '')
        if not text and event.type == 'SPACE':
            text = ' '
        if text and all(ord(character) >= 32 for character in text):
            editor.replace(text, kind='typing')
    return changed()


def _prompt_pointer(context, active):
    window = getattr(context, 'window', None)
    if not window or active == HUD_STATE.get('text_pointer', False):
        return
    if active:
        window.cursor_modal_set('TEXT')
    else:
        window.cursor_modal_restore()
    HUD_STATE['text_pointer'] = active


def _prompt_caret_tick():
    if HUD_STATE['handler'] is None:
        return None
    if HUD_STATE['typing']:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    return .4


def _submit_agent_prompt(context):
    """Submit from the HUD without allowing operator errors to escape the modal."""
    scene = context.scene
    backend = getattr(scene, 'blendrelay_agent_backend', 'ANTIGRAVITY')
    agent_name = {'ANTIGRAVITY': 'Antigravity', 'CODEX': 'Codex', 'CLAUDE': 'Claude'}.get(backend, 'AI agent')

    if not getattr(scene, 'blendrelay_codex_prompt', '').strip():
        HUD_STATE['typing'] = True
        HUD_STATE['flash_msg'] = f'Enter a prompt for {agent_name}'
        HUD_STATE['flash_time'] = time.monotonic()
        return False

    try:
        result = bpy.ops.blendrelay.send_to_agent()
    except RuntimeError as error:
        message = str(error).strip().splitlines()[0] if str(error).strip() else 'Could not start agent'
        if message.startswith('Error: '):
            message = message[7:]
        HUD_STATE['typing'] = True
        HUD_STATE['flash_msg'] = message
        HUD_STATE['flash_time'] = time.monotonic()
        return False

    if 'CANCELLED' in result:
        HUD_STATE['typing'] = True
        return False

    HUD_STATE['typing'] = False
    return True


# ── Main 2D Drawing Callback ────────────────────────────────────────────────

def draw_viewport_hud():
    """Render the unique BlendRelay floating AI command bar over the 3D Viewport."""
    context = bpy.context
    scene = getattr(context, 'scene', None)
    if not scene or not getattr(scene, 'blendrelay_show_viewport_hud', False):
        return

    region = context.region
    if region is None or region.type != 'WINDOW':
        return

    from . import bridge_ui
    is_running = bool(bridge_ui.STATE.get('agent_process') or bridge_ui.STATE.get('codex_process'))

    rw = region.width
    rh = region.height

    # ── Scale & Sizing Transform ─────────────────────────────────────────────
    # Auto sizing follows the actual 3D Viewport, not the whole Blender window.
    # It therefore responds correctly when a sidebar, timeline, or editor is
    # resized.  Manual width remains available for users who turn it off.
    metrics = _hud_layout_metrics(
        rw,
        rh,
        getattr(scene, 'blendrelay_hud_width', 780),
        getattr(scene, 'blendrelay_hud_scale', 1.0),
        getattr(scene, 'blendrelay_hud_adaptive', True),
        manual_height=getattr(scene, 'blendrelay_hud_height', HUD_BASE_HEIGHT),
    )
    scale = metrics['scale']
    hud_w = metrics['width']
    hud_h = metrics['height']

    x_off = getattr(scene, 'blendrelay_hud_x_offset', 0.0)
    y_off = getattr(scene, 'blendrelay_hud_y_offset', 0.0)

    base_x = (rw - hud_w) / 2.0 + x_off
    base_y = max(24.0, rh * 0.05) + y_off

    # Clamp to stay visible within the viewport
    hud_x = max(8.0, min(rw - hud_w - 8.0, base_x))
    hud_y = max(8.0, min(rh - hud_h - 8.0, base_y))

    bounds = {}
    hover = HUD_STATE.get('hover')

    # Total bounding box for fast pass-through check (expanded slightly for edge handles)
    bounds['total'] = (hud_x - 8.0, hud_y - 4.0, hud_w + 16.0, hud_h + 8.0)

    # Resize hit zones
    bounds['resize_top'] = (hud_x, hud_y + hud_h - 5.0, hud_w, 10.0)
    bounds['resize_bottom'] = (hud_x, hud_y - 5.0, hud_w, 10.0)
    bounds['resize_left'] = (hud_x - 7.0, hud_y, 14.0, hud_h)
    bounds['resize_right'] = (hud_x + hud_w - 7.0, hud_y, 14.0, hud_h)
    bounds['resize_corner'] = (hud_x + hud_w - 24.0 * scale, hud_y, 24.0 * scale, 24.0 * scale)

    # ── 1. Main Obsidian Glass Chassis ──────────────────────────────────────
    _draw_rounded_box(
        hud_x, hud_y, hud_w, hud_h, 15.0 * scale,
        fill_color=(0.06, 0.08, 0.12, 0.95),
        border_color=(0.18, 0.24, 0.36, 0.85),
        border_width=max(1.0, 1.5 * scale),
    )
    # Electric Cyan top hairline rim
    _draw_top_accent_line(hud_x, hud_y, hud_w, hud_h, 15.0 * scale, color=(0.0, 0.82, 1.0, 0.85), width=max(1.5, 2.0 * scale))

    # Draw edge resize indicators on hover
    if hover in ('resize_left', 'resize_right', 'resize_corner') or HUD_STATE.get('dragging') in ('resize_left', 'resize_right', 'resize_corner'):
        if hover == 'resize_left' or HUD_STATE.get('dragging') == 'resize_left':
            _draw_edge_highlight(hud_x, hud_y, hud_h, scale, (0.0, 0.85, 1.0, 0.95))
        if hover in ('resize_right', 'resize_corner') or HUD_STATE.get('dragging') in ('resize_right', 'resize_corner'):
            _draw_edge_highlight(hud_x + hud_w, hud_y, hud_h, scale, (0.0, 0.85, 1.0, 0.95))

    if hover in ('resize_top', 'resize_bottom') or HUD_STATE.get('dragging') in ('resize_top', 'resize_bottom'):
        edge = HUD_STATE.get('dragging') or hover
        edge_y = hud_y + hud_h - 2 if edge == 'resize_top' else hud_y
        _draw_rounded_box(hud_x + hud_w * .35, edge_y, hud_w * .3, 2, 1,
                          fill_color=(0.0, .85, 1.0, .95))

    # Corner grip slashes
    grip_x = hud_x + hud_w - 18.0 * scale
    grip_y = hud_y + 4.0 * scale
    grip_color = (0.0, 0.85, 1.0, 0.95) if (hover == 'resize_corner' or HUD_STATE.get('dragging') == 'resize_corner') else (0.28, 0.38, 0.55, 0.6)
    _draw_grip_lines(grip_x, grip_y, scale, grip_color)

    # ── 2. Top Bar: Branding, Live Status, Tabs, Zoom, and Close ─────────────
    top_y = hud_y + hud_h - 30.0 * scale
    top_h = 24.0 * scale

    # Titlebar drag region (allows moving HUD freely)
    bounds['header_drag'] = (hud_x, top_y, hud_w, top_h)

    # Brand badge
    brand_w = 106.0 * scale
    brand_x = hud_x + 14.0 * scale
    _draw_rounded_box(
        brand_x, top_y, brand_w, top_h, 12.0 * scale,
        fill_color=(0.08, 0.14, 0.24, 0.85),
        border_color=(0.0, 0.70, 0.95, 0.5),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("✦ BLENDRELAY", brand_x, top_y, brand_w, top_h, size=11 * scale, color=(0.15, 0.88, 1.0, 1.0))

    # Live Status Dot & Label
    status_x = brand_x + brand_w + 10.0 * scale
    status_y = top_y + 12.0 * scale
    if is_running:
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 5.0)
        _draw_circle(status_x, status_y, 4.5 * scale, (0.85 + 0.15 * pulse, 0.25, 0.35, 1.0))
        _draw_text("RUNNING TASK...", status_x + 8.0 * scale, top_y + 6.0 * scale, size=11 * scale, color=(0.95, 0.45, 0.45, 1.0))
    elif bridge_ui.STATE.get('connection'):
        _draw_circle(status_x, status_y, 4.0 * scale, (0.1, 0.9, 0.45, 1.0))
        _draw_text("READY", status_x + 8.0 * scale, top_y + 6.0 * scale, size=11 * scale, color=(0.4, 0.9, 0.6, 1.0))
    else:
        _draw_circle(status_x, status_y, 4.0 * scale, (0.5, 0.55, 0.65, 0.8))
        _draw_text("STANDBY", status_x + 8.0 * scale, top_y + 6.0 * scale, size=11 * scale, color=(0.55, 0.62, 0.72, 1.0))

    # Right side controls: Close, Console, Zoom (+/-)
    close_w = 24.0 * scale
    close_x = hud_x + hud_w - close_w - 14.0 * scale
    bounds['btn_close'] = (close_x, top_y, close_w, top_h)
    c_hover = (hover == 'btn_close')
    _draw_rounded_box(
        close_x, top_y, close_w, top_h, 12.0 * scale,
        fill_color=(0.25, 0.15, 0.18, 0.9) if c_hover else (0.12, 0.14, 0.18, 0.7),
        border_color=(0.8, 0.3, 0.3, 0.6) if c_hover else (0.22, 0.26, 0.33, 0.5),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("✕", close_x, top_y, close_w, top_h, size=11 * scale, color=(1.0, 0.5, 0.5, 1.0) if c_hover else (0.6, 0.65, 0.72, 1.0))

    # Console Toggle Button
    con_w = 68.0 * scale
    con_x = close_x - con_w - 6.0 * scale
    bounds['btn_console'] = (con_x, top_y, con_w, top_h)
    con_hover = (hover == 'btn_console')
    _draw_rounded_box(
        con_x, top_y, con_w, top_h, 12.0 * scale,
        fill_color=(0.18, 0.22, 0.30, 0.9) if con_hover else (0.10, 0.12, 0.17, 0.7),
        border_color=(0.30, 0.40, 0.55, 0.6),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("🗲 Console", con_x, top_y, con_w, top_h, size=10 * scale, color=(0.85, 0.90, 0.95, 1.0))

    # Scale Zoom Controls: [+] [ 100% ] [-]
    btn_z_w = 20.0 * scale
    btn_zp_x = con_x - btn_z_w - 5.0 * scale
    bounds['scale_up'] = (btn_zp_x, top_y, btn_z_w, top_h)
    _draw_rounded_box(
        btn_zp_x, top_y, btn_z_w, top_h, 10.0 * scale,
        fill_color=(0.18, 0.24, 0.34, 0.9) if hover == 'scale_up' else (0.10, 0.13, 0.18, 0.7),
        border_color=(0.28, 0.38, 0.55, 0.6),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("+", btn_zp_x, top_y, btn_z_w, top_h, size=12 * scale, color=(0.9, 0.95, 1.0, 1.0))

    lbl_z_w = 42.0 * scale
    lbl_z_x = btn_zp_x - lbl_z_w - 3.0 * scale
    bounds['scale_reset'] = (lbl_z_x, top_y, lbl_z_w, top_h)
    _draw_rounded_box(
        lbl_z_x, top_y, lbl_z_w, top_h, 10.0 * scale,
        fill_color=(0.16, 0.22, 0.30, 0.9) if hover == 'scale_reset' else (0.08, 0.11, 0.16, 0.6),
        border_color=(0.28, 0.38, 0.55, 0.6),
        border_width=max(0.8, 1.0 * scale),
    )
    pct_text = f"{int(round(scale * 100))}%"
    _draw_text_centered(pct_text, lbl_z_x, top_y, lbl_z_w, top_h, size=10 * scale, color=(0.75, 0.85, 0.95, 1.0))

    btn_zm_x = lbl_z_x - btn_z_w - 3.0 * scale
    bounds['scale_down'] = (btn_zm_x, top_y, btn_z_w, top_h)
    _draw_rounded_box(
        btn_zm_x, top_y, btn_z_w, top_h, 10.0 * scale,
        fill_color=(0.18, 0.24, 0.34, 0.9) if hover == 'scale_down' else (0.10, 0.13, 0.18, 0.7),
        border_color=(0.28, 0.38, 0.55, 0.6),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("−", btn_zm_x, top_y, btn_z_w, top_h, size=12 * scale, color=(0.9, 0.95, 1.0, 1.0))

    # ── 3. Multi-line Prompt Field & Main Action Button ──────────────────────
    mid_y = hud_y + 12.0 * scale
    mid_h = max(1.0, hud_h - 54.0 * scale)

    btn_w = 94.0 * scale
    btn_h = 34.0 * scale
    btn_y = mid_y + (mid_h - btn_h) / 2.0
    btn_x = hud_x + hud_w - btn_w - 14.0 * scale
    bounds['btn_action'] = (btn_x, btn_y, btn_w, btn_h)

    # Action Button: Run Agent (or Stop Task when running)
    prompt_x = hud_x + 14.0 * scale
    if is_running:
        btn_w = 98.0 * scale
        btn_x = hud_x + hud_w - btn_w - 14.0 * scale
        bounds['btn_action'] = (btn_x, btn_y, btn_w, btn_h)
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 6.0)
        c_fill = (0.88 + 0.12 * pulse, 0.22, 0.24, 1.0)
        _draw_rounded_box(
            btn_x, btn_y, btn_w, btn_h, 10.0 * scale,
            fill_color=c_fill,
            border_color=(1.0, 0.4, 0.4, 0.9),
            border_width=max(1.0, 1.2 * scale),
        )
        _draw_text_centered("✖ STOP", btn_x, btn_y, btn_w, btn_h, size=11 * scale, color=(1.0, 1.0, 1.0, 1.0))
        prompt_w = btn_x - prompt_x - 10.0 * scale
    else:
        btn_run_w = 94.0 * scale
        btn_run_x = hud_x + hud_w - btn_run_w - 14.0 * scale
        bounds['btn_action'] = (btn_run_x, btn_y, btn_run_w, btn_h)

        # Draw Run Agent Button
        run_hover = (hover == 'btn_action')
        c_fill = (0.05, 0.62, 0.98, 1.0) if run_hover else (0.02, 0.50, 0.90, 1.0)
        c_border = (0.45, 0.88, 1.0, 0.95) if run_hover else (0.15, 0.65, 0.98, 0.8)
        _draw_rounded_box(
            btn_run_x, btn_y, btn_run_w, btn_h, 10.0 * scale,
            fill_color=c_fill,
            border_color=c_border,
            border_width=max(1.0, 1.5 * scale),
        )
        _draw_text_centered("✦ RUN", btn_run_x, btn_y, btn_run_w, btn_h, size=11 * scale, color=(1.0, 1.0, 1.0, 1.0))

        prompt_w = btn_run_x - prompt_x - 10.0 * scale

    # Prompt Text Input Box
    bounds['prompt'] = (prompt_x, mid_y, prompt_w, mid_h)
    is_typing = HUD_STATE['typing']

    p_border = (0.0, 0.82, 1.0, 0.95) if is_typing else (0.18, 0.24, 0.35, 0.7)
    _draw_rounded_box(
        prompt_x, mid_y, prompt_w, mid_h, 10.0 * scale,
        fill_color=(0.04, 0.05, 0.08, 0.95),
        border_color=p_border,
        border_width=max(1.0, (1.5 if is_typing else 1.0) * scale),
    )

    # Clear button inside prompt box
    prompt_text = getattr(scene, 'blendrelay_codex_prompt', '')
    if prompt_text:
        clear_w = 22.0 * scale
        clear_x = prompt_x + prompt_w - clear_w - 6.0 * scale
        clear_y = mid_y + mid_h - 28.0 * scale
        bounds['prompt_clear'] = (clear_x, clear_y, clear_w, 22.0 * scale)
        clr_hover = (hover == 'prompt_clear')
        _draw_rounded_box(
            clear_x, clear_y, clear_w, 22.0 * scale, 11.0 * scale,
            fill_color=(0.20, 0.24, 0.32, 0.9) if clr_hover else (0.12, 0.15, 0.20, 0.7),
        )
        _draw_text_centered("✕", clear_x, clear_y, clear_w, 22.0 * scale, size=10 * scale, color=(0.85, 0.90, 0.95, 1.0))

    _draw_prompt_editor(scene, bounds, prompt_x, mid_y, prompt_w, mid_h, scale, region)

    # Flash notification message
    now = time.monotonic()
    if HUD_STATE['flash_msg'] and (now - HUD_STATE['flash_time'] < 2.5):
        msg = HUD_STATE['flash_msg']
        fw = 180.0 * scale
        fx = hud_x + (hud_w - fw) / 2.0
        fy = hud_y + hud_h + 8.0 * scale
        _draw_rounded_box(
            fx, fy, fw, 26.0 * scale, 13.0 * scale,
            fill_color=(0.04, 0.15, 0.10, 0.95),
            border_color=(0.1, 0.9, 0.45, 0.8),
            border_width=max(0.8, 1.0 * scale),
        )
        _draw_text_centered(msg, fx, fy, fw, 26.0 * scale, size=11 * scale, color=(0.3, 1.0, 0.6, 1.0))

    HUD_STATE['bounds'] = bounds
    HUD_STATE['region_bounds'][region.as_pointer()] = bounds


# ── Interactive Modal Controller ────────────────────────────────────────────

class AF_OT_ViewportHUDModal(bpy.types.Operator):
    """Interactive controller for the BlendRelay 3D Viewport floating AI command bar."""
    bl_idname = 'blendrelay.viewport_hud_modal'
    bl_label = 'BlendRelay Viewport HUD'

    def modal(self, context, event):
        scene = getattr(context, 'scene', None)
        if not scene or not getattr(scene, 'blendrelay_show_viewport_hud', False):
            HUD_STATE['modal_active'] = False
            HUD_STATE['typing'] = False
            HUD_STATE['dragging'] = None
            HUD_STATE['text_drag'] = None
            _prompt_pointer(context, False)
            return {'CANCELLED'}

        # Find the VIEW_3D area and WINDOW region containing the cursor
        mx, my = event.mouse_x, event.mouse_y
        target_area = None
        target_region = None

        if context.screen:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    if (area.x <= mx <= area.x + area.width and
                            area.y <= my <= area.y + area.height):
                        for r in area.regions:
                            if r.type == 'WINDOW' and (r.x <= mx <= r.x + r.width and r.y <= my <= r.y + r.height):
                                target_area = area
                                target_region = r
                                break
                        if target_region:
                            break

        if not target_region:
            _prompt_pointer(context, False)
            if event.type in ('LEFTMOUSE', 'RIGHTMOUSE') and event.value == 'PRESS':
                HUD_STATE['typing'] = False
                HUD_STATE['text_drag'] = None
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                HUD_STATE['text_drag'] = None
            return {'PASS_THROUGH'}

        rx = mx - target_region.x
        ry = my - target_region.y
        HUD_STATE['last_mouse'] = (rx, ry)

        bounds = HUD_STATE['region_bounds'].get(target_region.as_pointer(), HUD_STATE.get('bounds', {}))
        total_bounds = bounds.get('total')

        def is_inside(rect):
            if not rect:
                return False
            x0, y0, w0, h0 = rect
            return (x0 <= rx <= x0 + w0) and (y0 <= ry <= y0 + h0)

        _prompt_pointer(context, is_inside(bounds.get('prompt')) and
                        not is_inside(bounds.get('prompt_clear')) and
                        not is_inside(bounds.get('prompt_scrollbar')))

        # ── Detect Dragged & Dropped Image onto HUD ──────────────────────────
        act = context.active_object
        if act and getattr(act, 'type', None) == 'EMPTY' and getattr(act, 'empty_display_type', None) == 'IMAGE':
            if getattr(act, 'data', None) and getattr(act.data, 'filepath', None):
                if is_inside(total_bounds):
                    fp = bpy.path.abspath(act.data.filepath)
                    if Path(fp).is_file():
                        from . import bridge_ui
                        bridge_ui.add_reference_images(context, [Path(fp)])
                        try:
                            bpy.data.objects.remove(act, do_unlink=True)
                        except Exception:
                            pass
                        target_area.tag_redraw()

        # ── Handle Ongoing Drag (Resize or Move) ─────────────────────────────
        dragging = HUD_STATE.get('dragging')
        if dragging:
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                HUD_STATE['dragging'] = None
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            if event.type in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
                metrics = _hud_layout_metrics(
                    target_region.width,
                    target_region.height,
                    getattr(scene, 'blendrelay_hud_width', 780),
                    getattr(scene, 'blendrelay_hud_scale', 1.0),
                    getattr(scene, 'blendrelay_hud_adaptive', True),
                    manual_height=getattr(scene, 'blendrelay_hud_height', HUD_BASE_HEIGHT),
                )
                scale = max(.001, HUD_STATE['drag_start_scale'])
                start_mx, start_my = HUD_STATE['drag_start_mouse']

                if dragging in ('resize_top', 'resize_bottom', 'resize_corner'):
                    direction = 1 if dragging == 'resize_top' else -1
                    start_h = HUD_STATE['drag_start_height']
                    new_h = max(90.0, min(900.0, (target_region.height - HUD_VIEWPORT_MARGIN) / scale,
                                          start_h + direction * (my - start_my) / scale))
                    scene.blendrelay_hud_height = round(new_h)
                    if direction < 0:
                        scene.blendrelay_hud_y_offset = HUD_STATE['drag_start_offset'][1] - (new_h - start_h) * scale
                    if dragging != 'resize_corner':
                        target_area.tag_redraw()
                        return {'RUNNING_MODAL'}

                if dragging in ('resize_right', 'resize_corner'):
                    dx = (mx - start_mx) / scale
                    new_w = max(500, min(4000, int(HUD_STATE['drag_start_width'] + dx * 2.0)))
                    scene.blendrelay_hud_width = new_w
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                elif dragging == 'resize_left':
                    dx = (start_mx - mx) / scale
                    new_w = max(500, min(4000, int(HUD_STATE['drag_start_width'] + dx * 2.0)))
                    scene.blendrelay_hud_width = new_w
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                elif dragging == 'move':
                    dx = mx - start_mx
                    dy = my - start_my
                    off_x, off_y = HUD_STATE['drag_start_offset']
                    scene.blendrelay_hud_x_offset = off_x + dx
                    scene.blendrelay_hud_y_offset = off_y + dy
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

        result = _handle_prompt_event(context, event, bounds, rx, ry, target_region.as_pointer())
        if result is not None:
            target_area.tag_redraw()
            return result

        # ── Mouse Wheel Zoom / Scale (Ctrl + Wheel) ─────────────────────────
        if event.ctrl and is_inside(total_bounds):
            if event.type == 'WHEELUPMOUSE':
                cur = getattr(scene, 'blendrelay_hud_scale', 1.0)
                scene.blendrelay_hud_scale = min(2.5, round(cur + 0.05, 2))
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}
            elif event.type == 'WHEELDOWNMOUSE':
                cur = getattr(scene, 'blendrelay_hud_scale', 1.0)
                scene.blendrelay_hud_scale = max(0.65, round(cur - 0.05, 2))
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

        # ── Mouse Hover Tracking ────────────────────────────────────────────
        if is_inside(total_bounds):
            new_hover = None
            # Prioritize interactive buttons and resize handles
            check_keys = (
                'resize_corner', 'resize_top', 'resize_bottom', 'resize_left', 'resize_right',
                'btn_action', 'prompt_clear', 'prompt', 'btn_close', 'btn_console',
                'scale_up', 'scale_down', 'scale_reset',
                'header_drag',
            )
            for key in check_keys:
                if is_inside(bounds.get(key)):
                    new_hover = key
                    break

            if new_hover != HUD_STATE.get('hover'):
                HUD_STATE['hover'] = new_hover
                target_area.tag_redraw()
        else:
            if HUD_STATE.get('hover'):
                HUD_STATE['hover'] = None
                target_area.tag_redraw()

        # ── Click & Drag Initiations on Floating Elements ────────────────────
        if is_inside(total_bounds):
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                # 1. Resize handles initiation
                for rkey in ('resize_corner', 'resize_top', 'resize_bottom', 'resize_right', 'resize_left'):
                    if is_inside(bounds.get(rkey)):
                        # A direct resize is an explicit request for manual
                        # width. Preserve the currently visible auto width so
                        # the HUD does not jump when the drag begins.
                        metrics = _hud_layout_metrics(
                            target_region.width,
                            target_region.height,
                            getattr(scene, 'blendrelay_hud_width', 780),
                            getattr(scene, 'blendrelay_hud_scale', 1.0),
                            getattr(scene, 'blendrelay_hud_adaptive', True),
                            manual_height=getattr(scene, 'blendrelay_hud_height', HUD_BASE_HEIGHT),
                        )
                        if rkey not in ('resize_top', 'resize_bottom') and getattr(scene, 'blendrelay_hud_adaptive', True):
                            scene.blendrelay_hud_adaptive = False
                            # Store both the effective scale and the matching
                            # design width.  This keeps text, controls, and
                            # physical width identical when manual resizing
                            # begins instead of resetting the HUD to 100%.
                            scene.blendrelay_hud_scale = round(metrics['scale'], 2)
                            scene.blendrelay_hud_width = round(
                                metrics['width'] / scene.blendrelay_hud_scale
                            )
                        HUD_STATE['dragging'] = rkey
                        HUD_STATE['drag_start_mouse'] = (mx, my)
                        HUD_STATE['drag_start_width'] = float(scene.blendrelay_hud_width)
                        HUD_STATE['drag_start_scale'] = metrics['scale']
                        HUD_STATE['drag_start_height'] = metrics['height'] / metrics['scale']
                        HUD_STATE['drag_start_offset'] = (scene.blendrelay_hud_x_offset, scene.blendrelay_hud_y_offset)
                        target_area.tag_redraw()
                        return {'RUNNING_MODAL'}

                # 2. Scale Zoom Buttons (+/- and reset)
                if is_inside(bounds.get('scale_up')):
                    cur = getattr(scene, 'blendrelay_hud_scale', 1.0)
                    scene.blendrelay_hud_scale = min(2.5, round(cur + 0.1, 2))
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                if is_inside(bounds.get('scale_down')):
                    cur = getattr(scene, 'blendrelay_hud_scale', 1.0)
                    scene.blendrelay_hud_scale = max(0.65, round(cur - 0.1, 2))
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                if is_inside(bounds.get('scale_reset')):
                    scene.blendrelay_hud_scale = 1.0
                    scene.blendrelay_hud_width = 780
                    scene.blendrelay_hud_height = 122
                    scene.blendrelay_hud_adaptive = True
                    scene.blendrelay_hud_x_offset = 0.0
                    scene.blendrelay_hud_y_offset = 0.0
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 3. Action Button (Run Agent / Cancel)
                if is_inside(bounds.get('btn_action')):
                    from . import bridge_ui
                    is_running = bool(bridge_ui.STATE.get('agent_process') or bridge_ui.STATE.get('codex_process'))
                    if is_running:
                        bpy.ops.blendrelay.cancel_agent()
                    else:
                        _submit_agent_prompt(context)
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 6. Console Toggle Button
                if is_inside(bounds.get('btn_console')):
                    if hasattr(bpy.ops.wm, 'console_toggle'):
                        bpy.ops.wm.console_toggle()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 7. Close Button
                if is_inside(bounds.get('btn_close')):
                    context.scene.blendrelay_show_viewport_hud = False
                    HUD_STATE['modal_active'] = False
                    HUD_STATE['typing'] = False
                    _prompt_pointer(context, False)
                    target_area.tag_redraw()
                    return {'CANCELLED'}

                # 15. Header Drag-to-Move initiation
                if is_inside(bounds.get('header_drag')):
                    HUD_STATE['dragging'] = 'move'
                    HUD_STATE['drag_start_mouse'] = (mx, my)
                    HUD_STATE['drag_start_offset'] = (
                        float(getattr(scene, 'blendrelay_hud_x_offset', 0.0)),
                        float(getattr(scene, 'blendrelay_hud_y_offset', 0.0))
                    )
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # Empty HUD space is visually transparent to scene interaction.
                # Let Blender use the click for normal object selection.
                return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        return self._start_modal(context)

    def invoke(self, context, event):
        return self._start_modal(context)

    def _start_modal(self, context):
        if HUD_STATE['modal_active']:
            return {'CANCELLED'}
        HUD_STATE['modal_active'] = True
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


# ── Registration & Lifecycle ────────────────────────────────────────────────

def register_hud():
    """Register the viewport 2D draw handler."""
    if HUD_STATE['handler'] is None:
        HUD_STATE['handler'] = bpy.types.SpaceView3D.draw_handler_add(
            draw_viewport_hud, (), 'WINDOW', 'POST_PIXEL'
        )
    if not bpy.app.timers.is_registered(_prompt_caret_tick):
        bpy.app.timers.register(_prompt_caret_tick, first_interval=.4)


def unregister_hud():
    _prompt_pointer(bpy.context, False)
    HUD_STATE['text_drag'] = None
    HUD_STATE['text_region'] = None
    HUD_STATE['region_bounds'].clear()
    if bpy.app.timers.is_registered(_prompt_caret_tick):
        bpy.app.timers.unregister(_prompt_caret_tick)
    """Unregister the viewport 2D draw handler."""
    if HUD_STATE['handler'] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(HUD_STATE['handler'], 'WINDOW')
        HUD_STATE['handler'] = None
    HUD_STATE['modal_active'] = False
    HUD_STATE['typing'] = False
    HUD_STATE['dragging'] = None


def ensure_hud_modal():
    """Launch the modal operator if the HUD is enabled and modal isn't running yet."""
    if HUD_STATE.get('modal_active'):
        return
    scene = getattr(bpy.context, 'scene', None)
    if not scene or not getattr(scene, 'blendrelay_show_viewport_hud', False):
        return

    wm = getattr(bpy.context, 'window_manager', None)
    if not wm or not wm.windows:
        return
    win = wm.windows[0]
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            for region in area.regions:
                if region.type == 'WINDOW':
                    try:
                        with bpy.context.temp_override(window=win, area=area, region=region):
                            bpy.ops.blendrelay.viewport_hud_modal('EXEC_DEFAULT')
                    except Exception as e:
                        pass
                    return
