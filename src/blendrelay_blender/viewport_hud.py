# SPDX-License-Identifier: GPL-3.0-or-later
"""Modern Viewport Floating AI Command HUD for BlendRelay.

Rendered at 60 FPS in 3D Viewport 2D screen space via Blender's GPU & BLF modules.
Designed with a unique dark obsidian interface and electric cyan accents.
Features full interactive drag-to-resize (left/right edges & corner grip), titlebar drag-to-move,
scale zoom buttons (+/-), Ctrl+Wheel zoom, and rock-solid click hit-testing.
"""
import math
import time
from pathlib import Path
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

# ── Preset Prompt Suggestions ───────────────────────────────────────────────

PRESETS = [
    'with realistic PBR materials and cinematic lighting',
    'polished hard-surface product with a clear silhouette',
    'cyberpunk neon detailing with metallic shaders',
    'clean minimalist low-poly geometric style',
    'add bevel modifiers on sharp mesh edges',
    'centered at origin with clean subdivision topology',
]

def _get_model_list(agent):
    from . import bridge_ui
    return bridge_ui._MODEL_ITEMS[agent]


def _get_selected_model(scene, agent):
    from . import bridge_ui
    return bridge_ui.selected_model(scene, agent)


def _format_model_short(mid, mname):
    """Format short concise name for the floating HUD chip."""
    if not mid:
        return 'Model'
    if '3.8-flash' in mid:
        return 'Flash 3.8' + (' Med' if 'med' in mid else (' Low' if 'low' in mid else ' High'))
    if '3.7-flash' in mid:
        return 'Flash 3.7'
    if '3.6-flash' in mid:
        return 'Flash 3.6'
    if '3.1-pro' in mid:
        return 'Pro 3.1'
    if 'claude-sonnet' in mid:
        return 'Sonnet 4.6'
    if 'claude-opus' in mid:
        return 'Opus 4.6'
    if 'gpt-oss' in mid:
        return 'GPT-OSS'
    if 'gpt-6-astra' in mid or 'astra' in mid:
        return 'GPT-6 Astra'
    if 'gpt-6' in mid:
        return 'GPT-6'
    if 'gpt-5.6-sol' in mid or 'sol' in mid:
        return 'GPT-5.6 Sol'
    if 'gpt-5.6-terra' in mid or 'terra' in mid:
        return 'GPT-5.6 Terra'
    if 'gpt-5.6-luna' in mid or 'luna' in mid:
        return 'GPT-5.6 Luna'
    if 'gpt-5.6' in mid:
        return 'GPT-5.6'
    if 'gpt-5.5' in mid:
        return 'GPT-5.5'
    if 'gpt-5-mini' in mid:
        return 'GPT-5 Mini'
    if 'gpt-5' in mid:
        return 'GPT-5'
    if 'gpt-4o' in mid:
        return 'GPT-4o'
    if 'o3-mini' in mid:
        return 'o3-mini'
    if 'o3' in mid:
        return 'o3'
    if 'o1' in mid:
        return 'o1'
    return mname[:14]

# ── Global HUD State ─────────────────────────────────────────────────────────

HUD_STATE = {
    'handler': None,
    'modal_active': False,
    'hover': None,
    'typing': False,
    'dragging': None,
    'drag_start_mouse': (0, 0),
    'drag_start_width': 780.0,
    'drag_start_scale': 1.0,
    'drag_start_offset': (0.0, 0.0),
    'cursor_time': 0.0,
    'preset_idx': 0,
    'bounds': {},
    'last_mouse': (0, 0),
    'flash_msg': '',
    'flash_time': 0.0,
}


HUD_BASE_HEIGHT = 126.0
HUD_DESIGN_WIDTH = 780.0
HUD_VIEWPORT_MARGIN = 24.0
HUD_AUTO_WIDTH_FRACTION = 0.55
HUD_AUTO_SCALE_MIN = 0.50
HUD_AUTO_SCALE_MAX = 1.45


def _hud_layout_metrics(viewport_width, viewport_height, manual_width, user_scale, adaptive=True):
    """Return a HUD size that remains usable in the available viewport.

    Automatic mode uses a balanced proportion of the active 3D viewport and
    derives the visual scale from it. Its scale range avoids tiny controls on
    high-DPI displays and an oversized HUD on ultrawide screens. Manual mode
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
        # Use the same design width at every resolution. Scaling both axes
        # from the viewport width keeps text and controls comfortably sized on
        # high-DPI displays while retaining the exact visual proportions.
        base_width = HUD_DESIGN_WIDTH
        target_width = min(available_w, rw * HUD_AUTO_WIDTH_FRACTION)
        viewport_scale = max(
            HUD_AUTO_SCALE_MIN,
            min(HUD_AUTO_SCALE_MAX, target_width / base_width),
        )
        requested_scale = viewport_scale * user_scale
    else:
        base_width = max(500.0, min(4000.0, float(manual_width)))
        requested_scale = user_scale

    fit = min(1.0, available_w / (base_width * requested_scale), available_h / (HUD_BASE_HEIGHT * requested_scale))
    effective_scale = requested_scale * fit
    return {
        'base_width': base_width,
        'scale': effective_scale,
        'width': base_width * effective_scale,
        'height': HUD_BASE_HEIGHT * effective_scale,
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
    agent = getattr(scene, 'blendrelay_agent_backend', 'ANTIGRAVITY')
    agent_name = 'Antigravity' if agent == 'ANTIGRAVITY' else 'Codex'
    is_running = bool(bridge_ui.STATE.get('agent_process') or bridge_ui.STATE.get('codex_process'))
    only_selected = getattr(scene, 'blendrelay_only_selected', False)
    active_tab = getattr(scene, 'blendrelay_ui_tab', 'GENERATE')

    # Dynamic model friendly name
    cur_model = _get_selected_model(scene, agent)
    model_label = _format_model_short(cur_model, cur_model) if cur_model else 'CLI default'

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

    # Mode Tabs (Generate, Sketch, Versions, Settings)
    tabs = [
        ('GENERATE', '✦ Generate'),
        ('SKETCH', 'Sketch & Region'),
        ('HISTORY', '⏱ Versions'),
        ('SETTINGS', '⚙ Settings'),
    ]
    tab_w = 76.0 * scale
    tabs_start_x = hud_x + (hud_w - 316.0 * scale) / 2.0
    for i, (tid, tlabel) in enumerate(tabs):
        tx = tabs_start_x + i * (tab_w + 4.0 * scale)
        bounds[f'tab_{tid}'] = (tx, top_y, tab_w, top_h)
        is_active = (active_tab == tid)
        is_t_hover = (hover == f'tab_{tid}')

        if is_active:
            _draw_rounded_box(
                tx, top_y, tab_w, top_h, 12.0 * scale,
                fill_color=(0.18, 0.26, 0.38, 0.95),
                border_color=(0.0, 0.75, 1.0, 0.7),
                border_width=max(0.8, 1.0 * scale),
            )
            _draw_text_centered(tlabel, tx, top_y, tab_w, top_h, size=(8 if tid == 'SKETCH' else 11) * scale, color=(1.0, 1.0, 1.0, 1.0))
        elif is_t_hover:
            _draw_rounded_box(
                tx, top_y, tab_w, top_h, 12.0 * scale,
                fill_color=(0.13, 0.17, 0.23, 0.9),
                border_color=(0.30, 0.40, 0.55, 0.6),
                border_width=max(0.8, 1.0 * scale),
            )
            _draw_text_centered(tlabel, tx, top_y, tab_w, top_h, size=(8 if tid == 'SKETCH' else 11) * scale, color=(0.88, 0.92, 0.98, 1.0))
        else:
            _draw_text_centered(tlabel, tx, top_y, tab_w, top_h, size=(8 if tid == 'SKETCH' else 11) * scale, color=(0.60, 0.66, 0.75, 1.0))

    # ── 3. Middle Bar: Prompt Field & Main Action Button ──────────────────────
    mid_y = hud_y + 45.0 * scale
    mid_h = 38.0 * scale

    btn_w = 136.0 * scale
    btn_x = hud_x + hud_w - btn_w - 14.0 * scale
    bounds['btn_action'] = (btn_x, mid_y, btn_w, mid_h)
    btn_hover = (hover == 'btn_action')

    # Action Buttons: Run Agent & Verify/Fix (or Stop Task when running)
    prompt_x = hud_x + 14.0 * scale
    if is_running:
        btn_w = 140.0 * scale
        btn_x = hud_x + hud_w - btn_w - 14.0 * scale
        bounds['btn_action'] = (btn_x, mid_y, btn_w, mid_h)
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 6.0)
        c_fill = (0.88 + 0.12 * pulse, 0.22, 0.24, 1.0)
        _draw_rounded_box(
            btn_x, mid_y, btn_w, mid_h, 10.0 * scale,
            fill_color=c_fill,
            border_color=(1.0, 0.4, 0.4, 0.9),
            border_width=max(1.0, 1.2 * scale),
        )
        _draw_text_centered("✖ STOP TASK", btn_x, mid_y, btn_w, mid_h, size=12 * scale, color=(1.0, 1.0, 1.0, 1.0))
        prompt_w = btn_x - prompt_x - 10.0 * scale
    else:
        btn_run_w = 114.0 * scale
        btn_fix_w = 104.0 * scale
        btn_gap = 6.0 * scale
        btn_run_x = hud_x + hud_w - btn_run_w - 14.0 * scale
        btn_fix_x = btn_run_x - btn_fix_w - btn_gap
        bounds['btn_action'] = (btn_run_x, mid_y, btn_run_w, mid_h)
        bounds['btn_fix'] = (btn_fix_x, mid_y, btn_fix_w, mid_h)

        # Draw Verify & Fix Button
        f_hover = (hover == 'btn_fix')
        f_fill = (0.24, 0.14, 0.38, 0.95) if f_hover else (0.13, 0.09, 0.22, 0.90)
        f_border = (0.85, 0.45, 1.0, 0.95) if f_hover else (0.55, 0.28, 0.85, 0.75)
        _draw_rounded_box(
            btn_fix_x, mid_y, btn_fix_w, mid_h, 10.0 * scale,
            fill_color=f_fill,
            border_color=f_border,
            border_width=max(1.0, 1.3 * scale),
        )
        _draw_text_centered("👁 VERIFY & FIX", btn_fix_x, mid_y, btn_fix_w, mid_h, size=10.5 * scale, color=(0.94, 0.86, 1.0, 1.0))

        # Draw Run Agent Button
        run_hover = (hover == 'btn_action')
        c_fill = (0.05, 0.62, 0.98, 1.0) if run_hover else (0.02, 0.50, 0.90, 1.0)
        c_border = (0.45, 0.88, 1.0, 0.95) if run_hover else (0.15, 0.65, 0.98, 0.8)
        _draw_rounded_box(
            btn_run_x, mid_y, btn_run_w, mid_h, 10.0 * scale,
            fill_color=c_fill,
            border_color=c_border,
            border_width=max(1.0, 1.5 * scale),
        )
        _draw_text_centered("✦ RUN AGENT", btn_run_x, mid_y, btn_run_w, mid_h, size=11.5 * scale, color=(1.0, 1.0, 1.0, 1.0))

        prompt_w = btn_fix_x - prompt_x - 10.0 * scale

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
    prompt_text = getattr(scene, 'blendrelay_codex_prompt', '').strip()
    if prompt_text:
        clear_w = 22.0 * scale
        clear_x = prompt_x + prompt_w - clear_w - 6.0 * scale
        clear_y = mid_y + (mid_h - 22.0 * scale) / 2.0
        bounds['prompt_clear'] = (clear_x, clear_y, clear_w, 22.0 * scale)
        clr_hover = (hover == 'prompt_clear')
        _draw_rounded_box(
            clear_x, clear_y, clear_w, 22.0 * scale, 11.0 * scale,
            fill_color=(0.20, 0.24, 0.32, 0.9) if clr_hover else (0.12, 0.15, 0.20, 0.7),
        )
        _draw_text_centered("✕", clear_x, clear_y, clear_w, 22.0 * scale, size=10 * scale, color=(0.85, 0.90, 0.95, 1.0))
        max_text_w = prompt_w - 42.0 * scale
    else:
        max_text_w = prompt_w - 18.0 * scale

    # Reference image pill inside prompt box if attached
    from . import bridge_ui
    ref_images = bridge_ui.reference_images(scene)
    ref_image = ref_images[0] if ref_images else ''
    has_ref = bool(ref_image)
    if has_ref:
        img_pill_name = Path(ref_image).name + (f' +{len(ref_images) - 1}' if len(ref_images) > 1 else '')
        pill_w = min(110.0 * scale, (len(img_pill_name[:10]) * 7.0 + 36.0) * scale)
        pill_x = prompt_x + 8.0 * scale
        pill_y = mid_y + (mid_h - 22.0 * scale) / 2.0
        _draw_rounded_box(
            pill_x, pill_y, pill_w, 22.0 * scale, 10.0 * scale,
            fill_color=(0.06, 0.24, 0.18, 0.95),
            border_color=(0.15, 0.85, 0.50, 0.8),
            border_width=max(0.8, 1.0 * scale),
        )
        _draw_text(f"🖼 {img_pill_name[:10]}", pill_x + 6.0 * scale, pill_y + 5.0 * scale, size=10 * scale, color=(0.4, 1.0, 0.7, 1.0))
        text_start_x = pill_x + pill_w + 6.0 * scale
        avail_text_w = max_text_w - pill_w - 6.0 * scale
    else:
        text_start_x = prompt_x + 12.0 * scale
        avail_text_w = max_text_w

    # Draw Prompt Content or Placeholder
    text_y = mid_y + 13.0 * scale
    if not prompt_text and not is_typing:
        _draw_text("Prompt AI agent to create or modify scene / selected objects...", text_start_x, text_y, size=13 * scale, color=(0.42, 0.48, 0.58, 1.0))
    else:
        disp_text = _truncate_left(prompt_text, avail_text_w, size=13 * scale)
        _draw_text(disp_text, text_start_x, text_y, size=13 * scale, color=(0.95, 0.97, 1.0, 1.0))

        if is_typing and (int(time.monotonic() * 2.5) % 2 == 0):
            font_id = 0
            blf.size(font_id, max(9, int(13 * scale)))
            tw, _ = blf.dimensions(font_id, disp_text)
            _draw_text('|', text_start_x + tw + 2.0 * scale, text_y, size=13 * scale, color=(0.0, 0.85, 1.0, 1.0))

    # ── 4. Bottom Bar: Control Chips ─────────────────────────────────────────
    bot_y = hud_y + 11.0 * scale
    chip_h = 25.0 * scale
    cur_x = hud_x + 14.0 * scale

    # Chip 1: Backend Provider [⬡ Antigravity ⌵]
    c1_w = 120.0 * scale
    bounds['chip_backend'] = (cur_x, bot_y, c1_w, chip_h)
    c1_hover = (hover == 'chip_backend')
    _draw_rounded_box(
        cur_x, bot_y, c1_w, chip_h, 12.0 * scale,
        fill_color=(0.14, 0.18, 0.26, 0.95) if c1_hover else (0.09, 0.12, 0.18, 0.85),
        border_color=(0.28, 0.38, 0.55, 0.8),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered(f'⬡ {agent_name} ⌵', cur_x, bot_y, c1_w, chip_h, size=11 * scale, color=(0.88, 0.92, 0.98, 1.0))
    cur_x += c1_w + 6.0 * scale

    # Chip 2: Model [❖ Flash 3.8 ⌵]
    c2_w = 124.0 * scale
    bounds['chip_model'] = (cur_x, bot_y, c2_w, chip_h)
    c2_hover = (hover == 'chip_model')
    _draw_rounded_box(
        cur_x, bot_y, c2_w, chip_h, 12.0 * scale,
        fill_color=(0.14, 0.18, 0.26, 0.95) if c2_hover else (0.09, 0.12, 0.18, 0.85),
        border_color=(0.28, 0.38, 0.55, 0.8),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered(f'❖ {model_label} ⌵', cur_x, bot_y, c2_w, chip_h, size=11 * scale, color=(0.88, 0.92, 0.98, 1.0))
    cur_x += c2_w + 6.0 * scale

    # Chip: Auto-Verify [👁 Verify: ON / OFF]
    auto_verify = getattr(scene, 'blendrelay_auto_verify', True)
    cv_w = 104.0 * scale
    if cur_x + cv_w < hud_x + hud_w - 24.0 * scale:
        bounds['chip_verify'] = (cur_x, bot_y, cv_w, chip_h)
        cv_hover = (hover == 'chip_verify')
        if auto_verify:
            cv_fill = (0.04, 0.22, 0.26, 0.95) if cv_hover else (0.03, 0.16, 0.20, 0.85)
            cv_border = (0.0, 0.95, 1.0, 0.95) if cv_hover else (0.0, 0.75, 0.90, 0.8)
            cv_color = (0.2, 0.95, 1.0, 1.0)
            cv_text = '👁 Verify: ON'
        else:
            cv_fill = (0.16, 0.18, 0.24, 0.95) if cv_hover else (0.09, 0.12, 0.18, 0.85)
            cv_border = (0.38, 0.45, 0.55, 0.8) if cv_hover else (0.25, 0.30, 0.40, 0.6)
            cv_color = (0.60, 0.66, 0.76, 1.0)
            cv_text = '👁 Verify: OFF'
        _draw_rounded_box(
            cur_x, bot_y, cv_w, chip_h, 12.0 * scale,
            fill_color=cv_fill,
            border_color=cv_border,
            border_width=max(0.8, 1.0 * scale),
        )
        _draw_text_centered(cv_text, cur_x, bot_y, cv_w, chip_h, size=11 * scale, color=cv_color)
        cur_x += cv_w + 6.0 * scale

    # Chip 3: Reference Image Dropzone [🖼 Drop Image] or [🖼 villa.jpg ✕]
    if has_ref:
        img_name = Path(ref_image).name
        short_name = img_name if len(img_name) <= 12 else (img_name[:9] + '…')
        c_img_w = max(112.0, (len(short_name) * 7.2 + 48.0)) * scale
        bounds['chip_image'] = (cur_x, bot_y, c_img_w, chip_h)
        clr_w = 18.0 * scale
        bounds['chip_image_clear'] = (cur_x + c_img_w - clr_w - 4.0 * scale, bot_y + (chip_h - clr_w) / 2.0, clr_w, clr_w)

        img_hover = (hover == 'chip_image')
        _draw_rounded_box(
            cur_x, bot_y, c_img_w, chip_h, 12.0 * scale,
            fill_color=(0.06, 0.22, 0.16, 0.95) if img_hover else (0.04, 0.16, 0.12, 0.85),
            border_color=(0.2, 0.9, 0.55, 0.9) if img_hover else (0.12, 0.75, 0.45, 0.7),
            border_width=max(0.8, 1.0 * scale),
        )
        _draw_text(f"🖼 {short_name}", cur_x + 8.0 * scale, bot_y + 7.0 * scale, size=11 * scale, color=(0.4, 1.0, 0.7, 1.0))
        clr_hover = (hover == 'chip_image_clear')
        _draw_text_centered("✕", cur_x + c_img_w - clr_w - 4.0 * scale, bot_y + (chip_h - clr_w) / 2.0, clr_w, clr_w, size=10 * scale, color=(1.0, 0.6, 0.6, 1.0) if clr_hover else (0.7, 0.9, 0.8, 1.0))
        cur_x += c_img_w + 6.0 * scale
    else:
        c_img_w = 106.0 * scale
        bounds['chip_image'] = (cur_x, bot_y, c_img_w, chip_h)
        img_hover = (hover == 'chip_image')
        _draw_rounded_box(
            cur_x, bot_y, c_img_w, chip_h, 12.0 * scale,
            fill_color=(0.14, 0.20, 0.28, 0.95) if img_hover else (0.09, 0.12, 0.18, 0.85),
            border_color=(0.0, 0.82, 1.0, 0.8) if img_hover else (0.28, 0.38, 0.55, 0.8),
            border_width=max(0.8, 1.0 * scale),
        )
        img_label = "🖼 Drop Image" if img_hover else "🖼 + Image"
        _draw_text_centered(img_label, cur_x, bot_y, c_img_w, chip_h, size=11 * scale, color=(0.15, 0.88, 1.0, 1.0) if img_hover else (0.80, 0.86, 0.94, 1.0))
        cur_x += c_img_w + 6.0 * scale

    # Chip 3: Target Scope [🎯 Selected Only] vs [🌐 Full Scene]
    c3_w = 132.0 * scale
    bounds['chip_scope'] = (cur_x, bot_y, c3_w, chip_h)
    c3_hover = (hover == 'chip_scope')
    if only_selected:
        scope_text = '🎯 Selected Only'
        scope_fill = (0.28, 0.20, 0.08, 0.95)
        scope_border = (0.95, 0.72, 0.20, 0.9)
        scope_color = (1.0, 0.85, 0.35, 1.0)
    else:
        scope_text = '🌐 Full Scene'
        scope_fill = (0.14, 0.18, 0.26, 0.95) if c3_hover else (0.09, 0.12, 0.18, 0.85)
        scope_border = (0.28, 0.38, 0.55, 0.8)
        scope_color = (0.88, 0.92, 0.98, 1.0)

    _draw_rounded_box(
        cur_x, bot_y, c3_w, chip_h, 12.0 * scale,
        fill_color=scope_fill,
        border_color=scope_border,
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered(scope_text, cur_x, bot_y, c3_w, chip_h, size=11 * scale, color=scope_color)
    cur_x += c3_w + 6.0 * scale

    # Chip 4: Save Checkpoint [💾 Checkpoint]
    c4_w = 110.0 * scale
    bounds['chip_checkpoint'] = (cur_x, bot_y, c4_w, chip_h)
    c4_hover = (hover == 'chip_checkpoint')
    _draw_rounded_box(
        cur_x, bot_y, c4_w, chip_h, 12.0 * scale,
        fill_color=(0.14, 0.18, 0.26, 0.95) if c4_hover else (0.09, 0.12, 0.18, 0.85),
        border_color=(0.28, 0.38, 0.55, 0.8),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("💾 Checkpoint", cur_x, bot_y, c4_w, chip_h, size=11 * scale, color=(0.88, 0.92, 0.98, 1.0))
    cur_x += c4_w + 6.0 * scale

    # Chip 5: Sketch Viewport [✎ Sketch]
    c5_w = 94.0 * scale
    bounds['chip_sketch'] = (cur_x, bot_y, c5_w, chip_h)
    c5_hover = (hover == 'chip_sketch')
    _draw_rounded_box(
        cur_x, bot_y, c5_w, chip_h, 12.0 * scale,
        fill_color=(0.14, 0.18, 0.26, 0.95) if c5_hover else (0.09, 0.12, 0.18, 0.85),
        border_color=(0.28, 0.38, 0.55, 0.8),
        border_width=max(0.8, 1.0 * scale),
    )
    _draw_text_centered("✎ Sketch", cur_x, bot_y, c5_w, chip_h, size=11 * scale, color=(0.88, 0.92, 0.98, 1.0))
    cur_x += c5_w + 6.0 * scale

    # Chip 6: Quick Preset Suggestions [+ Preset ⌵]
    c6_w = 94.0 * scale
    if cur_x + c6_w < hud_x + hud_w - 30.0 * scale:
        bounds['chip_preset'] = (cur_x, bot_y, c6_w, chip_h)
        c6_hover = (hover == 'chip_preset')
        _draw_rounded_box(
            cur_x, bot_y, c6_w, chip_h, 12.0 * scale,
            fill_color=(0.14, 0.18, 0.26, 0.95) if c6_hover else (0.09, 0.12, 0.18, 0.85),
            border_color=(0.28, 0.38, 0.55, 0.8),
            border_width=max(0.8, 1.0 * scale),
        )
        _draw_text_centered("+ Preset ⌵", cur_x, bot_y, c6_w, chip_h, size=11 * scale, color=(0.88, 0.92, 0.98, 1.0))

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
            return {'PASS_THROUGH'}

        rx = mx - target_region.x
        ry = my - target_region.y
        HUD_STATE['last_mouse'] = (rx, ry)

        bounds = HUD_STATE.get('bounds', {})
        total_bounds = bounds.get('total')

        def is_inside(rect):
            if not rect:
                return False
            x0, y0, w0, h0 = rect
            return (x0 <= rx <= x0 + w0) and (y0 <= ry <= y0 + h0)

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
                )
                scale = metrics['scale']
                start_mx, start_my = HUD_STATE['drag_start_mouse']

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
                'resize_corner', 'resize_left', 'resize_right',
                'btn_action', 'btn_fix', 'prompt', 'prompt_clear', 'btn_close', 'btn_console',
                'scale_up', 'scale_down', 'scale_reset',
                'chip_backend', 'chip_model', 'chip_verify', 'chip_image_clear', 'chip_image', 'chip_scope', 'chip_checkpoint',
                'chip_sketch', 'chip_preset',
                'tab_GENERATE', 'tab_SKETCH', 'tab_HISTORY', 'tab_SETTINGS',
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

        # ── Text Input / Typing Mode ────────────────────────────────────────
        if HUD_STATE.get('typing'):
            if event.type in ('LEFTMOUSE', 'RIGHTMOUSE') and event.value == 'PRESS':
                if not is_inside(bounds.get('prompt')):
                    HUD_STATE['typing'] = False
                    target_area.tag_redraw()
                    if not is_inside(total_bounds):
                        return {'PASS_THROUGH'}

            elif event.type == 'ESC' and event.value == 'PRESS':
                HUD_STATE['typing'] = False
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.type in ('RET', 'NUMPAD_ENTER') and event.value == 'PRESS':
                HUD_STATE['typing'] = False
                if context.scene.blendrelay_codex_prompt.strip():
                    bpy.ops.blendrelay.send_to_agent()
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.type in ('BACK_SPACE', 'BACKSPACE') and event.value == 'PRESS':
                cur = context.scene.blendrelay_codex_prompt
                if cur:
                    if event.ctrl:
                        parts = cur.rstrip().rsplit(' ', 1)
                        context.scene.blendrelay_codex_prompt = (parts[0] + ' ') if len(parts) > 1 else ''
                    else:
                        context.scene.blendrelay_codex_prompt = cur[:-1]
                    target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.type == 'DEL' and event.value == 'PRESS':
                cur = context.scene.blendrelay_codex_prompt
                if cur:
                    context.scene.blendrelay_codex_prompt = cur[:-1]
                    target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.type == 'V' and event.ctrl and event.value == 'PRESS':
                raw_clip = getattr(context.window_manager, 'clipboard', '').strip().strip('"\'')
                try:
                    p = Path(raw_clip)
                    if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'):
                        from . import bridge_ui
                        bridge_ui.add_reference_images(context, [p])
                        target_area.tag_redraw()
                        return {'RUNNING_MODAL'}
                except Exception:
                    pass
                if raw_clip:
                    clean_clip = raw_clip.replace('\r\n', ' ').replace('\n', ' ')
                    context.scene.blendrelay_codex_prompt += clean_clip
                    target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.type == 'C' and event.ctrl and event.value == 'PRESS':
                context.window_manager.clipboard = context.scene.blendrelay_codex_prompt
                HUD_STATE['flash_msg'] = 'Copied to clipboard'
                HUD_STATE['flash_time'] = time.monotonic()
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.type == 'SPACE' and event.value == 'PRESS':
                context.scene.blendrelay_codex_prompt += ' '
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            elif event.ascii and event.value == 'PRESS' and ord(event.ascii) >= 32:
                context.scene.blendrelay_codex_prompt += event.ascii
                target_area.tag_redraw()
                return {'RUNNING_MODAL'}

            if event.type not in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
                return {'RUNNING_MODAL'}

        # ── Click & Drag Initiations on Floating Elements ────────────────────
        if is_inside(total_bounds):
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                # 1. Resize handles initiation
                for rkey in ('resize_corner', 'resize_right', 'resize_left'):
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
                        )
                        if getattr(scene, 'blendrelay_hud_adaptive', True):
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
                        HUD_STATE['drag_start_scale'] = float(scene.blendrelay_hud_scale)
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
                        HUD_STATE['typing'] = False
                        bpy.ops.blendrelay.send_to_agent()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 3b. Verify & Fix Button
                if is_inside(bounds.get('btn_fix')):
                    HUD_STATE['typing'] = False
                    bpy.ops.blendrelay.verify_and_fix()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 4. Prompt Input Area (Focus typing)
                if is_inside(bounds.get('prompt')):
                    if is_inside(bounds.get('prompt_clear')):
                        context.scene.blendrelay_codex_prompt = ''
                        HUD_STATE['typing'] = False
                    else:
                        HUD_STATE['typing'] = True
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 5. Clear prompt button
                if is_inside(bounds.get('prompt_clear')):
                    context.scene.blendrelay_codex_prompt = ''
                    HUD_STATE['typing'] = False
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 6. Backend Chip (Toggle Antigravity / Codex)
                if is_inside(bounds.get('chip_backend')):
                    cur = context.scene.blendrelay_agent_backend
                    context.scene.blendrelay_agent_backend = 'CODEX' if cur == 'ANTIGRAVITY' else 'ANTIGRAVITY'
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # Reference Image Chip Clear
                if is_inside(bounds.get('chip_image_clear')):
                    from . import bridge_ui
                    bridge_ui.set_reference_images(context.scene, [])
                    HUD_STATE['flash_msg'] = 'All reference images removed'
                    HUD_STATE['flash_time'] = time.monotonic()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # Reference Image Chip (Click to browse / change)
                if is_inside(bounds.get('chip_image')):
                    bpy.ops.blendrelay.browse_reference_image('INVOKE_DEFAULT')
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # Chip Auto-Verify Toggle
                if is_inside(bounds.get('chip_verify')):
                    scene.blendrelay_auto_verify = not getattr(scene, 'blendrelay_auto_verify', True)
                    status_txt = "ON" if scene.blendrelay_auto_verify else "OFF"
                    HUD_STATE['flash_msg'] = f"Auto-Verify: {status_txt}"
                    HUD_STATE['flash_time'] = time.monotonic()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 7. Model Chip (cycle through dynamically discovered choices)
                if is_inside(bounds.get('chip_model')):
                    from . import bridge_ui
                    agent = context.scene.blendrelay_agent_backend
                    model_prop = 'blendrelay_antigravity_model' if agent == 'ANTIGRAVITY' else 'blendrelay_codex_model'
                    choices = [item[0] for item in _get_model_list(agent) if item[0] != bridge_ui.MODEL_CUSTOM]
                    current = getattr(context.scene, model_prop, bridge_ui.MODEL_DEFAULT)
                    index = (choices.index(current) + 1) % len(choices) if current in choices else 0
                    setattr(context.scene, model_prop, choices[index])
                    selected = bridge_ui.selected_model(context.scene, agent)
                    HUD_STATE['flash_msg'] = f"Model: {selected or 'CLI default'}"
                    HUD_STATE['flash_time'] = time.monotonic()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 8. Target Scope Chip (Selected Only vs Full Scene)
                if is_inside(bounds.get('chip_scope')):
                    context.scene.blendrelay_only_selected = not context.scene.blendrelay_only_selected
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 9. Checkpoint Chip (Save Version immediately)
                if is_inside(bounds.get('chip_checkpoint')):
                    bpy.ops.blendrelay.checkpoint()
                    HUD_STATE['flash_msg'] = '✔ Checkpoint saved!'
                    HUD_STATE['flash_time'] = time.monotonic()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 10. Sketch Chip (Start Viewport Sketching)
                if is_inside(bounds.get('chip_sketch')):
                    bpy.ops.blendrelay.draw_viewport_sketch()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 11. Preset Suggestions (+ Tag)
                if is_inside(bounds.get('chip_preset')):
                    preset = PRESETS[HUD_STATE['preset_idx'] % len(PRESETS)]
                    HUD_STATE['preset_idx'] += 1
                    cur = context.scene.blendrelay_codex_prompt.strip()
                    if not cur:
                        context.scene.blendrelay_codex_prompt = preset
                    elif preset.lower() not in cur.lower():
                        context.scene.blendrelay_codex_prompt = f"{cur}, {preset}"
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 12. Header Mode Tabs (Generate, Sketch, Versions, Settings)
                for tid in ('GENERATE', 'SKETCH', 'HISTORY', 'SETTINGS'):
                    if is_inside(bounds.get(f'tab_{tid}')):
                        context.scene.blendrelay_ui_tab = tid
                        target_area.tag_redraw()
                        return {'RUNNING_MODAL'}

                # 13. Console Toggle Button
                if is_inside(bounds.get('btn_console')):
                    if hasattr(bpy.ops.wm, 'console_toggle'):
                        bpy.ops.wm.console_toggle()
                    target_area.tag_redraw()
                    return {'RUNNING_MODAL'}

                # 14. Close Button
                if is_inside(bounds.get('btn_close')):
                    context.scene.blendrelay_show_viewport_hud = False
                    HUD_STATE['modal_active'] = False
                    HUD_STATE['typing'] = False
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


def unregister_hud():
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
