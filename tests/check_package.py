"""Offline checks: distribution, TLS initialization, CLI admission and HTTP boundaries."""
import asyncio,importlib,json,os,sys,tempfile,tomllib,types
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'plugins/taixu-codex-bridge'
sys.path.insert(0,str(PLUGIN))
from determinflow_codex_bridge import local_setup
from determinflow_codex_bridge.bridge_contract import validate_chat,require_json_object


def rejected(fn):
    try:fn()
    except (ValueError,FileNotFoundError):return
    raise AssertionError('Expected rejection')


def main():
    manifest=tomllib.loads((PLUGIN/'extension.toml').read_text())
    index=tomllib.loads((ROOT/'plugin-repository.toml').read_text())
    assert index['plugins'][0]['id']==manifest['extension']['id']
    assert (ROOT/index['plugins'][0]['subdirectory']).resolve()==PLUGIN
    assert (PLUGIN/manifest['settings']['schema']).is_file()
    schema=json.loads((PLUGIN/manifest['settings']['schema']).read_text())
    assert set(schema['properties'])=={'codex_path','proxy'}
    for file in PLUGIN.rglob('*'):
        assert not file.is_symlink()
        if file.is_file() and '__pycache__' not in file.parts:
            assert file.suffix not in ('.key','.pem','.crt','.log') and file.name!='auth.json'
    body={'model':'synthetic','messages':[{'role':'user','content':'hello'}]}
    assert validate_chat(body)['input']==[{'type':'text','text':'hello'}]
    rejected(lambda:validate_chat(dict(body,max_tokens=1)))
    rejected(lambda:require_json_object('[]'))
    require_json_object('{"ok":true}')
    assert 'HTTPS_PROXY' not in local_setup.proxy_environment('')
    assert local_setup.proxy_environment('http://127.0.0.1:7890')['HTTPS_PROXY']=='http://127.0.0.1:7890'
    for value in ('socks5://localhost:7890','http://user:secret@localhost','http://localhost/?token=secret'):
        rejected(lambda:local_setup.proxy_environment(value))
    with tempfile.TemporaryDirectory() as folder:
        data=Path(folder)/'data'
        first=local_setup.prepare_tls(data)
        before=(data/'tls/server.key').read_bytes()
        assert local_setup.prepare_tls(data)==first
        assert (data/'tls/server.key').read_bytes()==before
        assert (data/'tls/server.key').stat().st_mode & 0o077==0
        from cryptography import x509
        cert=x509.load_pem_x509_certificate((data/'tls/server.crt').read_bytes())
        assert str(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)[0])=='127.0.0.1'
        rejected(lambda:local_setup.find_runtime(str(data/'missing')))
        # Test npm layout using synthetic hashes; real hashes are verified by check_runtime.py.
        import hashlib
        npm=data/'node_modules/@openai/codex';(npm/'bin').mkdir(parents=True)
        entry=npm/'bin/codex.js';entry.write_text('// synthetic npm entry')
        native=npm.parent/'codex-darwin-arm64/vendor/aarch64-apple-darwin/bin';native.mkdir(parents=True)
        binary=native/'codex';binary.write_bytes(b'synthetic runtime');binary.chmod(0o700)
        host=native/'codex-code-mode-host';host.write_bytes(b'synthetic host');host.chmod(0o700)
        pins={'runtime_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'runtime_code_mode_host_sha256':hashlib.sha256(host.read_bytes()).hexdigest()}
        real_read=Path.read_text
        def read(path,*a,**kw):return json.dumps(pins) if path.name=='app-pins.json' else real_read(path,*a,**kw)
        with patch.object(Path,'read_text',read),patch.object(local_setup.platform,'system',return_value='Darwin'),patch.object(local_setup.platform,'machine',return_value='arm64'):
            assert local_setup.find_runtime(str(entry))==binary.resolve()
            host.unlink();rejected(lambda:local_setup.find_runtime(str(entry)))
        (data/'tls/server.crt').unlink()
        rejected(lambda:local_setup.prepare_tls(data))
        # Minimal host API doubles: no private Core code or personal data in tests.
        src=types.ModuleType('src');api=types.ModuleType('src.extension_api')
        api.ExtensionManifest=lambda **kw:types.SimpleNamespace(**kw)
        config=types.ModuleType('src.config');config.DATA_DIR=Path(folder)/'core-data'
        adapters=types.ModuleType('src.core.provider_adapters');adapters.PROVIDER_ADAPTERS={}
        manager=types.ModuleType('src.core.model_manager');manager.PROVIDER_SCHEMAS={}
        modules={'src':src,'src.extension_api':api,'src.config':config,'src.core':types.ModuleType('src.core'),'src.core.provider_adapters':adapters,'src.core.model_manager':manager}
        with patch.dict(sys.modules,modules),patch.dict(os.environ,{},clear=False):
            bridge_module=importlib.import_module('determinflow_codex_bridge.taixu_bridge')
            bridge=bridge_module.Bridge();routers=[]
            registrar=types.SimpleNamespace(manifest=bridge.manifest,add_router=routers.append,add_middleware=lambda *a,**kw:None)
            original_proxy=os.environ.get('HTTPS_PROXY')
            bridge.register(registrar)
            assert os.environ.get('HTTPS_PROXY')==original_proxy
            assert Path(os.environ['SSL_CERT_FILE']).is_file()
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            app=FastAPI();app.include_router(routers[0])
            with TestClient(app) as client:
                assert client.post('/api/taixu-codex-bridge/control/account').status_code==403
                bridge.config['codex_path']=str(data/'missing')
                res=client.post('/api/taixu-codex-bridge/control/account',headers={'X-Taixu-Bridge-Control':'1'})
                assert res.status_code==400 and 'Codex CLI' in res.json()['error']
            request=types.SimpleNamespace(client=types.SimpleNamespace(host='192.0.2.1'),headers={})
            from fastapi import HTTPException
            try:bridge.authorize(request)
            except HTTPException as error:assert error.status_code==403
            else:raise AssertionError('Remote request admitted')
    print('PASS: package, TLS, npm CLI discovery, missing runtime, request validation and local API boundaries')


if __name__=='__main__':main()
