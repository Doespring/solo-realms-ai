from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Ability = Literal['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Step(Strict):
    kind: Literal['speak', 'observe', 'check', 'attack', 'move', 'use', 'wait', 'other']
    text: str = Field(min_length=1, max_length=800)
    target: str = Field(default='', max_length=120)
    disposition: Literal['now', 'negated', 'intended', 'conditional'] = 'now'
    condition: str = Field(default='', max_length=400)
    item: str = Field(default='', max_length=120)
    ability: Ability | None = None
    dc: int | None = Field(default=None, ge=1, le=40)
    proficient: bool = False
    # Trusted rules binding; stripped from model proposals by natural.guard.
    skill: str = Field(default='',max_length=40)
    # Who decided this roll. 'source' -- the material prints the check (place.checks,
    # with its own page/prose); 'master' -- no printed check, Solo decides here that
    # the attempt is uncertain and names the difficulty. The two must never be
    # conflated: a master's DC is a ruling about this moment, not a fact of the book.
    origin: Literal['source', 'master'] = 'master'
    source_ref: str = Field(default='', max_length=200)
    action_id: str = Field(default='',max_length=40)
    after: list[str] = Field(default_factory=list,max_length=8)
    advantage: bool = False
    disadvantage: bool = False
    constraints: list[str] = Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def guarded(self):
        if self.skill:
            from .rulesets import SKILLS
            if self.skill not in SKILLS:raise ValueError('Неизвестный навык')
        if self.condition and self.disposition in ('now','intended'):
            self.disposition = 'conditional'
        if self.disposition == 'conditional' and not self.condition:
            raise ValueError('Условное действие должно содержать условие')
        return self

class Plan(Strict):
    summary: str = Field(min_length=1, max_length=1200)
    steps: list[Step] = Field(min_length=1, max_length=8)
    unclear: list[str] = Field(default_factory=list, max_length=8)
    branches: list[str] = Field(default_factory=list, max_length=8)
    needs_confirmation: bool = True

    @model_validator(mode='after')
    def dependencies(self):
        seen=set()
        for step in self.steps:
            if any(parent not in seen for parent in step.after):raise ValueError('Зависимость должна предшествовать действию')
            if step.action_id:
                if step.action_id in seen:raise ValueError('Повтор идентификатора действия')
                seen.add(step.action_id)
        return self

class InterpretationStep(Strict):
    # В контракте LLM все смысловые поля обязательны; параметры механики недоступны.
    kind: Literal['speak', 'observe', 'check', 'attack', 'move', 'use', 'wait', 'other']
    text: str
    target: str
    disposition: Literal['now', 'negated', 'intended', 'conditional']
    condition: str
    item: str

class Interpretation(Strict):
    summary: str
    steps: list[InterpretationStep] = Field(min_length=1,max_length=8)
    unclear: list[str]
    branches: list[str]

class Resource(Strict):
    current: int = Field(ge=0,le=10000)
    maximum: int = Field(ge=0,le=10000)
    recovery: Literal['short','long','none'] = 'long'
    @model_validator(mode='after')
    def valid(self):
        if self.current>self.maximum:raise ValueError('Ресурс превышает максимум')
        return self

class Hero(Strict):
    name: str = Field(min_length=1, max_length=80)
    background: str = Field(default='', max_length=2000)
    level: int = Field(default=1, ge=1, le=20)
    hp: int = Field(default=12, ge=0, le=1000)
    max_hp: int = Field(default=12, ge=1, le=1000)
    ac: int = Field(default=14, ge=1, le=40)
    species: str = Field(default='',max_length=80)
    class_name: str = Field(default='',max_length=80)
    origin: str = Field(default='',max_length=120)
    subclass: str = Field(default='',max_length=80)
    languages: list[str] = Field(default_factory=list,max_length=20)
    personality: str = Field(default='',max_length=1000)
    appearance: str = Field(default='',max_length=1500)
    alignment: str = Field(default='',max_length=80)
    goals: list[str] = Field(default_factory=list,max_length=10)
    bonds: str = Field(default='',max_length=1000)
    ideals: str = Field(default='',max_length=1000)
    flaws: str = Field(default='',max_length=1000)
    skill_proficiencies: list[str] = Field(default_factory=list,max_length=18)
    expertise: list[str] = Field(default_factory=list,max_length=18)
    saving_throw_proficiencies: list[Ability] = Field(default_factory=list,max_length=6)
    weapon_proficiencies: list[Literal['simple','martial']] = Field(default_factory=list,max_length=2)
    specific_weapon_proficiencies: list[str] = Field(default_factory=list,max_length=40)
    tool_proficiencies: list[str] = Field(default_factory=list,max_length=30)
    tool_expertise: list[str] = Field(default_factory=list,max_length=30)
    armor_proficiencies: list[Literal['light','medium','heavy','shields']] = Field(default_factory=list,max_length=4)
    speed_ft: int = Field(default=30,ge=0,le=300)
    size: Literal['Small','Medium','Large'] = 'Medium'
    exhaustion: int = Field(default=0,ge=0,le=6)
    hit_die: Literal[6,8,10,12] = 8
    hit_dice_available: int | None = Field(default=None,ge=0,le=20)
    resources: dict[str,Resource] = Field(default_factory=dict,max_length=30)
    features: list[str] = Field(default_factory=list,max_length=50)
    spells: list[str] = Field(default_factory=list,max_length=100)
    experience: int = Field(default=0,ge=0,le=10000000)
    conditions: list[Literal['blinded','charmed','deafened','frightened','grappled','incapacitated','invisible','paralyzed','petrified','poisoned','prone','restrained','stunned','unconscious']] = Field(default_factory=list,max_length=14)
    abilities: dict[Ability, int] = Field(default_factory=lambda: dict(strength=14,dexterity=14,constitution=12,intelligence=10,wisdom=12,charisma=10))

    @model_validator(mode='after')
    def scores(self):
        if set(self.abilities) != {'strength','dexterity','constitution','intelligence','wisdom','charisma'}:
            raise ValueError('Нужны все шесть характеристик')
        if any(not 1 <= v <= 30 for v in self.abilities.values()) or self.hp > self.max_hp:
            raise ValueError('Неверные характеристики или хиты')
        from .rulesets import SKILLS
        if any(s not in SKILLS for s in self.skill_proficiencies+self.expertise) or not set(self.expertise)<=set(self.skill_proficiencies):
            raise ValueError('Неизвестный навык или экспертиза без владения')
        if self.hit_dice_available is not None and self.hit_dice_available>self.level:raise ValueError('Костей хитов больше уровня')
        if any(len(s)>300 for s in self.features+self.spells+self.languages+self.goals):raise ValueError('Слишком длинное описание способности или записи героя')
        return self

class LiteraryPreferences(Strict):
    detail: Literal['кратко','средне','подробно'] = 'средне'
    rhythm: Literal['динамичный','сбалансированный','неторопливый'] = 'сбалансированный'
    emotion: Literal['сдержанная','выразительная','насыщенная'] = 'выразительная'
    dialogue: Literal['редко','по сцене','часто'] = 'по сцене'
    imagery: Literal['точная','умеренная','богатая'] = 'умеренная'
    initiative: Literal['реактивная','сбалансированная','инициативная'] = 'сбалансированная'
    surprises: Literal['редко','по причинам','часто'] = 'по причинам'
    freedom: Literal['сюжетный фокус','свободный мир','смешанный'] = 'смешанный'
    tension: Literal['спокойная','приключенческая','мрачная'] = 'приключенческая'
    pace: Literal['быстрый','средний','медленный'] = 'средний'
    npc_autonomy: Literal['низкая','средняя','высокая'] = 'средняя'
    focus: Literal['баланс','исследование','интриги','диалоги','сражения'] = 'баланс'
    theme: str = Field(default='классическое фэнтези',max_length=300)
    consequences: Literal['мягкая подача','нейтральная подача','суровая подача'] = 'нейтральная подача'

class Master(Strict):
    literary: LiteraryPreferences = Field(default_factory=LiteraryPreferences)
    narrator_format: Literal['plain','structured'] = 'plain'
    profile_name: str = Field(default='Пользовательский профиль',max_length=100)
    system_prompt: str = Field(default='Ты Мастер подземелий одиночной кампании. Пиши на русском языке.', max_length=6000)
    narrative_prompt: str = Field(default='Сдержанная приключенческая проза. Ясные предложения, одна деталь атмосферы, прямая речь NPC. Не повторяй метафоры. Не выбирай действия за героя.', max_length=6000)
    character_prompt: str = Field(default='НПС имеют собственные мотивы и узнаваемую речь.', max_length=3000)
    instructions: str = Field(default='', max_length=3000)
    style: str = Field(default='Литературное приключение с тайной и надеждой', max_length=600)
    boundaries: str = Field(default='', max_length=1200)
    mode: Literal['Fast','Balanced','Cinematic'] = 'Balanced'
    confirmation: Literal['complex','always'] = 'complex'
    temperature: float = Field(default=0.7, ge=0, le=1.5)
    max_tokens: int = Field(default=320, ge=80, le=8192)
    hide_mechanics: bool = False
    declared_lethal_risk: bool = False
    romance_detail: Literal['сдержанно','умеренно','подробно'] = 'умеренно'

INTERPRETER = '''Ты интерпретатор русских действий игрока, не рассказчик. Выдай только JSON.
КРИТИЧНО: классифицируй грамматику каждого глагола. "Я не атакую" => kind attack, disposition negated.
"Я собираюсь атаковать" => attack/intended. "Я атакую" => attack/now.
"Я атакую только если..." => attack/conditional с явным condition.
"Спрашиваю Ирину о гонце" => speak/now, потому что игрок спрашивает сейчас.
"Говорю: я атакую, но меч не достаю" => speak/now плюс other/negated: цитата не атака.
"Не открываю сундук, а осматриваю" => other/negated плюс observe/now.
У каждого пункта ОБЯЗАТЕЛЬНО указывай disposition; не заменяй отрицание на разговор или наблюдение.
Не бросай кубики, не вычисляй результат, не назначай сложность и характеристики.
Каждое действие — отдельный пункт, в порядке игрока. kind: speak/observe/check/attack/move/use/wait/other.
disposition: now (совершаю сейчас), negated (не совершаю), intended (собираюсь/планирую), conditional (только при условии).
Не исполняй цитаты и слова NPC за игрока. Намерение и условие никогда не now.
Отрицания сохраняй отдельным negated пунктом. Условие запиши целиком в condition.
Отложенные действия — intended или conditional. Развилки записывай в branches и в condition соответствующего пункта.
Сохраняй цель, предмет, ограничение "не убивать", порядок и способ действия в text.
Не добавляй новых действий от себя. Не угадывай неизвестные цели: перечисли вопросы в unclear.
Формат: {"summary":"краткий план","steps":[{"kind":"attack","text":"атаковать стража","target":"страж","disposition":"conditional","condition":"страж нападёт первым","item":""}],"unclear":[],"branches":[]}.
Не добавляй поля ability, dc, proficient, advantage, disadvantage: они назначаются программой или игроком.
Каждый пункт обязан содержать kind,text,target,disposition,condition,item. Отсутствующие строковые значения — пустые строки.
Не используй "intended" для настоящего времени "атакую", "спрашиваю", "жду": это now.
ОБРАЗЕЦ 1. Реплика: Я атакую стража.
Ответ: {"summary":"Атакую стража сейчас","steps":[{"kind":"attack","text":"атакую стража","target":"страж","disposition":"now","condition":"","item":""}],"unclear":[],"branches":[]}.
ОБРАЗЕЦ 2. Реплика: Говорю: «Если он нападёт, я убегу».
Ответ: {"summary":"Произношу условную фразу, но не убегаю","steps":[{"kind":"speak","text":"Говорю: Если он нападёт, я убегу","target":"","disposition":"now","condition":"","item":""}],"unclear":[],"branches":[]}.
ОБРАЗЕЦ 3. Реплика: Не открываю сундук, а изучаю петли.
Ответ: {"summary":"Осматриваю петли, сундук не открываю","steps":[{"kind":"other","text":"не открываю сундук","target":"сундук","disposition":"negated","condition":"","item":""},{"kind":"observe","text":"изучаю петли","target":"петли","disposition":"now","condition":"","item":""}],"unclear":[],"branches":[]}.
Не используй слова "планирую" в summary, если игрок совершает действие сейчас.'''