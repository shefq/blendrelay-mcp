"""Launched by Blender in batch mode; does not depend on its timer loop."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bpy
from mathutils import Vector
from archforge_domain.geometry import build_specs
from archforge_blender.geometry import stage, commit

source,output,format=sys.argv[sys.argv.index('--')+1:]
model=json.loads(Path(source).read_text())
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
for _ in stage(model,build_specs(model)):pass
commit(model['revision'])
scene=bpy.context.scene
pts=[v['xy_m'] for v in model['vertices']];x0,x1=min(p[0] for p in pts),max(p[0] for p in pts);y0,y1=min(p[1] for p in pts),max(p[1] for p in pts)
cx,cy=(x0+x1)/2,(y0+y1)/2;size=max(x1-x0,y1-y0)
camera=bpy.data.cameras.new('ArchForge camera');obj=bpy.data.objects.new('ArchForge camera',camera);scene.collection.objects.link(obj)
obj.location=(cx+size*.8,cy-size*1.1,size*2.0)
obj.rotation_euler=(Vector((cx,cy,1.1))-obj.location).to_track_quat('-Z','Y').to_euler();camera.type='ORTHO';camera.ortho_scale=size*1.6;scene.camera=obj
sun=bpy.data.lights.new('ArchForge sun','SUN');sun.energy=2;sun.angle=.12;sunobj=bpy.data.objects.new('ArchForge sun',sun);scene.collection.objects.link(sunobj);sunobj.rotation_euler=(.45,-.55,-.45)
scene.world.use_nodes=True
background=scene.world.node_tree.nodes.get('Background');background.inputs['Color'].default_value=(.65,.72,.8,1);background.inputs['Strength'].default_value=.6
fill=bpy.data.lights.new('ArchForge softbox','AREA');fill.energy=2200;fill.shape='DISK';fill.size=size*1.2
fillobj=bpy.data.objects.new('ArchForge softbox',fill);scene.collection.objects.link(fillobj);fillobj.location=(cx,cy,10)
scene.render.resolution_x=1100;scene.render.resolution_y=850;scene.render.resolution_percentage=100
scene.render.engine='CYCLES';scene.cycles.samples=12;scene.cycles.use_denoising=True
# A cutaway view makes the generated room layout visible in the sample/export render.
for obj in scene.objects:
    role=obj.get('archforge_part_role','')
    if role=='ceiling' or role.startswith('roof'):obj.hide_set(True);obj.hide_render=True
scene['archforge_cutaway']=True
if format=='blend':bpy.ops.wm.save_as_mainfile(filepath=output)
elif format=='glb':bpy.ops.export_scene.gltf(filepath=output,export_format='GLB',use_selection=False)
else:
    scene.render.filepath=output;scene.render.image_settings.file_format='PNG';bpy.ops.render.render(write_still=True)
