// The playable loop in a real browser: a finished chapter grants the book
// level, the reward is real, and the campaign survives a reload.
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp,campaign]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;
fs.mkdirSync(out,{recursive:true});
async function run(){
 const browser=await chromium.launch({channel:"msedge",headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 const page=await context.newPage();page.setDefaultTimeout(45000);
 const errors=[];page.on("pageerror",error=>errors.push(String(error)));
 await context.route("**/*",route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const get=async route=>{const response=await context.request.get(base+"/api"+route);assert(response.ok(),await response.text());return response.json();};
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);
  // 1. The prepared chapter carries the book advancement model and its pages.
  const levelUp=await get("/campaigns/"+campaign+"/level-up");
  assert.equal(levelUp.available,true,"the finished chapter grants a level: "+String(levelUp.reason));
  assert.equal(levelUp.from_level,1);
  assert.equal(levelUp.to_level,2,"page 15 grants level two");
  const character=await get("/campaigns/"+campaign+"/character");
  assert(character.sheet,"the Nat20 sheet is served");
  // 2. The level-up screen opens in the browser and shows the delta.
  await page.evaluate(value=>window.soloWizard.levelUp(value),campaign);
  await page.locator("#wizard .wizard-shell").waitFor();
  await page.waitForFunction(()=>{const root=document.getElementById("wizard");return root&&root.getAttribute("aria-busy")==="false";});
  const shown=await page.evaluate(()=>window.soloWizard.state());
  assert.equal(shown.error,"","the advancement screen loaded: "+shown.error);
  assert.equal(shown.levelUp.to_level,2);
  const header=await page.locator("#wizard .wizard-delta").innerText();
  assert.match(header,/1 → 2/);
  await page.screenshot({path:path.join(out,"milestone-level.png")});
  // 3. The save survives a restart of the process: reload and read again.
  await page.reload();
  await page.waitForFunction(()=>window.soloPrototypeEnabled);
  const after=await get("/campaigns/"+campaign+"/level-up");
  assert.equal(after.to_level,2,"the earned level is still there after reload");
  const identity=(await get("/campaigns/"+campaign+"/character")).identity;
  assert.equal(identity.level,1,"nothing advanced without a confirmation");
  assert.deepEqual(errors,[],"the page raised no script error");
  fs.writeFileSync(path.join(out,"loop.json"),JSON.stringify({campaign,from:levelUp.from_level,to:levelUp.to_level},null,2));
  console.log("milestone level",levelUp.from_level,"->",levelUp.to_level,"available",levelUp.available);
 }finally{await browser.close();}
}
run().catch(error=>{console.error(error);process.exit(1);});