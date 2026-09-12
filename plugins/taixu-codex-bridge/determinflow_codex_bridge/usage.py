"""Read-only summaries. Local records are not an account billing ledger."""
import json,math
from collections import defaultdict


def local_usage(data):
    rows=defaultdict(lambda:dict(requests=0,known=0,input=0,output=0,total=0,cached=0,reasoning=0,cached_known=0,reasoning_known=0))
    unreadable=0;recent=[];legacy_usage_requests=0
    for file in (data/'attempts').glob('*.json'):
        try:
            record=json.loads(file.read_text())
            if not isinstance(record,dict):raise ValueError()
        except (OSError,ValueError):
            unreadable+=1;continue
        model=record.get('model')
        row=rows[model if isinstance(model,str) else '未知模型'];row['requests']+=1
        usage=record.get('runtime_usage') or record.get('usage')
        if not isinstance(usage,dict):
            try:usage=json.loads((data/'outputs'/file.stem/'runtime-result.json').read_text()).get('usage')
            except (OSError,ValueError,AttributeError):usage=None
        if not isinstance(usage,dict):usage={}
        tokens={name:usage.get(raw,usage.get(compat)) for name,raw,compat in (
            ('input','inputTokens','prompt_tokens'),('output','outputTokens','completion_tokens'),('total','totalTokens','total_tokens'))}
        for key,raw,details,field in (('cached','cachedInputTokens','prompt_tokens_details','cached_tokens'),('reasoning','reasoningOutputTokens','completion_tokens_details','reasoning_tokens')):
            detail=usage.get(details);tokens[key]=usage.get(raw,(detail if isinstance(detail,dict) else {}).get(field))
        tokens={key:value if type(value) is int and value>=0 else None for key,value in tokens.items()}
        item={key:record.get(key) if isinstance(record.get(key),str) else None for key in ('model','version','effort','state')}
        item['operation']=record.get('operation') if isinstance(record.get('operation'),str) else file.stem
        for key in ('started_at','ended_at','duration_ms'):
            value=record.get(key);item[key]=value if type(value) in (int,float) and (type(value) is int or math.isfinite(value)) and value>=0 else None
        count=record.get('validation_feedback_count');item['validation_feedback_count']=count if type(count) is int and count>=0 else None
        scope=record.get('usage_scope','legacy');item['usage_scope']=scope if scope in ('thread_total','last_only','unknown','legacy') else 'unknown'
        legacy_usage_requests+=item['usage_scope']=='legacy'
        item['tokens']=tokens;recent.append(item)
        values={key:tokens[key] for key in ('input','output','total')}
        if not all(type(v) is int and v>=0 for v in values.values()):continue
        row['known']+=1
        for key,value in values.items():row[key]+=value
        for key in ('cached','reasoning'):
            value=tokens[key]
            if type(value) is int and value>=0:row[key]+=value;row[key+'_known']+=1
    totals={key:sum(row[key] for row in rows.values()) for key in ('requests','known','input','output','total','cached','reasoning','cached_known','reasoning_known')}
    recent.sort(key=lambda row:(row['started_at'] is None,-(row['started_at'] or 0),row['operation']))
    return dict(totals=totals,models=[dict(model=model,**row) for model,row in sorted(rows.items())],unreadable=unreadable,recent=recent[:50],recent_limit=50,legacy_usage_requests=legacy_usage_requests)


def account_limits(reply):
    buckets=reply.get('rateLimitsByLimitId')
    if not isinstance(buckets,dict) or not buckets:
        legacy=reply.get('rateLimits');buckets={'codex':legacy} if isinstance(legacy,dict) else {}
    result=[]
    for key,bucket in buckets.items():
        if not isinstance(bucket,dict):continue
        row={k:bucket.get(k) for k in ('limitName','planType')};row['id']=key
        for name in ('primary','secondary'):
            window=bucket.get(name)
            row[name]={k:window.get(k) for k in ('usedPercent','windowDurationMins','resetsAt')} if isinstance(window,dict) else None
        credits=bucket.get('credits')
        row['credits']={k:credits.get(k) for k in ('hasCredits','unlimited','balance')} if isinstance(credits,dict) else None
        result.append(row)
    return result
