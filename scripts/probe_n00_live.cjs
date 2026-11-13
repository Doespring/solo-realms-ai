// n00: живой вход после `partial` — на настоящем профиле r239 (книга разобрана),
// мастер офлайн-мок. Снимки 375 px и журнал сети (4xx/5xx) — в reports/n00.
// node scripts/probe_n00_live.cjs <base> <draft id> <out dir>
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,draft,outDir]=process.argv.slice(2);
const out=path.resolve(outDir||'reports/n00/evi');fs.mkdirSync(out,{recursive:true});
const WIDTH=375,HEIGHT=812;
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:WIDTH,height:HEIGHT},locale:'ru-RU'});
 const page=await context.newPage();page.setDefaultTimeout(90000);
 const pageErrors=[],httpBad=[];
 page.on('pageerror',e=>pageErrors.push(String(e)));
 page.on('console',m=>{if(m.type()==='error')pageErrors.push('console: '+m.text());});
 page.on('response',r=>{if(r.status()>=400)httpBad.push(r.status()+' '+r.request().method()+' '+new URL(r.url()).pathname);});
 const shots=[];
 const file_for=name=>path.join(out,'n00-'+name+'.png');
 const shot=async name=>{await page.locator('#creation-world').scrollIntoViewIfNeeded().catch(()=>{});await page.screenshot({path:file_for(name)});shots.push(file_for(name));};
 const report={width:WIDTH,height:HEIGHT};
 try{
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>typeof window.soloOpenCreation==='function');
  await page.evaluate(id=>window.soloOpenCreation(id),draft);
  await page.waitForFunction(()=>{const p=document.getElementById('creation-world');return p&&!p.hidden;});
  // Экран перерисовывается после ответа world-readiness: ждём не «видим», а «нарисован».
  await page.waitForFunction(()=>{const p=document.getElementById('creation-world');return p&&/Глава собрана|Мир подтверждён/.test(p.textContent);});
  const panel=page.locator('#creation-world');
  const message=(await panel.locator('p.hint').first().textContent()||'').trim();
  const buttons=await panel.locator('.button-row button').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent.trim(),hidden:n.hidden,disabled:n.disabled})));
  report.partial={message,buttons};
  await shot('1-partial-375');
  assert.ok(buttons.some(b=>b.text==='Начать с этой главой'&&!b.hidden),'кнопки входа нет: '+JSON.stringify(buttons));
  assert.ok(buttons.some(b=>b.text==='Проверить мир заново'&&!b.hidden),'вторичной ссылки нет: '+JSON.stringify(buttons));
  assert.ok(buttons.some(b=>b.text==='Выбрать героя Nat20'&&b.hidden),'герой не должен быть доступен до подтверждения');
  // Вход: подтверждение мира и герой.
  await panel.getByRole('button',{name:'Начать с этой главой',exact:true}).click();
  await page.locator('#world-confirm').waitFor({state:'visible'});
  await shot('2-confirm-dialog-375');
  await page.locator('#world-confirm').getByRole('button',{name:'Подтвердить этот мир',exact:true}).click();
  await page.waitForFunction(()=>{const b=[...document.querySelectorAll('#creation-world .button-row button')].find(n=>n.textContent.trim()==='Выбрать героя Nat20');return b&&!b.hidden;});
  report.confirmed={message:(await panel.locator('p.hint').first().textContent()||'').trim(),
   buttons:await panel.locator('.button-row button').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent.trim(),hidden:n.hidden})))};
  await shot('3-confirmed-375');
  // Герой: предложение заполняет открытые шаги, имя остаётся за игроком.
  await panel.getByRole('button',{name:'Выбрать героя Nat20',exact:true}).click();
  await page.locator('#wizard').waitFor({state:'visible'});
  await page.waitForFunction(()=>window.soloWizard.state().contract);
  const recommend=page.locator('#wizard').getByRole('button',{name:'Рекомендовать',exact:true});
  if(await recommend.isVisible())await recommend.click();
  await page.waitForFunction(()=>{const c=window.soloWizard.state().contract;return c&&(c.confirm_ready===undefined?c.ready:c.confirm_ready);},{timeout:120000});
  // Имя — последний шаг договора, без него сервер героя не создаёт.
  const steps=await page.locator('#wizard .wizard-list button').count();
  await page.locator('#wizard .wizard-list button').nth(steps-1).click();
  await page.waitForFunction(()=>{const i=document.querySelector('#wizard .wizard-stage input[type=text]');return Boolean(i);});
  await page.locator('#wizard .wizard-stage input[type=text]').first().fill('Рив Ночная Волна');
  await page.locator('#wizard .wizard-stage input[type=text]').first().dispatchEvent('change');
  await page.waitForFunction(()=>{const c=window.soloWizard.state().contract;return c&&(c.confirm_ready===undefined?c.ready:c.confirm_ready);});
  await shot('4-hero-ready-375');
  await page.locator('#wizard').getByRole('button',{name:'Подтвердить героя',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#play')?.classList.contains('active')&&!document.getElementById('player-input').disabled,{timeout:120000});
  await page.waitForFunction(()=>document.querySelectorAll('#chat .message, #chat article, #chat .turn').length>0||document.getElementById('chat').textContent.length>200,{timeout:120000});
  report.play={campaign:await page.evaluate(()=>localStorage.getItem('solo-campaign'))};
  await shot('5-play-375');
  const before=await page.locator('#chat').textContent();
  await page.locator('#player-input').fill('Осматриваюсь по сторонам и слушаю, что говорят вокруг.');
  await page.locator('#send').click();
  await page.waitForFunction(prev=>{const chat=document.getElementById('chat');return chat&&chat.textContent!==prev&&!document.getElementById('send').disabled;},before,{timeout:180000});
  const after=await page.locator('#chat').textContent();
  report.turn={grew:after.length>before.length,reply_excerpt:after.slice(-400).replace(/\s+/g,' ').trim()};
  await shot('6-turn-375');
 }catch(error){report.error=String(error&&error.stack||error);await shot('error-375').catch(()=>{});}
 report.page_errors=pageErrors;report.http_bad=httpBad;report.shots=shots;
 console.log(JSON.stringify(report,null,2));
 await browser.close();
 process.exit(report.error?1:0);
})();
