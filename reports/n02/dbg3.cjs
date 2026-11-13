const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const ctx=await b.newContext({viewport:{width:375,height:812}});const p=await ctx.newPage();
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(2000);
await p.evaluate(()=>document.querySelector('.nav[data-view="campaigns"]').click());
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>[...document.querySelectorAll('#campaign-list article, #campaign-list .list-card, #campaign-list .chronicle-card')].map((c,i)=>i+' :: '+c.tagName+'.'+c.className+' :: '+c.innerText.replace(/\s+/g,' ').slice(0,140)+' :: btns='+[...c.querySelectorAll('button')].map(b=>b.textContent+':'+b.disabled).join('/')).join('\n')));
await b.close();})();
