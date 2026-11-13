const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
(async()=>{
const b=await chromium.launch({executablePath:'/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
const p=await (await b.newContext({viewport:{width:375,height:812}})).newPage();
await p.goto('http://127.0.0.1:46979',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(1500);
console.log(await p.evaluate(()=>{
 const out=[];
 out.push('master exists: '+!!document.querySelector('#master')+' class='+document.querySelector('#master')?.className);
 out.push('master hidden: '+document.querySelector('#master')?.hidden);
 view('campaigns');
 out.push('after campaigns: '+[...document.querySelectorAll('.view.active')].map(v=>v.id));
 view('master');
 out.push('after master: '+[...document.querySelectorAll('.view.active')].map(v=>v.id));
 out.push('view source: '+view.toString().slice(0,80));
 return out.join('\n');
}));
await b.close();})();
