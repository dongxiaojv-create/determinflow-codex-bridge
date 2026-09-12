// Run with NODE_PATH pointing to an existing Playwright installation.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(__dirname,'../plugins/taixu-codex-bridge/ui/index.html'),'utf8');
  const recent=[
   {operation:'<img src=x onerror=alert(1)>',model:'<img src=x onerror=alert(1)>',started_at:2000,ended_at:2001,duration_ms:1000,version:'0.3.10',effort:'low',state:'COMPLETED',validation_feedback_count:0,usage_scope:'thread_total',tokens:{input:0,output:0,total:0,cached:0,reasoning:0}},
   {operation:'partial',model:'sol',started_at:1000,state:'CANCELLED_LOCALLY',validation_feedback_count:1,usage_scope:'last_only',tokens:{input:10,output:4,total:14,cached:null,reasoning:2}},
   {operation:'new-unknown',model:'sol',version:'0.3.10',started_at:500,duration_ms:0,state:'FAILED_OR_UNKNOWN',usage_scope:'unknown',tokens:{input:null,output:null,total:null,cached:null,reasoning:null}},
   {operation:'legacy',model:'luna',started_at:null,state:'FAILED_OR_UNKNOWN',validation_feedback_count:null,usage_scope:'legacy',tokens:{input:null,output:null,total:null,cached:null,reasoning:null}},
  ];
  let offline=false,loginPhase='idle',loginUrl='https://auth.openai.com/authorize?state=fixture';
  await page.route('http://bridge.test/**',async route=>{
   const url=route.request().url();
   if(!url.includes('/control/'))return route.fulfill({contentType:'text/html',body:html});
   if(offline)return route.fulfill({status:502,json:{error:'暂时不可用'}});
   if(url.endsWith('login-start'))loginPhase='waiting';
   if(url.endsWith('login-cancel'))loginPhase='cancelled';
   if(/login-(start|cancel|status)$/.test(url))return route.fulfill({json:{phase:loginPhase,message:loginPhase==='ready'?'登录成功，可以开始使用':'fixture',...(loginPhase==='waiting'?{auth_url:loginUrl}:{})}});
   if(url.endsWith('local-usage'))return route.fulfill({json:{totals:{requests:2,known:1,total:14},unreadable:1,legacy_usage_requests:1,recent_limit:50,recent,models:[{model:'<img src=x onerror=alert(1)>',requests:2,known:1,input:10,output:4,total:14,cached:0,cached_known:0,reasoning:2,reasoning_known:1}]}});
   return route.fulfill({json:{account:{type:'chatgpt',email:'demo@example.test'},checked_at:1000,limits:[{id:'codex',primary:{usedPercent:0,windowDurationMins:10080,resetsAt:2000},credits:{balance:'0',unlimited:false}},{id:'other',credits:null,secondary:{usedPercent:null,windowDurationMins:300,resetsAt:null}}]}});
  });
  await page.goto('http://bridge.test/');await page.waitForFunction(()=>document.querySelector('#coverage').textContent.includes('14'));
  const limits=await page.locator('#limits').innerText();assert(limits.includes('剩余 100%'));assert(limits.includes('Credits 余额：0'));assert(limits.includes('Credits 余额：未知'));assert(limits.includes('已用 未知%'));
  assert((await page.locator('#coverage').innerText()).includes('缺失 1'));assert.equal(await page.locator('#tokens img').count(),0);
  assert((await page.locator('#coverage').innerText()).includes('1 次旧记录的用量范围未知'));
  assert.equal(await page.locator('#recent tr').count(),4);assert.equal(await page.locator('#recent img').count(),0);
  const cells=await page.locator('#recent tr').evaluateAll(rows=>rows.map(row=>Array.from(row.cells,cell=>cell.textContent)));
  assert(cells[0][1].includes('<img src=x onerror=alert(1)> / low / 0.3.10'));assert.equal(cells[0][2],'已完成');assert.equal(cells[0][3],'0 / 0 / 0');assert.equal(cells[0][4],'0 / 0');assert.equal(cells[0][5],'0');assert.equal(cells[0][6],'累计已报告用量');
  assert(cells[0][0].includes('耗时 1,000 ms'));
  assert.equal(cells[1][2],'本地中断');assert.equal(cells[1][4],'未知 / 2');assert.equal(cells[1][5],'1');assert(cells[1][6].includes('仅末次生成'));
  assert(cells[2][0].includes('耗时 0 ms'));assert.equal(cells[2][1],'sol / 未知 / 0.3.10');assert.equal(cells[2][6],'未收到用量报告');
  assert.equal(cells[3][0],'未知；耗时 未知 ms');assert.equal(cells[3][1],'luna / 未知 / 未知');assert.equal(cells[3][3],'未知 / 未知 / 未知');assert.equal(cells[3][5],'未知');assert.equal(cells[3][6],'旧记录：范围未知');
  assert((await page.locator('#recent tr').first().getAttribute('title')).includes('<img src=x onerror=alert(1)>'));
  assert((await page.locator('#recent-coverage').innerText()).includes('最多 50 次'));
  await page.click('#login');await page.waitForFunction(()=>!document.querySelector('#login-link').hidden);
  assert.equal(await page.locator('#login-link').getAttribute('href'),loginUrl);
  assert(await page.locator('#cancel-login').isVisible());assert(await page.locator('#login').isDisabled());
  await page.click('#cancel-login');await page.waitForFunction(()=>document.querySelector('#login-link').hidden);
  assert.equal(await page.locator('#login-link').getAttribute('href'),null);
  loginUrl='https://auth.openai.com.evil.test/';await page.click('#login');
  await page.waitForFunction(()=>!document.querySelector('#cancel-login').hidden);
  assert.equal(await page.locator('#login-link').getAttribute('href'),null);
  loginPhase='ready';await page.waitForFunction(()=>document.querySelector('#login-state').textContent.includes('登录成功'));
  assert(await page.locator('#login').isEnabled());assert(!(await page.locator('#cancel-login').isVisible()));
  await page.screenshot({path:'/tmp/codex-bridge-usage-ui.png',fullPage:true});
  offline=true;await page.locator('#usage').click();await page.waitForFunction(()=>document.querySelector('#limits').textContent.includes('额度未知'));
  assert.equal(await page.locator('#tokens tr').count(),0);assert.equal(await page.locator('#recent tr').count(),0);assert((await page.locator('#recent-coverage').innerText()).includes('近期调用未知'));assert.deepEqual(errors,[]);
  console.log('PASS: usage page renders recent metadata, scopes, zero/unknown, safe text and clears stale results on failure');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
