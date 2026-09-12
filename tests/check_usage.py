"""Offline usage regression: missing != zero, legacy fallback, no double counting."""
import ast,inspect,json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge.usage import local_usage,account_limits
from determinflow_codex_bridge.bridge_native import DIRECTORY_METHODS
from determinflow_codex_bridge import bridge_native
# Every to_thread RPC call must match the real bound RPC signature.
for node in ast.walk(ast.parse(inspect.getsource(bridge_native))):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=="to_thread" and node.args:
        fn=node.args[0]
        if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name) and fn.value.id=="rpc" and fn.attr=="call":
            inspect.signature(bridge_native.RPC.call).bind(None,*[None for _ in node.args[1:]])
assert "account/rateLimits/read" in DIRECTORY_METHODS
assert "turn/start" not in DIRECTORY_METHODS and "account/rateLimitResetCredit/consume" not in DIRECTORY_METHODS
with tempfile.TemporaryDirectory() as folder:
 root=Path(folder);(root/'attempts').mkdir()
 def put(name,record): (root/'attempts'/name).write_text(json.dumps(record))
 raw=dict(inputTokens=10,outputTokens=4,totalTokens=14,cachedInputTokens=3,reasoningOutputTokens=2)
 put('one.json',dict(operation='explicit-operation',model='sol',started_at=3,ended_at=4,duration_ms=1000,version='0.3.10',effort='low',state='COMPLETED',validation_feedback_count=1,usage_scope='thread_total',runtime_usage=raw,usage=dict(prompt_tokens=10,completion_tokens=4,total_tokens=14),prompt='private prompt',credential='private credential',diagnostic={'private':'detail'}))
 put('two.json',dict(model='sol',started_at=2,usage=None))
 out=root/'outputs/two';out.mkdir(parents=True);(out/'runtime-result.json').write_text(json.dumps(dict(usage=raw)))
 put('three.json',dict(model='sol',started_at=float('nan'),usage_scope='last_only',validation_feedback_count=-1,usage=None))
 put('four.json',dict(model='luna',started_at=0,ended_at=0,duration_ms=0,validation_feedback_count=0,usage=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)))
 put('five.json',dict(model='luna',started_at='not a timestamp',usage=dict(prompt_tokens=-1,completion_tokens=0,total_tokens=-1)))
 (root/'attempts/bad.json').write_text('{')
 stats=local_usage(root);t=stats['totals']
 assert (t['requests'],t['known'],t['total'],t['input'],t['output'],stats['unreadable'])==(5,3,28,20,8,1)
 assert t['cached']==6 and t['reasoning']==4 and t['cached_known']==2
 assert stats['recent_limit']==50 and stats['legacy_usage_requests']==3
 assert [row['operation'] for row in stats['recent']]==['explicit-operation','two','four','five','three']
 one,two,zero,bad,missing=stats['recent']
 assert set(one)=={'operation','model','started_at','ended_at','duration_ms','version','effort','state','validation_feedback_count','usage_scope','tokens'}
 assert one['tokens']==dict(input=10,output=4,total=14,cached=3,reasoning=2)
 assert (one['version'],one['effort'],one['validation_feedback_count'],one['usage_scope'])==('0.3.10','low',1,'thread_total')
 assert two['version'] is None and two['effort'] is None and two['validation_feedback_count'] is None and two['usage_scope']=='legacy'
 assert (zero['started_at'],zero['ended_at'],zero['duration_ms'],zero['validation_feedback_count'])==(0,0,0,0)
 assert zero['tokens']==dict(input=0,output=0,total=0,cached=None,reasoning=None)
 assert bad['tokens']['input'] is None and bad['tokens']['output']==0 and bad['tokens']['total'] is None
 assert missing['started_at'] is None and missing['validation_feedback_count'] is None and missing['usage_scope']=='last_only'
 assert all(value is None for value in missing['tokens'].values()) and 'private' not in json.dumps(stats)
 put('new-unknown.json',dict(model='sol',version='0.3.10',started_at=4,usage_scope='unknown',state='FAILED_OR_UNKNOWN'))
 unknown=local_usage(root)
 assert unknown['recent'][0]['usage_scope']=='unknown' and unknown['recent'][0]['version']=='0.3.10'
 assert unknown['legacy_usage_requests']==3 and unknown['totals']['requests']==6
 (root/'attempts/new-unknown.json').unlink()
 for index in range(55):put('bulk-'+str(index)+'.json',dict(model='sol',started_at=1000+index))
 capped=local_usage(root)
 assert capped['totals']['requests']==60 and capped['totals']['total']==28 and capped['legacy_usage_requests']==58
 assert len(capped['recent'])==50 and [row['started_at'] for row in capped['recent']]==list(range(1054,1004,-1))
 assert account_limits({'rateLimits':{'primary':None,'credits':None}})[0]['credits'] is None
 assert account_limits({})==[]
 limits=account_limits({'rateLimits':{'limitName':'old'},'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':0,'resetsAt':123},'credits':{'balance':'0','unlimited':False},'secret':'excluded'}}})
 assert len(limits)==1 and limits[0]['primary']['usedPercent']==0 and limits[0]['credits']['balance']=='0' and 'secret' not in limits[0]
print('PASS: raw/legacy usage, fallback, zero/unknown, recent sorting/50 limit, metadata whitelist, partial coverage and account limits')
