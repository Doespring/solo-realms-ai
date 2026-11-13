// Acceptance harness: real screen gestures, real Nat20, scripted offline AI.
const {chromium}=require('C:/Users/iwa41/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'reports/preview7'),base='http://127.0.0.1:19179',phase=process.argv[2]||'pause';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},...(phase==='resume'?{storageState:path.join(out,'browser-state.json')}:{})});
 await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const page=await context.newPage(),errors=[],trace=[];
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('response',async r=>{const req=r.request();if(req.url().includes('/screen'))trace.push({path:new URL(req.url()).pathname,status:r.status(),ms:Math.round((req.timing().responseEnd||0))});});
 const info={phase,mock_ai:true,live_ai:false,paid_calls:0,steps:[]};
 try{
  for(let attempt=0;attempt<30;attempt++){try{if((await context.request.get(base+'/api/health')).ok())break;}catch(e){}await new Promise(r=>setTimeout(r,300));}
  await page.goto(base);await page.waitForSelector('#new-nat20-campaign',{state:'attached'});
  let cid;
  if(phase==='pause'){
   await page.locator('[data-view="campaigns"]').click();
   await page.locator('#new-nat20-campaign').click();
   await page.locator('#nat20-campaign-name').fill('Приёмка · Мирон');
   await page.getByRole('button',{name:'Начать испытание',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#play.active')&&document.querySelector('#scene-title').textContent==='Письмо у старого фонаря');
   cid=await page.evaluate(()=>localStorage.getItem('solo-campaign'));
  }else{
   cid=JSON.parse(fs.readFileSync(path.join(out,'browser-pause.json'),'utf8')).cid;
   await page.locator('[data-view="play"]').click();
  }
  info.cid=cid;
  async function packet(){const r=await page.request.get(base+'/api/campaigns/'+cid+'/screen');assert.equal(r.status(),200);return r.json();}
  async function idle(){await page.waitForFunction(()=>!document.querySelector('#send').disabled,{},{timeout:45000});}
  async function say(text){await idle();await page.locator('#player-input').fill(text);await page.locator('#send').click();await idle();info.steps.push({text,feedback:await page.locator('#game-feedback').innerText()});}
  async function action(kind,match={}){
   await idle();const p=await packet();const c=p.commands.find(c=>c.parameters.kind===kind&&Object.entries(match).every(([k,v])=>c.parameters[k]===v));
   assert.ok(c,kind+' not available '+JSON.stringify(p.commands));
   if(p.snapshot.combat?.active&&!await page.locator('#battle-panel').evaluate(el=>el.open))await page.locator('#battle-panel > summary').click();
   if(kind==='move'&&!await page.locator('.game-movement').evaluate(el=>el.open))await page.locator('.game-movement > summary').click();
   await page.locator('.game-actions button:visible').getByText(c.label,{exact:true}).click();await idle();info.steps.push({action:kind});
  }
  if(phase==='pause'){
   await idle();await say('Поговорю с Ладой');await say('Осматриваю домик');await say('Осматриваю следы');
   await action('start_combat');await action('move');
   await action('use_feature');await say('Атакую гоблина');
   await page.locator('.game-restore > summary').click();
   await page.getByRole('button',{name:'Сохранить контрольную точку',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#game-feedback').textContent.includes('Контрольная точка сохранена'));
   info.before_restart=await packet();assert.ok(info.before_restart.snapshot.combat.active);
   await context.storageState({path:path.join(out,'browser-state.json')});
   await page.screenshot({path:path.join(out,'browser-midcombat.png'),fullPage:true});
  }else{
   await idle();const initial=await packet(),previous=JSON.parse(fs.readFileSync(path.join(out,'browser-pause.json'),'utf8')).before_restart;
   assert.deepEqual(initial.snapshot,previous.snapshot);info.restart_snapshot_equal=true;
   for(let i=0;i<25;i++){
    const p=await packet(),enemy=p.snapshot.combat.actors.find(a=>a.id!=='hero');
    assert.ok(!p.snapshot.terminal,'Unexpected defeat');
    if(enemy.hp<=0)break;
    if(p.snapshot.combat.current_actor!=='hero')await action('end_turn');
    else if(p.commands.some(c=>c.parameters.kind==='attack'))await action('attack');
    else await action('move');
   }
   await action('end_combat');
   let p=await packet();assert.ok(p.snapshot.scene.letter_received);info.reward=p.snapshot.scene;
   await action('short_rest',{hit_dice:1});await action('long_rest');
   p=await packet();assert.ok(!p.commands.some(c=>c.parameters.kind==='start_combat'));
   info.final=p.snapshot;
   await page.screenshot({path:path.join(out,'browser-victory.png'),fullPage:true});
  }
  info.ok=true;
 }catch(error){info.ok=false;info.error=String(error);info.stack=error.stack;await page.screenshot({path:path.join(out,'browser-failure-'+phase+'.png'),fullPage:true});process.exitCode=1;}
 finally{info.js_errors=errors;info.screen_timings=trace;fs.writeFileSync(path.join(out,'browser-'+phase+'.json'),JSON.stringify(info,null,2));await browser.close();console.log(JSON.stringify({phase,ok:info.ok,error:info.error,steps:info.steps.length,js_errors:errors}));}
})().catch(e=>{console.error(e);process.exit(1)});
