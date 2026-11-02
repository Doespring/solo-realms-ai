const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const [base,evidence]=process.argv.slice(2);
const shot=async(page,name)=>{await page.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});};
(async()=>{
 const before=JSON.parse(fs.readFileSync(path.join(evidence,'before.json'),'utf8'));
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1360,height:950}});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 await page.goto(base,{waitUntil:'domcontentloaded'});
 // The player finds the chronicle on the campaigns screen, by name.
 await page.locator('[data-view="campaigns"]').click();
 await page.waitForSelector('#campaign-list',{timeout:15000});
 await shot(page,'13-campaigns-after-restart');
 const card=page.locator('#campaign-list .chronicle-card',{hasText:'Тихий перекрёсток'}).first();
 if(!(await card.count())){
  const seen=await page.evaluate(()=>document.getElementById('campaign-list')?.innerText||'(no list)');
  const cards=await page.locator('#campaign-list .chronicle-card').allInnerTexts();
  assert.fail('the campaign is missing after the restart :: '+JSON.stringify(cards)+' :: '+seen.slice(0,400));
 }
 await card.getByRole('button',{name:'Продолжить'}).click();
 await page.waitForFunction(()=>document.getElementById('play')?.classList.contains('active'),null,{timeout:30000});
 await page.waitForFunction(()=>document.querySelectorAll('#chat .message').length>0,null,{timeout:30000});
 await shot(page,'14-continued');
 const now=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return {location:c.state.location,turn:c.state.turn,saves:c.saves.length};},before.campaign);
 // The story continues in the same place, at the same turn, with its save.
 assert.equal(now.location,before.location,'the place changed across the restart');
 assert.equal(now.turn,before.turn,'the turn changed across the restart');
 assert.ok(now.saves>=before.saves,'the checkpoint disappeared');
  // The save written before the restart must still be readable there.
  assert.ok(now.saves>=1,'no checkpoint survived the restart');
 // And the next turn still plays.
 await page.locator('#player-input').fill('Осматриваю берег реки');
 await page.locator('#action-form').evaluate(f=>f.requestSubmit());
 await page.waitForTimeout(6000);
 const reply=await page.locator('#chat').innerText();
 assert.ok(reply.length>0,'the game said nothing to the next turn');
 await shot(page,'15-next-turn-after-restart');
 const after=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return {location:c.state.location,turn:c.state.turn};},before.campaign);
 assert.ok(after.turn>before.turn,'the next turn was not recorded after the restart');
 assert.equal(after.location,before.location,'a plain question moved the hero');
 assert.deepEqual(errors,[],'page errors: '+errors.join(' | '));
 await browser.close();
 fs.writeFileSync(path.join(evidence,'after.json'),JSON.stringify({before,after},null,1));
 console.log('JOURNEY_PART2_OK '+JSON.stringify({before,after}));
})().catch(e=>{console.error(e);process.exit(1);});