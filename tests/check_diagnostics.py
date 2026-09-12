"""Exercise production failure milestones and HTTP persistence without account access."""
import ast,asyncio,json,queue,sys,tempfile,types,uuid
from pathlib import Path
from unittest.mock import patch
from fastapi.responses import JSONResponse,StreamingResponse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge import bridge_native as native
from determinflow_codex_bridge.bridge_contract import validate_chat,chat_sse

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'attempts').mkdir()
        class Request:
            async def is_disconnected(self):return False
            async def body(self):return json.dumps({'model':'gpt-5.6-sol','messages':[{'role':'user','content':'fixture'}],'response_format':{'type':'json_object'}}).encode()
        class RPC:
            def __init__(self,*args,**kwargs):
                self.pending=[];self.inbox=queue.Queue();self.events=[];self.closed=False
                self.proc=types.SimpleNamespace(poll=lambda:0 if self.closed else None)
            def send(self,message):pass
            def wait(self,*args):raise TimeoutError('PRIVATE RAW ERROR')
            def close(self):self.closed=True
            def call(self,method,params):
                if method=='initialize':
                    if case=='setup':raise ValueError('PRIVATE RAW ERROR')
                    return {}
                if method=='config/read':return {}
                if method=='remoteControl/status/read':return {'status':'disabled'}
                if method=='account/read':
                    if case=='auth':raise ValueError('PRIVATE RAW ERROR')
                    return {}
                if method=='thread/start':return {'thread':{'id':'t'},'model':'gpt-5.6-sol','modelProvider':native.PROVIDER}
                if method=='turn/start':
                    if case=='unknown':raise TimeoutError('PRIVATE RAW ERROR')
                    if case=='cancel':raise asyncio.CancelledError()
                    terminal={'id':'u','status':'failed' if case=='failed' else 'completed'}
                    self.pending=[{'method':'turn/completed','params':{'threadId':'t','turn':terminal}}]
                    return {'turn':{'id':'u'}}
                raise AssertionError(method)
        source=ROOT/'plugins/taixu-codex-bridge/determinflow_codex_bridge/taixu_bridge.py'
        bridge_ast=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='Bridge')
        method=next(n for n in bridge_ast.body if getattr(n,'name',None)=='chat')
        scope=dict(asyncio=asyncio,json=json,uuid=uuid,time=__import__('time'),Request=Request,native=native,
                   validate_chat=validate_chat,JSONResponse=JSONResponse,StreamingResponse=StreamingResponse,chat_sse=chat_sse)
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),scope)
        for case,stage,outcome in [('setup','runtime_setup','not_submitted'),('auth','authentication','not_submitted'),
                                   ('unknown','turn_submission','unknown'),('failed','generation','runtime_failed'),
                                   ('invalid_json','result_validation','completed'),('cancel','turn_submission','unknown')]:
            state=dict(runtime_command=[],runtime_env={},lab=root,out=root,note=lambda *a,**k:None,
                       expected_runtime={'model_providers':{native.PROVIDER:{'requires_openai_auth':False}}})
            bridge=types.SimpleNamespace(authorize=lambda *a:None,enabled=True,ready=True,generation='fixture',
                                         launch=lambda _:state,data=root,active={},note=lambda *a,**k:None)
            with patch.object(native,'RPC',RPC),patch.object(native,'restore_tool_config',lambda x:x),patch.object(native,'check_config',lambda *a:None):
                try:response=await scope['chat'](bridge,'fixture',Request())
                except asyncio.CancelledError:assert case=='cancel'
                else:
                    assert case!='cancel' and response.status_code==400
                    diagnostic=json.loads(response.body)['error']
                    assert 'PRIVATE RAW ERROR' not in diagnostic['message']
                record=json.loads((root/'attempts'/(state['operation']+'.json')).read_text())
                diagnostic=record['diagnostic']
                assert (diagnostic['stage'],diagnostic['outcome'])==(stage,outcome),diagnostic
                assert diagnostic['operation']==state['operation']
                assert state['rpc'].closed and not bridge.active
                assert state['done'].done()
        print('PASS: setup/auth, uncertain submission, terminal failure, invalid JSON, cancellation and HTTP diagnostics')

if __name__=='__main__':asyncio.run(main())
