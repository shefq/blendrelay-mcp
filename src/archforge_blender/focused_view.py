# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependable, focused camera inspection with evaluated bounds, matrix replay, and occlusion diagnostics."""
import base64
import math
from pathlib import Path
import uuid

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector


def _find_object(name):
    scene = bpy.context.scene
    if name in scene.objects:
        return scene.objects[name]
    low = name.strip().lower()
    for obj in scene.objects:
        if obj.name.lower() == low:
            return obj
    return None


def _evaluated_bounds(objects, depsgraph):
    """Compute combined world-space AABB and 8 corner points from evaluated geometry."""
    points = []
    for obj in objects:
        try:
            eval_obj = obj.evaluated_get(depsgraph)
        except Exception:
            eval_obj = obj
        if hasattr(eval_obj, 'bound_box') and eval_obj.bound_box:
            mat = eval_obj.matrix_world
            points.extend(mat @ Vector(corner) for corner in eval_obj.bound_box)
        else:
            points.append(obj.matrix_world.translation.copy())

    if not points:
        return None, None, None, []

    min_pt = Vector((
        min(p.x for p in points),
        min(p.y for p in points),
        min(p.z for p in points),
    ))
    max_pt = Vector((
        max(p.x for p in points),
        max(p.y for p in points),
        max(p.z for p in points),
    ))
    center = (min_pt + max_pt) * 0.5
    dims = max_pt - min_pt

    corners = [
        Vector((min_pt.x, min_pt.y, min_pt.z)),
        Vector((max_pt.x, min_pt.y, min_pt.z)),
        Vector((min_pt.x, max_pt.y, min_pt.z)),
        Vector((max_pt.x, max_pt.y, min_pt.z)),
        Vector((min_pt.x, min_pt.y, max_pt.z)),
        Vector((max_pt.x, min_pt.y, max_pt.z)),
        Vector((min_pt.x, max_pt.y, max_pt.z)),
        Vector((max_pt.x, max_pt.y, max_pt.z)),
    ]
    return min_pt, max_pt, center, corners


def _calculate_distance_and_frame(scene, camera, target_center, dir_vec, corners, radius, padding=0.15):
    """Back up camera along dir_vec until all 8 bounding corners fit within frustum with padding."""
    pad = max(0.02, min(0.45, float(padding)))
    allowed_extent = 0.5 - pad
    dist = max(radius * 2.2, 0.2)
    adjustments = []

    for _ in range(5):
        cam_pos = target_center + dir_vec * dist
        camera.location = cam_pos
        camera.rotation_euler = (target_center - cam_pos).to_track_quat('-Z', 'Y').to_euler()
        bpy.context.view_layer.update()

        views = [world_to_camera_view(scene, camera, pt) for pt in corners]
        x_ext = max(abs(v.x - 0.5) for v in views)
        y_ext = max(abs(v.y - 0.5) for v in views)

        scale_factor = max(x_ext / allowed_extent, y_ext / allowed_extent)
        if scale_factor > 1.005:
            dist *= scale_factor * 1.02
        else:
            break

    views = [world_to_camera_view(scene, camera, pt) for pt in corners]
    fully_framed = all(0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0 and v.z > 0.0 for v in views)
    min_x = min(v.x for v in views)
    max_x = max(v.x for v in views)
    min_y = min(v.y for v in views)
    max_y = max(v.y for v in views)
    frame_coverage = round(min(1.0, max(0.0, (max_x - min_x) * (max_y - min_y))), 3)

    adjustments.append(f"Framed target bounding box at distance {dist:.2f}m with {pad*100:.0f}% margin")
    return dist, frame_coverage, fully_framed, adjustments


def _evaluate_visibility_and_occluders(scene, depsgraph, camera_pos, target_center, corners, resolved_objects):
    """Cast rays from camera towards target sample points to score visibility and identify occluders."""
    samples = list(corners) + [target_center]
    for obj in resolved_objects:
        samples.append(obj.matrix_world.translation.copy())

    resolved_names = {obj.name for obj in resolved_objects}
    visible_count = 0
    occluders = set()

    for pt in samples:
        ray_dir = pt - camera_pos
        dist = ray_dir.length
        if dist < 1e-4:
            visible_count += 1
            continue
        hit, loc, norm, index, hit_obj, matrix = scene.ray_cast(
            depsgraph,
            camera_pos,
            ray_dir.normalized(),
            distance=max(0.001, dist - 0.01)
        )
        if hit and hit_obj is not None:
            if hit_obj.name in resolved_names:
                visible_count += 1
            else:
                occluders.add(hit_obj.name)
        else:
            visible_count += 1

    visibility_score = round(visible_count / max(1, len(samples)), 2)
    return visibility_score, sorted(list(occluders))


def capture(root, target_objects=(), camera_position_hints=None, context_mode="targets_and_nearby",
            padding=0.15, max_dimension=768, render_mode="auto", camera_matrix_world=None,
            lens_mm=50, cycles_samples=32, **kwargs):
    """Capture one or more dependable focused camera views of target objects."""
    if isinstance(target_objects, str):
        target_objects = [target_objects]
    if not target_objects:
        raise ValueError("target_objects must contain at least one object name")

    resolved_objects = []
    missing_objects = []
    for name in target_objects:
        obj = _find_object(name)
        if obj is not None:
            resolved_objects.append(obj)
        else:
            missing_objects.append(name)

    if missing_objects:
        return {
            'failed': True,
            'error': f"Target objects not found in scene: {', '.join(missing_objects)}",
            'resolved_objects': [o.name for o in resolved_objects],
            'missing_objects': missing_objects,
            'views': [],
            'scene_state_restored': True,
        }

    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    min_pt, max_pt, center, corners = _evaluated_bounds(resolved_objects, depsgraph)
    if min_pt is None:
        raise RuntimeError("Could not determine geometry bounds for target objects")

    dims = max_pt - min_pt
    radius = max(dims.length * 0.5, 0.1)

    # ── State Preservation ──────────────────────────────────────────────
    render = scene.render
    orig_camera = scene.camera
    orig_engine = render.engine
    orig_res_x = render.resolution_x
    orig_res_y = render.resolution_y
    orig_res_pct = render.resolution_percentage
    orig_filepath = render.filepath
    orig_format = render.image_settings.file_format
    orig_quality = render.image_settings.quality
    orig_transparent = render.film_transparent
    orig_compositing = render.use_compositing

    orig_cycles_samples = getattr(scene.cycles, 'samples', 32) if hasattr(scene, 'cycles') else 32
    orig_mode = bpy.context.mode
    orig_active = bpy.context.view_layer.objects.active
    orig_selected = [o.name for o in bpy.context.selected_objects]
    orig_visibility = {obj: obj.hide_render for obj in scene.objects}

    # Ensure Object Mode for camera creation and matrix manipulation
    if orig_mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    camera_data = None
    camera_obj = None
    views_result = []
    images_result = []

    try:
        # ── Context Mode Visibility ─────────────────────────────────────
        if context_mode == 'targets_only':
            resolved_set = set(resolved_objects)
            for obj in scene.objects:
                if obj not in resolved_set and obj.type not in ('LIGHT', 'CAMERA'):
                    obj.hide_render = True
        elif context_mode == 'targets_and_nearby':
            expand = max(radius * 1.5, 1.0)
            near_min = min_pt - Vector((expand, expand, expand))
            near_max = max_pt + Vector((expand, expand, expand))
            resolved_set = set(resolved_objects)
            for obj in scene.objects:
                if obj in resolved_set or obj.type in ('LIGHT', 'CAMERA'):
                    continue
                loc = obj.matrix_world.translation
                if not (near_min.x <= loc.x <= near_max.x and
                        near_min.y <= loc.y <= near_max.y and
                        near_min.z <= loc.z <= near_max.z):
                    obj.hide_render = True

        # ── Camera Setup ────────────────────────────────────────────────
        camera_data = bpy.data.cameras.new('ArchForge_Focused_Cam')
        camera_obj = bpy.data.objects.new(camera_data.name, camera_data)
        scene.collection.objects.link(camera_obj)
        scene.camera = camera_obj
        camera_data.lens = max(5.0, min(500.0, float(lens_mm)))
        camera_data.clip_start = max(0.005, radius * 0.01)
        camera_data.clip_end = max(1000.0, radius * 50.0)

        # ── Render Engine Setup ─────────────────────────────────────────
        req_mode = str(render_mode).lower()
        max_limit = 512 if req_mode == 'cycles' else 2048
        max_dim = max(128, min(max_limit, int(max_dimension)))
        render.resolution_x = max_dim
        render.resolution_y = max(1, round(max_dim * 0.75))
        render.resolution_percentage = 100
        render.image_settings.file_format = 'JPEG'
        render.image_settings.quality = 85
        if req_mode == 'cycles':
            try:
                render.engine = 'CYCLES'
                if hasattr(scene, 'cycles'):
                    scene.cycles.samples = min(max(1, int(cycles_samples)), 64)
            except Exception:
                render.engine = 'BLENDER_WORKBENCH'
        elif req_mode == 'eevee':
            try:
                render.engine = 'BLENDER_EEVEE_NEXT'
            except Exception:
                try:
                    render.engine = 'BLENDER_EEVEE'
                except Exception:
                    render.engine = 'BLENDER_WORKBENCH'
        else:
            try:
                render.engine = 'BLENDER_WORKBENCH'
            except Exception:
                pass

        # ── Viewpoints Setup ────────────────────────────────────────────
        viewpoints = []
        if camera_matrix_world is not None:
            viewpoints.append(('matrix', camera_matrix_world))
        elif camera_position_hints and isinstance(camera_position_hints, (list, tuple)):
            for hint in camera_position_hints[:4]:
                viewpoints.append(('hint', hint))
        else:
            viewpoints.append(('default', None))

        out_dir = Path(root) / 'focused_views'
        out_dir.mkdir(parents=True, exist_ok=True)
        run_tag = uuid.uuid4().hex[:10]

        # ── Render Each View ────────────────────────────────────────────
        for idx, (kind, param) in enumerate(viewpoints, start=1):
            adjustments = []
            if kind == 'matrix':
                if len(param) == 16:
                    mat = Matrix([param[i:i+4] for i in range(0, 16, 4)])
                elif len(param) == 4 and len(param[0]) == 4:
                    mat = Matrix(param)
                else:
                    raise ValueError("camera_matrix_world must be 16 floats or a 4x4 matrix")
                camera_obj.matrix_world = mat
                cam_pos = camera_obj.matrix_world.translation.copy()
                adjustments.append("Replayed exact camera_matrix_world")
                bpy.context.view_layer.update()

                views = [world_to_camera_view(scene, camera_obj, pt) for pt in corners]
                fully_framed = all(0.0 <= v.x <= 1.0 and 0.0 <= v.y <= 1.0 and v.z > 0.0 for v in views)
                min_x = min(v.x for v in views)
                max_x = max(v.x for v in views)
                min_y = min(v.y for v in views)
                max_y = max(v.y for v in views)
                frame_coverage = round(min(1.0, max(0.0, (max_x - min_x) * (max_y - min_y))), 3)
            else:
                if kind == 'hint' and param:
                    hint_vec = Vector(param)
                    diff = hint_vec - center
                    dir_vec = diff.normalized() if diff.length > 1e-4 else Vector((1.0, 1.0, 0.6)).normalized()
                    adjustments.append(f"Positioned from hint vector: {[round(v, 2) for v in param]}")
                else:
                    el = math.radians(24.0)
                    az = math.radians(35.0)
                    dir_vec = Vector((
                        math.cos(el) * math.cos(az),
                        math.cos(el) * math.sin(az),
                        math.sin(el)
                    )).normalized()
                    adjustments.append("Positioned from standard 35° azimuth / 24° elevation angle")

                dist, frame_coverage, fully_framed, frame_adjs = _calculate_distance_and_frame(
                    scene, camera_obj, center, dir_vec, corners, radius, padding
                )
                adjustments.extend(frame_adjs)
                cam_pos = camera_obj.matrix_world.translation.copy()

            # Occlusion & Visibility Diagnostics
            deps = bpy.context.evaluated_depsgraph_get()
            vis_score, occluder_names = _evaluate_visibility_and_occluders(
                scene, deps, cam_pos, center, corners, resolved_objects
            )

            # Render to file
            img_path = out_dir / f'{run_tag}-view-{idx}.jpg'
            render.filepath = str(img_path)
            bpy.context.view_layer.update()
            bpy.ops.render.render(write_still=True)

            img_bytes = img_path.read_bytes() if img_path.is_file() else b''
            b64_data = base64.b64encode(img_bytes).decode('ascii') if img_bytes else ''

            cam_mat_flat = [round(float(v), 6) for row in camera_obj.matrix_world for v in row]

            view_info = {
                'image_path': str(img_path),
                'camera_matrix_world': cam_mat_flat,
                'camera_position': [round(float(v), 4) for v in cam_pos],
                'target_center': [round(float(v), 4) for v in center],
                'target_dimensions': [round(float(v), 4) for v in dims],
                'frame_coverage': frame_coverage,
                'visibility_score': vis_score,
                'fully_framed': fully_framed,
                'occluders': occluder_names,
                'adjustments': adjustments,
            }
            views_result.append(view_info)
            if b64_data:
                images_result.append({'data': b64_data, 'mime_type': 'image/jpeg'})

    finally:
        # ── State Restoration (Guaranteed) ──────────────────────────────
        for obj, orig_hide in orig_visibility.items():
            try:
                obj.hide_render = orig_hide
            except Exception:
                pass

        if camera_obj and camera_obj.name in bpy.data.objects:
            bpy.data.objects.remove(camera_obj, do_unlink=True)
        if camera_data and camera_data.name in bpy.data.cameras:
            bpy.data.cameras.remove(camera_data)

        scene.camera = orig_camera
        render.engine = orig_engine
        render.resolution_x = orig_res_x
        render.resolution_y = orig_res_y
        render.resolution_percentage = orig_res_pct
        render.filepath = orig_filepath
        render.image_settings.file_format = orig_format
        render.image_settings.quality = orig_quality
        render.film_transparent = orig_transparent
        render.use_compositing = orig_compositing

        if hasattr(scene, 'cycles'):
            try:
                scene.cycles.samples = orig_cycles_samples
            except Exception:
                pass

        # Restore active object and selection
        if orig_active and orig_active.name in bpy.context.scene.objects:
            try:
                bpy.context.view_layer.objects.active = orig_active
            except Exception:
                pass

        for name in orig_selected:
            if name in bpy.context.scene.objects:
                try:
                    bpy.context.scene.objects[name].select_set(True)
                except Exception:
                    pass

        # Restore original interaction mode
        if orig_mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
            try:
                bpy.ops.object.mode_set(mode=orig_mode)
            except Exception:
                pass

    return {
        'resolved_objects': [o.name for o in resolved_objects],
        'missing_objects': missing_objects,
        'views': views_result,
        'scene_state_restored': True,
        'images': images_result,
    }
