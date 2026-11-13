"""Public Python-only Solo/Nat20 seam. No UI, LLM, new DB or combat mathematics.

Nat20 has no durable handle API. Each operation replays its bounded JSON recipe
in a fresh process, verifies the prior complete read-model, and commits through
Solo's existing transaction. A failed worker or failed commit leaves no live
provider state which could leak into a retry.
"""
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys

logger = logging.getLogger(__name__)

from dnd5e_engine import PartyMemberSpec, parse_cell

from .check_text import check_text
from .event_display import display_text, native_sentence, provoked_attacks
from .command_engine import CommandEngine, StateVersionError
from .game_commands import GameCommand as SoloGameCommand
from .contracts import Hero
from .engine_memory_contracts import pair
from .nat20_content import CHECKS, ROOT, canonical, digest
from .nat20_contracts import DurableState, GameCommand, GameEvent, GameSnapshot
from .source_checks import current_checks
from .nat20_campaign import load_definition, validate_definition
from .rulesets import BUILTINS, active


def _weapon_ids():
    from .nat20_content import weapons
    return weapons()


#: Отказы движка, которые доходили до игрока английским текстом. Переводятся
#: только те, что действительно видны на экране, и всегда с путём восстановления:
#: причина без действия — это тот же отказ, только вежливее.
_ENGINE_REFUSALS = (
    ('Pinned engine/content release changed',
     'Ревизия содержимого кампании не совпадает с закреплённой, а миграция не подтверждена: '
     'движок не подменяет правила молча. Обновите кампанию («Сделать копию и обновить») и повторите действие.'),
    ('Spell capability is not registered for this campaign',
     'Это заклинание не зарегистрировано в кампании: движок не подставляет его сам. '
     'Выберите другое действие или добавьте заклинание в сборку героя.'),
    ('Class feature is not registered for this encounter',
     'Это умение не зарегистрировано в текущей сцене: выбор не потрачен. '
     'Выберите другое действие.'),
    ('No active Nat20 combat',
     'Это действие доступно только в бою: сейчас боя нет. Начните столкновение — '
     'тогда появятся «Уклонение», «Отход» и «Рывок».'),
    ('no active combat',
     'Это действие доступно только в бою: сейчас боя нет. Начните столкновение и повторите.'),
    ('Encounter is not in the registered campaign',
     'Этого столкновения нет в зарегистрированном сценарии кампании: бой не начат. '
     'Выберите столкновение из списка сцены.'),
    ('Hero is not the current actor',
     'Сейчас ход не героя: передайте ход, противник отыграет по правилам, и только потом действуйте.'),
    ('Combat has ended',
     'Бой уже завершён: закройте его, прежде чем начинать новый.'),
    ('Cannot start another combat in this state',
     'Новый бой сейчас начать нельзя: сначала завершите текущий.'),
    ('Unknown or non-hostile target',
     'Цель не выбрана или не является противником: действие не потрачено. Выберите цель в бою.'),
    ('Combat replay limit',
     'Бой слишком длинный для восстановления этим циклом: сохраните кампанию и начните новый бой.'),
)


#: Отказы сверки журнала с сохранением: у них после двоеточия идёт перечень
#: разошедшихся полей с числами. Рамка — русская, перечень сохраняется: без него
#: игроку (и нам) нечего показать разработчику.
_ENGINE_DIVERGENCE = (
    ('Nat20 durable snapshot does not match replay',
     'Сохранённое состояние боя не совпадает с пересчётом журнала: загрузка отменена, '
     'состояние не изменено. Разошлось: '),
    ('Nat20 replay diverged',
     'Пересчёт журнала боя разошёлся с сохранением: загрузка отменена, состояние не изменено. Разошлось: '),
    ('Nat20 initial replay diverged',
     'Стартовое состояние боя пересчитано иначе, чем записано: загрузка отменена, '
     'состояние не изменено. Разошлось: '),
)



#: Имя действия по-русски для журнала, когда движок вызван напрямую (без экрана,
#: который присылает своё «Проверка»/«Рывок»). Раньше здесь стояло
#: ``"Nat20 " + kind`` — и в ленте игрока появлялась английская строка
#: «Nat20 check», хотя это не отказ движка, а подпись записи.
_ACTION_LABELS = {
    'check': 'Проверка', 'start_combat': 'Начать бой', 'move': 'Перемещение',
    'attack': 'Атака', 'end_turn': 'Закончить ход', 'end_combat': 'Завершить бой',
    'short_rest': 'Короткий отдых', 'long_rest': 'Длительный отдых',
    'dash': 'Рывок', 'dodge': 'Уклонение', 'disengage': 'Отход',
    'stand_up': 'Встать', 'use_feature': 'Умение героя', 'cast_spell': 'Заклинание',
    'drop_concentration': 'Прекратить концентрацию', 'native_action': 'Действие героя',
    'scene_action': 'Действие сцены', 'finish': 'Завершить бой', 'npc_turn': 'Ход противника',
    'observe': 'Осмотр сцены', 'interact': 'Взаимодействие', 'search': 'Поиск',
    'rest': 'Отдых', 'stabilize': 'Стабилизация', 'temp_hp': 'Временные хиты',
    'inspiration': 'Героическое вдохновение',
}


def user_refusal(text, default='Движок отклонил действие: состояние не изменено.'):
    """Причина отказа так, как её читает игрок: по-русски и с действием.

    Английская проза движка на экран не попадает: она уходит в журнал
    (причина не теряется), а игроку остаётся русская рамка с машинным кодом,
    если движок его назвал.
    """
    message = str(text or '').strip() or default
    for marker, frame in _ENGINE_DIVERGENCE:
        if marker in message:
            tail = message.split(': ', 1)[1].strip() if ': ' in message else ''
            return frame + (tail or 'поля не названы.')
    for marker, russian in _ENGINE_REFUSALS:
        if marker in message:
            return russian
    if not any('а' <= letter.lower() <= 'я' or letter in 'ёЁ' for letter in message):
        # Причина не теряется: без неё отказ превращается в вежливую заглушку и
        # разбирать его потом нечем (это уже стреляло — «Диагноз отказа по
        # вежливой фразе хоста» в скилле). Рамка русская, сама причина — как её
        # назвал движок, рядом; в журнал она уходит отдельной строкой.
        logger.warning('Nat20 declined in English: %s', message)
        return default + ' Причина от движка: ' + message
    return message


def targets_of(current,runtime):
    """Кого сейчас можно назвать целью — и кого нельзя.

    F13: вне боя список целей был `['hero']`. Экран и разбор реплики получали
    героя единственной допустимой мишенью, поэтому «атакую» без цели выглядело
    законным, а целью числился сам герой. Вне боя целью может быть только тот,
    кто стоит в этой сцене; герой остаётся действующим лицом, а не мишенью.
    В бою список прежний — инициатива, и «hero» в ней законная цель заклинания.
    """
    if runtime.get('combat'):
        return [row['entity_id'] for row in runtime['combat']['view']['initiative']]
    state=(current or {}).get('state') or {}
    place=next((p for p in state.get('locations') or [] if p.get('name')==state.get('location')), None) or {}
    names=[]
    for person in place.get('npc') or []:
        if not person.get('present',True):
            continue
        identity=str(person.get('id') or person.get('name') or '').strip()
        if identity and identity not in names:
            names.append(identity)
    return names


class Nat20GameEngine:
    """Trusted server API. Caller must authorize campaign access before invoking.

    Existing campaigns never opt in implicitly. create_campaign makes a new
    explicit Nat20 campaign; ordinary Solo campaigns are refused, not migrated.
    """
    def __init__(self, store):
        self.store = store

    visible = staticmethod(CommandEngine.visible)

    @staticmethod
    def catalog(category=None, query='', limit=50, offset=0):
        from .nat20_prototype import catalog
        return catalog(category,query,limit,offset)

    @staticmethod
    def catalog_detail(identity):
        from .nat20_prototype import catalog_detail
        return catalog_detail(identity)

    @staticmethod
    def preview_build(build, prepared_spells=()):
        from .nat20_prototype import preview_build
        return preview_build(build,prepared_spells)

    @staticmethod
    def build_schema():
        from dnd5e_engine import CharacterBuildSpec
        return CharacterBuildSpec.model_json_schema()

    @staticmethod
    def monster_attack_profile(slug):
        from .nat20_content import release
        from .nat20_prototype import RELEASE
        from .nat20_monsters import attack_profile
        release(RELEASE)
        return attack_profile(slug)

    def create_experimental_campaign(self,name,*,build,encounters,checks=None,prepared_spells=(),seed=None,episode_id=None,
                                     title='Experimental campaign',description='Experimental Nat20 campaign',
                                     background='',progression=None):
        from .nat20_prototype import make_definition
        if not isinstance(name,str) or not 1<=len(name)<=80:
            raise ValueError('Name must be 1..80 characters')
        seed=secrets.randbits(63) if seed is None else seed
        if type(seed) is not int or not 0<=seed<2**63:raise ValueError('Invalid local RNG seed')
        definition=make_definition(build,encounters,checks or {},prepared_spells,title,description,progression=progression,episode_id=episode_id)
        runtime=self._worker(dict(mode='initialize',name=name,seed=seed,definition=definition.model_dump(mode='json')))['state']
        initial=dict(location=title,turn=0,facts=[],deferred=[],
            locations=[dict(name=title,description=description,npc=[],objects=[],facts=[])],nat20=runtime)
        hero=self._hero(runtime)
        # A free-text backstory is the hero's own knowledge, not world text. It is
        # stored on the hero and confirmed here so the GM packet carries it with
        # provenance from the first turn instead of an unlabelled prompt string.
        note=str(background or '').strip()
        if note:
            hero=hero.model_copy(update={'background':note[:2000]})
            initial['preparation']=dict(background_confirmed=True,source='hero-backstory')
        return self.store.create(name,hero,BUILTINS['dnd2024'],initial_state=initial)

    @staticmethod
    def party(current):
        """The campaign party as the screen reads it (r214)."""
        from .nat20_party import projection
        return projection(Nat20GameEngine._bound(current))

    def add_companion(self, campaign, *, name, build, prepared_spells=(), choices=None, request_id=None, traits=()):
        """One companion joins through the worker that owns the sheet.

        The derivation runs in the disposable Nat20 process on the campaign's
        own pinned release, so a companion cannot carry numbers the rest of the
        game does not agree with. The join is one journal event with its
        before/after state, like every other accepted turn.

        ``traits`` (r216) is the companion's character: 2-4 of ``cautious``,
        ``loyal``, ``greedy``, ``proud`` (осторожный, верный, жадный, гордый);
        the host decides his answers from it and from the relation the campaign
        keeps, never from the narrator's mood.
        """
        current = self.store.campaign(campaign)
        Nat20GameEngine._bound(current)
        member = {"name": name, "build": build, "prepared_spells": list(prepared_spells or ()),
                  "choices": dict(choices or {}), "traits": list(traits or ())}
        joined = {}

        def transition(state):
            runtime = DurableState.model_validate(state['nat20'])
            result = self._worker({"mode": "party_add", "state": runtime.model_dump(mode="json"),
                                   "member": member})
            state['nat20'] = result['state']
            joined.update(result['member'])
            return [dict(type='scripted', text='Спутник ' + str(result['member']['name'])
                         + ' присоединился к партии')]

        from .storage import uid
        event = self.store.record_scenario(campaign, request_id or 'party-' + uid(),
                                           'Спутник присоединяется', transition)
        return dict(party=Nat20GameEngine.party(self.store.campaign(campaign)),
                    member=joined.get('id'), event=event['id'])

    def remove_companion(self, campaign, member, *, reason='left', request_id=None):
        """A companion leaves the party; the hero never leaves it this way.

        r218: leaving is a journal event with its reason (``left``, ``died``,
        ``betrayed``), not a row that disappears. The party shrinks through the
        same worker rule that refuses a departure mid-fight, and the NPC of the
        material comes back to his place when he merely walked away.
        """
        if member == 'hero':
            raise ValueError('Главный герой не выбывает из своей партии.')
        current = self.store.campaign(campaign)
        Nat20GameEngine._bound(current)
        left = {}

        def transition(state):
            runtime = DurableState.model_validate(state['nat20'])
            result = self._worker({"mode": "party_leave", "state": runtime.model_dump(mode="json"),
                                   "member": member, "reason": reason})
            state['nat20'] = result['state']
            left.update(result['member'])
            self._return_npc(state, result['member'], reason)
            from .nat20_recruit import leave_sentence
            line = leave_sentence(result['member'], reason)
            return [dict(type='scripted', kind='npc_left_party', text=line, events=result['events'],
                         npc_left_party=dict(result['events'][0], member=str(result['member'].get('id'))))]

        from .storage import uid
        event = self.store.record_scenario(campaign, request_id or 'party-leave-' + uid(),
                                           'Спутник выбывает', transition)
        return dict(party=Nat20GameEngine.party(self.store.campaign(campaign)),
                    member=member, event=event['id'])

    @staticmethod
    def _return_npc(state, member, reason):
        """The person behind a departed companion: back in his place, or not.

        Only a companion who walked away stands where the party found him again.
        One who died stays dead - putting him back would let the table meet him
        twice - and one who betrayed the party is out of the scene on purpose.
        """
        npc_id = str((member.get('origin') or {}).get('npc_id') or '')
        if not npc_id:
            return
        for place in state.get('locations') or []:
            for person in place.get('npc') or []:
                if Nat20GameEngine._npc_reference(person) != npc_id:
                    continue
                person.pop('party_member', None)
                person['present'] = reason == 'left'
                return

    @staticmethod
    def npc_candidates(current):
        """Who stands beside the hero and could join (r218), with the book's words."""
        from .nat20_recruit import candidates
        return candidates(current['state'])

    def recruit_npc(self, campaign, *, npc, reason, class_slug='', species_slug='', traits=(), request_id=None):
        """An NPC of the material becomes a companion, in one journal event.

        The class comes from the printed sentence about him when that sentence
        names one next to his own name; his level is the hero's level (r214); and
        when the material names no class he still joins, on a minimal legal sheet
        the record itself labels «параметры не из книги». The sheet is derived by
        the same disposable Nat20 process that owns every other sheet, so the
        companion cannot carry numbers the rest of the game disagrees with.
        """
        from .nat20_recruit import NOT_FROM_BOOK, build_for, describe, join_sentence, read_hint

        current = self.store.campaign(campaign)
        Nat20GameEngine._bound(current)
        if reason not in ('rescued', 'hired', 'asked'):
            raise ValueError('Причина вступления — rescued, hired или asked: ' + str(reason))
        joined = {}

        def transition(state):
            place, person = Nat20GameEngine._find_npc(state, npc)
            reference = Nat20GameEngine._npc_reference(person)
            if place.get('name') != state.get('location'):
                raise ValueError('Взять с собой можно того, кто стоит рядом: ' + str(person.get('name'))
                                 + ' сейчас в другом месте (' + str(place.get('name')) + ').')
            already = next((item for item in (state['nat20'].get('party') or [])
                            if str((item.get('origin') or {}).get('npc_id') or '') == reference), None)
            if already is not None:
                raise ValueError(str(person.get('name')) + ' уже в партии: ' + str(already.get('id')))
            runtime = DurableState.model_validate(state['nat20'])
            level = int(((runtime.definition.model_dump(mode='json') if hasattr(runtime.definition, 'model_dump')
                          else runtime.definition) or {}).get('build', {}).get('level') or 1)
            hint = read_hint(person.get('name') or '', person.get('character') or '',
                             person.get('biography') or '', person.get('known') or '',
                             *list(person.get('private_knowledge') or ()))
            build = build_for(hint, level, class_slug=class_slug, species_slug=species_slug)
            member = dict(name=person.get('name') or npc, build=build, traits=list(traits or ()),
                          reason=reason,
                          origin=dict(npc_id=reference, place=str(place.get('name') or ''),
                                      from_book=bool(hint.get('from_book')),
                                      evidence=hint.get('evidence') or '',
                                      printed_level=hint.get('printed_level'),
                                      class_slug=build.get('class_slug') or '',
                                      species_slug=build.get('species_slug') or '',
                                      sheet=describe(hint, level),
                                      note='' if hint.get('from_book') else NOT_FROM_BOOK))
            result = self._worker({"mode": "party_recruit", "state": runtime.model_dump(mode="json"),
                                   "member": member})
            state['nat20'] = result['state']
            # The person leaves the scene with the party: the place must not show
            # him standing at the bar while his sheet walks in the party.
            person['present'] = False
            person['party_member'] = result['member']['id']
            joined.update(result['member'])
            line = join_sentence(result['member'], reason)
            block = dict(result['events'][0], id=result['member']['id'],
                         sheet=member['origin']['sheet'], level=level)
            return [dict(type='scripted', kind='npc_joined_party', text=line,
                         events=result['events'], npc_joined_party=block)]

        from .storage import uid
        event = self.store.record_scenario(campaign, request_id or 'party-join-' + uid(),
                                           'НСП вступает в партию', transition)
        return dict(party=Nat20GameEngine.party(self.store.campaign(campaign)),
                    member=joined.get('id'), event=event['id'],
                    origin=joined.get('origin'))

    @staticmethod
    def _npc_reference(person):
        """How a person of a place is addressed: his id, or his name.

        ``world.import_package`` gives every imported NPC an id, while the
        opening scene installed by ``bind_library_module`` copies the book's
        people without one. The world contract already requires names to identify
        a person uniquely («Имена новых NPC должны однозначно определять
        личность»), so the name is a reference the state itself enforces - the id
        is preferred only because it survives a rename.
        """
        return str(person.get('id') or person.get('name') or '')

    @staticmethod
    def _find_npc(state, npc):
        """The person of a place this reference addresses, or a refusal naming who is there."""
        wanted = str(npc or '').strip()
        if not wanted:
            raise ValueError('Не назван NPC, который должен вступить в партию.')
        for place in state.get('locations') or []:
            for person in place.get('npc') or []:
                if wanted in (str(person.get('id') or ''), str(person.get('name') or '')):
                    return place, person
        names = ', '.join(str(person.get('name')) for place in state.get('locations') or []
                          for person in place.get('npc') or [])
        raise ValueError('Такого NPC в мире кампании нет: ' + wanted
                         + ('. В мире: ' + names if names else '. В мире никого нет.'))

    @staticmethod
    def settings(current):
        """The three campaign rules the player owns, as the screen reads them (r217)."""
        from .nat20_settings import describe, rules_of
        runtime = Nat20GameEngine._bound(current)
        return describe(rules_of(runtime))

    def set_settings(self, campaign, *, death=None, difficulty=None, rewards=None, request_id=None):
        """One trusted write of the player's own rules, as one journal event (r217).

        A parameter left as ``None`` keeps its value, so the screen may save only
        what the player touched. The write is legal mid-campaign as well: the
        next fight is prepared with the new numbers, while a fight already in
        progress keeps the terms it was opened with (they live in its save).
        """
        from .nat20_settings import CampaignRules, change_sentence, describe, rules_of
        current = self.store.campaign(campaign)
        before = rules_of(Nat20GameEngine._bound(current))
        payload = before.model_dump(mode="json")
        for key, value in (("death", death), ("difficulty", difficulty), ("rewards", rewards)):
            if value is not None:
                payload[key] = value
        after = CampaignRules.model_validate(payload)
        if after == before:
            return dict(settings=describe(after), changed=False)
        in_fight = bool(current['state']['nat20'].get('combat'))

        def transition(state):
            runtime = DurableState.model_validate(state['nat20'])
            data = runtime.model_dump(mode="json")
            data['settings'] = after.model_dump(mode="json")
            state['nat20'] = DurableState.model_validate(data).model_dump(mode="json")
            line = change_sentence(before, after)
            if in_fight:
                line += ' Текущее столкновение ведётся по условиям, с которыми открылось.'
            return [dict(type='scripted', kind='settings', text=line)]

        from .storage import uid
        event = self.store.record_scenario(campaign, request_id or 'settings-' + uid(),
                                           'Правила кампании', transition)
        return dict(settings=describe(after), changed=True, event=event['id'])

    @staticmethod
    def _lethal_risk_declared(current):
        """Was the serious risk announced before the fight? (r217)

        The flag is the campaign's existing master switch
        (``Master.declared_lethal_risk``), not a second mechanism: the same
        checkbox the player already has is the agreement that lets an
        irreversible outcome stand.
        """
        # Read the one flag, not the whole profile: a master record with a key
        # this contract no longer knows must not break every command of the
        # campaign. The default of the flag is False, so absence is the answer.
        master = current.get('master')
        if not isinstance(master, dict):
            return False
        return bool(master.get('declared_lethal_risk'))

    @staticmethod
    def capabilities(current):
        from .nat20_prototype import experimental,spell_status,SHIPPED_NATIVE_OPERATIONS
        from .game_commands import NativeAction
        from typing import get_args
        runtime=Nat20GameEngine._bound(current)
        definition=runtime.get('definition') or {}
        return dict(release=runtime['release_digest'],experimental=experimental(definition),
            build=definition.get('build'),sheet=deepcopy(runtime['sheet']),
            prepared_spells=list(definition.get('prepared_spells',[])),
            equipment=list(definition.get('build',{}).get('equipment',[])),
            features=list(definition.get('features',[])),checks=list(definition.get('checks',{})),
            encounters=list(definition.get('encounters',{})),
            # r194: штатная кампания тоже принимает посчитанные движком нативные
            # действия, поэтому `native_action` есть в её списке, а
            # `native_operations` называет ровно разрешённые — раньше пустой
            # список прятал разницу между экраном и хостом.
            actions=['check','scene_action','start_combat','move','attack','dash','end_turn','end_combat','short_rest','long_rest','level_up','order','native_action']+
                ([] if experimental(definition) else ['use_feature']),
            # r216: the interpreter can only address a companion it can name, so
            # the party travels with the capabilities. Relation and traits are
            # what the host decides from - the model reads them, never writes.
            party=[dict(id=item.get('id'), name=item.get('name'), role=item.get('role'),
                        relation=item.get('relation'), traits=list(item.get('traits') or ()))
                   for item in runtime.get('party') or []],
            native_operations=list(get_args(NativeAction.model_fields['operation'].annotation)) if experimental(definition) else list(SHIPPED_NATIVE_OPERATIONS),
            spell_execution={s:spell_status(s) for s in definition.get('prepared_spells',[])},
            known_issues={'hide':'Native does not spend Action',
                'ready':'Native Ready resolves only a pre-armed spell reaction; a readied weapon attack spends the Action and is dropped without resolution',
                'bless':'Incorrect save bonus / concentration and recast defects',
                'rage':'Engine applies Rage (damage bonus and the three resistances) and spends a use; the host refuses heavy armour, a repeat activation and spells under Rage. Vendor remainder: armour does not end Rage, concentration is not dropped, and the Advantage-on-Strength keys are not read by the engine',
                'monster_attack_bonus':'New encounters auto-project uniform constant to-hit with public Native stat-block resolver. Only unrepresentable profiles still require explicit source or refuse; existing saved bindings do not migrate.',
                'spell_components':'Native component metadata is exposed; full V/S/M inventory and free-hand legality is not implemented',
                'build_choices':'Preparation counts and class/feat choices are gated by the wizard contract; the advanced catalogue form still accepts a hand-assembled sheet'},
            verified={'spell_lists':'The wizard offers a class only the spells its printed list names (integration/nat20/playable/class-spell-lists.json, 877 links, CC-BY-4.0), and only up to a slot the build really has',
                'extracted_opponents':'An opponent extracted from any book reaches play through the shared mapping: a resolved corpus monster becomes an encounter actor, and an unresolved name stays a visible adaptation instead of a lookalike',
                'extracted_rewards':'A printed reward becomes a priced rule only when the cited quote proves the number; otherwise it is reported as awaiting adaptation, never booked as zero',
                'entry_stage':'The prepared entry is re-verified for any document: the cited file must still hash to itself and the opening text to what was reviewed'},
            targets=targets_of(current,runtime))

    @staticmethod
    def _worker(payload):
        environment = dict(os.environ, SOLO_TEST="1", PYTHONDONTWRITEBYTECODE="1")
        invocation = [sys.executable, '--nat20-worker'] if getattr(sys, 'frozen', False) else [sys.executable, "-X", "utf8", "-B", "-m", "solo.nat20_worker"]
        process = subprocess.run(invocation,
            input=canonical(payload), text=True, encoding="utf-8", capture_output=True, cwd=ROOT,
            env=environment, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if process.returncode:
            raise ValueError("Движок не смог выполнить действие: состояние не изменено. Повторите; "
                             "если повторяется — сообщите шаг и время.")
        try:
            response = json.loads(process.stdout)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Движок ответил неразборчиво: состояние не изменено. Повторите действие.") from error
        if response.get("ok") is not True:
            raise ValueError(user_refusal(response.get("error", "Nat20 operation rejected")))
        DurableState.model_validate(response["state"])
        return response

    @staticmethod
    async def _worker_async(payload):
        """The same worker, off the event loop.

        _worker is a blocking subprocess.run of 1.5-3.5 seconds (start_combat
        measured at 3.55 s). Called directly from an async route it froze the
        whole server: every other request, including a plain state read, waited
        for a fight to start. Running it in a thread keeps the CPU work identical
        and only moves the wait off the loop.
        """
        import asyncio
        return await asyncio.to_thread(Nat20GameEngine._worker, payload)

    @staticmethod
    def _hero(runtime):
        from typing import get_args
        member = PartyMemberSpec.model_validate(runtime["hero"])
        sheet = runtime["sheet"]
        # Legacy Hero accepts actual conditions, not native tactical statuses
        # such as dodging/disengaging. All statuses remain in native state and
        # the existing generic GameSnapshot/effect projection.
        accepted=set(get_args(get_args(Hero.model_fields['conditions'].annotation)[0]))
        conditions = sorted({status for effect in runtime["active_effects"] if not effect.get("disabled")
                             for status in effect.get("statuses", []) if status in accepted})
        # Projection only: all derived numbers came from Nat20, never recomputed.
        definition = runtime.get('definition')
        build = definition['build'] if definition else dict(species_slug='human', class_slug='fighter', level=1)
        return Hero(name=member.name, species=build['species_slug'], class_name=build['class_slug'], level=build['level'],
            hp=member.hp_current, max_hp=member.hp_max, ac=member.ac,
            abilities={key: getattr(member, key) for key in Hero(name="keys").abilities},
            skill_proficiencies=list(sheet["skill_proficiencies"]), expertise=list(sheet["skill_expertise"]),
            saving_throw_proficiencies=[{"str": "strength", "dex": "dexterity", "con": "constitution",
                "int": "intelligence", "wis": "wisdom", "cha": "charisma"}[key] for key in sheet["save_proficiencies"]],
            speed_ft=member.base_speed, hit_die=runtime['hit_dice']['hit_die_size'], hit_dice_available=runtime["hit_dice"]["dice_remaining"],
            experience=runtime.get('experience', 0),
            features=list(sheet["features"]), conditions=conditions, exhaustion=runtime["exhaustion"])

    @staticmethod
    def _keep_own_words(previous, fresh):
        """Carry the hero's own words over the mechanical projection.

        ``_hero`` builds the record from the native runtime, and the runtime
        stores mechanics only: the backstory the player typed is not there, so
        replacing the record with the projection alone wiped the hero's history
        on every command and on every advancement (owner, L03: the advanced hero
        must stay the same character). An absent value is the one to fill - the
        native sheet stays the only source for every number.
        """
        old = (previous or {}).get("hero") or {}
        for field in ("background", "equipment"):
            if not fresh.get(field) and old.get(field):
                fresh[field] = old[field]
        return fresh

    def create_campaign(self, name, *, definition_id='fighter-road-2024-v1', seed=None, hp_current=None, conditions=(), second_wind_spent=0):
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError("Name must be 1..80 characters")
        seed = secrets.randbits(63) if seed is None else seed
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError("Invalid trusted local RNG seed")
        if hp_current is not None and (type(hp_current) is not int or not 1 <= hp_current <= 12):
            raise ValueError("Invalid initial HP")
        if not set(conditions) <= {"poisoned"} or type(second_wind_spent) is not int or not 0 <= second_wind_spent <= 2:
            raise ValueError("Unreviewed initial condition/resource")
        definition = load_definition(definition_id)
        request = {"mode": "initialize", "name": name, "seed": seed, 'definition': definition.model_dump(mode='json'),
                   "conditions": sorted(set(conditions)), "second_wind_spent": second_wind_spent}
        if hp_current is not None:
            request["hp_current"] = hp_current
        runtime = self._worker(request)["state"]
        # Explicit new empty scene, not START's example campaign or user data.
        initial = {"location": definition.title, "turn": 0, "facts": [], "deferred": [],
                   "locations": [{"name": definition.title, "description": definition.description,
                                  "npc": [], "objects": [], "facts": []}], "nat20": runtime}
        return self.store.create(name, self._hero(runtime), BUILTINS["dnd2024"], initial_state=initial)

    @staticmethod
    def _bound(current):
        if "nat20" not in current["state"]:
            raise ValueError("В кампании не выбран движок Nat20: она ведётся прежним циклом. "
                             "Действия Nat20 к ней неприменимы.")
        preset = active(current["state"])
        if preset.model_dump() != BUILTINS["dnd2024"].model_dump():
            raise ValueError("Кампания Nat20 закреплена за правилами D&D 2024: другой набор правил требует "
                             "явной миграции. Откройте кампанию с набором D&D 2024 или перенесите её "
                             "отдельной миграцией.")
        if current["state"].get("combat", {}).get("active"):
            raise ValueError("В кампании уже идёт бой прежнего цикла: завершите его или откатите ход, "
                             "прежде чем действие Nat20.")
        runtime = DurableState.model_validate(current["state"]["nat20"]).model_dump(mode="json")
        if runtime['definition'] is not None:
            validate_definition(runtime['definition'])
        return runtime

    @staticmethod
    def _scene_checks(current):
        """The book-printed checks of the place the hero stands in.

        Kept out of the campaign definition on purpose: that definition carries
        its own ``definition_sha256`` and PrototypeDefinition refuses a changed
        author definition outright. The scene travels beside the state instead,
        into the same worker that rolls the die.

        Entries are built here but validated in the worker, by the very model it
        parses them with: a malformed check must refuse that one roll with the
        field that is wrong, and must not take movement or attacks down with it.
        """
        from .source_checks import registry
        return registry(current['state'])

    @staticmethod
    def _social(current, command):
        """Что хост знает об отношении НИПа для этой команды (r179).

        Влияние адресуется существу, а преимущество за дружелюбие печатает
        книга (стр. 18). Отношение живёт в состоянии кампании, которого у
        движка нет, поэтому хост приносит его в рабочий процесс сам. Пустой
        словарь — не ошибка: кого убеждать, назовёт отказ `scene_actions.guard`
        до броска, а не случайный НИП сцены.
        """
        from .nat20_content import SCENE_ACTIONS
        from . import social
        action = command.action
        if getattr(action, 'kind', None) != 'scene_action' or action.action_id != 'influence':
            return {}
        try:
            npc = social.select(current['state'], action.npc_id)
        except ValueError:
            return {}
        attitude = social.attitude_of(npc)
        return dict(npc=npc.get('id') or npc.get('name'), attitude=attitude,
                    check_id=SCENE_ACTIONS['influence']['skills'][action.skill],
                    advantage=social.advantage(attitude))

    @staticmethod
    def snapshot(current, *, event_id=None, branch_id=None, sequence=None):
        state = current["state"]
        runtime = state["nat20"]
        combat = runtime["combat"]
        head = state.get("engine_snapshot", {})
        own = {"scope": "private", "known_by": ["hero"]}
        actors = []
        native = combat["view"] if combat else None
        from .nat20_prototype import experimental
        magical = experimental(runtime.get('definition')) or bool(runtime.get('definition', {}).get('spellcasting')) if runtime.get('definition') else False
        members = native["initiative"] if native else [dict(runtime["hero"], entity_id="hero")]
        for member in members:
            id = member["entity_id"]
            resource_access = {"scope": "private", "known_by": [id]}
            access = {"scope": "public", "known_by": ["hero", id] if id != "hero" else ["hero"]}
            hp = native["tracked_hp"].get(id, member["hp_current"]) if native else member["hp_current"]
            counters = native["custom_counters_by_entity"].get(id, {}) if native else member.get("custom_counters", {})
            resources = {key: {"current": value["spent"], **resource_access} for key, value in counters.items()}
            if id == "hero":
                resources["hit_dice"] = {"current": runtime["hit_dice"]["dice_remaining"], **own}
                if runtime.get('definition'):
                    resources['experience'] = {'current': runtime.get('experience', 0), **own}
                if runtime['exhaustion']:
                    resources['exhaustion']={'current':runtime['exhaustion'],**own}
                death=member.get('death_saves') or {}
                if death:
                    resources.update({'death_save:'+key:{'current':int(death.get(key,0)),**own}
                        for key in ('successes','failures','is_stable')})
                if native and id in native['dead_ids']:
                    resources['dead']={'current':int(id in native['dead_ids']),**own}
                if magical:
                    slots = native['spell_slots_by_entity'].get(id, {}) if native else member.get('spell_slots', {})
                    resources.update({'spell_slot:' + str(level): {'current': amount, **own} for level, amount in slots.items()})
                    concentrating = bool(native['concentration_chain'].get(id)) if native else bool(member.get('concentration_effect_id'))
                    resources['concentration'] = {'current': int(concentrating), **own}
            if native:
                for key in ("movement_remaining", "attacks_remaining", "action_available", "bonus_action_available", "reaction_available"):
                    if key in member:
                        resources[key] = {"current": int(member[key]), **resource_access}
                statuses = native["active_conditions"].get(id, [])
                zone = native["actor_zone"].get(id, runtime["hero"]["zone_id"])
            else:
                statuses = sorted({s for effect in runtime["active_effects"] for s in effect.get("statuses", [])})
                zone = member["zone_id"]
            x, y = parse_cell(zone)
            actors.append({"id": id, "kind": "hero" if id == "hero" else "npc", "name": member["name"],
                "hp": {"current": hp, "maximum": member["hp_max"], **access}, "resources": resources,
                "conditions": [{"id": s, **access} for s in statuses],
                "position": {"coordinates": (x, y, 0), **access}, **access})
        projected_effects = None
        if magical:
            projected_effects = []
            for effect in combat['active_effects'] if combat else runtime['active_effects']:
                if effect.get('disabled'):
                    continue
                limit = effect.get('duration') or {}
                caster=effect['origin'].rsplit(':',1)[-1]
                if caster not in {a['id'] for a in actors}:caster=effect['target_id']
                projected_effects.append(dict(id=effect['id'], origin=effect['origin'], target=effect['target_id'],
                    caster=caster, name=effect['name'], concentration=bool(effect.get('flags', {}).get('concentration')),
                    duration_limit=dict(maximum_rounds=limit.get('rounds'), maximum_seconds=limit.get('seconds')), **own))
        return GameSnapshot.model_validate({"campaign_id": current["id"],
            "event_id": event_id or head.get("event_id", "initial"), "branch_id": branch_id or head.get("branch_id", "main"),
            "sequence": sequence or head.get("sequence", 1), "location": state["location"], "actors": actors,
            "available_checks": sorted({*(runtime['definition']['checks'] if runtime.get('definition') else CHECKS),
                                        *(current_checks(state) if not combat else {})}) if not combat else [],
            # The engine resolves attacks for all 86 weapons; without this
            # list the table cannot name a dagger.
            'available_weapons': sorted(_weapon_ids()),
            'available_encounters': sorted(((runtime.get('definition') or {}).get('encounters') or {})),
            'effects': projected_effects,
            "combat": {"id": combat["initial"]["session_id"] if combat else "none", "active": bool(combat),
                "round": native["round_number"] if native else 0,
                "turn": native["initiative"][native["current_turn_index"]]["entity_id"] if native and not native["ended"] else None,
                **own}})

    def restore(self, campaign):
        current = self.store.campaign(campaign)
        runtime = self._bound(current)
        response = self._worker({"mode": "verify", "state": runtime})
        restored = response["state"]
        if restored != runtime:
            raise ValueError('Восстановленное состояние не совпало с сохранённым снимком: загрузка отменена, '
                             'журнал не изменён. Выберите другое сохранение.')
        # A fight saved on another reviewed engine revision is replayed on the
        # current rules instead of being refused; the worker names the numbers
        # that moved. Restore stays read-only - the save is re-bound by the next
        # committed command - so the diagnosis travels in the answer.
        return {"snapshot": self.snapshot(current), "runtime": deepcopy(runtime), "version": current["version"],
                "engine_migration": response.get("migration") or {}}

    @classmethod
    def _preflight(cls, current, command):
        """Shared guards for a committed command or a disposable screen probe."""
        runtime = cls._bound(current)
        state = current['state']
        if current['version'] != command.expected_version:
            raise StateVersionError('Состояние изменилось, пока вы действовали: ход не выполнен. '
                                    'Обновите сцену и повторите.')
        from .first_playable import require_full_scene
        require_full_scene(state)
        if runtime['hero']['hp_current'] <= 0:
            combat=runtime['combat']
            dead=bool(combat and 'hero' in combat['view']['dead_ids'])
            # r217: a hero the chosen rules would not let die is *out*, not dead.
            # He keeps 0 hit points until a long rest wakes him with 1 (SRD 5.2,
            # Stabilizing), and no other action bypasses that.
            out = runtime.get('out') or {}
            if not combat and out.get('reason') == 'death':
                raise ValueError('Герой погиб: смерть наступила по правилам кампании. '
                                 'Кампания им больше не продолжается — откатите ход '
                                 'или восстановите сохранение.')
            if not combat and out:
                if command.action.kind != 'long_rest':
                    raise ValueError('Герой выбыл из боя, но не умер: доступен только '
                                     'длительный отдых — он возвращает 1 хит и восстановление.')
            elif not combat or command.action.kind not in (('end_combat',) if dead else ('end_turn',)):
                raise ValueError('У героя 0 хитов: передайте ход — движок проведёт спасброски от смерти. '
                                 'Погибшего героя можно только закрыть боем (end_combat).')
        from .lantern_scene import guard
        guard(state, command.action)
        # A Study with nothing left to show is refused before the die is drawn.
        from .scene_actions import guard as scene_guard
        scene_guard(state, command.action)
        # L02: сверка уровня героя с книгой. Пока игрок не ответил, бой не
        # открывается: Мастер сначала называет оба числа. Это вопрос, а не
        # бросок, поэтому кампания от отказа не двигается.
        from . import level_gate
        level_gate.guard(state, command.action)
        # r175: условия умений базового класса, которых движок не проверяет —
        # тяжёлый доспех у Ярости, тип и дистанция цели у Изгнания нежити,
        # 60 фт и союзник у Бардовского вдохновения, известный зверь у Дикого
        # облика, выбранный вариант Сосредоточенности. Отказ до интента, с
        # причиной; прогон — tests/test_class_features_reach_game.py.
        from .nat20_content import feature_gate
        feature_gate(runtime, command.action)
        # A prepared scenario runs its own reviewed events through adventure_engine
        # after each committed command, so Nat20 actions are allowed inside it. The
        # list form of ``world.events`` is what the prepared world wrote down before
        # the Nat20 cycle owned the turn: those events are read as history and must
        # not lock the campaign (S61/S63: every action of «Тест Тиамат 08.11» died
        # here). Only a queue the old handler still holds open refuses, and it says
        # so in Russian with a way out.
        from .nat20_compat import blocking_world_events
        blocked = blocking_world_events(state)
        if blocked:
            raise ValueError(blocked['reason'])
        if command.action.kind == 'long_rest' and state.get('elapsed_minutes', 0) + 480 - state.get('last_long_rest', -1440) < 1440:
            raise ValueError('Длительный отдых уже был за последние сутки: следующий возможен после 24 часов игры. '
                             'Доступен короткий отдых или ход вперёд по времени.')
        return runtime

    def preview(self, current, command):
        """Read-only legality probe in a disposable worker; never writes a journal.

        Candidate outcomes/dice are discarded. Execution revalidates everything.
        """
        self.resolve(current, command)

    def resolve(self, current, command):
        """Pure Native result-list probe; disposable worker, no store or RNG writes."""
        command = GameCommand.model_validate(command.model_dump() if isinstance(command, SoloGameCommand) else command)
        runtime = self._preflight(current, command)
        response = self._worker({'mode': 'execute', 'state': runtime, 'command': command.model_dump(mode='json'),
                                 'session_id': current['id'], 'scene_checks': self._scene_checks(current),
                                 'social': self._social(current, command),
                                 'lethal_risk_declared': self._lethal_risk_declared(current)})
        return [dict(type='scripted',kind='nat20_command',native_events=response['events'],
                     native_result=response['result'])]

    def execute(self, campaign, command, *, original=None):
        command = GameCommand.model_validate(command.model_dump() if isinstance(command, SoloGameCommand) else command)
        # The journal/master use the shared command. Nat20's existing versioned
        # envelope remains only at the optional backend boundary and worker.
        shared = SoloGameCommand.model_validate(command.model_dump(exclude={"contract_version", "rules_version"}))
        signature = digest({"backend": "Nat20", "campaign": campaign, "command": command.model_dump(mode="json")})
        with self.store.connection(True) as db:
            current = self.store.campaign(campaign, db)
            runtime = self._bound(current)
            def transition(state):
                self._preflight(current, command)
                before = self.snapshot(current)
                response = self._worker({"mode": "execute", "state": runtime, "command": command.model_dump(mode="json"),
                                         "session_id": current["id"], "scene_checks": self._scene_checks(current),
                                         "social": self._social(current, command),
                                         # L02: игрок отказался пересобирать героя — бой
                                         # собирается от его уровня, а не от книги.
                                         "balance_override": current['state'].get('balance_override'),
                                         # r217: the terms of a fight are fixed when it opens.
                                         "lethal_risk_declared": self._lethal_risk_declared(current)})
                state["nat20"] = response["state"]
                state["hero"] = self._keep_own_words(state, self._hero(response["state"]).model_dump())
                # A fight saved on another reviewed engine revision is not
                # refused: the worker replayed it on the current rules and named
                # what moved. The sentence goes into the journal event (durable,
                # player-visible) and the record travels with the result.
                migration = response.get("migration") or ((response["result"] if isinstance(response["result"], dict)
                    else {}).get("engine_migration") or {})
                migration_text = migration.get("text") if migration.get("mode") in ("migrated", "unknown") else ""
                from .lantern_scene import confirmed
                scene_memories = confirmed(state, command.action, response, 'nat20:'+command.request_id)
                from .scene_actions import confirmed as scene_confirmed
                scene_outcome, scene_evidence = scene_confirmed(state, command.action, response,
                                                                'nat20:'+command.request_id)
                scene_memories += scene_evidence
                # A check answers with its own numbers: the same formatter the
                # legacy command path uses. "Nat20 confirmed: check" said nothing
                # the player could check against the dice.
                roll_text = (check_text(response['result'])
                             if command.action.kind == 'check' else '')
                if command.action.kind in ("short_rest", "long_rest"):
                    state["elapsed_minutes"] = state.get("elapsed_minutes", 0) + (60 if command.action.kind == "short_rest" else 480)
                    if command.action.kind == "long_rest":
                        state["last_long_rest"] = state["elapsed_minutes"]
                head = state.get("engine_snapshot")
                sequence = head["sequence"] + 1 if head else 1
                branch = head["branch_id"] if head else "main"
                used = db.execute("SELECT MAX(sequence) FROM memory_engine_events WHERE campaign=? AND branch_id=?", (campaign, branch)).fetchone()[0]
                if used is not None and used >= sequence:
                    branch += "-" + hashlib.sha256(command.request_id.encode()).hexdigest()[:12]
                event_id = "nat20:" + command.request_id
                after = self.snapshot(current, event_id=event_id, branch_id=branch, sequence=sequence)
                effects = CommandEngine.effects(before, after, [])
                if after.effects is not None:
                    known = {(e.target, e.id, e.origin): e.concentration for e in before.effects or []}
                    for native_effect in response['events']:
                        if native_effect['type'] == 'effect_applied':
                            e = native_effect['effect']; key = (e['target_id'], e['id'], e['origin'])
                            known[key] = bool(e.get('flags', {}).get('concentration'))
                            active = True
                        elif native_effect['type'] == 'effect_expired':
                            key = (native_effect['target_id'], native_effect['effect_id'], native_effect['origin'])
                            if key not in known:
                                raise ValueError('Native effect expiry has no known identity')
                            active = False
                        else:
                            continue
                        effects.append(dict(type='effect', payload=dict(target=key[0], effect_id=key[1],
                            origin=key[2], active=active, concentration=known[key])))
                if command.action.kind == "check" and response['result'].get('natural_roll') is not None:
                    check = response["result"]
                    # r226: the plaque prints «нужно · выпало · модификатор = итог»,
                    # so the numbers the engine already computed travel with the
                    # roll instead of being derived in the browser.
                    effects.append({"type": "roll", "payload": {"kind": "save" if check.get('kind')=='saving_throw' else "check", "dice": [check["natural_roll"]],
                        "total": check["roll_total"], "success": check["success"],
                        "dc": check.get("dc"), "check_id": check.get("check_id"),
                        "modifier": check.get("modifier")}})
                if command.action.kind == 'scene_action' and response['result'].get('natural_roll') is not None:
                    # A scene action's fact names its own DC and check id; plain
                    # checks carry the same keys, unset when the engine had none.
                    check = response['result']
                    effects.append({"type": "roll", "payload": {"kind": "check",
                        "dice": [check["natural_roll"]], "total": check["roll_total"],
                        "success": check["success"], "dc": check["dc"],
                        "check_id": check.get("check_id"),
                        "modifier": check.get("modifier")}})
                for roll in response["events"]:
                    if roll['type'] == 'concentration_check' and roll.get('natural') is not None:
                        effects.append({'type': 'roll', 'payload': {'kind': 'save', 'dice': [roll['natural']],
                            'total': roll['roll_total'], 'success': roll['succeeded'],
                            'dc': roll.get('dc'), 'modifier': roll.get('modifier')}})
                    if roll["type"] == "attack_rolled" and roll.get("natural") is not None:
                        # Native API exposes only the kept d20, not discarded
                        # advantage draws. Never fabricate the missing die.
                        effects.append({"type": "roll", "payload": {"kind": "attack", "dice": [roll["natural"]],
                            "total": roll["roll_total"], "success": roll["is_hit"],
                            "modifier": roll.get("modifier")}})
                from .nat20_prototype import experimental
                source_ref = ("Nat20 0.7.0 / pinned bundled canonical provenance / " if experimental(runtime.get('definition')) else "Nat20 0.7.0 / Foundry 5.3.3 SRD 5.2 / ") + runtime['release_digest']
                if runtime.get('definition'):
                    definition = runtime['definition']
                    source_ref += ' / campaign ' + definition['id'] + ' / authored ' + definition['source']['id'] + '@' + definition['source']['version']
                # r187: у операции героя есть числа, и игрок их видит. Раньше любая
                # нативная операция читалась «Действие героя подтверждено» —
                # захват, промах из-за укрытия и провоцированная атака были
                # невидимы, то есть правило работало молча.
                result_text = scene_outcome or roll_text or display_text(command.action.kind)
                # r216: an order is answered by the companion, not by the engine,
                # so the sentence is the host's record: what was asked, what he
                # answered, why, and the relation before and after. The narrator
                # gets this same line as the outcome to describe.
                order_record = (response.get('result') or {}).get('order')
                if order_record:
                    from .nat20_orders import sentence as order_sentence
                    result_text = order_sentence(order_record)[:1500]
                if command.action.kind == 'native_action':
                    combat = runtime.get('combat') or {}
                    result_text = native_sentence(command.action, response.get('events') or [],
                                                  grid=(combat.get('initial') or {}).get('grid'),
                                                  view=combat.get('view'))
                else:
                    # Провокированную атаку мог вызвать шаг ЛЮБОЙ команды.
                    combat = runtime.get('combat') or {}
                    provoked = provoked_attacks(response.get('events') or [],
                                                grid=(combat.get('initial') or {}).get('grid'),
                                                view=combat.get('view'))
                    if provoked:
                        result_text = ' / '.join(part for part in (result_text, provoked) if part)
                # r215: the master's companion turns are part of what the player
                # reads. Each line carries the companion's name and the engine's
                # own numbers; a refusal carries the engine's reason.
                from .nat20_companion import journal_text
                companion_turns = (response.get('result') or {}).get('companion_turns') or []
                companion_line = journal_text(companion_turns)[:2000]
                if companion_line:
                    result_text = ' '.join(part for part in (result_text, companion_line) if part)[:2900]
                # r217: the master says out loud what the player's own rules
                # changed — the difficulty of this fight, the check DC, the reward,
                # or the fate of the hero. Silence here would be the "silent
                # adjustment" the owner forbade.
                native_result = response.get('result') if isinstance(response.get('result'), dict) else {}
                rules_said = list(native_result.get('settings_notes') or [])
                for key in ('settings_dc', 'reward_note'):
                    if native_result.get(key):
                        rules_said.append(native_result[key])
                for key in ('death', 'recovery'):
                    record = native_result.get(key)
                    if isinstance(record, dict) and record.get('text'):
                        rules_said.append(record['text'])
                if rules_said:
                    result_text = ' '.join(part for part in (result_text, *rules_said) if part)[:3000]
                event = GameEvent.model_validate({"id": event_id, "campaign_id": campaign, "branch_id": branch,
                    "sequence": sequence, "parent_event_id": head["event_id"] if head else None,
                    "type": "command_resolved", "payload": {"command": command.action.kind,
                        # r184: the journal carries the sentence the screen shows, not
                        # the engine's own 'Nat20 confirmed: <kind>'. One dictionary
                        # (solo/event_display.py) owns the wording, so a new kind needs
                        # no matching edit in the browser.
                        "outcome": " / ".join(part for part in (migration_text, result_text) if part),
                        "effects": effects},
                    "subjects": [a.id for a in after.actors], "source_ref": source_ref,
                    "game_minutes": int(state.get("elapsed_minutes", 0)), "scope": "private", "known_by": ["hero"]})
                pair(event, after)
                state["engine_snapshot"] = after.model_dump(mode="json")
                return [{"type": "scripted", "kind": "nat20_command", "memory_evidence": scene_memories,
                         **({"text": roll_text} if roll_text else {}),
                         "game_event": event.model_dump(mode="json"),
                         "game_snapshot": after.model_dump(mode="json"), "game_command": shared.model_dump(mode="json"),
                         "native_events": response["events"], "native_result": response["result"],
                         "ai_proposal_applied": False}]
            journal = self.store.engine_event(db, current, command.request_id, original or _ACTION_LABELS.get(command.action.kind, 'Действие героя'), transition, signature,
                                              resolve_pending=True)
        row = journal["results"][0]
        return {"journal": journal, "event": GameEvent.model_validate(row["game_event"]),
                "events": [GameEvent.model_validate(row["game_event"])],
                "snapshot": GameSnapshot.model_validate(row["game_snapshot"]),
                "native_events": row["native_events"], "result": row["native_result"]}

    def _replayed_level_up(self, campaign, previous, signature):
        """The stored answer to an already-applied confirmation.

        A repeated request_id must not grant a second level, so the journal
        entry is returned unchanged; a different command reusing the id is
        refused, exactly like every other engine event.
        """
        import json

        recorded = json.loads(previous["metrics"]).get("engine_signature")
        if signature and recorded != signature:
            raise ValueError("request_id уже использован для другой команды")
        event = self.store.event_for_action(previous["id"])
        if event["undone"]:
            raise ValueError("Scenario event belongs to an abandoned branch")
        row = event["results"][0]
        fresh = self.store.campaign(campaign)
        return dict(row["native_result"], version=fresh["version"], request_id=None)

    @staticmethod
    def _hero_class(current):
        """The class the hero already has; the chat word never picks one."""
        definition = (current.get("state") or {}).get("nat20", {}).get("definition") or {}
        build = definition.get("build") or {}
        classes = build.get("classes") or {}
        return build.get("class_slug") or sorted(classes)[0]

    def level_up_from_chat(self, campaign, command):
        """Route the chat word for advancement through the shared level_up.

        The chat contract deliberately carries no mechanical choice, so the
        engine alone decides whether a level is owed; an unearned request is
        refused by the advancement rules rather than granted here.
        """
        action = command.action
        result = self.level_up(
            campaign,
            request_id=command.request_id,
            expected_version=command.expected_version,
            class_slug=self._hero_class(self.store.campaign(campaign)),
        )
        return {
            "journal": {"results": [dict(type="scripted", kind="nat20_level_up",
                                        native_result=result, native_events=[])]},
            "state": self.snapshot(self.store.campaign(campaign)),
        }

    def level_up(self, campaign, *, request_id, expected_version, class_slug,
                 selected_choices=(), subclass_slug=None, prepared_spells=(),
                 milestone=None, party_choices=None, target_level=None):
        """Commit one advancement through the worker and the shared journal.

        The worker computes the whole new runtime and refuses before returning
        it if any rule fails, so the host never stores a half-advanced hero.
        The journal makes the change replayable, and a repeated request_id is
        answered from the journal instead of granting a second level.

        ``target_level`` is the level the request names, and it is not a choice:
        the advancement always goes one step above the hero, so a request that
        names any other level is refused in words instead of being honoured or
        silently ignored. The screen never sends it as a destination - it echoes
        the single transition the server itself offered.
        """
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Повышение уровня требует request_id.")
        signature = digest({
            "backend": "Nat20",
            "campaign": campaign,
            "operation": "level_up",
            "request_id": request_id,
            "class_slug": class_slug,
            "selected_choices": list(selected_choices),
            "subclass_slug": subclass_slug,
            "prepared_spells": list(prepared_spells),
            "milestone": milestone,
        })
        with self.store.connection(True) as db:
            current = self.store.campaign(campaign, db)
            runtime = self._bound(current)
            # A retry of the same confirmation is answered from the journal, so
            # the version it saw is spent and the hero keeps one new level.
            previous = db.execute(
                "SELECT id,metrics FROM actions WHERE campaign=? AND request_id=?",
                (campaign, request_id),
            ).fetchone()
            if previous is not None:
                return self._replayed_level_up(campaign, previous, signature)
            if current["version"] != expected_version:
                raise ValueError(
                    "Состояние героя изменилось; обновите лист и подтвердите повышение снова."
                )
            # The destination is not the client's to choose (owner, L03): the
            # hero always advances by exactly one level, so a request that names
            # a jump is refused in words instead of being applied or dropped.
            hero_level = int(
                ((runtime.get("definition") or {}).get("build") or {}).get("level") or 1
            )
            if target_level is not None:
                named = int(target_level)
                if named != hero_level + 1:
                    raise ValueError(
                        "Повышение идёт на один уровень за подтверждение: герой "
                        + str(hero_level) + "-го уровня, значит подтверждается "
                        + str(hero_level + 1) + "-й, а запрос назвал " + str(named)
                        + "-й. Уровень не поднят."
                    )
            if runtime.get("combat"):
                raise ValueError(
                    "Повышение уровня недоступно во время боя; завершите бой и повторите."
                )

            def transition(state):
                response = self._worker({
                    "mode": "level_up",
                    "state": runtime,
                    "class_slug": class_slug,
                    "selected_choices": list(selected_choices),
                    "subclass_slug": subclass_slug,
                    "prepared_spells": list(prepared_spells),
                    "milestone": milestone,
                    # r214: companions advance with the hero; their unanswered
                    # questions come from the table, not from the host.
                    "party_choices": dict(party_choices or {}),
                })
                state["nat20"] = response["state"]
                state["hero"] = self._keep_own_words(state, self._hero(response["state"]).model_dump())
                return [dict(type="scripted", kind="nat20_level_up",
                             native_result=response["result"], native_events=[])]

            journal = self.store.engine_event(
                db,
                current,
                request_id,
                "Nat20 повышение уровня: " + class_slug,
                transition,
                signature,
                resolve_pending=True,
            )
        row = journal["results"][0]
        fresh = self.store.campaign(campaign)
        return dict(row["native_result"], version=fresh["version"], request_id=request_id)

    # --- L02: сверка уровня героя с книгой на старте кампании --------------

    def level_gate(self, campaign):
        """Что Мастер говорит о уровне героя: оба числа и два пути.

        Читает только кампанию: ничего не пишет и не бросает кубик. Экран и
        маршрут получают отсюда же ответ, чтобы «Мастер сказал» и «экран
        показал» были одним фактом, а не двумя пересказами.
        """
        from . import level_gate
        return level_gate.projection(self.store.campaign(campaign)['state'])

    def _event_of_request(self, campaign, request_id):
        """Журнальное событие по request_id или None: повтор не пишет второй раз."""
        with self.store.connection() as db:
            row = db.execute("SELECT id FROM actions WHERE campaign=? AND request_id=?",
                             (campaign, request_id)).fetchone()
        if row is None:
            return None
        event = self.store.event_for_action(row['id'])
        return event['id'] if event else None

    def accept_player_level(self, campaign, *, request_id):
        """«Играем так»: решение игрока в кампании, опасность — от его уровня.

        Это не ход и не реплика Мастера, а факт кампании: одно журнальное
        событие без хода (``counts_as_turn=False``). Уровень героя не
        меняется — меняются числа боя: состав столкновений книги пересчитан
        под одиночку, а правила кампании (r217) прямо говорят, что врагов
        меньше. Канон не отключается: тех же врагов просто меньше.
        """
        from . import level_gate
        current = self.store.campaign(campaign)
        self._bound(current)
        state = current['state']
        gate = level_gate.projection(state)
        if gate is None:
            raise ValueError('Книга не назвала уровень: сверять нечего.')
        if not gate['gap']:
            raise ValueError('Герой уже собран на уровень книги (' + str(gate['required'])
                             + '-й): сверка не нужна, опасность считается по книге.')
        if gate['decided']:
            existing = self._event_of_request(campaign, request_id)
            if existing and gate['decision'] == 'player_level':
                return dict(event=existing, level_gate=gate, version=current['version'])
            raise ValueError('Решение уже принято: ' + str(gate['decision_text'] or 'опасность ведётся от уровня героя.')
                             + ' Героя можно пересобрать до первого хода.')
        level = int(gate['hero_level'])
        required = level_gate.requirement(state)

        def transition(state):
            from .nat20_prototype import make_definition
            runtime = DurableState.model_validate(state['nat20']).model_dump(mode='json')
            definition = runtime.get('definition') or {}
            encounters, notes = level_gate.scale_encounters(definition.get('encounters') or {}, level)
            if definition:
                # Состав столкновений — часть авторского определения, поэтому
                # оно пересобирается целиком (make_definition) и снова получает
                # свой отпечаток: подменить поле в обход отпечатка нельзя.
                rebuilt = make_definition(
                    definition.get('build'), encounters, definition.get('checks') or {},
                    definition.get('prepared_spells') or (),
                    definition.get('title') or state.get('location') or 'Приключение',
                    definition.get('description') or 'Приключение',
                    progression=definition.get('progression'), episode_id=definition.get('id'))
                runtime['definition'] = rebuilt.model_dump(mode='json')
            # Правила кампании (r217): у стола один герой, значит врагов меньше.
            # Это те же правила, что игрок видит на экране «Правила», а не
            # скрытая подкрутка рассказчика.
            settings = dict(runtime.get('settings') or {})
            settings['difficulty'] = 'solo_softer'
            runtime['settings'] = settings
            state['nat20'] = DurableState.model_validate(runtime).model_dump(mode='json')
            text = level_gate.accept_text(level, required, encounters)
            if notes:
                text += ' ' + ' '.join(notes)
            level_gate.mark(state, 'player_level', text=text,
                            method='danger_from_hero_level')
            return [dict(type='scripted', kind='level_gate', text=text)]

        event = self.store.record_scenario(campaign, request_id,
                                           'Мастер: играем как есть, опасность от уровня героя',
                                           transition, counts_as_turn=False)
        fresh = self.store.campaign(campaign)
        return dict(event=event['id'], level_gate=level_gate.projection(fresh['state']),
                    version=fresh['version'])

    def rebuild_hero(self, campaign, *, request_id, build, prepared_spells=(), name=''):
        """Пересборка героя на уровень книги вместо прежнего (до первого хода).

        Сборку считает тот же рабочий процесс, что и обычный старт: лист,
        умения и снаряжение приходят из движка. Книга остаётся прежней —
        сцена, места и столкновения не пересобираются; меняется только герой,
        поэтому старого героя в кампании не остаётся. Ход не делается:
        ``counts_as_turn=False``.
        """
        from . import level_gate
        current = self.store.campaign(campaign)
        runtime = self._bound(current)
        state = current['state']
        gate = level_gate.projection(state)
        if gate is None:
            raise ValueError('Книга не назвала уровень: пересобирать героя не под что.')
        if not isinstance(build, dict):
            raise ValueError('Нужна собранная сборка героя, а не пустое поле.')
        level = int(build.get('level') or 0)
        required = int(gate['required'])
        if not level:
            raise ValueError('Сборка героя не называет уровень: книга просит '
                             + str(required) + '-й.')
        if level < required:
            raise ValueError('Книга рассчитана на ' + str(required) + '-й уровень, а сборка называет '
                             + str(level) + '-й. Пересоберите героя на ' + str(required) + '-й уровень.')
        if gate['maximum'] and level > int(gate['maximum']):
            raise ValueError('Книга рассчитана на ' + str(required) + '–' + str(gate['maximum'])
                             + '-й уровни, а сборка называет ' + str(level) + '-й.')
        if state.get('engine_snapshot'):
            # Сверка живёт до первого действия героя. Открывающая сцена книги
            # тоже пишет журнальное событие, поэтому признак хода — снимок
            # движка после первой разрешённой команды, а не номер хода.
            raise ValueError('История кампании уже начата: пересборка бесплатна только до первого хода.')
        if (state.get('nat20') or {}).get('combat'):
            raise ValueError('Идёт бой: героя пересобирают до столкновения, а не в нём.')
        definition = (runtime.get('definition') or None)
        if not definition:
            raise ValueError('Кампания ведётся по встроенному сценарию: пересборка героя под книгу '
                             'доступна кампаниям, начатым по подготовленной книге.')
        hero_name = str(name or (state.get('hero') or {}).get('name') or 'Герой')[:80]
        seed = int(runtime.get('seed') or 0)

        def transition(state):
            from .nat20_prototype import make_definition
            kept = state['nat20'].get('definition') or {}
            rebuilt = make_definition(
                build, kept.get('encounters') or {}, kept.get('checks') or {},
                list(prepared_spells or kept.get('prepared_spells') or ()),
                kept.get('title') or state.get('location') or 'Приключение',
                kept.get('description') or 'Приключение',
                progression=kept.get('progression'), episode_id=kept.get('id'))
            response = self._worker({'mode': 'initialize', 'name': hero_name, 'seed': seed,
                                     'definition': rebuilt.model_dump(mode='json')})
            state['nat20'] = response['state']
            # Старый лист не переносится даже частично: иначе в кампании
            # остались бы числа двух героев. Свои слова (предыстория) — тоже
            # нового героя, а не прежнего.
            state['hero'] = self._hero(response['state']).model_dump()
            text = level_gate.rebuild_text(int(build.get('level') or required),
                                           level_gate.requirement(state))
            level_gate.mark(state, 'rebuilt', text=text, method='rebuilt_at_book_level')
            return [dict(type='scripted', kind='level_gate', text=text)]

        event = self.store.record_scenario(campaign, request_id,
                                           'Мастер: герой пересобран на уровень книги',
                                           transition, counts_as_turn=False)
        fresh = self.store.campaign(campaign)
        return dict(event=event['id'], hero=fresh['state'].get('hero'),
                    level_gate=level_gate.projection(fresh['state']), version=fresh['version'])


__all__ = ["Nat20GameEngine", "GameCommand", "GameEvent", "GameSnapshot"]
