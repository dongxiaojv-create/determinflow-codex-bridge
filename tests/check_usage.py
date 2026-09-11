"""Offline usage regression: missing != zero, legacy fallback, no double counting."""
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge.usage import local_usage,account_limits
from determinflow_codex_bridge.bridge_native import DIRECTORY_METHODS
assert "account/rateLimits/read" in DIRECTORY_METHODS
assert "turn/start" not in DIRECTORY_METHODS and "account/rateLimitResetCredit/consume" not in DIRECTORY_METHODS
with tempfile.TemporaryDirectory() as folder:
 root=Path(folder);(root/'attempts').mkdir()
 def put(name,record): (root/'attempts'/name).write_text(json.dumps(record))
 raw=dict(inputTokens=10,outputTokens=4,totalTokens=14,cachedInputTokens=3,reasoningOutputTokens=2)
 put('one.json',dict(model='sol',runtime_usage=raw,usage=dict(prompt_tokens=10,completion_tokens=4,total_tokens=14)))
 put('two.json',dict(model='sol',usage=None))
 out=root/'outputs/two';out.mkdir(parents=True);(out/'runtime-result.json').write_text(json.dumps(dict(usage=raw)))
 put('three.json',dict(model='sol',usage=None))
 put('four.json',dict(model='luna',usage=dict(prompt_tokens=0,completion_tokens=0,total_tokens=0)))
 put('five.json',dict(model='luna',usage=dict(prompt_tokens=-1,completion_tokens=0,total_tokens=-1)))
 (root/'attempts/bad.json').write_text('{')
 stats=local_usage(root);t=stats['totals']
 assert (t['requests'],t['known'],t['total'],t['input'],t['output'],stats['unreadable'])==(5,3,28,20,8,1)
 assert t['cached']==6 and t['reasoning']==4 and t['cached_known']==2
 assert account_limits({'rateLimits':{'primary':None,'credits':None}})[0]['credits'] is None
 assert account_limits({})==[]
 limits=account_limits({'rateLimits':{'limitName':'old'},'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':0,'resetsAt':123},'credits':{'balance':'0','unlimited':False},'secret':'excluded'}}})
 assert len(limits)==1 and limits[0]['primary']['usedPercent']==0 and limits[0]['credits']['balance']=='0' and 'secret' not in limits[0]
print('PASS: raw/legacy usage, streaming fallback, zero/missing distinction, partial coverage, multi-bucket limits and field filtering')
