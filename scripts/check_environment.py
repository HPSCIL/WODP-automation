"""Standard-library preflight checks; never downloads data or installs packages."""
from pathlib import Path
import json
import importlib.util
import hashlib
import os
import platform
import struct
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DEPENDENCIES={'numpy':'numpy','rasterio':'rasterio','matplotlib':'matplotlib','pyshp':'shapefile'}

def requirements_for(root):
    return {line.split('==')[0].strip():line.split('==')[1].strip() for line in (Path(root)/'requirements.txt').read_text(encoding='utf8').splitlines() if '==' in line and not line.lstrip().startswith('#')}

def probe_dependencies(python,requirements,timeout=30):
    code="""import importlib,importlib.metadata,json,struct,sys
issues=[]
for package,module in json.loads(sys.argv[1]).items():
    try:
        importlib.import_module(module)
        version=importlib.metadata.version(package)
        wanted=json.loads(sys.argv[2]).get(package)
        if wanted and version!=wanted:issues.append(package+' '+version+' (required '+wanted+')')
    except Exception as exc:issues.append(package+': '+type(exc).__name__)
print(json.dumps({'issues':issues,'version':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8}))
"""
    try:
        process=subprocess.run([str(python),'-I','-c',code,json.dumps(DEPENDENCIES),json.dumps(requirements)],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=timeout)
        if process.returncode:return {'issues':['解释器或依赖导入失败（退出码'+str(process.returncode)+'）']}
        result=json.loads(process.stdout)
        if result['version'][:2]<[3,12] or result['bits']!=64:result['issues'].append('专用解释器需要64位Python 3.12+')
        return result
    except (OSError,ValueError,subprocess.TimeoutExpired):return {'issues':['解释器无法运行或依赖检查超时']}

def writable_ancestor(path):
    candidate=Path(path).expanduser().absolute()
    while not candidate.exists() and candidate.parent!=candidate:candidate=candidate.parent
    if not candidate.is_dir():return False,'目标或其父路径不是目录'
    try:
        fd,name=tempfile.mkstemp(prefix='.wind-check-',dir=candidate)
        os.close(fd);Path(name).unlink()
        return True,str(candidate)
    except OSError:return False,str(candidate)

def check_environment(root=ROOT,output=None,cache=None,require_cache=True,env_dir=None,proxy=None):
    root=Path(root);checks=[]
    environment=Path(env_dir) if env_dir else Path.home()/'.wind-corridor-native/venv'
    dedicated=environment/'Scripts/python.exe'
    report={'checks':checks,'python':None,'needs_dependencies':False,'environment':str(environment)}
    def add(name,status,detail,action=''):checks.append(dict(name=name,status=status,detail=detail,action=action))
    system_ok=platform.system()=='Windows' and platform.machine().lower() in ('amd64','x86_64')
    add('操作系统','ok' if system_ok else 'error',platform.system()+' / '+platform.machine(),'请在Windows x64电脑上运行本技能。' if not system_ok else '')
    python_ok=sys.version_info[:2]>=(3,12) and struct.calcsize('P')==8
    add('Python','ok' if python_ok else 'error',platform.python_version()+' / '+str(struct.calcsize('P')*8)+'位','请安装或切换到64位Python 3.12或更新版本；推荐Python 3.12，并勾选加入PATH。' if not python_ok else '')
    required=['requirements.txt','scripts/run_wind.py','scripts/render_report.py','scripts/auto_pipeline.py','scripts/sources/network.py',
              'bin/wind-engine.exe','bin/runtime-manifest.json','bin/proj_data/proj.db']
    absent=[name for name in required if not (root/name).is_file()]
    # Include every bundled DLL/resource so a partial clone fails before computation.
    runtime={};damaged=[]
    try:
        runtime=json.loads((root/'bin/runtime-manifest.json').read_text(encoding='utf8'))
        if not isinstance(runtime,dict) or not runtime:raise ValueError('Invalid runtime manifest')
        if any(Path(name).is_absolute() or '..' in Path(name).parts for name in runtime):raise ValueError('Invalid runtime path')
        absent += ['bin/'+name for name in runtime if not (root/'bin'/name).is_file()]
    except (OSError,ValueError,TypeError):
        if 'bin/runtime-manifest.json' not in absent:absent.append('bin/runtime-manifest.json（无法读取）')
    add('技能文件','error' if absent else 'ok','缺少：'+', '.join(dict.fromkeys(absent)) if absent else '引擎、DLL和GIS资源齐全','请重新完整克隆或解压技能包，保留bin、scripts和references目录。' if absent else '')
    if not absent:
        for name,want in runtime.items():
            try:
                h=hashlib.sha256()
                with (root/'bin'/name).open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
                if h.hexdigest()!=want:damaged.append(name)
            except OSError:damaged.append(name)
        add('运行文件校验','error' if damaged else 'ok','文件损坏或被修改：'+', '.join(damaged) if damaged else '原生引擎和随包运行文件SHA-256一致','请重新获取完整技能包，不要混用不同版本的bin目录。' if damaged else '')
    if system_ok and python_ok and not absent and not damaged:
        try:
            process=subprocess.run([str(root/'bin/wind-engine.exe'),'--version'],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=15)
            valid=process.returncode==0 and bool(json.loads(process.stdout).get('version'))
            add('原生引擎','ok' if valid else 'error','启动正常' if valid else '无法启动（退出码'+str(process.returncode)+'）','请检查文件是否完整或被安全软件隔离，确认允许运行此引擎。' if not valid else '')
        except (OSError,ValueError,subprocess.TimeoutExpired):add('原生引擎','error','无法启动或启动超时','请完整解压技能包，并检查系统架构及安全软件的拦截记录。')
        libraries=list((root/'bin/gdal').glob('gdal*.dll'))
        try:
            code="import ctypes,os,sys; scope=os.add_dll_directory(sys.argv[1]); ctypes.WinDLL(sys.argv[2]); scope.close()"
            process=subprocess.run([sys.executable,'-I','-c',code,str(root/'bin/gdal'),str(libraries[0])],capture_output=True,timeout=15) if libraries else None
            valid=process is not None and process.returncode==0
        except (OSError,subprocess.TimeoutExpired):valid=False
        add('GIS运行库','ok' if valid else 'error','GDAL动态库实际加载通过' if valid else 'GDAL动态库无法加载','请完整保留bin内的DLL和运行资源，检查安全软件隔离记录及Windows x64兼容性。' if not valid else '')
    destinations=[('输出目录',Path(output) if output else Path.cwd()/'wind-results')]
    try:destinations.append(('临时目录',Path(tempfile.gettempdir())))
    except OSError:add('临时目录','error','找不到可用的临时目录','请将TEMP或TMP指向可写目录，并重新打开终端。')
    if require_cache:destinations.append(('数据缓存',Path(cache).expanduser() if cache else Path.home()/'.wind-corridor-native/cache'))
    for name,path in destinations:
        ok,parent=writable_ancestor(path)
        action='请将TEMP或TMP指向可写目录，并重新打开终端。' if name=='临时目录' else '请选择有写入权限的目录；输出用--out，缓存用--cache-dir指定。'
        add(name,'ok' if ok else 'error',str(path.absolute()) if ok else '不可写：'+str(path),action if not ok else '')
    if python_ok:
        try:
            requirements=requirements_for(root)
        except OSError:requirements={}
        current=probe_dependencies(sys.executable,requirements)
        chosen=sys.executable if not current['issues'] else None
        dedicated_result=None
        if not chosen and dedicated.is_file():
            dedicated_result=probe_dependencies(dedicated,requirements)
            if not dedicated_result['issues']:chosen=str(dedicated)
        if chosen:
            report['python']=str(chosen)
            add('Python依赖','ok','依赖版本及实际导入通过；使用 '+str(chosen))
        else:
            issues=(dedicated_result or current)['issues']
            report['needs_dependencies']=True
            add('Python依赖','fixable','；'.join(issues),'正常启动将自动安装固定版本依赖到专用环境；仅检查模式不会安装。安装需要可用网络或代理。')
            ok,_=writable_ancestor(environment)
            add('依赖安装目录','ok' if ok else 'error',str(environment),'请确保用户目录可写后重试。' if not ok else '')
            modules_ok=all(importlib.util.find_spec(name) is not None for name in ['venv','ensurepip'])
            add('依赖安装组件','ok' if modules_ok else 'error','venv / ensurepip齐全' if modules_ok else '缺少venv或ensurepip','请安装完整的64位Python 3.12；嵌入式精简Python可能无法自动安装依赖。' if not modules_ok else '')
            if dedicated_result and (dedicated_result.get('version',[0,0])[:2]<[3,12] or dedicated_result.get('bits',0)!=64):
                add('专用解释器','error','现有专用环境的Python版本或位数不符合要求','请先重命名用户目录下.wind-corridor-native/venv，再使用64位Python 3.12启动，重新创建专用环境。')
    if require_cache and (root/'scripts/sources/network.py').is_file():
        try:
            from sources.network import proxy_settings
            settings=proxy_settings(proxy)
            add('网络配置','ok','已识别HTTP/HTTPS代理；实际联网在取数时检查。' if settings else '未配置HTTP/HTTPS代理，将按直连和绕过规则取数。')
        except (ValueError,OSError,ImportError):add('网络配置','error','代理配置无法使用','请检查系统HTTP/HTTPS代理，或使用--proxy提供HTTP/混合端口；不要使用SOCKS-only地址。')
    report['can_prepare']=not any(c['status']=='error' for c in checks)
    report['ready']=report['can_prepare'] and not report['needs_dependencies']
    return report

def print_report(report):
    labels={'ok':'通过','error':'缺失/不可用','fixable':'待安装'}
    print('启动前环境检查',flush=True)
    for item in report['checks']:
        print('['+labels[item['status']]+'] '+item['name']+'：'+item['detail'],flush=True)
        if item['action']:print('  处理方法：'+item['action'],flush=True)
    print('环境已就绪。' if report['ready'] else ('基础环境可用，需准备Python依赖。' if report['can_prepare'] else '环境不满足要求，已停止启动；请按上述提示处理后重试。'),flush=True)

if __name__=='__main__':
    result=check_environment();print_report(result);sys.exit(0 if result['ready'] else 2)
