"""Check the user-selected environment and launch only when it is ready."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if '--help' in argv or '-h' in argv:
        print('环境检查：python scripts/start.py --check-env（只检查，不安装、不模拟）',flush=True)
        return subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/run_wind.py'),'--help']).returncode
    parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    parser.add_argument('--check-env','--check-only',action='store_true',dest='check_only')
    parser.add_argument('--out',type=Path);parser.add_argument('--cache-dir');parser.add_argument('--config',type=Path);parser.add_argument('--proxy')
    parser.add_argument('--buildings',type=Path);parser.add_argument('--resume',action='store_true')
    options,_=parser.parse_known_args(argv)
    output=options.out
    building_path=options.buildings
    if options.config:
        import json
        config=json.loads(options.config.read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict):raise ValueError('配置文件必须为JSON对象。')
        if output is None and config.get('output_dir'):output=options.config.resolve().parent/config['output_dir']
        if building_path is None and config.get('buildings'):building_path=options.config.resolve().parent/config['buildings']
    if options.resume and options.out and building_path is None:
        import json
        state=json.loads((options.out/'task_state.json').read_text(encoding='utf8'))
        building_path=state.get('request',{}).get('local_buildings',{}).get('path')
    from check_environment import check_environment,print_report
    check_cache=not ('--dem' in argv or options.config)
    vector=bool(building_path and Path(building_path).suffix.lower() in ('.shp','.gpkg','.geojson','.json'))
    report=check_environment(ROOT,output=output,cache=options.cache_dir,require_cache=check_cache,proxy=options.proxy,vector=vector)
    print_report(report)
    if options.check_only:return 0 if report['ready'] else 2
    if not report['ready']:return 2
    python=report['python']
    return subprocess.run([python,str(ROOT/'scripts/run_wind.py'),*argv],env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')).returncode

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,ValueError,ImportError,subprocess.CalledProcessError) as exc:
        message='启动组件缺失或无法导入，请重新完整克隆或解压技能包。' if isinstance(exc,ImportError) else str(exc)
        print('启动失败：'+message,file=sys.stderr);sys.exit(2)
