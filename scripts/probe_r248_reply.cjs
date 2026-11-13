// r248: ответ Мастера в уже созданной кампании (повтор после внешнего 503).
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const fs=require('node:fs');
const [base,width,height,campaign,tag,out]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:Number(width),height:Number(height)},locale:'ru-RU'});
 await context.addInitScript(id=>{localStorage.setItem('solo-campaign',id);},campaign);
 const page=await context.newPage();page.setDefaultTimeout(90000);
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const trace=[];
 page.on('response',async r=>{if(!/\/command-turn/.test(r.url()))return;let body='';try{body=(await r.text()).slice(0,600);}catch(_){}
  trace.push({status:r.status(),body});});
 await page.goto(base,{waitUntil:'domcontentloaded',timeout:30000});await page.waitForFunction(()=>document.getElementById('play'));
 await page.locator('nav [data-view="play"], .nav[data-view="play"]').first().click().catch(()=>page.evaluate(()=>view('play')));
 await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));
 const chat=page.locator('#chat');
 for(let attempt=1;attempt<=2;attempt++){
  const before=(await chat.innerText()).length;
  await page.locator('#player-input').fill(attempt===1?'Иду к старой башне.':'Подхожу к старой башне и стучу в дверь.');
  await page.locator('#send').click();
  try{await page.waitForFunction(n=>document.getElementById('chat').innerText.length>n+40,before,{timeout:120000});
      await page.waitForFunction(()=>!document.getElementById('send').disabled,{timeout:120000});
      await page.waitForTimeout(800);
      break;}
  catch(error){if(attempt===2)throw error;}
 }
 const file=out+'/'+tag+'-08-master-reply.png';await page.screenshot({path:file});
 const refusal=page.locator('#toast');
 console.log(JSON.stringify({ok:true,campaign,errors,file,reply:(await chat.innerText()).slice(-500),turns:trace}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
