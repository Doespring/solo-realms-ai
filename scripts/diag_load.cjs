const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const base=process.argv[2];
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true});
 const page=await browser.newPage();
 const pending=new Map();
 page.on('request',r=>pending.set(r.url(),Date.now()));
 page.on('requestfinished',r=>pending.delete(r.url()));
 page.on('requestfailed',r=>{pending.delete(r.url());});
 const t0=Date.now();
 try{await page.goto(base,{waitUntil:'domcontentloaded',timeout:20000});}catch(e){console.log('goto error',e.message);}
 await page.waitForTimeout(12000);
 console.log('elapsed',Date.now()-t0);
 console.log('незавершённые:',[...pending.entries()].map(([u,t])=>u+' ('+(Date.now()-t)+'ms)'));
 console.log('readyState',await page.evaluate(()=>document.readyState));
 await browser.close();
})();
