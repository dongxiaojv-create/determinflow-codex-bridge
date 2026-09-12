"""Real loopback HTTP streaming and cancellation; no Runtime or account access."""
import ast,asyncio,contextlib,json,os,queue,socket,sys,tempfile,threading,time,types,uuid
from pathlib import Path
from unittest.mock import patch
import httpx,uvicorn
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import JSONResponse,StreamingResponse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge import bridge_native as native,bridge_contract as contract


def bridge_class():
    source=ROOT/'plugins/taixu-codex-bridge/determinflow_codex_bridge/taixu_bridge.py'
    tree=ast.parse(source.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Bridge')
    scope=dict(vars(contract),asyncio=asyncio,contextlib=contextlib,json=json,time=time,
               uuid=uuid,Path=Path,Request=Request,HTTPException=HTTPException,
               JSONResponse=JSONResponse,StreamingResponse=StreamingResponse,native=native,
               OWNER='taixu-codex-bridge',VERSION='fixture',ExtensionManifest=lambda **kw:kw)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
    return scope['Bridge']


class Run:
    def __init__(self,mode='complete'):
        self.mode=mode;self.gate=asyncio.Event();self.closed=asyncio.Event();self.calls=0

    async def __call__(self,state,body,operation,on_text=None):
        self.calls+=1;self.state=state;self.effort=body.get('effort')
        state.update(stage='runtime_setup')
        try:
            if self.mode=='before_error':raise ValueError('PRIVATE RAW ERROR')
            state.update(stage='generation',turn_id='synthetic-turn',turn_submission_attempted=True)
            if on_text:await on_text('')
            if self.mode.startswith('queue_'):
                self.queue=on_text.__self__
                for _ in range(32 if self.mode=='queue_error' else 100):await on_text('queued')
                raise ValueError('PRIVATE RAW ERROR')
            if self.mode=='heartbeat':await self.gate.wait()
            if on_text:await on_text('early ')
            await self.gate.wait()
            if self.mode=='after_error':raise ValueError('PRIVATE RAW ERROR')
            if on_text:await on_text('late')
            message=dict(role='assistant',content='early late')
            if self.mode=='tools':message['tool_calls']=[dict(id='call-1',type='function',function=dict(name='deter_tool',arguments='{"value":1}'))]
            return dict(id='chatcmpl-'+operation,object='chat.completion',created=state['created'],model=body['model'],
                        choices=[dict(index=0,message=message,finish_reason='tool_calls' if self.mode=='tools' else 'stop')],
                        usage=dict(prompt_tokens=7,completion_tokens=3,total_tokens=10),bridge_capabilities=[])
        finally:self.closed.set()


async def first_text(lines):
    async with asyncio.timeout(3):
        async for line in lines:
            if line.startswith('data: ') and line!='data: [DONE]':
                chunk=json.loads(line[6:])
                if chunk.get('choices') and chunk['choices'][0].get('delta',{}).get('content'):
                    return chunk
    raise AssertionError('HTTP stream ended without a text delta')


async def check_clean(bridge,run,root):
    await asyncio.wait_for(run.closed.wait(),3)
    async with asyncio.timeout(3):
        while bridge.active:await asyncio.sleep(.01)
    assert run.state['done'].done() and run.calls==1
    record=json.loads((root/'attempts'/(run.state['operation']+'.json')).read_text())
    assert record['version']=='fixture' and record['effort']==run.effort
    assert record['ended_at']>=record['started_at'] and record['duration_ms']>=0
    return record


async def http_checks():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'attempts').mkdir()
        bridge=bridge_class()();bridge.data=root;bridge.enabled=bridge.ready=True
        bridge.generation='fixture';bridge.key='synthetic';bridge.note=lambda *a,**k:None
        bridge.launch=lambda _:dict(note=bridge.note)
        app=FastAPI();app.add_api_route('/v1/{generation}/chat/completions',bridge.chat,methods=['POST'])
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.setblocking(False)
        server=uvicorn.Server(uvicorn.Config(app,log_level='critical',lifespan='off'))
        serving=asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    if serving.done():await serving
                    await asyncio.sleep(.01)
            url='http://127.0.0.1:'+str(listener.getsockname()[1])+'/v1/fixture/chat/completions'
            body=dict(model='gpt-5.6-sol',messages=[dict(role='user',content='synthetic')],stream=True,stream_options=dict(include_usage=True))
            async with httpx.AsyncClient(trust_env=False,timeout=5,headers={'Authorization':'Bearer synthetic'}) as client:
                for mode in ('complete','tools'):
                    run=Run(mode)
                    with patch.object(native,'run_chat',run):
                        async with client.stream('POST',url,json=body) as response:
                            assert response.status_code==200
                            lines=response.aiter_lines();first=await first_text(lines)
                            assert first['choices'][0]['delta']['content']=='early ' and not run.gate.is_set()
                            assert not run.closed.is_set() and bridge.active
                            run.gate.set();tail=[line async for line in lines]
                        chunks=[first]+[json.loads(line[6:]) for line in tail if line.startswith('data: ') and line!='data: [DONE]']
                        assert ''.join(c['choices'][0]['delta'].get('content','') for c in chunks if c.get('choices'))=='early late',chunks
                        assert [c['choices'][0]['finish_reason'] for c in chunks if c.get('choices') and c['choices'][0]['finish_reason']]==['tool_calls' if mode=='tools' else 'stop']
                        assert [c['usage'] for c in chunks if c.get('usage')]==[dict(prompt_tokens=7,completion_tokens=3,total_tokens=10)]
                        assert {(c['id'],c['model'],c['created']) for c in chunks}=={('chatcmpl-'+run.state['operation'],body['model'],run.state['created'])}
                        tool_chunks=[c['choices'][0]['delta']['tool_calls'] for c in chunks if c.get('choices') and c['choices'][0]['delta'].get('tool_calls')]
                        assert tool_chunks==([[dict(index=0,id='call-1',type='function',function=dict(name='deter_tool',arguments='{"value":1}'))]] if mode=='tools' else [])
                        assert tail.count('data: [DONE]')==1
                        assert (await check_clean(bridge,run,root))['state']=='COMPLETED'

                run=Run('before_error')
                with patch.object(native,'run_chat',run):
                    response=await client.post(url,json=body)
                    assert response.status_code==400,response.text
                    assert 'PRIVATE RAW ERROR' not in response.text and response.json()['error']['code']=='codex_not_submitted'
                    assert (await check_clean(bridge,run,root))['state']=='FAILED_OR_UNKNOWN'

                run=Run('after_error')
                with patch.object(native,'run_chat',run):
                    async with client.stream('POST',url,json=body) as response:
                        assert response.status_code==200
                        lines=response.aiter_lines();await first_text(lines);run.gate.set()
                        tail='\n'.join([line async for line in lines])
                    assert '"error"' in tail and 'PRIVATE RAW ERROR' not in tail and '[DONE]' not in tail,tail
                    assert (await check_clean(bridge,run,root))['state']=='FAILED_OR_UNKNOWN'

                for mode in ('disconnect','drain'):
                    run=Run(mode)
                    with patch.object(native,'run_chat',run):
                        async with client.stream('POST',url,json=body) as response:
                            lines=response.aiter_lines();await first_text(lines)
                            if mode=='drain':
                                await asyncio.wait_for(bridge.drain(),3)
                                tail='\n'.join([line async for line in lines])
                                assert '[DONE]' not in tail,tail
                        record=await check_clean(bridge,run,root)
                        assert record['state']=='CANCELLED_LOCALLY',record
                        assert not run.gate.is_set()

                run=Run('heartbeat')
                with patch.object(native,'run_chat',run):
                    async with client.stream('POST',url,json=body) as response:
                        chunks=[];start=time.monotonic()
                        async with asyncio.timeout(3):
                            async for line in response.aiter_lines():
                                if line.startswith('data: '):
                                    chunks.append(json.loads(line[6:]))
                                    if len(chunks)==2:break
                        assert chunks[0]['choices'][0]['delta'].get('role')=='assistant',chunks
                        assert chunks[1]['choices'][0]['delta']=={},chunks
                        assert time.monotonic()-start<2.5 and not run.gate.is_set()
                    assert (await check_clean(bridge,run,root))['state']=='CANCELLED_LOCALLY'

                # Leave StreamingResponse unconsumed to deterministically fill its bounded queue.
                class UnreadRequest:
                    client=types.SimpleNamespace(host='127.0.0.1');headers={'authorization':'Bearer synthetic'}
                    async def body(self):return json.dumps(body).encode()
                    async def is_disconnected(self):return False
                for mode in ('queue_cancel','queue_error'):
                    run=Run(mode)
                    with patch.object(native,'run_chat',run):
                        response=await bridge.chat('fixture',UnreadRequest())
                        async with asyncio.timeout(3):
                            while not run.queue.full():await asyncio.sleep(.01)
                        if mode=='queue_cancel':await asyncio.wait_for(bridge.drain(),3)
                        record=await check_clean(bridge,run,root)
                        assert run.state['task'].done(),mode
                        assert record['state']==('CANCELLED_LOCALLY' if mode=='queue_cancel' else 'FAILED_OR_UNKNOWN')
                        await response.body_iterator.aclose()
                original_durable=native.durable
                for disk_error in (False,True):
                    state={};run=Run()
                    def cancel_before_start(_):
                        asyncio.get_running_loop().call_soon(lambda:state['task'].cancel())
                        return state
                    def save_cancel(path,value):
                        if disk_error and value.get('state')=='CANCELLED_LOCALLY':raise OSError('synthetic cancellation write failure')
                        return original_durable(path,value)
                    with patch.object(bridge,'launch',cancel_before_start),patch.object(native,'run_chat',run),patch.object(native,'durable',save_cancel):
                        try:await bridge.chat('fixture',UnreadRequest())
                        except (asyncio.CancelledError,OSError) as error:
                            assert isinstance(error,OSError)==disk_error
                            if disk_error:assert str(error)=='synthetic cancellation write failure'
                        else:raise AssertionError('Cancelled producer unexpectedly returned success')
                        assert run.calls==0 and state['task'].cancelled() and not bridge.active and state['done'].done()
                        record=json.loads((root/'attempts'/(state['operation']+'.json')).read_text())
                        assert record['version']=='fixture' and record['effort'] is None
                        assert record['ended_at']>=record['started_at'] and record['duration_ms']>=0
        finally:
            server.should_exit=True
            await asyncio.wait_for(serving,5)
            listener.close()
    print('PASS: early HTTP text, metadata, tool/usage finish, safe errors, heartbeat and full-queue/disconnect/drain cleanup')


async def runtime_cleanup_checks():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);normalized=contract.validate_chat(dict(model='gpt-5.6-sol',messages=[dict(role='user',content='synthetic')],stream=True))
        for mode in ('initialization','generation','last_only','wrong_identity'):
            generation=mode!='initialization'
            entered=threading.Event();release=threading.Event();interrupting=threading.Event()
            closing=threading.Event();close_release=threading.Event();ready=asyncio.Event()
            class RPC:
                def __init__(self,*a,**kw):
                    self.pending=[];self.events=[];self.inbox=queue.Queue();self.closed=False;self.waits=0
                    self.proc=types.SimpleNamespace(poll=lambda:0 if self.closed else None)
                def call(self,method,params):
                    if method=='initialize':
                        entered.set()
                        if mode=='initialization':assert release.wait(3)
                        return {}
                    if method in ('config/read','account/read'):return {}
                    if method=='remoteControl/status/read':return {'status':'disabled'}
                    if method=='thread/start':return dict(thread={'id':'thread'},model='gpt-5.6-sol',modelProvider=native.PROVIDER)
                    if method=='turn/start':return {'turn':{'id':'turn'}}
                    raise AssertionError(method)
                def send(self,message):
                    if message.get('method')=='turn/interrupt':interrupting.set()
                def wait(self,*a):
                    assert release.wait(3);self.waits+=1
                    if self.waits==1:
                        last=dict(inputTokens=10,outputTokens=3,totalTokens=13)
                        token_usage={'last':last}
                        if mode!='last_only':token_usage['total']=dict(inputTokens=20,outputTokens=6,totalTokens=26)
                        self.usage_event={'method':'thread/tokenUsage/updated','params':{'threadId':'other' if mode=='wrong_identity' else 'thread','turnId':'turn','tokenUsage':token_usage}}
                        # The real reader captures the event before wait leaves it pending.
                        self.events.append(self.usage_event);self.pending.append(self.usage_event)
                    return {'id':'bridge-interrupt','result':{}} if self.waits==1 else {'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'interrupted'}}}
                def close(self):
                    closing.set()
                    if generation:
                        assert close_release.wait(3)
                        self.events.append(self.usage_event)  # A duplicate notification must not be summed.
                        self.events.append({'method':'thread/tokenUsage/updated','params':{'threadId':'thread','turnId':'other','tokenUsage':{'total':{'totalTokens':999}}}})
                    self.closed=True;release.set()
            saved={}
            bridge=bridge_class()();state=dict(runtime_command=[],runtime_env={},lab=root,out=root,note=lambda *a,**kw:None,save=lambda **values:saved.update(values),
                request=types.SimpleNamespace(is_disconnected=lambda:asyncio.sleep(0,result=False)),enabled=lambda:True,
                expected_runtime={'model_providers':{native.PROVIDER:{'requires_openai_auth':False}}},done=asyncio.get_running_loop().create_future())
            bridge.active={'synthetic':state}
            async def started(_):ready.set()
            async def generate():
                try:return await native.run_chat(state,normalized,'synthetic',on_text=started)
                finally:state['done'].set_result(None);bridge.active.clear()
            with patch.object(native,'RPC',RPC),patch.object(native,'check_config',lambda *a:None),patch.object(native,'restore_tool_config',lambda value:value):
                task=asyncio.create_task(generate());state['task']=task;drains=[]
                try:
                    assert await asyncio.to_thread(entered.wait,3)
                    if generation:await asyncio.wait_for(ready.wait(),3)
                    task.cancel()
                    if generation:
                        assert await asyncio.to_thread(interrupting.wait,3)
                        drains.append(asyncio.create_task(bridge.drain()));await asyncio.sleep(.01)
                        assert task.cancelling()==1 and not task.done()
                        release.set();assert await asyncio.to_thread(closing.wait,3)
                        drains.append(asyncio.create_task(bridge.drain()));await asyncio.sleep(.01)
                        assert task.cancelling()==1 and not task.done()
                        close_release.set()
                    with contextlib.suppress(asyncio.CancelledError):await asyncio.wait_for(task,3)
                    await asyncio.gather(*drains)
                    assert state['rpc'].closed and not bridge.active and state['done'].done()
                    assert state['runtime_result']['error_type']=='CancelledError'
                    assert state['runtime_result']['interrupted']==generation
                    result=state['runtime_result']
                    assert result['usage_scope']==('thread_total' if mode=='generation' else 'last_only' if mode=='last_only' else 'unknown')
                    assert result['usage']==(dict(inputTokens=20,outputTokens=6,totalTokens=26) if mode=='generation' else dict(inputTokens=10,outputTokens=3,totalTokens=13) if mode=='last_only' else None)
                    assert saved['usage_scope']==result['usage_scope'] and saved['runtime_usage']==result['usage']
                finally:
                    release.set();close_release.set()
                    with contextlib.suppress(asyncio.CancelledError):await task
    print('PASS: native cancellation and repeated drain preserve cleanup; late/duplicate usage, last-only fallback and identity filtering')


async def core_consumer_checks():
    core=os.environ.get('DETERMINFLOW_CORE_ROOT')
    if not core:return
    import logging,openai
    from anthropic import BadRequestError as AnthropicBadRequestError
    from langchain_openai import ChatOpenAI
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables.config import ensure_config,merge_configs
    from langgraph.graph import StateGraph,MessagesState,START,END
    source=Path(core)/'src/core/llm_client.py'
    node=next(n for n in ast.parse(source.read_text()).body if getattr(n,'name',None)=='_wrap_llm_with_retry')
    logger=logging.getLogger('synthetic-stream-consumer');logger.addHandler(logging.NullHandler());logger.propagate=False
    scope=dict(asyncio=asyncio,BaseChatModel=BaseChatModel,BaseCallbackHandler=BaseCallbackHandler,
               ensure_config=ensure_config,merge_configs=merge_configs,logger=logger,
               PROVIDER_BAD_REQUEST_ERRORS=(openai.BadRequestError,AnthropicBadRequestError))
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
    base=dict(id='chatcmpl-synthetic',object='chat.completion.chunk',created=0,model='gpt-5.6-sol')
    def wire(data):return ('data: '+json.dumps(data)+'\n\n').encode()
    for mode in ('astream','ainvoke'):
        requests=[]
        async def handler(request):
            requests.append(request)
            return httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=
                wire(dict(base,choices=[dict(index=0,delta=dict(role='assistant',content=''),finish_reason=None)]))+
                wire({'error':{'message':'Synthetic generation failed; remote state unknown','code':'codex_runtime_error'}}))
        with httpx.Client(trust_env=False) as sync:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False) as client:
                llm=ChatOpenAI(model='gpt-5.6-sol',api_key='synthetic',base_url='http://synthetic.invalid/v1',
                               streaming=True,http_async_client=client,http_client=sync,http_socket_options=())
                scope['_wrap_llm_with_retry'](llm,dict(max_retries=1,delays=[0]))
                try:
                    if mode=='astream':
                        async for _ in llm.astream('synthetic'):pass
                    else:await llm.ainvoke('synthetic')
                except openai.APIError:pass
                else:raise AssertionError(mode+' silently accepted a failed stream')
                assert len(requests)==1,(mode,len(requests))
    gate=asyncio.Event();closed=asyncio.Event()
    class HeartbeatBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wire(dict(base,choices=[dict(index=0,delta=dict(role='assistant',content=''),finish_reason=None)]))
            yield wire(dict(base,choices=[dict(index=0,delta={},finish_reason=None)]))
            await gate.wait()
        async def aclose(self):closed.set()
    async def heartbeat_handler(request):
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=HeartbeatBody())
    with httpx.Client(trust_env=False) as sync:
        async with httpx.AsyncClient(transport=httpx.MockTransport(heartbeat_handler),trust_env=False) as client:
            llm=ChatOpenAI(model='gpt-5.6-sol',api_key='synthetic',base_url='http://synthetic.invalid/v1',
                           streaming=True,http_async_client=client,http_client=sync,http_socket_options=())
            async def generate(state):return {'messages':[await llm.ainvoke(state['messages'])]}
            builder=StateGraph(MessagesState);builder.add_node('llm',generate)
            builder.add_edge(START,'llm');builder.add_edge('llm',END);graph=builder.compile()
            streamed=[]
            async with asyncio.timeout(3):
                # Match Core Session._invoke_graph: bare async-for break, no aclosing.
                async for event in graph.astream_events({'messages':[('user','synthetic')]},version='v2'):
                    if event['event']=='on_chat_model_stream':
                        streamed.append(event['data']['chunk'].content)
                        if len(streamed)==2:break
            assert streamed==['',''] and not gate.is_set(),streamed
            await asyncio.wait_for(closed.wait(),3)
    print('PASS: real SDK/Core reject stream errors without retry; empty deltas wake LangGraph and bare break closes HTTP')


async def main():
    await http_checks();await runtime_cleanup_checks();await core_consumer_checks()

if __name__=='__main__':asyncio.run(main())
