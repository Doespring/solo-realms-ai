"""Cloud profiles without secrets, plus one-way Windows CurrentUser DPAPI key storage."""
import ctypes
import ipaddress
import json
import os
import socket
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

# --- Куда уходит ключ -------------------------------------------------------
# Bearer-токен уезжает на base_url+'/chat/completions', '/models' и
# '/models/<model>/endpoints' (solo/cloud.py). Значит получателя ключа задают
# провайдер и сам адрес — схема, хост, порт, путь. Модель, тарифы, temperature
# меняют тело запроса на ТОТ ЖЕ сервер, а `endpoint` у RouterAI — имя маршрута
# в теле, а не новый адрес.
KNOWN_PROVIDERS=('groq','routerai')

def resolve_host(host):
    """Все IP имени; отдельная функция, чтобы проверка адреса заменялась в тестах."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host,443,proto=socket.IPPROTO_TCP)})

def _require_public(text,host):
    try: address=ipaddress.ip_address(text)
    except ValueError as exc: raise ValueError(f'Адрес {host} вернул не IP: {text}') from exc
    if not address.is_global:
        raise ValueError(f'Адрес {host} ({address}) ведёт в приватную или служебную сеть: ключ на такой адрес не отправляется')

def verify_public_host(host,resolve=None):
    """Пропустить только публичный адрес; решает резолв, а не написание имени.

    `internal.example` выглядит безобидно, но резолвится в приватную сеть —
    поэтому проверяется каждый полученный адрес, а не строка URL.
    """
    host=(host or '').strip().strip('[]')
    if not host: raise ValueError('В адресе нет имени сервера')
    try: address=ipaddress.ip_address(host)
    except ValueError: pass
    else:
        _require_public(str(address),host);return [str(address)]
    try: found=list((resolve or resolve_host)(host))
    except OSError as exc: raise ValueError(f'Адрес {host} не разрешается в IP: проверьте написание и доступность DNS') from exc
    if not found: raise ValueError(f'Адрес {host} не разрешается в IP: проверьте написание и доступность DNS')
    for text in found: _require_public(text,host)
    return found

def verify_public_address(url,resolve=None):
    """Та же проверка для полного Base URL (имя берётся из него)."""
    host=urlsplit(url).hostname
    if not host: raise ValueError('В адресе нет имени сервера')
    return verify_public_host(host,resolve)

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
    # A picture is read by its own model and paid for by its own image tariff,
    # so a cheaper text analyst never takes vision away. None means the old
    # behaviour: send an image and the request refuses instead of guessing.
    vision: ModelConfig | None=None
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
        try: ipaddress.ip_address(p.hostname.strip('[]'))
        except ValueError: pass
        else: verify_public_host(p.hostname)  # литерал IP проверяется без сети
        self.base_url=self.base_url.rstrip('/')
        if self.provider.lower()=='routerai' and self.base_url!='https://routerai.ru/api/v1':raise ValueError('RouterAI использует https://routerai.ru/api/v1')
        if self.narrator_selection=='secondary' and self.secondary_narrator is None:raise ValueError('Дополнительный рассказчик не настроен')
        for model in (self.interpreter,self.narrator,self.secondary_narrator,self.pdf_analyst,self.vision):
            if model is None:continue
            if model.max_tokens>=self.context_tokens:raise ValueError('Лимит ответа должен оставлять место входному контексту')
            if model.max_token_parameter not in model.parameters:raise ValueError('Подтвердите поддерживаемый параметр лимита токенов')
            if model.structured!='none' and 'response_format' not in model.parameters:raise ValueError('Модель не заявляет response_format')
        return self
    def delivery_target(self):
        """Кто именно получает bearer-токен: провайдер, схема, хост, порт, путь.

        Совпал target — ключ уходит туда же, куда и раньше, и профиль можно
        сохранять на лету. Изменился (другой провайдер или адрес) — это новый
        получатель ключа: профиль выключается, ключ отвязывается.
        """
        p=urlsplit(self.base_url)
        return (self.provider.strip().lower(),p.scheme,(p.hostname or '').lower(),p.port or 443,p.path.rstrip('/'))

class KeyVaultError(RuntimeError):
    """Хранилище ключей недоступно или сохранённый ключ нельзя прочитать."""

class KeyVaultUnavailable(KeyVaultError):
    """Ни одно хранилище не подошло: ключ не сохранён и в открытом виде не пишется."""

def _check_key(key):
    if not isinstance(key,str) or not 1<=len(key)<=4096 or any(c in key for c in (chr(13),chr(10))):raise ValueError('Некорректный API-ключ')

def _profile_id(profile):return str(uuid.UUID(profile))

def _write_private(path,data):
    """Записать файл с правами 0600 атомарно: временный файл, fsync, replace."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_name(path.name+'.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True);raise
    os.replace(tmp,path);os.chmod(path,0o600)

def _inside_git(path):
    for parent in [path.parent,*path.parent.parents]:
        if (parent/'.git').exists():return True
    return False

class KeyVault:
    """Общий интерфейс хранилища ключей: exists/set/get/delete, как у WindowsKeyVault."""
    backend='base'
    def file(self,profile):raise NotImplementedError
    def exists(self,profile):raise NotImplementedError
    def set(self,profile,key):raise NotImplementedError
    def get(self,profile):raise NotImplementedError
    def delete(self,profile):raise NotImplementedError

class WindowsKeyVault(KeyVault):
    """Ключи под Windows CurrentUser DPAPI; на других ОС честный отказ, не открытый текст."""
    backend='dpapi'
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

class KeyringVault(KeyVault):
    """Ключи в системном хранилище секретов: SecretService/KWallet на Linux, Credential Manager на Windows.

    Библиотека keyring необязательна (requirements-keyring.txt). Без неё или без
    живого бэкенда выбор уходит на FileKeyVault: тихого сохранения в открытом виде нет.
    """
    backend='keyring'
    service='solo-realms-cloud'
    def __init__(self,service=None):self.service=service or self.service
    @staticmethod
    def _module():
        import importlib
        return importlib.import_module('keyring')
    @classmethod
    def available(cls):
        """Живой бэкенд проверяется чтением: keyring без SecretService даёт fail.Keyring и NoKeyringError.

        Бэкенды, которые пишут секрет открытым файлом (keyrings.alt.PlaintextKeyring),
        не считаются живым хранилищем: иначе обещание «открытого текста нет» ломается.
        """
        try:
            module=cls._module();backend=module.get_keyring()
            if cls.plaintext(backend):return False
            module.get_password(cls.service,'availability-probe');return True
        except Exception:return False
    @staticmethod
    def plaintext(backend):
        kind=type(backend)
        return 'plaintext' in kind.__name__.lower() or kind.__module__.lower()=='keyrings.alt.file'
    def file(self,profile):raise NotImplementedError('keyring не пишет ключи в файлы профиля')
    def exists(self,profile):
        try:return self.get(profile) is not None
        except KeyVaultError:return False
    def get(self,profile):
        try:return self._module().get_password(self.service,_profile_id(profile))
        except KeyVaultError:raise
        except Exception as exc:raise KeyVaultUnavailable(f'Системное хранилище ключей недоступно: {exc}') from exc
    def set(self,profile,key):
        _check_key(key);module=self._module()
        if self.plaintext(module.get_keyring()):raise KeyVaultUnavailable('Системное хранилище ключей пишет секрет открытым текстом (PlaintextKeyring): ключ не сохранён.')
        try:
            module.set_password(self.service,_profile_id(profile),key)
            stored=module.get_password(self.service,_profile_id(profile))
        except Exception as exc:raise KeyVaultUnavailable(f'Системное хранилище ключей недоступно: {exc}') from exc
        if stored!=key:raise KeyVaultUnavailable('Системное хранилище ключей недоступно: бэкенд не подтвердил сохранение ключа чтением')
    def delete(self,profile):
        try:self._module().delete_password(self.service,_profile_id(profile))
        except Exception:return  # ключа нет или бэкенд недоступен — удалять нечего

class FileKeyVault(KeyVault):
    """Шифрованный файл: Fernet-шифротекст в каталоге профиля, ключ шифрования — отдельный файл 0600 вне него.

    Защита от случайного чтения и от переноса папки профиля на другую машину:
    без файла ключа шифротекст не раскрыть. От root, от копии домашнего каталога
    целиком и от вредоносного процесса того же пользователя это НЕ защищает —
    файл ключа лежит рядом, просто в приватном каталоге пользователя.
    """
    backend='file'
    def __init__(self,path,key_path=None):
        self.path=Path(path);self.key_path=Path(key_path) if key_path else default_key_file()
        if self.key_path.parent==self.path or self.path in self.key_path.parents:
            raise ValueError('Ключ шифрования нельзя хранить в каталоге с зашифрованными ключами профиля')
        if _inside_git(self.key_path):
            raise ValueError(f'Ключ шифрования нельзя хранить внутри git-репозитория: {self.key_path.parent}')
    def file(self,profile):return self.path/(_profile_id(profile)+'.enc')
    def _fernet(self):
        from cryptography.fernet import Fernet
        if not self.key_path.is_file():_write_private(self.key_path,Fernet.generate_key())
        return Fernet(self.key_path.read_bytes().strip())
    def exists(self,profile):return self.file(profile).is_file()
    def set(self,profile,key):
        _check_key(key);_write_private(self.file(profile),self._fernet().encrypt(key.encode()))
    def get(self,profile):
        from cryptography.fernet import InvalidToken
        target=self.file(profile)
        if not target.is_file():return None
        try:return self._fernet().decrypt(target.read_bytes()).decode()
        except InvalidToken as exc:raise KeyVaultError('Сохранённый ключ не расшифровывается: файл ключа шифрования не тот или повреждён') from exc
    def delete(self,profile):
        target=self.file(profile)
        if target.exists():target.unlink()
    @classmethod
    def available(cls,path,key_path=None):
        """Проверка делом: каталог ключа и каталог профиля должны принимать файл с правами 0600."""
        try:
            from cryptography.fernet import Fernet  # noqa: F401 — без него шифровать нечем
            vault=cls(path,key_path);vault.key_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            probe=vault.key_path.with_name('.probe-'+uuid.uuid4().hex)
            _write_private(probe,b'probe');probe.unlink()
            vault.path.mkdir(parents=True,exist_ok=True,mode=0o700)
            return True
        except Exception:return False

class UnavailableVault(KeyVault):
    """Заглушка, когда не подошло ни одно хранилище: понятный отказ вместо открытого текста."""
    backend='unavailable'
    def __init__(self,reason):self.reason=reason
    def message(self):
        return f'Хранилище ключей API недоступно: {self.reason} Ключ не сохранён и не будет записан в открытом виде.'
    def file(self,profile):raise KeyVaultUnavailable(self.message())
    def exists(self,profile):return False
    def set(self,profile,key):raise KeyVaultUnavailable(self.message())
    def get(self,profile):return None
    def delete(self,profile):return None

VAULT_ENV='SOLO_REALMS_KEY_VAULT'
VAULT_BACKENDS=('auto','dpapi','keyring','file')

def default_key_file():
    """Файл ключа шифрования вне профиля и вне git: приватный каталог данных пользователя."""
    if os.name=='nt':base=Path(os.environ.get('APPDATA') or Path.home()/'AppData'/'Roaming')
    else:base=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')
    return base/'solo-realms'/'keyvault.key'

def select_vault(secrets_dir,backend=None,key_path=None):
    """Выбрать хранилище явно: dpapi на Windows, иначе keyring, при недоступности — file.

    Порядок можно зафиксировать аргументом backend или переменной окружения
    SOLO_REALMS_KEY_VAULT (auto|dpapi|keyring|file). Если не подошло ничего —
    UnavailableVault: сохранение ключа отказывает по-русски, приложение работает.
    """
    choice=(backend or os.environ.get(VAULT_ENV) or 'auto').strip().lower()
    if choice not in VAULT_BACKENDS:
        raise ValueError(f'Неизвестное хранилище ключей «{choice}». Допустимо: '+', '.join(VAULT_BACKENDS)+f' (переменная {VAULT_ENV}).')
    if choice=='dpapi' or (choice=='auto' and os.name=='nt'):return WindowsKeyVault(secrets_dir)
    problems=[];order=('keyring','file') if choice=='auto' else (choice,)
    for name in order:
        if name=='keyring':
            if KeyringVault.available():return KeyringVault()
            problems.append('системное хранилище секретов недоступно (модуль keyring не установлен или нет SecretService/KWallet)')
            continue
        try:vault=FileKeyVault(secrets_dir,key_path)
        except ValueError as exc:problems.append(str(exc));continue
        if FileKeyVault.available(secrets_dir,key_path):return vault
        problems.append(f'нет прав на запись в {vault.key_path.parent} или {vault.path}')
    return UnavailableVault('; '.join(problems)+'.')

class CloudSettings:
    def __init__(self,path,vault=None):
        self.path=Path(path);self.vault=vault or select_vault(self.path.parent/'cloud-secrets')
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
        return dict(active=self.active,vault_backend=getattr(self.vault,'backend','custom'),profiles=[dict(**p.model_dump(mode='json'),key_present=self.vault.exists(p.id)) for p in self.profiles.values()])
    def save(self,profile):
        self.profiles[profile.id]=profile
        if self.active is None:self.active=profile.id
        self.persist();return profile.id
    def activate(self,profile):self.profile(profile);self.active=profile;self.persist()
    def delete(self,profile):
        self.profile(profile);del self.profiles[profile];self.catalog.pop(profile,None);self.vault.delete(profile)
        if self.active==profile:self.active=None
        self.persist()
