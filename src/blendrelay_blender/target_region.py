"""Interactive spatial prompt target; the wire box is a non-rendering guide."""
import bpy
from mathutils import Vector
from bpy_extras import view3d_utils

TAG = 'blendrelay_target_region'
PROMPT_RULES = ('\nSPATIAL TARGET: target_region is the oriented volume chosen by the user. '
    'Treat its guide_object as a non-rendering guide; never modify or delete it. '
    'Fit requested new geometry within the volume. For edits, inspect overlapping_bounds '
    'and change only the requested portion. Preserve geometry outside the region unless '
    'explicitly requested otherwise. Bounds overlap is a candidate test, not mesh intersection. '
    'The region replaces selected-only scope for this request. Read world_corners and '
    'matrix_world for rotation; dimensions are local-axis extents in Blender units. '
    'Use meters_per_blender_unit to interpret physical size.\n')


def box(scene):
    return next((o for o in scene.objects if o.get(TAG)), None)


def payload(context):
    guide = box(context.scene)
    if guide is None:
        return None
    context.view_layer.update()
    if min(guide.dimensions) < .001:
        raise RuntimeError('The target box must have nonzero width, length and height.')
    corners = [guide.matrix_world @ Vector(v) for v in guide.bound_box]
    inverse = guide.matrix_world.inverted_safe()
    lower = [min(v[i] for v in guide.bound_box) for i in range(3)]
    upper = [max(v[i] for v in guide.bound_box) for i in range(3)]
    intersecting = []
    for obj in context.scene.objects:
        if obj.get(TAG) or obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME', 'POINTCLOUD'}:
            continue
        points = [inverse @ (obj.matrix_world @ Vector(v)) for v in obj.bound_box]
        if all(min(p[i] for p in points) <= upper[i] and max(p[i] for p in points) >= lower[i] for i in range(3)):
            intersecting.append({'name': obj.name, 'type': obj.type,
                                 'center': list(obj.matrix_world.translation),
                                 'dimensions': list(obj.dimensions),
                                 'materials': [slot.material.name for slot in obj.material_slots if slot.material]})
    center = guide.matrix_world @ Vector([(lower[i]+upper[i])/2 for i in range(3)])
    return dict(guide_object=guide.name, center=list(center),
                dimensions=list(guide.dimensions), matrix_world=[list(r) for r in guide.matrix_world],
                world_corners=[list(p) for p in corners],
                units=context.scene.unit_settings.system,
                meters_per_blender_unit=context.scene.unit_settings.scale_length,
                axes={'X': 'width', 'Y': 'length', 'Z': 'height'},
                selected_objects=[o.name for o in context.selected_objects if not o.get(TAG)],
                overlapping_bounds=intersecting,
                instructions='Use this oriented box as the spatial target for the user request. '
                'It is a guide, not generated geometry. Keep the guide intact. Preserve objects outside '
                'the region unless the user explicitly requests changes there. Overlapping bounds '
                'are candidates, not proof of mesh intersection. Fit new geometry to this volume; '
                'for edits, inspect candidate objects and modify only the requested portion.')


class BR_OT_DrawRegion(bpy.types.Operator):
    bl_idname = 'blendrelay.draw_target_region'
    bl_label = 'Draw Target Box'
    bl_description = 'Drag a footprint on the cursor-height XY plane; wheel adjusts height; release to finish'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D' or context.mode != 'OBJECT':
            self.report({'WARNING'}, 'Use Object Mode in the 3D Viewport')
            return {'CANCELLED'}
        self.area = context.area
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.depth = context.scene.cursor.location.z
        self.start = None
        self.height = 2.5
        self.guide = None
        from . import viewport_hud
        viewport_hud.HUD_STATE['typing'] = False
        context.window_manager.modal_handler_add(self)
        self.area.header_text_set('Drag footprint • Wheel: height • Esc: cancel')
        return {'RUNNING_MODAL'}

    def point(self, event):
        xy = (event.mouse_x-self.region.x, event.mouse_y-self.region.y)
        rv = self.area.spaces.active.region_3d
        origin = view3d_utils.region_2d_to_origin_3d(self.region, rv, xy)
        direction = view3d_utils.region_2d_to_vector_3d(self.region, rv, xy)
        if abs(direction.z) < 1e-5:
            return None
        distance = (self.depth-origin.z)/direction.z
        return origin + direction*distance if distance >= 0 else None

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            if self.guide:
                mesh = self.guide.data
                bpy.data.objects.remove(self.guide, do_unlink=True)
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            self.area.header_text_set(None)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if not (self.region.x <= event.mouse_x < self.region.x+self.region.width and
                    self.region.y <= event.mouse_y < self.region.y+self.region.height):
                return {'RUNNING_MODAL'}
            self.start = self.point(event)
            if self.start is None:
                self.report({'WARNING'}, 'Orbit the viewport to see the horizontal drawing plane')
                return {'RUNNING_MODAL'}
            if box(context.scene):
                self.report({'WARNING'}, 'Adjust or remove the existing target box first')
                self.area.header_text_set(None)
                return {'CANCELLED'}
            bpy.ops.mesh.primitive_cube_add(size=2, location=self.start)
            self.guide = context.object
            self.guide.name = 'BlendRelay Target Region'
            self.guide[TAG] = True
            self.guide.display_type = 'WIRE'
            self.guide.show_in_front = True
            self.guide.hide_render = True
            self.guide.dimensions = (.05, .05, self.height)
            self.area.spaces.active.overlay.show_overlays = True
            self.area.spaces.active.overlay.show_outline_selected = True
        if self.guide:
            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                self.height = max(.05, self.height + (.1 if event.type == 'WHEELUPMOUSE' else -.1))
            end = self.point(event)
            if end is not None:
                self.guide.location = ((self.start.x+end.x)/2, (self.start.y+end.y)/2, self.depth+self.height/2)
                self.guide.dimensions = (max(.05, abs(end.x-self.start.x)), max(.05, abs(end.y-self.start.y)), self.height)
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self.area.header_text_set(None)
                return {'FINISHED'}
        return {'RUNNING_MODAL'}


class BR_OT_SelectRegion(bpy.types.Operator):
    bl_idname = 'blendrelay.select_target_region'
    bl_label = 'Adjust Box'

    def execute(self, context):
        guide = box(context.scene)
        if guide is None:
            return {'CANCELLED'}
        for obj in context.selected_objects:
            obj.select_set(False)
        guide.select_set(True)
        guide['edit_faces'] = False
        context.view_layer.objects.active = guide
        bpy.ops.wm.tool_set_by_id(name='builtin.scale')
        return {'FINISHED'}


def face_frame(guide, axis, side):
    points = [v.co for v in guide.data.vertices]
    lower = Vector([min(p[i] for p in points) for i in range(3)])
    upper = Vector([max(p[i] for p in points) for i in range(3)])
    center = (lower + upper) / 2
    center[axis] = upper[axis] if side > 0 else lower[axis]
    local_normal = Vector((0, 0, 0))
    local_normal[axis] = side
    normal = (guide.matrix_world.to_3x3().inverted().transposed() @ local_normal).normalized()
    frame = normal.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    frame.translation = guide.matrix_world @ center
    return frame, lower, upper


def move_face(guide, axis, side, distance, original):
    """Move one box plane by a world-space normal distance, keeping its opposite fixed."""
    lower = min(p[axis] for p in original)
    upper = max(p[axis] for p in original)
    local_normal = Vector((0, 0, 0))
    local_normal[axis] = side
    normal_length = (guide.matrix_world.to_3x3().inverted().transposed() @ local_normal).length
    delta = max(distance * normal_length, -(upper-lower) + .001)
    edge = upper if side > 0 else lower
    for vertex, point in zip(guide.data.vertices, original):
        vertex.co = point
        if abs(point[axis]-edge) < 1e-6:
            vertex.co[axis] += side * delta
    guide.data.update()


class BR_OT_AdjustRegionFaces(bpy.types.Operator):
    bl_idname = 'blendrelay.adjust_region_faces'
    bl_label = 'Adjust Six Faces'
    bl_description = 'Drag the arrow at each face to move that side perpendicular to itself'

    def execute(self, context):
        guide = box(context.scene)
        if guide is None or context.mode != 'OBJECT':
            self.report({'WARNING'}, 'Select Object Mode and create a target box first')
            return {'CANCELLED'}
        for obj in context.selected_objects:
            obj.select_set(False)
        guide.select_set(True)
        context.view_layer.objects.active = guide
        guide['edit_faces'] = True
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
        context.space_data.show_gizmo = True
        context.area.tag_redraw()
        return {'FINISHED'}


class BR_GGT_RegionFaces(bpy.types.GizmoGroup):
    bl_idname = 'BR_GGT_region_faces'
    bl_label = 'BlendRelay Region Faces'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT', 'SHOW_MODAL_ALL'}

    @classmethod
    def poll(cls, context):
        guide = box(context.scene)
        return bool(context.mode == 'OBJECT' and guide and guide.select_get() and guide.get('edit_faces'))

    def setup(self, context):
        self.handles = []
        self.drag = None
        for axis in range(3):
            for side in (-1, 1):
                arrow = self.gizmos.new('GIZMO_GT_arrow_3d')
                arrow.draw_style = 'BOX'
                arrow.color = [(1, .25, .2), (.25, 1, .3), (.25, .5, 1)][axis]
                arrow.alpha = .8
                arrow.color_highlight = (1, .85, .2)
                arrow.alpha_highlight = 1
                arrow.scale_basis = .65
                arrow.use_draw_modal = True
                arrow.target_set_handler('offset', get=lambda: 0.0,
                    set=lambda value, a=axis, s=side: self.resize(value, a, s))
                self.handles.append((arrow, axis, side))

    def invoke_prepare(self, context, gizmo):
        guide = box(context.scene)
        self.drag = (guide, [v.co.copy() for v in guide.data.vertices])

    def resize(self, value, axis, side):
        if self.drag:
            guide, original = self.drag
            move_face(guide, axis, side, value, original)

    def draw_prepare(self, context):
        guide = box(context.scene)
        if any(arrow.is_modal for arrow, _, _ in self.handles):
            return
        for arrow, axis, side in self.handles:
            arrow.matrix_basis = face_frame(guide, axis, side)[0]


class BR_OT_RemoveRegion(bpy.types.Operator):
    bl_idname = 'blendrelay.remove_target_region'
    bl_label = 'Remove Target Box'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        guide = box(context.scene)
        if guide:
            mesh = guide.data
            bpy.data.objects.remove(guide, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        return {'FINISHED'}


class BR_OT_SendRegion(bpy.types.Operator):
    bl_idname = 'blendrelay.send_region_prompt'
    bl_label = 'Apply Prompt to Region'
    bl_description = 'Send the prompt and box bounds to the configured agent; replaces selected-only scope'

    def execute(self, context):
        from . import bridge_ui
        try:
            bridge_ui.start_agent(context, use_region=True)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw(layout, context):
    card = layout.box()
    card.label(text='3D Prompt Region', icon='CUBE')
    guide = box(context.scene)
    if guide is None:
        card.operator('blendrelay.draw_target_region', text='Draw Target Box')
        card.label(text='Drag width/length; wheel adjusts height.')
        card.label(text='Drawing plane: XY at the 3D cursor height')
    else:
        card.operator('blendrelay.adjust_region_faces', text='Adjust Six Faces')
        card.operator('blendrelay.remove_target_region', text='Remove Box', icon='TRASH')
    card.label(text='Describe the change, style and materials.')
    prompt_prop = 'blendrelay_codex_prompt'
    card.prop(context.scene, prompt_prop, text='Prompt')
    row = card.row()
    row.enabled = guide is not None
    row.operator('blendrelay.send_region_prompt')


CLASSES = (BR_OT_DrawRegion, BR_OT_SelectRegion, BR_OT_AdjustRegionFaces,
           BR_GGT_RegionFaces, BR_OT_RemoveRegion, BR_OT_SendRegion)

# Compatibility aliases
AF_OT_DrawRegion = BR_OT_DrawRegion
AF_OT_SelectRegion = BR_OT_SelectRegion
AF_OT_AdjustRegionFaces = BR_OT_AdjustRegionFaces
AF_GGT_RegionFaces = BR_GGT_RegionFaces
AF_OT_RemoveRegion = BR_OT_RemoveRegion
AF_OT_SendRegion = BR_OT_SendRegion
