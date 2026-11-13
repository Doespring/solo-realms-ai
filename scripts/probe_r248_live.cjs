// r248: живой путь владельца через редактор героя на настоящем приложении.
// node scripts/probe_r248_live.cjs <base> <width> <height> <tag> <hero-name>
// Пишет снимки в reports/r248/evi и сетевой след в reports/r248/evi/<tag>-network.json.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,width,height,tag,hero]=process.argv.slice(2);
const out=path.resolve(__dirname,'../reports/r248/evi');fs.mkdirSync(out,{recursive:true});
const trace=[];const shots=[];
const short=value=>{const text=String(value==null?'':value);return text.length>900?text.slice(0,900)+'…':text;};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:Number(width),height:Number(height)},locale:'ru-RU'});
 const page=await context.newPage();page.setDefaultTimeout(60000);
 const errors=[];page.on('pageerror',error=>errors.push(String(error)));
 page.on('console',message=>{if(message.type()==='error')errors.push('console: '+message.text());});
 page.on('request',request=>{const url=request.url();if(!/\/api\/(prototype|campaigns)/.test(url))return;
  trace.push({at:Date.now(),kind:'request',method:request.method(),url:url.replace(base,''),body:short(request.postData())});});
 page.on('response',async response=>{const url=response.url();if(!/\/api\/(prototype|campaigns)/.test(url))return;
  let body='';try{body=short(await response.text());}catch(_){body='<нет тела>';}
  trace.push({at:Date.now(),kind:'response',status:response.status(),url:url.replace(base,''),body});});
 const shot=async name=>{const file=path.join(out,tag+'-'+name+'.png');await page.screenshot({path:file});shots.push(file);};
 const state=()=>page.evaluate(()=>window.soloWizard.state());
 const contract=async()=>(await state()).contract;
 const step=async id=>((await contract()).steps||[]).find(row=>row.id===id);
 async function waitIdle(){await page.waitForFunction(()=>{const root=document.getElementById('wizard');return root&&root.getAttribute('aria-busy')==='false';});}
 async function clickOption(name){const button=page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:name}).first();await button.click();await waitIdle();}
 async function navTo(stepId,choiceKey){const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');
  const index=list.findIndex(row=>row.id===stepId&&(!choiceKey||row.choice_key===choiceKey));assert(index>=0,'step '+stepId+' '+choiceKey+' нет в договоре');
  await page.locator('#wizard .wizard-list button').nth(index).click();await waitIdle();}
 async function fillScores(){await page.locator('#wizard .wizard-stage .wizard-option').filter({hasText:'Покупка за очки'}).first().click();await waitIdle();
  const inputs=page.locator('#wizard .wizard-scores input');const count=await inputs.count();assert.equal(count,6);
  const values=[15,14,13,12,10,8];for(let index=0;index<count;index++){await inputs.nth(index).fill(String(values[index]));await inputs.nth(index).dispatchEvent('change');await waitIdle();}
  const after=await step('scores');assert.equal(after.status,'complete',JSON.stringify(after.reasons));}
 async function fillChoices(){for(let guard=0;guard<16;guard++){const list=((await contract()).steps||[]).filter(row=>row.id!=='equipment');
  const open=list.find(row=>row.choice_key&&row.id!=='magic'&&Number(row.missing||0)>0);if(!open)return;await navTo(open.id,open.choice_key);
  if(open.kind==='increase'){for(const [row,times] of [[0,2],[1,1]])for(let count=0;count<times;count++){await page.locator('#wizard .wizard-stage .wizard-scores .wizard-score').nth(row).locator('button').last().click();await waitIdle();}continue;}
  const chosen=new Set(open.chosen||[]);const all=page.locator('#wizard .wizard-stage .wizard-option[data-id]');const total=await all.count();assert(total>0);
  let picked=false;for(let index=0;index<total;index++){const node=all.nth(index);if(chosen.has(await node.getAttribute('data-id')))continue;await node.click();await waitIdle();picked=true;break;}
  assert(picked,'не удалось ответить на '+open.choice_key);}}
 async function fillMagic(){for(let guard=0;guard<20;guard++){const magic=((await contract()).steps||[]).find(row=>row.id==='magic');if(!magic||magic.status==='complete')return magic;
  await navTo('magic');const all=page.locator('#wizard .wizard-stage .wizard-block .wizard-option:not([disabled])');const total=await all.count();assert(total>0,'нет заклинаний');
  const picked=new Set(magic.chosen||[]);let acted=false;for(let index=0;index<total;index++){const node=all.nth(index);if(picked.has(await node.getAttribute('data-id')))continue;await node.click();await waitIdle();acted=true;break;}
  assert(acted,'шаг магии не заполняется');}}
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloWizard);
  await page.evaluate(()=>window.soloWizard.open());await page.locator('#wizard .wizard-shell').waitFor();await waitIdle();
  const first=await contract();assert.equal(first.ready,false);
  await clickOption('Человек');await clickOption('Волшебник');await clickOption('Мудрец');
  await shot('02-species-class');
  await navTo('scores');await fillScores();
  await fillChoices();const magic=await fillMagic();
  await navTo('final');await shot('04-magic-and-final');
  await page.locator('#wizard .wizard-field input[type=text]').fill(hero);
  await page.locator('#wizard .wizard-field input[type=text]').dispatchEvent('change');await waitIdle();
  await page.locator('#wizard .wizard-field textarea').fill('Ищет следы старой башни и не доверяет страже.');
  await page.locator('#wizard .wizard-field textarea').dispatchEvent('change');await waitIdle();
  const before=await contract();assert.equal(before.confirm_ready,true,JSON.stringify(before.confirm_issues));
  await shot('05-wizard-final');
  const created=page.waitForResponse(r=>r.url().endsWith('/prototype/campaigns')&&r.request().method()==='POST');
  await page.locator('#wizard .wizard-confirm, #wizard button.primary').last().click();
  const response=await created;assert(response.ok(),await response.text());
  const id=(await response.json()).id;
  await page.waitForFunction(()=>document.getElementById('play').classList.contains('active'));
  await page.waitForTimeout(600);await shot('06-game-screen');
  // Первая реплика игрока и ответ Мастера: настоящая модель, один ход.
  const chat=page.locator('#chat');
  const beforeText=(await chat.innerText()).length;
  await page.locator('#player-input').fill('Осматриваюсь по сторонам.');
  await page.locator('#send').click();
  await page.waitForFunction(length=>document.getElementById('chat').innerText.length>length+40,beforeText,{timeout:180000});
  await page.waitForFunction(()=>!document.getElementById('send').disabled,{timeout:180000});
  await page.waitForTimeout(800);await shot('07-master-reply');
  const text=await chat.innerText();
  const sheet=await (await context.request.get(base+'/api/campaigns/'+id+'/character')).json();
  console.log(JSON.stringify({ok:true,view:{width:Number(width),height:Number(height)},campaign:id,hero,sheetName:sheet.identity&&sheet.identity.name,spells:magic&&(magic.chosen||[]).length,masterReply:text.slice(-400),errors,shots}));
  fs.writeFileSync(path.join(out,tag+'-network.json'),JSON.stringify(trace,null,1));
 }catch(error){await shot('failure');fs.writeFileSync(path.join(out,tag+'-network.json'),JSON.stringify(trace,null,1));throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
