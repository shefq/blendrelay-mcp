"""MCP stdio gateway for general Blender work with strict tool contracts."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from archforge_domain.model import DomainError
from archforge_runtime.protocol import Client, data_dir
from .job_results import wait_and_compact
from .validation import SchemaError, validate

from archforge_blender.version import VERSION

LATEST_PROTOCOL='2026-07-28'
LEGACY_PROTOCOLS=('2024-11-05','2025-03-26','2025-06-18','2025-11-25')
SERVER_INFO={'name':'archforge-mcp','version':VERSION}
INSTRUCTIONS=('Use ArchForge tools to inspect and edit the current Blender scene. For full builds, '
    'build_scene_batch and execute_blender_python are primary creation tools; use mesh_edit for '
    'selected topology and capture_viewport for visual refinement. Preserve unrelated content, '
    'use stable operation IDs, and never poll terminal work.')

def obj(properties=None,required=(),additional=False):
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object',
            'properties':properties or {},'required':list(required),'additionalProperties':additional}

STR={'type':'string'};SHORT={'type':'string','maxLength':200};ID={'type':'string','minLength':1,'maxLength':100}
NUM={'type':'number'};BOOL={'type':'boolean'}
VEC3={'type':'array','items':NUM,'minItems':3,'maxItems':3}
RGBA={'type':'array','items':NUM,'minItems':3,'maxItems':4}
CONTROL={'operation_id':ID,'instance_id':ID,'wait_seconds':{'type':'number','minimum':0,'maximum':30},'repeat_result':BOOL}
JOB_OUTPUT=obj({'operation_id':ID,'instance_id':ID,'action':STR,
    'status':{'enum':['queued','running','complete','failed','interrupted','cancelled']},
    'error':{},'result':{},'unchanged':BOOL,'next_step':STR},('operation_id','status'))
SESSION_OUTPUT={'type':'array','items':obj({'instance_id':ID,'scene_name':STR,'filepath':STR,
    'selection':{'type':'array','items':STR},'version':STR,'asset_policy':{},'asset_scene':{},
    'allow_python_execution':BOOL,'agent_run_id':{},'mcp_call_limit':{'type':'integer'},
    'edit_attempt_limit':{'type':'integer'},'job_poll_limit':{'type':'integer'},
    'max_mcp_calls':{'type':'integer'},'max_edit_attempts':{'type':'integer'},'auto_extend':BOOL,
    'mcp_calls':{'type':'integer'},'edit_attempts':{'type':'integer'},'job_polls':{'type':'integer'},
    'poll_counts':{'type':'object'}},('instance_id',))}
ASSET_JOB_OUTPUT=obj({'job_id':ID,'status':{'enum':['running','complete','failed','queued','interrupted','cancelled']},
    'created_at':NUM,'instance_id':ID,'result':{},'error':{}},('job_id','status'))
POLICY_OUTPUT=obj({'poly_haven':BOOL,'poly_pizza':BOOL,'pizza_cc0':BOOL,'pizza_cc_by':BOOL,
    'max_download_mb':{'type':'integer'},'local_cache':BOOL,'instance_id':ID},
    ('poly_haven','poly_pizza','pizza_cc0','pizza_cc_by','max_download_mb','local_cache','instance_id'))
CAPABILITIES_OUTPUT=obj({'product':STR,'version':STR,'protocol_version':{'type':'integer'},'mcp_protocol':STR,
    'schema_version':STR,'legacy_template_operations':{'type':'object'},'legacy_asset_families':{'type':'array'},
    'general_blender':{'type':'object'},'legacy_template_limits':{'type':'object'},
    'legacy_native_planner':STR,'image_workflow':STR},('product','version','mcp_protocol','general_blender'))
SOURCE_OUTPUT=obj({'source_id':ID,'path':STR,'next_step':STR},('source_id','path','next_step'))
ARTIFACT_OUTPUT=obj({'mime_type':STR,'offset':{'type':'integer'},'eof':BOOL,
    'total_bytes':{'type':'integer'}},('mime_type','offset','eof','total_bytes'))
ANY_OBJECT={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','additionalProperties':False}

TOOLS={}
def tool(name,title,method,description,properties=None,required=(),*,action=None,read=False,
         destructive=False,output=ANY_OBJECT,task=False):
    record={'name':name,'title':title,'description':description,'inputSchema':obj(properties,required),
            'outputSchema':output,'annotations':{'readOnlyHint':read,'destructiveHint':destructive,
            'idempotentHint':read,'openWorldHint':False},
            'execution':{'taskSupport':'optional' if task else 'forbidden'},'_method':method}
    if action is not None:record['_action']=action
    TOOLS[name]=record

tool('archforge_blender_sessions','Connected Blender Sessions','blender.sessions','List live Blender connections.',read=True,output=SESSION_OUTPUT)
tool('inspect_scene','Inspect Blender Scene','blender.submit','Read compact scene or named-object information without changing Blender.',
     {**CONTROL,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':200},
      'object_name':SHORT,'names':{'type':'array','items':SHORT,'maxItems':50}},action='inspect',read=True,output=JOB_OUTPUT,task=True)
tool('get_scene_info','Get Scene Summary','blender.submit','Read a compact summary of the active scene and selection.',CONTROL,action='get_scene_info',read=True,output=JOB_OUTPUT,task=True)
tool('get_object_info','Get Blender Object','blender.submit','Read transforms, bounds, mesh counts, materials and modifiers for one named object.',
     {**CONTROL,'name':SHORT},('name',),action='get_object_info',read=True,output=JOB_OUTPUT,task=True)
tool('mesh_edit','Edit Selected Mesh','blender.submit','Run one validated operation on selected mesh elements in Blender Edit Mode.',
     {**CONTROL,'operation':{'enum':['bevel','extrude','inset','bridge','move_normal','assign_material']},
      'distance':{'type':'number','minimum':-10000,'maximum':10000},'segments':{'type':'integer','minimum':1,'maximum':64},'material':SHORT},
     ('operation',),action='mesh_edit',destructive=True,output=JOB_OUTPUT,task=True)
tool('validate_mesh_selection','Validate Selected Mesh','blender.submit','Check selected or named meshes for common geometry defects.',
     {**CONTROL,'names':{'type':'array','items':SHORT,'maxItems':50}},action='validate_selection',read=True,output=JOB_OUTPUT,task=True)
tool('capture_viewport','Capture Blender Viewport','blender.submit','Capture a bounded viewport image for visual verification.',
     {**CONTROL,'max_dimension':{'type':'integer','minimum':128,'maximum':2048}},action='screenshot',read=True,output=JOB_OUTPUT,task=True)
tool('capture_focused_view','Capture Focused View','blender.submit',
     'Capture one or more dependable framed camera views of target objects with evaluated bounds, occlusion diagnostics, and optional matrix replay.',
     {**CONTROL,
      'target_objects':{'type':'array','items':SHORT,'minItems':1,'maxItems':50},
      'camera_position_hints':{'type':'array','items':VEC3,'maxItems':4},
      'context_mode':{'enum':['full_scene','targets_and_nearby','targets_only']},
      'padding':{'type':'number','minimum':0,'maximum':0.8},
      'max_dimension':{'type':'integer','minimum':128,'maximum':2048,'description':'Render max dimension (capped at 512 for cycles)'},
      'render_mode':{'enum':['auto','workbench','eevee','cycles']},
      'camera_matrix_world':{'type':'array','items':NUM,'minItems':16,'maxItems':16},
      'lens_mm':{'type':'number','minimum':5,'maximum':500},
      'cycles_samples':{'type':'integer','minimum':1,'maximum':512}},
     ('target_objects',),action='capture_focused_view',read=True,output=JOB_OUTPUT,task=True)
tool('execute_blender_python','Execute Blender Python','blender.submit','Execute arbitrary Python inside Blender. The namespace includes bpy, mathutils, and af helpers for collection, material, assign, cube, cylinder, area_light, camera, look_at, world, and smooth. Requires scene authorization.',
     {**CONTROL,'code':{'type':'string','minLength':1,'maxLength':524288},'label':{'type':'string','maxLength':120},'save_checkpoint':BOOL},
     ('code',),action='execute',destructive=True,output=JOB_OUTPUT,task=True)
tool('build_scene_batch','Build Scene Batch','blender.submit','Create common objects, materials, lights, cameras and world settings in one strict batch. Use Python for custom geometry.',
     {**CONTROL,'label':SHORT,'operations':{'type':'array','minItems':1,'maxItems':500,'items':obj({
       'type':{'enum':['collection','material','cube','cylinder','area_light','camera','world','assign_material']},
       'name':SHORT,'collection':SHORT,'location':VEC3,'dimensions':VEC3,'target':VEC3,'rotation':VEC3,
       'color':RGBA,'material':SHORT,'object':SHORT,'metallic':{'type':'number','minimum':0,'maximum':1},
       'roughness':{'type':'number','minimum':0,'maximum':1},'bevel':{'type':'number','minimum':0,'maximum':1000},
       'radius':{'type':'number','minimum':0.000001,'maximum':100000},'depth':{'type':'number','minimum':0.000001,'maximum':100000},
       'vertices':{'type':'integer','minimum':3,'maximum':512},'energy':{'type':'number','minimum':0,'maximum':1000000000},
       'size':{'type':'number','minimum':0.000001,'maximum':100000},'lens':{'type':'number','minimum':1,'maximum':500},
       'strength':{'type':'number','minimum':0,'maximum':100000}},('type',))}},('operations',),
     action='build_batch',destructive=True,output=JOB_OUTPUT,task=True)
tool('list_scene_versions','List Scene Versions','blender.submit','List ArchForge full-scene checkpoints.',CONTROL,action='versions',read=True,output=JOB_OUTPUT,task=True)
tool('create_scene_checkpoint','Create Scene Checkpoint','blender.submit','Save an explicit full-scene checkpoint.',
     {**CONTROL,'label':{'type':'string','minLength':1,'maxLength':120}},('label',),action='checkpoint',output=JOB_OUTPUT,task=True)
tool('restore_scene_version','Restore Scene Version','blender.submit','Replace the current scene with a selected checkpoint.',
     {**CONTROL,'version_id':ID},('version_id',),action='restore',destructive=True,output=JOB_OUTPUT,task=True)
tool('archforge_blender_job','Read Blender Job','blender.job','Read a queued or running Blender job. Do not poll terminal jobs.',
     {'operation_id':ID,'instance_id':ID,'wait_seconds':CONTROL['wait_seconds'],'repeat_result':BOOL},('operation_id',),read=True,output=JOB_OUTPUT)

tool('get_asset_policy','Get Asset Policy','assets.policy','Read active provider, licence and download policy.',{'instance_id':ID},read=True,output=POLICY_OUTPUT)
tool('search_assets','Search Permitted Assets','assets.search','Search enabled asset sources according to active scene policy.',
     {'query':{'type':'string','minLength':1,'maxLength':200},'provider':{'enum':['local','poly_haven','poly_pizza']},
      'style':{'type':'string','maxLength':100},'max_results':{'type':'integer','minimum':1,'maximum':50},'instance_id':ID},('query',),read=True,output=ASSET_JOB_OUTPUT,task=True)
tool('import_asset','Import Asset','assets.import','Download, verify and import a permitted asset into a dedicated collection.',
     {'asset_id':ID,'collection_name':{'type':'string','maxLength':100},'location':VEC3,
      'scale':{'type':'number','minimum':0.000001,'maximum':10000},'instance_id':ID},('asset_id',),destructive=True,output=ASSET_JOB_OUTPUT,task=True)
tool('list_cached_assets','List Cached Assets','assets.cached','List permitted assets already stored locally.',
     {'query':{'type':'string','maxLength':200},'instance_id':ID},read=True,output=ASSET_JOB_OUTPUT,task=True)
tool('refresh_asset_cache','Refresh Asset Cache','assets.refresh','Rescan cached asset metadata.',{'instance_id':ID},read=True,output=ASSET_JOB_OUTPUT,task=True)
tool('asset_job','Read Asset Job','assets.job','Read an asynchronous asset job; respect its poll interval.',{'job_id':ID},('job_id',),read=True,output=ASSET_JOB_OUTPUT)
tool('archforge_get_capabilities','ArchForge Capabilities','capabilities','Read runtime features and compatibility information.',read=True,output=CAPABILITIES_OUTPUT)
tool('archforge_register_source','Register Reference Image','source.register','Copy a PNG or JPEG from an explicitly allowed source root into the artifact store.',
     {'path':{'type':'string','minLength':1,'maxLength':32768}},('path',),destructive=True,output=SOURCE_OUTPUT)
tool('archforge_read_artifact','Read ArchForge Artifact','artifact.read','Read a generated preview or registered image from the artifact store.',
     {'path':{'type':'string','minLength':1,'maxLength':32768}},('path',),read=True,output=ARTIFACT_OUTPUT)

PUBLIC_TOOLS=tuple(TOOLS);ASYNC_ASSET={'search_assets','import_asset','list_cached_assets','refresh_asset_cache'}
BUDGET_TOOLS=set(PUBLIC_TOOLS)-{'archforge_get_capabilities','archforge_register_source','archforge_read_artifact'}
TASKS={};TASK_LOCK=threading.RLock();MAX_TASKS=8;MAX_TASK_TTL=3600000;MAX_TASK_POLLS=120

def ensure_runtime(root):
    client=Client(root,timeout=1)
    try:client.call('capabilities');return
    except (DomainError,OSError):pass
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with open(root/'runtime.log','ab') as log:
        subprocess.Popen([sys.executable,'-m','archforge_runtime.cli','--data-dir',str(root),'serve','--allow-source-root',str(Path.cwd())],
                         stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(60):
        try:client.call('capabilities');return
        except (DomainError,OSError):time.sleep(.1)
    raise DomainError('RUNTIME_OFFLINE','Runtime could not start; see runtime.log')

def public_tool(record):return {k:v for k,v in record.items() if not k.startswith('_')}

def invoke_raw(definition,args,client,task_mode=False):
    if '_action' in definition:
        control={k:args[k] for k in CONTROL if k in args};action_args={k:v for k,v in args.items() if k not in CONTROL}
        control.update(action=definition['_action'],arguments=action_args)
        if task_mode:control['wait_seconds']=0
        return wait_and_compact(client,'blender.submit',control)
    if definition['_method']=='blender.job':return wait_and_compact(client,'blender.job',args)
    return client.call(definition['_method'],args)

def wait_async(name,raw,client,cancel,deadline):
    polls=0
    while isinstance(raw,dict) and raw.get('status') in ('queued','running'):
        if cancel.is_set():raise DomainError('TASK_CANCELLED','Task cancelled by the user')
        if time.monotonic()>=deadline:raise DomainError('TASK_EXPIRED','Task exceeded its lifetime')
        if polls>=MAX_TASK_POLLS:raise DomainError('POLL_LIMIT','Task exceeded the polling limit')
        time.sleep(.5);polls+=1
        if name in ASYNC_ASSET:raw=client.call('assets.job',{'job_id':raw['job_id']})
        elif raw.get('operation_id'):raw=client.call('blender.job',{'operation_id':raw['operation_id'],'instance_id':raw.get('instance_id')})
        else:break
    return raw

def tool_result(name,raw):
    structured=raw
    if isinstance(raw,dict) and isinstance(raw.get('result'),dict) and raw['result'].get('images'):
        images=raw['result']['images']
        content=[{'type':'image','data':im['data'],'mimeType':im.get('mime_type','image/jpeg')}
                 for im in images if isinstance(im,dict) and im.get('data')]
        structured=dict(raw);structured['result']={k:v for k,v in raw['result'].items() if k!='images'}
        content.append({'type':'text','text':json.dumps(structured,ensure_ascii=False)})
    elif isinstance(raw,dict) and isinstance(raw.get('result'),dict) and raw['result'].get('image'):
        image=raw['result']['image'];mime=raw['result'].get('mime_type','image/png')
        structured=dict(raw);structured['result']={k:v for k,v in raw['result'].items() if k!='image'}
        content=[{'type':'image','data':image,'mimeType':mime},{'type':'text','text':json.dumps(structured,ensure_ascii=False)}]
    elif name=='archforge_read_artifact':
        data=base64.b64decode(raw['data']);mime=raw['mime_type'];structured={k:v for k,v in raw.items() if k!='data'}
        content=[{'type':'image','data':base64.b64encode(data).decode(),'mimeType':mime}] if mime.startswith('image/') else [{'type':'text','text':data.decode('utf-8')}]
    else:content=[{'type':'text','text':json.dumps(raw,ensure_ascii=False)}]
    failed=isinstance(raw,dict) and raw.get('status') in ('failed','interrupted','cancelled')
    validate(structured,TOOLS[name]['outputSchema'],path='structuredContent')
    return {'resultType':'complete','content':content,'structuredContent':structured,'isError':failed}

def call_tool(name,args,client,task_mode=False,cancel=None,deadline=None):
    definition=TOOLS.get(name)
    if name not in PUBLIC_TOOLS or definition is None:raise DomainError('UNKNOWN_TOOL',str(name))
    validate(args,definition['inputSchema'])
    if name in BUDGET_TOOLS:
        kind=('poll' if name in ('archforge_blender_job','asset_job') else
              'asset' if name=='import_asset' else
              'edit' if definition['annotations']['destructiveHint'] else 'read')
        client.call('budget.claim',{'kind':kind,'instance_id':args.get('instance_id'),
                    'operation_id':args.get('operation_id') or args.get('job_id')})
    raw=invoke_raw(definition,args,client,task_mode)
    if name in BUDGET_TOOLS and kind in ('edit','asset'):
        accepted = (isinstance(raw,dict) and (raw.get('operation_id') or raw.get('job_id'))
                    and raw.get('status') not in ('failed','interrupted','cancelled'))
        if accepted:
            client.call('budget.commit',{'kind':kind,'instance_id':args.get('instance_id') or raw.get('instance_id')})
    if name=='archforge_read_artifact' and isinstance(raw,dict):
        chunks=[base64.b64decode(raw['data'])]
        while not raw.get('eof'):
            raw=client.call('artifact.read',{'path':args['path'],'offset':sum(len(c) for c in chunks)})
            chunks.append(base64.b64decode(raw['data']))
        raw=dict(raw,data=base64.b64encode(b''.join(chunks)).decode())
    if task_mode:raw=wait_async(name,raw,client,cancel,deadline)
    return tool_result(name,raw)

def now():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def cleanup_tasks():
    current=time.time()*1000
    for key in list(TASKS):
        if current-TASKS[key]['created_ms']>TASKS[key]['ttl']:TASKS.pop(key,None)
def task_view(task):return {k:task[k] for k in ('taskId','status','statusMessage','createdAt','lastUpdatedAt','ttl','pollInterval')}

def create_task(name,args,request,client):
    definition=TOOLS[name]
    if definition['execution']['taskSupport']!='optional':raise DomainError('TASK_NOT_SUPPORTED',name)
    ttl=request.get('ttl',600000)
    if isinstance(ttl,bool) or not isinstance(ttl,int) or not 10000<=ttl<=MAX_TASK_TTL:raise DomainError('INVALID_TASK_TTL',f'ttl must be 10000–{MAX_TASK_TTL} ms')
    with TASK_LOCK:
        cleanup_tasks()
        if sum(t['status']=='working' for t in TASKS.values())>=MAX_TASKS:raise DomainError('TASK_LIMIT','Too many ArchForge tasks are running')
        task_id=uuid.uuid4().hex;stamp=now();cancel=threading.Event();done=threading.Event()
        task={'taskId':task_id,'status':'working','statusMessage':'ArchForge operation is running.','createdAt':stamp,
              'lastUpdatedAt':stamp,'ttl':ttl,'pollInterval':1000,'created_ms':time.time()*1000,
              'cancel':cancel,'done':done,'result':None}
        TASKS[task_id]=task
    def worker():
        try:
            result=call_tool(name,args,client,True,cancel,time.monotonic()+ttl/1000)
            status='cancelled' if cancel.is_set() else 'failed' if result.get('isError') else 'completed';message='ArchForge operation finished.'
        except Exception as error:
            result={'resultType':'complete','content':[{'type':'text','text':str(error)}],'isError':True}
            status='cancelled' if cancel.is_set() else 'failed';message=str(error)[:300]
        with TASK_LOCK:
            if task_id in TASKS and TASKS[task_id]['status']=='working':TASKS[task_id].update(status=status,statusMessage=message,lastUpdatedAt=now(),result=result)
        done.set()
    threading.Thread(target=worker,name='ArchForge-MCP-task',daemon=True).start()
    return {'task':task_view(task),'_meta':{'io.modelcontextprotocol/model-immediate-response':'ArchForge is working in Blender.'}}

def capabilities(protocol=LATEST_PROTOCOL):
    result={'tools':{'listChanged':False}}
    if protocol in LEGACY_PROTOCOLS:result['tasks']={'list':{},'cancel':{},'requests':{'tools':{'call':{}}}}
    else:result['extensions']={'io.modelcontextprotocol/tasks':{'version':'2026-07-28'}}
    return result
def discover():return {'protocolVersion':LATEST_PROTOCOL,'serverInfo':SERVER_INFO,'capabilities':capabilities(),'instructions':INSTRUCTIONS}

def handle(message,client):
    method=message.get('method');params=message.get('params') or {}
    if not isinstance(params,dict):raise DomainError('INVALID_ARGUMENTS','params must be an object')
    if method=='initialize':
        requested=params.get('protocolVersion','2025-11-25');version=requested if requested in LEGACY_PROTOCOLS else '2025-11-25'
        return {'protocolVersion':version,'capabilities':capabilities(version),'serverInfo':SERVER_INFO,'instructions':INSTRUCTIONS}
    if method=='server/discover':return discover()
    if method=='ping':return {}
    if method=='tools/list':return {'resultType':'complete','tools':[public_tool(TOOLS[n]) for n in PUBLIC_TOOLS],'ttlMs':300000,'cacheScope':'public'}
    if method=='tools/call':
        name=params.get('name');args=params.get('arguments',{})
        if name not in PUBLIC_TOOLS:raise DomainError('UNKNOWN_TOOL',str(name))
        request=params.get('task')
        if request is not None:
            if not isinstance(request,dict):raise DomainError('INVALID_ARGUMENTS','task must be an object')
            return create_task(name,args,request,client)
        try:return call_tool(name,args,client)
        except (DomainError,OSError,ValueError,SchemaError) as error:
            detail=error.as_dict() if isinstance(error,DomainError) else {'code':'INVALID_ARGUMENTS','message':str(error)}
            return {'resultType':'complete','content':[{'type':'text','text':json.dumps(detail)}],'structuredContent':detail,'isError':True}
    if method in ('tasks/get','tasks/result','tasks/cancel'):
        task_id=params.get('taskId')
        with TASK_LOCK:cleanup_tasks();task=TASKS.get(task_id)
        if not task:raise DomainError('UNKNOWN_TASK',str(task_id))
        if method=='tasks/cancel':
            task['cancel'].set()
            if task['status']=='working':task.update(status='cancelled',statusMessage='Cancelled by user.',lastUpdatedAt=now())
            task['done'].set()
            return task_view(task)
        if method=='tasks/get':return task_view(task)
        if task['status']=='working':
            remaining=max(0,(task['created_ms']+task['ttl']-time.time()*1000)/1000)
            task['done'].wait(remaining)
            with TASK_LOCK:
                task=TASKS.get(task_id)
                if task and task['status']=='working':
                    task['cancel'].set();task.update(status='failed',statusMessage='Task lifetime expired.',lastUpdatedAt=now())
        if not task:raise DomainError('UNKNOWN_TASK',str(task_id))
        result=task['result'] or {'resultType':'complete','content':[{'type':'text','text':task['statusMessage']}],'isError':True}
        result.setdefault('_meta',{})['io.modelcontextprotocol/related-task']={'taskId':task_id};return result
    if method=='tasks/list':
        with TASK_LOCK:cleanup_tasks();return {'tasks':[task_view(t) for t in TASKS.values()]}
    raise DomainError('METHOD_NOT_FOUND',str(method))

def request_protocol(message):
    params=message.get('params') or {};meta=params.get('_meta',{}) if isinstance(params,dict) else {}
    return meta.get('io.modelcontextprotocol/protocolVersion')
def validate_envelope(message):
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0':raise DomainError('INVALID_REQUEST','Expected a JSON-RPC 2.0 object')
    if 'id' in message and (message['id'] is None or isinstance(message['id'],bool) or not isinstance(message['id'],(str,int))):raise DomainError('INVALID_REQUEST','Request id must be a string or integer')
    if not isinstance(message.get('method'),str):raise DomainError('INVALID_REQUEST','method must be a string')
    if request_protocol(message)==LATEST_PROTOCOL and message['method']!='server/discover':
        meta=(message.get('params') or {}).get('_meta',{})
        if not isinstance(meta.get('io.modelcontextprotocol/clientCapabilities'),dict):raise DomainError('INVALID_ARGUMENTS','2026 requests require clientCapabilities in _meta')
def error_code(error):
    if isinstance(error,DomainError):
        if error.code=='METHOD_NOT_FOUND':return -32601
        if error.code=='INVALID_REQUEST':return -32600
        if error.code.startswith('INVALID_') or error.code in ('UNKNOWN_TOOL','UNKNOWN_TASK'):return -32602
        return -32000
    if isinstance(error,json.JSONDecodeError):return -32700
    return -32603

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',default=str(data_dir()));parser.add_argument('--no-autostart',action='store_true');args=parser.parse_args()
    if not args.no_autostart:ensure_runtime(args.data_dir)
    client=Client(args.data_dir)
    for line in sys.stdin:
        message=None
        try:
            if len(line)>1024*1024:raise DomainError('INVALID_REQUEST','Input exceeds 1 MiB')
            message=json.loads(line);validate_envelope(message)
            if 'id' not in message:continue
            result=handle(message,client)
            if request_protocol(message)==LATEST_PROTOCOL and isinstance(result,dict):result.setdefault('_meta',{})['io.modelcontextprotocol/serverInfo']=SERVER_INFO
            response={'jsonrpc':'2.0','id':message['id'],'result':result}
        except Exception as error:
            response={'jsonrpc':'2.0','id':message.get('id') if isinstance(message,dict) else None,'error':{'code':error_code(error),'message':str(error)}}
        print(json.dumps(response,ensure_ascii=True),flush=True)

if __name__=='__main__':main()
