"""Reviewed campaign inputs, distinct from the searchable source catalogue.

Only trusted campaign creation loads registered definitions. Commands carry IDs,
never a build, DC, monster statistics, reward amount or executable expression.
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from dnd5e_engine import CharacterBuildSpec, GridScene
from pydantic import Field, model_validator, model_serializer

from .game_commands import ToolInput, GridPosition

ROOT = Path(__file__).resolve().parents[1] / 'integration/nat20/playable'


class CampaignSource(ToolInput):
    kind: Literal['authored_scenario', 'native_derived'] = 'authored_scenario'
    id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=800)


class SceneCheck(ToolInput):
    kind: Literal['skill', 'ability']
    skill: str | None
    ability: Literal['str', 'dex', 'con', 'int', 'wis', 'cha'] | None
    dc: int = Field(ge=1, le=30)
    source: CampaignSource

    @model_validator(mode='after')
    def target(self):
        if (self.kind == 'skill') != (self.skill is not None) or (self.kind == 'ability') != (self.ability is not None):
            raise ValueError('Check requires exactly one registered skill or ability')
        return self


class EncounterActor(ToolInput):
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,59}$')
    monster: Literal['goblin-warrior']
    position: GridPosition
    xp_value: int = Field(ge=0, le=1000)


class Reward(ToolInput):
    """What an encounter or a printed situation pays.

    native_encounter_xp is the engine own encounter experience.
    printed_experience is a value the book states (page and amount), paid
    through the host ledger so it is booked once with its source. When the
    book grants a reward the rules data cannot price yet, experience stays
    None and the reward is reported as awaiting adaptation, not booked as zero.
    """

    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,79}$')
    kind: Literal['native_encounter_xp', 'printed_experience']
    source: CampaignSource
    experience: int | None = Field(default=None, ge=0, le=1000000)
    page: int | None = Field(default=None, ge=1, le=2000)

    @model_validator(mode='after')
    def priced(self):
        if self.kind == 'printed_experience' and self.experience is None and self.page is None:
            raise ValueError('A printed reward needs its page or its amount')
        if self.kind == 'native_encounter_xp' and self.experience is not None:
            raise ValueError('Native encounter experience is computed by Nat20, not supplied')
        return self

    @model_serializer(mode='wrap')
    def stable_optional_price(self, handler):
        # A reward that predates printed pricing must serialize exactly as it
        # did: the definition digest is part of every durable state, so adding
        # a null key here would invalidate saved campaigns for no benefit.
        result = handler(self)
        if self.experience is None:
            result.pop('experience', None)
        if self.page is None:
            result.pop('page', None)
        return result


class SceneEncounter(ToolInput):
    grid: GridScene
    hero_position: GridPosition
    actors: list[EncounterActor] = Field(min_length=1, max_length=1)
    reward: Reward

    @model_validator(mode='after')
    def positions(self):
        ids = [a.id for a in self.actors]
        cells = [(p.x, p.y, p.z) for p in [self.hero_position, *(a.position for a in self.actors)]]
        if len(set(ids)) != len(ids) or 'hero' in ids or len(set(cells)) != len(cells):
            raise ValueError('Duplicate actor or starting cell')
        if any(z != 0 or x >= self.grid.width or y >= self.grid.height for x, y, z in cells):
            raise ValueError('Unbound starting surface')
        return self


class BlessSpellcasting(ToolInput):
    content_release: Literal['bless-self-2024-v1']
    prepared: list[Literal['bless']] = Field(min_length=1, max_length=1)
    holy_symbol: Literal['amulet']
    hands: Literal['mace_one_hand_symbol_other_hand']
    environment: Literal['open_bright_no_silence']


class CampaignDefinition(ToolInput):
    schema_version: Literal['nat20-campaign-v1'] = 'nat20-campaign-v1'
    rules_version: Literal['dnd2024'] = 'dnd2024'
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,79}$')
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1500)
    source: CampaignSource
    build: CharacterBuildSpec
    attack_weapon: Literal['longsword', 'mace']
    features: list[Literal['second-wind']] = Field(max_length=1)
    checks: dict[str, SceneCheck] = Field(min_length=1, max_length=12)
    encounters: dict[str, SceneEncounter] = Field(min_length=1, max_length=2)
    spellcasting: BlessSpellcasting | None = None

    @model_serializer(mode='wrap')
    def preserve_fighter_serialization(self, handler):
        result = handler(self)
        if self.spellcasting is None:
            result.pop('spellcasting', None)
        return result

    @model_validator(mode='after')
    def scope(self):
        build = self.build
        if self.spellcasting is not None:
            if (self.id != 'cleric-bless-2024-v1' or build.classes != {'cleric': 1}
                    or build.species_slug != 'human' or build.subclass_slug or build.background_slug
                    or build.hp_mode != 'fixed' or build.attuned_items or self.features
                    or self.attack_weapon != 'mace' or tuple(build.equipment) != ('mace', 'amulet')
                    or tuple(build.selected_choices) != ('skill:insight', 'skill:religion')
                    or build.ability_score_method != 'standard_array'):
                raise ValueError('Unreviewed pending Cleric build')
            return self
        if (build.classes != {'fighter': 1} or build.species_slug != 'human' or build.subclass_slug
                or build.background_slug or build.hp_mode != 'fixed' or build.attuned_items
                or tuple(build.equipment) != ('longsword', 'chain-mail', 'shield')
                or tuple(build.selected_choices) != ('skill:athletics', 'skill:perception')):
            raise ValueError('Build outside the reviewed level-one fighter capability')
        if any(type(n) is not int or not 8 <= n <= 17 for n in build.ability_scores.model_dump().values()):
            raise ValueError('Unreviewed starting abilities')
        return self


class RewardReceipt(ToolInput):
    """One confirmed payout, in the only ledger that pays experience.

    An encounter reward is proven by the fight that produced it: the campaign
    definition registers its id, and the outcome hash pins the fight. A printed
    reward (``kind='printed'``) is paid by a reviewed scenario rather than by an
    encounter, so it names the page it came from and carries no encounter; it
    still moves the very same counter, because a second counter beside the
    engine ledger is exactly what the durable state refuses.
    """

    id: str
    encounter_id: str
    combat_id: str
    experience: int = Field(ge=0, le=1000000)
    outcome_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source: CampaignSource
    kind: Literal['encounter', 'printed'] = 'encounter'
    page: int | None = Field(default=None, ge=1, le=2000)

    @model_validator(mode='after')
    def printed(self):
        if self.kind == 'printed':
            if not self.id:
                raise ValueError('A printed reward needs the id of its rule')
            if self.experience <= 0:
                raise ValueError('A printed reward pays the amount the book prints')
            if self.page is None:
                raise ValueError('A printed reward carries the page it came from')
        elif self.page is not None:
            raise ValueError('An encounter reward is priced by the encounter, not by a page')
        return self

    @model_serializer(mode='wrap')
    def stable_optional_price(self, handler):
        # A receipt saved before printed rewards existed must serialize exactly
        # as it did: the combat ledger is part of every durable state, so an
        # added null key would rewrite saves for no benefit.
        result = handler(self)
        if self.kind == 'encounter':
            result.pop('kind', None)
        if self.page is None:
            result.pop('page', None)
        return result


class DefinitionRefused(ValueError):
    """Отказ регистрации определения — с машинной причиной, не только текстом.

    ``code`` читает экран совместимости: по нему видно, можно ли обновить
    кампанию на текущую регистрацию или остаются только чтение и выгрузка.
    Игроку по-прежнему показывается текст исключения.
    """

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def load_definition(identity='fighter-road-2024-v1'):
    root = ROOT
    from .nat20_bless import PROFILE, ROOT as BLESS_ROOT, require_stand
    if identity == PROFILE:
        require_stand()
        root = BLESS_ROOT
    registry = json.loads((root / 'registry.json').read_text(encoding='utf-8'))
    record = registry['definitions'].get(identity)
    if record is None:
        raise DefinitionRefused('Определение кампании не зарегистрировано для исполнения. '
                                    'Зарегистрируйте версию определения или откройте кампанию только '
                                    'для чтения с выгрузкой истории.', 'not_registered')
    if record.get('verification') != 'behaviorally_verified' and os.environ.get('SOLO_TEST') != '1':
        raise DefinitionRefused('Определение кампании не прошло поведенческую приёмку: '
                                        'играть им нельзя, пока приёмка не подтверждена. Доступны '
                                        'чтение истории и выгрузка кампании.', 'not_accepted')
    path = (root / record['file']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Invalid registered campaign path')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise DefinitionRefused('Файл проверенной кампании изменился: регистрация больше '
                                'не совпадает с содержимым. Зарегистрируйте новую версию '
                                'определения; старую кампанию можно обновить миграцией на копии.',
                            'file_changed')
    definition = CampaignDefinition.model_validate_json(raw)
    if definition.id != identity:
        raise DefinitionRefused('Идентификатор определения в сохранении не совпадает '
                                'с зарегистрированным: кампания собрана из другой сцены. '
                                'Обновите её миграцией на копии или выгрузите историю.', 'identity')
    return definition


def validate_definition(raw):
    if isinstance(raw, dict) and raw.get('schema_version') == 'nat20-experimental-v1':
        from .nat20_prototype import PrototypeDefinition
        definition=PrototypeDefinition.model_validate(raw)
        from .nat20_monsters import attack_profile, binding_source
        for encounter in definition.encounters.values():
            for actor in encounter.actors:
                if actor.attack_bonus_source and actor.attack_bonus_source.kind=='native_derived':
                    profile=attack_profile(actor.monster)
                    if actor.attack_bonus!=profile['attack_bonus'] or actor.attack_bonus_source!=binding_source(profile):
                        raise DefinitionRefused('Сохранённая привязка атаки монстра, выведенной движком, '
                                        'не совпадает с закреплённым расчётом. Пересоберите '
                                        'кампанию на текущей версии: бой на старых числах не начнётся.',
                                    'monster_binding')
        return definition
    definition = CampaignDefinition.model_validate(raw)
    if definition != load_definition(definition.id):
        raise DefinitionRefused('Сохранённое определение кампании отличается от проверенной '
                                'регистрации: авторская сцена (сетка боя, проверки, награды) изменилась '
                                'после сборки кампании. Без обновления на копии кампания не продолжается — '
                                'история и герой при этом целы.', 'registration_changed')
    return definition
