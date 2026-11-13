const path=require('path');
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const ctx=await b.newContext({viewport:{width:375,height:812}});const p=await ctx.newPage();
p.on('pageerror',e=>console.log('PAGEERROR',e.message));
p.on('console',m=>{if(m.type()==='error')console.log('CONSOLE',m.text().slice(0,200));});
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(2500);
console.log('views',await p.evaluate(()=>[...document.querySelectorAll('.view')].map(v=>v.id+':'+v.className).join(' | ')));
console.log('navs',await p.evaluate(()=>[...document.querySelectorAll('.nav[data-view]')].map(n=>n.dataset.view+':'+n.getAttribute('aria-disabled')).join(',')));
await p.evaluate(()=>document.querySelector('.nav[data-view="campaigns"]').click());
await p.waitForTimeout(2500);
console.log('views2',await p.evaluate(()=>[...document.querySelectorAll('.view')].map(v=>v.id+':'+v.className).join(' | ')));
console.log('cards',await p.evaluate(()=>document.querySelectorAll('#campaign-list .list-card').length));
console.log('toast',await p.evaluate(()=>{const t=document.querySelector('#toast');return [t.hidden,t.textContent]}));
await b.close();})();
