"""Asynchronous asset RPCs: downloads never hold the Blender polling lock."""
from dataclasses import asdict
import copy
import math
import threading
import time
import uuid
from archforge_blender.asset_rules import AssetPolicy, AssetError
from .asset_broker import AssetBroker


class AssetService:
    def assets_init(self):
        self.asset_broker=AssetBroker(self.store.root)
        self.asset_jobs={}

    def asset_session(self, instance_id=None):
        sessions=self.rpc_blender_sessions()
        if instance_id is None and len(sessions)==1: instance_id=sessions[0]['instance_id']
        session=next((s for s in sessions if s['instance_id']==instance_id),None)
        if not session or session.get('asset_policy') is None:
            raise AssetError('Connect an updated ArchForge add-on; select instance_id if multiple Blender instances are open')
        return session

    def rpc_assets_policy(self, instance_id=None):
        session=self.asset_session(instance_id)
        return dict(asdict(AssetPolicy.read(session['asset_policy'])),local_cache=True,instance_id=session['instance_id'])

    def asset_start(self, operation, instance_id=None, **arguments):
        session=self.asset_session(instance_id)
        instance_id=session['instance_id']
        scene_key=session.get('asset_scene')
        if sum(j['status']=='running' for j in self.asset_jobs.values())>=4:
            raise AssetError('Asset Library is busy; wait for an existing request')
        for key in list(self.asset_jobs):
            if time.time()-self.asset_jobs[key]['created_at']>3600 and self.asset_jobs[key]['status']!='running': del self.asset_jobs[key]
        job_id=uuid.uuid4().hex
        self.asset_jobs[job_id]=dict(job_id=job_id,status='running',created_at=time.time(),instance_id=instance_id)
        def policy():
            with self.lock:
                current=self.asset_session(instance_id)
                if current.get('asset_scene')!=scene_key: raise AssetError('Active scene changed; submit the asset request again')
                return AssetPolicy.read(current['asset_policy'])
        def work():
            try:
                if operation=='search': result=self.asset_broker.search(policy_getter=policy,**arguments)
                elif operation=='cache': result={'results':self.asset_broker.list_cached(arguments.get('query'),policy())}
                else:
                    record=self.asset_broker.acquire(arguments.pop('asset_id'),policy)
                    with self.lock:
                        policy().check(record,cached=True)
                        op_id='asset-'+job_id
                        self._write_job(dict(operation_id=op_id,instance_id=instance_id,action='asset_import',
                            arguments=dict(asset_id=record['asset_id'],asset_scene=scene_key,**arguments),
                            fingerprint=job_id,status='queued',created_at=time.time()))
                    result={'operation_id':op_id,'status':'queued','asset':self.asset_broker.summary(record)}
                with self.lock: self.asset_jobs[job_id].update(status='complete',result=result)
            except Exception as error:
                with self.lock: self.asset_jobs[job_id].update(status='failed',error=str(error))
        threading.Thread(target=work,name='ArchForge-assets',daemon=True).start()
        return dict(job_id=job_id,status='running')

    def rpc_assets_search(self,query,provider=None,style=None,max_results=10,instance_id=None):
        return self.asset_start('search',instance_id,query=query,provider=provider,style=style,max_results=max_results)

    def rpc_assets_cached(self,query=None,instance_id=None):
        if query is not None and (not isinstance(query,str) or len(query)>200): raise AssetError('Invalid cache query')
        return self.asset_start('cache',instance_id,query=query)

    def rpc_assets_refresh(self,instance_id=None):
        return self.asset_start('cache',instance_id)

    def rpc_assets_import(self,asset_id,collection_name=None,location=None,scale=1.0,instance_id=None):
        if collection_name is not None and (not isinstance(collection_name,str) or len(collection_name)>100): raise AssetError('Collection name must be at most 100 characters')
        if isinstance(scale,bool) or not isinstance(scale,(float,int)) or not math.isfinite(scale) or not 0<scale<=10000: raise AssetError('Scale must be positive and finite')
        if location is not None and (not isinstance(location,(list,tuple)) or len(location)!=3 or any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in location)): raise AssetError('Location must contain three finite coordinates')
        return self.asset_start('import',instance_id,asset_id=asset_id,collection_name=collection_name,location=location,scale=scale)

    def rpc_assets_job(self,job_id):
        if job_id not in self.asset_jobs: raise AssetError('Unknown asset job; runtime may have restarted')
        result=copy.deepcopy(self.asset_jobs[job_id])
        bridge_id=result.get('result',{}).get('operation_id')
        if bridge_id:
            bridge=self.rpc_blender_job(bridge_id)
            result.update(status=bridge['status'],result=bridge.get('result'),error=bridge.get('error'))
        return result
