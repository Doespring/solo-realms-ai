"""Opt-in metadata setup. Never generates, authorizes spend or replaces another profile."""
import time
from decimal import Decimal,InvalidOperation
from pydantic import Field,SecretStr
from typing import Literal
from .contracts import Strict
from .cloud_control import ROLES,LABELS,status

class SetupCloud(Strict):
    provider:Literal['RouterAI','Groq']='RouterAI'
    profile:str|None=None
    api_key:SecretStr|None=Field(default=None,max_length=4096)
    free_account_confirmed:bool=False

def price(row,key):
    if row.get('pricing_units',{}).get(key)!='token':raise ValueError('Неизвестная единица тарифа')
    values=[row.get('pricing',{}),*row.get('variable_pricings',[])]
    try:
        numbers=[Decimal(str(v.get(key,values[0][key]))) for v in values]
        if not all(v.is_finite() and v>=0 for v in numbers):raise ValueError('Неверный тариф')
        return max(numbers)*1000000
    except (KeyError,TypeError,InvalidOperation):raise ValueError('Тариф не подтверждён') from None

def configure_route(cfg,row):
    params=row.get('supported_parameters',[])
    if not isinstance(params,list) or not all(isinstance(v,str) for v in params):raise ValueError('Неизвестные параметры маршрута')
    bound=next((v for v in ('max_tokens','max_completion_tokens') if v in params),None)
    if not bound or not row.get('tag'):raise ValueError('Нет ограничения ответа или маршрута')
    if row.get('status',0)<0:raise ValueError('Маршрут временно недоступен')
    apis=row.get('supported_apis',[])
    if apis and 'chat' not in apis:raise ValueError('Маршрут не поддерживает chat')
    context=int(row.get('context_length') or 0)
    maximum=int(row.get('max_completion_tokens') or context)
    if context<8192 or maximum<256:raise ValueError('Недостаточный контекст/лимит ответа')
    values=cfg.model_dump();values.update(endpoint=row['tag'],parameters=params,max_token_parameter=bound,
        input_per_million=price(row,'prompt'),output_per_million=price(row,'completion'),
        max_tokens=min(cfg.max_tokens,maximum,context-1024),reasoning_effort=None,
        # RouterAI — OpenAI-совместимый провайдер: маршрут, который заявляет
        # `structured_outputs` в каталоге, получает схему, а не только «верни
        # JSON». Иначе модель вольна придумывать ключи.
        structured=('json_schema' if 'structured_outputs' in params else 'json_object') if cfg.structured!='none' and 'response_format' in params else 'none',
        price_checked_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),image_per_million=None,
        cached_input_per_million=None,cache_read_per_million=None)
    if row.get('pricing_units',{}).get('internal_reasoning')=='token':values['output_per_million']=max(values['output_per_million'],price(row,'internal_reasoning'))
    if row.get('pricing_units',{}).get('image')=='token':values['image_per_million']=price(row,'image')
    mandatory=row.get('reasoning',{}).get('required',False) if isinstance(row.get('reasoning'),dict) else False
    low=cfg.model.startswith('google/gemini-3') or mandatory
    values.update(thinking='low' if low else 'off',reasoning_control='routerai_enabled' if 'reasoning' in params else 'effort' if 'reasoning_effort' in params else 'unverified')
    if values['reasoning_control']=='unverified':values['thinking']='provider_default'
    if low:
        if maximum<1024:raise ValueError('Лимит недостаточен для reasoning и ответа')
        values['max_tokens']=min(max(cfg.max_tokens,2048),maximum,context-1024)
    return type(cfg).model_validate(values),min(context,int(row.get('max_prompt_tokens') or context))

async def setup(llm,request):
    settings=llm.settings
    if request.profile:
        original=settings.profile(request.profile)
        if original.provider!=request.provider:raise ValueError('Для другого провайдера создайте новое подключение')
        p=original.model_copy(deep=True)
    else:
        if request.provider=='RouterAI':
            from .ai_roles import routerai_preset
            p=routerai_preset()
        else:
            from .groq import preset
            p=preset()
        settings.save(p)
    if request.api_key and request.api_key.get_secret_value().strip():settings.vault.set(p.id,request.api_key.get_secret_value().strip())
    catalog=(await llm.models(p.id))['models'];models={m['id']:m for m in catalog};issues=[];limits=[];checks=[]
    if request.provider=='RouterAI':
        for role in ROLES:
            cfg=getattr(p,role)
            if cfg is None:continue
            if cfg.model not in models:
                issues.append(LABELS[role]+': сохранённая модель отсутствует в каталоге');continue
            data=await llm.endpoints(p.id,role);valid=[]
            for row in data['endpoints']:
                try:valid.append(configure_route(cfg,row))
                except (ValueError,TypeError,OverflowError):continue
            if not valid:
                issues.append(LABELS[role]+': совместимый маршрут с известным тарифом не найден');continue
            valid.sort(key=lambda pair:(pair[0].endpoint!=cfg.endpoint,pair[0].input_per_million+pair[0].output_per_million,pair[0].endpoint))
            chosen,context=valid[0];setattr(p,role,chosen);limits.append(context)
            checks.append(dict(role=role,model=chosen.model,endpoint=chosen.endpoint,context_tokens=context,max_tokens=chosen.max_tokens,thinking=chosen.thinking))
    else:
        from .groq import CANDIDATES
        selected=p.interpreter.model or next((m for m in CANDIDATES if m in models),'')
        if not selected or selected not in models:issues.append('Поддерживаемая модель Groq не найдена в каталоге')
        else:
            for role in ('interpreter','narrator'):
                cfg=getattr(p,role);cfg.model=cfg.model or selected
                if cfg.model not in models:issues.append('Сохранённая модель Groq отсутствует в каталоге');continue
                cfg.reasoning_effort='low' if cfg.model.startswith('openai/gpt-oss') else 'none'
                cfg.reasoning_control='effort';cfg.thinking='low' if cfg.reasoning_effort=='low' else 'off'
                cfg.cached_input_per_million=None;cfg.cache_read_per_million=None
                if request.free_account_confirmed:cfg.input_per_million=cfg.output_per_million=Decimal(0)
                context=int(models[cfg.model].get('context_window') or models[cfg.model].get('context_length') or 8192);limits.append(context)
                if cfg.thinking=='low':cfg.max_tokens=min(max(cfg.max_tokens,1800),context-1024)
                checks.append(dict(role=role,model=cfg.model,endpoint='Groq',context_tokens=context,max_tokens=cfg.max_tokens,thinking=cfg.thinking))
            p.free_account_confirmed=request.free_account_confirmed;p.retry_429=0
            if not p.free_account_confirmed:issues.append('Подтвердите бесплатный тариф Groq в аккаунте; каталог не подтверждает биллинг')
    if not issues:
        # A catalogue re-check must not revoke spending consent the owner already gave.
        # Only a brand-new profile starts disabled; an existing one keeps its choice.
        p.context_tokens=min(65536 if request.provider=='Groq' else p.context_tokens,*limits)
        p.enabled=original.enabled if request.profile else False
        p.retry_429=0
        p=type(p).model_validate(p.model_dump());settings.save(p)
    return dict(profile=p.id,ready=not issues,connection_verified=True,key_verified=False,generation_tested=False,
        model_count=len(models),checks=checks,issues=issues,quota_verified=False,
        message='Каталог и совместимость проверены. Генерация не запускалась. Квота/баланс аккаунта неизвестны.',
        status=status(settings,p.id,llm.ledger))
