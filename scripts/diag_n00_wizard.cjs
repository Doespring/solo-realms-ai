// n00: что договор мастера героя просит после «Рекомендовать» — без догадок.
// node scripts/diag_n00_wizard.cjs <base> <draft id>
const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const [base,draft]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SOLO_UI_BROWSER,headless:true});
 const context=await browser.newContext({viewport:{width:375,height:812},locale:'ru-RU'});
 const page=await context.newPage();page.setDefaultTimeout(60000);
 page.on('pageerror',e=>console.log('PAGEERROR',String(e).slice(0,300)));
 page.on('response',r=>{if(r.status()>=400)console.log('HTTP',r.status(),r.request().method(),new URL(r.url()).pathname);});
 await page.goto(base,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>typeof window.soloOpenCreation==='function');
 await page.evaluate(id=>window.soloOpenCreation(id),draft);
 await page.waitForFunction(()=>{const p=document.getElementById('creation-world');return p&&/Глава собрана/.test(p.textContent);});
 await page.locator('#creation-world').getByRole('button',{name:'Начать с этой главой',exact:true}).click();
 await page.locator('#world-confirm').waitFor({state:'visible'});
 await page.locator('#world-confirm').getByRole('button',{name:'Подтвердить этот мир',exact:true}).click();
 await page.waitForFunction(()=>{const b=[...document.querySelectorAll('#creation-world .button-row button')].find(n=>n.textContent.trim()==='Выбрать героя Nat20');return b&&!b.hidden;});
 await page.locator('#creation-world').getByRole('button',{name:'Выбрать героя Nat20',exact:true}).click();
 await page.locator('#wizard').waitFor({state:'visible'});
 const snap=async label=>{
   const value=await page.evaluate(()=>{const s=window.soloWizard.state();const c=s.contract;return c?{ready:c.ready,confirm_ready:c.confirm_ready,blocking:c.blocking,pending:(c.pending||[]).map(p=>p.id+':'+p.missing),issues:(c.issues||[]).slice(0,4),confirm_issues:(c.confirm_issues||[]).slice(0,4),name:c.name,steps:(c.steps||[]).map(s=>s.id)}:null;});
   console.log('==',label,JSON.stringify(value));
   return value;
 };
 try{await page.waitForFunction(()=>window.soloWizard.state().contract,{timeout:60000});}catch(e){console.log('NO CONTRACT',String(e).slice(0,120));}
 await snap('open');
 const recommend=page.locator('#wizard').getByRole('button',{name:'Рекомендовать',exact:true});
 if(await recommend.isVisible()){await recommend.click();console.log('clicked recommend');}
 for(let i=0;i<8;i++){await page.waitForTimeout(2000);await snap('after-recommend-'+i);}
 await page.screenshot({path:'reports/n00/diag-wizard-375.png'});
 await browser.close();
})();
