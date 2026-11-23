"""Standard-library launcher with an isolated dependency environment when needed."""
from pathlib import Path
import argparse
import importlib.util
import os
import platform
import struct
import subprocess
import sys
from sources.network import proxy_settings

ROOT=Path(__file__).resolve().parents[1]

def dependency_environment(proxy=None):
    environment=dict(os.environ)
    for scheme,address in proxy_settings(proxy).items():
        # pip/requests reads environment proxies, not Windows Internet Settings.
        environment[scheme+'_proxy']=address
        environment[scheme.upper()+'_PROXY']=address
    return environment

def main():
    if platform.system()!='Windows' or struct.calcsize('P')!=8:
        raise RuntimeError('本包需要Windows x64及64位Python')
    if sys.version_info<(3,12):raise RuntimeError('需要Python 3.12或更新版本')
    python=sys.executable
    required=['numpy','rasterio','matplotlib','shapefile']
    missing=[name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        env=Path.home()/'.wind-corridor-native'/'venv'
        interpreter=env/'Scripts/python.exe'
        print('缺少依赖：'+', '.join(missing)+'；将使用专用虚拟环境安装。',flush=True)
        if not interpreter.is_file():
            subprocess.run([sys.executable,'-m','venv',str(env)],check=True)
        ready=subprocess.run([str(interpreter),'-c','import numpy,rasterio,matplotlib,shapefile'],capture_output=True).returncode==0
        if not ready:
            parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--proxy')
            options,_=parser.parse_known_args(sys.argv[1:])
            install_environment=dependency_environment(options.proxy)
            command=[str(interpreter),'-m','pip','install','--timeout','30','--retries','1','--disable-pip-version-check','-r',str(ROOT/'requirements.txt')]
            index=os.environ.get('WIND_CORRIDOR_PIP_INDEX')
            if index:command+=['--index-url',index]
            installed=subprocess.run(command,env=install_environment).returncode==0
            if not installed and not index and not os.environ.get('PIP_INDEX_URL') and not os.environ.get('PIP_EXTRA_INDEX_URL'):
                print('PyPI连接失败，改用清华大学HTTPS镜像。',flush=True)
                installed=subprocess.run(command+['--index-url','https://pypi.tuna.tsinghua.edu.cn/simple'],env=install_environment).returncode==0
            if not installed:raise RuntimeError('依赖安装失败；可设置WIND_CORRIDOR_PIP_INDEX或网络代理后重试')
        python=str(interpreter)
    return subprocess.run([python,str(ROOT/'scripts/run_wind.py'),*sys.argv[1:]],env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')).returncode

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,subprocess.CalledProcessError) as exc:
        print('启动失败：'+str(exc),file=sys.stderr);sys.exit(2)
