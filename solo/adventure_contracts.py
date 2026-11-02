"""Reviewed declarative adventure data. Models propose data, never executable code."""
from typing import Literal
import re
from pydantic import Field, model_validator
from .contracts import Strict
from .progression import Progression
from .world import ScenarioPackage

Id = str

class Provenance(Strict):
    document: str = Field(min_length=1, max_length=80)
    page: int | None = Field(default=None, ge=1, le=2000)
    printed_page: str = Field(default='', max_length=30)
    section: str = Field(default='', max_length=180)
    bbox: list[float] = Field(default_factory=list, max_length=4)
    version: str = Field(default='1', max_length=80)
    layer: Literal['original','translation','translator_note','errata','house_rule'] = 'original'
    review: Literal['extracted','requires_review','confirmed','unsupported'] = 'requires_review'

class Predicate(Strict):
    path: str = Field(max_length=160)
    op: Literal['eq','ne','gte','lte','contains','exists'] = 'eq'
    value: str | int | float | bool | None = None
    @model_validator(mode='after')
    def safe(self):
        if not re.fullmatch(r'(location|elapsed_minutes|combat\.(round|active)|hero\.(hp|level|exhaustion)|adventure\.(flags|trackers|events|knowledge)\.[A-Za-z0-9_-]{1,80}|world\.visited)', self.path):
            raise ValueError('Неподдержанный путь условия')
        return self

class Effect(Strict):
    # 'reward' books a printed reward through the host ledger, once, with the
    # page it came from. A reward the rules cannot price stays pending instead
    # of being applied as zero.
    kind: Literal['notice','flag','tracker','handout','evidence','weather','schedule','table','check','location','resource','companion','reward']
    target: str = Field(default='', max_length=120)
    text: str = Field(default='', max_length=1000)
    value: str | int | bool | None = None
    dc: int | None = Field(default=None, ge=1, le=40)
    ability: Literal['strength','dexterity','constitution','intelligence','wisdom','charisma'] | None = None
    public: bool = False
    truth: Literal['confirmed','rumor','false','unknown']='confirmed'

class SourceRange(Strict):
    document: str=Field(min_length=1,max_length=80)
    first_page: int=Field(ge=1,le=2000)
    last_page: int=Field(ge=1,le=2000)
    @model_validator(mode='after')
    def valid(self):
        if self.last_page<self.first_page:raise ValueError('Неверные границы приключения')
        return self

class TableRow(Strict):
    low: int = Field(ge=-100, le=1000)
    high: int = Field(ge=-100, le=1000)
    label: str = Field(max_length=160)
    effects: list[Effect] = Field(default_factory=list, max_length=12)

class RollTable(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    formula: str = Field(pattern=r'^[1-9][0-9]?d(4|6|8|10|12|20|100)([+-][0-9]{1,2})?$')
    rows: list[TableRow] = Field(min_length=1, max_length=100)
    source: Provenance
    @model_validator(mode='after')
    def valid(self):
        match=re.fullmatch(r'(\d+)d(\d+)([+-]\d+)?', self.formula)
        count,sides,bonus=int(match[1]),int(match[2]),int(match[3] or 0)
        if count>20:raise ValueError('Слишком много костей')
        covered=[]
        for row in self.rows:
            if row.low>row.high:raise ValueError('Неверный диапазон таблицы')
            covered.extend(range(row.low,row.high+1))
        if len(covered)!=len(set(covered)) or set(covered)!=set(range(count+bonus,count*sides+bonus+1)):
            raise ValueError('Таблица должна покрывать каждый результат ровно один раз')
        return self

class ScenarioEvent(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    category: Literal['mandatory','conditional','random','timed','repeating','optional','reactive','consequence','regional','world']
    instruction: Literal['rule','procedure','recommendation','alternative','read_aloud','gm_note'] = 'rule'
    conditions: list[Predicate] = Field(default_factory=list,max_length=12)
    prerequisites: list[str] = Field(default_factory=list,max_length=20)
    after_minutes: float = Field(default=0,ge=0,le=10000000,allow_inf_nan=False)
    repeat_minutes: float | None = Field(default=None,ge=.1,le=100000,allow_inf_nan=False)
    action_kind: str = Field(default='',max_length=60)
    action_target: str = Field(default='',max_length=120)
    time_anchor: Literal['module','campaign']='module'
    effects: list[Effect] = Field(default_factory=list,max_length=20)
    source: Provenance
    @model_validator(mode='after')
    def valid(self):
        if self.category=='repeating' and self.repeat_minutes is None:raise ValueError('Повторяющемуся событию нужен интервал')
        if self.category in ('mandatory','conditional') and not (self.conditions or self.action_kind or self.prerequisites):
            raise ValueError('Обязательное событие требует проверяемого условия')
        return self

class Tracker(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    name: str = Field(max_length=120)
    value: int = Field(default=0,ge=-100000,le=100000)
    minimum: int = Field(default=0,ge=-100000,le=100000)
    maximum: int = Field(default=100,ge=-100000,le=100000)
    public: bool=False
    source: Provenance
    @model_validator(mode='after')
    def valid(self):
        if not self.minimum<=self.value<=self.maximum:raise ValueError('Значение вне границ трекера')
        return self

class SurvivalRule(Strict):
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    kind: Literal['cold','navigation','forage','pursuit','food','water']
    source: Provenance
    conditions: list[Predicate]=Field(default_factory=list,max_length=12)
    interval_minutes: float=Field(default=60,ge=1,le=100000,allow_inf_nan=False)
    ability: Literal['strength','dexterity','constitution','intelligence','wisdom','charisma']='wisdom'
    dc: int=Field(default=10,ge=1,le=40)
    saving_throw: bool=False
    failure_exhaustion: int=Field(default=0,ge=0,le=1)
    tracker: str=Field(default='',max_length=80)
    failure_delta: int=Field(default=0,ge=-100,le=100)
    immunity_flag: str=Field(default='',max_length=80)
    supply_resource: str=Field(default='',max_length=80)
    supply_per_person: int=Field(default=1,ge=0,le=100)
    action_minutes: int=Field(default=60,ge=1,le=480)
    gain_formula: str=Field(default='1d6',max_length=30)
    add_ability_modifier: bool=False
    gain_resource: str=Field(default='',max_length=80)

class Companion(Strict):
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    npc: str=Field(min_length=1,max_length=80)
    source: Provenance
    status: Literal['joined','left','missing','dead']='joined'
    goals: list[str]=Field(default_factory=list,max_length=8)
    departure_conditions: list[Predicate]=Field(default_factory=list,max_length=12)
    combat_stats_verified: bool=False
    combat_stats: dict | None=None
    weapon: str=Field(default='',max_length=80)
    @model_validator(mode='after')
    def verified(self):
        if self.combat_stats_verified:
            from .contracts import Hero
            from .combat import WEAPONS
            required={'name','level','hp','max_hp','ac','abilities','speed_ft','size'}
            if self.combat_stats is None or not required<=set(self.combat_stats) or self.weapon not in WEAPONS:raise ValueError('Боевые возможности спутника требуют полного проверенного профиля и зарегистрированного оружия')
            Hero.model_validate(self.combat_stats)
        return self

class WildernessProcedure(Strict):
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    source: Provenance
    enabled: bool=False
    encounter_table: str=Field(max_length=80)
    conditions: list[Predicate]=Field(default_factory=list,max_length=12)
    period_windows: list[list[int]]=Field(default_factory=list,max_length=4)
    scheduling_choice: Literal['window_midpoint']='window_midpoint'
    @model_validator(mode='after')
    def checked(self):
        if self.enabled and (len(self.period_windows)!=4 or any(len(w)!=2 or not 0<=w[0]<w[1]<=1440 for w in self.period_windows)):
            raise ValueError('Подтвердите четыре временных окна суток; время рассвета не угадывается')
        return self

class EntryStage(Strict):
    # The policy names the check, not the book. A prepared entry from any source
    # is verified the same way: the stage names its document and page, and the
    # opening text must still hash to what was reviewed.
    policy: str = Field(default='verified-entry-v1', min_length=1, max_length=40)
    document: str=Field(pattern=r'^[a-f0-9]{64}$')
    page: int=Field(ge=1,le=2000)
    opening_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    boundary: str=Field(min_length=1,max_length=1000)

class AdventureDefinition(Strict):
    version: Literal[1]=1
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str=Field(min_length=1,max_length=150)
    #: The edition whose numbers this scenario was prepared against. This is the
    #: mechanics preset, and it alone gates execution in the adventure engine.
    ruleset: str=Field(max_length=80)
    ruleset_version: int=Field(ge=1)
    #: The edition the source book was written for, when the book says so. It is a
    #: property of the text, not of the mechanics: a 2014 adventure runs on 2024
    #: numbers through the mapping layer, and the two values must not be merged.
    source_ruleset: str=Field(default='', max_length=80)
    source_ruleset_version: int=Field(default=0, ge=0)
    mode: Literal['strict','solo','free']='strict'
    world: ScenarioPackage | None=None
    events: list[ScenarioEvent]=Field(default_factory=list,max_length=500)
    tables: list[RollTable]=Field(default_factory=list,max_length=100)
    trackers: list[Tracker]=Field(default_factory=list,max_length=100)
    media: list[str]=Field(default_factory=list,max_length=500)
    unsupported: list[str]=Field(default_factory=list,max_length=100)
    reviewed: bool=False
    entry_stage: EntryStage | None=None
    source_ranges: list[SourceRange]=Field(default_factory=list,max_length=30)
    continuation_of: list[str]=Field(default_factory=list,max_length=20)
    start_conditions: list[Predicate]=Field(default_factory=list,max_length=30)
    finish_conditions: list[Predicate]=Field(default_factory=list,max_length=30)
    alternate_start: bool=False
    alternate_flags: dict[str,str | int | bool]=Field(default_factory=dict,max_length=50)
    minimum_level: int | None=Field(default=None,ge=1,le=20)
    maximum_level: int | None=Field(default=None,ge=1,le=20)
    recommended_party: int | None=Field(default=None,ge=1,le=12)
    survival: list['SurvivalRule']=Field(default_factory=list,max_length=30)
    companions: list['Companion']=Field(default_factory=list,max_length=30)
    wilderness: list['WildernessProcedure']=Field(default_factory=list,max_length=10)
    # The advancement model belongs to the scenario: the book may grant levels
    # at episode ends (milestone) instead of counting experience. Keeping it on
    # the definition is what stops a fight from deciding the hero's growth.
    progression: Progression=Field(default_factory=Progression)
    @model_validator(mode='after')
    def valid(self):
        for rows in (self.events,self.tables,self.trackers,self.survival,self.companions,self.wilderness):
            if len({r.id for r in rows})!=len(rows):raise ValueError('Повтор идентификатора')
        if self.minimum_level and self.maximum_level and self.minimum_level>self.maximum_level:raise ValueError('Неверные уровни приключения')
        by_id={e.id:e for e in self.events};visited=set();active=set()
        def visit(id):
            if id in active:raise ValueError('Цикл зависимостей событий')
            if id in visited:return
            if id not in by_id:raise ValueError('Неизвестная зависимость события')
            active.add(id)
            for parent in by_id[id].prerequisites:visit(parent)
            active.remove(id);visited.add(id)
        for event in self.events:visit(event.id)
        for event in self.events:
            for effect in event.effects:
                if effect.kind=='table' and effect.target not in {t.id for t in self.tables}:raise ValueError('Неизвестная таблица')
                if effect.kind=='schedule' and effect.target not in by_id:raise ValueError('Неизвестное планируемое событие')
                if effect.kind=='tracker' and effect.target not in {t.id for t in self.trackers}:raise ValueError('Неизвестный трекер')
                if effect.kind=='handout' and effect.target not in self.media:raise ValueError('Неизвестный материал')
        if len(self.model_dump_json())>2000000:raise ValueError('Определение приключения превышает 2 МБ')
        return self

AdventureDefinition.model_rebuild()
