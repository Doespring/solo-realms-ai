'use strict';
(()=>{
 // This panel exists only when an actual native host responds.
 async function initialize(){
  const response=await fetch('/api/desktop/status');if(!response.ok)return;const host=await response.json();if(host.available===false)return;
  window.soloDesktop=true;
  const panel=el('details',undefined,'disclosure shell-preferences');panel.append(el('summary','Окно приложения и резервная копия'));const controls=el('div',undefined,'disclosure-body'),resolution=el('select'),fullscreen=el('input'),apply=el('button','Применить размер окна'),save=el('button','Создать резервную копию данных'),result=el('p',undefined,'hint');fullscreen.type='checkbox';fullscreen.checked=host.preferences.fullscreen;
  const sizes=[[1024,720],[1280,800],[1366,768],[1600,900],[1920,1080],[host.preferences.width,host.preferences.height]];for(const pair of sizes.filter((p,i,list)=>list.findIndex(q=>p[0]===q[0]&&p[1]===q[1])===i)){const o=el('option',pair.join(' × '));o.value=pair.join(',');resolution.append(o);}resolution.value=[host.preferences.width,host.preferences.height].join(',');
  controls.append(field('Размер окна в логических пикселях',resolution),field('Полноэкранный режим',fullscreen),apply,save,el('p','Данные: '+host.data_dir,'hint'),el('p',host.updates,'hint'),result);panel.append(controls);document.querySelector('header').after(panel);
  apply.onclick=protect(async()=>{const[width,height]=resolution.value.split(',').map(Number);const value=await api('/desktop/preferences',{...host.preferences,width,height,fullscreen:fullscreen.checked});host.preferences=value.preferences;result.textContent='Настройки окна сохранены.';});
  save.onclick=protect(async()=>{if(busy)throw new Error('Дождитесь завершения ответа.');const value=await api('/desktop/backup',{});result.textContent='Копия сохранена: '+value.path;});
  document.addEventListener('keydown',protect(async e=>{if(e.key!=='F11')return;e.preventDefault();fullscreen.checked=!fullscreen.checked;apply.click();}));
 }
 initialize().catch(()=>toast('Не удалось получить настройки окна.'));
})();
