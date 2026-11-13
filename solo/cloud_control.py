"""Local configuration diagnostics and explicit, non-recurring spend authorization."""
from decimal import Decimal
from pydantic import Field
from .contracts import Strict

ROLES=('interpreter','narrator','secondary_narrator','pdf_analyst','vision')
LABELS=dict(interpreter='Qwen Interpreter',narrator='DeepSeek Primary',secondary_narrator='Venice Secondary',pdf_analyst='Gemini PDF',vision='Зрение: карты')

class EnableCloud(Strict):
    confirm:bool=False
    amount:Decimal=Field(gt=0,le=1000000)
    max_request:Decimal=Field(gt=0,le=1000000)
    currency:str=Field(pattern='^[A-Z]{3}$')

def status(settings,profile_id=None,ledger=None):
    p=settings.profile(profile_id);issues=[]
    def issue(code,message):issues.append(dict(code=code,message=message))
    if not p.enabled:issue('profile_disabled','Профиль выключен. Ключ сохранён отдельно; включите облачный AI с подтверждением лимита.')
    if not settings.vault.exists(p.id):issue('key_missing','API-ключ не сохранён в DPAPI.')
    for field,label in [('max_request_cost','запроса'),('session_budget','сессии'),('daily_budget','дня'),('monthly_budget','месяца'),('preparation_budget','подготовки')]:
        if getattr(p,field)==0:issue('zero_'+field,'Бюджет '+label+' равен 0 '+p.currency+'. Платный запрос потребует отдельного разрешения.')
    roles={}
    for role in ROLES:
        cfg=getattr(p,role);problems=[]
        if cfg is None or not cfg.model:problems.append('Модель не выбрана')
        elif cfg.input_per_million is None or cfg.output_per_million is None:problems.append('Не подтверждены тарифы модели')
        # Without an image price the role looks configured but refuses every
        # picture, so the screen must show the missing tariff, not 'заполнено'.
        if role=='vision' and cfg is not None and cfg.model and cfg.image_per_million is None:problems.append('Не подтверждён тариф изображений за токен')
        if cfg and p.provider.lower()=='routerai' and not cfg.endpoint:problems.append('Маршрут RouterAI не выбран явно')
        reason=None
        if role=='narrator':
            from .ai_roles import narrator_reason
            # The player must see not only which narrator speaks but why it was chosen.
            reason=narrator_reason(p)
        roles[role]=dict(label=LABELS[role],model=cfg.model if cfg else None,endpoint=cfg.endpoint if cfg else None,problems=problems,reason=reason,thinking=cfg.thinking if cfg else None,streaming=p.streaming,key_shared=True)
    remaining=None
    if ledger and p.total_budget is not None:
        with ledger.db() as db:used=db.execute("SELECT coalesce(sum(CASE WHEN status='reserved' THEN reserved ELSE cost END),0) FROM calls WHERE currency=?",(p.currency,)).fetchone()[0]
        remaining=str(max(Decimal(0),p.total_budget-Decimal(used)/1000000))
        if Decimal(remaining)==0:issue('total_budget_exhausted','Общий разовый лимит исчерпан. Подтвердите новый остаток; перезапуск не сбрасывает лимит.')
    return dict(profile=p.id,active=settings.active==p.id,enabled=p.enabled,key_present=settings.vault.exists(p.id),issues=issues,roles=roles,total_budget=str(p.total_budget) if p.total_budget is not None else None,remaining_budget=remaining,currency=p.currency)

def enable(settings,ledger,profile_id,request,session):
    p=settings.profile(profile_id)
    if not request.confirm:raise ValueError('Подтвердите общий разовый лимит расходов.')
    if request.currency!=p.currency:raise ValueError('Валюта подтверждения не совпадает с профилем.')
    if request.max_request>request.amount:raise ValueError('Лимит одного запроса превышает общий остаток.')
    details=status(settings,profile_id)
    if not details['key_present']:raise ValueError('Сначала сохраните один API-ключ профиля в DPAPI.')
    required=['interpreter','secondary_narrator' if p.narrator_selection=='secondary' else 'narrator']
    if p.pdf_analysis_enabled:required.append('pdf_analyst')
    for role in required:
        if details['roles'][role]['problems']:raise ValueError(LABELS[role]+': '+'; '.join(details['roles'][role]['problems']))
    # The ledger already accounts in one currency across profiles. A cumulative
    # ceiling uses that same conservative scope and survives app/month changes.
    with ledger.db() as db:
        rows=db.execute("SELECT session,day,CASE WHEN status='reserved' THEN reserved ELSE cost END AS used FROM calls WHERE currency=?",(p.currency,)).fetchall()
    from datetime import datetime,timezone,timedelta
    day=datetime.now(timezone(timedelta(hours=p.day_utc_offset))).date().isoformat()
    used=lambda selected:Decimal(sum(r['used'] for r in rows if selected(r)))/1000000
    remaining=request.amount
    ceiling=Decimal(1000000)
    values=p.model_dump();values.update(enabled=True,total_budget=used(lambda r:True)+remaining,max_request_cost=request.max_request,
        session_budget=min(ceiling,used(lambda r:r['session']==session)+remaining),daily_budget=min(ceiling,used(lambda r:r['day']==day)+remaining),
        monthly_budget=min(ceiling,used(lambda r:r['day'][:7]==day[:7])+remaining),preparation_budget=remaining)
    p=type(p).model_validate(values)
    settings.save(p);settings.activate(p.id)
    return dict(**status(settings,p.id,ledger),authorized_new_spend=str(remaining),automatic_generation=False,non_recurring=True)
