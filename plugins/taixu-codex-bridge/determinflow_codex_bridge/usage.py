"""Read-only summaries. Local records are not an account billing ledger."""
import json
from collections import defaultdict


def local_usage(data):
    rows=defaultdict(lambda:dict(requests=0,known=0,input=0,output=0,total=0,cached=0,reasoning=0,cached_known=0,reasoning_known=0))
    unreadable=0
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
        if not isinstance(usage,dict):continue
        values={name:usage.get(raw,usage.get(compat)) for name,raw,compat in (
            ('input','inputTokens','prompt_tokens'),('output','outputTokens','completion_tokens'),('total','totalTokens','total_tokens'))}
        if not all(type(v) is int and v>=0 for v in values.values()):continue
        row['known']+=1
        for key,value in values.items():row[key]+=value
        for key,raw,details,field in (('cached','cachedInputTokens','prompt_tokens_details','cached_tokens'),('reasoning','reasoningOutputTokens','completion_tokens_details','reasoning_tokens')):
            detail=usage.get(details);value=usage.get(raw,(detail if isinstance(detail,dict) else {}).get(field))
            if type(value) is int and value>=0:row[key]+=value;row[key+'_known']+=1
    totals={key:sum(row[key] for row in rows.values()) for key in ('requests','known','input','output','total','cached','reasoning','cached_known','reasoning_known')}
    return dict(totals=totals,models=[dict(model=model,**row) for model,row in sorted(rows.items())],unreadable=unreadable)


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
