"""Automatic public building datasets. No wind-model code is included."""
from pathlib import Path
import json
import math
import re
import zipfile
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_bounds,transform_geom
from .network import get_json,download,digest,atomic_json,gdal_proxy_options
from .rasters import mosaic_tiles

CNBH_API='https://zenodo.org/api/records/7923866'
PARTS={1:28879733,2:28881749,3:28882700,4:28889813,5:28890593,
       6:28891631,7:28903454,8:28903853,9:28904453,10:28906499}
ASSETS=Path(__file__).resolve().parents[1]/'assets'

def cnbh(bbox,grid,cache_dir,proxy=None,log=print):
    cache_dir=Path(cache_dir)
    index=get_json(CNBH_API,cache_dir.parent/'cnbh-index.json',proxy)
    files={f['key']:f for f in index['files']}
    candidates=[]
    for y in range(math.floor(bbox[1])-1,math.ceil(bbox[3])+2):
        for x in range(math.floor(bbox[0])-1,math.ceil(bbox[2])+2):
            key=f'CNBH10m_X{x:03d}Y{y:02d}.tif'
            if key in files:candidates.append(key)
    if not candidates:raise RuntimeError('CNBH has no candidate tiles for this location')
    known_path=cache_dir.parent/'cnbh-headers.json'
    try:known=json.loads(known_path.read_text(encoding='utf8'))
    except (OSError,ValueError):known={}
    options={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','CPL_VSIL_CURL_USE_HEAD':'NO','GDAL_HTTP_TIMEOUT':'20','GDAL_HTTP_MAX_RETRY':'1'}
    options.update(gdal_proxy_options(CNBH_API,proxy))
    items=[];errors=[]
    for key in candidates:
        url=f'/vsicurl/{CNBH_API}/files/{key}/content'
        if key not in known:
            try:
                with rasterio.Env(**options):
                    with rasterio.open(url) as src:bb=transform_bounds(src.crs,'EPSG:4326',*src.bounds)
                known[key]={'bounds':list(bb)};atomic_json(known_path,known)
            except Exception as exc:errors.append(key+': '+type(exc).__name__);continue
        bb=known[key]['bounds']
        if bb[2]<=bbox[0] or bb[0]>=bbox[2] or bb[3]<=bbox[1] or bb[1]>=bbox[3]:continue
        f=files[key]
        items.append({'id':key[:-4],'url':url,'source_url':f'{CNBH_API}/files/{key}/content',
                      'checksum':f.get('checksum'),'size_bytes':f.get('size'),'bounds_wgs84':bb})
    if not items:raise RuntimeError('CNBH tiles could not be discovered: '+str(errors))
    data,records=mosaic_tiles(items,grid,cache_dir/'warped',buildings=True,proxy=proxy,log=log)
    return data,{'name':'CNBH-10m 2020','url':'https://zenodo.org/records/7923866',
                 'license':'CC BY 4.0','native_resolution_m':10,
                 'semantics':'Regional averaged building-height product, not measured individual building heights; NaN background inside read tiles is treated as non-building.',
                 'warnings':['Average-height building data can underestimate tall-building obstruction.'],
                 'tiles':records,'discovery_errors':errors}

def _grid_hits(bbox):
    import shapefile
    hits=[]
    with shapefile.Reader(str(ASSETS/'world_grid.shp'),encoding='utf_8') as reader:
        for sr in reader.iterShapeRecords():
            b=sr.shape.bbox
            if b[2]<=bbox[0] or b[0]>=bbox[2] or b[3]<=bbox[1] or b[1]>=bbox[3]:continue
            hits.append(int(sr.record[0]))
    return hits

def _part(gid):
    for part,limit in [(1,400),(2,699),(3,899),(4,1299),(5,1699),(6,1799),(7,1899),(8,1999),(9,2299),(10,2554)]:
        if gid<=limit:return part
    raise ValueError('Unknown global building grid')

def _local_shapes(gid,info,cache_dir,proxy,log):
    root=Path(cache_dir);folder=root/f'g{gid}';complete=folder/'complete.json'
    try:
        meta=json.loads(complete.read_text(encoding='utf8'))
        if meta['archive_md5']==info['computed_md5'] and all(digest(folder/p)==h for p,h in meta['files'].items()):
            return sorted(folder.rglob('*.shp'))
    except (OSError,ValueError,KeyError):pass
    archive=download(info.get('download_url') or f"https://ndownloader.figshare.com/files/{info['id']}",
                     root/info['name'],size=info['size'],checksum='md5:'+info['computed_md5'],proxy=proxy,log=log)
    folder.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            target=(folder/entry.filename).resolve()
            if not target.is_relative_to(folder.resolve()):raise ValueError('Unsafe ZIP member path')
        z.extractall(folder)
    shapes=sorted(folder.rglob('*.shp'))
    if not shapes:raise RuntimeError('Downloaded building archive has no shapefiles')
    for p in shapes:
        for ext in ['.dbf','.shx']:
            if not p.with_suffix(ext).is_file():raise RuntimeError('Incomplete shapefile components')
    hashes={p.relative_to(folder).as_posix():digest(p) for p in folder.rglob('*') if p.is_file() and p!=complete}
    atomic_json(complete,{'archive_md5':info['computed_md5'],'files':hashes})
    return shapes

def _rasterize_shapes(path,bbox,grid,arr,log):
    import shapefile
    total=used=invalid=0;geometries=[];heights=[]
    def flush():
        if not geometries:return
        projected=transform_geom('EPSG:4326',grid['crs'],geometries)
        pairs=sorted(zip(projected,heights),key=lambda v:v[1])
        tile=rasterize(pairs,out_shape=arr.shape,transform=grid['transform'],fill=0,dtype='float32',all_touched=False)
        np.maximum(arr,tile,out=arr);geometries.clear();heights.clear()
    with shapefile.Reader(str(path),encoding='latin1') as reader:
        fields=[f[0] for f in reader.fields[1:]]
        name=next((f for f in fields if f.lower()=='height'),None)
        if not name:raise ValueError('Building source has no Height field')
        for sr in reader.iterShapeRecords(fields=[name],bbox=bbox):
            total+=1;v=sr.record[0]
            if v is None or not math.isfinite(float(v)) or float(v)<=0:invalid+=1;continue
            geometries.append(sr.shape.__geo_interface__);heights.append(float(v));used+=1
            if len(geometries)>=5000:flush()
        flush()
    log(f'单体建筑栅格化：{used:,} 栋，跳过无效高度 {invalid:,} 栋')
    return {'n_selected':total,'n_used':used,'invalid_height':invalid}

def globfp(bbox,grid,cache_dir,proxy=None,log=print):
    cache_dir=Path(cache_dir);hits=_grid_hits(bbox)
    if not hits:raise RuntimeError('3D-GloBFP has no intersecting grids')
    indexes={}
    for part in sorted({_part(gid) for gid in hits}):
        data=get_json(f'https://api.figshare.com/v2/articles/{PARTS[part]}',cache_dir/f'part-{part}.json',proxy)
        for f in data.get('files',[]):
            gid=f['name'].split('_')[0]
            if gid.isdigit():indexes[int(gid)]=f
    data=np.zeros((grid['height'],grid['width']),dtype='float32');records=[]
    for gid in hits:
        if gid not in indexes:raise RuntimeError(f'Required 3D-GloBFP grid {gid} missing from source index')
        f=indexes[gid];log(f"建筑网格 {gid}：{f['name']}（{f['size']/1e6:.1f} MB）")
        stats=[]
        for shp in _local_shapes(gid,f,cache_dir,proxy,log):stats.append(_rasterize_shapes(shp,bbox,grid,data,log))
        records.append({'grid':gid,'name':f['name'],'size_bytes':f['size'],'md5':f['computed_md5'],'stats':stats})
    return data,{'name':'3D-GloBFP 2020','url':'https://doi.org/10.5281/zenodo.11397014','license':'CC BY 4.0',
                 'semantics':'Individual footprint polygons rasterized by pixel-centre coverage with maximum overlapping relative height.',
                 'warnings':['Estimated building heights can substantially underestimate very tall buildings; not a measured high-rise dataset.'],
                 'tiles':records}

def acquire(bbox,grid,cache_dir,source='auto',proxy=None,log=print):
    if source in ('auto','3dglobfp'):
        try:return globfp(bbox,grid,Path(cache_dir)/'3dglobfp',proxy,log)
        except Exception as exc:
            if source=='3dglobfp':raise
            log(f'3D-GloBFP取数不可用，切换CNBH：{type(exc).__name__}')
            fallback=str(exc)
    elif source!='cnbh':raise ValueError('Unsupported building source')
    result,info=cnbh(bbox,grid,Path(cache_dir)/'cnbh',proxy,log)
    if source=='auto':info['fallback_reason']=fallback
    return result,info
