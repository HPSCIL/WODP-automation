"""Public GIS adapter for local relative building heights, without simulation code."""
from pathlib import Path
import math
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject,transform_bounds,transform_geom
from sources.network import digest

VECTOR_SUFFIXES={'.shp','.gpkg','.geojson','.json'}
HEIGHT_NAMES={'height','height_m','building:height','楼高','建筑高度'}
FLOOR_NAMES={'floors','levels','building:levels','层数','楼层','楼层数'}

def is_vector(path):return Path(path).suffix.lower() in VECTOR_SUFFIXES

def source_identity(path):
    path=Path(path).expanduser().resolve()
    if not path.is_file():raise ValueError('本地建筑文件不存在：'+str(path))
    files=[path]
    if path.suffix.lower()=='.shp':
        siblings={p.suffix.lower():p for p in path.parent.iterdir() if p.stem==path.stem and p.is_file()}
        missing=[ext for ext in ['.shp','.shx','.dbf','.prj'] if ext not in siblings]
        if missing:raise ValueError('SHP缺少配套文件：'+', '.join(missing))
        files=[siblings[ext] for ext in ['.shp','.shx','.dbf','.prj','.cpg','.qix','.sbn','.sbx'] if ext in siblings]
    if path.suffix.lower()=='.gpkg' and any(Path(str(path)+s).exists() for s in ['-wal','-shm']):
        raise ValueError('GPKG仍有数据库事务文件，请关闭编辑软件并保存后重试')
    return {'path':str(path),'files_sha256':{p.name:digest(p) for p in files}}

def choose_height(fields,height_field=None,height_unit=None,floor_height=None):
    if height_field:
        if height_field not in fields:raise ValueError('指定高度字段不存在；可用字段：'+', '.join(fields))
        selected=height_field
    else:
        candidates=[f for f in fields if f.casefold() in HEIGHT_NAMES|FLOOR_NAMES]
        if len(candidates)!=1:raise ValueError('高度字段不唯一或未识别，请用--height-field指定；候选：'+', '.join(candidates or fields))
        selected=candidates[0]
    unit=height_unit
    if unit is None:
        if selected.casefold() in HEIGHT_NAMES:unit='m'
        elif selected.casefold() in FLOOR_NAMES:unit='floors'
        else:raise ValueError('字段单位不明确，请指定--height-unit m或floors')
    if unit not in ('m','floors'):raise ValueError('高度单位仅支持m或floors')
    if unit=='floors':
        if floor_height is None or not math.isfinite(floor_height) or floor_height<=0:
            raise ValueError('楼层换算必须提供有限正数--floor-height（每层米数），不会自动假设层高')
    elif floor_height is not None:raise ValueError('--floor-height仅用于floors单位')
    return selected,unit,float(floor_height) if unit=='floors' else 1.

def on_grid(path,grid,*,layer=None,height_field=None,height_unit=None,floor_height=None,confirm_coverage=False,log=print):
    identity=source_identity(path);path=Path(identity['path'])
    info={'name':'本地建筑高度','url':path.as_uri(),'license':'用户提供，许可由用户核实','source':identity,'warnings':[]}
    target=np.full((grid['height'],grid['width']),np.nan,dtype='float32')
    if not is_vector(path):
        if any(v is not None for v in [layer,height_field,height_unit,floor_height]) or confirm_coverage:
            raise ValueError('图层/高度字段/楼层换算/矢量覆盖声明仅适用于建筑矢量')
        with rasterio.open(path) as src:
            if src.count!=1 or not src.crs:raise ValueError('建筑高度栅格必须单波段并声明CRS')
            reproject(rasterio.band(src,1),target,src_transform=src.transform,src_crs=src.crs,src_nodata=src.nodata,
                      dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=np.nan,resampling=Resampling.max)
        if np.any(np.isfinite(target)&(target<0)):raise ValueError('建筑相对高度不得为负数')
        info.update(semantics='Relative building heights in metres; maximum resampling.',resampling='max')
    else:
        try:
            import pyogrio
            from pyogrio.raw import read
            import shapely
            from shapely.geometry import mapping,shape
        except ImportError as exc:raise ValueError('本地建筑矢量需要requirements-vector.txt中的pyogrio和shapely；请自行准备后重试，技能不会安装。') from exc
        layers=[str(v[0]) for v in pyogrio.list_layers(path)]
        if layer is None:
            if len(layers)!=1:raise ValueError('存在多个图层，请用--building-layer指定：'+', '.join(layers))
            layer=layers[0]
        if layer not in layers:raise ValueError('建筑图层不存在：'+layer+'；可用：'+', '.join(layers))
        details=pyogrio.read_info(path,layer=layer,force_total_bounds=True)
        if not details['crs']:raise ValueError('建筑矢量缺少CRS，不能猜测坐标系')
        field,unit,scale=choose_height(list(details['fields']),height_field,height_unit,floor_height)
        source_bbox=transform_bounds(grid['crs'],details['crs'],*grid['bounds'],densify_pts=21)
        log(f'本地建筑：{path.name}；图层{layer}；字段{field}；单位{unit}；换算系数{scale:g}')
        log('需覆盖的计算范围（含缓冲）：'+str(grid['bounds'])+'；'+str(grid['crs']))
        if not confirm_coverage:
            raise ValueError('建筑矢量无法仅凭外包矩形证明覆盖完整。请确认数据包含整个计算范围及外围缓冲内全部建筑后，提供--confirm-building-coverage；否则请补充数据或缩小范围。')
        target.fill(0);used=0;offset=0
        footprint=shape({'type':'Polygon','coordinates':[[[grid['bounds'][0],grid['bounds'][1]],[grid['bounds'][2],grid['bounds'][1]],[grid['bounds'][2],grid['bounds'][3]],[grid['bounds'][0],grid['bounds'][3]],[grid['bounds'][0],grid['bounds'][1]]]]})
        while True:
            _,_,wkb,columns=read(path,layer=layer,columns=[field],bbox=source_bbox,skip_features=offset,max_features=5000,force_2d=True)
            if wkb is None or len(wkb)==0:break
            pairs=[]
            for blob,value in zip(wkb,columns[0]):
                try:geometry=shapely.from_wkb(blob) if blob is not None else None
                except shapely.errors.GEOSException as exc:raise ValueError('建筑几何无法读取，请修复源文件') from exc
                if geometry is None or geometry.is_empty or geometry.geom_type not in ('Polygon','MultiPolygon') or not geometry.is_valid:
                    raise ValueError('建筑包含空、非面或无效几何；请修复源数据后重试，不会自动跳过')
                projected=transform_geom(details['crs'],grid['crs'],mapping(geometry))
                projected_shape=shape(projected)
                if not projected_shape.is_valid:raise ValueError('建筑投影转换后几何无效，请核实源坐标系')
                if not footprint.intersects(projected_shape):continue
                try:height=float(value)*scale
                except (ValueError,TypeError):height=float('nan')
                if not math.isfinite(height) or height<=0 or height>np.finfo('float32').max:
                    raise ValueError('选中建筑存在缺失、非正或非法高度；请修正字段/单位或补充真实高度，不会填零或中位数')
                pairs.append((projected,height));used+=1
            if pairs:
                pairs.sort(key=lambda pair:pair[1])
                part=rasterize(pairs,out_shape=target.shape,transform=grid['transform'],fill=0,dtype='float32',all_touched=False)
                np.maximum(target,part,out=target)
            offset+=len(wkb)
            if len(wkb)<5000:break
        if not used or not np.any(target>0):raise ValueError('计算范围内没有有效建筑像元；请检查CRS、范围与分辨率，纯地形请明确指定--terrain-only')
        info.update(layer=layer,height_field=field,height_unit=unit,height_scale_to_m=scale,features_used=used,
                    source_bounds=list(details['total_bounds']),source_crs=details['crs'],coverage_declared_complete=True,
                    semantics='True footprint polygons, pixel-centre rasterization, maximum overlapping relative height; user declares full coverage including buffer.')
        if unit=='floors':info['warnings'].append(f'建筑高度由楼层数×{scale:g}米估算，非实测高度。')
    if source_identity(path)!=identity:raise ValueError('建筑源文件在读取期间发生变化，请停止编辑后重试')
    return target,info
