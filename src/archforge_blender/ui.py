# SPDX-License-Identifier: GPL-3.0-or-later
import json
import time
import uuid
import bpy
from bpy.props import StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty
from bpy.app.handlers import persistent
from .client import Connection, default_root
from . import geometry

STATE={'connection':None,'status':'Start the local runtime, then connect','project':'','instance':uuid.uuid4().hex,
       'attached':False,'last_poll':0,'task':None,'command':None,'plan':None,'projects':[],
       'epoch':0,'last_diverged':[],'force':False,'completed':set(),'export_job':None,'last_job_poll':0,'output':'','source':None}


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()


def status(text):STATE['status']=str(text)[:300];redraw()


def request(method,params,callback=None):
    try:
        root=bpy.context.scene.archforge_runtime_dir
        if STATE['connection'] is None or str(STATE['connection'].root)!=root:
            if STATE['connection']:STATE['connection'].close()
            STATE['connection']=Connection(root)
        def done(response):
            if 'error' in response:
                status(response['error']['message'])
                if response['error']['code'] in ('SESSION_EXPIRED','UNAUTHORIZED'):STATE['attached']=False
                return
            if callback:callback(response['result'])
        STATE['connection'].request(method,params,done)
    except Exception as e:status(e)


def attach(project):
    scene=bpy.context.scene
    bad=[] if STATE['force'] else geometry.diverged(project)
    def done(result):
        STATE.update(project=project,attached=True,plan=None);status('Connected · synchronizing project')
    request('session.attach',{'project_id':project,'instance_id':STATE['instance'],'revision':scene.get('archforge_revision',0),'scene_epoch':STATE['epoch'],'diverged_ids':bad},done)


def ack(cmd,success=True,message=''):
    request('session.ack',{'project_id':STATE['project'],'instance_id':STATE['instance'],'command_id':cmd['id'],'success':success,'message':message,'revision':bpy.context.scene.get('archforge_revision',0)})


def command(cmd):
    if not cmd or cmd['id'] in STATE['completed'] or STATE['task']:return
    STATE['command']=cmd
    try:
        kind=cmd['kind']
        if kind in ('stage','sync'):
            if kind=='stage' and (cmd['base_revision']!=bpy.context.scene.get('archforge_revision',0) or cmd['scene_epoch']!=STATE['epoch']):raise RuntimeError('Scene changed after the proposal; generate a new proposal')
            STATE['task']=iter(geometry.stage(cmd['model'],cmd['specs'],force=STATE['force']))
            STATE['force']=False;status('Building candidate geometry…')
        elif kind=='commit':
            geometry.commit(cmd['revision']);ack(cmd);STATE['completed'].add(cmd['id']);status(f"Revision {cmd['revision']} applied")
        elif kind=='discard':
            geometry.discard();ack(cmd);STATE['completed'].add(cmd['id']);status('Uncommitted change discarded')
    except Exception as e:
        geometry.discard();ack(cmd,False,str(e));STATE['completed'].add(cmd['id']);STATE['task']=None;status(e)


def timer():
    try:
        c=STATE['connection']
        if c:c.tick()
        if STATE['task']:
            deadline=time.monotonic()+.008
            while time.monotonic()<deadline:
                try:next(STATE['task'])
                except StopIteration:
                    cmd=STATE['command']
                    geometry.verify_stage()
                    STATE['task']=None
                    if cmd['kind']=='sync':geometry.commit(cmd['revision']);status(f"Project ready · revision {cmd['revision']}")
                    else:status('Candidate ready · committing')
                    STATE['completed'].add(cmd['id']);ack(cmd);break
        if STATE['attached'] and time.monotonic()-STATE['last_poll']>.4 and c and not c.pending:
            project=STATE['project'];bad=geometry.diverged(project)
            if bad!=STATE['last_diverged']:STATE['epoch']+=1;STATE['last_diverged']=bad
            STATE['last_poll']=time.monotonic()
            selection=sorted({o.get('archforge_entity_id') for o in bpy.context.selected_objects if o.get('archforge_project_id')==project and o.get('archforge_entity_id')})
            request('session.poll',{'project_id':project,'instance_id':STATE['instance'],'revision':bpy.context.scene.get('archforge_revision',0),'scene_epoch':STATE['epoch'],'selected_entity_ids':selection,'diverged_ids':bad},lambda result:command(result.get('command')))
        if STATE['export_job'] and time.monotonic()-STATE['last_job_poll']>1:
            STATE['last_job_poll']=time.monotonic()
            def job_done(result):
                if result['status'] in ('complete','failed'):
                    STATE['export_job']=None;STATE['output']=result.get('result',{}).get('path','')
                    status('Export saved: '+STATE['output'] if result['status']=='complete' else result.get('message','Export failed'))
            request('job.get',{'job_id':STATE['export_job']},job_done)
    except Exception as e:
        cmd=STATE.get('command')
        if STATE['task']:
            STATE['task']=None;geometry.discard()
            if cmd:ack(cmd,False,str(e))
        else:
            if STATE['connection']:STATE['connection'].close()
            STATE['connection']=None;STATE['attached']=False
        status(e)
    return .025


class AF_OT_Refresh(bpy.types.Operator):
    bl_idname='archforge.refresh';bl_label='Connect / Refresh'
    def execute(self,context):
        request('project.list',{},lambda result:(STATE.update(projects=result),status('Runtime ready')))
        if context.scene.get('archforge_project_id'):
            text=bpy.data.texts.get('ArchForge model.json')
            if text:
                try:request('project.adopt',{'model':json.loads(text.as_string())},lambda r:attach(r['project_id']))
                except ValueError:status('Saved model metadata is invalid')
            else:attach(context.scene['archforge_project_id'])
        return {'FINISHED'}


class AF_OT_Attach(bpy.types.Operator):
    bl_idname='archforge.attach';bl_label='Open project'
    project_id:StringProperty()
    def execute(self,context):attach(self.project_id);return {'FINISHED'}


class AF_OT_Create(bpy.types.Operator):
    bl_idname='archforge.create';bl_label='Generate house'
    def execute(self,context):
        scene=context.scene
        if scene.get('archforge_project_id') and geometry.managed(scene['archforge_project_id']):
            self.report({'WARNING'},'Use a new Blender file for a new project');return {'CANCELLED'}
        params={'prompt':scene.archforge_brief} if scene.archforge_use_brief else {'width':scene.archforge_width,'depth':scene.archforge_depth,'height':scene.archforge_height,'bedrooms':scene.archforge_bedrooms,'roof':scene.archforge_roof,'furnish':scene.archforge_furnish}
        def done(result):
            STATE['projects'].append(result);attach(result['project_id'])
            if result['assumptions']:status(' · '.join(result['assumptions']))
        request('project.create',params,done);return {'FINISHED'}


class AF_OT_Propose(bpy.types.Operator):
    bl_idname='archforge.propose';bl_label='Preview selected-object edit'
    def execute(self,context):
        selected=sorted({o.get('archforge_entity_id') for o in context.selected_objects if o.get('archforge_project_id')==STATE['project']})
        if not STATE['attached']:self.report({'WARNING'},'Connect a project first');return {'CANCELLED'}
        def done(plan):STATE['plan']=plan;status(f"Preview ready: {len(plan['affected_entities'])} affected elements")
        request('plan.propose',{'project_id':STATE['project'],'prompt':context.scene.archforge_edit_prompt,'selected_entity_ids':selected,'expected_revision':context.scene.get('archforge_revision',0)},done)
        return {'FINISHED'}


class AF_OT_Apply(bpy.types.Operator):
    bl_idname='archforge.apply';bl_label='Apply proposal'
    def execute(self,context):
        plan=STATE['plan']
        if plan:
            request('plan.apply',{'plan_id':plan['plan_id'],'operation_id':uuid.uuid4().hex,'expected_revision':plan['base_revision']},lambda r:status('Change '+r['status']))
            STATE['plan']=None
        return {'FINISHED'}


class AF_OT_Discard(bpy.types.Operator):
    bl_idname='archforge.discard';bl_label='Discard proposal'
    def execute(self,context):STATE['plan']=None;status('Proposal discarded');return {'FINISHED'}


class AF_OT_Restore(bpy.types.Operator):
    bl_idname='archforge.restore';bl_label='Preview previous revision'
    def execute(self,context):
        revision=context.scene.get('archforge_revision',1)
        if revision<=1:self.report({'INFO'},'No earlier revision');return {'CANCELLED'}
        def done(plan):STATE['plan']=plan;status(f'Restore revision {revision-1}: review and Apply')
        request('plan.restore',{'project_id':STATE['project'],'revision':revision-1},done);return {'FINISHED'}


class AF_OT_Rebuild(bpy.types.Operator):
    bl_idname='archforge.rebuild';bl_label='Replace manual edits with saved model'
    bl_description='Restore managed geometry from the current saved model; unmanaged objects are preserved'
    def invoke(self,context,event):return context.window_manager.invoke_confirm(self,event)
    def execute(self,context):
        STATE['force']=True;STATE['completed'].clear();attach(context.scene.get('archforge_project_id',''));return {'FINISHED'}


class AF_OT_Cutaway(bpy.types.Operator):
    bl_idname='archforge.cutaway';bl_label='Toggle roof / ceilings'
    def execute(self,context):geometry.apply_cutaway(not context.scene.get('archforge_cutaway',False));return {'FINISHED'}


class AF_OT_Reference(bpy.types.Operator):
    bl_idname='archforge.reference';bl_label='Register plan / design image'
    filepath:StringProperty(subtype='FILE_PATH')
    filter_glob:StringProperty(default='*.png;*.jpg;*.jpeg',options={'HIDDEN'})
    def invoke(self,context,event):context.window_manager.fileselect_add(self);return {'RUNNING_MODAL'}
    def execute(self,context):
        def done(result):STATE['source']=result;status('Image registered. Ask your MCP assistant to inspect it and trace a calibrated layout.')
        request('source.register',{'path':self.filepath},done);return {'FINISHED'}


class AF_OT_Export(bpy.types.Operator):
    bl_idname='archforge.export';bl_label='Export project'
    format:EnumProperty(items=[('png','Render',''),('blend','Blender file',''),('glb','GLB',''),('archforge','Project snapshot',''),('svg','Floor plan','')],default='png')
    def execute(self,context):
        project=context.scene.get('archforge_project_id')
        if not project:self.report({'WARNING'},'Generate or connect a project');return {'CANCELLED'}
        def done(result):STATE['export_job']=result['job_id'];status('Export running in background')
        request('export.start',{'project_id':project,'format':self.format},done);return {'FINISHED'}


class AF_PT_Main(bpy.types.Panel):
    bl_label='ArchForge MCP';bl_idname='AF_PT_Main';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='ArchForge'
    def draw(self,context):
        layout=self.layout;scene=context.scene
        layout.label(text='ArchForge MCP · 0.1.0',icon='HOME')
        for start in range(0,min(len(STATE['status']),200),45):layout.label(text=STATE['status'][start:start+45])
        layout.prop(scene,'archforge_runtime_dir');layout.operator('archforge.refresh',icon='FILE_REFRESH')
        if not scene.get('archforge_project_id'):
            for p in STATE['projects'][:5]:
                op=layout.operator('archforge.attach',text=p.get('name','House')+' · '+p['project_id'][-6:]);op.project_id=p['project_id']
        box=layout.box();box.label(text='New house · one storey')
        box.prop(scene,'archforge_use_brief')
        if scene.archforge_use_brief:box.prop(scene,'archforge_brief')
        else:
            row=box.row();row.prop(scene,'archforge_width');row.prop(scene,'archforge_depth')
            box.prop(scene,'archforge_height');box.prop(scene,'archforge_bedrooms');box.prop(scene,'archforge_roof');box.prop(scene,'archforge_furnish')
        box.operator('archforge.create',icon='OUTLINER_OB_MESH')
        layout.operator('archforge.reference',icon='IMAGE_DATA')
        if STATE['source']:layout.label(text=STATE['source']['source_id'])
        box=layout.box();box.label(text='Edit selection')
        obj=context.active_object
        if obj and obj.get('archforge_entity_id'):box.label(text=obj.name[:45]);box.label(text=obj['archforge_entity_id'])
        box.prop(scene,'archforge_edit_prompt');box.operator('archforge.propose')
        if STATE['plan']:
            box.label(text=f"{len(STATE['plan']['affected_entities'])} affected elements")
            for change in STATE['plan'].get('summary',{}).get('changes',[])[:5]:
                before=str(change['before'])[:18];after=str(change['after'])[:18]
                box.label(text=f"{change['field']}: {before} → {after}")
            row=box.row();row.operator('archforge.apply',icon='CHECKMARK');row.operator('archforge.discard',icon='X')
        layout.operator('archforge.cutaway');layout.operator('archforge.restore',icon='LOOP_BACK')
        if STATE['last_diverged']:
            layout.label(text='Manual changes detected',icon='ERROR')
        if scene.get('archforge_project_id'):layout.operator('archforge.rebuild')
        layout.label(text='Save the .blend to retain model metadata')
        row=layout.row();row.operator('archforge.export',text='Render').format='png';row.operator('archforge.export',text='GLB').format='glb'
        row=layout.row();row.operator('archforge.export',text='Plan SVG').format='svg';row.operator('archforge.export',text='Project snapshot').format='archforge'


CLASSES=(AF_OT_Refresh,AF_OT_Attach,AF_OT_Create,AF_OT_Propose,AF_OT_Apply,AF_OT_Discard,AF_OT_Restore,AF_OT_Rebuild,AF_OT_Cutaway,AF_OT_Reference,AF_OT_Export,AF_PT_Main)
PROPS={
    'archforge_runtime_dir':StringProperty(name='Runtime folder',subtype='DIR_PATH',default=default_root()),
    'archforge_use_brief':BoolProperty(name='Use text brief',default=False),
    'archforge_brief':StringProperty(name='Brief',default='12 x 10 m house with 3 bedrooms and a gable roof'),
    'archforge_width':FloatProperty(name='Width (m)',default=12,min=6,max=40),
    'archforge_depth':FloatProperty(name='Depth (m)',default=10,min=6,max=40),
    'archforge_height':FloatProperty(name='Wall height (m)',default=2.8,min=2.2,max=6),
    'archforge_bedrooms':IntProperty(name='Bedrooms',default=3,min=1,max=8),
    'archforge_roof':EnumProperty(name='Roof',items=[('gable','Gable',''),('flat','Flat',''),('none','None','')],default='gable'),
    'archforge_furnish':BoolProperty(name='Procedural furniture',default=True),
    'archforge_edit_prompt':StringProperty(name='Prompt',default='Make this window 30 cm wider and use a black frame'),
}


@persistent
def loaded(_):
    if STATE['connection']:STATE['connection'].close()
    STATE.update(connection=None,attached=False,task=None,plan=None,completed=set(),epoch=STATE['epoch']+1,last_diverged=[])
    geometry.PENDING=None
    status('File loaded · reconnect ArchForge')
    if not bpy.app.timers.is_registered(timer):bpy.app.timers.register(timer,first_interval=.1)


@persistent
def undone(_):
    STATE['epoch']+=1;STATE['plan']=None
    status('Undo/redo changed scene state · reconnect or restore managed geometry')


def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    for name,prop in PROPS.items():setattr(bpy.types.Scene,name,prop)
    bpy.app.handlers.load_post.append(loaded);bpy.app.handlers.undo_post.append(undone);bpy.app.handlers.redo_post.append(undone)
    bpy.app.timers.register(timer,first_interval=.1)


def unregister():
    if bpy.app.timers.is_registered(timer):bpy.app.timers.unregister(timer)
    for group,handler in ((bpy.app.handlers.load_post,loaded),(bpy.app.handlers.undo_post,undone),(bpy.app.handlers.redo_post,undone)):
        if handler in group:group.remove(handler)
    if STATE['connection']:STATE['connection'].close()
    STATE.update(connection=None,attached=False,task=None,plan=None)
    geometry.discard()
    for name in PROPS:delattr(bpy.types.Scene,name)
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
