"""Standard-library launcher with an isolated dependency environment when needed."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def dependency_environment(proxy=None):
    from sources.network import proxy_settings
    environment=dict(os.environ)
    for scheme,address in proxy_settings(proxy).items():
        # pip/requests reads environment proxies, not Windows Internet Settings.
        environment[scheme+'_proxy']=address
        environment[scheme.upper()+'_PROXY']=address
    return environment

def prepare_dependencies(environment,proxy=None):
    interpreter=Path(environment)/'Scripts/python.exe'
    print('正在准备专用Python环境；首次安装需要可用网络或代理。',flush=True)
    if not interpreter.is_file():
        if subprocess.run([sys.executable,'-m','venv',str(environment)]).returncode:
            raise RuntimeError('创建专用环境失败；请检查Python的venv/ensurepip组件及用户目录写入权限。')
    install_environment=dependency_environment(proxy)
    command=[str(interpreter),'-m','pip','install','--timeout','30','--retries','1','--disable-pip-version-check','-r',str(ROOT/'requirements.txt')]
    index=os.environ.get('WIND_CORRIDOR_PIP_INDEX')
    if index:command+=['--index-url',index]
    installed=subprocess.run(command,env=install_environment).returncode==0
    if not installed and not index and not os.environ.get('PIP_INDEX_URL') and not os.environ.get('PIP_EXTRA_INDEX_URL'):
        print('PyPI连接失败，改用清华大学HTTPS镜像。',flush=True)
        installed=subprocess.run(command+['--index-url','https://pypi.tuna.tsinghua.edu.cn/simple'],env=install_environment).returncode==0
    if not installed:raise RuntimeError('依赖安装失败；请检查网络/系统代理，或设置WIND_CORRIDOR_PIP_INDEX后重试。')
    return str(interpreter)

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if '--help' in argv or '-h' in argv:
        print('环境检查：python scripts/start.py --check-env（只检查，不安装、不模拟）',flush=True)
        return subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/run_wind.py'),'--help']).returncode
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--check-env','--check-only',action='store_true',dest='check_only')
    parser.add_argument('--out',type=Path);parser.add_argument('--cache-dir');parser.add_argument('--config',type=Path);parser.add_argument('--proxy')
    options,_=parser.parse_known_args(argv)
    output=options.out
    if output is None and options.config:
        import json
        config=json.loads(options.config.read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict):raise ValueError('配置文件必须为JSON对象。')
        if config.get('output_dir'):output=options.config.resolve().parent/config['output_dir']
    from check_environment import check_environment,print_report
    check_cache=not ('--dem' in argv or options.config)
    report=check_environment(ROOT,output=output,cache=options.cache_dir,require_cache=check_cache,proxy=options.proxy)
    print_report(report)
    if options.check_only:return 0 if report['ready'] else 2
    if not report['can_prepare']:return 2
    python=report['python']
    if report['needs_dependencies']:
        python=prepare_dependencies(report['environment'],options.proxy)
        from check_environment import probe_dependencies,requirements_for
        result=probe_dependencies(python,requirements_for(ROOT))
        if result['issues']:raise RuntimeError('依赖安装后仍无法使用：'+'；'.join(result['issues'])+'。请检查完整Python安装及系统运行库。')
        print('Python依赖准备完成，开始执行任务。',flush=True)
    return subprocess.run([python,str(ROOT/'scripts/run_wind.py'),*argv],env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')).returncode

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,ValueError,ImportError,subprocess.CalledProcessError) as exc:
        message='启动组件缺失或无法导入，请重新完整克隆或解压技能包。' if isinstance(exc,ImportError) else str(exc)
        print('启动失败：'+message,file=sys.stderr);sys.exit(2)
