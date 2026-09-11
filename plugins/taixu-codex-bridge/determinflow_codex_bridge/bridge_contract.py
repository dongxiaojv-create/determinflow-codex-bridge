import json
from decimal import Decimal
from . import bridge_native as native

def require_json_object(text):
    def reject_constant(value): raise ValueError('Non-standard JSON constant')
    value=json.loads(text,parse_constant=reject_constant,parse_float=Decimal)
    if not isinstance(value,dict): raise ValueError('JSON output must be an object')

def checked_schema(schema):
    """Use the already bundled validator; schema resolution never opens a network URL."""
    from jsonschema.validators import validator_for
    if not isinstance(schema,dict):raise ValueError('JSON Schema must be an object')
    def local_refs(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('$ref','$dynamicRef') and (not isinstance(item,str) or not item.startswith('#')):
                    raise ValueError('Only local JSON Schema references are supported')
                local_refs(item)
        elif isinstance(value,list):
            for item in value:local_refs(item)
    local_refs(schema)
    validator=validator_for(schema)
    try:validator.check_schema(schema)
    except Exception as error:raise ValueError('Invalid JSON Schema') from error
    return validator(schema)


def map_chat_messages(messages,names):
    """ResponseItem mapping from the existing protocol probe, with tool-name round trips."""
    if not isinstance(messages,list) or not messages:raise ValueError('messages must be a nonempty list')
    items=[];pending=set();seen=set()
    for index,message in enumerate(messages):
        if not isinstance(message,dict):raise ValueError(f'message[{index}] must be an object')
        role=message.get('role')
        if role not in ('system','developer','user','assistant','tool'):raise ValueError('Unsupported message role')
        allowed={'role','content'}|({'tool_calls'} if role=='assistant' else {'tool_call_id'} if role=='tool' else set())
        if set(message)-allowed:raise ValueError('Unsupported message fields: '+','.join(sorted(set(message)-allowed)))
        content=message.get('content');calls=message.get('tool_calls',[])
        if not isinstance(calls,list):raise ValueError('tool_calls must be a list')
        if pending and role!='tool':raise ValueError('Missing results for preceding tool calls')
        if role=='tool':
            call_id=message.get('tool_call_id')
            if not isinstance(call_id,str) or call_id not in pending:raise ValueError('Unmatched or duplicate tool result')
            if not isinstance(content,str):raise ValueError('Tool results must be text')
            pending.remove(call_id);items.append({'type':'function_call_output','call_id':call_id,'output':content});continue
        if content is None:
            if role!='assistant' or not calls:raise ValueError('Only assistant tool calls can omit content')
        else:
            blocks=[{'type':'text','text':content}] if isinstance(content,str) else content
            if not isinstance(blocks,list) or any(not isinstance(b,dict) or set(b)!={'type','text'} or b['type']!='text' or not isinstance(b['text'],str) for b in blocks):
                raise ValueError('Only text message content is currently supported')
            items.append({'type':'message','role':role,'content':[{'type':'output_text' if role=='assistant' else 'input_text','text':b['text']} for b in blocks]})
        for call in calls:
            if not isinstance(call,dict) or set(call)!={'id','type','function'} or call['type']!='function':raise ValueError('Invalid tool call')
            call_id=call['id'];fn=call['function']
            if not isinstance(call_id,str) or not call_id or call_id in seen:raise ValueError('Duplicate or invalid tool call ID')
            if not isinstance(fn,dict) or set(fn)!={'name','arguments'} or not isinstance(fn['name'],str) or not fn['name'] or not isinstance(fn['arguments'],str):raise ValueError('Invalid function call')
            if fn['name'] not in names:names[fn['name']]='deter_'+str(len(names))
            seen.add(call_id);pending.add(call_id)
            items.append({'type':'function_call','call_id':call_id,'name':names[fn['name']],'arguments':fn['arguments']})
    if pending:raise ValueError('Tool calls require results before the next model request')
    return items


def validate_chat(body):
    import copy,math
    if not isinstance(body,dict):raise ValueError('Chat request must be an object')
    allowed={'model','messages','tools','stream','stream_options','reasoning_effort','response_format','tool_choice','parallel_tool_calls','n','temperature','top_p','presence_penalty','frequency_penalty'}
    if set(body)-allowed:raise ValueError('Unsupported chat controls: '+','.join(sorted(set(body)-allowed)))
    json.dumps(body,allow_nan=False)
    model=body.get('model');effort=body.get('reasoning_effort');stream=body.get('stream',False)
    if not isinstance(model,str) or not model.strip():raise ValueError('model must be nonempty text')
    if effort is not None and (not isinstance(effort,str) or not effort):raise ValueError('reasoning_effort must be text or null')
    if type(stream) is not bool:raise ValueError('stream must be boolean')
    if type(body.get('n',1)) is not int or body.get('n',1)!=1:raise ValueError('Runtime returns one choice per request; n must be 1')
    parallel=body.get('parallel_tool_calls',True)
    if type(parallel) is not bool:raise ValueError('parallel_tool_calls must be boolean')
    options=body.get('stream_options')
    if options is None:options={}
    if not isinstance(options,dict) or set(options)-{'include_usage'} or ('include_usage' in options and type(options['include_usage']) is not bool):raise ValueError('Unsupported stream_options')
    notes=[]
    for key,lower,upper in (('temperature',0,2),('top_p',0,1),('presence_penalty',-2,2),('frequency_penalty',-2,2)):
        value=body.get(key)
        if value is not None:
            if type(value) not in (int,float) or not math.isfinite(value) or not lower<=value<=upper:raise ValueError('Invalid '+key)
            notes.append({'parameter':key,'requested':value,'supported':False,'behavior':'Not forwarded: Runtime has no corresponding control; Runtime behavior applies'})
    tools=body.get('tools',[])
    if not isinstance(tools,list):raise ValueError('tools must be a list')
    names={};specs=[];functions={}
    for tool in tools:
        if not isinstance(tool,dict) or set(tool)!={'type','function'} or tool['type']!='function':raise ValueError('Only function tools are supported')
        fn=tool['function']
        if not isinstance(fn,dict) or set(fn)-{'name','description','parameters','strict'}:raise ValueError('Unsupported function declaration')
        name=fn.get('name');schema=fn.get('parameters',{'type':'object','properties':{}});strict=fn.get('strict',False)
        if not isinstance(name,str) or not name or name in names:raise ValueError('Duplicate or invalid tool name')
        if not isinstance(fn.get('description',''),str) or type(strict) is not bool:raise ValueError('Invalid tool description/strict')
        checked_schema(schema);alias='deter_'+str(len(names));names[name]=alias
        specs.append({'type':'function','name':alias,'description':fn.get('description',''),'inputSchema':copy.deepcopy(schema)})
        functions[alias]={'name':name,'parameters':copy.deepcopy(schema),'strict':strict}
        if strict:notes.append({'parameter':'tools.'+name+'.strict','supported':'local_validation','behavior':'Arguments validated before return to Deter; not native constrained decoding'})
    choice=body.get('tool_choice','auto');required=None
    if choice=='none':specs=[];functions={}
    elif choice=='required':
        if not specs:raise ValueError('tool_choice required needs tools')
        required='required'
    elif isinstance(choice,dict):
        if set(choice)!={'type','function'} or choice['type']!='function' or not isinstance(choice['function'],dict) or set(choice['function'])!={'name'}:raise ValueError('Invalid tool_choice')
        name=choice['function']['name']
        if not isinstance(name,str) or name not in names:raise ValueError('Chosen tool is not declared')
        specs=[spec for spec in specs if spec['name']==names[name]];functions={names[name]:functions[names[name]]};required=name
    elif choice!='auto':raise ValueError('Unsupported tool_choice')
    if required:notes.append({'parameter':'tool_choice','supported':'local_validation','behavior':'Only permitted tools advertised; response must contain the requested tool call'})
    messages=body.get('messages')
    if not isinstance(messages,list) or not messages:raise ValueError('messages must be a nonempty list')
    leading=[];offset=0
    while offset<len(messages) and isinstance(messages[offset],dict) and messages[offset].get('role')=='system':
        message=messages[offset]
        if set(message)!={'role','content'} or not isinstance(message['content'],str):raise ValueError('Leading system content must be text')
        leading.append(message['content']);offset+=1
    if any(isinstance(m,dict) and m.get('role')=='system' for m in messages[offset:]):raise ValueError('Runtime cannot preserve a system message after conversation history')
    if len(leading)>1:notes.append({'parameter':'messages.system','supported':'combined','behavior':'Consecutive leading system messages joined with two newlines in baseInstructions'})
    items=map_chat_messages(messages[offset:],names) if messages[offset:] else []
    turn_input=[]
    if items and items[-1].get('type')=='message' and items[-1].get('role')=='user':
        turn_input=[{'type':'text','text':block['text']} for block in items.pop()['content']]
    output_schema=None;response_format=body.get('response_format')
    if response_format is not None:
        if not isinstance(response_format,dict):raise ValueError('response_format must be an object')
        kind=response_format.get('type')
        if kind in ('text','json_object') and set(response_format)=={'type'}:
            if kind=='json_object':notes.append({'parameter':'response_format','supported':'instruction_and_local_validation','behavior':'Runtime receives a JSON-object output instruction and the complete output is validated; not native constrained decoding'})
        elif kind=='json_schema' and set(response_format)=={'type','json_schema'}:
            spec=response_format['json_schema']
            if not isinstance(spec,dict) or set(spec)-{'name','description','schema','strict'} or 'schema' not in spec:raise ValueError('Invalid response JSON Schema')
            if 'strict' in spec and type(spec['strict']) is not bool:raise ValueError('Invalid JSON Schema strict')
            checked_schema(spec['schema']);output_schema=copy.deepcopy(spec['schema'])
        else:raise ValueError('Unsupported response_format')
    return dict(model=model,effort=effort,base_instructions='\n\n'.join(leading),messages=copy.deepcopy(body['messages']),history=items,input=turn_input,tools=specs,functions=functions,
                required_tool=required,parallel_tool_calls=parallel,stream=stream,include_usage=options.get('include_usage',False),response_format=response_format,output_schema=output_schema,capability_notes=notes)


def chat_sse(completion):
    choice=completion['choices'][0];message=choice['message'];delta={'role':'assistant'}
    if message.get('content') is not None:delta['content']=message['content']
    if message.get('tool_calls'):delta['tool_calls']=[dict(call,index=i) for i,call in enumerate(message['tool_calls'])]
    base={key:completion[key] for key in ('id','created','model')};base['object']='chat.completion.chunk'
    if completion.get('bridge_capabilities'):base['bridge_capabilities']=completion['bridge_capabilities']
    chunks=[dict(base,choices=[{'index':0,'delta':delta,'finish_reason':None}]),dict(base,choices=[{'index':0,'delta':{},'finish_reason':choice['finish_reason']}])]
    if completion.get('usage') is not None:chunks.append(dict(base,choices=[],usage=completion['usage']))
    return ''.join('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
