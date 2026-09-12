"""Offline installer validation and official-login lifecycle checks; no real login."""
import asyncio,io,json,os,sys,tarfile,tempfile,types
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/taixu-codex-bridge'))
from determinflow_codex_bridge import setup_runtime as setup,onboarding


def archive(link=False):
    result=io.BytesIO()
    with tarfile.open(fileobj=result,mode='w:gz') as tar:
        for name in ('codex','codex-code-mode-host'):
            member=tarfile.TarInfo('package/vendor/aarch64-apple-darwin/bin/'+name)
            if link:member.type=tarfile.SYMTYPE;member.linkname='/tmp/escape'
            else:member.size=3
            tar.addfile(member,None if link else io.BytesIO(b'bin'))
    return result.getvalue()


def installer():
    payload=archive();calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_bytes(self):yield payload
    class Client(Response):
        def __init__(self,**kwargs):calls.append(kwargs)
        def stream(self,method,url):assert method=='GET' and url==setup.ARCHIVE;return Response()
    def verify(path):
        binary=Path(path);assert binary.read_bytes()==b'bin' and binary.with_name('codex-code-mode-host').read_bytes()==b'bin'
        return binary
    with tempfile.TemporaryDirectory() as temp,patch.object(setup.httpx,'Client',Client),patch.object(setup,'find_runtime',verify):
        data=Path(temp)
        binary=setup.install_runtime(data)
        assert binary.is_file() and binary.stat().st_mode&0o777==0o700
        assert setup.install_runtime(data)==binary and len(calls)==1
        payload=archive(link=True)
        try:setup.install_runtime(data/'malicious')
        except ValueError:pass
        else:raise AssertionError('Archive symlink admitted')
        assert not (data/'malicious/runtime-0.153.4').exists()
        payload=archive()
        with patch.object(setup,'find_runtime',side_effect=ValueError('hash mismatch')):
            try:setup.install_runtime(data/'corrupt')
            except ValueError:pass
            else:raise AssertionError('Unverified binary installed')
        assert not (data/'corrupt/runtime-0.153.4').exists()


async def login():
    assert onboarding.login_url('https://auth.openai.com/authorize?state=fixture')
    for url in ['http://auth.openai.com/x','https://auth.openai.com.evil.test/','https://evil@auth.openai.com/','https://chatgpt.com:bad/x','file:///etc/passwd','SECRET RAW LOG']:
        assert onboarding.login_url(url) is None
    with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CODEX_HOME':temp}):
        for case in ('existing','success','failure','cancel'):
            checks=[];spawned=[]
            class Process:
                returncode=None
                def __init__(self):self.stderr=self;self.reads=0
                async def readline(self):
                    self.reads+=1
                    if case=='cancel':await asyncio.Future()
                    return b'https://auth.openai.com/authorize?state=fixture\n' if self.reads==1 else b''
                async def wait(self):
                    if self.returncode is None:self.returncode=1 if case=='failure' else 0
                    return self.returncode
                def terminate(self):self.returncode=-15
                def kill(self):self.returncode=-9
            async def spawn(*args,**kwargs):
                assert args==('/fixture/codex','login') and kwargs['stdin']==asyncio.subprocess.DEVNULL
                p=Process();spawned.append(p);return p
            async def check(_):
                checks.append(1)
                return {'account':{'type':'chatgpt' if case=='existing' or len(checks)>1 else None,'email':'demo@example.test'}}
            bridge=types.SimpleNamespace(management=asyncio.Lock(),management_rpc=check,launch=lambda _:dict(runtime_command=['/fixture/codex'],runtime_env={}))
            with patch.object(asyncio,'create_subprocess_exec',spawn):
                task=asyncio.create_task(onboarding.login(bridge))
                if case=='cancel':
                    while not spawned:await asyncio.sleep(0)
                    task.cancel()
                try:await task
                except asyncio.CancelledError:assert case=='cancel'
            assert bridge.onboarding['phase']=={'existing':'ready','success':'ready','failure':'failed','cancel':'cancelled'}[case]
            assert 'auth_url' not in bridge.onboarding
            assert not spawned if case=='existing' else spawned[0].returncode is not None
            assert not bridge.management.locked()
    print('PASS: atomic verified install, idempotence, archive rejection, official URL, existing login, success, failure and cancellation')

if __name__=='__main__':
    installer();asyncio.run(login())
    if os.environ.get('CODEX_TEST_RUNTIME'):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            setup.prepare(data,{'codex_path':os.environ['CODEX_TEST_RUNTIME']})
            assert len(json.loads((data/'catalog.json').read_text()))>1
            assert not list(data.rglob('auth.json'))
        print('PASS: pinned official Runtime catalog prepared with blank login store; no model generation')
