"""Optional real-Core preflight/startup check, isolated data and zero model requests."""
import asyncio,os,sys,tempfile,types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'plugins/taixu-codex-bridge'

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
    bridge.register(ExtensionRegistrar(manifest,contributions))
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
        import ssl
        context=ssl.create_default_context(cafile=str(data/'tls/trust.pem'))
        async with httpx.AsyncClient(verify=context,trust_env=False) as client:
            url=bridge.model_origin+'/g/'+bridge.generation+'/v1/models'
            response=await client.get(url);assert response.status_code==401
            response=await client.get(url,headers={'Authorization':'Bearer '+bridge.key})
            assert response.status_code==200 and response.json()['data']
        # account/read against isolated, logged-out HOME; no model generation.
        result=await bridge.management_rpc(False,usage=True)
        assert result['account']['type'] is None and result['turns_sent']==0
        assert result['limits'] is None and result['limits_error']
        assert not list((data/'attempts').glob('*.json'))
    finally:await bridge.stop()
    print('PASS: real Core preflight, register/start, HTTPS provider, auth boundary, logged-out Runtime and stop; zero model requests')

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as temp:asyncio.run(check(Path(temp)))
