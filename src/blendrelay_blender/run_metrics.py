"""Provider usage is reported as supplied; cache/thinking fields are not added twice."""
import json


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
        stream=data.get('event',{}) if data.get('type')=='stream_event' else {}
        block=stream.get('content_block',{}) if stream.get('type')=='content_block_start' else {}
        if block.get('type')=='tool_use':
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
