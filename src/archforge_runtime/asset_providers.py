"""Public provider metadata adapters. No credentials, private API or paid listings."""
import html
import json
import re
import threading
import time
from urllib.parse import quote, urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from archforge_blender.asset_rules import AssetError

HOSTS = {'poly_haven': {'api.polyhaven.com', 'cdn.polyhaven.com', 'dl.polyhaven.org'},
         'poly_pizza': {'poly.pizza', 'static.poly.pizza'}}


def checked_url(provider, url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS[provider] or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise AssetError('Provider returned an unsupported download location')
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AssetError('Unexpected provider redirect; download stopped')


class PublicTransport:
    def __init__(self):
        self._gate = threading.Lock()
        self._pizza_last = 0

    def open(self, provider, url):
        checked_url(provider, url)
        # Public Poly Pizza robots.txt specifies Crawl-delay: 10.
        if urlsplit(url).hostname == 'poly.pizza':
            with self._gate:
                time.sleep(max(0, 10 - (time.monotonic() - self._pizza_last)))
                self._pizza_last = time.monotonic()
        return build_opener(NoRedirect).open(Request(url, headers={'User-Agent': 'ArchForge-AssetLibrary/0.2.4', 'Accept-Encoding': 'identity'}), timeout=20)

    def text(self, provider, url):
        with self.open(provider, url) as response:
            data = response.read(24 * 1024 * 1024 + 1)
        if len(data) > 24 * 1024 * 1024:
            raise AssetError('Provider metadata exceeded its size limit')
        return data.decode('utf-8')

    def json(self, provider, url):
        return json.loads(self.text(provider, url))

    def download(self, provider, url, target, limit, policy_check):
        policy_check()
        with self.open(provider, url) as response:
            size = response.headers.get('Content-Length')
            if size:
                try: announced=int(size)
                except ValueError: raise AssetError('Provider returned an invalid download size')
                if announced < 0 or announced > limit:
                    raise AssetError('Download exceeds Maximum download size')
            total = 0
            with target.open('wb') as output:
                while True:
                    policy_check()
                    chunk = response.read(min(65536, limit - total + 1))
                    if not chunk: break
                    total += len(chunk)
                    if total > limit: raise AssetError('Download exceeds Maximum download size')
                    output.write(chunk)
        return total


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise AssetError('Invalid provider asset ID')
    return value


def base_record(provider, asset_id, name, licence, creator, source, licence_url, **extra):
    return dict(provider=provider, asset_id=provider + ':' + identifier(asset_id), provider_id=asset_id,
                name=str(name)[:200], licence=licence, creator=str(creator)[:300], source_url=source,
                licence_url=licence_url, tags=[], format='unknown', size_bytes=None, kind='model', **extra)


class PolyHaven:
    name = 'poly_haven'

    def __init__(self, transport):
        self.transport = transport
        self.catalogue = None
        self.updated = 0

    def _catalogue(self):
        if self.catalogue is None or time.monotonic() - self.updated > 3600:
            self.catalogue = self.transport.json(self.name, 'https://api.polyhaven.com/assets')
            self.updated = time.monotonic()
        return self.catalogue

    def record(self, asset_id, item):
        result = base_record(self.name, asset_id, item['name'], 'CC0', ', '.join(item.get('authors', {})),
                             'https://polyhaven.com/a/' + asset_id, 'https://creativecommons.org/publicdomain/zero/1.0/')
        result.update(tags=item.get('tags', []), kind={0:'hdri',1:'material',2:'model'}.get(item.get('type'),'model'),
                      thumbnail_url=item.get('thumbnail_url', 'https://cdn.polyhaven.com/asset_img/thumbs/' + asset_id + '.png?width=128'))
        return result

    def search(self, query, limit):
        words = query.lower().split()
        rows = [(key, value) for key, value in self._catalogue().items()
                if all(word in (value.get('name','')+' '+' '.join(value.get('tags',[]))).lower() for word in words)]
        return [self.record(key, value) for key, value in rows[:limit]]

    def resolve(self, asset_id):
        item = self._catalogue().get(identifier(asset_id))
        if item is None: raise AssetError('Poly Haven asset was not found')
        record = self.record(asset_id, item)
        files = self.transport.json(self.name, 'https://api.polyhaven.com/files/' + asset_id)
        candidates = []
        def walk(value, parts=()):
            if not isinstance(value,dict): return
            if isinstance(value.get('url'),str):
                candidates.append((parts,value))
            else:
                for key, sub in value.items(): walk(sub,parts+(key,))
        walk(files)
        formats = ('.glb','.gltf','.blend') if record['kind']=='model' else ('.hdr','.exr') if record['kind']=='hdri' else ('.blend',)
        def rank(entry):
            parts, value = entry
            suffix = '.' + urlsplit(value['url']).path.rsplit('.',1)[-1].lower()
            resolution = next((int(p[:-1]) for p in parts if re.fullmatch(r'\d+k',p)), 1)
            return (formats.index(suffix) if suffix in formats else 100, abs(resolution-1), value.get('size',0))
        candidates = [c for c in candidates if any(urlsplit(c[1]['url']).path.lower().endswith(ext) for ext in formats)]
        if not candidates: raise AssetError('No supported downloadable variant is available')
        _, selected = min(candidates,key=rank)
        filename = unquote(urlsplit(selected['url']).path.rsplit('/',1)[-1])
        record.update(format=filename.rsplit('.',1)[-1].upper(), entry_file=filename,
                      files=[dict(path=filename, url=selected['url'], size=selected.get('size'))])
        for path, dependency in selected.get('include',{}).items():
            if not isinstance(dependency,dict) or not isinstance(dependency.get('url'),str):
                raise AssetError('Poly Haven returned invalid dependency metadata')
            record['files'].append(dict(path=path, url=dependency['url'], size=dependency.get('size')))
        sizes = [f.get('size') for f in record['files']]
        record['size_bytes'] = sum(sizes) if all(isinstance(s,int) for s in sizes) else None
        return record


def page_state(page):
    marker = 'window.__SERVER_APP_STATE__'
    position = page.find(marker)
    if position < 0: raise AssetError('Poly Pizza public page format changed; use the local cache or procedural geometry')
    content = page[page.find('=',position)+1:].lstrip()
    try: return json.JSONDecoder().raw_decode(content)[0]['initialData']
    except (ValueError,KeyError,TypeError) as error: raise AssetError('Poly Pizza public metadata could not be read') from error


class PolyPizza:
    name = 'poly_pizza'

    def __init__(self, transport): self.transport = transport

    def record(self, item):
        asset_id = identifier(item.get('PublicID', item.get('publicID')))
        raw = item.get('Licence',item.get('licence',''))
        if raw in ('CC0 1.0','CC0'):
            licence, url = 'CC0','https://creativecommons.org/publicdomain/zero/1.0/'
        elif re.fullmatch(r'CC-BY (?:3\.0|4\.0)',raw):
            licence, url = 'CC-BY','https://creativecommons.org/licenses/by/' + raw.split()[-1] + '/'
        else: raise AssetError('Unknown or unsupported Poly Pizza licence')
        creator = item.get('Creator',item.get('creator',{}))
        record = base_record(self.name,asset_id,item.get('Title',item.get('title','Asset')),licence,
                             creator.get('Username',creator.get('username','')),'https://poly.pizza/m/'+asset_id,url)
        record.update(tags=item.get('Tags',[]),format='GLB',thumbnail_url=item.get('previewUrl',''))
        return record

    def search(self, query, limit):
        state = page_state(self.transport.text(self.name,'https://poly.pizza/search/'+quote(query,safe='')))
        results=[]
        for item in state.get('result',[]):
            # Public model records only; exclude advertised/paid listings.
            if not item.get('publicID'): continue
            try: results.append(self.record(item))
            except AssetError: continue
        return results[:limit]

    def resolve(self, asset_id):
        page = self.transport.text(self.name,'https://poly.pizza/m/'+identifier(asset_id))
        item = page_state(page)['model']
        if item.get('Visibility') != 'Public': raise AssetError('Only public free Poly Pizza models are supported')
        record = self.record(item)
        if record['provider_id'] != asset_id:
            raise AssetError('Poly Pizza page returned a different model')
        urls = re.findall(r'https://static\.poly\.pizza/[A-Za-z0-9_-]+\.glb(?=[&"<\s])',html.unescape(page))
        if not urls: raise AssetError('No public GLB download was exposed by this model page')
        filename=asset_id+'.glb'
        record.update(entry_file=filename,files=[dict(path=filename,url=urls[0],size=None)])
        return record
