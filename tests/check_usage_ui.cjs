// Run with NODE_PATH pointing to an existing Playwright installation.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const html=fs.readFileSync(path.join(__dirname,'../plugins/taixu-codex-bridge/ui/index.html'),'utf8');
  let offline=false;
  await page.route('http://bridge.test/**',async route=>{
   const url=route.request().url();
   if(!url.includes('/control/'))return route.fulfill({contentType:'text/html',body:html});
   if(offline)return route.fulfill({status:502,json:{error:'暂时不可用'}});
   if(url.endsWith('local-usage'))return route.fulfill({json:{totals:{requests:2,known:1,total:14},unreadable:1,models:[{model:'<img src=x onerror=alert(1)>',requests:2,known:1,input:10,output:4,total:14,cached:0,cached_known:0,reasoning:2,reasoning_known:1}]}});
   return route.fulfill({json:{account:{type:'chatgpt',email:'demo@example.test'},checked_at:1000,limits:[{id:'codex',primary:{usedPercent:0,windowDurationMins:10080,resetsAt:2000},credits:{balance:'0',unlimited:false}},{id:'other',credits:null,secondary:{usedPercent:null,windowDurationMins:300,resetsAt:null}}]}});
  });
  await page.goto('http://bridge.test/');await page.waitForFunction(()=>document.querySelector('#coverage').textContent.includes('14'));
  const limits=await page.locator('#limits').innerText();assert(limits.includes('剩余 100%'));assert(limits.includes('Credits 余额：0'));assert(limits.includes('Credits 余额：未知'));assert(limits.includes('已用 未知%'));
  assert((await page.locator('#coverage').innerText()).includes('缺失 1'));assert.equal(await page.locator('#tokens img').count(),0);
  await page.screenshot({path:'/tmp/codex-bridge-usage-ui.png',fullPage:true});
  offline=true;await page.locator('#usage').click();await page.waitForFunction(()=>document.querySelector('#limits').textContent.includes('额度未知'));
  assert.equal(await page.locator('#tokens tr').count(),0);assert.deepEqual(errors,[]);
  console.log('PASS: usage page renders zero/unknown, partial coverage, safe text and clears stale results on failure');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
