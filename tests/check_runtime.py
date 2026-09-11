"""Exercise the model exec wrapper and Deter tool handoff locally; no official model call."""
import asyncio,hashlib,json,os,shutil,subprocess,sys,tempfile,threading,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ROOT=ROOT.parent
sys.path.insert(0,str(ROOT/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge import bridge_native as native
from determinflow_codex_bridge.bridge_contract import validate_chat,chat_sse

class Request:
    async def is_disconnected(self):return False

async def main():
    binary=Path(os.environ.get('CODEX_TEST_RUNTIME','codex'))
    binary=binary.resolve() if binary.is_file() else Path(shutil.which(str(binary)) or str(binary))
    from determinflow_codex_bridge.local_setup import find_runtime
    binary=find_runtime(str(binary))
    version=subprocess.check_output([str(binary),'--version'],text=True).strip()
    records=[]
    json_mode='--json-output' in sys.argv
    answer='{"answer":"SYNTHETIC TOOL RESULT RECEIVED"}' if json_mode else 'SYNTHETIC TOOL RESULT RECEIVED'
    repair='--validation-feedback' in sys.argv; invalid_again='--invalid-again' in sys.argv
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['content-length'])));records.append(body)
            resumed=any(x.get('type')=='function_call_output' for x in body['input'])
            if len(records)>(3 if repair else 2):
                self.send_error(400,'Unexpected extra sampling request');return
            invalid=repair and (len(records)==1 or invalid_again)
            item=({'type':'message','id':'msg-fixture','role':'assistant','content':[{'type':'output_text','text':answer}]} if resumed else
                  {'type':'custom_tool_call','id':'fc-fixture','call_id':'call-fixture','name':'exec','input':'const r = await tools.deter_0('+ ('{}' if invalid else '{value: "ok"}') +'); text(r);'})
            events=[{'type':'response.created','response':{'id':'resp-fixture'}},{'type':'response.output_item.done','item':item},
                    {'type':'response.completed','response':{'id':'resp-fixture','usage':{'input_tokens':10,'output_tokens':3,'total_tokens':13}}}]
            payload=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='generic-native-',) as temp:
        lab=Path(temp)
        for name in ('work','home','tmp','log','sqlite','bin'):(lab/name).mkdir()
        package_binary=binary
        shutil.copy2(binary,lab/'bin/codex')
        if '--missing-host' not in sys.argv:shutil.copy2(binary.with_name('codex-code-mode-host'),lab/'bin/codex-code-mode-host')
        binary=lab/'bin/codex'
        expected=native.overrides(lab,{},manual_fourth=True)
        expected['model_providers'][native.PROVIDER].update(base_url=f'http://127.0.0.1:{server.server_port}/v1',requires_openai_auth=False)
        command=[str(binary)]
        for value in native.flatten(expected):command+=['-c',value]
        command+=['app-server','--listen','stdio://']
        env=dict(PATH='/usr/bin:/bin',HOME=str(lab/'home'),CODEX_HOME=str(lab/'home'),CODEX_SQLITE_HOME=str(lab/'sqlite'),TMPDIR=str(lab/'tmp'),LANG='en_US.UTF-8',RUST_LOG=native.DIAGNOSTIC_LOG,LOG_FORMAT='json',NO_PROXY='127.0.0.1,localhost,::1')
        notes=[];states=[]
        def state():
            operation=uuid.uuid4().hex;out=lab/operation;out.mkdir()
            s=dict(runtime_command=command,runtime_env=env,lab=lab,out=out,note=lambda kind,**values:notes.append({'kind':kind,**values}),enabled=lambda:True,request=Request(),expected_runtime=expected)
            states.append(s);return s,operation
        tools=[{'type':'function','function':{'name':'shell','description':'A Deter-owned synthetic tool; Runtime must not execute it','strict':True,'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}}}]
        messages=[{'role':'system','content':'SYNTHETIC SYSTEM PRESERVED'}, {'role':'developer','content':'SYNTHETIC DEVELOPER PRESERVED'}, {'role':'user','content':'SYNTHETIC CALL THE DETER TOOL'}]
        body=dict(model='gpt-5.6-sol',reasoning_effort='medium',stream=True,messages=messages,tools=tools)
        if json_mode:body['response_format']={'type':'json_object'}
        s,op=state()
        if invalid_again:
            try:await asyncio.wait_for(native.run_chat(s,validate_chat(body),op),15)
            except ValueError as error:assert 'strict schema validation' in str(error)
            else:raise AssertionError('Repeated invalid arguments passed')
            assert len(records)==2 and s['runtime_result']['validation_feedback_count']==1
            assert s['rpc'].proc.poll() is not None
            print('PASS: repeated invalid arguments stop after one feedback; no business tool handoff')
            server.shutdown();server.server_close();return
        first=await asyncio.wait_for(native.run_chat(s,validate_chat(body),op),15)
        if repair:
            assert s['runtime_result']['validation_feedback_count']==1
            assert 'NOT executed' in json.dumps(records[1]['input'])
        assert first['choices'][0]['finish_reason']=='tool_calls'
        call=first['choices'][0]['message']['tool_calls'][0]
        assert call['function']=={'name':'shell','arguments':'{"value":"ok"}'} and s['runtime_result']['interrupted']
        assert s['rpc'].proc.poll() is not None
        messages=messages+[first['choices'][0]['message'],{'role':'tool','tool_call_id':call['id'],'content':'SYNTHETIC DETER EXECUTED RESULT'}]
        follow=validate_chat(dict(body,messages=messages));assert follow['input']==[]
        s,op=state();second=await asyncio.wait_for(native.run_chat(s,follow,op),15)
        assert second['choices'][0]['message']['content']==answer
        assert second['choices'][0]['finish_reason']=='stop' and s['rpc'].proc.poll() is not None
        assert len(records)==(3 if repair else 2),len(records)
        for record in records:
            assert ('Response format: return exactly one valid JSON object' in json.dumps(record)) is json_mode
        if json_mode:
            assert json.loads(second['choices'][0]['message']['content'])['answer']=='SYNTHETIC TOOL RESULT RECEIVED'
        assert any(x.get('call_id')==call['id'] and x.get('output')=='SYNTHETIC DETER EXECUTED RESULT' for x in records[-1]['input'])
        assert any(x.get('type')=='function_call' and x['name']=='deter_0' for x in records[-1]['input'])
        assert '"finish_reason": "tool_calls"' in chat_sse(first) and 'SYNTHETIC TOOL RESULT RECEIVED' in chat_sse(second)
        responses=[n['message']['result'] for n in notes if n['kind']=='rpc_send' and n.get('message',{}).get('result') is not None]
        assert len(responses)==int(repair) and all(r['success'] is False for r in responses), 'Only validation failures may be answered; Deter executes valid tools'
        out=lab/'evidence';out.mkdir()
        (out/'result.json').write_text(json.dumps({'status':'PASS','actual_runtime_version':version,'local_model_requests':len(records),'official_model_requests':0,'tool_result_empty_input_resumed':True,'deter_tools_only':True,'strict_schema':True,'runtime_closed_before_return':True,'results':[first,second],'runtime_results':[x['runtime_result'] for x in states]},ensure_ascii=False,indent=2))
        (out/'fixture-requests.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    server.shutdown();server.server_close()
    print('PASS: fixed Runtime + local fixture, Deter tool call/return, empty-input resume, no official model calls')

if __name__=='__main__':asyncio.run(main())
