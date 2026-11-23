"""Bounded HTTP reads, checksum validation and resumable downloads."""
from pathlib import Path
import hashlib
import http.client
import ipaddress
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit,unquote
import uuid

UA='wind-corridor-native/1.1'

def proxy_settings(proxy=None):
    """Merge actual proxy addresses separately from NO_PROXY bypass rules."""
    environment=urllib.request.getproxies_environment()
    read_registry=getattr(urllib.request,'getproxies_registry',lambda:{})
    registry=read_registry()
    explicit=proxy or os.environ.get('WIND_CORRIDOR_PROXY')
    selected={}
    for scheme in ('http','https'):
        value=explicit or environment.get(scheme) or environment.get('all') or registry.get(scheme)
        if value:
            value=value.strip()
            value=value if '://' in value else 'http://'+value
            parsed=urlsplit(value)
            if parsed.scheme not in ('http','https') or not parsed.hostname:
                raise ValueError('请使用HTTP/HTTPS代理地址；SOCKS-only代理需另提供HTTP或混合端口')
            selected[scheme]=value
    return selected

def is_loopback(host):
    host=(host or '').lower().rstrip('.')
    if host=='localhost' or host.endswith('.localhost'):return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False

class _ProxyHandler(urllib.request.ProxyHandler):
    def proxy_open(self,req,proxy,type):
        # RangeCache is an internal loopback service, regardless of NO_PROXY settings.
        if is_loopback(urlsplit(req.full_url).hostname):return None
        return super().proxy_open(req,proxy,type)

def opener(proxy=None):
    return urllib.request.build_opener(_ProxyHandler(proxy_settings(proxy)))

def proxy_for_url(url,proxy=None):
    target=urlsplit(url)
    if is_loopback(target.hostname) or urllib.request.proxy_bypass(target.netloc):return None
    return proxy_settings(proxy).get(target.scheme)

def gdal_proxy_options(url,proxy=None):
    selected=proxy_for_url(url,proxy)
    options={'GDAL_HTTP_PROXY':'','GDAL_HTTPS_PROXY':'','GDAL_HTTP_PROXYUSERPWD':''}
    if selected:
        parsed=urlsplit(selected)
        # Keep the scheme for libcurl, separate credentials from the server address.
        host='['+parsed.hostname+']' if ':' in parsed.hostname else parsed.hostname
        address=parsed.scheme+'://'+host+(f':{parsed.port}' if parsed.port else '')
        options.update(GDAL_HTTP_PROXY=address,GDAL_HTTPS_PROXY=address)
        if parsed.username is not None:
            options['GDAL_HTTP_PROXYUSERPWD']=unquote(parsed.username)+':'+unquote(parsed.password or '')
    return options

def request(url,proxy=None,headers=None,timeout=30):
    return opener(proxy).open(urllib.request.Request(url,headers={'User-Agent':UA,**(headers or {})}),timeout=timeout)

def digest(path,algorithm='sha256'):
    h=hashlib.new(algorithm)
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex[:12]+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    tmp.replace(path)

def get_json(url,path,proxy=None,refresh=False):
    path=Path(path)
    if path.is_file() and not refresh:
        try:return json.loads(path.read_text(encoding='utf8'))
        except (ValueError,OSError):pass
    last=None
    for attempt in range(3):
        try:
            with request(url,proxy) as r:data=json.load(r)
            atomic_json(path,data);return data
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403,404):raise
            last=exc
        except (OSError,ValueError) as exc:last=exc
        if attempt<2:time.sleep(attempt+1)
    raise RuntimeError('HTTP JSON failed: '+url) from last

def download(url,path,*,size=None,checksum=None,proxy=None,log=print):
    """Only promote .part after length/checksum validation. Resume with validators."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    def valid(p):
        if not p.is_file() or (size is not None and p.stat().st_size!=size):return False
        if checksum:
            algorithm,want=checksum.split(':',1)
            if digest(p,algorithm)!=want:return False
        return True
    if valid(path):return path
    partial=path.with_name(path.name+'.part');sidecar=partial.with_suffix(partial.suffix+'.json')
    last=None
    for attempt in range(3):
        offset=partial.stat().st_size if partial.exists() else 0
        info={}
        if sidecar.exists():
            try:info=json.loads(sidecar.read_text(encoding='utf8'))
            except (ValueError,OSError):pass
        if info.get('url')!=url or not info.get('validator'):offset=0
        headers={}
        if offset:headers={'Range':f'bytes={offset}-','If-Range':info['validator']}
        try:
            with request(url,proxy,headers,timeout=45) as response:
                status=getattr(response,'status',200)
                if offset and status==206:
                    content_range=response.headers.get('Content-Range','')
                    if not content_range.startswith(f'bytes {offset}-'):raise OSError('Invalid resume Content-Range')
                else:offset=0
                validator=response.headers.get('ETag') or response.headers.get('Last-Modified')
                atomic_json(sidecar,{'url':url,'validator':validator})
                length=response.headers.get('Content-Length')
                expected=offset+int(length) if length else size
                with partial.open('ab' if offset else 'wb') as out:
                    for chunk in iter(lambda:response.read(1024*1024),b''):out.write(chunk)
                if expected is not None and partial.stat().st_size!=expected:raise OSError('Incomplete HTTP response')
            if not valid(partial):
                # A complete but invalid object must never be appended to again.
                partial.unlink(missing_ok=True);sidecar.unlink(missing_ok=True)
                raise OSError('Downloaded data failed expected size/checksum')
            partial.replace(path);sidecar.unlink(missing_ok=True);return path
        except (OSError,urllib.error.URLError,http.client.HTTPException) as exc:
            last=exc;log(f'下载重试 {attempt+1}/3：{path.name} ({type(exc).__name__})')
            if isinstance(exc,urllib.error.HTTPError) and exc.code in (401,403,404):raise
            if attempt<2:time.sleep(attempt+1)
    raise RuntimeError('Download failed: '+url) from last
