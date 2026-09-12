"""Optional real-Core preflight/startup check, isolated data and zero model requests."""
import asyncio,os,sys,tempfile,types
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'plugins/taixu-codex-bridge'

async def check_clients(bridge):
    from src.core.llm_client import create_llm
    from src.core.model_manager import get_model_manager
    manager=get_model_manager();provider=manager.get_provider('taixu_codex_limited')
    assert manager.get_category_params_config('taixu_codex_limited')  # Registry deepcopy must not copy client locks.
    llm=create_llm('taixu_codex_limited:'+provider['models'][0],streaming=False,
                   model_params={'reasoning_effort':'low'},provider_retries_enabled=False)
    assert llm.http_client is bridge.clients['http_client']
    assert llm.http_async_client is bridge.clients['http_async_client']
    assert llm.extra_body['reasoning_effort']=='low'
    assert (await asyncio.to_thread(llm.root_client.models.list)).data
    assert (await llm.root_async_client.models.list()).data

async def check_executor():
    sys.path[:0]=[os.environ['DETERMINFLOW_CORE'],str(PLUGIN)]
    from src.extension_api.registrar import ExtensionRegistrar,ExtensionContributions
    from src.extension_host.plugin_preflight import validate_plugin_checkout
    from src.extension_host.executor_plane import ExtensionExecutorPlaneMixin
    from determinflow_codex_bridge.taixu_bridge import Bridge
    bridge=Bridge();before=dict(os.environ)
    bridge.register(ExtensionRegistrar(validate_plugin_checkout('taixu-codex-bridge',PLUGIN),ExtensionContributions()))
    ExtensionExecutorPlaneMixin._explicit_hook(bridge,'start_executor')(types.SimpleNamespace())
    try:await check_clients(bridge)
    finally:await ExtensionExecutorPlaneMixin._explicit_hook(bridge,'stop_executor')()
    assert dict(os.environ)==before
    assert all(client.is_closed for client in bridge.clients.values())

async def check(temp):
    core=Path(os.environ['DETERMINFLOW_CORE']).resolve()
    sys.path.insert(0,str(core));sys.path.insert(0,str(PLUGIN))
    for name in ('DATA_DIR','CONFIG_DIR','LOGS_DIR'):
        folder=temp/name.lower();folder.mkdir()
        os.environ['DETERMINFLOW_'+name]=str(folder)
    os.environ['HOME']=str(temp);os.environ['CODEX_HOME']=str(temp/'codex-home')
    (temp/'codex-home').mkdir()
    from src.extension_host.plugin_preflight import validate_plugin_checkout
    from src.extension_api.registrar import ExtensionRegistrar,ExtensionContributions
    from determinflow_codex_bridge.taixu_bridge import Bridge
    import httpx
    manifest=validate_plugin_checkout('taixu-codex-bridge',PLUGIN)
    contributions=ExtensionContributions();bridge=Bridge()
    before=dict(os.environ)
    bridge.register(ExtensionRegistrar(manifest,contributions))
    assert dict(os.environ)==before
    data=temp/'data_dir/plugins/data/taixu-codex-bridge'
    (data/'login-prompted').touch()  # Host checks never initiate an interactive login.
    services={'plugin_dir':PLUGIN,'plugin_data_dir':data,'plugin_config':{'codex_path':os.environ['CODEX_TEST_RUNTIME'],'proxy':''}}
    from src.extension_host.lifecycle import load_extension_lifecycle,run_extension_lifecycle
    config_file=temp/'plugin-config.json';config_file.write_text(__import__('json').dumps(services['plugin_config']))
    await run_extension_lifecycle(load_extension_lifecycle(PLUGIN/'extension.toml'),owner='taixu-codex-bridge',
        plugin_dir=PLUGIN,config_file=config_file,data_dir=data,base_dir=temp,plugin_revision='fixture',python_executable=sys.executable)
    await bridge.start(types.SimpleNamespace(get_service=services.get))
    try:
        assert bridge.ready and len(bridge.catalog)>1 and not bridge.restart_required
        assert dict(os.environ)==before
        await check_clients(bridge)
        import ssl
        context=ssl.create_default_context(cafile=str(data/'tls/trust.pem'))
        async with httpx.AsyncClient(verify=context,trust_env=False) as client:
            url=bridge.model_origin+'/g/'+bridge.generation+'/v1/models'
            response=await client.get(url);assert response.status_code==401
            response=await client.get(url,headers={'Authorization':'Bearer '+bridge.key})
            assert response.status_code==200 and response.json()['data']
        from determinflow_codex_bridge.local_setup import prepare_tls
        unrelated=prepare_tls(temp/'unrelated-installation')
        context=ssl.create_default_context(cafile=str(Path(unrelated['tls_dir'])/'trust.pem'))
        async with httpx.AsyncClient(verify=context,trust_env=False) as client:
            try:await client.get(url)
            except httpx.ConnectError as error:assert 'CERTIFICATE_VERIFY_FAILED' in str(error)
            else:raise AssertionError('An unrelated installation trusted the Bridge certificate')
        # A separate Executor process registers its own transport without Controller start.
        child=await asyncio.create_subprocess_exec(sys.executable,__file__,'--executor',
                    env=dict(os.environ,DETERMINFLOW_RUNTIME_ROLE='workflow-executor'),
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        output,_=await asyncio.wait_for(child.communicate(),30)
        assert child.returncode==0,output.decode()
        # account/read against isolated, logged-out HOME; no model generation.
        result=await bridge.management_rpc(False,usage=True)
        assert result['account']['type'] is None and result['turns_sent']==0
        assert result['limits'] is None and result['limits_error']
        assert not list((data/'attempts').glob('*.json'))
    finally:await bridge.stop()
    assert all(client.is_closed for client in bridge.clients.values())
    assert dict(os.environ)==before
    print('PASS: real Core and Executor sync/async HTTPS clients, unchanged environment, CA/auth boundaries, logged-out Runtime and cleanup; zero model requests')

if __name__=='__main__':
    if '--executor' in sys.argv:asyncio.run(check_executor())
    else:
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{
            'SSL_CERT_FILE':'/missing-bridge-test-ca',
            **{key:'socks5h://127.0.0.1:9' for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')},
        }):asyncio.run(check(Path(temp)))
