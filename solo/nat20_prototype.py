"""Explicit experimental data release and typed author inputs; no game arithmetic."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dnd5e_engine import CharacterBuildSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader
from pydantic import Field, model_validator, model_serializer

from .game_commands import ToolInput, GridPosition
from .nat20_campaign import CampaignSource, Reward, SceneCheck
from .content_verification_cache import read_json

RELEASE = 'prototype-srd-2024-v1'
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical'
MANIFEST = ROOT / 'integration/nat20/releases' / RELEASE / 'manifest.json'
CATEGORIES = ('classes', 'species', 'subclasses', 'backgrounds', 'feats', 'features',
              'items', 'spells', 'monsters', 'conditions', 'traits')


class PrototypeActor(ToolInput):
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,59}$')
    monster: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,119}$')
    position: GridPosition
    xp_value: int = Field(default=0, ge=0, le=1000000)
    attack_bonus: int | None = Field(default=None, ge=-10, le=50)
    attack_bonus_source: CampaignSource | None = None

    @model_validator(mode='after')
    def supplied_attack(self):
        if (self.attack_bonus is None)!=(self.attack_bonus_source is None):
            raise ValueError('Native monster to-hit requires an explicit trusted author source')
        return self

    @model_serializer(mode='wrap')
    def stable_optional_bonus(self,handler):
        result=handler(self)
        if self.attack_bonus is None:
            result.pop('attack_bonus');result.pop('attack_bonus_source')
        return result


class PrototypeEncounter(ToolInput):
    grid: GridScene
    hero_position: GridPosition
    actors: list[PrototypeActor] = Field(min_length=1, max_length=20)
    reward: Reward | None = None

    @model_validator(mode='after')
    def positions(self):
        cells = [(p.x, p.y, p.z) for p in [self.hero_position, *(a.position for a in self.actors)]]
        ids = [a.id for a in self.actors]
        if len(set(ids)) != len(ids) or 'hero' in ids or len(set(cells)) != len(cells):
            raise ValueError('Duplicate actor or starting cell')
        if self.grid.width > 80 or self.grid.height > 80 or any(z != 0 or x >= self.grid.width or y >= self.grid.height for x,y,z in cells):
            raise ValueError('Invalid grid starting surface')
        if set(f'{x},{y}' for x,y,_ in cells) & set(self.grid.blocked_cells):
            raise ValueError('Starting cell is blocked')
        return self


class PrototypeCheck(SceneCheck):
    kind: Literal['skill', 'ability', 'saving_throw']
    dc: int | None = Field(default=None, ge=1, le=40)
    advantage: bool = False
    disadvantage: bool = False

    @model_serializer(mode='wrap')
    def stable_optional_flags(self,handler):
        result=handler(self)
        for key in ('advantage','disadvantage'):
            if not result[key]:result.pop(key)
        return result

    @model_validator(mode='after')
    def target(self):
        if (self.kind == 'skill') != (self.skill is not None) or (self.kind != 'skill') != (self.ability is not None):
            raise ValueError('Check requires one skill or ability/save')
        return self


class PrototypeDefinition(ToolInput):
    schema_version: Literal['nat20-experimental-v1'] = 'nat20-experimental-v1'
    rules_version: Literal['dnd2024'] = 'dnd2024'
    content_release: Literal['prototype-srd-2024-v1'] = RELEASE
    id: str = 'experimental'
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1500)
    source: CampaignSource
    build: CharacterBuildSpec
    attack_weapon: str | None
    features: list[str]
    checks: dict[str, PrototypeCheck] = Field(max_length=100)
    encounters: dict[str, PrototypeEncounter] = Field(min_length=0, max_length=64)
    prepared_spells: list[str] = Field(default_factory=list, max_length=100)
    # The scenario's advancement model. It belongs to the definition because
    # the book decides it, not the combat log (page 7: milestone instead of the
    # usual experience rules).
    progression: 'Progression' = Field(default_factory=lambda: Progression(mode=UNSET))
    definition_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def fingerprint(self):
        from .nat20_content import digest
        if digest(self.model_dump(mode='json', exclude={'definition_sha256'})) != self.definition_sha256:
            raise ValueError('Experimental author definition changed; explicit new campaign required')
        return self


from .progression import UNSET, Progression  # noqa: E402  (used by the field default above)


def experimental(value):
    return isinstance(value, PrototypeDefinition) or isinstance(value, dict) and value.get('schema_version') == 'nat20-experimental-v1'


def bundled_release(parent_fingerprint, selector):
    from .nat20_content import digest
    from .content_verification_cache import verify_tree
    manifest = read_json(MANIFEST)
    lock = read_json(ROOT/'vendor/nat20/UPSTREAM.json')
    prefix = 'packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/'
    expected = {name.removeprefix(prefix): sha for name,sha in lock['files'].items() if name.startswith(prefix) and name.endswith('.json')}
    if manifest['files'] != expected:
        raise ValueError('Experimental corpus manifest differs from upstream pin')
    verify_tree(DATA, expected, suffix='.json')
    fingerprint = digest({'runtime_parent':parent_fingerprint,'bundled_data':manifest})
    if selector not in (RELEASE, fingerprint):
        raise ValueError('Experimental release fingerprint mismatch')
    return BundledAssetLoader(root=DATA), fingerprint, {'experimental': True}


def known_issue(category, slug, raw=None):
    if category == 'spells' and slug == 'bless':
        return 'blocked: pinned concentration save omits Bless bonus; Foundry save-key alias and recast identity defects'
    if category == 'actions' and slug == 'hide':
        return 'blocked: pinned Hide does not spend its required Action'
    if category=='features' and slug=='rage':
        return 'blocked: pinned Rage activation emits disabled effect; no mechanical Rage benefit can be committed'
    review = (raw or {}).get('review') or {}
    return review.get('known_divergence') or ('Requires adjudication' if review.get('requires_user_decision') else None)


def spell_execution_status(raw):
    """Describe actual typed combat payloads, not a hardcoded spell count."""
    if not raw.get('activities'):
        return 'narrative_or_unsupported_in_combat'
    from dnd5e_engine.activities.conjuration import CONJURATION_ALLOWLIST
    supported={'attack','save','damage','heal','check','cast'}
    for activity in raw['activities']:
        if activity.get('kind') in supported:
            return 'native_candidate'
        if activity.get('kind') in ('enchant','summon','transform') and raw.get('slug') in CONJURATION_ALLOWLIST:
            return 'native_candidate'
        if activity.get('kind')=='utility' and activity.get('effects') and any(
            not effect.get('disabled') and (effect.get('changes') or effect.get('statuses')) for effect in raw.get('passive_effects',[])):
            return 'native_candidate'
    return 'narrative_or_unsupported_in_combat'


def spell_status(slug):
    """Small per-prepared-spell projection for master context, not full catalogue."""
    if not isinstance(slug,str) or not slug or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in slug):
        raise ValueError('Invalid prepared spell reference')
    path=DATA/'spells'/(slug+'.json')
    if not path.is_file():raise ValueError('Unknown prepared spell reference')
    raw=read_json(path)
    issue=known_issue('spells',slug,raw)
    return 'blocked' if issue and issue.startswith('blocked:') else spell_execution_status(raw)


def preview_build(build, prepared_spells=()):
    from dnd5e_engine import CombatInstance, derive_sheet, build_party_member
    from dnd5e_engine.rules.character import MAX_SCORE_FROM_INCREASES
    from dnd5e_engine.rules.advancement import choice_options, validate_choices
    from .nat20_content import release
    loader, fingerprint, _ = release(RELEASE)
    build = CharacterBuildSpec.model_validate(build)
    if not 1 <= build.level <= 20:
        raise ValueError('Native character level must be 1..20')
    # A score array is only legal if a generation method produced it. Nat20
    # validates the method when one is named; leaving it unset accepts any six
    # integers, which is how six free numbers used to pass as a character.
    if build.ability_score_method is None:
        raise ValueError(
            'Не выбран способ генерации характеристик: standard_array или point_buy.'
        )
    # Nat20 checks the score range only when ``ability_score_method`` is set; an
    # unset method would otherwise let an arbitrary integer through and the sheet
    # would keep it (strength 999 -> modifier +494). The ceiling is Nat20's own
    # SRD 5.2 constant, not a number invented here; 1 is the floor because a
    # score of zero or below is not a score.
    for name, score in build.ability_scores.model_dump().items():
        if type(score) is not int or not 1 <= score <= MAX_SCORE_FROM_INCREASES:
            raise ValueError(
                f'Характеристика {name} должна быть целым числом от 1 до '
                f'{MAX_SCORE_FROM_INCREASES}; получено {score!r}.'
            )
    for category, slug in [('species', build.species_slug), *(('classes',s) for s in build.classes),
                           *(('items',s) for s in build.equipment)]:
        if slug not in loader.list_slugs(category):
            raise ValueError(f'Unknown {category} reference: {slug}')
    # Every advancement obligation the levels create is checked here, in the
    # native module that reads the same canonical data ``derive_sheet`` reads:
    # the subclass at its gate level, the Ability Score Improvement slot, the
    # skill and expertise counts, and each feature pool (fighting style, Divine
    # Order, Giant Ancestry, origin feat). The same call runs on create and on
    # level-up, so an illegal build cannot arrive by direct HTTP either.
    try:
        validate_choices(build, loader=loader)
    except ValueError as error:
        raise ValueError(str(error)) from error
    for slug in prepared_spells:
        if slug not in loader.list_slugs('spells'):
            raise ValueError('Unknown prepared spell: '+slug)
    sheet = derive_sheet(build, loader=loader)
    has_casting = any(str(loader.get_class(s).spellcasting.progression) != 'none' for s in build.classes)
    if not has_casting and any(s not in sheet.spell_abilities for s in prepared_spells):
        raise ValueError('Prepared spell not granted by native class/species/feat build')
    member = build_party_member(build, CombatInstance(entity_id='hero',name='Preview',zone_id='1,1',
        spells_known=tuple(prepared_spells)),loader=loader)
    if prepared_spells and not (sheet.spell_slots or sheet.pact_slots):
        if any(loader.get_spell(s).level > 0 for s in prepared_spells):
            raise ValueError('Build has no native spell slots for selected levelled spells')
    return dict(release=fingerprint, build=build.model_dump(mode='json'), sheet=sheet.model_dump(mode='json'),
        hero=member.model_dump(mode='json',exclude_unset=True), choices=choice_options(build, loader=loader),
        warnings=[
            'Experimental upstream builder; prepared spell counts are supplied by explicit player selection and are not fully enforced by the native builder.',
            'Pinned canonical provenance is per record; not the local Foundry 5.3.3 import.'])


def make_definition(build, encounters, checks, prepared_spells, title, description, progression=None, episode_id=None):
    from .nat20_content import digest
    preview = preview_build(build, prepared_spells)
    source = CampaignSource(id='experimental-author',version='1',note='Explicit campaign creation input; never parameters supplied by an action from LLM')
    typed_encounters = {key:PrototypeEncounter.model_validate(value) for key,value in encounters.items()}
    from .nat20_monsters import attack_profile, binding_source
    for key,encounter in list(typed_encounters.items()):
        actors=[]
        for actor in encounter.actors:
            if actor.attack_bonus is None:
                profile=attack_profile(actor.monster)
                if profile['status']=='native_derived':
                    actor=actor.model_copy(update={'attack_bonus':profile['attack_bonus'],'attack_bonus_source':binding_source(profile)})
            actors.append(actor)
        typed_encounters[key]=encounter.model_copy(update={'actors':actors})
    # Reward metadata is author-owned; default XP remains zero, not generated by AI.
    for key, encounter in typed_encounters.items():
        if encounter.reward is None:
            typed_encounters[key] = encounter.model_copy(update={'reward':Reward(id='reward-'+key,kind='native_encounter_xp',source=source)})
    if not checks:
        from dnd5e_engine.rules.skills import SKILL_ABILITIES
        checks = {s:dict(kind='skill',skill=s,ability=None,dc=None,source=source) for s in SKILL_ABILITIES}
        checks.update({'save-'+a:dict(kind='saving_throw',skill=None,ability=a,dc=None,source=source) for a in ('str','dex','con','int','wis','cha')})
        checks.update({a:dict(kind='ability',skill=None,ability=a,dc=None,source=source) for a in ('str','dex','con','int','wis','cha')})
    selected_spells=list(dict.fromkeys([*prepared_spells,*preview['sheet'].get('spell_abilities',{})]))
    from .progression import UNSET, Progression
    fields = dict(title=title,description=description,source=source,build=CharacterBuildSpec.model_validate(preview['build']),
        attack_weapon=next((s for s in preview['build']['equipment'] if s in catalog_slugs('weapons')),None),
        features=preview['sheet']['features'],checks={k:PrototypeCheck.model_validate(v) for k,v in checks.items()},
        encounters=typed_encounters,prepared_spells=selected_spells,
        # The advancement model travels with the definition so the fight cannot
        # decide the hero growth. A campaign made with no model says so.
        progression=Progression.model_validate(progression) if progression is not None else Progression(mode=UNSET))
    # Materialize defaults before hashing, identical to durable JSON validation.
    # The id names the episode the campaign plays: a book keeps milestones by
    # episode, so a fixed id would never match and the hero could not advance
    # by the book's own rule.
    typed = PrototypeDefinition.model_construct(**fields, schema_version='nat20-experimental-v1', rules_version='dnd2024',
        content_release=RELEASE, definition_sha256='0'*64, id=episode_id or 'experimental')
    raw=typed.model_dump(mode='json');raw['definition_sha256']=digest({k:v for k,v in raw.items() if k!='definition_sha256'})
    return PrototypeDefinition.model_validate(raw)


@lru_cache(maxsize=None)
def catalog_slugs(category):
    # The release is verified once per category instead of once per lookup;
    # a single options screen asks for the same categories several times.
    from .nat20_content import release
    loader,_,_=release(RELEASE)
    return [s for s in loader.list_slugs('items') if loader.get_weapon(s)] if category=='weapons' else loader.list_slugs(category)


def _subclass_metadata(raw):
    """Project the pinned record's own ownership/level; no rule arithmetic.

    ``class_identifier`` names the parent class and the record's own
    ``granted_features`` levels are its subclass-feature levels, so the first
    positive one is the level at which this subclass can be chosen. The
    native builder enforces exactly this gate via ``subclass_gate_level``; a
    test pins both derivations together so a divergent corpus fails loudly.
    """
    levels=[f['level'] for f in raw.get('granted_features') or []
            if isinstance(f,dict) and isinstance(f.get('level'),int) and f['level']>0]
    owner=raw.get('class_identifier')
    return dict(class_slug=owner if isinstance(owner,str) and owner else None,
                min_level=min(levels) if levels else None)


def _catalog_entries(categories):
    from .game_commands import NativeAction
    from typing import get_args
    entries=[]
    for cat in categories:
        if cat=='actions':
            for slug in get_args(NativeAction.model_fields['operation'].annotation):
                issue=known_issue('actions',slug)
                entries.append(dict(id='actions:'+slug,slug=slug,name=slug,category=cat,rules_version='dnd2024',
                    source={'runtime':'Nat20 0.7.0'},execution_status='blocked' if issue else 'native_experimental',
                    known_issue=issue,parameters=NativeAction.model_json_schema()))
            continue
        for path in sorted((DATA/cat).glob('*.json')):
            raw=read_json(path);slug=raw['slug'];issue=known_issue(cat,slug,raw)
            prov=raw.get('provenance') or {};versions=prov.get('srd_version') or []
            rules='dnd2024' if '5.2' in versions else ('dnd2014' if '5.1' in versions else 'unspecified')
            activity=bool(raw.get('activities'))
            status='blocked' if issue and issue.startswith('blocked:') else ('builder_native_experimental' if cat in ('classes','species','subclasses','backgrounds','feats') else 'native_candidate' if activity or cat=='monsters' else 'reference_only')
            if cat=='spells' and status!='blocked':status=spell_execution_status(raw)
            if status=='narrative_or_unsupported_in_combat':
                issue='No typed combat activity/effect payload in this pinned spell; use narrative adjudication outside combat'
            row=dict(id=cat+':'+slug,slug=slug,name=raw.get('name',slug),category=cat,rules_version=rules,
                source=prov,execution_status=status,known_issue=issue,parameters={'operation':'native_action'} if activity else {})
            # Ownership/level come from this same record, so the builder never
            # has to guess from a name and adds no extra content read.
            if cat=='subclasses':row.update(_subclass_metadata(raw))
            entries.append(row)
    return entries


def catalog_batch(categories):
    """One verified release and projection for selected categories; no pagination."""
    from .nat20_content import release
    categories=tuple(categories)
    if len(set(categories))!=len(categories) or any(cat not in CATEGORIES for cat in categories):
        raise ValueError('Unknown or duplicate catalogue category')
    _,fingerprint,_=release(RELEASE)
    return dict(release=fingerprint,entries=_catalog_entries(categories))


def catalog(category=None, query='', limit=50, offset=0):
    from .nat20_content import release
    _, fingerprint, _ = release(RELEASE)
    if category not in (*CATEGORIES,'actions',None):
        raise ValueError('Unknown catalogue category')
    if not isinstance(query,str) or not 1 <= limit <= 200 or offset < 0:
        raise ValueError('Invalid catalogue pagination')
    entries=_catalog_entries(CATEGORIES if category is None else (category,))
    if query:
        entries=[e for e in entries if query.casefold() in (e['slug']+' '+e['name']).casefold()]
    return dict(release=fingerprint,total=len(entries),entries=entries[offset:offset+limit])


def catalog_detail(identity):
    category,sep,slug=identity.partition(':')
    if sep and category=='actions':
        packet=catalog('actions',limit=200)
        row=next((e for e in packet['entries'] if e['slug']==slug),None)
        if row is None:raise ValueError('Unknown native action ID')
        return dict(row,data=row['parameters'])
    if not sep or category not in CATEGORIES or not slug or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in slug):
        raise ValueError('Invalid stable catalogue ID')
    path=DATA/category/(slug+'.json')
    if not path.is_file():raise ValueError('Unknown catalogue ID')
    found=catalog(category,slug,200)['entries']
    result=dict(next(e for e in found if e['id']==identity),data=read_json(path))
    if category=='monsters':
        from .nat20_monsters import attack_profile
        profile=attack_profile(slug)
        result['attack_profile']=profile
        if profile['status']=='unbound':
            result['known_issue']='Native attack binding unavailable for this profile: '+profile['reason']
    return result
