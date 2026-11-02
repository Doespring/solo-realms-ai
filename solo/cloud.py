"""One OpenAI-compatible cloud adapter. No local process, weights, fallback or paid warmup."""
import asyncio,contextvars,json,time,uuid
from contextlib import contextmanager,aclosing
import httpx
from .cloud_cost import BudgetExceeded,amount
from .model_protocol import ModelBehaviour

class CloudUnavailable(RuntimeError):pass
class GroqThrottle(CloudUnavailable):
    def __init__(self,delay):self.delay=delay;super().__init__('Groq: HTTP 429, квота исчерпана. Сохранения и подтверждённые результаты остаются доступны.')


class _TaskMetrics(list):
    """Keep global history, but metrics[-1] in a caller refers to its own request."""
    def __init__(self):
        super().__init__();self.current=contextvars.ContextVar('cloud_current_metric',default=None)
    def append(self,value):
        super().append(value);self.current.set(value)
    def __getitem__(self,index):
        if isinstance(index,int) and index==-1 and self:
            current=self.current.get()
            if current is not None:return current
        return super().__getitem__(index)


class CloudLLM(ModelBehaviour):
    def __init__(self,settings=None,ledger=None,*,client=None):
        self.settings=settings;self.ledger=ledger;self.client=client
        self.lock=asyncio.Lock()  # Compatibility name: fallback for no campaign only.
        self.campaign_locks={};self.metrics=_TaskMetrics()
        self._narrative=contextvars.ContextVar('cloud_narrative')
        self._narrative_policy=contextvars.ContextVar('cloud_narrative_policy')
        self._last_narrative=None;self._last_narrative_policy='model'
        self.session=str(uuid.uuid4());self.context=contextvars.ContextVar('cloud_scope',default=(None,'connection'))
        self.last_connection=None
        self.last_generation=None
    @property
    def last_narrative(self):return self._narrative.get(self._last_narrative)
    @last_narrative.setter
    def last_narrative(self,value):self._narrative.set(value);self._last_narrative=value
    @property
    def last_narrative_policy(self):return self._narrative_policy.get(self._last_narrative_policy)
    @last_narrative_policy.setter
    def last_narrative_policy(self,value):self._narrative_policy.set(value);self._last_narrative_policy=value
    def campaign_lock(self,campaign):
        if campaign is None:return self.lock
        if campaign not in self.campaign_locks:self.campaign_locks[campaign]=asyncio.Lock()
        return self.campaign_locks[campaign]
    def contract_error(self,code):
        if self.metrics:
            self.metrics[-1].update(status='invalid_contract',error_code=code)
            if self.ledger:self.ledger.contract_error(self.metrics[-1]['id'],code)
    @contextmanager
    def scope(self,campaign,turn_ref):
        token=self.context.set((campaign,turn_ref))
        try:yield
        finally:self.context.reset(token)
    def configured(self):
        if self.settings is None:raise CloudUnavailable('Облачный Мастер не подключён. Настройте провайдера в разделе «Нейросети и API».')
        try:p=self.settings.profile()
        except ValueError as exc:raise CloudUnavailable(str(exc)) from None
        if not p.enabled:raise CloudUnavailable('Профиль облачного AI выключен. Ключ сам по себе не включает расходы. В «Нейросети и API» нажмите «Включить облачный AI» и подтвердите лимит.')
        try:key=self.settings.vault.get(p.id)
        except RuntimeError:raise CloudUnavailable('Windows не смог открыть защищённый ключ. Введите ключ заново.') from None
        if not key:raise CloudUnavailable('API-ключ не задан. Локального AI fallback нет.')
        return p,key
    def http(self):
        if self.client is None or self.client.is_closed:
            self.client=httpx.AsyncClient(trust_env=False,follow_redirects=False)
        return self.client
    @staticmethod
    def provider_error(code,stream=False):
        reasons={400:'Провайдер отклонил параметры модели',401:'API-ключ отклонён провайдером',402:'Недостаточно средств у провайдера',403:'Провайдер запретил доступ к модели',404:'Модель или выбранный маршрут недоступны',429:'Провайдер ограничил частоту или квоту запросов',502:'Зеркало выбранной модели вернуло ошибку шлюза',503:'Выбранные зеркала модели временно недоступны',504:'Зеркало выбранной модели не ответило вовремя'}
        text=('Ошибка внутри SSE: ' if stream else '')+reasons.get(code,'Ошибка облачного провайдера')+(' (HTTP '+str(code)+')' if isinstance(code,int) else '')
        if code in (502,503,504):
            return text+'. Ход не выполнен и не оплачен дважды. Повторите через минуту или выберите другой маршрут этой же модели в настройках рассказа.'
        return text+'. Автоматического повтора или смены модели нет.'
    async def close(self):
        if self.client is not None:await self.client.aclose()
    async def available(self):
        try:
            p,key=self.configured()
            if not p.interpreter.model or not p.narrator.model:return None
            from .ai_roles import select_role
            narrator,_=select_role(p,'narrator')
            return dict(result=p.provider+' / '+narrator.model,cloud=True,connection_verified=bool(self.last_connection))
        except (ValueError,RuntimeError):return None
    async def warmup(self):
        # Configuration only; startup must never issue an inference call.
        return dict(status='configured' if await self.available() else 'unconfigured',warmup='no_paid_startup_request')
    async def interpret(self,text,scene,*,seed=None):
        profile,_=self.configured()
        if profile.interpreter_contract=='legacy':return await super().interpret(text,scene,seed=seed)
        from .action_validator import IntentPacket,ActionValidator
        raw=''.join([part async for part in self.stream('',json.dumps({'реплика':text,'сцена':scene},ensure_ascii=False),mode='interpreter',grammar=IntentPacket.model_json_schema(),seed=seed)])
        from .json_framing import unframe
        try:return ActionValidator.validate(unframe(raw)[0],text,scene)
        except ValueError:
            self.contract_error('invalid_qwen_intent');raise
    async def models(self,profile_id=None):
        if self.settings is None:raise CloudUnavailable('Провайдер не настроен')
        p=self.settings.profile(profile_id);key=self.settings.vault.get(p.id)
        if not key:raise CloudUnavailable('API-ключ не задан')
        try:
            response=await self.http().get(p.base_url+'/models',headers={'Authorization':'Bearer '+key},timeout=p.timeout_seconds)
            if not response.is_success:raise CloudUnavailable('Проверка каталога: '+self.provider_error(response.status_code))
            payload=response.json()
            data=payload.get('data') if isinstance(payload,dict) else None
            if not isinstance(data,list):raise CloudUnavailable('Провайдер вернул неизвестный формат каталога')
            catalog=[]
            for row in data:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str):continue
                modalities=row.get('architecture',{}).get('output_modalities',['text'])
                if 'text' not in modalities:continue
                catalog.append({k:row[k] for k in ['id','name','context_length','context_window','supported_parameters','pricing','pricing_units'] if k in row})
            if key and key in json.dumps(catalog):raise CloudUnavailable('Каталог провайдера содержит недопустимые данные')
            self.settings.catalog[p.id]=catalog;self.settings.persist()
            self.last_connection=time.time();return dict(models=catalog,verified='catalog_only',generation_tested=False)
        except httpx.HTTPError:raise CloudUnavailable('Провайдер недоступен: соединение или таймаут') from None
        except (ValueError,TypeError):raise CloudUnavailable('Провайдер вернул повреждённый каталог') from None
    async def endpoints(self,profile_id,role):
        from urllib.parse import quote
        p=self.settings.profile(profile_id)
        if p.provider.lower()!='routerai':raise ValueError('Каталог маршрутов доступен для RouterAI')
        if role not in ('interpreter','narrator','secondary_narrator','pdf_analyst'):raise ValueError('Неизвестная роль')
        cfg=getattr(p,role)
        if cfg is None or not cfg.model:raise ValueError('Модель роли не выбрана')
        try:
            response=await self.http().get(p.base_url+'/models/'+quote(cfg.model,safe='/')+'/endpoints',timeout=p.timeout_seconds)
            if not response.is_success:raise CloudUnavailable('Маршруты: HTTP '+str(response.status_code))
            data=response.json().get('data',{});rows=data.get('endpoints',[]) if isinstance(data,dict) else []
            if not isinstance(rows,list):raise ValueError('Неверный каталог маршрутов')
            fields=('name','provider_name','tag','context_length','max_completion_tokens','max_prompt_tokens','status','pricing','pricing_units','variable_pricings','supported_parameters','country','supported_apis','reasoning')
            result=[{k:r[k] for k in fields if k in r} for r in rows if isinstance(r,dict)]
            return dict(model=cfg.model,endpoints=result,checked_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),generation_tested=False)
        except httpx.HTTPError:raise CloudUnavailable('Не удалось получить маршруты RouterAI') from None
    async def stream(self,system,user,**kwargs):
        from .groq import is_groq
        attempts=0
        while True:
            emitted=False
            try:
                async with aclosing(self._stream_once(system,user,**kwargs)) as response_stream:
                    async for piece in response_stream:
                        emitted=True;yield piece
                return
            except GroqThrottle as exc:
                p,_=self.configured()
                if emitted or not is_groq(p) or not p.free_only or not p.free_account_confirmed or attempts>=p.retry_429 or exc.delay is None or exc.delay>10:raise
                attempts+=1
                # Cancellation interrupts backoff; never retry an interrupted/partial generation.
                await asyncio.sleep(max(.1,exc.delay)+.05)
    async def _stream_once(self,system,user,*,mode='narrator',max_tokens=None,temperature=None,grammar=None,seed=None,**unused):
        async with self.campaign_lock(self.context.get()[0]):
            p,key=self.configured()
            from .ai_roles import select_role,prompt,reasoning_parameters
            cfg,prompt_version=select_role(p,mode)
            if mode=='interpreter' and unused.get('command_contract'):prompt_version='gm_commands_v1'
            if mode=='preparation_world' and unused.get('source_extract'):prompt_version='qwen_source_extract_v1'
            from .prompt_compaction import compact_schema,compact_user
            user=compact_user(user,mode)
            from .analysis_images import image_count
            images=image_count(user)
            if images:
                if mode!='pdf_analyst' or cfg.image_per_million is None:raise ValueError('Для изображений нужен Gemini PDF с отдельно подтверждённым тарифом изображений за токен')
                cfg=cfg.model_copy(update=dict(input_per_million=max(cfg.input_per_million or 0,cfg.image_per_million,cfg.cached_input_per_million or 0,cfg.cache_read_per_million or 0),cached_input_per_million=None,cache_read_per_million=None))
            if prompt_version:
                system=prompt(prompt_version)+'\n'+system
            elif mode=='narrator' and unused.get('narrative_contract'):system=prompt('gm_narrative_contract_v1')+'\n'+system
            if grammar and cfg.structured!='json_schema':system+='\nJSON schema (верни только объект этой схемы):\n'+json.dumps(compact_schema(grammar),ensure_ascii=False,separators=(',',':'))
            reasoning,reasoning_status=reasoning_parameters(p,cfg)
            if not cfg.model:raise CloudUnavailable('Не выбрана модель '+mode)
            tokens=cfg.max_tokens if max_tokens is None else min(max_tokens,cfg.max_tokens)
            if mode=='narrator':
                from .cloud_context import move_turn_limits
                system,user=move_turn_limits(system,user,tokens)
            from .groq import is_groq,check,record
            groq=is_groq(p)
            body={'model':cfg.model,'messages':[{'role':'system','content':system},{'role':'user','content':user}],
                  cfg.max_token_parameter:tokens,'stream':p.streaming}
            if 'temperature' in cfg.parameters:body['temperature']=cfg.temperature if temperature is None else temperature
            if cfg.top_p is not None and 'top_p' in cfg.parameters:body['top_p']=cfg.top_p
            if seed is not None and 'seed' in cfg.parameters:body['seed']=seed
            if grammar and cfg.structured=='json_schema':body['response_format']={'type':'json_schema','json_schema':{'name':'game_contract','strict':True,'schema':grammar}}
            elif grammar and cfg.structured=='json_object':body['response_format']={'type':'json_object'}
            if p.streaming and p.stream_usage:body['stream_options']={'include_usage':True}
            if cfg.reasoning_effort is not None and 'reasoning_effort' in cfg.parameters:body['reasoning_effort']=cfg.reasoning_effort
            body.update(reasoning)
            # The chosen mirror is a preference, not a promise. A mirror that
            # reports 502/503/504 has not done any work yet, so letting the same
            # model answer elsewhere costs nothing twice and keeps the turn alive.
            # Any other refusal stays a refusal: switching models behind the
            # player's back would change the voice they chose.
            pinned=p.provider.lower()=='routerai' and cfg.endpoint
            if pinned:body['provider']={'only':[cfg.endpoint],'allow_fallbacks':True}
            if groq and cfg.model.startswith('qwen/'):body['reasoning_format']='hidden'
            elif groq and cfg.model.startswith('openai/gpt-oss'):body['include_reasoning']=False
            from .cloud_context import estimate_context
            estimate=estimate_context(body['messages'],response_format=body.get('response_format'),image_count=images)
            input_estimate=estimate['input_tokens_estimate']
            if input_estimate+tokens>p.context_tokens:
                raise ValueError('Оценка контекста с запасом превышает настроенный бюджет. Уменьшите профиль или память.')
            if groq:
                try:check(self.settings,self.ledger,p,cfg,input_estimate,tokens)
                except ValueError as exc:raise CloudUnavailable(str(exc)) from None
            if self.ledger is None:raise CloudUnavailable('Журнал расходов не подключён')
            campaign,turn_ref=self.context.get()
            try:call,quote=self.ledger.reserve(p,cfg,body,campaign=campaign,session=self.session,turn_ref=turn_ref,purpose=mode,input_estimate=input_estimate,estimate_details=estimate)
            except ValueError as exc:raise CloudUnavailable(str(exc)) from None
            started=time.perf_counter();usage=None;not_sent=False;output='';reasoning_seen=False;refusal_seen=False
            from .visible_content import text as visible_text,no_visible
            metric=dict(id=call,mode=mode,provider=p.provider,model=cfg.model,started=time.time(),status='running',
                        context_tokens=input_estimate,input_tokens_estimated=True,output_tokens=None,reserved_cost=amount(quote),currency=p.currency,
                        planned_input_tokens=input_estimate,output_limit_tokens=tokens,context_limit_tokens=p.context_tokens,
                        actual_input_tokens=None,actual_output_tokens=None)
            metric.update(prompt_version=prompt_version,thinking_requested=cfg.thinking,thinking_control=reasoning_status,requested_endpoint=cfg.endpoint)
            metric['context_estimate']=estimate
            self.metrics.append(metric);self.metrics[:]=self.metrics[-200:]
            def record_route(data):
                generation=data.get('id')
                if isinstance(generation,str) and len(generation)<=200 and key not in generation:metric['generation_id']=generation
                endpoint=data.get('provider')
                if isinstance(endpoint,str) and len(endpoint)<=160 and key not in endpoint:metric['actual_endpoint']=endpoint
            try:
                async with self.http().stream('POST',p.base_url+'/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json=body,timeout=p.timeout_seconds) as response:
                    if groq:retry_delay,cooldown=record(self.settings,p,cfg.model,response.headers,response.status_code)
                    if not response.is_success:
                        try:
                            failure=json.loads(await response.aread())
                            if isinstance(failure,dict):usage=failure.get('usage')
                        except (ValueError,TypeError):pass
                        metric['error_code']=('http_'+str(response.status_code)) if not (pinned and response.status_code in (502,503,504)) else 'http_'+str(response.status_code)+'_mirror_down'
                        if groq and response.status_code==429:raise GroqThrottle(max(retry_delay,cooldown) if retry_delay is not None else None)
                        raise CloudUnavailable(self.provider_error(response.status_code))
                    if p.streaming:
                        block=[];done=False
                        async def events():
                            nonlocal block
                            async for line in response.aiter_lines():
                                if line=='':
                                    if block:yield '\n'.join(block);block=[]
                                elif line.startswith('data:'):block.append(line[5:].lstrip())
                            if block:yield '\n'.join(block);block=[]
                        async for event in events():
                            if event.strip()=='[DONE]':done=True;break
                            data=json.loads(event)
                            if not isinstance(data,dict):raise ValueError('Неверная форма SSE')
                            record_route(data)
                            if data.get('usage'):usage=data['usage']
                            if data.get('error'):
                                error=data['error'];code=error.get('code') if isinstance(error,dict) else None
                                if isinstance(code,str):code=int(code) if code.isdigit() else {'rate_limit_exceeded':429,'model_not_found':404,'invalid_api_key':401,'service_unavailable':503}.get(code)
                                metric['error_code']='sse_'+str(code) if isinstance(code,int) else 'sse_provider'
                                raise CloudUnavailable(self.provider_error(code,stream=True))
                            for choice in data.get('choices',[]):
                                if choice.get('finish_reason'):
                                    metric['finish_reason']=str(choice['finish_reason'])[:40]
                                if choice.get('finish_reason')=='length':metric['hit_token_limit']=True
                                delta=choice.get('delta') or {};message=choice.get('message') or {}
                                reasoning_seen=reasoning_seen or bool(delta.get('reasoning') or delta.get('reasoning_content') or message.get('reasoning') or message.get('reasoning_content'))
                                refusal_seen=refusal_seen or bool(delta.get('refusal') or message.get('refusal'))
                                content=visible_text(delta.get('content'))
                                if not content and not output:content=visible_text(message.get('content'))
                                # reasoning/reasoning_content/tool_calls are not literary prose.
                                if isinstance(content,str) and content:
                                    metric.setdefault('ttft_ms',round((time.perf_counter()-started)*1000,2));output+=content;yield content
                        if not done:
                            metric['error_code']='incomplete_stream'
                            raise CloudUnavailable('Облачный поток прерван без завершения. Частичный ответ не подтверждён; автоматического повтора нет.')
                    else:
                        raw=await response.aread();data=json.loads(raw)
                        record_route(data)
                        usage=data.get('usage');choices=data.get('choices',[])
                        if not choices:raise CloudUnavailable(no_visible(metric,False,False))
                        message=choices[0].get('message') or {}
                        reasoning_seen=bool(message.get('reasoning') or message.get('reasoning_content'));refusal_seen=bool(message.get('refusal'))
                        output=visible_text(message.get('content'))
                        metric['hit_token_limit']=choices[0].get('finish_reason')=='length'
                        if output:metric['ttft_ms']=round((time.perf_counter()-started)*1000,2);yield output
                    if not output.strip():
                        details=(usage or {}).get('completion_tokens_details') or (usage or {}).get('output_tokens_details') or {}
                        reasoning_seen=reasoning_seen or bool(details.get('reasoning_tokens') or (usage or {}).get('reasoning_tokens'))
                        raise CloudUnavailable(no_visible(metric,reasoning_seen,refusal_seen))
                metric['status']='ok';self.last_connection=time.time();self.last_generation=time.time()
            except (asyncio.CancelledError,GeneratorExit):
                metric['status']='cancelled';metric['error_code']='client_cancelled';raise
            except httpx.ConnectError:
                not_sent=True;metric['status']='error';metric['error_code']='connection';raise CloudUnavailable('Облачный Мастер недоступен. Сохранения доступны локально.') from None
            except httpx.TimeoutException:
                metric['status']='error';metric['error_code']='timeout';raise CloudUnavailable('Таймаут облачного Мастера. Автоматического повтора нет.') from None
            except httpx.HTTPError:
                metric['status']='error';metric['error_code']='network';raise CloudUnavailable('Соединение с облачным Мастером прервано') from None
            except (ValueError,TypeError,KeyError,AttributeError):
                metric['status']='error';metric['error_code']='invalid_response';raise CloudUnavailable('Провайдер вернул неверный JSON или структуру ответа. Результаты механики сохранены; автоматического повтора нет.') from None
            except CloudUnavailable:
                metric['status']='error';metric.setdefault('error_code','provider');raise
            finally:
                metric['duration_ms']=round((time.perf_counter()-started)*1000,2)
                result=self.ledger.finish(call,p,cfg,usage,metric,definitely_not_sent=not_sent)
                if groq and p.free_only and isinstance(usage,dict) and usage.get('cost') is not None:
                    from decimal import Decimal,InvalidOperation
                    try:
                        if Decimal(str(usage['cost']))>0:
                            p.enabled=False;p.free_account_confirmed=False;self.settings.save(p);metric['free_account_warning']='Provider reported nonzero cost; profile disabled'
                    except (InvalidOperation,ValueError):pass
                metric.update(result);metric['output_tokens']=result['output_tokens']
                metric['actual_input_tokens']=result['input_tokens'];metric['actual_output_tokens']=result['output_tokens']
                if result['input_tokens'] is not None:metric['context_tokens']=result['input_tokens'];metric['input_tokens_estimated']=False
                if result['output_tokens'] is not None:metric['tokens_per_sec']=round(result['output_tokens']/(metric['duration_ms']/1000),2)
