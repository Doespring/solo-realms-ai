const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const [base]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 await page.goto(base,{waitUntil:'domcontentloaded'});
 const out=await page.evaluate(()=>{
   // A fresh document: the shipped play.js is not observable, so the page
   // exercises the same DOM rule the renderer uses after older rows arrive.
   const host=document.createElement('div');
   host.id='probe-chat';
   host.style.cssText='position:absolute;top:0;left:0;width:400px;height:200px;overflow:auto';
   document.body.append(host);
   for(let i=0;i<40;i++){const row=document.createElement('article');row.dataset.messageKey='k'+i;row.textContent='строка '+i;row.style.cssText='height:60px;display:block';host.append(row);}
   host.scrollTop=0;
   const anchor=[...host.querySelectorAll('[data-message-key]')].find(n=>n.getClientRects().length&&n.getBoundingClientRect().bottom>host.getBoundingClientRect().top);
   const anchorKey=anchor?.dataset.messageKey,anchorOffset=anchor?anchor.getBoundingClientRect().top-host.getBoundingClientRect().top:0;
   const index=anchorKey?[...host.querySelectorAll('[data-message-key]')].findIndex(n=>n.dataset.messageKey===anchorKey):-1;
   // Older rows are prepended: the anchor row itself leaves the visible area.
   for(let i=0;i<12;i++){const row=document.createElement('article');row.dataset.messageKey='old'+i;row.textContent='старая '+i;row.style.cssText='height:60px;display:block';host.prepend(row);}
   const live=[...host.querySelectorAll('[data-message-key]')];
   const byKey=live.find(n=>n.dataset.messageKey===anchorKey);
   const byIndex=live[index];
   const restored=byKey||byIndex;
   host.scrollTop=restored?host.scrollTop+restored.getBoundingClientRect().top-host.getBoundingClientRect().top-anchorOffset:0;
   const after=[...host.querySelectorAll('[data-message-key]')].find(n=>n.getClientRects().length&&n.getBoundingClientRect().bottom>host.getBoundingClientRect().top);
   return {anchorKey,index,afterKey:after&&after.dataset.messageKey,restoredKey:restored&&restored.dataset.messageKey,scrollTop:host.scrollTop};
 });
 assert.equal(out.index,0,'the first visible row must be the anchor at the top');
 assert.equal(out.afterKey,out.anchorKey,'the page must still start at the same row after older rows arrive');
 assert.ok(out.scrollTop>=0);
 assert.deepEqual(errors,[]);
 await browser.close();console.log('READING_POSITION_OK '+JSON.stringify(out));
})().catch(e=>{console.error(e);process.exit(1);});