"""Cloud profiles without secrets, plus one-way Windows CurrentUser DPAPI key storage."""
import ctypes
import json
import os
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from decimal import Decimal
from pydantic import Field,model_validator
from .contracts import Strict

class ModelConfig(Strict):
    model: str=Field(default='',max_length=160)
    max_tokens: int=Field(default=600,ge=16,le=32768)
    temperature: float=Field(default=.65,ge=0,le=2)
    top_p: float|None=Field(default=None,ge=0,le=1)
    parameters: list[str]=Field(default_factory=lambda:['max_tokens','temperature'],max_length=40)
    max_token_parameter: Literal['max_tokens','max_completion_tokens']='max_tokens'
    structured: Literal['none','json_object','json_schema']='none'
    reasoning_effort: Literal['none','default','low','medium','high'] | None=None
    thinking: Literal['provider_default','off','low','medium']='provider_default'
    reasoning_control: Literal['unverified','routerai_enabled','effort']='unverified'
    endpoint: str=Field(default='',max_length=160)
    price_checked_at: str=Field(default='',max_length=80)
    input_per_million: Decimal|None=Field(default=None,ge=0,le=10000000)
    cached_input_per_million: Decimal|None=Field(default=None,ge=0,le=10000000)
    cache_read_per_million: Decimal|None=Field(default=None,ge=0,le=10000000,exclude_if=lambda value:value is None)
    output_per_million: Decimal|None=Field(default=None,ge=0,le=10000000)
    image_per_million: Decimal|None=Field(default=None,ge=0,le=10000000)

    @model_validator(mode='after')
    def cache_aliases(self):
        if self.cache_read_per_million is not None and self.cached_input_per_million is not None and self.cache_read_per_million!=self.cached_input_per_million:
            raise ValueError('Конфликт тарифов cache_read_per_million и cached_input_per_million')
        return self

class CloudProfile(Strict):
    enabled: bool=False
    id: str=Field(default_factory=lambda:str(uuid.uuid4()))
    name: str=Field(default='Облачный Мастер',min_length=1,max_length=100)
    provider: str=Field(default='OpenAI-compatible',min_length=1,max_length=100)
    base_url: str=Field(min_length=8,max_length=500)
    interpreter: ModelConfig=Field(default_factory=lambda:ModelConfig(max_tokens=1200,temperature=.05))
    narrator: ModelConfig=Field(default_factory=ModelConfig)
    secondary_narrator: ModelConfig | None=None
    pdf_analyst: ModelConfig | None=None
    narrator_selection: Literal['primary','secondary']='primary'
    interpreter_contract: Literal['legacy','qwen_interpreter_v1']='legacy'
    pdf_analysis_enabled: bool=False
    # Which configured model reads the book while the world is structured.
    # A book is read, not role-played, so the PDF-capable analyst is the honest
    # default; 'interpreter' keeps the old behaviour for a profile without one.
    preparation_role: Literal['analyst','interpreter']='analyst'
    monthly_budget: Decimal | None=Field(default=None,ge=0,le=1000000)
    preparation_budget: Decimal | None=Field(default=None,ge=0,le=1000000)
    streaming: bool=True
    stream_usage: bool=False
    timeout_seconds: int=Field(default=90,ge=5,le=240)
    context_tokens: int=Field(default=16384,ge=2048,le=1048576)
    currency: str=Field(default='RUB',pattern='^[A-Z]{3}$')
    usage_cost_currency: str|None=Field(default=None,pattern='^[A-Z]{3}$')
    max_request_cost: Decimal=Field(default=Decimal('0'),ge=0,le=1000000)
    session_budget: Decimal=Field(default=Decimal('0'),ge=0,le=1000000)
    daily_budget: Decimal=Field(default=Decimal('0'),ge=0,le=1000000)
    total_budget: Decimal|None=Field(default=None,ge=0) # cumulative ceiling, never resets with date/session
    day_utc_offset: int=Field(default=4,ge=-12,le=14)
    free_only: bool=False
    free_account_confirmed: bool=False
    retry_429: int=Field(default=0,ge=0,le=1)
    requests_per_minute: int=Field(default=5,ge=1,le=1000)
    requests_per_day: int=Field(default=200,ge=1,le=1000000)
    tokens_per_minute: int=Field(default=8000,ge=100,le=10000000)
    @model_validator(mode='after')
    def secure(self):
        if self.provider.lower()=='groq' and self.base_url.rstrip('/')!='https://api.groq.com/openai/v1':raise ValueError('Groq использует только https://api.groq.com/openai/v1')
        self.id=str(uuid.UUID(self.id))
        p=urlsplit(self.base_url)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError('Используйте HTTPS Base URL без ключа, userinfo, query или fragment')
        if p.hostname.lower() in ('localhost','127.0.0.1','::1'):
            raise ValueError('Локальный inference не поддерживается')
        self.base_url=self.base_url.rstrip('/')
        if self.provider.lower()=='routerai' and self.base_url!='https://routerai.ru/api/v1':raise ValueError('RouterAI использует https://routerai.ru/api/v1')
        if self.narrator_selection=='secondary' and self.secondary_narrator is None:raise ValueError('Дополнительный рассказчик не настроен')
        for model in (self.interpreter,self.narrator,self.secondary_narrator,self.pdf_analyst):
            if model is None:continue
            if model.max_tokens>=self.context_tokens:raise ValueError('Лимит ответа должен оставлять место входному контексту')
            if model.max_token_parameter not in model.parameters:raise ValueError('Подтвердите поддерживаемый параметр лимита токенов')
            if model.structured!='none' and 'response_format' not in model.parameters:raise ValueError('Модель не заявляет response_format')
        return self

class WindowsKeyVault:
    def __init__(self,path):self.path=Path(path)
    def file(self,profile):return self.path/(str(uuid.UUID(profile))+'.dpapi')
    def exists(self,profile):return self.file(profile).is_file()
    @staticmethod
    def crypt(data,protect):
        if os.name!='nt':raise RuntimeError('Windows DPAPI unavailable; no plaintext secret fallback')
        class Blob(ctypes.Structure):_fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
        buffer=ctypes.create_string_buffer(data)
        incoming=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));outgoing=Blob()
        api=ctypes.WinDLL('crypt32',use_last_error=True)
        fn=api.CryptProtectData if protect else api.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(Blob)]
        fn.restype=ctypes.c_int
        if not fn(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(outgoing)):
            raise RuntimeError('Windows could not protect/unprotect the API key')
        try:return ctypes.string_at(outgoing.data,outgoing.size)
        finally:
            free=ctypes.WinDLL('kernel32').LocalFree;free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
            free(ctypes.cast(outgoing.data,ctypes.c_void_p))
    def set(self,profile,key):
        if not isinstance(key,str) or not 1<=len(key)<=4096 or any(c in key for c in '\r\n'):raise ValueError('Некорректный API-ключ')
        ciphertext=self.crypt(key.encode(),True)
        self.path.mkdir(parents=True,exist_ok=True)
        dest=self.file(profile);tmp=dest.with_suffix('.tmp');tmp.write_bytes(ciphertext);tmp.replace(dest)
    def get(self,profile):
        p=self.file(profile)
        return self.crypt(p.read_bytes(),False).decode() if p.is_file() else None
    def delete(self,profile):
        p=self.file(profile)
        if p.exists():p.unlink()

class CloudSettings:
    def __init__(self,path,vault=None):
        self.path=Path(path);self.vault=vault or WindowsKeyVault(self.path.parent/'cloud-secrets')
        self.active=None;self.profiles={};self.catalog={};self.quota={}
        if self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf8'))
            self.profiles={p['id']:CloudProfile.model_validate(p) for p in data.get('profiles',[])}
            self.active=data.get('active');self.catalog=data.get('catalog',{})
            self.quota=data.get('quota',{})
    def persist(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(active=self.active,profiles=[p.model_dump(mode='json') for p in self.profiles.values()],catalog=self.catalog,quota=self.quota),ensure_ascii=False,indent=2),encoding='utf8')
        tmp.replace(self.path)
    def profile(self,profile=None):
        key=profile or self.active
        if key not in self.profiles:raise ValueError('Облачный провайдер пока не настроен')
        return self.profiles[key]
    def public(self):
        return dict(active=self.active,profiles=[dict(**p.model_dump(mode='json'),key_present=self.vault.exists(p.id)) for p in self.profiles.values()])
    def save(self,profile):
        self.profiles[profile.id]=profile
        if self.active is None:self.active=profile.id
        self.persist();return profile.id
    def activate(self,profile):self.profile(profile);self.active=profile;self.persist()
    def delete(self,profile):
        self.profile(profile);del self.profiles[profile];self.catalog.pop(profile,None);self.vault.delete(profile)
        if self.active==profile:self.active=None
        self.persist()
