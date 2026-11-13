"""Groq-specific capability and quota checks around the existing compatible adapter."""
import re,time,math
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from .cloud_settings import CloudProfile,ModelConfig
BASE='https://api.groq.com/openai/v1'
CANDIDATES=['qwen/qwen3.8-27b','openai/gpt-oss-120b']
def is_groq(p):return p.base_url.rstrip('/')==BASE
def preset():
    return CloudProfile(name='Groq · бесплатный профиль',provider='Groq',base_url=BASE,currency='USD',enabled=False,free_only=True,free_account_confirmed=False,retry_429=0,context_tokens=8192,interpreter=ModelConfig(max_tokens=1000,temperature=.05,structured='json_object',parameters=['max_tokens','temperature','response_format','reasoning_effort'],reasoning_effort='none'),narrator=ModelConfig(max_tokens=600,temperature=.65,parameters=['max_tokens','temperature','reasoning_effort'],reasoning_effort='none'))
def seconds(value):
    if not value:return None
    try:
        result=float(value)
        if math.isfinite(result) and 0<=result<=86400:return result
    except (ValueError,TypeError):pass
    try:return max(0,min(86400,parsedate_to_datetime(value).timestamp()-time.time()))
    except (ValueError,TypeError,OverflowError):pass
    parts=re.findall(r'(\d+(?:\.\d+)?)(ms|h|m|s)',str(value))
    if parts and ''.join(v+k for v,k in parts)==str(value):return min(86400,sum(float(v)*{'h':3600,'m':60,'s':1,'ms':.001}[k] for v,k in parts))
    return None
def check(settings,ledger,p,cfg,input_estimate,output_limit):
    if cfg.model not in {m['id'] for m in settings.catalog.get(p.id,[])}:raise ValueError('Groq: модель не подтверждена каталогом /models этого профиля. Обновите каталог и выберите доступную модель; автоматической замены нет.')
    if p.free_only and (not p.free_account_confirmed or cfg.input_per_million!=0 or cfg.output_per_million!=0):raise ValueError('Groq: подтвердите бесплатный тариф аккаунта и нулевые тарифы. До этого генерация заблокирована.')
    if cfg.model.startswith('openai/gpt-oss') and cfg.reasoning_effort in ('none','default'):raise ValueError('GPT-OSS поддерживает low/medium/high, отключение thinking не подтверждено. Выберите low или другую модель.')
    quota=settings.quota.get(p.id,{}).get(cfg.model,{})
    if quota.get('cooldown_until',0)>time.time():raise ValueError('Groq: лимит исчерпан. Следующая попытка после '+datetime.fromtimestamp(quota['cooldown_until'],timezone.utc).isoformat(timespec='seconds'))
    if ledger is None:return
    with ledger.db() as c:
        minute=c.execute('SELECT count(*),coalesce(sum(coalesce(input_tokens,0)+coalesce(output_tokens,0)),0) FROM calls WHERE provider=? AND model=? AND created>?',(p.provider,cfg.model,time.time()-60)).fetchone()
        daily=c.execute('SELECT count(*) FROM calls WHERE provider=? AND model=? AND created>?',(p.provider,cfg.model,time.time()-86400)).fetchone()[0]
    if minute[0]>=p.requests_per_minute or daily>=p.requests_per_day:raise ValueError('Groq: локальный лимит запросов исчерпан. Дождитесь обновления квоты.')
    if minute[1]+input_estimate+output_limit>p.tokens_per_minute:raise ValueError('Groq: локальный минутный лимит токенов. Уменьшите контекст/ответ либо подождите.')
def record(settings,p,model,headers,status):
    names=['retry-after',*[f'x-ratelimit-{kind}-{unit}' for kind in ('limit','remaining','reset') for unit in ('requests','tokens')]]
    safe={};cooldown=0
    for k in names:
        value=str(headers.get(k,''))
        if ('limit-' in k or 'remaining-' in k) and re.fullmatch(r'\d{1,10}',value):safe[k]=value
        elif ('reset-' in k or k=='retry-after') and seconds(value) is not None:
            safe[k]=str(seconds(value))
    for unit in ('requests','tokens'):
        if safe.get('x-ratelimit-remaining-'+unit)=='0':cooldown=max(cooldown,seconds(safe.get('x-ratelimit-reset-'+unit)) or 1)
    delay=seconds(safe.get('retry-after'))
    if status==429:cooldown=max(cooldown,delay if delay is not None else 1)
    settings.quota.setdefault(p.id,{})[model]=dict(headers=safe,checked=time.time(),status=status,cooldown_until=time.time()+cooldown if cooldown else 0)
    settings.persist();return delay,cooldown
