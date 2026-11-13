const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const ctx=await b.newContext({viewport:{width:375,height:812}});const p=await ctx.newPage();
p.on('pageerror',e=>console.log('PAGEERROR',e.message));
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(1500);
console.log('typeof view',await p.evaluate(()=>typeof view));
await p.evaluate(()=>view('master'));
await p.waitForTimeout(2500);
console.log('active',await p.evaluate(()=>[...document.querySelectorAll('.view')].filter(v=>v.classList.contains('active')).map(v=>v.id)));
console.log('opts',await p.evaluate(()=>document.querySelector('#profile-select')?.options.length));
console.log('toast',await p.evaluate(()=>{const t=document.querySelector('#toast');return [t.hidden,t.textContent]}));
await b.close();})();
