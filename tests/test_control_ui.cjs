// Ordinary player UI over the real control campaign: no endpoint is faked.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,cid,out]=process.argv.slice(2);fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 // The player's own resume: the same localStorage key the campaigns screen sets.
 await context.addInitScript(id=>{localStorage.setItem('solo-campaign',id);},cid);
 const page=await context.newPage();const errors=[];const report={steps:[]};
 page.on('pageerror',e=>errors.push(String(e)));page.setDefaultTimeout(20000);
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const location=async()=>page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);return (await r.json()).state.location;},cid);
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);
  await page.waitForTimeout(2500);
  report.steps.push('opened');
  const nav=page.locator('[data-view="world"]');
  report.world_nav=await nav.count()>0;
  // The rail is inside a collapsible shell; the same button is pressed the way
  // the player presses it, through the router it is bound to.
  if(report.world_nav){await page.evaluate(()=>document.querySelector('[data-view="world"]').click());}
  await page.waitForTimeout(2000);
  const panel=page.locator('#world');
  report.world_visible=await panel.isVisible().catch(()=>false);
  const text=await panel.innerText().catch(()=>'');
  report.panel_text=text.slice(0,500);
  report.has_both=/Bell Landing/.test(text)&&/The Sunken Gallery/.test(text);
  // What the book keeps for the Master stays off the player's map: the printed
  // hint about avoiding the fight is a secret of the place, not a caption.
  report.secret_leaked=/without a fight|paid in salt/i.test(text);
  // A place named on a road is known by name; its page is not read until the
  // party stands there, so the map prints the neutral notice instead of the scene.
  report.page_read_early=/Water covers the floor/i.test(text)&&report.before!=='The Sunken Gallery';
  report.before=await location();
  const go=panel.getByRole('button',{name:/Идти: The Sunken Gallery/}).first();
  report.travel_button=await go.count()>0;
  if(report.travel_button){
   await go.click();await page.waitForTimeout(3000);
   report.steps.push('walked');
  }
  report.after=await location();
  const world=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id+'/world');return await r.json();},cid);
  report.offer=(world.pending_encounter||{}).id||null;
  report.routes=world.routes.length;
  const panelAgain=page.locator('#world');
  const refuse=panelAgain.getByRole('button',{name:/Отклонить столкновение|Обсудить столкновение/}).first();
  report.decline_button=await refuse.count()>0;
  if(report.decline_button){
   const text2=await panelAgain.innerText();
   report.offer_text=text2.slice(0,600);
   report.steps.push('offer_offered');
  }
  report.errors=errors;
 }finally{
  fs.writeFileSync(path.join(out,'ui-control.json'),JSON.stringify(report,null,2));
  await browser.close();
 }
 assert.equal(report.world_visible,true,'the world screen must open: '+JSON.stringify(report.steps));
 assert.equal(report.has_both,true,'both printed places are on the player map: '+JSON.stringify(report.panel_text));
 assert.equal(report.routes>0,true,'the printed road is usable');
 assert.equal(report.secret_leaked,false,'a place secret stays off the player map');
 assert.equal(report.page_read_early,false,'an unvisited scene is not read aloud on the map');
 assert.equal(report.errors.length,0,'no page errors: '+JSON.stringify(report.errors));
})().catch(error=>{console.error(error);process.exit(1);});