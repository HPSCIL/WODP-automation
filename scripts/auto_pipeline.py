"""One-request orchestration of public data acquisition and native simulation."""
from pathlib import Path
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds,transform_geom
from sources import places,terrain,buildings
from sources.network import atomic_json,digest
from sources.rasters import cache_valid,save

ROOT=Path(__file__).resolve().parents[1]

def progress_log(message):print(message,flush=True)

def parse_description(text):
    text=text.replace('，',',').replace('：',':');result={}
    for word,code in [('东北风','NE'),('东南风','SE'),('西南风','SW'),('西北风','NW'),('东风','E'),('西风','W'),('南风','S'),('北风','N')]:
        if word in text:result['wind_from']=code;break
    matches=list(re.finditer(r'[\u4e00-\u9fff]{2,20}?(?:市|区|县)',text))
    if matches:
        city=matches[0].group()
        city=re.sub(r'^(?:(?:我希望|我想|希望|请|帮我|模拟|计算|整个|全域|对|一下|研究区是|研究区为|城市是|城市为))+','',city)
        if len(matches)>1 and matches[1].start()==matches[0].end() and matches[1].group().endswith(('区','县')):city+=matches[1].group()
        if '省' in city:city=city.rsplit('省',1)[-1]
        if city:result['city']=city
    if 'city' not in result:
        for city in sorted(places.COMMON_CITIES,key=len,reverse=True):
            if city in text:result['city']=city;break
    for key,pattern in [('wind_speed_ms',r'风速[^0-9+\-]{0,6}([+\-]?\d+(?:\.\d+)?)'),('res_m',r'分辨率[^0-9+\-]{0,6}([+\-]?\d+(?:\.\d+)?)')]:
        match=re.search(pattern,text)
        if match:result[key]=float(match.group(1))
    if any(s in text for s in ['主城区','中心城区']):raise ValueError('主城区边界不明确，请指定区县组合或WGS84 bbox，不能自动当作全市')
    return result

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def make_grid(bbox,res,buffer_m):
    l,b,r,t=bbox
    zone=max(1,min(60,int((l+r)/2+180)//6+1));crs=f"EPSG:{32600+zone if (b+t)/2>=0 else 32700+zone}"
    bounds=transform_bounds('EPSG:4326',crs,l,b,r,t,densify_pts=21)
    left=math.floor((bounds[0]-buffer_m)/res)*res;bottom=math.floor((bounds[1]-buffer_m)/res)*res
    right=math.ceil((bounds[2]+buffer_m)/res)*res;top=math.ceil((bounds[3]+buffer_m)/res)*res
    width=int(round((right-left)/res));height=int(round((top-bottom)/res))
    if width<3 or height<3 or width*height>250_000_000:raise ValueError('计算网格过小或超过2.5亿像元，请调整分辨率或范围')
    return {'crs':crs,'width':width,'height':height,'transform':from_origin(left,top,res,res),'bounds':[left,bottom,right,top],
            'bbox_wgs84':list(transform_bounds(crs,'EPSG:4326',left,bottom,right,top,densify_pts=21)),'res_m':res,'buffer_m':buffer_m}

def _alive(pid):
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=wintypes.HANDLE;kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return ctypes.get_last_error()==5
    try:
        status=wintypes.DWORD();return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(status))) and status.value==259
    finally:kernel.CloseHandle(handle)

@contextmanager
def task_lock(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);lock=folder/'.task.lock'
    if lock.exists():
        try:pid=int(lock.read_text())
        except (ValueError,OSError):raise RuntimeError('任务锁损坏，请核实任务是否仍在运行：'+str(lock))
        if _alive(pid):raise RuntimeError('任务正在运行：'+str(folder))
        lock.unlink()
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    try:yield
    finally:lock.unlink(missing_ok=True)

def acquire_inputs(request,cache_dir,proxy=None,log=print):
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    if request.get('bbox'):
        bbox=list(map(float,request['bbox']))
        if len(bbox)!=4 or not(-180<=bbox[0]<bbox[2]<=180 and -90<=bbox[1]<bbox[3]<=90):raise ValueError('非法bbox范围')
        geometry={'type':'Polygon','coordinates':[[[bbox[0],bbox[1]],[bbox[2],bbox[1]],[bbox[2],bbox[3]],[bbox[0],bbox[3]],[bbox[0],bbox[1]]]]}
        place={'name':request.get('bbox_label') or '自定义范围','adcode':'bbox','geometry':geometry,'bbox_wgs84':bbox,'coord_fix':'WGS84 input'}
    else:
        place=places.resolve(request['city'],str(cache_dir/'boundaries'),log=log,proxy=proxy)
    grid=make_grid(place['bbox_wgs84'],request['res_m'],request['buffer_m'])
    key=fingerprint({'cache_version':2,'boundary':place['geometry'],'grid':dict(grid,transform=list(grid['transform'])),
                     'terrain':'FABDEM-v1.2','building_source':request['building_source']})
    folder=cache_dir/'grids'/key
    with task_lock(folder):
        meta_path=folder/'inputs.json'
        if meta_path.exists() and all(cache_valid(folder/n,grid) for n in ['dem.tif','buildings.tif','analysis_mask.tif']):
            meta=json.loads(meta_path.read_text(encoding='utf8'));meta['cache_hit']=True
            log('命中已校验数据缓存：'+key[:12]);return folder,meta
        log(f"研究区：{place['name']}；网格 {grid['width']}×{grid['height']} @ {grid['res_m']:g}米；{grid['width']*grid['height']/1e6:.2f}百万像元")
        log('将按所需窗口获取FABDEM地形和建筑数据；网络耗时取决于覆盖分片及缓存。')
        geom=transform_geom('EPSG:4326',grid['crs'],place['geometry'])
        mask=rasterize([(geom,1)],out_shape=(grid['height'],grid['width']),transform=grid['transform'],fill=0,dtype='uint8')
        if not mask.any():raise ValueError('行政边界栅格化为空')
        dtm,terrain_info=terrain.acquire(grid['bbox_wgs84'],grid,folder/'terrain-tiles',proxy,log)
        missing=np.count_nonzero((mask==1)&~np.isfinite(dtm))/int(mask.sum())
        if missing>.001:raise RuntimeError(f'FABDEM缺失覆盖了分析区{missing:.2%}，停止计算，保留已完成分片供恢复')
        bld,building_info=buildings.acquire(grid['bbox_wgs84'],grid,folder/'building-tiles',request['building_source'],proxy,log)
        if np.any((mask==1)&~np.isfinite(bld)):raise RuntimeError('建筑源存在未覆盖的分析区，不能将下载缺口当作无遮挡')
        common=np.isfinite(dtm)&np.isfinite(bld)
        dtm[~common]=np.nan;bld[~common]=np.nan
        if not np.any((mask==1)&(bld>0)):raise RuntimeError('分析区内建筑高度全为0，取数或范围可能有误')
        save(folder/'dem.tif',dtm,grid,terrain_info)
        save(folder/'buildings.tif',bld,grid,building_info)
        save(folder/'analysis_mask.tif',mask.astype('float32'),grid,{'boundary':place['name']})
        atomic_json(folder/'boundary.geojson',{'type':'Feature','geometry':place['geometry'],'properties':{'name':place['name']}})
        warnings=list(building_info.get('warnings',[]))
        if grid['res_m']<30:warnings.append('地形源约30米；更细网格属于重采样，不增加地形的实际精度。')
        if missing:warnings.append(f'分析区内地形缺失{missing:.3%}，维持NoData。')
        meta={'cache_key':key,'cache_hit':False,'place':place,'grid':dict(grid,transform=list(grid['transform'])),
              'terrain':terrain_info,'buildings':building_info,'warnings':warnings,'analysis_cells':int(mask.sum()),
              'data_sha256':{n:digest(folder/n) for n in ['dem.tif','buildings.tif','analysis_mask.tif']}}
        atomic_json(meta_path,meta);return folder,meta

def execute_engine(executable,config_path,log_path,log=print):
    start=time.perf_counter();peak=[0];stop=threading.Event()
    process=subprocess.Popen([str(executable),'run','--config',str(config_path)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace')
    def sample():
        if os.name!='nt':return
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
        kernel=ctypes.WinDLL('kernel32');psapi=ctypes.WinDLL('psapi')
        kernel.OpenProcess.restype=wintypes.HANDLE;kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(PMC),wintypes.DWORD]
        handle=kernel.OpenProcess(0x410,False,process.pid)
        if not handle:return
        try:
            while not stop.is_set():
                p=PMC();p.cb=ctypes.sizeof(p)
                if psapi.GetProcessMemoryInfo(handle,ctypes.byref(p),p.cb):peak[0]=max(peak[0],p.PeakWorkingSetSize)
                stop.wait(.5)
        finally:kernel.CloseHandle(handle)
    monitor=threading.Thread(target=sample,daemon=True);monitor.start()
    try:
        with open(log_path,'w',encoding='utf8') as output:
            for line in process.stdout:output.write(line);output.flush();log(line.rstrip())
        code=process.wait()
    except BaseException:
        process.terminate();process.wait();raise
    finally:stop.set();monitor.join(timeout=2)
    if code:raise RuntimeError(f'原生引擎失败，退出码{code}，日志：{log_path}')
    return {'elapsed_seconds':round(time.perf_counter()-start,3),'peak_working_set_bytes':peak[0] or None}

def run_auto(args,log=progress_log):
    saved={}
    if args.resume:
        if not args.out:raise ValueError('恢复任务必须指定--out')
        saved=json.loads((args.out.resolve()/'task_state.json').read_text(encoding='utf8')).get('request',{})
    parsed={k:saved[k] for k in ['city','wind_from','wind_speed_ms','res_m'] if k in saved}
    if args.describe:parsed.update(parse_description(args.describe))
    for k in ['city','wind_from','wind_speed_ms','res_m']:
        value=getattr(args,k,None)
        if value is not None:parsed[k]=value
    if args.bbox:
        bbox=[float(x) for x in args.bbox.split(',')]
    else:bbox=saved.get('bbox')
    if not bbox and not parsed.get('city'):raise ValueError('缺少研究区，请提供城市名或--bbox')
    if not parsed.get('wind_from'):raise ValueError('缺少风来向，请提供东风/西风等描述或--wind-from')
    res=float(parsed.get('res_m',30));buffer_m=float(args.buffer if args.buffer is not None else saved.get('buffer_m',1000))
    if not math.isfinite(res) or res<=0 or not math.isfinite(buffer_m) or buffer_m<0:raise ValueError('分辨率必须大于0，缓冲必须非负')
    if args.rounds is not None and args.rounds<=0:raise ValueError('轮数必须为正整数')
    if parsed.get('wind_speed_ms') is not None and (not math.isfinite(parsed['wind_speed_ms']) or parsed['wind_speed_ms']<=0):raise ValueError('风速必须为有限正数')
    request=dict(parsed,res_m=res,buffer_m=buffer_m,bbox=bbox,bbox_label=args.bbox_label or saved.get('bbox_label'),
                 building_source=args.building_source or saved.get('building_source','auto'),rounds=args.rounds or saved.get('rounds',40),
                 seed=saved.get('seed',42) if args.seed is None else args.seed,threads=args.threads if args.threads is not None else saved.get('threads'),
                 thresholds=args.thresholds if args.thresholds is not None else saved.get('thresholds'),
                 normalization_max=args.normalization_max if args.normalization_max is not None else saved.get('normalization_max'))
    if not 1<=request['rounds']<=10000:raise ValueError('轮数范围为1至10000')
    if not 0<=request['seed']<=2**64-1:raise ValueError('随机种子必须为非负64位整数')
    if request['threads'] is not None and not 1<=request['threads']<=256:raise ValueError('线程数范围为1至256')
    if request['thresholds'] is not None and (len(request['thresholds'])>32 or any(not math.isfinite(x) or x<0 for x in request['thresholds'])):
        raise ValueError('阈值必须为不超过32项的有限非负数')
    if request['normalization_max'] is not None and (not math.isfinite(request['normalization_max']) or request['normalization_max']<=0):
        raise ValueError('归一化上限必须为有限正数')
    scenario_key=fingerprint(request)
    name=re.sub(r'[^\w\-]+','_',request.get('city') or args.bbox_label or 'bbox')
    out=args.out.resolve() if args.out else Path.cwd()/'wind-results'/f'{name}-{request["wind_from"]}-{res:g}m-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}'
    if out.exists() and not args.resume:raise ValueError('输出任务目录已存在；恢复任务请加--resume，或选择新目录')
    start=time.perf_counter()
    with task_lock(out):
        state_path=out/'task_state.json';state={}
        if state_path.exists():
            state=json.loads(state_path.read_text(encoding='utf8'))
            if state.get('scenario_key')!=scenario_key:raise ValueError('恢复参数与原任务不一致，不能复用该任务目录')
        if state.get('error'):
            state.setdefault('previous_failures',[]).append({'error':state.pop('error'),'error_type':state.pop('error_type',None)})
        state.update({'scenario_key':scenario_key,'request':request,'status':'preparing','started_at':time.strftime('%Y-%m-%d %H:%M:%S')})
        atomic_json(state_path,state)
        try:
            log(f"任务：{request.get('city') or request.get('bbox_label') or bbox}，风来向{request['wind_from']}，风速输入{request.get('wind_speed_ms','未指定')}，分辨率{res:g}米。")
            cache=Path(args.cache_dir).expanduser().resolve()
            prepared,meta=acquire_inputs(request,cache,args.proxy,log)
            atomic_json(out/'data_sources.json',meta)
            shutil_copy=__import__('shutil').copy2
            shutil_copy(prepared/'boundary.geojson',out/'boundary.geojson')
            cfg={'schema_version':1,'dem':str(prepared/'dem.tif'),'terrain_kind':'DTM','buildings':str(prepared/'buildings.tif'),
                 'analysis_mask':str(prepared/'analysis_mask.tif'),'wind_from':request['wind_from'],'rounds':request['rounds'],'seed':request['seed']}
            if request.get('wind_speed_ms') is not None:cfg['wind_speed_ms']=request['wind_speed_ms']
            if request['threads'] is not None:cfg['threads']=request['threads']
            if request['thresholds'] is not None:cfg['thresholds']=request['thresholds']
            if request['normalization_max'] is not None:cfg['normalization_max']=request['normalization_max']
            engine=ROOT/'bin/wind-engine.exe';engine_sha=digest(engine)
            expected_run=fingerprint({'config':cfg,'inputs':meta['data_sha256'],'engine_sha':engine_sha})
            result_dir=Path(state['result_dir']) if state.get('result_dir') else None
            reusable=False
            if result_dir and (result_dir/'result.json').exists() and state.get('engine_run_key')==expected_run:
                r=json.loads((result_dir/'result.json').read_text(encoding='utf8'))
                reusable=r.get('status')=='success' and all((result_dir/n).exists() and digest(result_dir/n)==v for n,v in state.get('result_sha256',{}).items()) and bool(state.get('result_sha256'))
            if not reusable:
                attempt=1
                while (out/f'run-{attempt}').exists():attempt+=1
                result_dir=out/f'run-{attempt}';cfg['output_dir']=str(result_dir)
                job=out/f'job-{attempt}.json';atomic_json(job,cfg)
                state.update(status='simulating',engine_run_key=expected_run,result_dir=str(result_dir));atomic_json(state_path,state)
                perf=execute_engine(engine,job,out/f'engine-{attempt}.log',log)
                state['performance']=perf
                state['result_sha256']={p.name:digest(p) for p in result_dir.glob('*') if p.is_file() and p.suffix in ('.tif','.json')}
            else:log('复用已完成且哈希校验通过的原生计算结果')
            atomic_json(result_dir/'data_sources.json',meta)
            from render_report import render
            state['status']='rendering';atomic_json(state_path,state)
            render(result_dir,make_plots=not args.no_plot)
            state.update(status='success',elapsed_seconds=round(time.perf_counter()-start,3),data_cache_hit=meta['cache_hit'])
            atomic_json(state_path,state)
            atomic_json(out/'result_pointer.json',{'result_dir':str(result_dir),'report':str(result_dir/'report.md'),'request':request,'performance':state.get('performance')})
            log('完成：'+str(result_dir/'report.md'));return 0
        except BaseException as exc:
            state.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error_type=type(exc).__name__,error=str(exc))
            atomic_json(state_path,state);raise
