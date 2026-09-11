"""Offline regression for the actual task middleware and effort selection; no model calls."""
import ast,copy,json,sys,types
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'plugins/taixu-codex-bridge/determinflow_codex_bridge/taixu_bridge.py'

def main():
    # Load only the production selection path, so this also checks legacy installations.
    tree=ast.parse(SOURCE.read_text())
    bridge_class=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Bridge')
    method=next(n for n in bridge_class.body if isinstance(n,ast.FunctionDef) and n.name=='freeze_selection')
    nodes=[n for n in tree.body if getattr(n,'name',None) in ('DefaultSelection','build_request')]+[method]
    import re
    from fastapi.responses import JSONResponse
    scope=dict(re=re,json=json,JSONResponse=JSONResponse,PROVIDER='taixu_codex_limited')
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),scope)
    agent=types.SimpleNamespace(model='taixu_codex_limited:gpt-5.6-luna',model_params={})
    main_agent=types.SimpleNamespace(model='taixu_codex_limited:gpt-5.6-sol',model_params={})
    definitions={'main':main_agent,'writer':agent}
    module=types.ModuleType('src.agent.definition');module.get_agent_definition=definitions.get
    workflow={'definition':{'nodes':[{'id':'n','node_type':'agent','agent_type':'writer'}]}}
    bridge=types.SimpleNamespace(enabled=True,ready=True,runtime=types.SimpleNamespace(workflow_runtime=types.SimpleNamespace(get_workflow=lambda _:workflow)))
    bridge.freeze_selection=types.MethodType(scope['freeze_selection'],bridge)
    app=FastAPI();app.add_middleware(scope['DefaultSelection'],bridge=bridge)
    @app.post('/api/workflows/test/tasks')
    async def create(request:Request):return await request.json()
    with patch.dict(sys.modules,{'src.agent.definition':module}),TestClient(app) as client:
        for agent_effort,main_effort,explicit,expected in [
            ('low','high',None,'low'),('medium','high',None,'medium'),
            ('high','low',None,'high'),('low','high','medium','medium'),
            (None,'medium',None,'medium'),(None,None,None,'high'),
        ]:
            agent.model_params={'reasoning_effort':agent_effort}
            main_agent.model_params={'reasoning_effort':main_effort}
            body={'node_model_params_overrides':{'n':{'response_format':{'type':'json_object'}}}}
            if explicit:body['node_model_params_overrides']['n']['reasoning_effort']=explicit
            before=copy.deepcopy((body,workflow,agent.model_params,main_agent.model_params))
            response=client.post('/api/workflows/test/tasks',json=body)
            assert response.status_code==200,response.text
            frozen=response.json();overlay=frozen['node_model_params_overrides']['n']
            assert overlay['reasoning_effort']==expected,(agent_effort,main_effort,explicit,frozen)
            assert overlay['response_format']=={'type':'json_object'}
            assert frozen['node_model_overrides']['n']==agent.model
            effective=dict(agent.model_params);effective.update(overlay)
            assert scope['build_request'](effective,{})['extra_body']['reasoning_effort']==expected
            bridge.freeze_selection(body,'test')
            assert (body,workflow,agent.model_params,main_agent.model_params)==before
        agent.model='other:model'
        assert client.post('/api/workflows/test/tasks',json={}).json()['node_model_params_overrides']=={}
        main_agent.model='other:model'
        assert client.post('/api/workflows/test/tasks',json={}).json()=={}
    print('PASS: task middleware preserves agent low/medium, explicit effort, fallback, model and unrelated parameters')

if __name__=='__main__':main()
