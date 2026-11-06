/* Documents stay inside the application. Text is never interpreted as HTML. */
(()=>{
 'use strict';
 const docs={'/static/preparation-guide.md':'preparation-guide','/static/visual/ATTRIBUTION.md':'asset-attribution','/static/player-help.txt':'player-help'};
 const dialog=el('dialog',undefined,'document-viewer'),title=el('h2'),text=el('pre',undefined,'document-text'),close=el('button','Закрыть','primary'),toolbar=el('div',undefined,'document-toolbar');dialog.id='document-viewer';title.id='document-title';dialog.setAttribute('aria-labelledby',title.id);close.type='button';toolbar.append(title,close);dialog.append(toolbar,text);document.body.append(dialog);close.onclick=()=>dialog.close();
 let previousFocus=null,stamp=0;dialog.addEventListener('close',()=>{stamp++;previousFocus?.focus?.();});
 async function openDocument(id){previousFocus=document.activeElement;const serial=++stamp;title.textContent='Документ';text.textContent='Открываем…';if(!dialog.open)dialog.showModal();try{const value=await api('/help/'+encodeURIComponent(id));if(serial!==stamp)return;title.textContent=value.title;text.textContent=value.text;}catch(error){if(serial===stamp)text.textContent=error.message+' Закройте окно или повторите открытие.';}}
 const external=el('dialog'),externalAddress=el('p',undefined,'prose'),externalClose=el('button','Остаться в игре'),externalOpen=el('button','Открыть в браузере','primary');external.id='external-link-dialog';external.append(el('h2','Открыть внешний сайт?'),externalAddress,externalClose,externalOpen);document.body.append(external);let pendingURL=null;
 externalClose.onclick=()=>external.close();externalOpen.onclick=protect(async()=>{if(!pendingURL)return;const url=pendingURL;externalOpen.disabled=true;try{await api('/navigation/external',{url,confirm:true});external.close();}finally{externalOpen.disabled=false;}});
 function receive(value){
  if(value.kind==='document')return protect(()=>openDocument(value.value))();
  if(value.kind==='app'){if(value.value)view(value.value);return;}
  if(value.kind==='external'){pendingURL=value.value;externalAddress.textContent=value.value;if(!external.open)external.showModal();return;}
  toast('Этот переход не разрешён. Вы остались в приложении.');
 }
 window.soloNavigation={receive,openDocument};
 document.addEventListener('click',event=>{
  const a=event.target.closest?.('a[href]');if(!a||event.defaultPrevented)return;
  const url=new URL(a.href,location.origin),id=docs[url.pathname]||(url.pathname.startsWith('/help/')?url.pathname.slice(6):null);
  if(url.origin===location.origin&&id){event.preventDefault();openDocument(id);return;}
  if(a.download&&(url.origin===location.origin||a.href.startsWith('blob:'+location.origin+'/')))return;
  if(url.origin===location.origin&&url.pathname==='/'&&!url.search){event.preventDefault();view(url.hash?.slice(1)||'welcome');return;}
  event.preventDefault();protect(async()=>{const decision=await api('/navigation/classify',{url:a.href});receive(decision);})();
 },true);
 const dev=document.getElementById('tabletop-advanced');if(dev){const b=el('button','Шаблон сценария и правила закрытых сведений','nav');b.type='button';b.id='developer-preparation-guide';b.onclick=()=>openDocument('preparation-guide');dev.append(b);}
 // Restore an allowed document opened outside the SPA, with an explicit mouse exit.
 const initial=docs[location.pathname]||(location.pathname.startsWith('/help/')?location.pathname.slice(6):null);
 if(initial){history.replaceState(null,'','/');protect(()=>openDocument(initial))();}
})();
