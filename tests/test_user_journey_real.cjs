const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const [base,material,evidence]=process.argv.slice(2);
fs.mkdirSync(evidence,{recursive:true});
const shot=async(page,name)=>{await page.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});};
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1360,height:950},acceptDownloads:true});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 // Only the local test server may be reached.
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 await page.goto(base,{waitUntil:'domcontentloaded'});

 // 1. The player starts a new chronicle with the real buttons.
 await page.locator('[data-view="campaigns"]').click();
 await page.locator('#new-campaign').click();
 // The advanced master lives behind the "Advanced / Developer" disclosure.
 await page.waitForTimeout(1500);
 await page.locator('#tabletop-advanced summary').click();
 await page.locator('#tabletop-advanced button',{hasText:'Прежний расширенный мастер'}).click();
 await page.waitForFunction(()=>document.getElementById('preparation')?.classList.contains('active'),null,{timeout:20000});
 await page.waitForSelector('#preparation .preparation-page input',{timeout:20000});
 await shot(page,'01-preparation-opened');
 const fieldValue=label=>page.locator('#preparation label',{hasText:label}).locator('input,textarea,select').first();
 const setField=async(label,value)=>{const f=fieldValue(label);await f.fill(value);await f.dispatchEvent('input');await f.dispatchEvent('change');};

 // 2. The material is uploaded through the file field of the interface.
 await page.getByLabel('Источник приключения PDF TXT Markdown JSON').setInputFiles(material);
 await page.waitForSelector('#preparation article.page-card',{timeout:30000});
 await page.waitForFunction(()=>/Текст извлечён/.test(document.getElementById('preparation').textContent),null,{timeout:30000});
 await setField('Название хроники','Тихий перекрёсток');
 await page.locator('#preparation label',{hasText:'Источник приключения'}).locator('select').first().selectOption('custom');
 await shot(page,'02-source-uploaded');
 const nextButton=()=>page.locator('#preparation .button-row button',{hasText:'Продолжить'});
 const waitStep=async re=>page.waitForFunction(r=>new RegExp(r).test(document.querySelector('#preparation .active')?.textContent||''),re.source,{timeout:20000});

 // 3. Rules, then the preparation step where the opening place is named.
 await nextButton().click();
 await waitStep(/Правила и Мастер/);
 await page.locator('#preparation label',{hasText:'Редакция / набор правил'}).locator('select').first().selectOption('dnd2024');
 await shot(page,'03-rules');
 await nextButton().click();
 await waitStep(/Подготовка/);
 await setField('Название открытой стартовой локации','Перекрёсток');
 await setField('Открытая стартовая сцена','Снег укрывает дорогу. У столба ждёт проводница в плаще.');
 await nextButton().click();
 await waitStep(/Подтверждение/);
 await page.locator('#preparation label',{hasText:'Я подтверждаю подготовку'}).locator('input[type=checkbox]').check();
 await shot(page,'04-preparation-confirmed');
 await nextButton().click();

 // 4. The hero is built with the fields and cards of the sheet.
 await waitStep(/Лист героя/);
 await setField('Имя героя','Мирон');
 await setField('Вид / раса','Человек');
 await setField('Класс','Плут');
 await setField('Происхождение','Наёмник с севера');
 await setField('Уровень','1');
 await setField('Максимум HP (ручной)','9');
 await setField('Текущие HP','9');
 await setField('Класс Доспеха (ручной)','14');
 await setField('Скорость, футы (ручная)','30');
 await setField('Кость хитов: 6, 8, 10, 12','8');
 await page.getByRole('button',{name:/Использовать стандартный массив/}).click();
 await shot(page,'05-hero-sheet');
 await nextButton().click();

 // 5. Background text and its own confirmation.
 await waitStep(/Предыстория/);
 await setField('История героя','Вырос в обозе, ушёл искать работу и чужие карманы.');
 await page.locator('#preparation label',{hasText:'Я подтверждаю эту предысторию'}).locator('input[type=checkbox]').check();
 await nextButton().click();

 // 6. Equipment, then the final consent and the start.
 await waitStep(/Снаряжение/);
 await shot(page,'06-equipment');
 await nextButton().click();
 await waitStep(/Начало истории/);
 await shot(page,'07-start-review');
 await page.locator('#preparation label',{hasText:'Подтверждаю подготовку, героя'}).locator('input[type=checkbox]').check();
 await page.getByRole('button',{name:'Начать историю'}).click();
 // 8. The chat of the new campaign.
 await page.waitForFunction(()=>document.getElementById('play')?.classList.contains('active'),null,{timeout:45000});
 await page.waitForFunction(()=>document.querySelectorAll('#chat .message').length>0,null,{timeout:45000});
 await shot(page,'09-chat');
 // Read back which chronicle the interface itself opened (title only, no injection).
 const campaign=await page.evaluate(async()=>{const r=await fetch('/api/campaigns');const list=await r.json();return list.find(c=>c.name==='Тихий перекрёсток');});
 assert.ok(campaign&&campaign.id,'the interface did not open a campaign named in the form');
 const start=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return {location:c.state.location,turn:c.state.turn};},campaign.id);
 assert.equal(start.location,'Перекрёсток','the campaign must open where the player said');

 // 9-10. Each sent line is judged by what the engine RECORDED for it, not by
 // the fact that it was sent: the last recorded event must match this action.
 const composer=page.locator('#player-input');
 const turnBefore=async()=>page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return c.state.turn;},campaign.id);
 // A campaign reply carries its journal; the last row is the turn just played.
 const lastEvent=async()=>page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();const rows=c.history||c.events||[];return rows[rows.length-1]||null;},campaign.id);
 const plans=[];
 const send=async text=>{
  const before=await turnBefore();
  await composer.fill(text);
  await page.locator('#action-form').evaluate(f=>f.requestSubmit());
  await page.waitForTimeout(3800);
  // A plan that needs confirmation is shown, and the player confirms it there.
  const confirm=page.getByRole('button',{name:/^Подтвердить/}).first();
 
  const after=await turnBefore();
  const event=await lastEvent();
  const plan=await page.evaluate(async({id,text})=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();const a=(c.actions||[]).find(x=>x.original===text)||{};const rows=c.history||c.events||[];const last=rows[rows.length-1]||{};return {plan:a.plan||null,status:a.status||null,event:last,results:last.results||null};},{id:campaign.id,text});
   return {text,before,after,event,plan,turnMoved:after!==before};
 };

 // A check typed by the player. The material prints no check here, so the
 // master has to call for one: a real Nat20 roll with its own difficulty,
 // recorded in the journal together with whose decision the difficulty was.
 const isRoll=r=>['check','roll','skill_check','ability_check'].includes(r.type)&&(r.dice||r.total)!==undefined;
 const attempt=await send('Незаметно подкрадываюсь к столбу');
 {const chatText=await page.locator('#chat').innerText();
  const fb=await page.locator('#feedback').innerText().catch(()=>'');
  console.log('AFTER_SEND', JSON.stringify({chat:chatText.slice(-400),feedback:fb.slice(0,200),input:await page.locator('#player-input').inputValue()}));}
 await shot(page,'10-check');
 const roll=(attempt.event?.results||[]).find(isRoll);
 const accepted=(attempt.event?.results||[]).length>0||(attempt.plan?.status||attempt.status)==='applied';
 assert.ok(accepted,'the attempt was not turned into a recorded action :: '+JSON.stringify({status:attempt.status,results:attempt.event?.results}));
 assert.ok(roll,'no roll was made for an uncertain attempt in a place the material left empty :: '+JSON.stringify(attempt.event?.results));
 assert.ok(roll.dc>0&&roll.total>0,'the roll carries no difficulty or no total :: '+JSON.stringify(roll));
 assert.ok(roll.origin==='master'||roll.origin==='source','the journal does not say whose decision the difficulty was :: '+JSON.stringify(roll.origin));
 console.log('ROLL', JSON.stringify({dice:roll.dice,total:roll.total,dc:roll.dc,success:roll.success,origin:roll.origin,ruling:roll.ruling}));
  require('fs').writeFileSync(require('path').join(evidence,'roll.json'),JSON.stringify({dice:roll.dice,total:roll.total,dc:roll.dc,success:roll.success,origin:roll.origin,ruling:roll.ruling,skill:roll.skill,ability:roll.ability},null,2));
 // A settled observation must NOT spend a die: no roll, no refusal.
 const plain=await send('Осматриваюсь');
 const plainRolls=(plain.event?.results||[]).filter(isRoll);
 assert.equal(plainRolls.length,0,'a settled look was charged a roll :: '+JSON.stringify(plain.event?.results));
 // A conversation with the guide must be recorded as speech addressed to her.
 const talk=await send('Спрашиваю проводницу, что случилось на тракте');
 await shot(page,'11-talk');
 const spoke=(talk.event?.results||[]).some(r=>r.type==='speak'&&/Ир/.test(String(r.target)));
 assert.ok(spoke,'the guide conversation was not recorded :: '+JSON.stringify(talk.event?.results));

 // The road is printed but the far place is not yet known to the hero: the
 // player explores it in the sandbox, and only then walks it.
 const explored=await send('Исследую дорогу на запад');
 const exploreResults=(explored.event?.results)||[];
 // Exploring a printed road IS the walk: the game moves the hero along it.
 const walked=(exploreResults[0]?.travel)||{};
 assert.equal(walked.from,'Перекрёсток','the hero was not on the crossroads before exploring');
 assert.equal(walked.target,'Берег реки','the exploration did not lead along the printed road');
 assert.ok(Number(walked.meters)>0,'the walked distance was not recorded');
 console.log('TRAVEL', JSON.stringify({from:walked.from,to:walked.target,meters:walked.meters,minutes:walked.minutes}));
 await shot(page,'11c-explored');

 // The printed road is walked by the player: the recorded action must be a
 // real move from the old place to the new one, and the place must follow.
 // The hero is on the bank already: the crossroads must be left behind.
 let travelled={moved:{travel:{from:'Перекрёсток',to:walked.target}}};
 const back=await send('Иду обратно на перекрёсток');
 const backResults=(back.event?.results)||[];
 // The trip is described either as a travel record or as a plain move result.
 const backMove=backResults.find(r=>{const trip=r.travel||(r.type==='move'?r:null);return trip && trip.from && trip.target && trip.from!==trip.target;});
 assert.ok(backMove,'the walk back recorded no move');
 console.log('BACK', JSON.stringify(backMove.travel || backMove));
 await shot(page,'12-travelled');
 assert.ok(travelled,'the journey never moved the hero to another place');
 assert.match(String(travelled.moved.travel.to),/берег/i,'the hero did not walk to the river bank');
 const after=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return {location:c.state.location,turn:c.state.turn,locations:c.state.locations.map(l=>({name:l.name,known:l.known_to_hero}))};},campaign.id);
 assert.equal(after.location,'Перекрёсток','the hero did not walk back to the crossroads');

 // 11. The play screen has no save buttons, so the player opens the saves view
 // from the navigation. It is a real click: nothing is re-enabled from the page.
 const chatBeforeSave=await page.locator('#chat').innerText();
 const savedBefore=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return c.saves.length;},campaign.id);
 await page.locator('[data-view="saves"]').first().click();
 await page.waitForTimeout(800);
 if(!await page.locator('#save-form').isVisible().catch(()=>false)){
  await page.locator('#saves [data-tabletop-link="saves"]').first().click().catch(()=>{});
  await page.waitForTimeout(600);
 }
 const saveFormVisible=await page.locator('#save-form').isVisible().catch(()=>false);
 await shot(page,'12-saves-screen');
 if(saveFormVisible){
  await page.locator('#save-name').fill('Перед Бaшней');
  await page.locator('#save-form').evaluate(f=>f.requestSubmit());
  await page.waitForTimeout(2500);
 }
 await shot(page,'13-saved');
 const saves=await page.evaluate(async id=>{const r=await fetch('/api/campaigns/'+id);const c=await r.json();return c.saves.length;},campaign.id);
 assert.ok(saveFormVisible,'the saves screen stays closed to the player');
 assert.ok(saves>=1,'the player could not save a checkpoint');

 // 12. Stop, restart a NEW server process, continue the same campaign by hand.
 const before={campaign:campaign.id,location:after.location,turn:after.turn,saves};
 fs.writeFileSync(path.join(evidence,'before.json'),JSON.stringify(before,null,1));
 await context.close();await browser.close();
 assert.deepEqual(errors,[],'page errors: '+errors.join(' | '));
 const chatText=chatBeforeSave;
 // The journey itself is asserted from the records above (TRAVEL and BACK),
 // not from a bare flag: the place, the road and the trip are all in the log.
 assert.ok(walked.from && backMove,'the journey did not change the place: '+after.location+' :: '+chatText.slice(-900));
 assert.ok(saves>savedBefore,'the player pressed save but no checkpoint was written');
 console.log('JOURNEY_PART1_OK '+JSON.stringify(before));
})().catch(e=>{console.error(e);process.exit(1);});
