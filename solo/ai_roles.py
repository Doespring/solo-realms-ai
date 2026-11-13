"""Role selection is explicit; it never reacts to refusal or silently changes models."""
import sys
from pathlib import Path
from .cloud_settings import CloudProfile,ModelConfig

QWEN='qwen/qwen3.5-35b-a3b'
DEEPSEEK='deepseek/deepseek-v4.1-flash'
VENICE='cognitivecomputations/dolphin-mistral-24b-venice-edition'
GEMINI='google/gemini-3.8-flash'

def prompt(name):
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))/'prompts'
    text=(base/(name+'.txt')).read_text(encoding='utf8')
    if name in ('deepseek_narrator_v1','venice_narrator_v1'):text=(base/'gm_narrative_contract_v1.txt').read_text(encoding='utf8')+'\n'+text
    return text

def routerai_preset():
    def role(model,temperature,tokens,json=False,thinking='provider_default'):
        # Роль, которой нужен JSON, просит его схемой: RouterAI заявляет
        # structured_outputs у всех моделей этого профиля.
        return ModelConfig(model=model,temperature=temperature,max_tokens=tokens,parameters=['max_tokens','temperature','response_format'],structured='json_schema' if json else 'none',thinking=thinking)
    # Prices are intentionally unknown until an explicit catalogue/endpoint refresh.
    return CloudProfile(name='RouterAI — Adventure Intelligence',provider='RouterAI',base_url='https://routerai.ru/api/v1',
        interpreter=role(QWEN,.2,1800,True,'off'),narrator=role(DEEPSEEK,.65,600,thinking='off'),
        secondary_narrator=role(VENICE,.7,600),pdf_analyst=role(GEMINI,.2,1800,True,'low'),
        vision=role(GEMINI,.0,1024,json=False,thinking='low'),
        interpreter_contract='qwen_interpreter_v1',stream_usage=True,context_tokens=65536,monthly_budget=0,preparation_budget=0)

def narrator_reason(profile):
    """Why this narrator was chosen, in the player's words.

    The choice is the profile's explicit setting, never an automatic switch after
    a refusal or an error: a model that failed must stay visible as failed rather
    than be silently replaced by a cheaper one.
    """
    # A model can be present with an empty name; both mean "not configured", and
    # saying "chosen" there contradicted the role's own problems list.
    def ready(cfg):return cfg is not None and bool(str(getattr(cfg,'model','') or '').strip())
    if profile.narrator_selection=='secondary':
        if not ready(profile.secondary_narrator):
            return 'Рассказчик недоступен: в профиле выбран запасной, но модель для него не задана.'
        return 'Рассказчик выбран по настройке профиля «запасной»: основного сменили вручную.'
    if not ready(profile.narrator):
        return 'Рассказчик недоступен: в профиле не задана модель основного рассказчика.'
    return 'Рассказчик выбран по настройке профиля «основной»: это ваш явный выбор, не автоподбор.'


IMAGE_REQUIRED='Для изображений нужен Gemini PDF с отдельно подтверждённым тарифом изображений за токен'

def vision_config(profile):
    """The model that actually looks at a picture, with its own image tariff.

    Images never fall back to a text model and never borrow the analyst's
    tariff: an unset role or an unconfirmed image price is a refusal in the
    same words the product used before the vision role existed.
    """
    cfg=getattr(profile,'vision',None)
    if cfg is None or not str(getattr(cfg,'model','') or '').strip() or cfg.image_per_million is None:raise ValueError(IMAGE_REQUIRED)
    return cfg

def select_role(profile,mode):
    if mode=='gm':
        cfg=profile.secondary_narrator if profile.narrator_selection=='secondary' else profile.narrator
        if cfg is None or not str(getattr(cfg,'model','') or '').strip():
            raise ValueError(narrator_reason(profile)+' Пока рассказчик не выбран, сцену описывает движок.')
        return cfg,None
    if mode=='character_creation':return profile.interpreter,'qwen_character_create_v1'
    if mode=='preparation_world':
        # A book is read, not role-played. When the profile names a PDF-capable
        # analyst and preparation is set to use it, the world is structured by
        # that model; otherwise the interpreter keeps the old behaviour. The
        # prompt is the same contract either way, so only the reading model
        # changes and a profile without an analyst still prepares as before.
        # Choosing the analyst is itself the decision. The separate PDF toggle
        # governs repair of unreadable pages, and letting it also cancel the
        # chosen reader made an explicit choice silently do nothing.
        if profile.preparation_role=='analyst' and profile.pdf_analyst is not None:
            return profile.pdf_analyst,'qwen_campaign_prepare_v1'
        return profile.interpreter,'qwen_campaign_prepare_v1'
    if mode=='vision':return vision_config(profile),None
    if mode=='preparation_qwen':return profile.interpreter,'qwen_adventure_analyst_v1'
    if mode=='interpreter':return profile.interpreter,'qwen_interpreter_v1' if profile.interpreter_contract=='qwen_interpreter_v1' else None
    if mode=='pdf_analyst':
        # The toggle is the owner's consent to review pages separately, not a
        # demand for a second model: a profile may keep the reading to the
        # vision role alone, and the picture is priced by that role anyway.
        if not profile.pdf_analysis_enabled:raise ValueError('Отдельный разбор проблемных страниц выключен: включите согласие в настройках ИИ')
        if profile.pdf_analyst is not None:return profile.pdf_analyst,'gemini_pdf_analyst_v1'
        return vision_config(profile),'gemini_pdf_analyst_v1'
    if profile.narrator_selection=='secondary':
        if profile.secondary_narrator is None:raise ValueError('Дополнительный рассказчик не настроен')
        return profile.secondary_narrator,'venice_narrator_v1'
    return profile.narrator,'deepseek_narrator_v1' if profile.interpreter_contract=='qwen_interpreter_v1' else None

def reasoning_parameters(profile,config):
    if config.model.startswith('google/gemini-3') and config.thinking=='off':
        raise ValueError('Gemini 3 не поддерживает полное thinking OFF. Выберите LOW и достаточный лимит общего ответа; настройки и расходы автоматически не меняются.')
    if config.thinking=='provider_default':return {},'provider_default'
    if config.reasoning_control=='routerai_enabled' and profile.provider.lower()=='routerai' and 'reasoning' in config.parameters:
        # Only an explicitly confirmed capability may enable this wire format.
        return {'reasoning':{'enabled':False} if config.thinking=='off' else {'effort':config.thinking}},'explicit_capability'
    if config.reasoning_control=='effort' and 'reasoning_effort' in config.parameters:
        return {'reasoning_effort':'none' if config.thinking=='off' else config.thinking},'explicit_capability'
    return {},'requested_but_unverified'
