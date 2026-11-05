// Browser acceptance: the workshop page opens the prepared world in dialogue.
const {chromium}=require('playwright-core');
const EDGE='C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:EDGE,headless:true});
 const context=await browser.newContext({viewport:{width:1360,height:900}}),page=await context.newPage();
 const errors=[],checks=[];
 page.on('pageerror',e=>errors.push(String(e)));
 try{
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(3000);
  const shot=path.join(out,'entry.png');
  await page.screenshot({path:shot,fullPage:true});
  const text=await page.evaluate(()=>document.body.innerText);
  fs.writeFileSync(path.join(out,'entry.txt'),text);
  checks.push('page chars: '+text.length);
  // Does the interface expose the prepared draft at all?
  const api=await page.evaluate(async()=>{const r=await fetch('/api/creation/drafts');return await r.text();});
  checks.push('drafts api: '+api.slice(0,200));
  await browser.close();
 }catch(e){errors.push(String(e));await browser.close();}
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({checks,errors},null,2));
 console.log(JSON.stringify({checks,errors},null,2));
 if(errors.length)process.exitCode=1;
})();
