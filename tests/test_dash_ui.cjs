// Real HTTP commands, capabilities and snapshots. No game-response interception.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,cid,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch(process.env.SOLO_UI_BROWSER?{executablePath:process.env.SOLO_UI_BROWSER,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']}:{channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 page.setDefaultTimeout(45000);
 const errors=[],requests=[],stages=[];
 page.on('pageerror',error=>errors.push(String(error)));
 page.on('request',request=>{if(request.method()==='POST'&&request.url().startsWith(base+'/api/'))requests.push({path:new URL(request.url()).pathname,body:request.postDataJSON()});});
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const get=async suffix=>{const response=await context.request.get(base+'/api/campaigns/'+cid+suffix);assert(response.ok(),await response.text());return response.json();};
 const screen=()=>get('/screen');
 const ready=()=>page.waitForFunction(()=>window.SoloGameScreen&&!document.getElementById('send').disabled);
 const dash=()=>page.getByRole('button',{name:'Рывок',exact:true});
 const capture=async(name,packet)=>{stages.push({name,packet});await page.screenshot({path:path.join(out,name+'.png')});};
 const verify=async packet=>{
  assert.equal(await dash().count(),packet.commands.some(c=>c.kind==='engine'&&c.parameters.kind==='dash')?1:0);
  // n14: хиты показываются один раз — в шапке («12/12 хиты»); в панели
  // «ВАШ ГЕРОЙ» строки «HP» больше нет (замечание №6 критика).
  assert.equal(await page.locator('.game-hp').count(),0,'дубль хитов вернулся в панель героя');
  assert.match(await page.locator('.vitals').innerText(),new RegExp(`(^|\\s)${packet.snapshot.hero.hp}/${packet.snapshot.hero.max_hp} хиты`));
  const text=await page.locator('#scene-hero').innerText();
  for(const resource of packet.snapshot.hero.resources){
   const value=resource.id.endsWith('_available')?(resource.current?'да':'нет'):String(resource.current);
   assert(text.includes(resource.label+': '+value),resource.id+' differs from server');
  }
  if(packet.snapshot.combat){const actors=await page.locator('.battle-actors').innerText();for(const actor of packet.snapshot.combat.actors)assert(actors.includes(actor.name+' · '+actor.hp+'/'+actor.max_hp+' HP · инициатива '+actor.initiative));}
 };
 const click=async label=>{
  await page.getByRole('button',{name:label,exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Действие подтверждено.')&&!document.getElementById('send').disabled);
  const packet=await screen();await verify(packet);return packet;
 };
 const submit=async text=>{
  const response=page.waitForResponse(r=>r.url().endsWith('/command-turn')&&r.request().method()==='POST');
  await page.locator('#player-input').fill(text);await page.locator('#send').click();
  const result=await (await response).json();await ready();
  const packet=await screen();await verify(packet);return {result,packet};
 };
 try{
  await page.goto(base);await page.evaluate(()=>{view('campaigns');document.body.classList.remove('effects');});
  const card=page.locator('#campaign-list .chronicle-card, #campaign-list .list-card').filter({hasText:'Dash UI · supported fighter'});
  await card.getByRole('button',{name:/Продолжить/}).click();await ready();
  let packet=await screen();await verify(packet);assert.equal(await dash().count(),0);
  const initial=structuredClone(packet),initialNative=(await get('')).state.nat20;
  const question=await submit('Осматриваю');assert.equal(question.result.status,'clarification');
  assert.equal(question.packet.snapshot.version,initial.snapshot.version);assert.deepEqual((await get('')).state.nat20,initialNative);
  const answer=await submit('следы');assert.equal(answer.result.status,'resolved');
  assert.equal(answer.result.events[0].payload.command,'check');
  assert.equal(answer.packet.snapshot.version,initial.snapshot.version+1);
  packet=await click('Войти во двор · начать бой');assert.equal(await dash().count(),1);
  const before=structuredClone(packet),beforeCampaign=await get('');
  const unsupported=await submit('Применяю Bless');assert.equal(unsupported.result.status,'unsupported');
  assert.equal(unsupported.packet.snapshot.version,before.snapshot.version);
  assert.deepEqual((await get('')).state.nat20,beforeCampaign.state.nat20);
  const textDash=await submit('Рывок');assert.equal(textDash.result.status,'resolved');
  assert.equal(textDash.result.events[0].payload.command,'dash');packet=textDash.packet;
  assert.equal(packet.snapshot.version,before.snapshot.version+1);
  assert.equal(packet.snapshot.hero.resources.find(r=>r.id==='action_available').current,0);
  assert(packet.snapshot.hero.resources.find(r=>r.id==='movement_remaining').current>before.snapshot.hero.resources.find(r=>r.id==='movement_remaining').current);
  assert.equal(await dash().count(),0);assert(!packet.commands.some(c=>c.parameters.kind==='attack'));
  assert.equal((await get('')).backend,'nat20');
  const textRequest=requests.filter(r=>r.path.endsWith('/command-turn')&&r.body.text==='Рывок');assert.equal(textRequest.length,1);
  assert.equal(textRequest[0].body.expected_version,before.snapshot.version);assert(textRequest[0].body.request_id);
  await capture('dash-text-spent-1280',packet);
  await click('Закончить ход'); // Independent, fresh native budget for button Dash.
  packet=await click('Рывок');
  const dashed=structuredClone(packet),dashedCampaign=await get('');
  const dashRequest=requests.filter(r=>r.path.endsWith('/commands')&&r.body.action.kind==='dash');assert.equal(dashRequest.length,1);
  assert.deepEqual(dashRequest[0].body.action,{kind:'dash'});assert(Number.isInteger(dashRequest[0].body.expected_version));assert(dashRequest[0].body.request_id);
  assert.equal(await dash().count(),0);assert.equal(packet.snapshot.hero.resources.find(r=>r.id==='action_available').current,0);
  assert(!packet.commands.some(c=>c.parameters.kind==='attack'));
  await capture('dash-spent-1280',packet);
  await page.locator('.game-restore>summary').click();
  await page.getByRole('button',{name:'Сохранить контрольную точку',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Контрольная точка сохранена.'));
  assert.equal((await get('')).saves.length,1);
  const next=await click('Закончить ход');
  assert.equal(await dash().count(),1);
  const beforeResources=Object.fromEntries(before.snapshot.hero.resources.map(r=>[r.id,r.current]));
  const nextResources=Object.fromEntries(next.snapshot.hero.resources.map(r=>[r.id,r.current]));
  assert.equal(nextResources.action_available,beforeResources.action_available);assert.equal(nextResources.movement_remaining,beforeResources.movement_remaining);
  await capture('next-turn-1280',next);
  await page.setViewportSize({width:960,height:640});
  await page.getByRole('button',{name:'Восстановить',exact:true}).click();
  await page.getByRole('button',{name:'Восстановить контрольную точку',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Сохранение восстановлено.')&&!document.getElementById('send').disabled);
  const restored=await screen();await verify(restored);
  assert.deepEqual((await get('')).state.nat20,dashedCampaign.state.nat20);
  assert.deepEqual(restored.snapshot.hero,dashed.snapshot.hero);assert.deepEqual(restored.snapshot.combat,dashed.snapshot.combat);assert.deepEqual(restored.snapshot.last_roll,dashed.snapshot.last_roll);
  assert.equal(await dash().count(),0);assert.equal(await page.locator('#chat .undone-history:not([open])').count(),1);
  const active=await page.locator('#chat [data-event]:not(.undone)').last().evaluate(e=>{const r=e.getBoundingClientRect(),p=e.closest('#chat').getBoundingClientRect();return r.bottom>p.top&&r.bottom<=p.bottom+2;});assert(active);
  await capture('dash-restored-960',restored);
  await page.reload();await ready();
  const reloaded=await screen();await verify(reloaded);
  assert.deepEqual(reloaded.snapshot,restored.snapshot);assert.deepEqual(reloaded.events,restored.events);
  await capture('dash-reloaded-960',reloaded);
  assert.equal(requests.filter(r=>r.path.endsWith('/play')).length,0);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({real_nat20:true,preview_mock:true,source_only:true,fixture_game_responses:false,text_dash:'PASS',button_dash:'PASS',unsupported_regression:'PASS',clarification_regression:'PASS',stages,requests,page_errors:errors},null,2));
  console.log(JSON.stringify({real_nat20:true,text_dash:'PASS',button_dash:'PASS',unsupported_regression:'PASS',clarification_regression:'PASS',server_resources:'PASS',repeat_unavailable:'PASS',next_turn_reset:'PASS',save_restore_reload:'PASS',small_window_restore:'PASS',page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,'failure.png')});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
