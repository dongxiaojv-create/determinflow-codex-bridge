"""Prepare the pinned official binaries and catalog before Core starts executors."""
import fcntl,json,shutil,sys,tarfile,tempfile
from pathlib import Path
import httpx
# Frozen desktop Python does not add the script directory or cwd to sys.path.
if not __package__:sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from determinflow_codex_bridge import bridge_native as native
from determinflow_codex_bridge.local_setup import find_runtime,proxy_environment

ARCHIVE='https://registry.npmjs.org/@openai/codex/-/codex-0.153.4-darwin-arm64.tgz'

def install_runtime(data,proxy=''):
    import platform
    if (platform.system(),platform.machine())!=('Darwin','arm64'):
        raise ValueError('此版本仅支持 macOS Apple Silicon。')
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=data/'runtime-0.153.4'
    with (data/'runtime-install.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if target.exists():return find_runtime(str(target/'codex'))
        proxy_environment(proxy)
        with tempfile.TemporaryDirectory(prefix='runtime-download-',dir=data) as temp:
            folder=Path(temp);archive=folder/'runtime.tgz'
            with httpx.Client(proxy=proxy or None,trust_env=False,timeout=60) as client:
                with client.stream('GET',ARCHIVE) as response,archive.open('wb') as output:
                    response.raise_for_status();size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>512*1024*1024:raise ValueError('Runtime 下载大小超出上限。')
                        output.write(chunk)
            binaries=folder/'bin';binaries.mkdir(mode=0o700)
            with tarfile.open(archive,'r:gz') as package:
                for name in ('codex','codex-code-mode-host'):
                    member=package.getmember('package/vendor/aarch64-apple-darwin/bin/'+name)
                    if not member.isfile() or not 0<member.size<512*1024*1024:raise ValueError('Runtime 压缩包文件无效。')
                    with package.extractfile(member) as source,(binaries/name).open('wb') as output:shutil.copyfileobj(source,output)
                    (binaries/name).chmod(0o700)
            find_runtime(str(binaries/'codex'))  # Verify both pinned SHA256 values before installation/execution.
            binaries.rename(target)
        return target/'codex'

def prepare(data,config):
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    binary=find_runtime(config['codex_path']) if config.get('codex_path') else install_runtime(data,config.get('proxy',''))
    # Use a blank login store: directory discovery never reads an existing account.
    with tempfile.TemporaryDirectory(prefix='catalog-',dir=data) as temp:
        lab=Path(temp)
        for name in ('home','work','tmp','log','sqlite'):(lab/name).mkdir()
        expected=native.overrides(lab,{},manual_fourth=True)
        command=[str(binary)]
        for value in native.flatten(expected):command+=['-c',value]
        command+=['app-server','--listen','stdio://']
        env=dict(PATH='/usr/bin:/bin',HOME=str(lab/'home'),CODEX_HOME=str(lab/'home'),TMPDIR=str(lab/'tmp'),**proxy_environment(config.get('proxy','')))
        rpc=native.RPC(command,env,lab/'work',lambda *args,**kwargs:None,directory_diagnostic=True)
        try:
            rpc.call('initialize',{'clientInfo':{'name':'bridge-setup','version':'1'},'capabilities':{'experimentalApi':True}});rpc.send({'method':'initialized'})
            models=[];cursor=None;seen=set()
            while True:
                page=rpc.call('model/list',dict(includeHidden=True,limit=100,**({'cursor':cursor} if cursor else {})))
                models.extend(page['data']);cursor=page.get('nextCursor')
                if cursor is None:break
                if cursor in seen:raise ValueError('Repeated catalog cursor')
                seen.add(cursor)
            if not models:raise ValueError('Runtime 未返回模型目录。')
            native.durable(data/'catalog.json',models)
        finally:rpc.close()
    return binary

if __name__=='__main__':
    data=Path(sys.argv[1]);config_file=Path(sys.argv[2])
    config=json.loads(config_file.read_text()) if config_file.is_file() else {}
    prepare(data,config)
    print('Codex Runtime and model catalog ready; no account or generation request used.')
