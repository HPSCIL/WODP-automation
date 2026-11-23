"""Local bounded parallel range cache for slow public raster endpoints."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import http.server
import json
import re
import threading
import time
from .network import request,atomic_json,digest

BLOCK=128*1024

class RangeCache:
    def __init__(self,url,folder,proxy=None):
        self.url=url;self.proxy=proxy
        self.folder=Path(folder)/hashlib.sha256(url.encode()).hexdigest()[:16]
        self.folder.mkdir(parents=True,exist_ok=True)
        with request(url,proxy,{'Range':'bytes=0-0'},timeout=30) as response:
            match=re.fullmatch(r'bytes 0-0/(\d+)',response.headers.get('Content-Range',''))
            if response.status!=206 or not match:raise OSError('Source does not support validated byte ranges')
            self.size=int(match.group(1));self.validator=response.headers.get('ETag') or response.headers.get('Last-Modified') or 'immutable-versioned-url'
            response.read(1)
        key=hashlib.sha256((self.validator+str(self.size)).encode()).hexdigest()[:12]
        self.folder=self.folder/key;self.folder.mkdir(exist_ok=True)
        identity=self.folder/'source.json'
        expected={'url':url,'validator':self.validator,'size':self.size}
        if identity.exists() and json.loads(identity.read_text(encoding='utf8'))!=expected:
            raise OSError('Range cache identity collision')
        atomic_json(identity,expected)
        self.pool=ThreadPoolExecutor(max_workers=8)
        self.mutex=threading.Lock();self.locks={};self.transferred=0;self.last_error=None

    def block(self,index):
        with self.mutex:lock=self.locks.setdefault(index,threading.Lock())
        with lock:
            path=self.folder/f'{index}.bin';meta=path.with_suffix('.json')
            start=index*BLOCK;end=min(self.size,start+BLOCK)-1
            if meta.exists() and path.exists():
                try:
                    info=json.loads(meta.read_text())
                    if path.stat().st_size==end-start+1 and digest(path)==info['sha256']:return path.read_bytes()
                except (OSError,ValueError,KeyError):pass
            last=None
            for attempt in range(3):
                try:
                    with request(self.url,self.proxy,{'Range':f'bytes={start}-{end}'},timeout=45) as r:
                        if r.status!=206 or r.headers.get('Content-Range')!=f'bytes {start}-{end}/{self.size}':raise OSError('Invalid source Content-Range')
                        data=r.read()
                    if len(data)!=end-start+1:raise OSError('Truncated source block')
                    temp=path.with_suffix('.part');temp.write_bytes(data);temp.replace(path)
                    atomic_json(meta,{'sha256':hashlib.sha256(data).hexdigest()})
                    with self.mutex:self.transferred+=len(data)
                    return data
                except Exception as exc:
                    last=exc
                    if attempt<2:time.sleep(attempt+1)
            self.last_error=last;raise OSError('Public raster block download failed') from last

    def read(self,start,end):
        ids=list(range(start//BLOCK,end//BLOCK+1))
        data=b''.join(self.pool.map(self.block,ids))
        return data[start-ids[0]*BLOCK:end-ids[0]*BLOCK+1]

    def __enter__(self):
        cache=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200);self.send_header('Content-Length',str(cache.size));self.send_header('Accept-Ranges','bytes');self.end_headers()
            def do_GET(self):
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers.get('Range',''))
                if not match:self.send_error(416,'A single byte range is required');return
                start=int(match.group(1));end=int(match.group(2)) if match.group(2) else cache.size-1
                end=min(end,cache.size-1)
                if start>end or end-start>32*1024*1024:self.send_error(416);return
                try:
                    data=cache.read(start,end)
                    self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{end}/{cache.size}')
                    self.send_header('Content-Length',str(len(data)));self.send_header('Accept-Ranges','bytes');self.end_headers();self.wfile.write(data)
                except (ConnectionError,BrokenPipeError):pass
                except Exception as exc:cache.last_error=exc;self.send_error(502,'Source block unavailable')
            def log_message(self,*args):pass
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        object_id=hashlib.sha256((self.url+self.validator).encode()).hexdigest()[:24]
        return f'http://127.0.0.1:{self.server.server_port}/{object_id}.zip'

    def __exit__(self,*args):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2);self.pool.shutdown(wait=True,cancel_futures=True)
