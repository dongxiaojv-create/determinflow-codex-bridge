"""Fixed Runtime helpers extracted from the verified V1 entry; no probe ledger dependency."""
import asyncio,copy,hashlib,ipaddress,json,os,queue,re,subprocess,tempfile,threading,time,tomllib
from pathlib import Path
PROVIDER='taixu_phase0_signed_in'
MODEL='gpt-5.5'
DIRECTORY_METHODS=frozenset(('initialize','initialized','config/read','remoteControl/status/read','account/read','account/rateLimits/read','model/list'))
DIAGNOSTIC_LOG=('off,codex_otel.trace_safe=info,reqwest::connect=debug,hyper_util::client::legacy::connect::http=trace,hyper_util::client::legacy::client=trace')
def write(path,value):
    durable(path,value)


def durable(path, value):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write('\n'); file.flush(); os.fsync(file.fileno())
    os.replace(file.name, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def flatten(config, prefix=''):
    for key, value in config.items():
        assert re.fullmatch(r'[A-Za-z0-9_-]+',key), 'Unsupported dotted CLI key'
        key = prefix + key
        if isinstance(value, dict):
            yield from flatten(value, key + '.')
        else:
            yield key + '=' + json.dumps(value, ensure_ascii=False)

def overrides(lab, native, manual_fourth=False):
    config = tomllib.loads((Path(__file__).parent/'runtime.toml').read_text())
    del config['cli_auth_credentials_store']
    provider = config.pop('model_providers')['fixture']
    del provider['base_url']
    provider.update(name='Phase 0 existing native ChatGPT account', requires_openai_auth=True, supports_websockets=False)
    config.update(model=MODEL, model_provider=PROVIDER, model_providers={PROVIDER:provider}, service_tier='default', notify=[],
                  log_dir=str(lab/'log'), sqlite_home=str(lab/'sqlite'), history={'persistence':'none'},
                  analytics={'enabled':False}, feedback={'enabled':False},
                  otel={'exporter':'none','trace_exporter':'none','metrics_exporter':'none','log_user_prompt':False})
    config['mcp_servers'] = {name:{'enabled':False} for name in native.get('mcp_servers',{})}
    if manual_fourth:
        config['features']['respect_system_proxy']=False  # Child only: reqwest uses the explicit proxy environment.
    return config

def check_config(effective, expected):
    def check(actual, wanted):
        for key, value in wanted.items():
            assert key in actual, 'Missing effective override: ' + key
            if isinstance(value, dict):
                check(actual[key], value)
            else:
                assert actual[key] == value, 'Effective override mismatch: ' + key
    check(effective, expected)
    provider = effective['model_providers'][PROVIDER]
    for key in set(provider)-set(expected['model_providers'][PROVIDER]):
        assert provider[key] is (False if key=='supports_standalone_web_search' else None), 'Unexpected provider value: '+key
    assert all(x.get('enabled') is False for x in effective.get('mcp_servers',{}).values())
    assert effective.get('chatgpt_base_url') in (None, 'https://chatgpt.com/backend-api/')
    assert effective.get('model_catalog_json') is None
    assert effective.get('service_tier') in (None, 'default')
    assert effective.get('model_verbosity') in (None, 'low')

def restore_tool_config(reply):
    # Fixed v2 Config.ToolsV2 drops these fields; inspect the public raw config layers.
    tools = {}
    for layer in reversed(reply['layers']):
        if layer.get('disabledReason'):
            continue
        for name, value in layer['config'].get('tools',{}).items():
            if isinstance(value,dict) and isinstance(tools.get(name),dict):
                tools[name].update(value)
            else:
                tools[name]=copy.deepcopy(value)
    reply['config']['tools']=tools
    return reply['config']

def safe_api_event(line):
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event,dict) or not isinstance(event.get('fields',{}),dict):
        return None
    fields = event.get('fields', {})
    if event.get('target') != 'codex_otel.trace_safe' or fields.get('event.name') != 'codex.api_request':
        return None
    keys = ('event.name','conversation.id','http.response.status_code','attempt','duration_ms',
            'auth.retry_after_unauthorized','auth.recovery_mode','auth.recovery_phase')
    return {**{k:fields[k] for k in keys if k in fields}, 'error_present':bool(fields.get('error.message'))}

def safe_connection_event(line):
    # Fixed reqwest 0.12.28 / hyper-util 0.1.20 log sites. No raw fields leave this function.
    result = dict(scope='process', request_correlation='UNKNOWN', stage='UNKNOWN', error_category='UNKNOWN')
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return {**result, 'error_category':'malformed_log'}
    if not isinstance(event,dict) or not isinstance(event.get('fields'),dict):
        return {**result, 'error_category':'malformed_log'}
    fields = event['fields']; target = event.get('target'); level = event.get('level')
    if target == 'log':
        target = fields.get('log.target')
    if target == 'codex_otel.trace_safe' and fields.get('event.name') == 'codex.api_request' and level == 'INFO':
        for key, maximum in (('attempt',2**32-1),('duration_ms',2**63-1),('http.response.status_code',599)):
            value = fields.get(key)
            if key == 'duration_ms' and isinstance(value,str) and re.fullmatch(r'[0-9]{1,19}',value):
                value = int(value)  # Native telemetry uses Display for duration.
            minimum = 100 if key == 'http.response.status_code' else 0
            if type(value) is int and minimum <= value <= maximum:
                result[key] = value
            elif value is not None:
                return {**result, 'error_category':'malformed_log'}
        if 'attempt' not in result or 'duration_ms' not in result:
            return {**result, 'error_category':'malformed_log'}
        result['stage'] = 'api_attempt_finished'
        value = fields.get('conversation.id')
        if isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value):
            result['conversation.id'] = value
        if type(fields.get('auth.retry_after_unauthorized')) is bool:
            result['auth.retry_after_unauthorized'] = fields['auth.retry_after_unauthorized']
        elif fields.get('auth.retry_after_unauthorized') is not None:
            return {**result, 'stage':'UNKNOWN', 'error_category':'malformed_log'}
        error = fields.get('error.message')
        if error is not None and not isinstance(error,str):
            return {**result, 'stage':'UNKNOWN', 'error_category':'malformed_log'}
        result['error_present'] = bool(error)
        result['error_category'] = 'api_error_unspecified' if isinstance(error,str) and error else 'UNKNOWN'
        return result
    message = fields.get('message')
    if not isinstance(message,str):
        return {**result, 'error_category':'malformed_log'}
    if target == 'reqwest::connect' and level == 'DEBUG':
        if re.fullmatch(r"starting new connection: https?://[^\s]+",message):
            result['stage'] = 'connection_start'
        elif re.fullmatch(r"proxy\(.+\) intercepts 'https?://[^\s']+'",message):
            result['stage'] = 'proxy_intercepted'
    elif target == 'hyper_util::client::legacy::connect::http':
        match = re.fullmatch(r'(connecting to|connected to|connect error for) (\[[0-9a-fA-F:]+\]|[0-9.]+):([0-9]{1,5})(?:: (.+))?',message)
        if match:
            verb, host, port, error = match.groups()
            try:
                address = ipaddress.ip_address(host.strip('[]'))
                valid = 0 < int(port) < 65536
            except ValueError:
                valid = False
            if valid and level == 'DEBUG' and error is None and verb in ('connecting to','connected to'):
                result['stage'] = 'tcp_connecting' if verb == 'connecting to' else 'tcp_connected'
            elif valid and level == 'TRACE' and verb == 'connect error for' and error and re.fullmatch(r'ConnectError\(.+\)',error):
                result.update(stage='tcp_connect_failed', error_category='tcp_connect_error')
                # Only the exact Rust OS-error shape exposes an errno; arbitrary source chains stay unknown.
                detail = re.fullmatch(r'ConnectError\("tcp connect error", Os \{ code: ([0-9]{1,6}), kind: (ConnectionRefused|ConnectionReset|ConnectionAborted|NotConnected|AddrInUse|AddrNotAvailable|TimedOut|NetworkUnreachable|HostUnreachable|PermissionDenied|Other), message: "(?:[^"\\]|\\.)*" \}\)',error)
                if detail:
                    result.update(os_error_code=int(detail[1]), error_category=detail[2])
            if result['stage'] in ('tcp_connecting','tcp_connected','tcp_connect_failed'):
                result.update(peer_kind='loopback' if address.is_loopback else 'non_loopback',port=int(port),
                              expected_proxy_peer=address==ipaddress.ip_address('127.0.0.1') and int(port)==7890)
    elif target == 'hyper_util::client::legacy::client':
        fixed = {'ALPN negotiated h2, updating pool':'h2_negotiated',
                 'http2 handshake complete, spawning background dispatcher task':'http2_handshake_complete',
                 'http1 handshake complete, spawning background dispatcher task':'http1_handshake_complete',
                 'connection is ready':'connection_ready'}
        if level == 'TRACE' and message in fixed:
            result['stage'] = fixed[message]
        elif level == 'TRACE' and re.fullmatch(r'unstarted request canceled, trying again \(reason=(?:hyper_util::client::legacy::Error\(.+\)|CheckedOutClosedValue)\)',message):
            result['stage'] = 'transport_internal_retry'
        elif ((level == 'DEBUG' and re.fullmatch(r'client connection error: .+',message)) or
              (level == 'TRACE' and re.fullmatch(r'connection readiness failed: .+',message))):
            result.update(stage='connection_failed',error_category='connection_error_unspecified')
    return result


class RPC:
    def __init__(self, command, env, cwd, note, directory_diagnostic=False, capture_diagnostics=False):
        self.pending, self.inbox, self.seq = [], queue.Queue(), 0
        self.note, self.api_events, self.events = note, [], []
        self.directory_diagnostic = directory_diagnostic
        self.proc = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def read():
            try:
                for line in self.proc.stdout:
                    message = json.loads(line)
                    method = message.get('method')
                    # Never record whole account/config/thread replies or unsolicited account data.
                    if directory_diagnostic:
                        code = message.get('error',{}).get('code') if isinstance(message.get('error',{}),dict) else None
                        rid = message.get('id')
                        note('rpc_receipt', method=method if isinstance(method,str) and method in DIRECTORY_METHODS else 'UNKNOWN',
                             id=rid if isinstance(rid,str) and re.fullmatch(r'probe-[0-9]{1,10}',rid) else None,
                             error_code=code if type(code) is int and -32768 <= code <= 32767 else None)
                    else:
                        note('rpc_receipt', method=method, id=message.get('id'), error_code=message.get('error',{}).get('code'))
                    if not directory_diagnostic and method in ('turn/started','turn/completed','item/started','item/completed','thread/tokenUsage/updated'):
                        self.events.append(message)
                        note('public_event', message=message)
                    self.inbox.put(message)
            finally:
                self.inbox.put({'closed':self.proc.poll()})
        def stderr():
            for line in self.proc.stderr:
                if directory_diagnostic or capture_diagnostics:
                    diagnostic = safe_connection_event(line)
                    note('connection_diagnostic', **diagnostic)
                if directory_diagnostic:
                    continue
                if capture_diagnostics:
                    keys=('conversation.id','http.response.status_code','attempt','duration_ms','auth.retry_after_unauthorized','error_present')
                    event = {k:diagnostic[k] for k in keys if k in diagnostic} if diagnostic['stage']=='api_attempt_finished' else None
                else:
                    event = safe_api_event(line)
                if event is not None:
                    self.api_events.append(event); note('api_attempt', **event)
        self.readers = [threading.Thread(target=fn, daemon=True) for fn in (read,stderr)]
        for reader in self.readers:
            reader.start()
        note('runtime_started', pid=self.proc.pid)

    def send(self, message):
        if self.directory_diagnostic:
            if not isinstance(message,dict) or not isinstance(message.get('method'),str) or message['method'] not in DIRECTORY_METHODS or 'result' in message or 'error' in message:
                raise ValueError('Directory diagnostic forbids this outbound RPC')
            self.note('rpc_send', method=message['method'])
        else:
            self.note('rpc_send', message=message)
        self.proc.stdin.write(json.dumps(message) + '\n'); self.proc.stdin.flush()

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.note('runtime_kill', pid=self.proc.pid)
                self.proc.kill(); self.proc.wait(1)
        for reader in self.readers:
            reader.join(1)
        self.note('runtime_exit', pid=self.proc.pid, returncode=self.proc.returncode)

    def wait(self,predicate,timeout=15):
        end=time.monotonic()+timeout
        while True:
            for i,msg in enumerate(self.pending):
                if predicate(msg): return self.pending.pop(i)
            msg=self.inbox.get(timeout=max(0.01,end-time.monotonic()))
            if 'closed' in msg: raise RuntimeError(f'Runtime closed: {msg}')
            if predicate(msg): return msg
            self.pending.append(msg)
            if time.monotonic()>end: raise TimeoutError('RPC event timeout')

    def call(self,method,params):
        self.seq+=1;rid=f'probe-{self.seq}';self.send({'id':rid,'method':method,'params':params})
        msg=self.wait(lambda m:m.get('id')==rid and 'method' not in m)
        if 'error' in msg: raise ValueError(msg['error'])
        return msg['result']



def failure_diagnostic(state,operation):
    """Only report observed milestones; never infer remote acceptance from a timeout."""
    stage=state.get('stage','runtime_setup')
    terminal=(state.get('runtime_result') or {}).get('terminal') or {}
    status=terminal.get('status')
    if not state.get('turn_submission_attempted'):
        outcome='not_submitted';detail='尚未提交生成请求，请检查本机 Runtime、登录和配置。'
    elif status=='completed' and terminal.get('error') is None:
        outcome='completed';detail='Runtime 已完成生成，但桥接器未能交付有效结果；请先检查记录。'
    elif status in ('failed','interrupted'):
        outcome='runtime_'+status;detail='Runtime 已报告失败或中断；这不代表未消耗额度。'
    else:
        outcome='unknown';detail='已尝试提交，远端结果未知；请勿直接重复提交。'
    return dict(code='codex_'+outcome,operation=operation,stage=stage,outcome=outcome,
                message=f'Codex 请求失败（阶段：{stage}，编号：{operation}）。{detail}')


async def run_chat(state,normalized,operation,on_text=None):
    """One disposable Runtime per Deter request; Deter alone executes returned tools."""
    from .bridge_contract import checked_schema,require_json_object
    from jsonschema.exceptions import ValidationError
    note=state['note'];rpc=None;tid=uid=None;terminal=None;failure=None;calls=[];items=[];usage=None;interrupted=False
    validation_feedback_count=0;usage_scope='unknown'
    structured_output=(normalized.get('response_format') or {}).get('type','text')!='text'
    live_text=None if structured_output else on_text
    agent_id=None;sent_text=''
    async def admission():
        if not state['enabled']():raise RuntimeError('Provider stopped')
        # Once SSE starts, StreamingResponse owns disconnect detection and cancels this task.
        if not state.get('streaming') and await state['request'].is_disconnected():raise RuntimeError('HTTP request disconnected')
    async def emit_text(delta):
        nonlocal sent_text
        if not isinstance(delta,str):raise ValueError('Runtime text delta must be a string')
        if live_text:
            if not sent_text and items:await live_text('\n')
            if delta:await live_text(delta)
        sent_text+=delta
    def tool_call(message,allow_feedback=False):
        nonlocal validation_feedback_count
        p=message.get('params',{})
        if p.get('threadId')!=tid or p.get('turnId')!=uid:raise ValueError('Runtime tool request identity mismatch')
        alias=p.get('tool');fn=normalized['functions'].get(alias)
        if fn is None or p.get('namespace') is not None:raise ValueError('Runtime requested a tool not owned by Deter')
        call_id=p.get('callId');arguments=p.get('arguments')
        if not isinstance(call_id,str) or not call_id:raise ValueError('Runtime tool call ID missing')
        encoded=json.dumps(arguments,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if fn['strict']:
            try:checked_schema(fn['parameters']).validate(arguments)
            except ValidationError as error:
                if not allow_feedback or calls or validation_feedback_count:
                    raise ValueError('Tool arguments failed local strict schema validation: '+fn['name']) from error
                validation_feedback_count+=1
                # Return only validation feedback; the business tool has not executed.
                feedback={'error':'Tool arguments failed schema validation; the tool was NOT executed. Correct the arguments and call it again.',
                          'tool':alias,'keyword':error.validator,'path':list(error.absolute_path),'schema':fn['parameters']}
                rpc.send({'id':message['id'],'result':{'success':False,'contentItems':[{'type':'inputText','text':json.dumps(feedback,ensure_ascii=False)}]}})
                note('tool_validation_feedback',tool=fn['name'],keyword=error.validator,count=validation_feedback_count)
                return
        if not any(c['id']==call_id for c in calls):calls.append({'id':call_id,'type':'function','function':{'name':fn['name'],'arguments':encoded}})
    try:
        state['stage']='runtime_setup'
        await admission()
        rpc=RPC(state['runtime_command'],state['runtime_env'],state['lab']/'work',note,capture_diagnostics=True);state['rpc']=rpc
        await asyncio.to_thread(rpc.call,'initialize',{'clientInfo':{'name':'deter-codex-bridge','version':'1'},'capabilities':{'experimentalApi':True}})
        rpc.send({'method':'initialized'})
        effective=restore_tool_config(await asyncio.to_thread(rpc.call,'config/read',{'includeLayers':True,'cwd':str(state['lab']/'work')}))
        expected=state.get('expected_runtime') or overrides(state['lab'],{},manual_fourth=True)
        check_config(effective,expected)
        if (await asyncio.to_thread(rpc.call,'remoteControl/status/read',None))['status']!='disabled':raise ValueError('Runtime remote control is not disabled')
        state['stage']='authentication'
        account=await asyncio.to_thread(rpc.call,'account/read',{'refreshToken':False})
        if expected['model_providers'][PROVIDER]['requires_openai_auth'] and ((account.get('account') or {}).get('type')!='chatgpt' or account.get('requiresOpenaiAuth') is not True):raise ValueError('Native ChatGPT authentication is unavailable')
        await admission()
        state['stage']='thread_setup'
        reply=await asyncio.to_thread(rpc.call,'thread/start',dict(model=normalized['model'],modelProvider=PROVIDER,allowProviderModelFallback=False,
            cwd=str(state['lab']/'work'),environments=[],sandbox='read-only',approvalPolicy='never',baseInstructions=normalized['base_instructions'],
            developerInstructions=('Response format: return exactly one valid JSON object as the final answer. No Markdown fences or text outside the object. Preserve the fields required by the task; this format requirement does not change tool calls.' if (normalized.get('response_format') or {}).get('type')=='json_object' else ''),personality='none',dynamicTools=normalized['tools'],ephemeral=True))
        tid=reply['thread']['id']
        if reply.get('model')!=normalized['model'] or reply.get('modelProvider')!=PROVIDER:raise ValueError('Runtime changed the requested model or Provider')
        if normalized['history']:await asyncio.to_thread(rpc.call,'thread/inject_items',{'threadId':tid,'items':normalized['history']})
        await admission()
        params=dict(threadId=tid,input=normalized['input'],model=normalized['model'],environments=[])
        if normalized['effort'] is not None:params['effort']=normalized['effort']
        if normalized['output_schema'] is not None:params['outputSchema']=normalized['output_schema']
        state['stage']='turn_submission';state['turn_submission_attempted']=True
        uid=(await asyncio.to_thread(rpc.call,'turn/start',params))['turn']['id']
        state['stage']='generation'
        if on_text:await on_text('')  # Start SSE after submission; setup failures still return HTTP 400.
        while True:
            await admission()
            messages=rpc.pending[:];rpc.pending.clear()
            while True:
                try:messages.append(rpc.inbox.get_nowait())
                except queue.Empty:break
            for message in messages:
                if 'closed' in message:raise RuntimeError('Runtime exited before a confirmed result')
                method=message.get('method');p=message.get('params',{})
                if method=='item/tool/call' and 'id' in message:tool_call(message,allow_feedback=True)
                elif method and 'id' in message:raise ValueError('Unsupported Runtime server request: '+method)
                elif method in ('item/started','item/completed'):
                    if p.get('threadId')!=tid or p.get('turnId')!=uid:raise ValueError('Runtime item identity mismatch')
                    item=p['item']
                    if item['type'] not in ('userMessage','agentMessage','reasoning','dynamicToolCall'):raise ValueError('Runtime attempted a non-Deter tool: '+item['type'])
                    if item['type']=='agentMessage':
                        if method=='item/started':
                            if agent_id is not None:raise ValueError('Runtime interleaved assistant messages')
                            agent_id=item['id'];sent_text=''
                        else:
                            if agent_id!=item['id']:raise ValueError('Runtime assistant message identity mismatch')
                            text=item.get('text','')
                            if not isinstance(text,str) or not text.startswith(sent_text):raise ValueError('Runtime final text differs from streamed text')
                            await emit_text(text[len(sent_text):])
                            # Runtime progress is not part of a structured final answer; null phases remain compatible.
                            if not structured_output or item.get('phase')!='commentary':items.append(text)
                            agent_id=None;sent_text=''
                elif method=='item/agentMessage/delta':
                    if p.get('threadId')!=tid or p.get('turnId')!=uid or p.get('itemId')!=agent_id or agent_id is None:raise ValueError('Runtime text delta identity mismatch')
                    if p['delta']!='':await emit_text(p['delta'])
                elif method=='turn/completed' and p.get('threadId')==tid and p['turn']['id']==uid:terminal=p['turn']
            if calls or terminal is not None:break
            await asyncio.sleep(.01)
        if agent_id is not None:raise ValueError('Runtime assistant message is incomplete')
        if not calls and (terminal is None or terminal.get('status')!='completed' or terminal.get('error') is not None):raise RuntimeError('Runtime did not complete: '+str(terminal))
    except BaseException as error:
        failure=error
    finally:
        state['closing_runtime']=True
        if rpc is not None:
            if uid is None and tid:
                started=[e['params']['turn']['id'] for e in rpc.events if e.get('method')=='turn/started' and e['params'].get('threadId')==tid]
                if len(set(started))==1:uid=started[0]
            if tid and uid and terminal is None and rpc.proc.poll() is None:
                try:
                    rpc.send({'id':'bridge-interrupt','method':'turn/interrupt','params':{'threadId':tid,'turnId':uid}})
                    end=time.monotonic()+3;ack=False
                    while time.monotonic()<end and (not ack or terminal is None):
                        m=await asyncio.to_thread(rpc.wait,lambda m:m.get('id')=='bridge-interrupt' or (m.get('method')=='turn/completed' and m.get('params',{}).get('threadId')==tid and m['params']['turn']['id']==uid),max(.01,end-time.monotonic()))
                        if m.get('id')=='bridge-interrupt':ack='result' in m
                        else:terminal=m['params']['turn']
                    interrupted=bool(ack and terminal and terminal.get('status')=='interrupted')
                except Exception as error:note('interrupt_unconfirmed',error_type=type(error).__name__)
            await asyncio.shield(asyncio.to_thread(rpc.close))
            # One fresh thread per request: its final total includes correction rounds.
            # Read after shutdown to include usage received during the interrupt handshake.
            for event in reversed(rpc.events):
                p=event.get('params',{})
                if event.get('method')=='thread/tokenUsage/updated' and p.get('threadId')==tid and p.get('turnId')==uid:
                    token_usage=p.get('tokenUsage') or {}
                    if isinstance(token_usage.get('total'),dict):usage=token_usage['total'];usage_scope='thread_total'
                    elif isinstance(token_usage.get('last'),dict):usage=token_usage['last'];usage_scope='last_only'
                    break
            # A parallel tool proposal may arrive during the bounded interrupt handshake.
            remaining=rpc.pending[:]
            while True:
                try:remaining.append(rpc.inbox.get_nowait())
                except queue.Empty:break
            if calls and failure is None:
                try:
                    for message in remaining:
                        if message.get('method')=='item/tool/call':tool_call(message)
                except Exception as error:failure=error
            if calls and not interrupted and failure is None:failure=RuntimeError('Tool handoff cancellation was not confirmed; Runtime closed, remote state unknown')
        result=dict(operation=operation,thread_id=tid,turn_id=uid,terminal=terminal,interrupted=interrupted,state=('UNKNOWN' if terminal is None else 'FAILED') if failure else 'TOOL_HANDOFF' if calls else 'COMPLETED',usage=usage,usage_scope=usage_scope,error_type=type(failure).__name__ if failure else None,validation_feedback_count=validation_feedback_count)
        state['runtime_result']=result
        if callable(state.get('save')):state['save'](**result,runtime_usage=usage)
        durable(state['out']/'runtime-result.json',result)
    if failure is not None:raise failure
    state['stage']='result_validation'
    await admission()
    if not normalized['parallel_tool_calls']:calls=calls[:1]
    if normalized['required_tool'] and not calls:raise ValueError('Runtime did not satisfy requested tool_choice')
    text='\n'.join(items)
    if not calls:
        fmt=normalized.get('response_format') or {}
        if fmt.get('type')=='json_object':require_json_object(text)
        if normalized['output_schema'] is not None:
            value=json.loads(text,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Non-standard JSON constant')))
            try:checked_schema(normalized['output_schema']).validate(value)
            except Exception as error:raise ValueError('Output failed JSON Schema validation') from error
    message={'role':'assistant','content':text or None}
    if calls:message['tool_calls']=calls
    counts=None
    if usage is not None:
        counts={'prompt_tokens':usage['inputTokens'],'completion_tokens':usage['outputTokens'],'total_tokens':usage['totalTokens']}
        if 'reasoningOutputTokens' in usage:counts['completion_tokens_details']={'reasoning_tokens':usage['reasoningOutputTokens']}
        if 'cachedInputTokens' in usage:counts['prompt_tokens_details']={'cached_tokens':usage['cachedInputTokens']}
    completion=dict(id='chatcmpl-'+operation,object='chat.completion',created=state.get('created',int(time.time())),model=normalized['model'],choices=[{'index':0,'message':message,'finish_reason':'tool_calls' if calls else 'stop'}],usage=None if normalized['stream'] and not normalized['include_usage'] else counts,bridge_capabilities=normalized['capability_notes'])
    durable(state['out']/'chat-completion.json',completion)
    return completion
