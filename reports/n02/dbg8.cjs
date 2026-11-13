const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const CID='92a64f09-662f-4b23-811c-3e36bc8eb54e';
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const p=await (await b.newContext({viewport:{width:375,height:812}})).newPage();
p.on('pageerror',e=>console.log('PAGEERROR',e.message));
p.on('response',r=>{if(r.url().includes('/api/master'))console.log('RES',r.status(),r.url());});
await p.addInitScript(c=>{localStorage.setItem('solo-remember-campaign','on');localStorage.setItem('solo-campaign',c);},CID);
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3500);
console.log('active',await p.evaluate(()=>[...document.querySelectorAll('.view.active')].map(v=>v.id)));
console.log('master nav visible',await p.evaluate(()=>document.querySelector('.nav[data-view="master"]')?.offsetParent!==null));
await p.evaluate(()=>document.querySelector('.nav[data-view="master"]').click());
await p.waitForTimeout(2500);
console.log('active2',await p.evaluate(()=>[...document.querySelectorAll('.view.active')].map(v=>v.id)));
console.log('opts',await p.evaluate(()=>document.querySelector('#profile-select')?.options.length));
console.log('toast',await p.evaluate(()=>{const t=document.querySelector('#toast');return [t.hidden,t.textContent]}));
console.log('view src',await p.evaluate(()=>view.toString().slice(0,200)));
await b.close();})();
