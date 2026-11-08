const {chromium}=require(process.env.SOLO_UI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const [base]=process.argv.slice(2);
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 const source=fs.readFileSync(path.join('web','play.js'),'utf8');
 assert.ok(source.includes('function mergeOlder('),'mergeOlder is missing from web/play.js');
 await page.goto(base,{waitUntil:'domcontentloaded'});
 const ids=await page.evaluate(async src=>{
   const mergeStart=src.indexOf('function mergeOlder(');
   const mergeEnd=src.indexOf('function message(',mergeStart);
   const refreshStart=src.indexOf('async function refresh(');
   const refreshEnd=src.indexOf('\n',refreshStart);
   if(mergeStart<0||refreshStart<0)throw new Error('play.js no longer defines mergeOlder/refresh');
   const mergeDecl=src.slice(mergeStart,mergeEnd).trim();
   const refreshDecl=src.slice(refreshStart,refreshEnd).trim();
   const newest={conversation:[{id:'c',created:3},{id:'d',created:4},{id:'e',created:5}],
                  conversation_cursor:[3,'c'],conversation_more:true};
   const scope={campaignId:'camp',api:async()=>newest,render:()=>{},
                current:{conversation:[{id:'a',created:1},{id:'b',created:2},{id:'c',created:3},{id:'d',created:4}],
                         conversation_cursor:[1,'a'],conversation_more:true}};
   const load=new Function('scope',
     'let campaignId=scope.campaignId, current=scope.current;'
     + 'const api=scope.api, render=scope.render;'
     + mergeDecl
     + ';' + refreshDecl.replace('async function refresh()','scope.refresh=async function refresh()')
     + ';scope.get=()=>current;return scope;');
   load(scope);
   await scope.refresh();
   return scope.get().conversation.map(r=>r.id);
 },source);
 assert.deepEqual(ids,['a','b','c','d','e'],'a new turn dropped the pages already on screen: '+JSON.stringify(ids));
 assert.deepEqual(errors,[]);
 await browser.close();console.log('PAGING_MERGE_OK');
})().catch(e=>{console.error(e);process.exit(1);});