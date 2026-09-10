"""Real GUI-event-loop test against a running runtime; writes a machine-readable receipt."""
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import bpy
from archforge_blender import ui,geometry

root=Path(os.environ['ARCHFORGE_TEST_ROOT']);receipt=root/'blender-live-result.json'
ui.register();bpy.context.scene.archforge_runtime_dir=str(root)
state={'phase':0,'started':time.monotonic(),'window':None,'old_width':None}


def finish(ok,message):
    receipt.write_text(json.dumps({'ok':ok,'message':message,'revision':bpy.context.scene.get('archforge_revision'),'objects':len(geometry.managed(ui.STATE['project']))},indent=2))
    bpy.ops.wm.quit_blender()


def step():
    try:
        if time.monotonic()-state['started']>45:raise RuntimeError('Live test timed out: '+ui.STATE['status'])
        if state['phase']==0:
            ui.request('project.create',{'width':12,'depth':10,'bedrooms':3},lambda result:ui.attach(result['project_id']))
            state['phase']=1
        elif state['phase']==1 and bpy.context.scene.get('archforge_revision')==1:
            model=json.loads(bpy.data.texts['ArchForge model.json'].as_string());window=model['windows'][0]
            state['window']=window['id'];opening=next(o for o in model['openings'] if o['id']==window['opening_id']);state['old_width']=opening['width_m']
            bpy.ops.object.select_all(action='DESELECT')
            obj=next(o for o in geometry.managed(ui.STATE['project']) if o['archforge_entity_id']==window['id'])
            obj.select_set(True);bpy.context.view_layer.objects.active=obj
            bpy.context.scene.archforge_edit_prompt='Make this window 30 cm wider and use a black frame'
            bpy.ops.archforge.propose();state['phase']=2
        elif state['phase']==2 and ui.STATE['plan']:
            assert bpy.context.scene['archforge_revision']==1
            # Wait for sync acknowledgement to reach the runtime before applying.
            if ui.STATE['connection'].pending:return .1
            bpy.ops.archforge.apply();state['phase']=3
        elif state['phase']==3 and bpy.context.scene.get('archforge_revision')==2:
            model=json.loads(bpy.data.texts['ArchForge model.json'].as_string());window=next(w for w in model['windows'] if w['id']==state['window'])
            opening=next(o for o in model['openings'] if o['id']==window['opening_id'])
            assert abs(opening['width_m']-state['old_width']-.3)<1e-6
            assert window['materials']['frame']=='mat-black'
            assert not geometry.diverged(model['project_id'])
            state['phase']=4
        elif state['phase']==4 and not ui.STATE['connection'].pending:
            bpy.ops.archforge.restore();state['phase']=5
        elif state['phase']==5 and ui.STATE['plan']:
            bpy.ops.archforge.apply();state['phase']=6
        elif state['phase']==6 and bpy.context.scene.get('archforge_revision')==3:
            model=json.loads(bpy.data.texts['ArchForge model.json'].as_string());window=next(w for w in model['windows'] if w['id']==state['window'])
            opening=next(o for o in model['openings'] if o['id']==window['opening_id'])
            assert abs(opening['width_m']-state['old_width'])<1e-6
            finish(True,'Real Blender timer, RPC, selection, preview, staged edit, and restore passed')
            return None
    except Exception as e:
        finish(False,f'{type(e).__name__}: {e}');return None
    return .1

bpy.app.timers.register(step,first_interval=1.0)
