# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene policy and a nonblocking Asset Library panel."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import textwrap
import time
import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty, EnumProperty, CollectionProperty
from .asset_rules import AssetPolicy
from .client import Connection
from . import workflow

FIELDS=('poly_haven','poly_pizza','pizza_cc0','pizza_cc_by','max_download_mb')
STATE={'connection':None,'job':None,'scene':None,'status':'Ready','last':0,'thumbnail_images':set()}


def scene_key(scene):
    return f'{scene.as_pointer()}:{scene.name}:{bpy.data.filepath}'


def policy(scene):
    return AssetPolicy.read({key:getattr(scene,'blendrelay_asset_'+key,getattr(AssetPolicy(),key)) for key in FIELDS})


def poll_fields(scene):
    try:
        from . import bridge_ui
        run_id=bridge_ui.STATE.get('active_agent_run_id')
    except Exception:
        run_id=None
    mode=getattr(scene,'blendrelay_resource_mode','FULL_BUILD')
    quality=getattr(scene,'blendrelay_output_quality','HIGH')
    limits=workflow.profile(mode,quality)
    autonomous=getattr(scene,'blendrelay_permission_mode','AUTONOMOUS')=='AUTONOMOUS'
    from .general import workspace_id
    return dict(asset_policy=asdict(policy(scene)),asset_scene=scene_key(scene),workspace_id=workspace_id(scene, getattr(scene, 'blendrelay_runtime_dir', None)),
                allow_python_execution=autonomous or bool(getattr(scene,'blendrelay_allow_python_execution',True)),
                agent_run_id=run_id,
                mcp_call_limit=limits['initial_calls'],edit_attempt_limit=limits['initial_edits'],
                job_poll_limit=limits['polls_per_job'],max_mcp_calls=limits['max_calls'],
                max_edit_attempts=limits['max_edits'],auto_extend=limits['auto_extend'])


def redraw():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D': area.tag_redraw()


def clear_thumbnails():
    for name in STATE['thumbnail_images']:
        image=bpy.data.images.get(name)
        if image and image.users==0: bpy.data.images.remove(image)
    STATE['thumbnail_images'].clear()


def response(message):
    if 'error' in message:
        STATE.update(status=message['error']['message'],job=None);redraw();return
    result=message['result']
    if result.get('status') in ('running','queued'):
        STATE['job']=result['job_id'];STATE['status']='Working…';return
    STATE['job']=None
    if result.get('status')=='failed': STATE['status']=result.get('error','Asset operation failed')
    else:
        data=result.get('result',result)
        if isinstance(data,dict) and 'results' in data:
            scene=STATE['scene']
            if scene and scene.name in bpy.data.scenes and scene_key(scene)==STATE.get('scene_key'):
                clear_thumbnails()
                scene.blendrelay_asset_results.clear()
                for record in data['results'][:50]:
                    path=Path(record.get('thumbnail_path',''))
                    if path.is_file():
                        try:
                            image=bpy.data.images.load(str(path),check_existing=True)
                            STATE['thumbnail_images'].add(image.name)
                            record['thumbnail_image']=image.name
                        except RuntimeError: pass
                    row=scene.blendrelay_asset_results.add();row.name=record['name'];row.record=json.dumps(record)
                scene.blendrelay_asset_index=0
            STATE['status']=' · '.join(data.get('messages',[])) or f"{len(data['results'])} assets found"
        else: STATE['status']='Asset imported' if data else 'Completed'
    redraw()


def timer():
    connection=STATE['connection']
    if connection:
        try:
            connection.tick()
            if STATE['job'] and not connection.pending and time.monotonic()-STATE['last']>.5:
                STATE['last']=time.monotonic()
                connection.request('assets.job',{'job_id':STATE['job']},response)
        except Exception as error:
            STATE.update(status=str(error),job=None);connection.close();STATE['connection']=None;redraw()
    return .1


class BR_AssetResult(bpy.types.PropertyGroup):
    record: StringProperty()


class BR_UL_AssetResults(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        record=json.loads(item.record)
        row=layout.row(align=True)
        row.label(text=item.name,icon='ASSET_MANAGER')
        row.label(text=record.get('licence',''))
        op=row.operator('blendrelay.asset_action',text='',icon='IMPORT');op.action='IMPORT';op.asset_id=record['asset_id']


class BR_OT_AssetAction(bpy.types.Operator):
    bl_idname='blendrelay.asset_action'
    bl_label='Asset Library'
    action: StringProperty(default='SEARCH')
    asset_id: StringProperty()

    def execute(self,context):
        from . import bridge_ui
        scene=context.scene
        try:
            rows=scene.blendrelay_asset_results
            record=json.loads(rows[scene.blendrelay_asset_index].record) if rows and scene.blendrelay_asset_index<len(rows) else {}
            if self.action=='OPEN':
                folder=Path(record.get('cache_directory',''))
                root=Path(scene.blendrelay_runtime_dir)/'assets'
                if not record.get('cached') or not folder.is_dir() or not folder.resolve().is_relative_to(root.resolve()): raise ValueError('Import this asset first to create its cache folder; then refresh local cache')
                bpy.ops.wm.path_open(filepath=str(folder));return {'FINISHED'}
            if STATE['job'] or (STATE['connection'] and STATE['connection'].pending): raise ValueError('Wait for the current asset operation')
            bridge_ui.ensure_connected(context)
            if STATE['connection']: STATE['connection'].close()
            connection=Connection(scene.blendrelay_runtime_dir)
            STATE.update(connection=connection,scene=scene,scene_key=scene_key(scene),status='Starting…')
            params={'instance_id':bridge_ui.STATE['instance']}
            if self.action=='SEARCH':
                method='assets.search';params.update(query=scene.blendrelay_asset_query,provider=None if scene.blendrelay_asset_provider=='all' else scene.blendrelay_asset_provider,max_results=20)
            elif self.action=='REFRESH': method='assets.refresh'
            else:
                method='assets.import';params.update(asset_id=self.asset_id or record.get('asset_id'),location=list(scene.cursor.location))
                if not params['asset_id']: raise ValueError('Select an asset first')
            # Publish the current scene policy before starting this request.
            # Queue polling through the normal bridge, whose callback runs jobs.
            def synced(message):
                bridge_ui.received(message)
                if 'error' in message: response(message)
                else: connection.request(method,params,response)
            connection.request('blender.poll',dict(instance_id=bridge_ui.STATE['instance'],scene_name=scene.name,filepath=bpy.data.filepath,**poll_fields(scene)),synced)
            redraw()
            return {'FINISHED'}
        except Exception as error:
            STATE['status']=str(error);self.report({'WARNING'},str(error));return {'CANCELLED'}


class BR_PT_AssetLibrary(bpy.types.Panel):
    bl_label='Asset Library'
    bl_idname='BR_PT_AssetLibrary'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='BlendRelay'
    bl_options={'DEFAULT_CLOSED'}

    def draw(self,context):
        scene=context.scene;layout=self.layout
        box=layout.box();box.label(text='Asset Provider Policy')
        box.label(text='Local Asset Cache · always available',icon='CHECKMARK')
        box.prop(scene,'blendrelay_asset_poly_haven',text='Poly Haven')
        box.label(text='Public catalogue · CC0')
        box.prop(scene,'blendrelay_asset_poly_pizza',text='Poly Pizza')
        row=box.row(align=True)
        row.prop(scene,'blendrelay_asset_pizza_cc0',text='CC0');row.prop(scene,'blendrelay_asset_pizza_cc_by',text='CC-BY')
        box.prop(scene,'blendrelay_asset_max_download_mb',text='Maximum download (MB)')
        layout.prop(scene,'blendrelay_asset_query',text='Search')
        layout.prop(scene,'blendrelay_asset_provider',text='Provider')
        row=layout.row(align=True);row.enabled=not STATE['job']
        row.operator('blendrelay.asset_action',text='Search',icon='VIEWZOOM').action='SEARCH'
        row.operator('blendrelay.asset_action',text='Refresh local cache',icon='FILE_REFRESH').action='REFRESH'
        layout.template_list('BR_UL_AssetResults','',scene,'blendrelay_asset_results',scene,'blendrelay_asset_index',rows=4)
        rows=scene.blendrelay_asset_results
        if rows and scene.blendrelay_asset_index<len(rows):
            record=json.loads(rows[scene.blendrelay_asset_index].record)
            detail=layout.box()
            thumbnail=bpy.data.images.get(record.get('thumbnail_image',''))
            if thumbnail:
                thumbnail.preview_ensure()
                icon_id=thumbnail.preview.icon_id
                if icon_id: detail.template_icon(icon_value=icon_id,scale=4.0)
                else: detail.label(text='Thumbnail available',icon='IMAGE')
            for text in (record['name'],record['provider'].replace('_',' ').title()+' · '+record['licence'], 'Creator: '+record.get('creator','Unknown'), 'Format: '+record.get('format','unknown'),'Size: '+(f"{record['size_bytes']/1048576:.1f} MB" if record.get('size_bytes') is not None else 'checked during download')):
                detail.label(text=text)
        row=layout.row();row.enabled=not STATE['job']
        row.operator('blendrelay.asset_action',text='Import selected asset',icon='IMPORT').action='IMPORT'
        layout.operator('blendrelay.asset_action',text='Open cached asset location',icon='FILE_FOLDER').action='OPEN'
        for line in textwrap.wrap(STATE['status'],55): layout.label(text=line)
        layout.label(text='Powered by Poly Haven')


CLASSES=(BR_AssetResult,BR_UL_AssetResults,BR_OT_AssetAction,BR_PT_AssetLibrary)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    for key in FIELDS:
        value=getattr(AssetPolicy(),key)
        prop=IntProperty(default=value,min=1,max=2048) if key=='max_download_mb' else BoolProperty(default=value)
        setattr(bpy.types.Scene,'blendrelay_asset_'+key,prop)
    bpy.types.Scene.blendrelay_asset_query=StringProperty(name='Search',maxlen=200)
    bpy.types.Scene.blendrelay_asset_provider=EnumProperty(items=[('all','Enabled providers','Search cache then permitted providers'),('local','Local Cache','Search downloaded assets'),('poly_haven','Poly Haven','Public CC0 catalogue'),('poly_pizza','Poly Pizza','Permitted public models')],default='all')
    bpy.types.Scene.blendrelay_asset_results=CollectionProperty(type=BR_AssetResult,options={'SKIP_SAVE'})
    bpy.types.Scene.blendrelay_asset_index=IntProperty(default=0,min=0,options={'SKIP_SAVE'})
    bpy.app.timers.register(timer,persistent=True)


def unregister():
    if bpy.app.timers.is_registered(timer): bpy.app.timers.unregister(timer)
    if STATE['connection']: STATE['connection'].close()
    clear_thumbnails()
    STATE.update(connection=None,job=None,scene=None)
    for key in FIELDS+('query','provider','results','index'):
        if hasattr(bpy.types.Scene,'blendrelay_asset_'+key): delattr(bpy.types.Scene,'blendrelay_asset_'+key)
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)

# Compatibility aliases
AF_AssetResult = BR_AssetResult
AF_UL_AssetResults = BR_UL_AssetResults
AF_OT_AssetAction = BR_OT_AssetAction
AF_PT_AssetLibrary = BR_PT_AssetLibrary
