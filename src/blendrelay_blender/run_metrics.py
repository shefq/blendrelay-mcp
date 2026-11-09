"""Provider usage is reported as supplied; cache/thinking fields are not added twice."""
import json

TOOL_ACTIONS={'execute_blender_python':'execute','build_scene_batch':'build_batch','mesh_edit':'mesh_edit',
              'validate_mesh_selection':'validate_selection','capture_viewport':'screenshot',
              'capture_focused_view':'screenshot','blendrelay_read_artifact':'screenshot',
              'get_scene_info':'get_scene_info','get_object_info':'get_object_info','inspect_scene':'inspect',
              'inspect_blender_data':'inspect'}


class Metrics:
    def __init__(self):
        self.seen=set()
        self.calls={}
        self.usage={}
        self.last_stage='planning'
        self.stage_usage={}
        self.warnings=[]
        self.signatures={}

    def feed(self,line):
        try: data=json.loads(line)
        except (ValueError,TypeError): return
        step=data.get('step_update',{})
        if step.get('step_type')=='tool' and step.get('state')=='DONE':
            key=step.get('step_index')
            if key in self.seen:return
            self.seen.add(key)
            info=step.get('tool_info',{})
            params=info.get('parameters',{})
            name=step.get('tool_name','')
            self.record(name,params)
        item=data.get('item',{})
        if data.get('type')=='item.completed' and item.get('type') in {'mcp_tool_call','command_execution','web_search'}:
            key=item.get('id')
            if key not in self.seen:
                self.seen.add(key);self.record(item.get('tool',item.get('type')),item.get('arguments',{}))
        # Claude: read tool calls from complete assistant messages. A streaming
        # content_block_start carries an empty input (arguments arrive later as
        # deltas), which made every call to one tool look identical.
        message=data.get('message',{}) if data.get('type')=='assistant' else {}
        for block in message.get('content',()) if isinstance(message.get('content'),list) else ():
            if isinstance(block,dict) and block.get('type')=='tool_use':
                key=block.get('id')
                if key not in self.seen:
                    self.seen.add(key);self.record(block.get('name','tool'),block.get('input',{}))
        if step.get('usage'):
            self.stage_usage.setdefault(self.last_stage,[]).append(step['usage'])
        if data.get('event')=='result':self.usage=data.get('result',{}).get('usage',{})
        if data.get('type')=='turn.completed':self.usage=data.get('usage',{})
        if data.get('type')=='result' and isinstance(data.get('usage'),dict):self.usage=data['usage']

    def record(self,name,params):
        raw=json.dumps(params,sort_keys=True,default=str)
        action=params.get('Arguments',params.get('arguments',params)) if isinstance(params,dict) else {}
        if isinstance(action,str):
            try:action=json.loads(action)
            except ValueError:action={}
        act=action.get('action','') if isinstance(action,dict) else ''
        # Direct MCP tool calls (Codex, Claude) carry no action field; use the tool name.
        # Antigravity wraps every MCP call as call_mcp_tool with the real name in ToolName.
        tool=params.get('ToolName') if isinstance(params,dict) and params.get('ToolName') else name
        act=act or TOOL_ACTIONS.get(str(tool).split('__')[-1],'')
        stage=('verification' if act in {'validate_selection','screenshot'} else
               'editing' if act in {'execute','execute_code','build_batch','mesh_edit'} else
               'waiting' if 'job' in str(name) or 'job' in str(params.get('ToolName','') if isinstance(params,dict) else '') else
               'inspection' if act in {'inspect','get_scene_info','get_object_info'} else 'other')
        self.last_stage=stage
        self.calls[stage]=self.calls.get(stage,0)+1
        signature=name+raw
        self.signatures[signature]=self.signatures.get(signature,0)+1
        if self.signatures[signature]==3:
            self.warnings.append('Repeated tool request: '+name+' (3 identical calls; check whether new information was needed)')

    def summary(self):
        return dict(tool_calls_by_stage=self.calls,provider_usage=self.usage,
                    stage_usage_estimates=self.stage_usage,
                    attribution='Stage usage is inferred from the preceding tool, not exact billing. Provider totals are authoritative; thinking/cache may overlap other fields.',
                    warnings=self.warnings)
