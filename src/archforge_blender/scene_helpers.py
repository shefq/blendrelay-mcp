"""Small general-purpose helpers exposed to generated Blender Python as ``af``."""
import math
import bpy
from mathutils import Vector


def collection(name, parent=None):
    result=bpy.data.collections.get(name) or bpy.data.collections.new(name)
    target=parent or bpy.context.scene.collection
    if result.name not in target.children:target.children.link(result)
    return result


def material(name, color=(.8,.8,.8,1), metallic=0.0, roughness=.5):
    result=bpy.data.materials.get(name) or bpy.data.materials.new(name)
    result.diffuse_color=(*color[:3],color[3] if len(color)>3 else 1)
    result.use_nodes=True
    shader=result.node_tree.nodes.get('Principled BSDF')
    if shader:
        shader.inputs['Base Color'].default_value=result.diffuse_color
        shader.inputs['Metallic'].default_value=metallic
        shader.inputs['Roughness'].default_value=roughness
    return result


def assign(obj, mat):
    if obj.data and hasattr(obj.data,'materials'):
        if obj.data.materials:obj.data.materials[0]=mat
        else:obj.data.materials.append(mat)
    return obj


def _move_to(obj, target):
    for owner in list(obj.users_collection):owner.objects.unlink(obj)
    target.objects.link(obj)
    return obj


def cube(name, location=(0,0,0), dimensions=(1,1,1), material=None, target=None, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj=bpy.context.object;obj.name=name;obj.dimensions=dimensions
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if target:_move_to(obj,target)
    if material:assign(obj,material)
    if bevel:
        modifier=obj.modifiers.new('Edge softness','BEVEL');modifier.width=bevel;modifier.segments=3
    return obj


def cylinder(name, location=(0,0,0), radius=1, depth=2, vertices=32, material=None, target=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices,radius=radius,depth=depth,location=location)
    obj=bpy.context.object;obj.name=name
    if target:_move_to(obj,target)
    if material:assign(obj,material)
    return obj


def area_light(name, location, energy=1000, size=5, color=(1,1,1), rotation=(0,0,0)):
    data=bpy.data.lights.get(name) or bpy.data.lights.new(name,'AREA')
    data.energy=energy;data.shape='DISK';data.size=size;data.color=color
    obj=bpy.data.objects.get(name) or bpy.data.objects.new(name,data)
    if not obj.users_collection:bpy.context.scene.collection.objects.link(obj)
    obj.location=location;obj.rotation_euler=rotation
    return obj


def camera(name='Camera', location=(10,-10,8), target=(0,0,0), lens=45):
    data=bpy.data.cameras.get(name) or bpy.data.cameras.new(name)
    obj=bpy.data.objects.get(name) or bpy.data.objects.new(name,data)
    if not obj.users_collection:bpy.context.scene.collection.objects.link(obj)
    obj.location=location;data.lens=lens;look_at(obj,target);bpy.context.scene.camera=obj
    return obj


def look_at(obj, target):
    direction=Vector(target)-obj.location
    if direction.length:obj.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
    return obj


def world(color=(.05,.05,.05,1), strength=.5):
    scene=bpy.context.scene
    scene.world=scene.world or bpy.data.worlds.new('ArchForge World')
    scene.world.use_nodes=True
    background=scene.world.node_tree.nodes.get('Background')
    if background:
        background.inputs['Color'].default_value=color
        background.inputs['Strength'].default_value=strength
    return scene.world


def smooth(obj, angle_degrees=30):
    if obj.type=='MESH':
        for polygon in obj.data.polygons:polygon.use_smooth=True
        if hasattr(obj.data,'set_sharp_from_angle'):
            obj.data.set_sharp_from_angle(angle=math.radians(angle_degrees))
    return obj
