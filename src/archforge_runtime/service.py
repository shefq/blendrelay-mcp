"""Durable semantic changes and recoverable live Blender projection."""
import copy
import json
import time
import threading
from pathlib import Path
from archforge_domain.model import DomainError, entities, validate, house, parse_brief, from_rectangles, uid, digest, canonical, room_area
from archforge_domain.operations import propose, prompt_operations, OPERATIONS
from archforge_domain.geometry import build_specs, plan_svg
from .store import Store


from .bridge import Bridge


class Service(Bridge):
    def __init__(self,root,source_roots=()):
        self.store=Store(root);self.lock=threading.RLock();self.sessions={};self.source_roots=[Path(p).resolve() for p in source_roots]
        self.bridge_init()
        # Uncommitted staging cannot be trusted after a runtime crash.
        for op in self.store.pending():
            op['status']='failed' if op['status']=='staging' else 'recovery_required'
            op['message']='Runtime restarted; no revision was committed' if op['status']=='failed' else 'Reconnect Blender to recover the committed revision'
            self.store.save_operation(op)

    def dispatch(self,method,p):
        if not isinstance(p,dict):raise DomainError('INVALID_INPUT','Parameters must be an object')
        with self.lock:
            fn=getattr(self,'rpc_'+method.replace('.','_'),None)
            if fn is None:raise DomainError('UNKNOWN_METHOD',method)
            try:return fn(**p)
            except TypeError as e:raise DomainError('INVALID_ARGUMENTS',str(e))

    def rpc_capabilities(self):
        return {'product':'ArchForge MCP','version':'0.2.1','protocol_version':1,'schema_version':'1.0.0',
            'legacy_template_operations':OPERATIONS,'legacy_asset_families':['table','chair','bed','cabinet','shelf'],
            'general_blender':{'enabled':True,'python_execution':True,'full_scene_versions':True,'architectural_schema_required':False},
            'legacy_template_limits':{'storeys':1,'wall_geometry':'orthogonal','roof_families':['flat','gable','none']},
            'legacy_native_planner':'bounded templates; external MCP hosts can submit structured changes',
            'image_workflow':'inspect images with host vision, then create arbitrary geometry through blender.submit execute; calibrated rectangles are a legacy helper'}

    def rpc_project_create(self,prompt=None,width=12,depth=10,bedrooms=3,height=2.8,roof='gable',furnish=True,name='ArchForge house',rectangles=None):
        model=from_rectangles(rectangles,name=name,height=height,roof=roof,furnish=furnish) if rectangles else parse_brief(prompt) if prompt else house(width,depth,bedrooms,height,roof,furnish,name)
        self.store.create(model)
        return {'project_id':model['project_id'],'revision':1,'name':model['name'],'assumptions':model['assumptions'],'validation':validate(model)}

    def rpc_project_list(self):return self.store.list_projects()

    def rpc_project_adopt(self,model):
        validate(model)
        try:existing=self.store.get(model['project_id'])
        except DomainError as e:
            if e.code!='UNKNOWN_PROJECT':raise
            self.store.create(model)
            return {'project_id':model['project_id'],'revision':model['revision'],'adopted':True}
        if digest(existing)!=digest(model):raise DomainError('REVISION_CONFLICT','Saved Blender snapshot differs from runtime. Use the explicit rebuild button to recover the runtime version; keep a copy of this file first.')
        return {'project_id':existing['project_id'],'revision':existing['revision'],'adopted':False}

    def rpc_project_from_plan(self,source_id,rectangles,pixel_length,real_length_m,name='Traced floor plan',height=2.8,roof='gable',furnish=True):
        from archforge_domain.model import number
        import re
        if not re.fullmatch(r'source-[a-f0-9]{16}',source_id):raise DomainError('UNKNOWN_SOURCE','Invalid source ID')
        source=next((self.store.root/'sources').glob(source_id+'.*'),None)
        if source is None:raise DomainError('UNKNOWN_SOURCE','Register the plan image first')
        factor=number(real_length_m,'known length',.1,100)/number(pixel_length,'calibration pixels',1,100000)
        if not isinstance(rectangles,list) or not rectangles:raise DomainError('INVALID_PLAN','Supply traced rectangular rooms')
        ymax=max(r['y']+r['depth'] for r in rectangles);xmin=min(r['x'] for r in rectangles)
        rooms=[{'name':r['name'],'x':(r['x']-xmin)*factor,'y':(ymax-r['y']-r['depth'])*factor,'width':r['width']*factor,'depth':r['depth']*factor} for r in rectangles]
        model=from_rectangles(rooms,name=name,height=height,roof=roof,furnish=furnish)
        model['sources']=[{'source_id':source_id,'metres_per_pixel':factor,'calibration':{'pixel_length':pixel_length,'real_length_m':real_length_m},'method':'host/user trace','coordinates':'input image y-down, converted to model y-up'}]
        self.store.create(model)
        return {'project_id':model['project_id'],'revision':1,'validation':validate(model),'source':model['sources'][0]}
    def rpc_project_get(self,project_id,revision=None):return self.store.get(project_id,revision)
    def rpc_project_validate(self,project_id):return validate(self.store.get(project_id))
    def rpc_revision_list(self,project_id):return self.store.revisions(project_id)

    def rpc_entity_query(self,project_id,kind=None,query='',offset=0,limit=50):
        model=self.store.get(project_id)
        if isinstance(offset,bool) or not isinstance(offset,int) or offset<0 or not isinstance(limit,int) or not 1<=limit<=200:raise DomainError('INVALID_PAGINATION','Use offset >= 0 and limit 1–200')
        if kind is not None and kind not in ('walls','spaces','openings','windows','doors','assets','materials'):raise DomainError('INVALID_KIND','Unknown entity kind')
        values=model[kind] if kind else list(entities(model).values())
        values=[e for e in values if query.lower() in (e.get('label','')+' '+e['id']).lower()]
        return {'revision':model['revision'],'total':len(values),'offset':offset,'truncated':offset+limit<len(values),'entities':values[offset:offset+limit]}

    def _session(self,project):
        s=self.sessions.get(project)
        if s and time.monotonic()-s['seen']>30:
            self.sessions.pop(project);return None
        return s

    def rpc_session_list(self):
        return [{k:s[k] for k in ('project_id','instance_id','revision','scene_epoch','selected_entity_ids','diverged_ids')} for p in list(self.sessions) if (s:=self._session(p))]

    def rpc_session_attach(self,project_id,instance_id,revision=0,scene_epoch=0,diverged_ids=None):
        model=self.store.get(project_id);old=self._session(project_id)
        if old and old['instance_id']!=instance_id:raise DomainError('PROJECT_LOCKED','Another Blender instance owns this project')
        if diverged_ids:raise DomainError('SCENE_DIVERGED','Reconcile or detach the modified managed geometry before connecting',diverged_ids)
        for op in self.store.pending():
            if op['project_id']==project_id and op['status']=='staging':
                op.update(status='cancelled',message='Uncommitted staging abandoned when Blender reconnected')
                self.store.save_operation(op)
        self.sessions[project_id]={'project_id':project_id,'instance_id':instance_id,'revision':revision,'scene_epoch':scene_epoch,'seen':time.monotonic(),'selected_entity_ids':[],'diverged_ids':[],
            'command':{'id':uid('sync'),'kind':'sync','project_id':project_id,'revision':model['revision'],'model':model,'specs':build_specs(model)}}
        return {'connected':True,'project_id':project_id,'revision':model['revision']}

    def rpc_session_poll(self,project_id,instance_id,revision=0,scene_epoch=0,selected_entity_ids=None,diverged_ids=None):
        s=self._session(project_id)
        if not s or s['instance_id']!=instance_id:raise DomainError('SESSION_EXPIRED','Reconnect the project')
        s.update(seen=time.monotonic(),revision=revision,scene_epoch=scene_epoch,selected_entity_ids=selected_entity_ids or [],diverged_ids=diverged_ids or [])
        return {'command':s.get('command'),'revision':self.store.get(project_id)['revision']}

    def rpc_selection_get(self,project_id):
        s=self._session(project_id)
        if not s:raise DomainError('BLENDER_OFFLINE','Connect the ArchForge panel in Blender')
        return {k:s[k] for k in ('instance_id','project_id','revision','scene_epoch','selected_entity_ids','diverged_ids')}

    def _plan(self,project_id,candidate,affected,summary,intent='',expected_revision=None):
        model=self.store.get(project_id)
        if expected_revision is not None and model['revision']!=expected_revision:raise DomainError('REVISION_CONFLICT','Fetch current project and replan')
        s=self._session(project_id)
        plan={'plan_id':uid('plan'),'project_id':project_id,'base_revision':model['revision'],'state':'ready','candidate':candidate,
              'affected_entities':affected,'summary':summary,'intent':intent,'scene_epoch':s['scene_epoch'] if s else None,'created':time.time()}
        self.store.save_plan(plan)
        return {k:v for k,v in plan.items() if k!='candidate'}

    def rpc_plan_propose(self,project_id,operations=None,prompt=None,selected_entity_ids=None,expected_revision=None):
        model=self.store.get(project_id)
        if operations is None:
            selection=selected_entity_ids if selected_entity_ids is not None else self.rpc_selection_get(project_id)['selected_entity_ids']
            operations=prompt_operations(model,prompt or '',selection)
        candidate,affected,summary=propose(model,operations)
        return self._plan(project_id,candidate,affected,summary,prompt or '',expected_revision)

    def rpc_plan_restore(self,project_id,revision):
        current=self.store.get(project_id);candidate=self.store.get(project_id,revision)
        candidate['revision']=current['revision']
        candidate['protected_ids']=current.get('protected_ids',[])
        affected=sorted(k for k in set(entities(candidate))|set(entities(current)) if entities(candidate).get(k)!=entities(current).get(k))
        old_specs={(s['entity_id'],s['role']):s['hash'] for s in build_specs(current)}
        new_specs={(s['entity_id'],s['role']):s['hash'] for s in build_specs(candidate)}
        affected=sorted(set(affected)|{k[0] for k in set(old_specs)|set(new_specs) if old_specs.get(k)!=new_specs.get(k)})
        blocked=set(affected)&set(current.get('protected_ids',[]))
        if blocked:raise DomainError('PROTECTED_ENTITY','Restore affects protected entities',blocked)
        return self._plan(project_id,candidate,affected,{'restore_revision':revision},f'Restore revision {revision}')

    def rpc_plan_protect(self,project_id,entity_ids,protected=True):
        model=self.store.get(project_id)
        if any(e not in entities(model) and e!='roof' for e in entity_ids):raise DomainError('UNKNOWN_ENTITY','Unknown protected entity')
        candidate=copy.deepcopy(model);ids=set(model.get('protected_ids',[]))
        ids.update(entity_ids) if protected else ids.difference_update(entity_ids)
        candidate['protected_ids']=sorted(ids)
        return self._plan(project_id,candidate,[],{'protected_ids':sorted(ids)},'Update protected elements')

    def rpc_plan_preview(self,plan_id):
        plan=self.store.plan(plan_id);directory=self.store.root/'previews';directory.mkdir(exist_ok=True)
        path=directory/(plan_id+'.svg');path.write_text(plan_svg(plan['candidate']),encoding='utf-8')
        return {'plan':{k:v for k,v in plan.items() if k!='candidate'},'artifact_path':str(path),'svg':path.read_text()}

    def rpc_plan_apply(self,plan_id,operation_id,expected_revision):
        if not isinstance(operation_id,str) or not 1<=len(operation_id)<=128:raise DomainError('INVALID_OPERATION_ID','Supply a persistent operation ID')
        payload_hash=digest({'plan_id':plan_id,'expected_revision':expected_revision})
        old=self.store.operation(operation_id)
        if old:
            if old['payload_hash']!=payload_hash:raise DomainError('IDEMPOTENCY_CONFLICT','Operation ID was used for another payload')
            return old
        plan=self.store.plan(plan_id);project=plan['project_id'];model=self.store.get(project)
        if expected_revision!=model['revision'] or plan['base_revision']!=model['revision']:raise DomainError('REVISION_CONFLICT','Project changed; make another proposal')
        if any(op['project_id']==project for op in self.store.pending()):raise DomainError('PROJECT_BUSY','Finish or cancel the pending operation first')
        s=self._session(project)
        if s and (s['revision']!=model['revision'] or s.get('command')):raise DomainError('PROJECTION_PENDING','Wait for Blender to finish synchronizing')
        if s and s['diverged_ids']:raise DomainError('SCENE_DIVERGED','Resolve manual changes before applying',s['diverged_ids'])
        if s and plan['scene_epoch'] is not None and s['scene_epoch']!=plan['scene_epoch']:raise DomainError('SCENE_DIVERGED','Scene changed since proposal; create a new preview')
        validate(plan['candidate'])
        op={'operation_id':operation_id,'job_id':operation_id,'project_id':project,'plan_id':plan_id,'payload_hash':payload_hash,
            'status':'staging' if s else 'complete','committed_revision':None,'projection_revision':None}
        self.store.save_operation(op)
        if s:
            s['command']={'id':operation_id+':stage','kind':'stage','operation_id':operation_id,'project_id':project,'base_revision':model['revision'],
                          'scene_epoch':s['scene_epoch'],'revision':model['revision']+1,'model':plan['candidate'],'specs':build_specs(plan['candidate'])}
        else:
            self.store.commit(plan['candidate'],op,expected_revision)
            op['message']='Model committed. Attach Blender or export to project the geometry.';self.store.save_operation(op)
        return op

    def rpc_session_ack(self,project_id,instance_id,command_id,success=True,message='',revision=0):
        s=self._session(project_id)
        if not s or s['instance_id']!=instance_id:raise DomainError('SESSION_EXPIRED','Reconnect Blender')
        s['seen']=time.monotonic();cmd=s.get('command')
        if not cmd or cmd['id']!=command_id:return {'acknowledged':True,'duplicate':True}
        op=self.store.operation(cmd['operation_id']) if 'operation_id' in cmd else None
        if not success:
            if op:
                op['status']='failed' if op['committed_revision'] is None else 'recovery_required';op['message']=message;self.store.save_operation(op)
            s['command']=None
            return {'acknowledged':True}
        if cmd['kind']=='stage':
            plan=self.store.plan(op['plan_id']);op['status']='projecting'
            try:self.store.commit(plan['candidate'],op,plan['base_revision'])
            except DomainError:
                op['status']='failed';self.store.save_operation(op);s['command']={'id':uid('discard'),'kind':'discard'};raise
            s['command']={'id':op['operation_id']+':commit','kind':'commit','operation_id':op['operation_id'],'project_id':project_id,'revision':op['committed_revision']}
        else:
            s['command']=None;s['revision']=revision
            if op:
                op['status']='complete';op['projection_revision']=revision;self.store.save_operation(op)
            for pending in self.store.pending():
                if pending['project_id']==project_id and pending.get('committed_revision')==revision:
                    pending.update(status='complete',projection_revision=revision);self.store.save_operation(pending)
        return {'acknowledged':True}

    def rpc_job_get(self,job_id):
        op=self.store.operation(job_id)
        if not op:raise DomainError('UNKNOWN_JOB','Job not found')
        return op

    def rpc_job_cancel(self,job_id):
        op=self.rpc_job_get(job_id)
        if op.get('kind')=='export' and op['status']=='exporting':raise DomainError('NOT_CANCELLABLE','This release does not interrupt a running batch export')
        if op['committed_revision'] is not None:raise DomainError('ALREADY_COMMITTED','Restore a revision to reverse a committed change')
        if op['status'] not in ('staging',):return op
        op['status']='cancelled';self.store.save_operation(op)
        s=self._session(op['project_id'])
        if s:s['command']={'id':uid('discard'),'kind':'discard'}
        return op

    def rpc_source_register(self,path):
        import shutil
        p=Path(path).resolve()
        if not any(p.is_relative_to(r) for r in self.source_roots):raise DomainError('SOURCE_NOT_ALLOWED','Start runtime with --allow-source-root for this image folder')
        if p.suffix.lower() not in ('.png','.jpg','.jpeg') or not p.is_file():raise DomainError('INVALID_SOURCE','Use PNG or JPEG')
        if p.stat().st_size>20*1024*1024:raise DomainError('RESOURCE_LIMIT','Image exceeds 20 MiB')
        source_id=uid('source');dest=self.store.root/'sources'/(source_id+p.suffix.lower());dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(p,dest)
        return {'source_id':source_id,'path':str(dest),'next_step':'Read image through host vision, calibrate a known dimension, then submit rectangular spaces. No automatic image reconstruction is claimed.'}

    def rpc_artifact_read(self,path,offset=0,limit=524288):
        import base64
        p=Path(path).resolve()
        if not any(p.is_relative_to(self.store.root/d) for d in ('sources','previews','exports')) or not p.is_file():raise DomainError('ARTIFACT_NOT_ALLOWED','Only source, preview, and export artifacts can be read')
        if p.stat().st_size>10*1024*1024:raise DomainError('RESOURCE_LIMIT','Artifact too large')
        if not isinstance(offset,int) or not isinstance(limit,int) or offset<0 or not 1<=limit<=524288:raise DomainError('INVALID_RANGE','Invalid artifact byte range')
        mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.svg':'image/svg+xml','.json':'application/json'}.get(p.suffix.lower())
        if not mime:raise DomainError('UNSUPPORTED_ARTIFACT','This artifact is not readable through MCP')
        with p.open('rb') as f:f.seek(offset);chunk=f.read(limit)
        return {'mime_type':mime,'data':base64.b64encode(chunk).decode(),'offset':offset,'eof':offset+len(chunk)>=p.stat().st_size,'total_bytes':p.stat().st_size}

    def rpc_export_start(self,project_id,format='blend'):
        from .export import export_project
        if format not in ('blend','png','glb','archforge','json','svg'):raise DomainError('UNSUPPORTED_FORMAT',format)
        model=self.store.get(project_id);job_id=uid('export');path=self.store.root/'exports'/(job_id+'.'+format)
        op={'operation_id':job_id,'job_id':job_id,'project_id':project_id,'payload_hash':digest({'model':model,'format':format}),
            'status':'exporting','kind':'export','source_revision':model['revision'],'committed_revision':None,'cancellable':False}
        self.store.save_operation(op)
        def work():
            try:
                result=export_project(model,path,format)
                op.update(status='complete',result=result)
            except Exception as e:op.update(status='failed',message=str(e))
            self.store.save_operation(op)
        threading.Thread(target=work,daemon=True,name='archforge-export').start()
        return op
