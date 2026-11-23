"""FABDEM v1.2 provider: public COG members inside HTTP-range-readable ZIPs."""
import math
from .rasters import mosaic_tiles

BASE='https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn'
SOURCE={'name':'FABDEM v1.2','kind':'DTM (forest/building bias removed)',
        'url':'https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn',
        'license':'CC BY-NC-SA 4.0; non-commercial use only',
        'citation':'Hawker et al. (2022), A 30 m global map of elevation with forests and buildings removed, Environmental Research Letters 17, 024016.',
        'attribution':'FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.',
        'native_resolution_m_approx':30,'vertical_datum':'EGM2008','coverage':[-180,-60,180,80]}

def corner(lat,lon):return f"{'N' if lat>=0 else 'S'}{abs(lat):02d}{'E' if lon>=0 else 'W'}{abs(lon):03d}"

def tiles(bbox):
    l,b,r,t=bbox
    if not (-180<=l<r<=180 and -60<=b<t<=80):raise ValueError('FABDEM request outside supported coverage')
    result=[]
    # Account for point-centred source bounds and reprojection interpolation at integer tile seams.
    for lat in range(math.floor(b-1/3600),math.ceil(t+1/3600)):
        for lon in range(math.floor(l-1/3600),math.ceil(r+1/3600)):
            if lat>=80 or lat< -60 or lon< -180 or lon>=180:continue
            by=math.floor(lat/10)*10;bx=math.floor(lon/10)*10
            bundle=f'{corner(by,bx)}-{corner(by+10,bx+10)}_FABDEM_V1-2.zip'
            name=f'{corner(lat,lon)}_FABDEM_V1-2.tif'
            result.append({'id':corner(lat,lon),'url':f'/vsizip//vsicurl/{BASE}/{bundle}/{name}',
                           'bundle_url':f'{BASE}/{bundle}','tile':name})
    return result

def acquire(bbox,grid,cache_dir,proxy=None,log=print):
    data,records=mosaic_tiles(tiles(bbox),grid,cache_dir,proxy=proxy,log=log)
    return data,dict(SOURCE,tiles=records)
