"""Invoke the bundled native engine. No model implementation lives here."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main(argv=None):
    ap=argparse.ArgumentParser(description='Run a native wind-corridor screening scenario')
    ap.add_argument('--config',type=Path)
    ap.add_argument('--describe',help='一句话描述城市、风来向、风速和分辨率')
    ap.add_argument('--city')
    ap.add_argument('--bbox',help='WGS84 minlon,minlat,maxlon,maxlat')
    ap.add_argument('--bbox-label')
    ap.add_argument('--res',dest='res_m',type=float)
    ap.add_argument('--buffer',type=float)
    ap.add_argument('--building-source',choices=['auto','3dglobfp','cnbh'])
    ap.add_argument('--cache-dir',default='~/.wind-corridor-native/cache')
    ap.add_argument('--proxy')
    ap.add_argument('--resume',action='store_true')
    ap.add_argument('--dem',type=Path)
    ap.add_argument('--crs',help='本地数据准备的目标米制投影')
    ap.add_argument('--building-layer');ap.add_argument('--height-field');ap.add_argument('--height-unit',choices=['m','floors'])
    ap.add_argument('--floor-height',type=float);ap.add_argument('--confirm-building-coverage',action='store_true',default=None)
    modes=ap.add_mutually_exclusive_group()
    modes.add_argument('--buildings',type=Path)
    modes.add_argument('--terrain-only',action='store_true')
    ap.add_argument('--wind-from',choices=['N','NE','E','SE','S','SW','W','NW'])
    ap.add_argument('--wind-speed',dest='wind_speed_ms',type=float)
    ap.add_argument('--rounds',type=int)
    ap.add_argument('--seed',type=int)
    ap.add_argument('--threads',type=int)
    ap.add_argument('--mask',type=Path)
    ap.add_argument('--thresholds',nargs='+',type=float)
    ap.add_argument('--normalization-max',type=float)
    ap.add_argument('--out',type=Path)
    ap.add_argument('--no-plot',action='store_true')
    args=ap.parse_args(argv)
    if args.describe or args.city or args.bbox or (args.resume and args.out):
        if args.config or args.dem or args.mask or args.crs:
            ap.error('自动范围可搭配--buildings；本地DEM/配置/掩膜/目标CRS请使用本地模式')
        if args.building_source and (args.buildings or args.terrain_only):ap.error('本地建筑/纯地形与在线建筑源不能同时指定')
        from auto_pipeline import run_auto
        return run_auto(args)
    if args.resume:ap.error('--resume用于自动取数任务，请提供原始描述或--city/--bbox')
    vector_options=any(v is not None for v in [args.building_layer,args.height_field,args.height_unit,args.floor_height,args.confirm_building_coverage])
    if vector_options and not args.buildings:ap.error('建筑字段/单位参数需要同时提供--buildings')
    cfg={};base=Path.cwd()
    if args.config:
        base=args.config.resolve().parent
        cfg=json.loads(args.config.read_text(encoding='utf-8-sig'))
        if not isinstance(cfg,dict):ap.error('Configuration must be a JSON object')
    for key in ('dem','buildings','analysis_mask','output_dir'):
        if key in cfg:
            p=Path(cfg[key]);cfg[key]=str((base/p).resolve())
    for key,value in [('dem',args.dem),('buildings',args.buildings),('analysis_mask',args.mask),('output_dir',args.out)]:
        if value is not None:cfg[key]=str(value.resolve())
    for key in ('wind_from','wind_speed_ms','rounds','seed','threads','thresholds','normalization_max'):
        value=getattr(args,key)
        if value is not None:cfg[key]=value
    if args.terrain_only:cfg.pop('buildings',None);cfg['terrain_only']=True
    if args.buildings:cfg.pop('terrain_only',None)
    cfg.setdefault('schema_version',1)
    cfg.setdefault('terrain_kind','DTM')
    cfg.setdefault('rounds',40);cfg.setdefault('seed',42)
    if not cfg.get('dem') or not cfg.get('wind_from') or not cfg.get('output_dir'):
        ap.error('Provide --dem, --wind-from and --out (or a complete --config)')
    executable=ROOT/'bin/wind-engine.exe'
    if not executable.is_file():ap.error('Bundled native engine is missing')
    preparation=None
    from local_buildings import is_vector
    local_vector=cfg.get('buildings') and is_vector(cfg['buildings'])
    if args.crs or (args.res_m is not None) or local_vector:
        if cfg.get('analysis_mask'):ap.error('自动本地对齐暂不重采样分析掩膜；请先prepare_data再传入同网格--mask')
        if Path(cfg['output_dir']).exists():ap.error('输出目录已存在，请选择新目录')
        from prepare_data import prepare
        prepared=Path(cfg['output_dir']).with_name(Path(cfg['output_dir']).name+'.inputs')
        prepare(cfg['dem'],cfg.get('buildings'),prepared,args.res_m,args.crs,building_layer=args.building_layer,
                height_field=args.height_field,height_unit=args.height_unit,floor_height=args.floor_height,
                confirm_building_coverage=bool(args.confirm_building_coverage))
        cfg['dem']=str(prepared/'dem.tif')
        if cfg.get('buildings'):cfg['buildings']=str(prepared/'buildings.tif')
        preparation=prepared/'preparation.json'
    elif vector_options:ap.error('高度字段和图层选项仅适用于矢量；高度栅格直接使用米制相对高度')
    with tempfile.TemporaryDirectory(prefix='wind_job_') as task:
        job=Path(task)/'job.json';job.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8')
        process=subprocess.run([str(executable),'run','--config',str(job)])
    if process.returncode:return process.returncode
    output=Path(cfg['output_dir'])
    (output/'job.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8')
    if preparation:
        __import__('shutil').copy2(preparation,output/'preparation.json')
    from render_report import render
    render(output,make_plots=not args.no_plot)
    print('成果目录：'+str(output))
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,RuntimeError) as exc:
        print('输入或文件错误：'+str(exc),file=sys.stderr);sys.exit(2)
