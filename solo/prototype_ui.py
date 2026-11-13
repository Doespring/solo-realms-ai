"""HTTP/presentation bridge to the Rules-owned prototype services; no rule arithmetic."""
import asyncio
import html
import json
import re
from functools import lru_cache
from copy import deepcopy
from typing import Any, Literal

from dnd5e_engine import CharacterBuildSpec
from pydantic import BaseModel, ConfigDict, Field

from .nat20_api import Nat20GameEngine


class BuildInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    build: CharacterBuildSpec
    prepared_spells: list[str] = Field(default_factory=list, max_length=100)


class CampaignRulesInput(BaseModel):
    """The three campaign rules a player owns (r217).

    Every field is optional: the screen names only what the player changed, and
    an untouched value keeps what the campaign already had. No numbers are
    accepted here — the table of what each choice means lives in
    ``solo/nat20_settings.py``, and the host applies it where the number is used.
    """
    model_config = ConfigDict(extra='forbid')
    death: Literal['off', 'agreed', 'on'] | None = None
    difficulty: Literal['solo_softer', 'as_is', 'harder'] | None = None
    rewards: Literal['normal', 'sparse', 'generous'] | None = None
    request_id: str = Field(default='', pattern=r'^[a-zA-Z0-9_.-]{0,80}$')


class CompanionInput(BuildInput):
    """A companion is built by the hero's own editor (r214).

    Same ``build`` and ``prepared_spells`` the campaign creation takes, plus
    the answers to any advancement question the join asks - never a sheet.
    r216: ``traits`` is the character the host decides his answers from
    (2-4 of cautious/loyal/greedy/proud); the relation itself starts neutral
    and only events move it.
    """
    name: str = Field(min_length=1, max_length=80)
    request_id: str = Field(default='', pattern=r'^[a-zA-Z0-9_.-]{0,80}$')
    choices: dict = Field(default_factory=dict)
    traits: list[str] = Field(default_factory=list, max_length=4)



class NpcRecruitInput(BaseModel):
    """An NPC of the material joins the party (r218).

    ``npc`` is the person's id in the world state (or his name, for a save that
    has no ids). The class and the species are read from the printed sentence
    about him; passing them explicitly is the player's override, and the record
    keeps which of the two it used.
    """
    npc: str = Field(min_length=1, max_length=120)
    reason: str = Field(default='asked', pattern=r'^(rescued|hired|asked)$')
    class_slug: str = Field(default='', max_length=40)
    species_slug: str = Field(default='', max_length=40)
    traits: list[str] = Field(default_factory=list, max_length=4)
    request_id: str = Field(default='', pattern=r'^[a-zA-Z0-9_.-]{0,80}$')


class NewGameInput(BuildInput):
    name: str = Field(min_length=1, max_length=80)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    adventure: str
    description: str = Field(default='', max_length=1500)
    encounter_monsters: list[str] = Field(default_factory=list, max_length=4)
    adventure_revision: int | None = Field(default=None,ge=0)
    encounter_attack_bonuses: dict[str,int] = Field(default_factory=dict)
    encounter_attack_sources: dict[str,str] = Field(default_factory=dict)
    mechanics_ruleset: Literal['dnd2024'] | None = None
    rules_confirmed: bool = False
    hero_confirmed: bool = False
    background_confirmed: bool = False
    hero_background: str = Field(default='',max_length=1000)
    # A prepared world may name the difficulty of its own checks. Without it every
    # check Nat20 rolls comes back with success=null, so a search can never succeed
    # or fail and the story cannot depend on the outcome.
    prepared_checks: dict[str,int] = Field(default_factory=dict, max_length=100)
    # The preparation draft the hero was built for, so a book read by the dialog
    # before the hero step is still attached when the campaign starts.
    preparation_draft: str = Field(default='', max_length=80)
    # A03/F01: the four parts the eight-step master hands over explicitly. They
    # are checked against the saved draft, so the launch starts the hero and the
    # items the player approved there instead of guessing or re-creating them.
    module_id: str | None = Field(default=None, max_length=80)
    hero_draft: dict | None = None
    equipment: list[str] | None = Field(default=None, max_length=20)

    def typed_checks(self):
        """Difficulty named by the world, typed by the host.

        The client supplies only skill:DC so it cannot forge the provenance block
        that Nat20 records for every check definition.
        """
        from .nat20_campaign import CampaignSource
        if not self.prepared_checks:
            return None
        source=CampaignSource(id='prepared-world',version='1',
                              note='Difficulty named by the prepared world, not by an action from the model')
        out={}
        for skill,dc in self.prepared_checks.items():
            if type(dc) is not int or not 1<=dc<=40:
                raise ValueError('Сложность проверки должна быть целым числом от 1 до 40: '+str(skill))
            out[skill]=dict(kind='skill',skill=skill,ability=None,dc=dc,source=source.model_dump(mode='json'))
        return out



class WizardInput(BaseModel):
    """A partially filled draft plus the answers the screen collected.

    The answers stay separate because the token grammar (``skill:``,
    ``background:+2``, ``asi:``) is native protocol: the screen sends what the
    player picked and the server writes the tokens the module reads.
    """

    model_config = ConfigDict(extra="forbid")
    draft: dict
    answers: dict[str, Any] = Field(default_factory=dict)
    #: The level the draft had before the player lowered it. Fitting away the
    #: picks a lower level cannot take is a statement the screen makes about its
    #: own history; a direct caller that stays silent gets the rules gate.
    from_level: int | None = Field(default=None, ge=1, le=20)


class ImportInput(BaseModel):
    """A pasted sheet, read locally; the model is never asked to parse it."""

    text: str = Field(default='', max_length=20000)


class LevelUpInput(BaseModel):
    """One confirmed advancement: the whole draft plus the version it saw."""

    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    expected_version: int = Field(ge=0)
    draft: dict
    answers: dict[str, Any] = Field(default_factory=dict)


def resolved_answers(answers):
    """The answers as the screen keeps them: JSON, no engine objects."""
    return {str(key): value for key, value in dict(answers or {}).items()}


def _earned(campaign):
    """The level this campaign has earned, by its own advancement model.

    A milestone scenario grants the level the book fixes for the finished
    chapter; asking the SRD experience table there would refuse a level the
    book prints, because a milestone game keeps no experience at all. Only an
    experience scenario is read through the table.
    """
    from dnd5e_engine.rules.advancement import level_for_experience

    runtime = campaign["state"].get("nat20") or {}
    definition = runtime.get("definition") or {}
    progression = definition.get("progression") or {}
    if progression.get("mode") == "milestone":
        finished = {
            str(volume) for volume in
            (campaign["state"].get("adventure") or {}).get("completed_modules") or ()
        }
        levels = [int(row["level"]) for row in progression.get("milestones") or []
                  if str(row.get("episode")) in finished]
        if levels:
            return max(levels)
        # No chapter finished yet: the hero keeps the level it already has.
        return int((definition.get("build") or {}).get("level") or 1)
    return level_for_experience(int(runtime.get("experience", 0)))


def wizard_contract(body):
    """The whole wizard state: steps, sheet, kit, legality.

    The single gate every builder screen and every direct HTTP call goes
    through, so an option the rules forbid cannot be offered or submitted.
    """
    from .nat20_wizard import WizardDraft, contract, fit_level_picks, keep_settled_picks, resolve_answers

    draft = WizardDraft.model_validate(body.draft)
    # The level is a parameter of the build, so the player may lower it after
    # answering a higher one; picks that name a level the build no longer has
    # are dropped here and named in ``dropped`` instead of losing the sheet to
    # an engine refusal. Only a caller that says it lowered the level gets this:
    # a direct request that names an option the level forbids (a subclass below
    # the gate) is still refused by the ordinary gate below.
    previous = body.from_level
    if previous is not None and int(previous) > int(draft.level):
        draft, answers, dropped, problems = fit_level_picks(draft, body.answers)
    else:
        settled = draft
        draft, problems = resolve_answers(draft, body.answers)
        # A level-up answers only what the new level opens; the hero's settled
        # picks must survive the rebuild or the advancement screen reports the
        # whole build owed and loses the step it was opened for.
        draft = keep_settled_picks(settled, body.answers, draft)
        answers, dropped = body.answers, []
    state = contract(draft)
    state["issues"] = list(state["issues"]) + list(problems)
    state["ready"] = bool(state["ready"]) and not problems
    state["confirm_ready"] = bool(state.get("confirm_ready", state["ready"])) and not problems
    state["answers"] = resolved_answers(answers)
    state["dropped"] = list(dropped)
    return state


def level_up_preview(body):
    """What the next level opens, and the sheet it would produce.

    Nothing is committed: the draft sent back is the advanced one, so the
    confirm step can post exactly what the player saw.
    """
    from .nat20_wizard import WizardDraft, contract, keep_settled_picks, level_up_draft, resolve_answers

    settled = WizardDraft.model_validate(body.draft)
    answered, _problems = resolve_answers(settled, body.answers)
    answered = keep_settled_picks(settled, body.answers, answered)
    advanced = level_up_draft(answered)
    return dict(
        advanced,
        answers=resolved_answers(body.answers),
        contract=contract(advanced["draft"]),
    )

@lru_cache(maxsize=1024)
def describe(category,slug):
    try:
        record=Nat20GameEngine.catalog_detail(category+':'+slug)
        return dict(id=slug,name=record['name'],description=html.unescape(re.sub(r'<[^>]*>',' ',str(record['data'].get('description','')))),
                    activities=[dict(id=a.get('id'),name=a.get('name') or a.get('id')) for a in (record['data'].get('activities') or [])],
                    issue=record['known_issue'],status=record['execution_status'])
    except (ValueError,KeyError):
        return dict(id=slug,name=slug)


@lru_cache(maxsize=1)
def _static_creation_options():
    from dnd5e_engine import AbilityScores
    from dnd5e_engine.rules.character import MAX_SCORE_FROM_INCREASES
    from dnd5e_engine.rules.skills import SKILL_ABILITIES
    from .rulesets import SKILL_NAMES
    # Nat20's own JSON Schema leaves the six scores unbounded, so a browser number
    # input accepts 999 and the sheet keeps it. Publish the ceiling Nat20 itself
    # enforces (SRD 5.2 increases stop at 20) so the form and the server agree;
    # this is a presentation bound, not new rule arithmetic.
    schema=Nat20GameEngine.build_schema()
    scores=(schema.get('$defs') or {}).get('AbilityScores') or {}
    for name,node in (scores.get('properties') or {}).items():
        if node.get('type')=='integer':
            node.setdefault('minimum',1)
            node.setdefault('maximum',MAX_SCORE_FROM_INCREASES)
    return dict(build_schema=schema,
        default_build=dict(class_slug='',species_slug='',level=1,ability_scores=AbilityScores().model_dump(),equipment=[],selected_choices=[]),
        choices=[dict(id=prefix+':'+skill,name=label+': '+SKILL_NAMES.get(skill,skill))
                 for prefix,label in [('skill','Владение'),('expertise','Компетентность')] for skill in SKILL_ABILITIES],
        adventures=[dict(id='sandbox',name='Песочница'),dict(id='authored',name='Своё приключение')],
        limitations=['Пошаговый конструктор проверяет каждый шаг через Nat20; каталог ниже нужен только расширенному мастеру.',
            'Выберите участников одной возможной встречи. Бой начнётся только отдельным действием в игре.',
            'Описания каталога не означают автоматическое исполнение всех эффектов. Известный отказ отдельного эффекта не закрывает остальную игру.'])


@lru_cache(maxsize=1)
def _legal_default_build():
    """The starting sheet the advanced catalogue opens with.

    A blank build owes skills, expertise, a background increase and an origin
    feat, so the native gate refuses it. Rather than let the fallback form open
    on an illegal draft, it opens on a sheet the same contract accepted.
    """
    from .nat20_wizard import suggested_build

    return suggested_build()


def creation_options():
    from .nat20_prototype import catalog_batch
    categories=('classes','species','subclasses','backgrounds','items','spells','monsters')
    packet=catalog_batch(categories)
    result=deepcopy(_static_creation_options())
    result.update({category:[] for category in categories})
    result['default_build']=deepcopy(_legal_default_build())
    for row in packet['entries']:
        option=dict(id=row['slug'],catalogue_id=row['id'],name=row['name'],
            issue=row['known_issue'],status=row['execution_status'])
        # Additive builder metadata only where the catalogue actually carries
        # it (subclasses); other categories keep the exact previous shape.
        if 'class_slug' in row:
            option.update(class_slug=row.get('class_slug'),min_level=row.get('min_level'))
        result[row['category']].append(option)
    return result


creation_options.cache_clear=_static_creation_options.cache_clear
_legal_default_build.cache_clear=_static_creation_options.cache_clear


def build_preview(body):
    try:
        result=Nat20GameEngine.preview_build(body.build.model_dump(mode='json'),body.prepared_spells)
        # The native module already resolved every decision the build's levels
        # create, with its canonical pool and how many picks are still owed, so
        # the screen renders that instead of re-reading feature_choices here.
        choices=[dict(row,label=row['title'] or row['kind']) for row in result['choices']]
        return dict(result,valid=True,issues=[],choices=choices)
    except (ValueError,KeyError) as error:
        return dict(valid=False,issues=[str(error)],warnings=[])


def player_options(current):
    from .game_commands import NativeAction
    from .nat20_prototype import catalog_slugs
    engine=Nat20GameEngine
    capabilities=engine.capabilities(current)
    snapshot=engine.visible(engine.snapshot(current))
    actions=engine.catalog('actions',limit=200)['entries'] if capabilities['experimental'] else []
    return dict(version=current['version'],experimental=capabilities['experimental'],
        actions=actions,action_schema=NativeAction.model_json_schema(),
        items=[describe('items',s) for s in capabilities['equipment']],
        spells=[describe('spells',s) for s in capabilities['prepared_spells']],
        features=[describe('features',s) for s in capabilities['features']],
        weapons=[describe('items',s) for s in capabilities['equipment'] if s in catalog_slugs('weapons')],
        targets=[dict(id=a['id'],name=a['name'],position=a.get('position')) for a in snapshot['actors']],
        known_issues=capabilities['known_issues'])


def register_prototype_ui_api(app,store,game):
    created={}

    @app.get('/api/prototype/options')
    async def options():
        result=dict(await asyncio.to_thread(creation_options));result['adventures']=list(result['adventures'])
        preparation=getattr(app.state,'preparation',None)
        if preparation:
            for row in preparation.list():
                draft=preparation.get(row['id']);body=draft['body']
                if body.get('flow')=='simple':
                    if draft['campaign']:continue
                    from .prototype_prepared import prepared_start_readiness
                    readiness=await asyncio.to_thread(prepared_start_readiness,preparation,row['id'])
                    if readiness['ready'] and readiness['world_ready']:
                        result['adventures'].append(dict(id='prepared:'+row['id'],name='Подготовлено: '+row['name'],
                            revision=draft['revision'],scope='world',world_ready=True,ready=True,
                            source_ruleset=readiness['source_ruleset'],mechanics_ruleset='dnd2024',
                            requires_hero_confirmation=True,requires_rules_confirmation=True))
                    continue
                if not draft['campaign'] and body.get('prepared') and body.get('background_confirmed') and body.get('opening'):
                    result['adventures'].append(dict(id='prepared:'+row['id'],name='Подготовлено: '+row['name'],revision=draft['revision'],hero_name=body.get('hero',{}).get('name') or body['name']))
        return result


    @app.post("/api/prototype/wizard/contract")
    async def wizard(body:WizardInput):
        return await asyncio.to_thread(wizard_contract,body)

    @app.post("/api/prototype/wizard/recommend")
    async def recommend(body:WizardInput):
        """A legal suggestion for the draft as it stands, never applied by itself.

        The player presses "Рекомендовать" and reads the answer. Choices already
        made are kept as the starting point, so the suggestion fits the hero the
        table is building instead of replacing it without being asked.
        """
        from .nat20_wizard import WizardDraft, recommend_draft, resolve_answers

        draft, _problems = resolve_answers(WizardDraft.model_validate(body.draft), body.answers)
        return await asyncio.to_thread(recommend_draft, draft)

    @app.post("/api/prototype/wizard/import")
    async def import_sheet(body:ImportInput):
        """Read a pasted sheet with local rules only, and offer what it found."""
        from .nat20_import import parse_sheet

        return await asyncio.to_thread(parse_sheet,body.text)

    @app.post("/api/prototype/wizard/level-up/preview")
    async def level_up_preview_route(body:WizardInput):
        return await asyncio.to_thread(level_up_preview,body)

    @app.get("/api/campaigns/{campaign}/level-up")
    async def level_up_state(campaign:str):
        """The advancement the campaign has earned, and the draft it applies to."""
        def read():
            from .nat20_wizard import WizardDraft, contract, level_up_draft

            current=store.campaign(campaign)
            capabilities=Nat20GameEngine.capabilities(current)
            version=current["version"]
            if not capabilities["experimental"]:
                return dict(available=False,version=version,
                    reason="Повышение уровня доступно для собранного Nat20-героя.")
            earned=_earned(current)
            build=dict(capabilities["build"])
            # The hero already has a name and a backstory; the advancement screen
            # must not ask for them again, so the draft carries what exists.
            hero=current["state"].get("hero") or {}
            draft=WizardDraft(
                name=str(hero.get("name") or ""),
                backstory=str(hero.get("background") or ""),
                species_slug=build.get("species_slug"),
                class_slug=build.get("class_slug"),
                subclass_slug=build.get("subclass_slug"),
                background_slug=build.get("background_slug"),
                level=int(build.get("level") or 1),
                ability_score_method=build.get("ability_score_method"),
                ability_scores=dict(build.get("ability_scores") or {}),
                selected_choices=list(build.get("selected_choices") or ()),
                prepared_spells=list(capabilities["prepared_spells"]),
                equipment=list(build.get("equipment") or ()),
                advancement=True,
            )
            runtime=current["state"]["nat20"]
            if runtime.get("combat"):
                return dict(available=False,version=version,earned=earned,level=draft.level,
                    reason="Повышение уровня недоступно во время боя.")
            if earned<=draft.level:
                return dict(available=False,version=version,earned=earned,level=draft.level,
                    reason="Нет доступного повышения: опыт соответствует уровню "+str(earned)+".")
            advanced=level_up_draft(draft)
            return dict(available=True,version=version,earned=earned,
                **advanced,contract=contract(advanced["draft"]))
        return await asyncio.to_thread(read)

    @app.post("/api/campaigns/{campaign}/level-up")
    async def level_up_apply(campaign:str,body:LevelUpInput):
        """Commit one advancement atomically; a replay grants nothing new.

        The draft's ``level`` is the transition the screen was shown, not a
        destination the client may pick: the engine advances the hero by one
        level and refuses a draft that names any other level (owner, L03).
        """
        from .nat20_wizard import WizardDraft, keep_settled_picks, resolve_answers

        previous=WizardDraft.model_validate(body.draft)
        resolved,_problems=resolve_answers(previous,body.answers)
        # A level-up answers only what the NEW level opens; the hero's own earlier
        # picks live in the sent draft and must survive the rebuild, or the engine
        # sees a hero that never chose its skills and refuses the advancement
        # (``keep_settled_picks`` also replaces the answer a player edited).
        draft=keep_settled_picks(previous,body.answers,resolved).model_dump(mode="json")
        return await asyncio.to_thread(Nat20GameEngine(store).level_up,campaign,
            request_id=body.request_id,expected_version=body.expected_version,
            class_slug=draft.get("class_slug"),
            selected_choices=tuple(draft.get("selected_choices") or ()),
            subclass_slug=draft.get("subclass_slug"),
            prepared_spells=tuple(draft.get("prepared_spells") or ()),
            target_level=draft.get("level"))
    @app.get('/api/prototype/catalog/{category}')
    async def catalog(category:str,query:str='',limit:int=50,offset:int=0):
        return await asyncio.to_thread(Nat20GameEngine.catalog,category,query,limit,offset)

    @app.get('/api/prototype/catalog/{category}/{slug}')
    async def catalog_detail(category:str,slug:str):
        return await asyncio.to_thread(Nat20GameEngine.catalog_detail,category+':'+slug)

    @app.post('/api/prototype/build/preview')
    async def preview(body:BuildInput):
        return await asyncio.to_thread(build_preview,body)

    @app.post('/api/prototype/campaigns')
    async def create(body:NewGameInput):
        if not body.name.strip():raise ValueError('Укажите имя героя.')
        if body.adventure not in ('sandbox','authored') and not body.adventure.startswith('prepared:'):raise ValueError('Выберите песочницу или своё приключение.')
        # Authored starting positions, not a path/range or legality calculation.
        positions=[dict(x=5,y=1,z=0),dict(x=5,y=3,z=0),dict(x=5,y=5,z=0),dict(x=5,y=7,z=0)]
        encounter=dict(grid=dict(width=10,height=10),hero_position=dict(x=1,y=1,z=0),
            actors=[dict(id='encounter-'+str(i),monster=slug,position=positions[i]) for i,slug in enumerate(body.encounter_monsters)])
        for actor in encounter['actors']:
            slug=actor['monster']
            if slug in body.encounter_attack_bonuses:
                note=body.encounter_attack_sources.get(slug,'').strip()
                if not note:raise ValueError('Укажите источник бонуса атаки противника: '+slug)
                actor.update(attack_bonus=body.encounter_attack_bonuses[slug],attack_bonus_source=dict(
                    kind='authored_scenario',id='player-encounter-input',version='1',note=note))
        encounters={'opening':encounter} if encounter['actors'] else {}
        async with game.operation:
            signature=body.model_dump_json(exclude={'request_id'})
            notice=book=None
            if body.request_id in created:
                old_signature,result=created[body.request_id]
                if signature!=old_signature:raise ValueError('Запрос создания уже использован с другим выбором.')
                return result
            # The book is attached after the campaign exists, so the draft is
            # checked before anything is created: a refused start must not leave
            # an orphan campaign. A replay of the same request returned above, so
            # a lost answer is still retried with the same id instead of refused.
            if body.preparation_draft and not body.adventure.startswith('prepared:'):
                started=app.state.preparation.get(body.preparation_draft)
                if started['campaign']:
                    raise ValueError('Этот черновик уже начал кампанию; откройте её в «Моих приключениях».')
                from .creation_handoff import record as handoff_record, verify as verify_handoff
                verify_handoff(handoff_record(app.state.preparation,body.preparation_draft,library=app.state.adventures),
                    module_id=body.module_id,hero_draft=body.hero_draft,equipment=body.equipment)
            if body.adventure.startswith('prepared:'):
                from .prototype_prepared import launch_prepared
                if body.adventure_revision is None:raise ValueError('Обновите список подготовленных приключений.')
                preparation=app.state.preparation;draft_id=body.adventure.removeprefix('prepared:')
                draft=preparation.get(draft_id);revision=body.adventure_revision;staged=None
                from .creation_handoff import record as handoff_record, verify as verify_handoff
                verify_handoff(handoff_record(preparation,draft_id,library=app.state.adventures),
                    module_id=body.module_id,hero_draft=body.hero_draft,equipment=body.equipment)
                if draft['body'].get('flow')=='simple':
                    if body.mechanics_ruleset!='dnd2024' or not body.rules_confirmed:
                        raise ValueError('Явно выберите и подтвердите механику D&D2024; редакция источника сохранится.')
                    if not body.hero_confirmed or not body.background_confirmed:
                        raise ValueError('Подтвердите выбранный Nat20-лист и предысторию героя отдельно от мира.')
                    if not draft['campaign']:
                        if draft['revision']!=revision:raise ValueError('Подготовка изменилась. Обновите выбранный старт.')
                        from .prototype_prepared import prepared_start_readiness
                        if not prepared_start_readiness(preparation,draft_id)['ready']:
                            raise ValueError('Сначала подтвердите проверенный мир; списка имён недостаточно.')
                        preview=await asyncio.to_thread(Nat20GameEngine.preview_build,body.build.model_dump(mode='json'),body.prepared_spells)
                        from .preparation import Draft
                        confirmed=Draft.model_validate(draft['body'])
                        narrative={key:confirmed.hero[key] for key in ('personality','appearance','goals','bonds','ideals','alignment') if key in confirmed.hero}
                        confirmed.hero=dict(narrative,name=body.name.strip(),
                            class_name=next(iter(preview['build']['classes'])),species=preview['build']['species_slug'],background=body.hero_background)
                        confirmed.background_confirmed=True
                        if confirmed.model_dump(mode='json')!=draft['body']:
                            staged=draft
                            draft=preparation.save(draft_id,confirmed,revision);revision=draft['revision']
                    elif draft['body']['hero'].get('name')!=body.name.strip() or draft['body']['hero'].get('background','')!=body.hero_background:
                        raise ValueError('Этот мир уже запущен с другим подтверждённым героем или предысторией.')
                try:
                    launched=await asyncio.to_thread(launch_prepared,preparation,draft_id,
                        confirm=True,expected_revision=revision,build=body.build.model_dump(mode='json'),
                        prepared_spells=body.prepared_spells,encounters=encounters,
                        checks=body.typed_checks(),
                        mechanics_ruleset=body.mechanics_ruleset,rules_confirmed=body.rules_confirmed)
                except Exception:
                    if staged:
                        # Undo only our uncommitted identity step. A published world keeps
                        # its existing cross-database recovery/linkage protocol.
                        with store.connection() as db:
                            installed=db.execute("SELECT 1 FROM campaigns WHERE json_extract(state,'$.preparation.draft')=?",(draft_id,)).fetchone()
                        if not installed:
                            with preparation.connection() as db:
                                db.execute('UPDATE drafts SET body=?,revision=?,updated=? WHERE id=? AND revision=? AND campaign IS NULL',
                                    (json.dumps(staged['body'],ensure_ascii=False),staged['revision'],staged['updated'],draft_id,revision))
                    raise
                result=launched['campaign']
            else:
                result=await asyncio.to_thread(Nat20GameEngine(store).create_experimental_campaign,body.name.strip(),
                build=body.build.model_dump(mode='json'),prepared_spells=body.prepared_spells,
                encounters=encounters,title=body.name.strip(),background=body.hero_background,
                description=body.description or ('Открытая песочница.' if body.adventure=='sandbox' else 'Приключение по описанию игрока.'))
            # A genuinely prepared world already carries its own scene; only a
            # campaign that starts without one is given the book's opening page.
            if body.preparation_draft and not body.adventure.startswith('prepared:'):
                from .prototype_prepared import bind_library_module
                bound=await asyncio.to_thread(bind_library_module,app.state.preparation,result['id'],body.preparation_draft)
                if bound:
                    notice=bound.get('notice');book={k:v for k,v in bound.items() if k!='notice'} if bound.get('bound') else None
            # A03/F01: the items the master approved are the hero's own kit in the
            # new story. Both launch branches carry them with the same record the
            # hero and the start came from — one contract, not one per screen.
            carried_draft=(body.preparation_draft or
                           (body.adventure.removeprefix('prepared:') if body.adventure.startswith('prepared:') else ''))
            carried=[]
            if carried_draft:
                from .creation_handoff import carry
                carried=await asyncio.to_thread(carry,store,result['id'],app.state.preparation,carried_draft)
            response=dict(id=result['id'],name=result['name'],backend='nat20',**({'notice':notice} if notice else {}),**({'book':book} if book else {}),**({'equipment':carried} if carried else {}))
            created[body.request_id]=(signature,response)
            return response

    @app.post('/api/campaigns/{campaign}/party')
    async def add_party_member(campaign:str,body:CompanionInput):
        """Спутник входит в партию (r214), лист считает Nat20.

        Тот же редактор, что у героя: тело — `build` и `prepared_spells`,
        ровно как у создания кампании. Уровень спутника приводится к уровню
        героя движком, а не подписью на экране.
        """
        async with game.operation:
            result=await asyncio.to_thread(Nat20GameEngine(store).add_companion,campaign,
                name=body.name.strip(),build=body.build.model_dump(mode='json'),
                prepared_spells=body.prepared_spells,choices=body.choices,traits=body.traits,
                request_id=body.request_id or None)
        return result

    @app.get('/api/campaigns/{campaign}/party/candidates')
    async def party_candidates(campaign:str):
        """Кого можно взять с собой (r218): люди места, где стоит герой.

        Чтение только: имена, что материал говорит об их классе, и не ходят ли
        они уже в партии. Ничего не записывает, поэтому экран может спрашивать
        без последствий.
        """
        return await asyncio.to_thread(Nat20GameEngine.npc_candidates,store.campaign(campaign))

    @app.post('/api/campaigns/{campaign}/party/npc')
    async def recruit_party_npc(campaign:str,body:NpcRecruitInput):
        """NPC материала становится спутником (r218): одно событие с причиной.

        Класс читается из напечатанного предложения о нём, уровень — уровень
        героя, лист считает Nat20. Если класс в материале не назван, спутник
        всё равно вступает, но запись честно говорит «параметры не из книги».
        """
        async with game.operation:
            result=await asyncio.to_thread(Nat20GameEngine(store).recruit_npc,campaign,
                npc=body.npc,reason=body.reason,class_slug=body.class_slug,
                species_slug=body.species_slug,traits=body.traits,
                request_id=body.request_id or None)
        return result

    @app.delete('/api/campaigns/{campaign}/party/{member}')
    async def remove_party_member(campaign:str,member:str,reason:str='left'):
        """Спутник выбывает (r218): ушёл, погиб, предал — с причиной в журнале."""
        async with game.operation:
            result=await asyncio.to_thread(Nat20GameEngine(store).remove_companion,campaign,member,reason=reason)
        return result

    @app.get('/api/campaigns/{campaign}/settings')
    async def campaign_rules(campaign:str):
        """Правила кампании, которые выбирает игрок (r217): смерть, сложность, награды."""
        return await asyncio.to_thread(Nat20GameEngine.settings,store.campaign(campaign))

    @app.put('/api/campaigns/{campaign}/settings')
    async def save_campaign_rules(campaign:str,body:CampaignRulesInput):
        """Смена правила — рука игрока и одно событие в журнал (r217)."""
        async with game.operation:
            return await asyncio.to_thread(Nat20GameEngine(store).set_settings,campaign,
                death=body.death,difficulty=body.difficulty,rewards=body.rewards,
                request_id=body.request_id or None)

    @app.get('/api/campaigns/{campaign}/prototype')
    async def choices(campaign:str):
        return await asyncio.to_thread(player_options,store.campaign(campaign))
