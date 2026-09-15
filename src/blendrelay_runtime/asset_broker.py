"""Policy checked asset acquisition, independent of Blender and its UI."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from blendrelay_blender.asset_rules import AssetError
from .asset_providers import PublicTransport, PolyHaven, PolyPizza, identifier
from urllib.parse import urlsplit

OFFLINE = 'No online asset providers are enabled. Enable Poly Haven or Poly Pizza, or use procedural geometry only.'


def safe_file(root, name):
    path = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or any(p == '..' or p.endswith(('.', ' ')) for p in path.parts):
        raise AssetError('Unsafe asset file path')
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise AssetError('Asset file escapes its cache directory')
    return target


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class AssetBroker:
    def __init__(self, root, transport=None, providers=None):
        self.root = Path(root) / 'assets'
        self.root.mkdir(parents=True, exist_ok=True)
        self.transport = transport or PublicTransport()
        self.providers = providers or {p.name:p for p in (PolyHaven(self.transport), PolyPizza(self.transport))}
        self.lock = threading.RLock()

    def directory(self, asset_id):
        if not isinstance(asset_id,str): raise AssetError('Asset ID must be text')
        provider, sep, key = asset_id.partition(':')
        if not sep or provider not in self.providers: raise AssetError('Use a provider-qualified asset ID returned by search')
        return safe_file(self.root, provider + '/' + identifier(key))

    def cached(self, asset_id, verify=False):
        directory = self.directory(asset_id)
        manifest = directory / 'metadata.json'
        if not manifest.is_file(): return None
        record = json.loads(manifest.read_text(encoding='utf-8'))
        if record.get('asset_id') != asset_id: raise AssetError('Cache metadata ID mismatch')
        if not isinstance(record.get('files'),list):
            raise AssetError('Cache metadata is invalid')
        for file in record['files']:
            if not isinstance(file,dict) or not isinstance(file.get('path'),str):
                raise AssetError('Cache file metadata is invalid')
            path = safe_file(directory, file['path'])
            if not path.is_file() or (verify and (not isinstance(file.get('sha256'),str) or checksum(path) != file['sha256'])):
                raise AssetError('Cached asset is incomplete or changed; remove its cache folder before downloading again')
        return dict(record, cached=True, cache_directory=str(directory))

    @staticmethod
    def summary(record):
        return {k:v for k,v in record.items() if k not in ('files',)}

    def thumbnail_path(self, record):
        provider=record.get('provider')
        asset_id=record.get('provider_id')
        if provider not in self.providers or not isinstance(asset_id,str): return None
        folder=safe_file(self.root,'thumbnails/'+provider)
        for suffix in ('.png','.jpg','.jpeg','.webp'):
            candidate=folder/(identifier(asset_id)+suffix)
            if candidate.is_file(): return candidate
        return None

    def cache_thumbnail(self, record, policy_getter):
        """Cache a small catalogue preview. A missing preview never blocks search."""
        if self.thumbnail_path(record): return self.thumbnail_path(record)
        url=record.get('thumbnail_url')
        if not isinstance(url,str): return None
        suffix=Path(urlsplit(url).path).suffix.lower()
        if suffix not in ('.png','.jpg','.jpeg','.webp'): return None
        provider=record['provider'];asset_id=identifier(record['provider_id'])
        folder=safe_file(self.root,'thumbnails/'+provider);folder.mkdir(parents=True,exist_ok=True)
        target=folder/(asset_id+suffix);partial=target.with_suffix(target.suffix+'.partial')
        def check(): policy_getter().check(record)
        try:
            self.transport.download(provider,url,partial,1024*1024,check)
            check();partial.replace(target)
            return target
        except Exception:
            try: partial.unlink()
            except OSError: pass
            return None

    def list_cached(self, query='', policy=None):
        rows = []
        words = (query or '').lower().split()
        for provider in self.providers:
            for manifest in (self.root / provider).glob('*/metadata.json'):
                try:
                    record = self.cached(provider + ':' + manifest.parent.name)
                    if policy: policy.check(record, cached=True)
                    if all(w in (record['name']+' '+' '.join(record.get('tags',[]))).lower() for w in words):
                        item=self.summary(record);thumbnail=self.thumbnail_path(record)
                        if thumbnail: item['thumbnail_path']=str(thumbnail)
                        rows.append(item)
                except (AssetError, OSError, ValueError, KeyError): continue
        return rows

    def search(self, query, policy_getter, provider=None, style=None, max_results=10):
        if not isinstance(query,str) or len(query)>200: raise AssetError('Search query must be at most 200 characters')
        if provider not in (None,'local','poly_haven','poly_pizza'): raise AssetError('Unknown provider filter')
        if isinstance(max_results,bool) or not isinstance(max_results,int) or not 1<=max_results<=50: raise AssetError('max_results must be 1–50')
        if style is not None and (not isinstance(style,str) or len(style)>100): raise AssetError('Style must be at most 100 characters')
        policy = policy_getter()
        rows = self.list_cached(query,policy)
        if provider not in (None,'local'): rows=[r for r in rows if r['provider']==provider]
        if rows or provider=='local': return dict(results=rows[:max_results],messages=[],source='cache')
        messages=[]
        if not policy.poly_haven and not policy.poly_pizza: return dict(results=[],messages=[OFFLINE],fallback='procedural')
        order = ['poly_pizza','poly_haven'] if style and any(w in style.lower() for w in ('low','stylized','lightweight')) else ['poly_haven','poly_pizza']
        enabled=[name for name in order if (not provider or name==provider) and getattr(policy_getter(),name)]
        found={}
        with ThreadPoolExecutor(max_workers=max(1,len(enabled)),thread_name_prefix='BlendRelay-provider') as pool:
            futures={pool.submit(self.providers[name].search,query,50):name for name in enabled}
            for future in as_completed(futures):
                name=futures[future]
                try:found[name]=future.result()
                except Exception as error:messages.append(f'{name}: {error}')
        for name in order:
            for record in found.get(name,[]):
                try:policy_getter().check(record)
                except AssetError:continue
                rows.append(record)
                if len(rows)>=max_results:break
            if len(rows)>=max_results:break
        for record in rows[:min(max_results,12)]:
            thumbnail=self.cache_thumbnail(record,policy_getter)
            if thumbnail: record['thumbnail_path']=str(thumbnail)
        if not rows: messages.append('No permitted assets found. Use local assets or procedural Blender geometry.')
        return dict(results=rows,messages=messages,fallback=None if rows else 'procedural')

    def acquire(self, asset_id, policy_getter):
        # Serialize acquisitions: duplicate requests share the completed cache.
        with self.lock:
            existing = self.cached(asset_id,verify=True)
            if existing:
                policy_getter().check(existing,cached=True)
                self.attribution(existing)
                return existing
            directory = self.directory(asset_id)
            provider, key = asset_id.split(':',1)
            if not getattr(policy_getter(),provider): raise AssetError('This online asset provider is disabled in Asset Provider Policy.')
            record = self.providers[provider].resolve(key)
            if record['asset_id']!=asset_id: raise AssetError('Provider returned a different asset ID')
            policy_getter().check(record)
            limit = policy_getter().max_download_mb * 1024 * 1024
            if record.get('size_bytes') and record['size_bytes']>limit: raise AssetError('Download exceeds Maximum download size')
            if not record.get('files') or len(record['files'])>100: raise AssetError('Unsupported asset file manifest')
            directory.parent.mkdir(parents=True,exist_ok=True)
            staging=directory.parent / ('.partial-'+uuid.uuid4().hex)
            staging.mkdir()
            total=0
            def check():
                current=policy_getter()
                current.check(record)
                if total>current.max_download_mb*1024*1024: raise AssetError('Download exceeds Maximum download size')
            try:
                used=set()
                for file in record['files']:
                    target=safe_file(staging,file['path'])
                    if target.name=='metadata.json' or str(target).casefold() in used: raise AssetError('Duplicate or reserved asset path')
                    used.add(str(target).casefold())
                    target.parent.mkdir(parents=True,exist_ok=True)
                    total+=self.transport.download(provider,file['url'],target,limit-total,check)
                    check()
                    file['sha256']=checksum(target)
                record.update(download_date=datetime.now(timezone.utc).isoformat(), original_file_name=record['entry_file'],size_bytes=total)
                if not safe_file(staging,record['entry_file']).is_file(): raise AssetError('Missing primary asset file')
                (staging/'metadata.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
                check()
                if directory.exists(): raise AssetError('Incomplete cache folder exists; inspect or remove it before retrying')
                staging.rename(directory)
            finally:
                if staging.exists(): shutil.rmtree(staging)
            result=self.cached(asset_id,verify=True)
            self.attribution(result)
            return result

    def attribution(self, record):
        if record['licence']!='CC-BY': return
        with self.lock:
            target=self.root/'ATTRIBUTION.txt'
            old=target.read_text(encoding='utf-8') if target.exists() else ''
            marker=f"[{record['asset_id']} | {record['licence_url']}]"
            if marker in old: return
            entry=f"\n{marker}\n{record['name']} — {record['creator']}\nSource: {record['source_url']}\nLicence: {record['licence_url']}\nChanges: describe your modifications when publishing.\n"
            temporary=target.with_suffix('.tmp')
            temporary.write_text(old+entry,encoding='utf-8')
            temporary.replace(target)
