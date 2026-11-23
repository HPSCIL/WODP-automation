"""Public raster data acquisition and exact-grid cache validation."""
from pathlib import Path
import math
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject,transform_bounds
from rasterio.windows import Window
from .network import digest,atomic_json,gdal_proxy_options

def clear_remote_cache():
    # A failed curl block can remain cached in the process after a RasterioIOError.
    # Clear only between closed datasets so a retry makes a fresh range request.
    import ctypes
    libraries=Path(rasterio.__file__).parent.parent/'rasterio.libs'
    matches=list(libraries.glob('gdal*.dll'))
    if matches:
        library=ctypes.CDLL(str(matches[0]));library.VSICurlClearCache.restype=None;library.VSICurlClearCache()

def profile(grid):
    return dict(driver='GTiff',height=grid['height'],width=grid['width'],count=1,dtype='float32',
                crs=grid['crs'],transform=grid['transform'],nodata=np.nan,compress='lzw',tiled=True)

def cache_valid(path,grid):
    path=Path(path);meta=path.with_suffix('.json')
    try:
        import json
        m=json.loads(meta.read_text(encoding='utf8'))
        if digest(path)!=m['sha256']:return False
        with rasterio.open(path) as src:
            return src.shape==(grid['height'],grid['width']) and src.crs==rasterio.crs.CRS.from_user_input(grid['crs']) and src.transform==grid['transform']
    except (OSError,ValueError,KeyError,rasterio.errors.RasterioError):return False

def save(path,values,grid,metadata):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.part.tif')
    with rasterio.open(temp,'w',**profile(grid)) as dst:dst.write(values.astype('float32'),1)
    temp.replace(path)
    atomic_json(path.with_suffix('.json'),dict(metadata,sha256=digest(path)))

def read_on_grid(url,grid,*,buildings=False,proxy=None,log=print,range_cache_dir=None):
    """Read native-resolution strips so NaN background is handled before aggregation."""
    target=np.full((grid['height'],grid['width']),np.nan,dtype='float32')
    options={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','CPL_VSIL_CURL_USE_HEAD':'NO',
             'GDAL_HTTP_TIMEOUT':'120','GDAL_HTTP_MAX_RETRY':'2','VSI_CACHE':'TRUE','VSI_CACHE_SIZE':'33554432'}
    if '/vsicurl/http://127.0.0.1:' in url:
        options.update(CPL_VSIL_CURL_USE_HEAD='YES',GDAL_HTTP_PROXY='',GDAL_HTTP_MAX_RETRY='0')
    if 'http' in url:options.update(gdal_proxy_options(url[url.index('http'):],proxy))
    if range_cache_dir is not None and url.startswith('/vsizip//vsicurl/'):
        from .range_cache import RangeCache
        remote,member=url[len('/vsizip//vsicurl/'):].split('.zip/',1);remote+='.zip'
        with RangeCache(remote,range_cache_dir,proxy) as local:
            # Local loopback is direct; upstream proxy handling is owned by RangeCache.
            with rasterio.Env(GDAL_HTTP_PROXY='',GDAL_HTTP_MAX_RETRY='0'):
                return read_on_grid('/vsizip//vsicurl/'+local+'/'+member,grid,buildings=buildings,log=log)
    if range_cache_dir is not None and url.startswith(('/vsicurl/https://','/vsicurl/http://')):
        from .range_cache import RangeCache
        remote=url[len('/vsicurl/'):]
        with RangeCache(remote,range_cache_dir,proxy) as local:
            with rasterio.Env(GDAL_HTTP_PROXY='',GDAL_HTTP_MAX_RETRY='0'):
                return read_on_grid('/vsicurl/'+local.replace('.zip','.tif'),grid,buildings=buildings,log=log)
    with rasterio.Env(**options):
        with rasterio.open(url) as src:
            bb=transform_bounds(grid['crs'],src.crs,*grid['bounds'],densify_pts=21)
            l=max(bb[0],src.bounds.left);b=max(bb[1],src.bounds.bottom);r=min(bb[2],src.bounds.right);t=min(bb[3],src.bounds.top)
            if l>=r or b>=t:return target
            window=src.window(l,b,r,t)
            col=max(0,int(math.floor(window.col_off))-2);row=max(0,int(math.floor(window.row_off))-2)
            right=min(src.width,int(math.ceil(window.col_off+window.width))+2)
            bottom=min(src.height,int(math.ceil(window.row_off+window.height))+2)
            step=max(1,int(8_000_000/max(1,right-col)))
            for y in range(row,bottom,step):
                y0=max(row,y-1);y1=min(bottom,y+step+1)
                win=Window(col,y0,right-col,y1-y0)
                a=src.read(1,window=win,masked=True).astype('float32').filled(np.nan)
                if buildings:
                    # CNBH uses NaN background within a successfully read tile. Failed tiles never enter here.
                    a=np.nan_to_num(a,nan=0.,posinf=0.,neginf=0.);np.maximum(a,0,out=a)
                reproject(a,target,src_transform=src.window_transform(win),src_crs=src.crs,src_nodata=np.nan,
                          dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=np.nan,
                          resampling=Resampling.average if buildings else Resampling.bilinear,init_dest_nodata=False)
    return target

def mosaic_tiles(items,grid,cache_dir,*,buildings=False,proxy=None,log=print):
    import json
    cache_dir=Path(cache_dir);merged=np.full((grid['height'],grid['width']),np.nan,dtype='float32');records=[]
    for number,item in enumerate(items,1):
        path=cache_dir/(item['id']+'.tif');cached=cache_valid(path,grid)
        log(f"数据分片 {number}/{len(items)}：{item['id']} {'缓存' if cached else '下载窗口'}")
        if not cached:
            values=None;last=None
            for attempt in range(3):
                try:values=read_on_grid(item['url'],grid,buildings=buildings,proxy=proxy,log=log,range_cache_dir=cache_dir.parent/'http-blocks');break
                except Exception as exc:
                    last=exc
                    log(f"读取重试 {attempt+1}/3：{item['id']} ({type(exc).__name__}: {str(exc)[:240]})")
                    clear_remote_cache()
            if values is None:raise RuntimeError('Required data tile failed: '+item['id']) from last
            save(path,values,grid,item)
        with rasterio.open(path) as src:a=src.read(1)
        take=np.isfinite(a)
        if buildings:
            replace=take&(~np.isfinite(merged)|(a>merged));merged[replace]=a[replace]
        else:merged[take]=a[take]
        records.append(dict(item,cached=cached,sha256=digest(path)))
    return merged,records
