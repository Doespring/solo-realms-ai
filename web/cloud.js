/* Local UI talks only to the local backend. API keys are one-way transient inputs. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id);
 const main=document.querySelector('main');main.insertBefore($('cloud-panel-template').content.cloneNode(true),main.querySelector('footer'));
 const advanced= document.createElement('details');advanced.id='api-advanced';
 const advancedTitle=document.createElement('summary');advancedTitle.textContent='Advanced / Developer';advanced.append(advancedTitle);
 $('api-form').before(advanced);advanced.append($('api-form'));
 let profiles=[],editing=null,catalog=[];
 const field=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const quick=field('form');quick.id='api-simple';quick.className='settings-card';
 const provider=field('select'),key=field('input'),connect=field('button','Проверить подключение'),ready=field('p'),start=field('button','Разрешить расходы и начать');
 provider.id='api-simple-provider';key.id='api-simple-key';key.type='password';key.autocomplete='new-password';key.maxLength=4096;key.placeholder='Оставьте пустым, если ключ уже сохранён';
 for(const name of ['RouterAI','Groq']){const option=field('option',name);option.value=name;provider.append(option);}
 for(const[title,input]of[['Провайдер',provider],['API-ключ',key]]){const label=field('label',title);label.append(input);quick.append(label);}
 const free=field('input');free.type='checkbox';const freeLabel=field('label','Подтверждаю бесплатный тариф Groq и отсутствие платного биллинга в аккаунте');freeLabel.append(free);freeLabel.hidden=true;quick.append(freeLabel);
 connect.type='submit';connect.disabled=true;start.type='button';start.disabled=true;ready.setAttribute('role','status');ready.id='api-simple-status';
 quick.append(connect,ready,start,field('p','Проверка получает каталог без генерации. Перед расходами вы отдельно подтверждаете общий лимит.','hint'));advanced.before(quick);
 let setupReady=false,simpleCheckRequested=false,setupBusy=false,retrySetup=null;
 function quickProfile(){connect.disabled=false;key.disabled=false;setupReady=false;start.disabled=true;const known=['RouterAI','Groq'].includes(editing?.provider);provider.disabled=!!editing&&!known;if(known)provider.value=editing.provider;freeLabel.hidden=provider.value!=='Groq';free.checked=editing?.free_account_confirmed??false;key.value='';ready.textContent=editing?.enabled?'Профиль включён. Повторная проверка не запускает генерацию.':editing?.key_present?'Ключ сохранён. Проверьте подключение, чтобы автоматически подобрать настройки.':'Выберите провайдера и введите ключ.';}
 provider.onchange=()=>{setupReady=false;start.disabled=true;freeLabel.hidden=provider.value!=='Groq';};
 key.oninput=()=>{setupReady=false;start.disabled=true;};free.onchange=()=>{setupReady=false;start.disabled=true;};
 async function simpleSetup(){
  if(setupBusy)return;setupBusy=true;
  const secret=key.value,selectedProvider=provider.value,freeConfirmed=free.checked,providerDisabled=provider.disabled;
  connect.disabled=start.disabled=key.disabled=provider.disabled=free.disabled=true;ready.textContent='Проверяем подключение и совместимость…';
  try{
   const result=await api('/ai/setup',{provider:selectedProvider,profile:retrySetup?.provider===selectedProvider?retrySetup.id:editing?.id||null,api_key:secret||null,free_account_confirmed:freeConfirmed});
   retrySetup={id:result.profile,provider:selectedProvider};
   await load();$('api-profile-list').value=result.profile;renderProfile(profiles.find(p=>p.id===result.profile));
   setupReady=result.ready;start.disabled=!setupReady;
   ready.replaceChildren(field('span',result.message));
   if(result.ready)ready.append(field('p','Настройки подобраны автоматически. Подтвердите лимит перед игрой.'));
   for(const issue of result.issues)ready.append(field('p',issue));
   for(const row of result.checks)ready.append(field('p',({interpreter:'Разбор действий',narrator:'Мастер',secondary_narrator:'Альтернативный Мастер',pdf_analyst:'Помощник подготовки'})[row.role]+' — '+row.model+' · до '+row.max_tokens+' токенов ответа'));
   key.value='';return result.ready;
  }catch(e){setupReady=false;start.disabled=true;provider.value=selectedProvider;free.checked=freeConfirmed;key.value=secret;freeLabel.hidden=selectedProvider!=='Groq';ready.textContent=e.message;throw e;}
  finally{setupBusy=false;connect.disabled=key.disabled=free.disabled=false;provider.disabled=providerDisabled;}
 }
 quick.onsubmit=async e=>{e.preventDefault();try{await simpleSetup();}catch(e){toast(e.message);}};
 start.onclick=async()=>{if(setupReady){simpleCheckRequested=true;enableAI.click();}};
 const safe=fn=>async(...args)=>{try{await fn(...args);}catch(e){$('api-feedback').textContent=e.message;ready.textContent=e.message;toast(e.message);}};
 const enableAI=field('button','Включить облачный AI'),diagnostics=field('div'),authorization=field('dialog');enableAI.type='button';enableAI.id='api-enable-cloud';diagnostics.id='api-configuration';diagnostics.setAttribute('role','status');
 $('api-key-state').after(enableAI,diagnostics);authorization.id='api-enable-confirm';
 const grantAmount=field('input'),grantRequest=field('input'),grantConsent=field('input'),grantSummary=field('p'),grantConfirm=field('button','Подтвердить лимит и включить'),grantCancel=field('button','Вернуться'),grantRoutes=field('div');let routeChoices=[],routesLoading=false;
 grantAmount.type=grantRequest.type='number';grantAmount.min=grantRequest.min='.01';grantAmount.step=grantRequest.step='.01';grantAmount.max=grantRequest.max='1000000';grantConsent.type='checkbox';grantConfirm.type=grantCancel.type='button';
 for(const [control,label]of [[grantAmount,'Новый общий остаток расходов'],[grantRequest,'Не более за один запрос'],[grantConsent,'Подтверждаю этот разовый лимит и включение выбранного профиля']]){const row=field('label',label);row.append(control);authorization.append(row);}
 authorization.prepend(field('h2','Включить облачный AI'),grantRoutes);authorization.append(grantSummary,grantCancel,grantConfirm);document.body.append(authorization);grantCancel.onclick=()=>{simpleCheckRequested=false;authorization.close();};
 function reviewGrant(){const amount=Number(grantAmount.value);if(!advanced.open)grantRequest.value=String(Math.min(amount||0,editing?.currency==='RUB'?5:1));const request=Number(grantRequest.value);grantRequest.closest('label').hidden=!advanced.open;grantConsent.closest('label').firstChild.textContent=simpleCheckRequested?'Подтверждаю общий лимит и один короткий тест подключения по тарифу API':'Подтверждаю этот разовый лимит и включение выбранного профиля';grantSummary.textContent=(editing?.name||'Профиль')+' · '+(editing?.currency||'RUB')+'. Не более '+request+' за запрос. Общий остаток действует без автоматического сброса при смене даты или перезапуске. '+(simpleCheckRequested?'После подтверждения выполняется ровно один короткий тест. Дальнейшие игровые запросы учитываются в этом же лимите.':'Включение само по себе не вызывает модель.')+' Один сохранённый ключ используется всеми ролями.';grantConfirm.disabled=routesLoading||routeChoices.some(s=>!s.value)||!grantConsent.checked||!(amount>0&&request>0&&request<=amount);}
 async function chooseRoutes(){grantRoutes.replaceChildren();routeChoices=[];if(editing.provider.toLowerCase()!=='routerai'||!['interpreter','narrator','secondary_narrator','pdf_analyst'].some(r=>editing[r]&&(!editing[r].endpoint||editing[r].input_per_million===null||editing[r].output_per_million===null)))return;routesLoading=true;reviewGrant();grantRoutes.append(field('p','Выберите маршруты для сохранённых моделей. Каталог запрашивается без генерации; другой маршрут без вашего выбора не используется.'));
  try{for(const role of ['interpreter','narrator','secondary_narrator','pdf_analyst']){if(!editing[role]?.model)continue;const data=await api('/ai/profiles/'+editing.id+'/endpoints/'+role),select=field('select'),label=field('label',editing[role].model);select.append(field('option','Выберите маршрут'));for(const row of data.endpoints){const option=field('option',row.name||row.tag);option.value=row.tag;if(row.pricing_units?.prompt!=='token'||row.pricing_units?.completion!=='token')option.disabled=true;select.append(option);}routeChoices.push(select);label.append(select);grantRoutes.append(label);select.onchange=()=>{const row=data.endpoints.find(r=>r.tag===select.value);if(!row){reviewGrant();return;}const prices=[row.pricing,...(row.variable_pricings||[])];$('api-'+role+'-endpoint').value=row.tag;for(const[side,key]of [['input','prompt'],['output','completion']])$('api-'+role+'-'+side+'-price').value=Math.max(...prices.map(p=>Number(p[key])))*1000000;if(row.supported_parameters){$('api-'+role+'-params').value=row.supported_parameters.join(',');const bound=$('api-'+role+'-bound');if(!row.supported_parameters.includes(bound.value))bound.value=row.supported_parameters.includes('max_tokens')?'max_tokens':'max_completion_tokens';if(!row.supported_parameters.includes('response_format'))$('api-'+role+'-json').value='none';}if(role==='pdf_analyst'&&editing[role].model?.startsWith('google/gemini-3')&&row.supported_parameters?.includes('reasoning')&&$('api-'+role+'-thinking').value==='low')$('api-'+role+'-control').value='routerai_enabled';editing[role].price_checked_at=data.checked_at;editing[role].image_per_million=row.pricing_units?.image==='token'?Math.max(...[row.pricing,...(row.variable_pricings||[])].map(p=>Number(p.image??row.pricing.image)))*1000000:null;reviewGrant();};if(data.endpoints.some(r=>r.tag===editing[role].endpoint)){select.value=editing[role].endpoint;select.onchange();}}}finally{routesLoading=false;reviewGrant();}}
 grantAmount.oninput=grantRequest.oninput=grantConsent.onchange=reviewGrant;
 enableAI.onclick=safe(async()=>{await save();grantAmount.value=grantRequest.value='';grantConsent.checked=false;reviewGrant();authorization.showModal();await chooseRoutes();});
 grantConfirm.onclick=safe(async()=>{grantConfirm.disabled=true;const verifyConnection=simpleCheckRequested;simpleCheckRequested=false;try{await save();const id=editing.id;await api('/ai/profiles/'+id+'/enable',{confirm:grantConsent.checked,amount:grantAmount.value,max_request:grantRequest.value,currency:editing.currency});authorization.close();await load();if(verifyConnection){ready.textContent='Проверяем доступ к модели — один подтверждённый запрос…';const checked=await api('/ai/profiles/'+id+'/probe',{});ready.textContent='Подключение проверено: '+checked.text+' · '+checked.metric.cost+' '+checked.metric.currency+'. Можно перейти к приключению.';}toast('Облачный AI включён с подтверждённым разовым лимитом.');}finally{reviewGrant();}});
 async function showStatus(id){const value=await api('/ai/profiles/'+id+'/status');if(editing?.id!==id)return;diagnostics.replaceChildren();diagnostics.append(field('p',value.enabled?'Профиль включён':'Профиль выключен'));for(const issue of value.issues)diagnostics.append(field('p',issue.message));for(const role of Object.values(value.roles))diagnostics.append(field('p',role.label+': '+(role.model||'не выбрана')+' · '+(role.problems.join('; ')||'настройки заполнены')+' · один ключ профиля'));if(value.total_budget!==null)diagnostics.append(field('p','Остаток подтверждённого общего лимита: '+value.remaining_budget+' '+value.currency+'. Новый остаток требует подтверждения.'));}
 const roles=['interpreter','narrator','secondary_narrator','pdf_analyst'];
 // The role key is Latin; a player needs the Russian name next to a button
 // that would otherwise appear four times with identical text.
 const ROLE_NAMES={interpreter:'Обработка команд',narrator:'Мастер',secondary_narrator:'Второй рассказчик',pdf_analyst:'Разбор PDF'};
 const roleOptions=field('details');roleOptions.append(field('summary','Дополнительные роли и бюджеты'));
 for(const[id,title,options]of[['api-narrator-choice','Рассказчик',['primary','secondary']],['api-intent-contract','Контракт разбора',['legacy','qwen_interpreter_v1']],['api-preparation-role','Кто читает книгу при подготовке',['analyst','interpreter']]]){const select=field('select');select.id=id;for(const value of options){const o=field('option',({primary:'Основной — DeepSeek',secondary:'Дополнительный — Venice',legacy:'Существующий',qwen_interpreter_v1:'Qwen — проверяемые намерения',analyst:'PDF-аналитик (Gemini), если настроен',interpreter:'Толкователь намерений (Qwen)'})[value]);o.value=value;select.append(o);}const label=field('label',title);label.append(select);roleOptions.append(label);}
 for(const[id,title]of[['api-month-budget','Лимит месяца'],['api-preparation-budget','Лимит подготовки одного приключения']]){const input=field('input');input.id=id;input.type='number';input.min=0;input.step='any';const label=field('label',title);label.append(input);roleOptions.append(label);}
 const analystEnabled=field('input');analystEnabled.type='checkbox';analystEnabled.id='api-analyst-enabled';const analystLabel=field('label','Разрешить отдельный разбор проблемных страниц Gemini');analystLabel.append(analystEnabled);roleOptions.append(analystLabel,field('p','Рассказчик выбирается вручную и не переключается после отказа. Gemini вызывается только из подготовки с отдельным согласием на отправку страниц.'));
 $('api-model-fields').append(roleOptions);
 for(const [role,label] of [['interpreter','Разбор действий — Qwen'],['narrator','Основной рассказчик — DeepSeek'],['secondary_narrator','Дополнительный рассказчик — Venice'],['pdf_analyst','Помощник подготовки — Gemini']]){
  const box=field('fieldset');box.append(field('legend',label));
  function input(name,title,type='text',value=''){
   const l=field('label',title),n=field('input');n.id='api-'+role+'-'+name;n.type=type;n.value=value;
   if(name==='model'){n.setAttribute('list','api-models');n.maxLength=160;}
   if(type==='number'){n.min='0';n.step=name==='temperature'?'0.05':'any';}
   l.append(n);box.append(l);
  }
  input('model','ID модели');input('tokens','Максимум токенов','number',role==='interpreter'?'1200':'600');
  input('temperature','Температура по умолчанию','number',role==='interpreter'?'0.05':'0.65');
  input('input-price','Цена входа за миллион токенов','number');input('output-price','Цена выхода за миллион токенов','number');
  const details=field('details');details.append(field('summary','Совместимость модели'));
  const l=field('label','Поддерживаемые параметры'),n=field('input');n.id='api-'+role+'-params';n.value='max_tokens,temperature';l.append(n);details.append(l);
  for(const [name,title,values] of [['bound','Параметр ограничения ответа',['max_tokens','max_completion_tokens']],['json','JSON в служебных операциях',['none','json_object','json_schema']]]){
   const l=field('label',title),s=field('select');s.id='api-'+role+'-'+name;
   for(const v of values){const o=field('option',v==='none'?'Только локальная валидация':v);o.value=v;s.append(o);}
   l.append(s);details.append(l);
  }
  box.append(details);if(roles.indexOf(role)>1)roleOptions.append(box);else $('api-model-fields').append(box);
  const reason=field('select'),reasonLabel=field('label','Thinking / reasoning (по возможностям модели)');reason.id='api-'+role+'-reasoning';for(const[v,text]of[['','Не отправлять параметр'],['none','Выключено — только поддерживаемая модель'],['default','По умолчанию'],['low','Малое усилие'],['medium','Среднее усилие'],['high','Высокое усилие']]){const o=field('option',text);o.value=v;reason.append(o);}reasonLabel.append(reason);details.append(reasonLabel);
  for(const[name,title,values]of[['thinking','Запрошенный режим thinking',['provider_default','off','low','medium']],['control','Подтверждённый способ управления',['unverified','routerai_enabled','effort']]]){const l=field('label',title),s=field('select');s.id='api-'+role+'-'+name;for(const v of values){const o=field('option',({provider_default:'По умолчанию провайдера',off:'Выключить при поддержке',low:'Низкий',medium:'Средний',unverified:'Не проверен — параметр не отправляется',routerai_enabled:'Проверен reasoning.enabled',effort:'Проверен reasoning_effort'})[v]);o.value=v;s.append(o);}l.append(s);details.append(l);}
  const endpoint=field('input');endpoint.id='api-'+role+'-endpoint';endpoint.maxLength=160;const endpointLabel=field('label','Проверенный tag конечного провайдера RouterAI');endpointLabel.append(endpoint);details.append(endpointLabel);
  const endpoints=field('button','Маршруты: '+(ROLE_NAMES[role]||role));endpoints.type='button';const routes=field('select');routes.id='api-'+role+'-routes';routes.append(field('option','Выберите проверенный маршрут'));details.append(endpoints,routes);
  endpoints.onclick=safe(async()=>{if(!editing)throw new Error('Сначала сохраните профиль');const data=await api('/ai/profiles/'+editing.id+'/endpoints/'+role);routes.replaceChildren(field('option','Выберите маршрут'));for(const row of data.endpoints){const option=field('option',row.name||row.tag);option.value=row.tag;routes.append(option);}routes.onchange=()=>{const row=data.endpoints.find(r=>r.tag===routes.value);if(!row)return;endpoint.value=row.tag;if(row.supported_parameters)$('api-'+role+'-params').value=row.supported_parameters.join(',');if(!row.variable_pricings?.length&&row.pricing_units?.prompt==='token'&&row.pricing_units?.completion==='token'){for(const[side,key]of[['input','prompt'],['output','completion']])$('api-'+role+'-'+side+'-price').value=Number(row.pricing[key])*1000000;}editing[role]=editing[role]||{};if(role==='pdf_analyst'&&editing[role].model?.startsWith('google/gemini-3')&&row.supported_parameters?.includes('reasoning')&&$('api-'+role+'-thinking').value==='low')$('api-'+role+'-control').value='routerai_enabled';editing[role].price_checked_at=data.checked_at;editing[role].image_per_million=row.pricing_units?.image==='token'?Math.max(...[row.pricing,...(row.variable_pricings||[])].map(p=>Number(p.image??row.pricing.image)))*1000000:null;$('api-'+role+'-control').value=role==='pdf_analyst'&&editing[role].model?.startsWith('google/gemini-3')&&row.supported_parameters?.includes('reasoning')&&$('api-'+role+'-thinking').value==='low'?'routerai_enabled':'unverified';};$('api-feedback').textContent='Маршруты получены. Выберите маршрут, сверьте ограничения и сохраните. Каталог не доказывает поддержку выключенного thinking.';});
  $('api-'+role+'-model').addEventListener('change',()=>applyCatalog(role));
 }
 $('api-model-fields').append(roleOptions);
 const val=id=>$(id).value;
 function renderProfile(p=null){
  editing=p;retrySetup=null;catalog=[];$('api-models').replaceChildren();
  $('api-narrator-choice').value=p?.narrator_selection||'primary';$('api-intent-contract').value=p?.interpreter_contract||'legacy';$('api-preparation-role').value=p?.preparation_role||'analyst';$('api-month-budget').value=p?.monthly_budget??'';$('api-preparation-budget').value=p?.preparation_budget??'';$('api-analyst-enabled').checked=p?.pdf_analysis_enabled??false;
  for(const [id,value] of Object.entries({'api-name':p?.name||'Облачный Мастер','api-provider':p?.provider||'OpenAI-compatible','api-url':p?.base_url||'',
   'api-currency':p?.currency||'RUB','api-context':p?.context_tokens||16384,'api-timeout':p?.timeout_seconds||90,
   'api-request-budget':p?.max_request_cost??0,'api-session-budget':p?.session_budget??0,'api-day-budget':p?.daily_budget??0}))$(id).value=value;
  $('api-key').value='';$('api-key-state').textContent=p?.key_present?'Ключ сохранён в защищённом хранилище Windows. Его значение не возвращается в браузер.':'Ключ ещё не сохранён.';
  $('api-enabled').disabled=!p?.enabled;enableAI.textContent=p?.enabled?'Подтвердить новый остаток расходов':'Включить облачный AI';diagnostics.replaceChildren();if(p)showStatus(p.id).catch(e=>diagnostics.textContent=e.message);
  $('api-stream').checked=p?.streaming??true;$('api-stream-usage').checked=p?.stream_usage??false;$('api-enabled').checked=p?.enabled??false;
  $('api-usage-currency').value=p?.usage_cost_currency||'';
  $('api-free-only').checked=p?.free_only??false;$('api-free-confirmed').checked=p?.free_account_confirmed??false;$('api-retry-429').value=p?.retry_429??0;for(const[id,key,defaultValue]of[['rpm','requests_per_minute',5],['rpd','requests_per_day',200],['tpm','tokens_per_minute',8000]])$('api-'+id).value=p?.[key]??defaultValue;
  $('api-catalog').disabled=!p?.key_present;
  $('api-catalog').title=p?.key_present?'':'Сначала сохраните профиль и ключ';
  $('api-probe').disabled=!(p?.enabled&&p?.key_present&&p?.narrator?.model&&p?.narrator?.input_per_million!==null&&p?.narrator?.output_per_million!==null);
  for(const role of roles){
   const m=p?.[role]||{};
   $('api-'+role+'-thinking').value=m.thinking||'provider_default';$('api-'+role+'-control').value=m.reasoning_control||'unverified';$('api-'+role+'-endpoint').value=m.endpoint||'';
   $('api-'+role+'-reasoning').value=m.reasoning_effort||'';
   for(const [key,value] of Object.entries({model:m.model||'',tokens:m.max_tokens||(role==='interpreter'?1200:600),temperature:m.temperature??(role==='interpreter'?.05:.65),
    'input-price':m.input_per_million??'','output-price':m.output_per_million??'',params:(m.parameters||['max_tokens','temperature']).join(','),bound:m.max_token_parameter||'max_tokens',json:m.structured||'none'}))$('api-'+role+'-'+key).value=value;
  }
  $('api-feedback').textContent=p?'Профиль загружен. Изменения применяются после сохранения.':'Создайте подключение.';quickProfile();
 }
 async function load(){
  connect.disabled=true;key.disabled=true;start.disabled=true;
  try{
   const d=await api('/ai/profiles');profiles=d.profiles;
   $('api-profile-list').replaceChildren();
   for(const p of profiles){const o=field('option',p.name+(p.id===d.active?' · используется':''));o.value=p.id;$('api-profile-list').append(o);}
   if(d.active)$('api-profile-list').value=d.active;
   renderProfile(profiles.find(p=>p.id===$('api-profile-list').value)||null);
  }finally{connect.disabled=key.disabled=setupBusy;start.disabled=setupBusy||!setupReady;}
 }
 function model(role){
  const get=name=>val('api-'+role+'-'+name);
  if(roles.indexOf(role)>1&&!get('model').trim())return null;
  return {...(editing?.[role]||{}),thinking:get('thinking'),reasoning_control:get('control'),endpoint:get('endpoint').trim(),model:get('model').trim(),max_tokens:Number(get('tokens')),temperature:Number(get('temperature')),top_p:null,
   input_per_million:get('input-price')===''?null:get('input-price'),output_per_million:get('output-price')===''?null:get('output-price'),
   parameters:get('params').split(',').map(x=>x.trim()).filter(Boolean),max_token_parameter:get('bound'),structured:get('json'),reasoning_effort:get('reasoning')||null};
 }
 async function save(){
  const {key_present,...previous}=editing||{};
  const p={...previous,id:editing?.id||crypto.randomUUID(),name:val('api-name').trim(),provider:val('api-provider').trim(),base_url:val('api-url').trim(),
   secondary_narrator:model('secondary_narrator'),pdf_analyst:model('pdf_analyst'),narrator_selection:val('api-narrator-choice'),interpreter_contract:val('api-intent-contract'),preparation_role:val('api-preparation-role'),pdf_analysis_enabled:$('api-analyst-enabled').checked,monthly_budget:val('api-month-budget')===''?null:val('api-month-budget'),preparation_budget:val('api-preparation-budget')===''?null:val('api-preparation-budget'),
   interpreter:model('interpreter'),narrator:model('narrator'),enabled:$('api-enabled').checked,streaming:$('api-stream').checked,stream_usage:$('api-stream-usage').checked,
   timeout_seconds:Number(val('api-timeout')),context_tokens:Number(val('api-context')),currency:val('api-currency').toUpperCase(),
   max_request_cost:val('api-request-budget'),session_budget:val('api-session-budget'),daily_budget:val('api-day-budget'),day_utc_offset:4,
   usage_cost_currency:val('api-usage-currency')||null,free_only:$('api-free-only').checked,free_account_confirmed:$('api-free-confirmed').checked,retry_429:Number(val('api-retry-429')),requests_per_minute:Number(val('api-rpm')),requests_per_day:Number(val('api-rpd')),tokens_per_minute:Number(val('api-tpm'))};
  const secret=val('api-key');
  await api('/ai/profiles',p);
  editing={...p,key_present:key_present||false};
  if(secret)await api('/ai/profiles/'+p.id+'/key',{api_key:secret},'PUT');
  await load();$('api-profile-list').value=p.id;renderProfile(profiles.find(x=>x.id===p.id));
  $('api-key').value='';$('api-feedback').textContent='Подключение сохранено. Платных запросов при сохранении нет.';
 }
 function applyCatalog(role){
  const m=catalog.find(x=>x.id===val('api-'+role+'-model'));
  if(!m)return;
  if(m.supported_parameters){
   $('api-'+role+'-params').value=m.supported_parameters.join(',');
   $('api-'+role+'-bound').value=m.supported_parameters.includes('max_tokens')?'max_tokens':'max_completion_tokens';
   $('api-'+role+'-json').value=m.supported_parameters.includes('structured_outputs')?'json_schema':m.supported_parameters.includes('response_format')?'json_object':'none';
  }
  if(val('api-url')==='https://routerai.ru/api/v1'&&val('api-currency')==='RUB'&&m.pricing_units?.prompt==='token'&&m.pricing_units?.completion==='token'){
   $('api-'+role+'-input-price').value=Number(m.pricing.prompt)*1000000;$('api-'+role+'-output-price').value=Number(m.pricing.completion)*1000000;
  }
 }
 $('api-form').onsubmit=safe(async e=>{e.preventDefault();await save();});
 $('api-profile-list').onchange=()=>renderProfile(profiles.find(p=>p.id===val('api-profile-list')));
 $('api-new').onclick=()=>renderProfile();
 $('api-groq-preset').onclick=safe(async()=>{const data=await api('/ai/groq-preset');renderProfile(data.profile);$('api-feedback').textContent='Новый Groq-профиль выключен, модели не выбраны. Введите новый ключ в этом окне, сохраните профиль, получите каталог /models и выберите модель явно. Кандидаты: '+data.candidates.join(', ')+'. Доступность аккаунту ещё не проверена.';});
 $('api-free-confirmed').onchange=()=>{if($('api-free-confirmed').checked&&val('api-url')==='https://api.groq.com/openai/v1'){for(const role of ['interpreter','narrator'])for(const side of ['input','output'])$('api-'+role+'-'+side+'-price').value='0';$('api-free-only').checked=true;}};
 $('api-quota-refresh').onclick=safe(async()=>{const data=await api('/ai/quota');$('api-quota-status').textContent=editing&&data[editing.id]?JSON.stringify(data[editing.id],null,2):'Заголовки квоты ещё не получены. Локальные лимиты — ваш дополнительный предел, доступный тариф проверяется в аккаунте Groq.';});
 $('api-router-preset').onclick=safe(async()=>{const data=await api('/ai/routerai-preset');renderProfile(data.profile);$('api-feedback').textContent='Новый профиль RouterAI выключен. Роли выбраны по спецификации; тарифы, ключ и доступ аккаунта требуют проверки. При сохранении платных запросов нет.';});
 $('api-activate').onclick=safe(async()=>{if(!editing)throw new Error('Сначала сохраните профиль');await api('/ai/profiles/'+editing.id+'/activate',{});await load();toast('Профиль выбран. Перезапуск не требуется.');});
 $('api-delete').onclick=safe(async()=>{if(!editing)return;await api('/ai/profiles/'+editing.id,undefined,'DELETE');await load();});
 $('api-catalog').onclick=safe(async()=>{
  if(!editing)throw new Error('Сначала сохраните адрес и ключ');
  const d=await api('/ai/profiles/'+editing.id+'/models');catalog=d.models;$('api-models').replaceChildren();
  for(const m of catalog){const o=field('option',m.name||m.id);o.value=m.id;$('api-models').append(o);}
  for(const role of roles)applyCatalog(role);
  $('api-feedback').textContent='Каталог проверен: '+catalog.length+' моделей. Генерация и её качество этим не проверяются.';
 });
 $('api-same-model').onclick=()=>{$('api-narrator-model').value=val('api-interpreter-model');applyCatalog('narrator');};
 $('api-probe').onclick=safe(async()=>{
  if(!editing)throw new Error('Сначала сохраните профиль');
  const d=await api('/ai/profiles/'+editing.id+'/probe',{});$('api-feedback').textContent='Реальный ответ: '+d.text+' · стоимость '+d.metric.cost+' '+d.metric.currency+' ('+d.metric.cost_source+')';
 });
 const money=rows=>rows?.length?rows.map(r=>Number(r.cost).toFixed(6)+' '+r.currency).join(' / '):'0';
 async function expenses(){
  const d=await api('/ai/costs'+(campaignId?'?campaign='+encodeURIComponent(campaignId):''));
  $('cost-summary').replaceChildren();
  for(const [label,rows] of [['Текущий запрос',d.current_turn],['Сессия',d.session],['Кампания',d.campaign],['Все обращения',d.total]]){
   const card=field('div');card.className='metric';card.append(field('strong',money(rows)),field('small',label));$('cost-summary').append(card);
  }
  for(const row of d.average_per_turn||[]){const card=field('div');card.className='metric';card.append(field('strong',Number(row.average).toFixed(6)+' '+row.currency),field('small','Среднее на запрос · '+row.turns));$('cost-summary').append(card);}
  $('cost-approvals').replaceChildren();
  for(const q of d.limits||[]){
   const box=field('div');box.className='list-card';
   box.append(field('p',(q.kind==='day'?'Дневной лимит':'Лимит сессии')+': '+q.spent+' / '+q.limit+' '+q.currency+(q.state==='warning'?' · Осталось менее 20%':q.state==='blocked'?' · Лимит достигнут':q.state==='disabled'?' · Ненулевые запросы требуют отдельного разрешения':'')));
   $('cost-approvals').append(box);
  }
  for(const q of d.pending){
   const box=field('div');box.className='list-card';box.append(field('p','Запрос '+q.purpose+' · '+q.model+' · резерв '+q.quote+' '+q.currency));
   const button=field('button','Разрешить именно этот запрос один раз');button.onclick=safe(async()=>{await api('/ai/budgets/'+q.id+'/approve',{confirm:true});await expenses();toast('Разрешение выдано. Повторите исходный запрос вручную; автоматической отправки нет.');});box.append(button);$('cost-approvals').append(box);
  }
  // Hundred rows of raw calls drown the summary. The newest twenty are shown;
  // the rest stay one click away, with the total named so nothing is lost.
  $('cost-calls').replaceChildren();
  // The note lives outside the table, so every re-entry into this screen must
  // clear the previous one; otherwise the notes pile up.
  document.querySelectorAll('.cost-note').forEach(n=>n.remove());
  const shownCalls=d.calls.slice(0,20);
  for(const r of shownCalls){const tr=field('tr');for(const value of [r.model+' / '+r.purpose,(r.input_tokens??'—')+' / '+(r.output_tokens??'—'),r.cost+' '+r.currency,r.cost_source,r.status])tr.append(field('td',value));$('cost-calls').append(tr);}
  // Nothing is hidden: the remaining rows are one click away and named.
  const rest=d.calls.length-shownCalls.length;
  if(rest>0){const more=el('p');more.className='hint cost-note';
   more.append(el('span','Показаны последние '+shownCalls.length+' из '+d.calls.length+'. '));
   const showAll=field('button','Показать все '+d.calls.length);showAll.type='button';
   showAll.onclick=()=>{$('cost-calls').replaceChildren();for(const r of d.calls){const tr=field('tr');
    for(const value of [r.model+' / '+r.purpose,(r.input_tokens??'—')+' / '+(r.output_tokens??'—'),r.cost+' '+r.currency,r.cost_source,r.status])tr.append(field('td',value));
    $('cost-calls').append(tr);}const all=el('p','Показаны все '+d.calls.length+' записей.');all.className='hint cost-note';more.replaceWith(all);};
   more.append(showAll);$('cost-calls').after(more);}
 }
 $('cost-refresh').onclick=safe(expenses);
 document.querySelector('[data-view="cloud"]').addEventListener('click',safe(load));
 document.querySelector('[data-view="costs"]').addEventListener('click',safe(expenses));
})();
