"""Check the current Windows exclusion, not Windows compatibility; no model/account calls."""
import importlib.util,json,os,platform,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'plugins/taixu-codex-bridge/determinflow_codex_bridge'
sys.path.insert(0,str(PACKAGE.parent))
from determinflow_codex_bridge import bridge_native,local_setup


def fcntl_import_failure(module):
    # Windows lacks fcntl. Block it even on macOS/Linux and execute the real module.
    spec=importlib.util.spec_from_file_location('determinflow_codex_bridge._platform_probe',PACKAGE/(module+'.py'))
    with patch.dict(sys.modules,{'fcntl':None}):
        try:spec.loader.exec_module(importlib.util.module_from_spec(spec))
        except ModuleNotFoundError as error:
            assert error.name=='fcntl',str(error)
        else:raise AssertionError(module+' unexpectedly imported without fcntl; reassess the Windows boundary')


def main():
    result={'status':'unsupported_expected','host':platform.system(),'real_windows_runtime_tested':False,
            'official_requests':0,'checks':[]}
    with tempfile.TemporaryDirectory(prefix='bridge-platform-') as folder:
        lab=Path(folder)
        exe=lab/'codex.exe';exe.write_bytes(b'not an executable')
        for machine in ('AMD64','arm64'):
            with patch.object(local_setup.platform,'system',return_value='Windows'),patch.object(local_setup.platform,'machine',return_value=machine):
                try:local_setup.find_runtime(str(exe))
                except ValueError as error:assert 'macOS Apple Silicon only' in str(error)
                else:raise AssertionError('Windows passed the runtime platform gate')
            result['checks'].append('Windows '+machine+': finder rejects before executable use')
        for module in ('setup_runtime','taixu_bridge'):
            fcntl_import_failure(module)
            result['checks'].append(module+': missing fcntl prevents import before platform message')
        with patch.dict(sys.modules,{'fcntl':None}):
            try:local_setup.prepare_tls(lab/'tls-data')
            except ModuleNotFoundError as error:assert error.name=='fcntl'
            else:raise AssertionError('TLS initialization unexpectedly works without fcntl')
        assert not (lab/'tls-data/tls').exists()
        result['checks'].append('TLS initialization cannot acquire its lock without fcntl')
        # On hosts that have fcntl, also exercise the actual installer guard.
        if platform.system()!='Windows':
            from determinflow_codex_bridge.setup_runtime import install_runtime
            for machine in ('AMD64','arm64'):
                with patch.object(platform,'system',return_value='Windows'),patch.object(platform,'machine',return_value=machine):
                    try:install_runtime(lab/'install')
                    except ValueError as error:assert 'macOS Apple Silicon' in str(error)
                    else:raise AssertionError('Windows passed the installer platform gate')
            assert not (lab/'install').exists()
            result['checks'].append('installer rejects both Windows architectures before download')
        # This proves direct-child cleanup on the host, not Runtime/host descendant cleanup.
        child_env={key:value for key,value in os.environ.items() if key in ('SystemRoot','WINDIR')}
        child_env.update(HOME=folder,USERPROFILE=folder,CODEX_HOME=folder,TMP=folder,TEMP=folder,TMPDIR=folder)
        rpc=bridge_native.RPC([sys.executable,'-c','import time; time.sleep(30)'],child_env,lab,lambda *args,**kwargs:None)
        started=time.monotonic()
        try:rpc.close()
        finally:
            if rpc.proc.poll() is None:rpc.proc.kill();rpc.proc.wait(3)
        assert rpc.proc.poll() is not None and time.monotonic()-started<8
        result['checks'].append('RPC closes one synthetic Python child on '+platform.system())
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
