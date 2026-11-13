const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const ctx=await b.newContext({viewport:{width:375,height:812}});const p=await ctx.newPage();
p.on('request',r=>{if(r.url().includes('/api/'))console.log('REQ',r.method(),r.url().replace('http://127.0.0.1:46979',''));});
p.on('response',r=>{if(r.url().includes('/api/'))console.log('RES',r.status(),r.url().replace('http://127.0.0.1:46979',''));});
p.on('pageerror',e=>console.log('PAGEERROR',e.message));
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(2000);
await p.evaluate(()=>document.querySelector('.nav[data-view="campaigns"]').click());
await p.waitForTimeout(3000);
console.log('cards',await p.evaluate(()=>document.querySelector('#campaign-list').innerHTML.slice(0,300)));
await b.close();})();
