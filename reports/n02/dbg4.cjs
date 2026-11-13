const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const ctx=await b.newContext({viewport:{width:375,height:812}});const p=await ctx.newPage();
p.on('pageerror',e=>console.log('PAGEERROR',e.message));
p.on('console',m=>{if(m.type()==='error')console.log('CONSOLE',m.text().slice(0,200));});
p.on('response',r=>{if(r.url().includes('/api/master'))console.log('RES',r.status(),r.url());});
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(1500);
await p.evaluate(()=>document.querySelector('.nav[data-view="master"]').click());
await p.waitForTimeout(3000);
console.log('sel',await p.evaluate(()=>{const s=document.querySelector('#profile-select');return s?[s.id,s.tagName,s.options.length,[...s.options].map(o=>o.value).join(',')]:'none';}));
console.log('toast',await p.evaluate(()=>{const t=document.querySelector('#toast');return [t.hidden,t.textContent]}));
console.log('active',await p.evaluate(()=>[...document.querySelectorAll('.view')].filter(v=>v.classList.contains('active')).map(v=>v.id)));
await b.close();})();
