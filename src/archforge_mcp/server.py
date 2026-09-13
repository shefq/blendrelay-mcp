"""MCP JSON-RPC stdio gateway; public transport differs from private framed RPC."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from archforge_domain.model import DomainError
from archforge_runtime.protocol import Client, data_dir
from .job_results import wait_and_compact

S=lambda **properties:{'type':'object','properties':properties,'additionalProperties':False}
STR={'type':'string'};INT={'type':'integer'};NUM={'type':'number'}
PROJECT={'project_id':STR}
TOOLS={}
def tool(name,method,description,properties=None,required=(),read=False,preapproved=False):
    schema=S(**(properties or {}));schema['required']=list(required)
    TOOLS[name]={'name':name,'description':description,'inputSchema':schema,'annotations':{'readOnlyHint':read,'destructiveHint':not (read or preapproved),'openWorldHint':False},'_method':method}

tool('archforge_blender_sessions','blender.sessions','List live general-purpose Blender connections; no architecture project is required.',read=True)
tool('get_asset_policy','assets.policy','Read active scene asset provider/licence policy. Never change these settings or bypass the broker using Python or shell downloads.',{'instance_id':STR},read=True)
tool('search_assets','assets.search','Search permitted assets, cache first. Poly Haven suits realistic environments, materials and HDRIs; Poly Pizza suits lightweight props. Returns an asynchronous job. If no permitted result exists, use procedural geometry.',{'query':STR,'provider':{'enum':['local','poly_haven','poly_pizza']},'style':STR,'max_results':INT,'instance_id':STR},('query',),read=True)
tool('import_asset','assets.import','Download/cache/import a permitted asset into its own collection. Respects live scene licence and size limits. Returns a job: poll asset_job until complete; do not resubmit. HDRIs load as separate world assets without changing active lighting.',{'asset_id':STR,'collection_name':STR,'location':{'type':'array','items':NUM,'minItems':3,'maxItems':3},'scale':NUM,'instance_id':STR},('asset_id',),preapproved=True)
tool('list_cached_assets','assets.cached','List locally cached assets allowed by the current licence policy. No network downloads. Returns an asynchronous job.',{'query':STR,'instance_id':STR},read=True)
tool('refresh_asset_cache','assets.refresh','Rescan the local asset cache. Does not delete assets or attribution. Returns an asynchronous job.',{'instance_id':STR},read=True)
tool('asset_job','assets.job','Read asset search/import result. Poll only queued/running jobs, never resubmit an import to poll it.',{'job_id':STR},('job_id',),read=True)
ASSET_TOOLS={'get_asset_policy','search_assets','import_asset','list_cached_assets','refresh_asset_cache','asset_job'}
tool('archforge_blender_command','blender.submit','Edit live Blender. inspect takes names=[...]; execute takes code for arbitrary edits. mesh_edit takes operation (bevel, extrude, inset, bridge, move_normal, assign_material), distance in local mesh units, segments, material; uses current Edit Mode selection. validate_selection takes names and reports mesh defects. screenshot takes max_dimension (default 512). Waits up to wait_seconds (default 8, max 30); complete results need no polling. Poll only queued/running jobs. Never blindly resubmit an edit. Preserve unrelated content; use targeted inspection.',
     {'operation_id':STR,'action':{'enum':['inspect','execute','execute_code','get_scene_info','get_object_info','versions','checkpoint','restore','screenshot','mesh_edit','validate_selection']},'arguments':{'type':'object'},'instance_id':STR,'wait_seconds':NUM,'repeat_result':{'type':'boolean'}},('action',),preapproved=True)
tool('archforge_blender_job','blender.job','Wait for a queued/running job. Terminal results need no polling. Repeated results are suppressed unless repeat_result=true. Interrupted jobs must not be blindly retried.',{'operation_id':STR,'instance_id':STR,'wait_seconds':NUM,'repeat_result':{'type':'boolean'}},('operation_id',),True)

tool('archforge_get_capabilities','capabilities','Supported ArchForge operations, asset families, and release limits.',read=True)
tool('archforge_list_projects','project.list','List persistent local projects.',read=True)
tool('archforge_create_house','project.create','Create a new editable one-storey house. Text planning is bounded; provide dimensions and room rectangles for precise plans.',
     {'prompt':STR,'width':NUM,'depth':NUM,'bedrooms':INT,'height':NUM,'roof':{'enum':['flat','gable','none']},'furnish':{'type':'boolean'},'name':STR,
      'rectangles':{'type':'array','maxItems':40,'items':{'type':'object','properties':{'name':STR,'x':NUM,'y':NUM,'width':NUM,'depth':NUM},'required':['name','x','y','width','depth'],'additionalProperties':False}}})
tool('archforge_get_project','project.get','Read a semantic model at its current or historical revision.',{**PROJECT,'revision':INT},('project_id',),True)
tool('archforge_query_entities','entity.query','Find target IDs and parameters. Results are paginated.',{**PROJECT,'kind':STR,'query':STR,'offset':INT,'limit':INT},('project_id',),True)
tool('archforge_list_sessions','session.list','List connected Blender instances and their selection/revision state.',read=True)
tool('archforge_get_selection','selection.get','Capture selected semantic entity IDs before planning an edit.',PROJECT,('project_id',),True)
tool('archforge_propose_changes','plan.propose','Validate structured operations or a bounded native prompt. Returns a plan; does not change live geometry. Use capabilities for operation fields.',
     {**PROJECT,'expected_revision':INT,'prompt':STR,'selected_entity_ids':{'type':'array','items':STR},'operations':{'type':'array','maxItems':100,'items':{'type':'object','properties':{'kind':STR,'target_id':STR,'parameters':{'type':'object'}},'required':['kind','target_id','parameters'],'additionalProperties':False}}},('project_id',))
tool('archforge_preview_plan','plan.preview','Read a plan summary and create its dimensioned SVG preview.',{'plan_id':STR},('plan_id',))
tool('archforge_apply_plan','plan.apply','Apply the reviewed plan with a unique durable operation_id. Reuse the same ID when retrying. Live Blender uses staging then commit.',{'plan_id':STR,'operation_id':STR,'expected_revision':INT},('plan_id','operation_id','expected_revision'))
tool('archforge_get_job','job.get','Read an operation state. A timeout is not cancellation.',{'job_id':STR},('job_id',),True)
tool('archforge_cancel_job','job.cancel','Cancel an uncommitted staged operation. Committed changes require restore.',{'job_id':STR},('job_id',))
tool('archforge_validate_project','project.validate','Validate supported model references and geometric constraints.',PROJECT,('project_id',),True)
tool('archforge_list_revisions','revision.list','List durable revisions for restore.',PROJECT,('project_id',),True)
tool('archforge_propose_restore','plan.restore','Propose restoring an earlier semantic revision. Preserves operation history.',{**PROJECT,'revision':INT},('project_id','revision'))
tool('archforge_propose_protection','plan.protect','Propose protection changes only when explicitly requested by the user.',{**PROJECT,'entity_ids':{'type':'array','items':STR},'protected':{'type':'boolean'}},('project_id','entity_ids'))
tool('archforge_register_source','source.register','Register a PNG/JPEG within configured source roots. Use host vision and a known dimension to trace rectangular spaces; this does not infer a house automatically.',{'path':STR},('path',))
tool('archforge_create_from_floor_plan','project.from_plan','Create a house from a registered image and host/user-traced rectangular rooms in image pixels. Supply a known segment in pixels and metres for calibration.',
     {'source_id':STR,'rectangles':{'type':'array','maxItems':40,'items':{'type':'object','properties':{'name':STR,'x':NUM,'y':NUM,'width':NUM,'depth':NUM},'required':['name','x','y','width','depth'],'additionalProperties':False}},'pixel_length':NUM,'real_length_m':NUM,'name':STR,'height':NUM,'roof':{'enum':['flat','gable','none']},'furnish':{'type':'boolean'}},('source_id','rectangles','pixel_length','real_length_m'))
tool('archforge_export','export.start','Start a background export from the committed semantic model. Formats: blend, png cutaway render, glb, archforge, json, svg. Read archforge_get_job for the output. Unmanaged live-scene objects are not exported.',{**PROJECT,'format':{'enum':['blend','png','glb','archforge','json','svg']}},('project_id',))
tool('archforge_read_artifact','artifact.read','Read a generated preview or registered image from the runtime artifact store.',{'path':STR},('path',),True)


def ensure_runtime(root):
    client=Client(root,timeout=1)
    try:client.call('capabilities');return
    except (DomainError,OSError):pass
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with open(root/'runtime.log','ab') as log:
        subprocess.Popen([sys.executable,'-m','archforge_runtime.cli','--data-dir',str(root),'serve','--allow-source-root',str(Path.cwd())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(60):
        try:client.call('capabilities');return
        except (DomainError,OSError):time.sleep(.1)
    raise DomainError('RUNTIME_OFFLINE','Runtime could not start; see runtime.log')


def handle(message,client):
    method=message.get('method');params=message.get('params',{})
    if method=='initialize':
        requested=params.get('protocolVersion','2025-11-25')
        version=requested if requested in ('2024-11-05','2025-03-26','2025-06-18','2025-11-25') else '2025-11-25'
        return {'protocolVersion':version,'capabilities':{'tools':{}},'serverInfo':{'name':'archforge-mcp','version':'0.2.4'},'instructions':'Use archforge_blender_sessions and archforge_blender_command for arbitrary Blender tasks. Inspect before editing; preserve unrelated scene content. Use get_asset_policy, search_assets and import_asset for permitted external assets; never bypass its provider or licence policy. Execute Python in small meaningful steps with descriptive version labels. Poll jobs and inspect results. Full-scene checkpoints allow restoration. Architecture template tools are optional legacy helpers, not limits on general Blender commands.'}
    if method=='ping':return {}
    if method=='tools/list':return {'tools':[{k:v for k,v in t.items() if k!='_method'} for t in TOOLS.values() if t['name'].startswith('archforge_blender_') or t['name'] in ASSET_TOOLS or t['name'] in ('archforge_get_capabilities','archforge_register_source','archforge_read_artifact')]}
    if method=='tools/call':
        name=params.get('name');definition=TOOLS.get(name)
        if not definition:raise DomainError('UNKNOWN_TOOL',str(name))
        args=params.get('arguments',{})
        schema=definition['inputSchema']
        if not isinstance(args,dict) or set(args)-set(schema['properties']) or set(schema['required'])-set(args):raise DomainError('INVALID_ARGUMENTS','Unknown or missing tool fields')
        try:
            result=(wait_and_compact(client,definition['_method'],args)
                    if name in ('archforge_blender_command','archforge_blender_job') else client.call(definition['_method'],args))
            if name in ('archforge_blender_command','archforge_blender_job') and isinstance(result.get('result'),dict) and result['result'].get('image'):
                content=[{'type':'image','data':result['result']['image'],'mimeType':result['result']['mime_type']}]
                if result['result'].get('path'):content.append({'type':'text','text':f"Viewport render saved to: {result['result']['path']}"})
                return {'content':content,'isError':False}
            if name=='archforge_read_artifact':
                chunks=[base64.b64decode(result['data'])];mime=result['mime_type']
                while not result['eof']:
                    result=client.call('artifact.read',{'path':args['path'],'offset':sum(len(c) for c in chunks)})
                    chunks.append(base64.b64decode(result['data']))
                data=b''.join(chunks)
                if mime in ('image/png','image/jpeg'):return {'content':[{'type':'image','data':base64.b64encode(data).decode(),'mimeType':mime}]}
                return {'content':[{'type':'text','text':data.decode('utf-8')}]}
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'isError':isinstance(result,dict) and result.get('status') in ('failed','interrupted')}
        except (DomainError,OSError,ValueError) as e:
            error=e.as_dict() if isinstance(e,DomainError) else {'code':'INVALID_ARGUMENTS' if isinstance(e,ValueError) else 'RUNTIME_OFFLINE','message':str(e)}
            return {'content':[{'type':'text','text':json.dumps(error)}],'isError':True}
    raise DomainError('METHOD_NOT_FOUND',str(method))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',default=str(data_dir()));parser.add_argument('--no-autostart',action='store_true');args=parser.parse_args()
    if not args.no_autostart:ensure_runtime(args.data_dir)
    client=Client(args.data_dir)
    for line in sys.stdin:
        msg=None
        try:
            if len(line)>1024*1024:raise ValueError('Input exceeds 1 MiB')
            msg=json.loads(line)
            if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0':raise ValueError('Invalid JSON-RPC envelope')
            if 'id' not in msg:continue
            result=handle(msg,client);response={'jsonrpc':'2.0','id':msg['id'],'result':result}
        except Exception as e:
            response={'jsonrpc':'2.0','id':msg.get('id') if isinstance(msg,dict) else None,'error':{'code':-32601 if isinstance(e,DomainError) and e.code=='METHOD_NOT_FOUND' else -32602 if isinstance(e,DomainError) else -32700,'message':str(e)}}
        print(json.dumps(response,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
