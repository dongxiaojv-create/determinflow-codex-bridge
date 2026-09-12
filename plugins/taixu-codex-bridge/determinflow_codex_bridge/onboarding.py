"""Delegate browser login and credential storage to the pinned official CLI."""
import asyncio,contextlib,os,uuid
from pathlib import Path
from urllib.parse import urlparse


def login_url(line):
    value=line.strip()
    try:
        parsed=urlparse(value);port=parsed.port
    except ValueError:return None
    if parsed.scheme=='https' and parsed.hostname in ('auth.openai.com','chatgpt.com') and not parsed.username and not parsed.password and port in (None,443):
        return value
    return None


async def login(bridge,force=False):
    process=None
    bridge.onboarding={'phase':'checking','message':'正在检查登录状态…'}
    try:
        async with bridge.management:
            home=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').expanduser()
            home.mkdir(parents=True,exist_ok=True,mode=0o700)
            if not force:
                current=await bridge.management_rpc(False)
                if current['account']['type']=='chatgpt':
                    bridge.onboarding={'phase':'ready','message':'已登录，可以选择 Codex Bridge 模型开始使用。','account':current['account']}
                    return
            state=bridge.launch('login-'+uuid.uuid4().hex)
            # No shell, tokens, password prompt, or copied credentials. The CLI owns OAuth and its browser callback.
            process=await asyncio.create_subprocess_exec(state['runtime_command'][0],'login',
                env=state['runtime_env'],stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
            bridge.onboarding={'phase':'waiting','message':'请在官方浏览器页面登录自己的 ChatGPT 账户。'}
            async with asyncio.timeout(600):
                while line:=await process.stderr.readline():
                    url=login_url(line.decode('utf-8',errors='replace'))
                    if url:bridge.onboarding['auth_url']=url
                code=await process.wait()
            if code:raise RuntimeError('Official login did not complete')
            current=await bridge.management_rpc(False)
            if current['account']['type']!='chatgpt':raise RuntimeError('ChatGPT login not confirmed')
            bridge.onboarding={'phase':'ready','message':'登录成功，可以选择 Codex Bridge 模型开始使用。','account':current['account']}
    except asyncio.CancelledError:
        bridge.onboarding={'phase':'cancelled','message':'已取消本次登录。可以重新点击登录。'}
        raise
    except Exception:
        bridge.onboarding={'phase':'failed','message':'登录未完成。请检查网络和代理设置，然后重试登录。'}
    finally:
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):process.terminate()
            try:await asyncio.wait_for(process.wait(),3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):process.kill()
                await process.wait()
