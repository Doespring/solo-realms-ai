const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const ctx=await b.newContext({viewport:{width:375,height:812}});const p=await ctx.newPage();
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(1500);
console.log(await p.evaluate(()=>[...document.querySelectorAll('[data-view="master"]')].map(e=>e.tagName+'.'+e.className+' visible='+(e.offsetParent!==null)+' onclick='+(typeof e.onclick)).join(' ; ')));
// открыть меню и кликнуть как игрок
const menu=await p.$('#menu-toggle')||await p.$('#menu');
console.log('menu',menu?await menu.getAttribute('id'):'none');
await p.evaluate(()=>{const m=document.querySelector('#menu-toggle,#menu,.menu-toggle,.topbar__menu');if(m)m.click();});
await p.waitForTimeout(600);
console.log(await p.evaluate(()=>[...document.querySelectorAll('[data-view="master"]')].map(e=>e.tagName+'.'+e.className+' visible='+(e.offsetParent!==null)).join(' ; ')));
await p.evaluate(()=>document.querySelector('.nav[data-view="master"]').click());
await p.waitForTimeout(2000);
console.log('active',await p.evaluate(()=>[...document.querySelectorAll('.view')].filter(v=>v.classList.contains('active')).map(v=>v.id)));
console.log('opts',await p.evaluate(()=>document.querySelector('#profile-select')?.options.length));
await b.close();})();
