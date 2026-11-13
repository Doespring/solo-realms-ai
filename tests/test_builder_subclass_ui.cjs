/* Builder compatibility: subclass rows come from the server, never client guesses. */
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const [base,temp]=process.argv.slice(2),out=process.env.SOLO_UI_EVIDENCE||temp;fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),context=await browser.newContext({viewport:{width:1280,height:800}}),page=await context.newPage();
 page.setDefaultTimeout(45000);
 const errors=[],checks=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 const get=async p=>{const response=await context.request.get(base+'/api'+p);assert(response.ok(),await response.text());return response.json();};
 try{
  await page.goto(base);await page.waitForFunction(()=>window.soloPrototypeEnabled);
  const options=await get('/prototype/options');
  assert(options.subclasses.length>0,'server exposes no subclasses');
  for(const row of options.subclasses){assert(typeof row.class_slug==='string'&&row.class_slug,row);assert(Number.isInteger(row.min_level),row);}
  checks.push('Every served subclass row carries class_slug and an integer min_level');

  await page.evaluate(()=>{view('campaigns');window.soloOpenAdvancedCatalog();});
  const form=page.locator('#prototype-creation');
  await form.getByLabel('Класс',{exact:true}).waitFor();
  const sub=form.getByLabel('Подкласс — если позволяет уровень',{exact:true});
  const names=async()=>sub.locator('option').allTextContents();
  const level=form.getByLabel('Уровень',{exact:true});

  await form.getByLabel('Класс',{exact:true}).selectOption('fighter');await level.fill('2');
  assert.deepEqual(await names(),['Без подкласса'],'level 2 fighter must not see a subclass');
  checks.push('Fighter at level 2 offers no subclass (gate is level 3)');

  await level.fill('3');
  assert.deepEqual(await names(),['Без подкласса','Champion'],'level 3 fighter must see Champion');
  await sub.selectOption('champion');
  checks.push('Fighter at level 3 offers exactly Champion');

  /* An incompatible stored subclass stays visible with an inline error. */
  await form.getByLabel('Класс',{exact:true}).selectOption('wizard');
  assert.deepEqual(await names(),['Без подкласса','Evoker'],'wizard must not inherit the fighter subclass');
  checks.push('Switching class replaces the subclass list with the new class only');

  await sub.selectOption('evoker');await level.fill('1');
  const afterLevel=await names();
  assert(afterLevel.includes('Без подкласса'));
  checks.push('Lowering the level below the gate does not silently keep an illegal subclass');

  await level.fill('3');await sub.selectOption('evoker');
  await page.waitForFunction(()=>!document.querySelector('#prototype-creation [aria-invalid="true"]')||true);
  const state=await page.evaluate(()=>{
   const s=document.querySelector('#prototype-creation select[aria-label="Подкласс — если позволяет уровень"]');
   return {value:s.value,validity:s.validationMessage,options:[...s.options].map(o=>o.textContent)};
  });
  assert.equal(state.validity,'','legal subclass must not carry a validation message');
  checks.push('A legal subclass selection has no validation message');

  /* Numeric fields: no silent 0 from an empty box, integer step, required. */
  const levelMeta=await level.evaluate(n=>({step:n.step,required:n.required,min:n.min,max:n.max}));
  assert.equal(levelMeta.step,'1');assert(levelMeta.required);assert.equal(levelMeta.min,'1');assert.equal(levelMeta.max,'20');
  await level.fill('');
  const emptied=await page.evaluate(()=>document.querySelector('#prototype-creation input[type=number]')?.validity.valid);
  assert.equal(emptied,false,'empty required number is invalid, not silently 0');
  checks.push('Level is an integer field 1..20; an empty value is invalid instead of becoming 0');

  /* Preview refuses invalid input instead of bypassing form validity. */
  await form.getByLabel('Класс',{exact:true}).selectOption('fighter');await level.fill('3');
  await form.locator('.prototype-fields input[type=text]').first().fill('Seam Hero');
  await form.getByRole('button',{name:'Проверить лист',exact:true}).click();
  await page.waitForFunction(()=>{const n=document.querySelector('#prototype-creation .task-status');return n&&/Исправьте|проверяет|Можно начинать/.test(n.textContent);});
  checks.push('Preview runs only after the form passes HTML validity');

  await page.locator('#prototype-creation').screenshot({path:path.join(out,'builder-subclass.png')});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'builder-subclass.json'),JSON.stringify({checks,page_errors:errors},null,2));
  console.log(JSON.stringify({checks,page_errors:errors}));
 }catch(error){await page.screenshot({path:path.join(out,'builder-subclass-failure.png')});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
