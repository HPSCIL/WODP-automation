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
        if args.config or args.dem or args.buildings or args.terrain_only or args.mask:
            ap.error('自动取数模式与本地输入/配置模式不能混用')
        from auto_pipeline import run_auto
        return run_auto(args)
    if args.resume:ap.error('--resume用于自动取数任务，请提供原始描述或--city/--bbox')
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
    with tempfile.TemporaryDirectory(prefix='wind_job_') as task:
        job=Path(task)/'job.json';job.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8')
        process=subprocess.run([str(executable),'run','--config',str(job)])
    if process.returncode:return process.returncode
    output=Path(cfg['output_dir'])
    (output/'job.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8')
    from render_report import render
    render(output,make_plots=not args.no_plot)
    print('成果目录：'+str(output))
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,RuntimeError) as exc:
        print('输入或文件错误：'+str(exc),file=sys.stderr);sys.exit(2)
