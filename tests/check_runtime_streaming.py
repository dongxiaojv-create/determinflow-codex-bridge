"""Pinned Runtime streaming/cancellation against local SSE fixtures; no account or model calls."""
import asyncio,json,os,subprocess,sys,tempfile,threading,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge import bridge_native as native
from determinflow_codex_bridge.bridge_contract import validate_chat
from determinflow_codex_bridge.local_setup import find_runtime


class Request:
    async def is_disconnected(self):return False


async def check(binary,case):
    requests=[];errors=[];gate=threading.Event();completed=threading.Event()
    first_text=asyncio.Event();chunks=[]
    answer='First segment.\nSecond final.' if case=='text' else '{"answer":"OK"}'
    if case=='invalid_json':answer='{"answer":'
    if case=='invalid_schema':answer='{"answer":17}'
    if case in ('mismatch','interleaved','cancel'):answer='Hello there.'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['content-length'])))
            requests.append((body,self.headers.get('Authorization')))
            if len(requests)>1:
                self.send_error(400,'Unexpected additional sampling request');return
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            def event(value):
                self.wfile.write(('data: '+json.dumps(value)+'\n\n').encode());self.wfile.flush()
            def start(index,phase='final_answer'):
                event({'type':'response.output_item.added','output_index':index,'item':{
                    'id':f'msg-{index}','type':'message','role':'assistant','phase':phase,'content':[]}})
            def delta(index,text):
                event({'type':'response.output_text.delta','output_index':index,
                       'item_id':f'msg-{index}','content_index':0,'delta':text})
            def done(index,text,phase='final_answer'):
                event({'type':'response.output_item.done','output_index':index,'item':{
                    'id':f'msg-{index}','type':'message','role':'assistant','phase':phase,
                    'content':[{'type':'output_text','text':text}]}})
            try:
                event({'type':'response.created','response':{'id':'resp-stream-fixture'}})
                if case=='text':
                    # This synthetic marker must never become assistant content.
                    reason={'id':'reason-fixture','type':'reasoning','summary':[]}
                    event({'type':'response.output_item.added','output_index':0,'item':reason})
                    event({'type':'response.reasoning_summary_text.delta','output_index':0,
                           'item_id':reason['id'],'summary_index':0,'delta':'REASONING_FIXTURE_MARKER'})
                    reason['summary']=[{'type':'summary_text','text':'REASONING_FIXTURE_MARKER'}]
                    event({'type':'response.output_item.done','output_index':0,'item':reason})
                    start(1,'commentary');delta(1,'First ')
                    if not gate.wait(10):raise AssertionError('First text was buffered until completion')
                    delta(1,'segment');done(1,'First segment.','commentary')  # Missing tail arrives at completion.
                    done(2,'Second final.')  # Runtime also accepts complete items without upstream deltas.
                elif case=='interleaved':
                    start(0);delta(0,'Hel');start(1);delta(1,'Other')
                    done(0,'Hello');done(1,'Other')
                elif case in ('json_commentary','schema_commentary'):
                    start(0,'commentary');delta(0,'Preparing the result.')
                    done(0,'Preparing the result.','commentary')
                    start(1);delta(1,answer);done(1,answer)
                else:
                    phase=None if case=='json_legacy' else 'commentary' if case=='json_commentary_only' else 'final_answer'
                    start(0,phase);delta(0,answer[:6])
                    if case=='cancel' and not gate.wait(10):raise AssertionError('Cancellation gate timed out')
                    delta(0,answer[6:]);done(0,'Corrected.' if case=='mismatch' else answer,phase)
                completed.set()
                event({'type':'response.completed','response':{'id':'resp-stream-fixture',
                    'usage':{'input_tokens':10,'output_tokens':3,'total_tokens':13}}})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as error:errors.append(error)

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='native-streaming-') as temp:
            lab=Path(temp)
            for name in ('home','work','tmp','log','sqlite','out'):(lab/name).mkdir()
            config_file=lab/'home/config.toml';config_before=b'service_tier = "priority"\n'
            config_file.write_bytes(config_before)
            expected=native.overrides(lab,{},manual_fourth=True)
            assert expected['service_tier']=='default', 'Bridge must override inherited priority in the child only'
            expected['model_providers'][native.PROVIDER].update(
                base_url=f'http://127.0.0.1:{server.server_port}/v1',requires_openai_auth=False)
            command=[str(binary)]
            for value in native.flatten(expected):command+=['-c',value]
            command+=['app-server','--listen','stdio://']
            env=dict(PATH='/usr/bin:/bin',HOME=str(lab/'home'),CODEX_HOME=str(lab/'home'),
                     CODEX_SQLITE_HOME=str(lab/'sqlite'),TMPDIR=str(lab/'tmp'),LANG='en_US.UTF-8',
                     NO_PROXY='127.0.0.1,localhost,::1',RUST_LOG='off')
            state=dict(runtime_command=command,runtime_env=env,lab=lab,out=lab/'out',
                       note=lambda *args,**kwargs:None,enabled=lambda:True,request=Request(),expected_runtime=expected)
            body=dict(model='gpt-5.6-sol',reasoning_effort='low',stream=True,
                      stream_options={'include_usage':True},messages=[{'role':'user','content':'SYNTHETIC STREAM FIXTURE'}])
            if case in ('json','invalid_json','json_commentary','json_commentary_only','json_legacy'):body['response_format']={'type':'json_object'}
            if case in ('schema','invalid_schema','schema_commentary'):
                body['response_format']={'type':'json_schema','json_schema':{'name':'answer','strict':True,
                    'schema':{'type':'object','properties':{'answer':{'type':'string'}},
                              'required':['answer'],'additionalProperties':False}}}
            async def receive(text):
                chunks.append(text)
                if text:first_text.set()
            task=asyncio.create_task(native.run_chat(state,validate_chat(body),uuid.uuid4().hex,on_text=receive))
            try:
                if case in ('text','cancel'):
                    await asyncio.wait_for(first_text.wait(),10)
                    assert chunks[0]=='' and not completed.is_set() and not task.done(), 'No live text before terminal'
                    if case=='cancel':task.cancel()
                    else:gate.set()
                if case=='cancel':
                    try:await asyncio.wait_for(task,10)
                    except asyncio.CancelledError:pass
                    else:raise AssertionError('Cancelled Runtime returned success')
                    assert not completed.is_set(), 'Fixture completed before interruption was confirmed'
                    assert state['runtime_result']['interrupted'] is True
                    assert state['runtime_result']['terminal']['status']=='interrupted'
                    assert state['runtime_result']['usage'] is None and state['runtime_result']['usage_scope']=='unknown'
                elif case in ('invalid_json','json_commentary_only','invalid_schema','mismatch','interleaved'):
                    try:await asyncio.wait_for(task,15)
                    except ValueError as error:
                        if case in ('invalid_json','json_commentary_only','invalid_schema'):
                            assert state['stage']=='result_validation'
                            assert state['runtime_result']['terminal']['status']=='completed'
                        if case in ('invalid_json','json_commentary_only'):assert isinstance(error,json.JSONDecodeError)
                        if case=='invalid_schema':assert 'Output failed JSON Schema validation' in str(error)
                        if case=='mismatch':assert 'differs from streamed text' in str(error)
                        if case=='interleaved':assert 'interleaved assistant messages' in str(error)
                    else:raise AssertionError(f'{case} returned success')
                else:
                    result=await asyncio.wait_for(task,15)
                    assert result['choices'][0]['message']['content']==answer
                    assert result['choices'][0]['finish_reason']=='stop'
                    assert result['usage']['total_tokens']==13
                    assert state['runtime_result']['usage_scope']=='thread_total'
                    if case=='text':
                        assert ''.join(chunks)==answer and chunks.count('\n')==1, chunks
                        assert any(e.get('params',{}).get('item',{}).get('type')=='reasoning'
                                   for e in state['rpc'].events), 'Fixture did not reach Runtime reasoning events'
                assert chunks[0]==''
                if 'response_format' in body:
                    assert chunks==[''], 'JSON text escaped before complete validation'
                assert 'REASONING_FIXTURE_MARKER' not in ''.join(chunks)
                if case in ('invalid_json','json_commentary_only','invalid_schema','mismatch','interleaved','cancel'):
                    assert not (state['out']/'chat-completion.json').exists(), 'Failure persisted a successful completion'
                assert state['rpc'].proc.poll() is not None, 'Runtime survived request completion/cancellation'
                assert config_file.read_bytes()==config_before, 'Bridge changed the user configuration'
                assert len(requests)==1 and requests[0][1] is None, 'Unexpected retry or credentials'
            finally:
                gate.set()
                if not task.done():
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
    finally:
        gate.set();server.shutdown();server.server_close()
    assert not errors, errors


async def main():
    binary=find_runtime(os.environ.get('CODEX_TEST_RUNTIME',''))  # Verifies Runtime and code-mode-host hashes.
    assert subprocess.check_output([str(binary),'--version'],text=True).strip()=='codex-cli 0.153.4'
    for case in ('text','json','json_commentary','json_commentary_only','json_legacy','invalid_json','schema','schema_commentary','invalid_schema','mismatch','interleaved','cancel'):
        await check(binary,case)
    print('PASS: pinned Runtime live deltas, JSON phase isolation and legacy compatibility, validation, reasoning isolation, confirmed cancellation, child-only service tier override; 12 local requests, 0 official model calls')


if __name__=='__main__':asyncio.run(main())
