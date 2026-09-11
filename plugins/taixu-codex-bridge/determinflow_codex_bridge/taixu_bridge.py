"""Small in-process Deter extension; only Runtime generation uses a child process."""
import asyncio,contextlib,fcntl,hashlib,json,os,re,socket,time,tomllib,uuid
from pathlib import Path
import httpx
from fastapi import APIRouter,FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse,StreamingResponse
from src.extension_api import ExtensionManifest
from . import bridge_native as native
from .bridge_contract import validate_chat,chat_sse

OWNER='taixu-codex-bridge'; PROVIDER='taixu_codex_limited'
PREFIX='/api/taixu-codex-bridge'; VERSION='0.3.0'

def build_request(params,provider):
    return {'client_kwargs':{},'extra_body':{'reasoning_effort':params.get('reasoning_effort') or 'high'}}

def model_adapter(model):return 'codex_bridge_'+hashlib.sha256(model.encode()).hexdigest()[:12]

def register_adapter(catalog):
    # This pinned Core exposes its registry, but has no add_adapter extension hook.
    # Register only our own type; leave every built-in adapter and Core file untouched.
    from src.core.provider_adapters import PROVIDER_ADAPTERS
    from src.core.model_manager import PROVIDER_SCHEMAS
    efforts=list(dict.fromkeys(e['reasoningEffort'] for row in catalog for e in row.get('supportedReasoningEfforts',[]) if e.get('reasoningEffort')))
    schema={
        'reasoning_effort':{'type':'select','label':'推理强度','default':'high','options':efforts or ['low','medium','high','xhigh']},
        'response_format':{'type':'json_mode','label':'JSON 输出模式','default':None},
        'stream_chunk_timeout':{'type':'number','label':'流式分块超时（秒）','default':None,'nullable':True,'min':1},
    }
    PROVIDER_ADAPTERS['codex_bridge']={'api_format':'openai','model_params':schema,'build_request':build_request}
    PROVIDER_SCHEMAS['codex_bridge']={'provider_type':'codex_bridge','display_name':'Codex Bridge','default_base_url':'','hyperparams':{}}
    for row in catalog:
        model=row.get('model') or row.get('id')
        options=[e['reasoningEffort'] for e in row.get('supportedReasoningEfforts',[]) if e.get('reasoningEffort')]
        if not isinstance(model,str) or not options:continue
        params=dict(schema,reasoning_effort=dict(schema['reasoning_effort'],options=options))
        PROVIDER_ADAPTERS[model_adapter(model)]={'api_format':'openai','model_params':params,'build_request':build_request}

class DefaultSelection:
    """Express the menu selection through the original task API, before Core freezes it."""
    def __init__(self,app,bridge):self.app=app;self.bridge=bridge
    async def __call__(self,scope,receive,send):
        match=re.fullmatch(r'/api/workflows/([^/]+)/tasks',scope.get('path',''))
        if scope['type']!='http' or scope.get('method')!='POST' or not match:
            return await self.app(scope,receive,send)
        raw=b''
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            raw+=message.get('body',b'')
            if len(raw)>1048576:return await JSONResponse({'detail':'Task request too large'},413)(scope,receive,send)
            if not message.get('more_body'):break
        try:
            body=json.loads(raw)
            transformed=self.bridge.freeze_selection(body,match[1])
            if transformed is not body:raw=json.dumps(transformed,ensure_ascii=False).encode()
        except (ValueError,KeyError,TypeError) as error:
            return await JSONResponse({'detail':str(error),'code':'bridge_selection_rejected'},400)(scope,receive,send)
        scope=dict(scope,headers=[(k,v) for k,v in scope['headers'] if k.lower()!=b'content-length']+[(b'content-length',str(len(raw)).encode())])
        replayed=False
        async def replay():
            nonlocal replayed
            if replayed:return await receive()
            replayed=True
            return {'type':'http.request','body':raw,'more_body':False}
        await self.app(scope,replay,send)

class Bridge:
    manifest=ExtensionManifest(extension_id=OWNER,name='Codex Bridge',version=VERSION)
    def __init__(self):
        self.enabled=False; self.ready=False; self.error=None; self.active={}
        self.management=asyncio.Lock(); self.restart_required=False
        self.catalog=[]; self.generation=None

    def register(self,registrar):
        self.manifest=registrar.manifest
        from src.config import DATA_DIR
        self.data=Path(DATA_DIR)/'plugins/data'/OWNER
        from .local_setup import prepare_tls
        self.config=prepare_tls(self.data)
        catalog=json.loads((self.data/'catalog.json').read_text()) if (self.data/'catalog.json').exists() else []
        register_adapter(catalog)
        # Preserve the existing local HTTPS transport before Core creates HTTP clients.
        os.environ['SSL_CERT_FILE']=str(Path(self.config['tls_dir'])/'trust.pem')
        router=APIRouter(prefix=PREFIX)
        router.add_api_route('/status',self.status,methods=['GET'])
        router.add_api_route('/control/{action}',self.control,methods=['POST'])
        registrar.add_router(router)
        registrar.add_middleware(DefaultSelection,bridge=self)

    def freeze_selection(self,body,workflow_id):
        from src.agent.definition import get_agent_definition
        main=get_agent_definition('main')
        selected=main.model if main else None
        if not isinstance(selected,str) or not selected.startswith(PROVIDER+':'):return body
        if not self.enabled or not self.ready:raise ValueError('Selected Codex Bridge is stopped or unavailable')
        if not isinstance(body,dict):raise ValueError('Task request must be an object')
        for key in ('node_model_overrides','node_model_params_overrides'):
            if body.get(key) is not None and not isinstance(body[key],dict):raise ValueError('Task overrides must be objects')
        models=dict(body.get('node_model_overrides') or {})
        params=dict(body.get('node_model_params_overrides') or {})
        workflow=self.runtime.workflow_runtime.get_workflow(workflow_id)
        if not workflow:return body
        for node in workflow['definition']['nodes']:
            if node.get('node_type')!='agent':continue
            node_id=node['id'];agent=get_agent_definition(node['agent_type'])
            explicit=models.get(node_id) or node.get('model_override') or (agent.model if agent else None)
            models[node_id]=explicit or selected
            if models[node_id].startswith(PROVIDER+':'):
                overlay=dict(params.get(node_id) or {})
                overlay.setdefault('reasoning_effort',(main.model_params or {}).get('reasoning_effort') or 'high')
                params[node_id]=overlay
        return dict(body,node_model_overrides=models,node_model_params_overrides=params)

    async def start(self,runtime):
        self.root=Path(runtime.get_service('plugin_dir'))
        self.runtime=runtime
        self.config.update(runtime.get_service('plugin_config') or {})
        self.data=Path(runtime.get_service('plugin_data_dir')); self.data.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=(self.data/'service.lock').open('a'); fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        (self.data/'attempts').mkdir(exist_ok=True,mode=0o700)
        for name in ('work','tmp','log','sqlite','outputs'):(self.data/name).mkdir(exist_ok=True,mode=0o700)
        self.catalog=json.loads((self.data/'catalog.json').read_text()) if (self.data/'catalog.json').exists() else []
        self.enabled=not (self.data/'stopped').exists(); self.key=uuid.uuid4().hex; self.generation=uuid.uuid4().hex
        import uvicorn
        tls=Path(self.config['tls_dir'])
        if os.environ.get('SSL_CERT_FILE')!=str(tls/'trust.pem'):raise ValueError('Launch Core with the installation process-local SSL_CERT_FILE')
        model_app=FastAPI()
        model_app.add_api_route('/g/{generation}/v1/models',self.models,methods=['GET'])
        model_app.add_api_route('/g/{generation}/v1/chat/completions',self.chat,methods=['POST'])
        listener=socket.socket();listener.bind(('127.0.0.1',0))
        self.model_origin='https://127.0.0.1:'+str(listener.getsockname()[1])
        self.server=uvicorn.Server(uvicorn.Config(model_app,loop='asyncio',lifespan='off',log_level='warning',ssl_certfile=str(tls/'server.crt'),ssl_keyfile=str(tls/'server.key'),timeout_graceful_shutdown=3))
        self.server.capture_signals=contextlib.nullcontext  # Core owns process signals.
        self.server_task=asyncio.create_task(self.server.serve(sockets=[listener]))
        async with asyncio.timeout(5):
            while not self.server.started:
                if self.server_task.done():await self.server_task;raise RuntimeError('TLS server stopped before readiness')
                await asyncio.sleep(.01)
        try:self.register_provider()
        except Exception:
            await self.stop()
            raise
        self.ready=True

    async def api(self,method,path,**kwargs):
        async with httpx.AsyncClient(base_url=self.config['core_url'],trust_env=False,timeout=2) as client:
            response=await client.request(method,path,**kwargs)
        response.raise_for_status(); return response.json()

    def listing(self):
        models=[]
        for row in self.catalog:
            if row.get('hidden') is True:continue
            name=row.get('model') or row.get('id')
            if not isinstance(name,str): continue
            models.append(dict(id=name,object='model',owned_by='native-codex',native=row,
                bridge_status='由 Codex Runtime 响应所选模型'))
        if not any(m['id']=='gpt-5.5' for m in models):
            models.insert(0,dict(id='gpt-5.5',object='model',owned_by='native-codex',native=None,bridge_status='目录尚未刷新'))
        return sorted(models,key=lambda m:m['id']!='gpt-5.5')

    def register_provider(self):
        # Same public ModelManager operations as the Provider API, before ExecutorPool starts.
        from src.core.model_manager import get_model_manager
        manager=get_model_manager();existing=manager.get_provider(PROVIDER)
        if existing and existing.get('managed_by')!=OWNER: raise ValueError('Provider ID belongs to another configuration')
        listing=self.listing()
        body=dict(name='Codex Bridge',provider_type='codex_bridge',
            base_url=self.model_origin+'/g/'+self.generation+'/v1',api_key=self.key,
            models=[m['id'] for m in listing],models_config={m['id']:{'bridge_status':m['bridge_status'],'native_catalog':m['native'],**({'provider_type':model_adapter(m['id'])} if (m['native'] or {}).get('supportedReasoningEfforts') else {})} for m in listing},managed_by=OWNER)
        if existing:manager.update_provider(PROVIDER,body)
        else:manager.add_provider(PROVIDER,body)

    def note(self,kind,**values):
        # Keep identities, outcomes and usage; never persist account/config replies, credentials or full prompts.
        allowed=('method','operation','task_id','node_id','pid','returncode','error_type','stage','conversation.id','http.response.status_code','attempt','duration_ms','error_present','accepted','status')
        row=dict(time=time.time(),kind=kind,**{k:v for k,v in values.items() if k in allowed})
        with (self.data/'events.ndjson').open('a') as file:file.write(json.dumps(row,ensure_ascii=False)+'\n')

    async def status(self,request:Request):
        self.authorize(request)
        return dict(workspace_root=str(self.data.parents[2]/'workspaces'),version=VERSION,enabled=self.enabled,ready=self.ready,restart_required=self.restart_required,error=self.error,generation=self.generation,
            active=[{'operation':key,'model':value.get('model')} for key,value in self.active.items()],
            attempts=sum(1 for _ in (self.data/'attempts').glob('*.json')),models=self.listing(),
            supported='All Deter workflows and chat; message history and Deter-executed tools; selected model and effort',
            capability_notes=['Codex Runtime does not expose temperature/top_p/penalty controls; the Bridge adapter does not advertise them.'])

    def authorize(self,request,generation=None):
        if request.client is None or request.client.host not in ('127.0.0.1','::1','testclient'): raise HTTPException(403,'Loopback only')
        if generation is not None and (generation!=self.generation or request.headers.get('authorization')!='Bearer '+self.key):
            raise HTTPException(401,'Retired or invalid service identity; no fallback')

    async def models(self,generation:str,request:Request):
        self.authorize(request,generation)
        if not self.enabled or not self.ready: raise HTTPException(401,'Provider stopped or unavailable')
        return dict(object='list',data=self.listing())

    def launch(self,operation):
        from .local_setup import find_runtime, proxy_environment
        binary=find_runtime(self.config.get('codex_path',''))
        home=Path.home(); codex=Path(os.environ.get('CODEX_HOME') or home/'.codex').expanduser()
        if not codex.is_dir():raise ValueError('尚未初始化 Codex 登录，请先在终端运行 codex login。')
        config=tomllib.loads((codex/'config.toml').read_text()) if (codex/'config.toml').exists() else {}
        if config.get('forced_login_method') is not None or native.PROVIDER in config.get('model_providers',{}):
            raise ValueError('Native authentication/provider configuration conflicts with reviewed path')
        expected=native.overrides(self.data,config,manual_fourth=True)
        command=[str(binary)]
        for value in native.flatten(expected): command+=['-c',value]
        command+=['app-server','--listen','stdio://']
        environment=dict(PATH='/usr/bin:/bin',HOME=str(home),CODEX_HOME=str(codex),CODEX_SQLITE_HOME=str(self.data/'sqlite'),
            TMPDIR=str(self.data/'tmp'),LANG='en_US.UTF-8',RUST_LOG=native.DIAGNOSTIC_LOG,LOG_FORMAT='json',
            **proxy_environment(self.config.get('proxy','')))
        out=self.data/'outputs'/operation; out.mkdir(mode=0o700)
        return dict(lab=self.data,out=out,runtime_command=command,runtime_env=environment,expected_runtime=expected,note=self.note)

    async def management_rpc(self,refresh):
        state=self.launch('management-'+uuid.uuid4().hex); rpc=None
        try:
            rpc=native.RPC(state['runtime_command'],state['runtime_env'],state['lab']/'work',self.note,directory_diagnostic=True)
            await asyncio.to_thread(rpc.call,'initialize',{'clientInfo':{'name':'taixu-codex-bridge','version':VERSION},'capabilities':{'experimentalApi':True}})
            rpc.send({'method':'initialized'})
            native.check_config(native.restore_tool_config(await asyncio.to_thread(rpc.call,'config/read',{'includeLayers':True,'cwd':str(state['lab']/'work')})),state['expected_runtime'])
            if (await asyncio.to_thread(rpc.call,'remoteControl/status/read',None))['status']!='disabled': raise ValueError('Remote control must be disabled')
            account=await asyncio.to_thread(rpc.call,'account/read',{'refreshToken':False})
            info=account.get('account') or {}
            public=dict(type=info.get('type'),email=info.get('email'),requiresOpenaiAuth=account.get('requiresOpenaiAuth'),source='native account/read; no login performed')
            if refresh:
                models=[]; cursor=None; seen=set()
                while True:
                    params={'includeHidden':True,'limit':100}
                    if cursor is not None:
                        if cursor in seen: raise ValueError('Repeated catalog cursor')
                        seen.add(cursor); params['cursor']=cursor
                    page=await asyncio.to_thread(rpc.call,'model/list',params); models.extend(page['data']); cursor=page.get('nextCursor')
                    if cursor is None: break
                self.catalog=models; native.durable(self.data/'catalog.json',models); self.restart_required=True
            return dict(account=public,models=self.listing() if refresh else None,turns_sent=0,restart_required=self.restart_required)
        finally:
            if rpc is not None: await asyncio.to_thread(rpc.close)

    async def control(self,action:str,request:Request):
        self.authorize(request)
        # Non-browser custom header avoids ambient cross-origin POSTs; the host's existing local API is the trust boundary.
        if request.headers.get('x-taixu-bridge-control')!='1': raise HTTPException(403,'Use bridge.py management command')
        async with self.management:
            if action=='stop':
                self.enabled=False; (self.data/'stopped').touch(mode=0o600)
                await self.drain()
            elif action=='start':
                if self.active: raise HTTPException(409,'Wait for current cancellation to finish')
                if not self.enabled:
                    (self.data/'stopped').unlink(missing_ok=True)
                    self.ready=False; self.restart_required=True  # New service generation needs fresh Executor clients.
            elif action in ('account','refresh-models'):
                if self.active: raise HTTPException(409,'Management is deferred until the active node finishes')
                try:
                    async with asyncio.timeout(60): return await self.management_rpc(action=='refresh-models')
                except (ValueError,FileNotFoundError) as error:
                    return JSONResponse({'error':str(error)},400)
                except Exception:
                    return JSONResponse({'error':'Codex 检查失败。请在终端运行 codex login status，检查网络和插件版本。'},502)
            else: raise HTTPException(400,'Unknown action')
        return await self.status(request)

    async def drain(self):
        running=list(self.active.values())
        if not running:return
        try:await asyncio.wait_for(asyncio.gather(*(asyncio.shield(state['done']) for state in running)),20)
        except TimeoutError:
            await asyncio.gather(*(asyncio.to_thread(state['rpc'].close) for state in running if state.get('rpc')),return_exceptions=True)

    async def stop(self):
        self.enabled=False; self.ready=False
        await self.drain()
        if hasattr(self,'server'):
            self.server.should_exit=True
            await asyncio.wait_for(self.server_task,10)
        if hasattr(self,'lock'): self.lock.close()

    async def chat(self,generation:str,request:Request):
        self.authorize(request,generation)
        if not self.enabled or not self.ready:raise HTTPException(401,'Codex Bridge is stopped or unavailable')
        raw=await request.body()
        if len(raw)>8388608:raise HTTPException(413,'Request exceeds 8 MiB')
        try:body=validate_chat(json.loads(raw))
        except (ValueError,TypeError,KeyError) as error:
            return JSONResponse({'error':{'message':str(error),'code':'invalid_request'}},400)
        operation=uuid.uuid4().hex
        try:state=self.launch(operation)
        except (ValueError,FileNotFoundError) as error:
            return JSONResponse({'error':{'message':str(error),'code':'codex_setup_required'}},503)
        slot=self.data/'attempts'/(operation+'.json')
        def save(**values):
            old=json.loads(slot.read_text()) if slot.exists() else {}
            old.update(values);native.durable(slot,old)
        save(state='STARTED',operation=operation,model=body['model'],generation=self.generation)
        state.update(operation=operation,model=body['model'],request=request,save=save,
                     enabled=lambda:self.enabled and self.ready,done=asyncio.get_running_loop().create_future())
        self.active[operation]=state
        try:
            completion=await native.run_chat(state,body,operation)
            save(state='COMPLETED',finish_reason=completion['choices'][0]['finish_reason'],usage=completion.get('usage'))
            if body.get('stream'):
                return StreamingResponse(iter([chat_sse(completion)]),media_type='text/event-stream')
            return JSONResponse(completion)
        except asyncio.CancelledError:
            save(state='CANCELLED_LOCALLY',remote_status='See Runtime terminal evidence')
            raise
        except Exception as error:
            save(state='FAILED_OR_UNKNOWN',error_type=type(error).__name__)
            self.note('request_failed',operation=operation,error_type=type(error).__name__)
            return JSONResponse({'error':{'message':'Codex Runtime 请求失败；请检查登录、网络和模型权限。','code':'codex_runtime_error'}},400)
        finally:
            state['done'].set_result(None);self.active.pop(operation,None)

def create_extension(): return Bridge()
