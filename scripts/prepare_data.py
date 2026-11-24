"""Generic raster alignment. Requires true DTM and relative building heights."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import calculate_default_transform,reproject
from local_buildings import on_grid

def prepare(dem,buildings,output,res=None,target_crs=None,*,building_layer=None,height_field=None,height_unit=None,floor_height=None,confirm_building_coverage=False):
    output=Path(output)
    if output.exists():raise ValueError('Preparation directory must be new')
    if res is not None and (not math.isfinite(res) or res<=0):raise ValueError('Resolution must be positive')
    with rasterio.open(dem) as src:
        if src.count!=1 or not src.crs:raise ValueError('DTM must be single-band with a declared CRS')
        if target_crs:crs=CRS.from_user_input(target_crs)
        elif src.crs.is_projected and src.crs.linear_units=='metre':crs=src.crs
        else:raise ValueError('Specify --crs with an appropriate metre-based projected CRS')
        if not crs.is_projected or crs.linear_units!='metre':raise ValueError('Target CRS must be projected in metres')
        if src.crs==crs and res is None:
            res=abs(src.res[0])
        tr,w,h=calculate_default_transform(src.crs,crs,src.width,src.height,*src.bounds,resolution=res)
        if w<3 or h<3 or w*h>250_000_000:raise ValueError('Target grid too small or too large')
        a=np.full((h,w),np.nan,dtype='float32')
        reproject(rasterio.band(src,1),a,src_transform=src.transform,src_crs=src.crs,
                  src_nodata=src.nodata,dst_transform=tr,dst_crs=crs,dst_nodata=np.nan,resampling=Resampling.bilinear)
    b=None;building_info=None
    if buildings:
        from rasterio.transform import array_bounds
        grid={'width':w,'height':h,'crs':str(crs),'transform':tr,'bounds':list(array_bounds(h,w,tr))}
        b,building_info=on_grid(buildings,grid,layer=building_layer,height_field=height_field,height_unit=height_unit,
                               floor_height=floor_height,confirm_coverage=confirm_building_coverage)
        if np.any(np.isfinite(a)&~np.isfinite(b)):raise ValueError('Building dataset does not cover the DTM; missing heights must not be filled with zero')
        if np.any(np.isfinite(b)&(b<0)):raise ValueError('Negative building heights')
        if not np.any(np.isfinite(a)&(b>0)):raise ValueError('No positive building heights; explicitly choose a terrain-only scenario instead')
    output.parent.mkdir(parents=True,exist_ok=True);output.mkdir()
    profile=dict(driver='GTiff',width=w,height=h,count=1,dtype='float32',crs=crs,transform=tr,nodata=np.nan,compress='lzw')
    for name,values in [('dem.tif',a),('buildings.tif',b)]:
        if values is not None:
            with rasterio.open(output/name,'w',**profile) as dst:dst.write(values,1)
    inputs={}
    for name,path in [('dem',dem),('buildings',buildings)]:
        if path:
            digest=hashlib.sha256()
            with open(path,'rb') as source:
                for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
            inputs[name]={'path':str(Path(path).resolve()),'sha256':digest.hexdigest()}
    (output/'preparation.json').write_text(json.dumps({'terrain_kind':'DTM declared by user','inputs':inputs,'crs':str(crs),'resolution_m':tr.a,'width':w,'height':h,'building_resampling':'max','dem_resampling':'bilinear','building_preparation':building_info},ensure_ascii=False,indent=2),encoding='utf8')
    return output

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dem',type=Path,required=True);ap.add_argument('--buildings',type=Path)
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--res',type=float);ap.add_argument('--crs')
    ap.add_argument('--building-layer');ap.add_argument('--height-field');ap.add_argument('--height-unit',choices=['m','floors'])
    ap.add_argument('--floor-height',type=float);ap.add_argument('--confirm-building-coverage',action='store_true')
    args=ap.parse_args()
    try:print(prepare(args.dem,args.buildings,args.out,args.res,args.crs,building_layer=args.building_layer,height_field=args.height_field,height_unit=args.height_unit,floor_height=args.floor_height,confirm_building_coverage=args.confirm_building_coverage))
    except (ValueError,OSError,ImportError) as exc:ap.exit(2,'数据准备失败：'+str(exc)+'\n')
