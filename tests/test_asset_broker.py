import json
from pathlib import Path
import tempfile
import time
import unittest
from archforge_blender.asset_rules import AssetError, AssetPolicy
from archforge_runtime.asset_broker import AssetBroker, OFFLINE
from archforge_runtime.asset_providers import PolyHaven, PolyPizza
from archforge_runtime.service import Service


def row(provider='poly_haven', asset_id='stone', licence='CC0', creator='Creator', **extra):
    return {'provider':provider, 'asset_id':provider+':'+asset_id, 'provider_id':asset_id,
            'name':'Stone Asset', 'licence':licence, 'creator':creator,
            'source_url':'https://example.test/source', 'licence_url':'https://example.test/licence',
            'tags':['stone'], 'format':'GLB', 'kind':'model', **extra}


class Transport:
    def __init__(self): self.downloads=0
    def download(self, provider, url, target, limit, policy_check):
        self.downloads += 1
        policy_check()
        data=b'asset data'
        if len(data)>limit: raise AssetError('Download exceeds Maximum download size')
        target.write_bytes(data)
        return len(data)


class Provider:
    def __init__(self, name, licence='CC0'):
        self.name=name;self.licence=licence;self.searches=0;self.resolves=0
    def search(self, query, limit):
        self.searches += 1
        return [row(self.name,licence=self.licence)]
    def resolve(self, asset_id):
        self.resolves += 1
        item=row(self.name,asset_id,self.licence)
        item.update(entry_file='asset.glb',files=[{'path':'asset.glb','url':'https://example.test/asset.glb','size':10}])
        return item


class AssetBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.transport=Transport()
        self.haven=Provider('poly_haven')
        self.pizza=Provider('poly_pizza')
        self.broker=AssetBroker(self.temp.name,self.transport,{'poly_haven':self.haven,'poly_pizza':self.pizza})

    def tearDown(self): self.temp.cleanup()

    def test_policy_switches_and_offline_fallback(self):
        off=AssetPolicy(poly_haven=False,poly_pizza=False)
        result=self.broker.search('stone',lambda:off)
        self.assertEqual(result['messages'],[OFFLINE])
        self.assertEqual(result['fallback'],'procedural')
        self.assertEqual(self.haven.searches,0)
        enabled=AssetPolicy(poly_haven=True,poly_pizza=False)
        result=self.broker.search('stone',lambda:enabled)
        self.assertEqual(result['results'][0]['provider'],'poly_haven')
        self.assertEqual(self.pizza.searches,0)

    def test_pizza_cc_by_is_excluded_until_enabled(self):
        self.pizza.licence='CC-BY'
        no_by=AssetPolicy(poly_haven=False,poly_pizza=True,pizza_cc_by=False)
        self.assertEqual(self.broker.search('stone',lambda:no_by,provider='poly_pizza')['results'],[])
        yes_by=AssetPolicy(poly_haven=False,poly_pizza=True,pizza_cc_by=True)
        self.assertEqual(self.broker.search('stone',lambda:yes_by,provider='poly_pizza')['results'][0]['licence'],'CC-BY')
        with self.assertRaisesRegex(AssetError,'requires CC-BY attribution'):
            no_by.check(row('poly_pizza',licence='CC-BY'))

    def test_cache_reuse_metadata_and_attribution_append(self):
        self.pizza.licence='CC-BY'
        policy=AssetPolicy(pizza_cc_by=True)
        first=self.broker.acquire('poly_pizza:chair',lambda:policy)
        second=self.broker.acquire('poly_pizza:chair',lambda:policy)
        self.assertEqual(self.pizza.resolves,1)
        self.assertEqual(self.transport.downloads,1)
        manifest=json.loads((Path(first['cache_directory'])/'metadata.json').read_text())
        for key in ('provider','asset_id','name','licence','creator','source_url','download_date','original_file_name','tags'):
            self.assertIn(key,manifest)
        attribution=(Path(self.temp.name)/'assets'/'ATTRIBUTION.txt').read_text()
        self.assertEqual(attribution.count('[poly_pizza:chair |'),1)
        self.assertTrue(second['cached'])

    def test_cached_assets_remain_local_when_provider_disabled(self):
        policy=AssetPolicy()
        self.broker.acquire('poly_haven:stone',lambda:policy)
        disabled=AssetPolicy(poly_haven=False,poly_pizza=False)
        cached=self.broker.list_cached('stone',disabled)
        self.assertEqual(len(cached),1)
        self.assertTrue(cached[0]['cached'])

    def test_small_thumbnail_is_cached_under_asset_root(self):
        record=row(thumbnail_url='https://cdn.polyhaven.com/asset_img/thumbs/stone.jpg')
        path=self.broker.cache_thumbnail(record,lambda:AssetPolicy())
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(Path(self.temp.name)/'assets'))


class AssetServiceTests(unittest.TestCase):
    def test_disabled_online_policy_returns_procedural_fallback_async(self):
        with tempfile.TemporaryDirectory() as temporary:
            service=Service(temporary)
            try:
                service.dispatch('blender.poll',{'instance_id':'blender-a','scene_name':'Scene','asset_scene':'scene-a',
                    'asset_policy':{'poly_haven':False,'poly_pizza':False,'pizza_cc0':True,'pizza_cc_by':False,'max_download_mb':200}})
                started=service.dispatch('assets.search',{'query':'chair','instance_id':'blender-a'})
                for _ in range(50):
                    result=service.dispatch('assets.job',{'job_id':started['job_id']})
                    if result['status']!='running': break
                    time.sleep(.01)
                self.assertEqual(result['status'],'complete')
                self.assertEqual(result['result']['messages'],[OFFLINE])
                self.assertEqual(result['result']['fallback'],'procedural')
            finally:
                service.store.close()


class ProviderAdapterTests(unittest.TestCase):
    def test_poly_haven_normalises_model_and_dependencies(self):
        class Metadata:
            def json(self, provider, url):
                if url.endswith('/assets'):
                    return {'sample':{'name':'Sample Model','type':2,'tags':['chair'],'authors':{'Maker':{}}}}
                return {'gltf':{'1k':{'gltf':{'url':'https://dl.polyhaven.org/sample.gltf','size':20,
                    'include':{'textures/sample.jpg':{'url':'https://dl.polyhaven.org/sample.jpg','size':10}}}}}}
        record=PolyHaven(Metadata()).resolve('sample')
        self.assertEqual(record['asset_id'],'poly_haven:sample')
        self.assertEqual(record['kind'],'model')
        self.assertEqual(record['entry_file'],'sample.gltf')
        self.assertEqual(len(record['files']),2)

    def test_poly_pizza_rejects_unknown_licence_and_reads_public_cc_by(self):
        state={'initialData':{'result':[{'publicID':'model_1','title':'Chair','licence':'CC-BY 4.0',
            'creator':{'username':'Designer'},'previewUrl':'https://static.poly.pizza/chair.webp'}]}}
        class Metadata:
            def text(self, provider, url): return 'window.__SERVER_APP_STATE__ = '+json.dumps(state)+';'
        record=PolyPizza(Metadata()).search('chair',10)[0]
        self.assertEqual(record['licence'],'CC-BY')
        self.assertEqual(record['creator'],'Designer')
