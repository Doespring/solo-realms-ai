"""Disposable offline Nat20 process: public API replay, no private-state codec.

Only this trusted module starts a provider runtime. All durable inputs are JSON;
no pickles, arbitrary imports, handlers or expressions come from the caller.
"""
import asyncio
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import random
import sys

from pydantic import BaseModel
from dnd5e_engine.rules.character import hit_die_size
from dnd5e_engine import (
    AbilityScores, ActiveEffect, CharacterBuildSpec, CombatInstance,
    EncounterMemberSpec, GridScene, HitDicePool, IntentRejectedError, PartyMemberSpec, PlayerIntent,
    advance_monster_turn, build_party_member, cell_id, configure_lib_loader,
    derive_sheet, drain_pending_events, end_combat, get_actor_active_effects,
    get_live, recover_feature_uses, recover_slotless_casts,
    resolve_long_rest, resolve_short_rest, start_combat, submit_player_intent,
)

from .nat20_content import CHECKS, digest, engine_identity, release
from .nat20_contracts import DurableState, GameCommand
from .nat20_campaign import validate_definition, RewardReceipt
from .nat20_prototype import experimental, RELEASE as PROTOTYPE_RELEASE, known_issue
from .nat20_settings import (DEATH_QUESTION, check_dc, check_note, describe, fight_rules,
                             reward_amount, reward_note, rules_of, scale_encounter)


def plain(value):
    if isinstance(value, BaseModel):
        return plain(value.model_dump(mode="python", exclude_unset=False))
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((plain(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def effects_of(handle, view):
    return [plain(effect) for id in sorted(set(view.party_ids) | set(view.encounter_ids))
            for effect in get_actor_active_effects(handle, id)]


def read_combat(handle):
    view = get_live(handle)
    return {"view": plain(view), "active_effects": effects_of(handle, view)}


def initialize(payload, loader, fingerprint):
    definition = validate_definition(payload['definition']) if payload.get('definition') is not None else None
    build = definition.build if definition else CharacterBuildSpec(species_slug="human", class_slug="fighter", level=1,
        ability_scores=AbilityScores(strength=16, dexterity=14, constitution=14, intelligence=10, wisdom=12, charisma=8),
        equipment=("longsword", "chain-mail", "shield"), selected_choices=("skill:athletics", "skill:perception"))
    sheet = derive_sheet(build, loader=loader)
    member = build_party_member(build, CombatInstance(entity_id="hero", name=payload["name"],
        initiative=None, zone_id=cell_id(1, 1), hp_current=payload.get("hp_current"),
        spells_known=tuple(definition.prepared_spells) if experimental(definition) else tuple(definition.spellcasting.prepared) if definition and definition.spellcasting else ()), loader=loader)
    if not 1 <= member.hp_current <= member.hp_max:
        raise ValueError("Invalid initial HP")
    # Preserve upstream's unset attack-bonus sentinel; dumping every default
    # would turn an automatically derived bonus into an explicit zero override.
    hero = member.model_dump(mode="json", exclude_unset=True)
    if not experimental(definition):
        hero["custom_counters"] = ({"feature_use:second-wind": {"spent": payload.get("second_wind_spent", 0)}}
                                   if build.class_slug == 'fighter' else {})
    else:
        hero.setdefault('custom_counters', {})
    effects = [plain(ActiveEffect(id="initial:" + status, name=status, origin="trusted_initial_state",
                                 target_id="hero", statuses={status})) for status in payload.get("conditions", [])]
    return DurableState(release_digest=fingerprint, seed=payload["seed"], hero=hero, sheet=plain(sheet), definition=definition,
                        hit_dice={"hit_die_size": hit_die_size(str(loader.get_class(build.class_slug).hit_die)), "dice_remaining": build.level, "dice_total": build.level},
                        exhaustion=0, active_effects=effects).model_dump(mode="json")


def _short(value):
    """One bounded line for a snapshot leaf."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 60 else text[:57] + '...'


def snapshot_differences(expected, actual, path='snapshot', limit=12):
    """Leaf-by-leaf differences between two plain snapshots, bounded."""
    found = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=str):
            child = path + '.' + str(key)
            if key not in expected:
                found.append(child + ': нет в сохранении -> ' + _short(actual[key]))
            elif key not in actual:
                found.append(child + ': ' + _short(expected[key]) + ' -> нет в повторе')
            else:
                found += snapshot_differences(expected[key], actual[key], child, limit)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            found.append(path + ': элементов ' + str(len(expected)) + ' -> ' + str(len(actual)))
        else:
            for index, (left, right) in enumerate(zip(expected, actual)):
                found += snapshot_differences(left, right, path + '[' + str(index) + ']', limit)
    elif expected != actual:
        found.append(path + ': ' + _short(expected) + ' -> ' + _short(actual))
    return found[:limit]


def migration_text(record):
    """What the player is told when a fight opens on other rules."""
    saved = record.get('saved_engine') or {}
    current = record.get('current_engine') or {}
    if record['mode'] == 'unknown':
        text = 'Версия правил сохранения неизвестна: бой открыт как есть'
    else:
        text = ('Бой начат на прежней версии правил (' + str(saved.get('release', '?')) +
                '), продолжаем на текущей (' + str(current.get('release', '?')) + ')')
    differences = record.get('differences') or []
    if differences:
        return text + '. Числа пересчитаны: ' + '; '.join(differences[:4])
    return text + '. Числа сверены, расхождений нет'


async def replay(combat, migration=None, state=None):
    """Rebuild the fight from its recipe, and say what the rules changed.

    A save records the engine fingerprint that produced its numbers
    (``combat['engine']``). Three cases, deliberately different:

    * same fingerprint, replay diverged -> a real fault: refuse, and name the
      fields that moved instead of the bare "replay diverged";
    * another engine revision -> migrate: open the fight on the current rules,
      report the numbers that changed, re-bind the digests, and record the
      migration. Nothing is guessed: a command the current rules reject stops
      the migration with that reason;
    * no fingerprint (a save made before r182) -> "version unknown", no refusal.

    ``migration``, when passed, receives the diagnosis for the caller to
    persist and show. The handle is only reusable after migration is committed:
    this function itself never writes durable state.
    """
    current_engine = engine_identity()
    saved_engine = combat.get('engine')
    if saved_engine is None:
        mode = 'unknown'
    elif saved_engine == current_engine:
        mode = 'identical'
    else:
        mode = 'migrated'
    strict = mode == 'identical'
    record = {'mode': mode, 'saved_engine': saved_engine, 'current_engine': current_engine,
              'differences': [], 'diverged': False, 'faults': [], 'digests': [], 'text': ''}

    def diverged(reason, differences):
        record['diverged'] = True
        record['faults'].append(reason)
        for item in differences:
            if item not in record['differences'] and len(record['differences']) < 12:
                record['differences'].append(item)

    initial = combat["initial"]
    opened = await start_combat(session_id=initial["session_id"], rng_seed=initial["seed"],
        party=[PartyMemberSpec.model_validate(item) for item in initial["party"]],
        encounter=[EncounterMemberSpec.model_validate(item) for item in initial["encounter"]],
        grid_scene=GridScene.model_validate(initial["grid"]),
        active_effects=[ActiveEffect.model_validate(item) for item in initial["active_effects"]])
    handle = opened.handle
    events = plain(drain_pending_events(handle))
    snapshot = read_combat(handle)
    initial_digest = digest({"snapshot": snapshot, "events": events})
    record['digests'].append(initial_digest)
    if initial_digest != combat["initial_digest"]:
        # With no commands recorded the save still holds the initial snapshot
        # itself, so the moved fields can be named exactly. Otherwise the
        # command digests and the final snapshot below carry the evidence.
        saved = {key: combat[key] for key in ("view", "active_effects")} if not combat["commands"] else None
        diverged("Nat20 initial replay diverged",
                 snapshot_differences(saved, snapshot) if saved is not None
                 else ["initial: стартовое состояние пересчитано иначе, чем записано"])
    if len(combat["commands"]) > 256:
        raise ValueError("Combat replay limit exceeded (256); no unsafe partial restore")
    for number, recorded in enumerate(combat["commands"], start=1):
        action = GameCommand.model_validate({"request_id": "replay", "expected_version": 0, "action": recorded["action"]}).action
        try:
            replayed_turns = await apply_action(handle, action, weapon=initial.get('attack_weapon', 'longsword'),
                               features=initial.get('allowed_features', []), spells=initial.get('allowed_spells', []), prototype=initial.get('prototype'),
                               state=state)
        except ValueError as error:
            if strict:
                raise
            raise ValueError('Миграция боя остановилась на команде ' + str(number) + ' (' + action.kind + '): '
                             + str(error) + '. Сохранение не тронуто.') from error
        # r215: a companion turn consumed its own events to read them step by
        # step; the replayed command's event list is the same merged list the
        # live command recorded, so the digest can match.
        events = [event for record in (replayed_turns or []) for event in record.get('events') or []]
        events += plain(drain_pending_events(handle))
        snapshot = read_combat(handle)
        command_digest = digest({"snapshot": snapshot, "events": events})
        record['digests'].append(command_digest)
        if command_digest != recorded["result_digest"]:
            # Name what moved: the engine's own event stream of the replayed
            # command and the turn pointer are the two things a digest covers.
            diverged('Nat20 replay diverged at command ' + str(number) + ' (' + action.kind + ')',
                     ['replay events: ' + ','.join(str(e.get('type')) for e in events)[:400],
                      'replay turn: ' + str(snapshot['view']['current_turn_index'])
                      + ' round ' + str(snapshot['view']['round_number'])])
    final = read_combat(handle)
    saved_final = {key: combat[key] for key in ("view", "active_effects")}
    if final != saved_final:
        diverged("Nat20 durable snapshot does not match replay",
                 snapshot_differences(saved_final, final))
    if record['diverged'] and strict:
        # A diagnosis, not a shrug: which field moved and to what.
        detail = '; '.join(record['differences'][:6]) or 'отпечаток повтора изменился, поля не названы'
        raise ValueError(record['faults'][0] + ': ' + detail)
    if migration is not None:
        migration.update(record)
        migration['text'] = migration_text(record)
    return handle


async def play_companion_turns(handle, state, *, grid=None, leading_only=False):
    """Let the master play every companion turn that is due (r215).

    ``state['party']`` says who the companions are; ``_companion_member`` is the
    only place that decides it, so a hero ("hero") and a monster (never in the
    party) are never driven by the master. Monsters keep advancing through the
    engine's own registered policy, as before; ``leading_only`` stops at the
    first non-companion actor, which is what opening a fight needs.
    """
    from .nat20_companion import MAX_TURNS, play_turn

    view = get_live(handle)
    grid = grid if grid is not None else ((state.get('combat') or {}).get('initial') or {}).get('grid') or {}
    records = []
    limit = MAX_TURNS + 4 * (len(view.initiative) + 1)
    for _ in range(limit):
        view = get_live(handle)
        if view.ended or 'hero' in view.dead_ids:
            break
        actor = view.initiative[view.current_turn_index].entity_id
        if actor == 'hero':
            break
        member = _companion_member(state, actor)
        if member is None:
            if leading_only or actor not in view.encounter_ids:
                break
            await advance_monster_turn(handle)
            continue
        try:
            records.append(await play_turn(handle, actor, member, grid=grid,
                                           effects=effects_of(handle, view)))
        except Exception as error:  # noqa: BLE001 - a broken turn must not stall the fight
            # The refusal is kept with its own text: a companion whose turn the
            # host cannot play is a defect the player reads, not a silent skip.
            records.append(dict(id=actor, name=member.get('name') or actor, actions=[],
                                refused=[dict(kind='ход спутника', reason=str(error)[:300])],
                                silent=[]))
            try:
                await submit_player_intent(handle, actor, PlayerIntent(intent_type='pass'))
            except Exception:  # noqa: BLE001 - the fight is already past this actor
                break
        if len(records) >= MAX_TURNS:
            break
    return records


def _companion_member(state, actor_id):
    """The party record the master plays for ``actor_id``; ``None`` for the hero."""
    if actor_id == 'hero':
        return None
    return next((item for item in state.get('party') or []
                 if item.get('id') == actor_id and item.get('role') == 'companion'), None)


async def apply_action(handle, action, *, weapon='longsword', features=(), spells=(), prototype=None, state=None):
    view = get_live(handle)
    if view.tracked_hp.get('hero', 0) <= 0 and action.kind not in ('end_turn','end_combat'):
        raise ValueError('Hero at 0 HP cannot act; end_turn advances native death saves')
    if 'hero' in view.dead_ids and action.kind!='end_combat':
        raise ValueError('Native confirmed hero death; only end_combat may close this battle')
    if view.ended and action.kind != "end_combat":
        raise ValueError("Combat has ended; close it before another operation")
    current = view.initiative[view.current_turn_index].entity_id
    if action.kind == "end_combat":
        # Finish is a state boundary, never a way to erase live hostiles.
        if 'hero' not in view.dead_ids and any(view.tracked_hp.get(id, 0) > 0 and id not in view.dead_ids for id in view.encounter_ids):
            raise ValueError("Living hostiles remain; surrender/flight requires a separate trusted procedure")
        await end_combat(handle)
        return
    if action.kind == "end_turn":
        # r214: a companion's turn is passed through the same intent.
        # r215: the master plays it instead of skipping it: companion turns are
        # resolved inside this command, and their events go to the journal.
        # A fight saved before r215 has no companion policy recorded, and it is
        # replayed exactly as it was played then — the saved command digests
        # would otherwise disagree with the replay and the save would refuse to
        # open for a host-policy change the player never made.
        if ((state or {}).get('combat') or {}).get('companion_policy') != 'master':
            if current in view.party_ids:
                await submit_player_intent(handle, current, PlayerIntent(intent_type="pass"))
            for _ in range(len(view.initiative) + 1):
                view = get_live(handle)
                if view.ended or 'hero' in view.dead_ids or view.initiative[view.current_turn_index].entity_id in view.party_ids:
                    return
                await advance_monster_turn(handle)
            raise ValueError("Monster turn did not converge")
        fresh = get_live(handle)
        current = fresh.initiative[fresh.current_turn_index].entity_id
        if current == 'hero':
            await submit_player_intent(handle, 'hero', PlayerIntent(intent_type="pass"))
        elif current in view.party_ids and _companion_member(state or {}, current) is None:
            # A party member the master does not know how to play must not stall
            # the fight; its turn is handed over and the gap is named upstream.
            await submit_player_intent(handle, current, PlayerIntent(intent_type="pass"))
        return await play_companion_turns(handle, state if state is not None else {})
    if current != "hero":
        raise ValueError("Hero is not the current actor; end_turn advances the registered monster policy")
    if action.kind == 'native_action':
        # r194: в штатной кампании доступны ровно те операции, которые пин
        # считает сам (`SHIPPED_NATIVE_OPERATIONS`), а не весь набор
        # экспериментальной ветки. Отказ называет причину, а не «нужна другая
        # кампания»: измеренные дефекты пина (`hide`, `ready`) остаются
        # отказом через `known_issue` с их собственной причиной.
        from .nat20_prototype import SHIPPED_NATIVE_OPERATIONS
        issue=known_issue('actions',action.operation)
        if issue:raise ValueError(issue)
        if prototype is None and action.operation not in SHIPPED_NATIVE_OPERATIONS:
            # Отказ обязан называть правду: атака, движение, рывок, «пропуск» и
            # умение в штатной кампании ДВИЖКОМ считаются, просто своей
            # командой. «Движок этого не умеет» было бы ложью того же класса,
            # что и прежний гейт.
            own_kind = {'attack': 'attack', 'move': 'move', 'dash': 'dash',
                        'pass': 'end_turn', 'use_feature': 'use_feature'}.get(action.operation)
            if own_kind:
                raise ValueError("Native operation " + str(action.operation) +
                    " is not a native action in a shipped campaign; use kind='" + own_kind + "'")
            raise ValueError('Native operation ' + str(action.operation) +
                ' is not exposed by the host as a native action in a shipped campaign; available: ' +
                ', '.join(SHIPPED_NATIVE_OPERATIONS))
        values=action.model_dump(exclude_none=True,exclude={'kind','operation','position'})
        for field in ('target_id','target_ids','excluded_target_ids'):
            ids=values.get(field,[])
            ids=[ids] if isinstance(ids,str) else ids
            if set(ids)-set(view.party_ids)-set(view.encounter_ids):
                raise ValueError('Unknown native target')
        # Снаряжение экспериментальной сцены приходит в `prototype`; у штатной
        # его нет, и проверка владения не должна падать на `None`.
        owned=set((prototype or {}).get('equipment') or ())
        for field in ('weapon_id','item_id'):
            if not values.get(field) or values[field] in owned:
                continue
            # r175: безоружный удар — не предмет в сумке. Движок знает его как
            # отдельное оружие (`unarmed-strike`), поэтому проверка владения
            # инвентарём его не касается; иначе монах не мог позвать ни Боевые
            # искусства, ни бонусный удар, ни Шквал.
            from .nat20_content import UNARMED_STRIKE
            if field == 'weapon_id' and values[field] == UNARMED_STRIKE:
                continue
            raise ValueError('Native equipment reference is not owned by this build')
        if action.operation=='attack' and not action.weapon_id and not action.stat_block_action_id:
            values['weapon_id']=weapon
        # Native's repertoire gate checks the actual class/species/subclass
        # grants. A second static adapter feature whitelist hides valid handlers.
        if action.operation=='use_feature':
            issue=known_issue('features',action.feature_id)
            if issue and issue.startswith('blocked:'):raise ValueError(issue)
            # r170: умение подкласса водится по своему плану, а не «как придёт».
            # Без activity_id многовариантное умение движок молча проглатывает
            # (CastFailed activity_not_selected), а урон-активность без цели
            # бьёт самого героя (Overchannel: 60 → 45 HP). План называет и
            # вариант, и цель, либо честно отказывает с причиной.
            from .nat20_content import feature_intent, is_subclass_feature
            if is_subclass_feature(action.feature_id):
                chosen_target=action.target_id or next(iter(action.target_ids or []),None)
                prepared=feature_intent(action.feature_id,action.activity_id,chosen_target)
                values['activity_id']=prepared['activity_id']
                if prepared['target_id']:
                    values['target_id']=prepared['target_id']
                else:
                    # Лечение и площадные активности целей не берут: переданная
                    # цель перенаправляла лечение героя на чужую фигуру.
                    values.pop('target_id',None);values.pop('target_ids',None)
        if action.operation=='cast_spell':
            prepared=view.spells_known_by_entity.get('hero',spells)
            if action.spell_id not in prepared:raise ValueError('Spell not in native prepared/granted set')
            issue=known_issue('spells',action.spell_id)
            if issue and issue.startswith('blocked:'):raise ValueError(issue)
            from .nat20_prototype import spell_execution_status
            from dnd5e_engine.lib_loader import get_lib_loader
            spell=get_lib_loader().get_spell(action.spell_id)
            if spell is None or spell_execution_status(spell.model_dump(mode='json'))=='narrative_or_unsupported_in_combat':
                raise ValueError('narrative_or_unsupported_in_combat: no typed combat handler payload for '+str(action.spell_id))
        if action.position:
            if action.position.z!=0:raise ValueError('Native grid only supports z=0')
            values['target_zone_id']=cell_id(action.position.x,action.position.y)
        intent=PlayerIntent(intent_type=action.operation,**values)
    elif action.kind == "move":
        if action.position.z != 0:
            raise ValueError("Nat20 GridScene supports this scene only at z=0")
        intent = PlayerIntent(intent_type="move", target_zone_id=cell_id(action.position.x, action.position.y))
    elif action.kind == "attack":
        if action.target not in view.encounter_ids:
            raise ValueError("Unknown or non-hostile target")
        intent = PlayerIntent(intent_type="attack", target_id=action.target, weapon_id=weapon)
    elif action.kind == 'dash':
        intent = PlayerIntent(intent_type='dash')
    elif action.kind == 'use_feature':
        if action.feature_id not in features:
            raise ValueError('Class feature is not registered for this encounter')
        intent = PlayerIntent(intent_type='use_feature', feature_id=action.feature_id)
    elif action.kind in ('cast_spell', 'drop_concentration'):
        if spells != ['bless']:
            raise ValueError('Spell capability is not registered for this campaign')
        if action.kind == 'cast_spell':
            intent = PlayerIntent(intent_type='cast_spell', spell_id='bless', target_id='hero', slot_level=1)
        else:
            intent = PlayerIntent(intent_type='drop_concentration')
    else:
        raise ValueError("Unsupported in-combat command")
    try:
        await submit_player_intent(handle, "hero", intent)
    except IntentRejectedError as error:
        # Availability probes and actual commands share the same native refusal.
        raise ValueError(str(error)) from error


def _milestone_mode(definition):
    """True when the scenario advances by milestones, not by experience."""
    progression = getattr(definition, "progression", None)
    mode = getattr(progression, "mode", None)
    return mode == "milestone"

def sync_hero(state, snapshot):
    view = snapshot["view"]
    hero = next(actor for actor in view["initiative"] if actor["entity_id"] == "hero")
    state["hero"]["hp_current"] = view["tracked_hp"]["hero"]
    state["hero"]["spell_slots"] = view["spell_slots_by_entity"].get("hero", {})
    state["hero"]["pact_slots"] = view["pact_slots_by_entity"].get("hero", {})
    state["hero"]["custom_counters"] = view["custom_counters_by_entity"].get("hero", {})
    state["hero"]["zone_id"] = view["actor_zone"]["hero"]
    state["hero"]["concentration_effect_id"] = hero.get("concentration_effect_id")
    state["active_effects"] = [effect for effect in snapshot["active_effects"] if effect["target_id"] == "hero"]
    # r214: a companion lives in the same fight and must not be left at the
    # numbers it had when the fight started. Only what the engine reports is
    # written back; nothing is recomputed here.
    for item in state.get("party") or []:
        identifier = item.get("id")
        if identifier == "hero" or identifier not in view["tracked_hp"]:
            continue
        member = dict(item.get("member") or {})
        actor = next((row for row in view["initiative"] if row["entity_id"] == identifier), {})
        member["hp_current"] = view["tracked_hp"][identifier]
        member["zone_id"] = view["actor_zone"].get(identifier, member.get("zone_id"))
        member["custom_counters"] = view["custom_counters_by_entity"].get(identifier, member.get("custom_counters") or {})
        member["spell_slots"] = view["spell_slots_by_entity"].get(identifier, member.get("spell_slots") or {})
        member["pact_slots"] = view["pact_slots_by_entity"].get(identifier, member.get("pact_slots") or {})
        member["concentration_effect_id"] = actor.get("concentration_effect_id")
        item["member"] = member
        item["exhaustion"] = int(item.get("exhaustion") or 0)


def apply_death_terms(state, command, snapshot, initial, result):
    """r217: what the hero's death means under the terms this fight was opened with.

    ``solo/nat20_settings.py`` owns the three choices; the fight carries the one
    that was in force when it started (``initial``), so changing the rule
    mid-fight cannot retroactively kill a hero it had pardoned or pardon one it
    had doomed.

    * ``on`` and the risk was declared before the fight — the rules apply: the
      hero is dead, and the journal says the risk was declared first.
    * ``off`` — the hero does not die: the mechanical result stands, but the
      campaign keeps him as *out* (0 hit points, stable). A long rest brings him
      back by the stabilizing rule.
    * ``agreed``, or ``on`` without a declared risk — the irreversible outcome
      is stopped with the master's question in the journal, and the hero is out
      as above. The answer is given before the next fight, never afterwards.
    """
    view = snapshot.get('view') or {}
    if 'hero' not in (view.get('dead_ids') or []):
        return
    previous = set((state['combat'].get('view') or {}).get('dead_ids') or [])
    if 'hero' in previous:
        return  # already recorded when it happened; this is a later command
    # A fight saved before r217 carries no terms: it is treated as "agreed" — the
    # irreversible outcome is stopped and named, never applied retroactively.
    policy = initial.get('death_policy') or 'agreed'
    declared = bool(initial.get('lethal_risk_declared'))
    if policy == 'on' and declared:
        # The rules apply: the hero is dead, and the campaign keeps that fact in
        # the same place it keeps "out" — one status, three reasons.
        state['out'] = dict(who='hero', reason='death', policy='on', declared_risk=True,
                            command=command.request_id, round=view.get('round'), hp=0)
        result['death'] = dict(
            mode='on', who='hero', held=False,
            text='Смерть: правила как есть. Риск был объявлен до начала боя, '
                 'поэтому необратимый исход наступил: герой погиб.')
        return
    state['out'] = dict(who='hero', reason='death_disabled' if policy == 'off' else 'death_held',
                        policy=policy, declared_risk=declared, command=command.request_id,
                        round=view.get('round'), hp=0)
    result['death'] = dict(
        mode=policy, who='hero', held=policy != 'off',
        text=('Смерть выключена: герой выбывает из боя и не умирает. Кампания '
              'продолжается; длительный отдых вернёт его в строй.' if policy == 'off'
              else DEATH_QUESTION[policy]))


async def execute(payload, resources=None):
    selector = payload.get('state', {}).get('release_digest')
    if payload['mode']=='initialize' and experimental(payload.get('definition')):
        selector=PROTOTYPE_RELEASE
    if payload['mode'] == 'initialize' and (payload.get('definition') or {}).get('spellcasting'):
        selector = payload['definition']['spellcasting']['content_release']
    loader, fingerprint, profile = resources or release(selector)
    configure_lib_loader(loader)
    if payload["mode"] == "initialize":
        return {"state": initialize(payload, loader, fingerprint)}
    state = DurableState.model_validate(payload["state"]).model_dump(mode="json")
    definition = validate_definition(state['definition']) if state['definition'] is not None else None
    is_prototype=experimental(definition)
    if state["release_digest"] != fingerprint:
        raise ValueError("Pinned engine/content release changed; migration must be explicit")
    if definition and not is_prototype and definition.spellcasting:
        from .nat20_bless import require_stand
        require_stand()
        if loader.get_spell('bless') is None or loader.get_class('cleric') is None:
            raise ValueError('Caster profile/content release mismatch; no implicit migration')
    elif not is_prototype and loader.get_spell('bless') is not None:
        raise ValueError('Fighter profile cannot change its pinned content release')
    if payload["mode"] == "verify":
        migration = {}
        if state["combat"]:
            await replay(state["combat"], migration=migration, state=state)
        return {"state": state, "migration": migration}
    if payload["mode"] == "level_up":
        # A level change is a definition change, so it goes through the same
        # validated path as creation instead of a partial state patch.
        from .nat20_levelup import level_up

        return level_up(dict(payload, state=state), loader, fingerprint)
    if payload["mode"] == "party_add":
        # r214: a companion joins with a sheet Nat20 derives, in this same
        # process and on the same pinned release as the campaign it joins.
        from .nat20_party import add_companion

        return add_companion(state, payload["member"], loader, fingerprint)
    if payload["mode"] == "party_recruit":
        # r218: the NPC of the material joins the same way, with the provenance
        # of the meeting in his record. The sheet is still Nat20's: the host
        # names the class, the engine prices it.
        from .nat20_recruit import join

        return join(state, payload["member"], loader, fingerprint)
    if payload["mode"] == "party_leave":
        # r218: leaving is its own event with a reason, and the party shrinks
        # under the same rule that refuses a departure mid-fight.
        from .nat20_recruit import leave

        return leave(state, {"member": payload.get("member"), "reason": payload.get("reason") or "left"})
    if payload['mode'] == 'availability':
        await execute(dict(mode='verify', state=state), (loader, fingerprint, profile))
        available = []
        for candidate in payload['commands']:
            try:
                await execute(dict(mode='execute', state=state, command=candidate,
                                   session_id=payload['session_id'],
                                   scene_checks=payload.get('scene_checks') or {}), (loader, fingerprint, profile))
                available.append(True)
            except ValueError:
                available.append(False)
        return dict(state=state, available=available)
    command = GameCommand.model_validate(payload["command"])
    action = command.action
    if action.kind in ('cast_spell', 'drop_concentration'):
        if is_prototype:
            raise ValueError('Use native_action for experimental spells and concentration')
        if not definition or not definition.spellcasting:
            raise ValueError('Spell capability is not registered for this campaign')
        if action.kind == 'cast_spell':
            from .nat20_bless import component_gate
            component_gate(state, loader)
            if any(e.get('origin') == 'cast:blessed:hero' and not e.get('disabled') for e in state['active_effects']):
                raise ValueError('Bless recast disabled: upstream duplicates active effect identity; drop first')
        elif not state['hero'].get('concentration_effect_id'):
            raise ValueError('No active concentration to drop')
    result, events = {}, []
    companion_turns = []
    migration, migration_result = {}, {}
    if action.kind == 'order':
        # r216: an order is a scene decision the host answers, not an engine
        # intent - the engine has no order to compute. The companion's traits and
        # relation live in the campaign state, the table is in nat20_orders, and
        # every answer becomes a journal event with its numbers.
        from .nat20_orders import apply as apply_order
        result, events = apply_order(state, action)
    elif action.kind in ("check", "short_rest", "long_rest", "scene_action"):
        if definition and not is_prototype and definition.spellcasting and state['active_effects']:
            raise ValueError('Bless elapsed-time boundary is pending; no implicit effect expiry')
        if state["combat"]:
            raise ValueError("Standalone checks/rest cannot run during this combat")
        if state["hero"]["hp_current"] <= 0:
            # r217: a hero the rules would not let die is *out*, not dead. He is a
            # stable creature at 0 hit points, and SRD 5.2 (Stabilizing) gives a
            # stable creature 1 hit point after 1d4 hours; a long rest is 8 hours,
            # so the hero wakes with 1 hit point and then rests. No hit point is
            # invented: the rule pays it.
            out = state.get('out') or {}
            if action.kind != 'long_rest' or not out or out.get('reason') == 'death':
                raise ValueError('Standalone check/rest cannot bypass dying or death; active native combat uses end_turn for death saves')
            woke = dict(state['out'])
            state['out'] = None
            state['hero']['hp_current'] = 1
        else:
            woke = None
        rng = random.Random(int(digest([state["seed"], command.request_id, command.expected_version]), 16))
        if action.kind == "check":
            # Two trusted registries may name the same id: the campaign's authored
            # definition, and the scene the book printed. The scene wins, because
            # it is the place the hero is standing in and its DC comes with the
            # page that printed it.
            scene = payload.get('scene_checks') or {}
            checks = definition.checks if definition else CHECKS
            if action.check_id in scene:
                # The host builds these entries and this is the model that
                # validates them: a malformed check refuses its own roll with the
                # field that is wrong instead of a TypeError mid-resolution.
                from .nat20_prototype import PrototypeCheck
                entry = PrototypeCheck.model_validate(scene[action.check_id]).model_dump(mode='json')
                kind, skill, ability, dc = entry.get('kind'), entry.get('skill'), entry.get('ability'), entry.get('dc')
                advantage, disadvantage = bool(entry.get('advantage')), bool(entry.get('disadvantage'))
            elif action.check_id not in checks:
                raise ValueError("Check is not in the trusted scene registry")
            elif definition:
                check = checks[action.check_id]
                kind, skill, ability, dc = check.kind, check.skill, check.ability, check.dc
                advantage, disadvantage = check.advantage if is_prototype else False, check.disadvantage if is_prototype else False
            else:
                kind, skill, ability, dc = CHECKS[action.check_id]
                advantage = disadvantage = False
            from .nat20_checks import resolve_state_check
            # r217: the difficulty the player chose moves the printed DC, and the
            # journal says by how much. The die is still the engine's.
            campaign_rules = rules_of(state)
            printed_dc = dc
            dc = check_dc(campaign_rules, dc)
            result=plain(resolve_state_check(state,kind=kind,skill=skill,ability=ability,dc=dc,
                advantage=advantage,disadvantage=disadvantage,rng=rng))
            settings_line = check_note(campaign_rules, printed_dc, dc)
            if settings_line:
                result['settings_dc'] = settings_line
                result['printed_dc'] = printed_dc
            if action.check_id in scene:
                # Provenance travels with the number: the table can see the DC
                # came from the scene, not from the engine's default table.
                result['check_id'] = action.check_id
                result['dc_source'] = 'scene'
        elif action.kind == "scene_action":
            # PHB 2024 Influence/Study/Search: the host names the action and the
            # skill, this worker rolls it through the same native check path as
            # any other ability check. It is NOT a PlayerIntent: the pinned
            # engine has no such intent_type (ValueError on construction), so the
            # action never enters the combat intent queue either.
            from .nat20_content import scene_check
            spec = scene_check(action.action_id, action.skill, definition)
            # r179: преимущество за дружелюбие (книга, стр. 18) знает хост —
            # он один видит отношение НИПа в состоянии кампании. Модель сюда
            # ничего не передаёт: `social` собирает хост из записи сцены.
            note = payload.get("social") or {}
            if action.action_id == "influence" and note.get("check_id") == spec["check_id"]:
                spec["advantage"] = bool(note.get("advantage")) or spec["advantage"]
            from .nat20_checks import resolve_state_check
            campaign_rules = rules_of(state)
            printed_dc = spec["dc"]
            spec = {**spec, "dc": check_dc(campaign_rules, printed_dc)}
            result = plain(resolve_state_check(state, kind=spec["kind"], skill=spec["skill"],
                ability=spec["ability"], dc=spec["dc"], advantage=spec["advantage"],
                disadvantage=spec["disadvantage"], rng=rng))
            settings_line = check_note(campaign_rules, printed_dc, spec["dc"])
            if settings_line:
                result["settings_dc"] = settings_line
                result["printed_dc"] = printed_dc
            # Provenance travels with the fact: which check it was and where its
            # DC came from (the scene's own entry or the host registry).
            result["check_id"] = spec["check_id"]
            result["action_id"] = action.action_id
            result["dc_source"] = spec["source"]
        else:
            if is_prototype and len(definition.build.classes)>1:
                raise ValueError('Mixed-class Hit Dice pools are not exposed by the native rest API adapter')
            if is_prototype and state['active_effects']:
                raise ValueError('Rest with timed active effects requires an elapsed-time expiry operation; not silently discarded')
            pool = HitDicePool(**state["hit_dice"])
            hero, sheet = state["hero"], state["sheet"]
            if action.kind == "short_rest":
                outcome = resolve_short_rest(pool, action.hit_dice, sheet["ability_modifiers"]["constitution"],
                    rng=rng, pact_slots=hero.get("pact_slots", {}), pact_slot_max=sheet["pact_slots"])
                hero["hp_current"] = min(hero["hp_max"], hero["hp_current"] + outcome.healed)
            else:
                outcome = resolve_long_rest(pool, hero["hp_current"], hero["hp_max"],
                    spell_slots=hero.get("spell_slots", {}), spell_slot_max=sheet["spell_slots"],
                    pact_slots=hero.get("pact_slots", {}), pact_slot_max=sheet["pact_slots"],
                    exhaustion_level=state["exhaustion"])
                hero["hp_current"] = outcome.hp_current
                state["exhaustion"] = outcome.exhaustion_level
                hero["spell_slots"] = outcome.spell_slots
            state["hit_dice"] = plain(outcome.pool)
            hero["pact_slots"] = outcome.pact_slots
            recovery = {slug: loader.get_feature(slug).uses.recovery for slug in sheet["features"]
                        if loader.get_feature(slug) is not None and loader.get_feature(slug).uses is not None}
            restored = recover_feature_uses(hero.get("custom_counters", {}),
                                            recovery=recovery, period="sr" if action.kind == "short_rest" else "lr", rng=rng)
            for slug, spent in restored.items():
                hero["custom_counters"]["feature_use:" + slug] = {"spent": spent}
            if action.kind == "long_rest":
                for slug, spent in recover_slotless_casts(hero["custom_counters"], "lr").items():
                    hero["custom_counters"]["slotless_cast:" + slug] = {"spent": spent}
            result = plain(outcome)
            if woke:
                result['recovery'] = dict(
                    hp=1, reason=woke.get('reason'),
                    text='Герой приходит в себя: стабильный персонаж с 0 хитов получает '
                         '1 хит через 1d4 часа (SRD 5.2), а отдых длится 8 часов. '
                         'Выбывание из прошлого боя снято.')
    elif action.kind == "start_combat":
        if state["combat"] or state["hero"]["hp_current"] <= 0:
            raise ValueError("Cannot start another combat in this state")
        encounter = definition.encounters.get(action.encounter_id) if definition else None
        if definition and encounter is None or not definition and action.encounter_id != 'basic':
            raise ValueError('Encounter is not in the registered campaign')
        if definition and any(r['encounter_id'] == action.encounter_id for r in state['rewards']):
            raise ValueError('Registered encounter is already completed; no repeat reward farming')
        if encounter:
            actor = encounter.actors[0]
            monster = loader.get_monster(actor.monster)
            if monster is None:raise ValueError('Unknown encounter monster')
            position = actor.position
            foe_id, foe_zone, xp = actor.id, cell_id(position.x, position.y), actor.xp_value
            hero_zone = cell_id(encounter.hero_position.x, encounter.hero_position.y)
            grid = encounter.grid
        else:
            monster = loader.get_monster('goblin-warrior')
            foe_id, foe_zone, xp = 'goblin', cell_id(4, 1), 0
            hero_zone, grid = cell_id(1, 1), GridScene(width=7, height=5)
        # r217: the fight is prepared from the difficulty the player chose. The
        # values the host is about to hand the engine are plain here, so the
        # scale table (solo/nat20_settings.py) moves them *before* the fight:
        # a settings change mid-fight cannot move a roll that has already landed,
        # and the scaled numbers travel inside the save with the encounter.
        planned = [dict(entity_id=foe_id, entity_type="Monster", name=monster.name,
            hp=monster.hp, ac=monster.ac, attack_bonus=profile.get("attack_bonus",0),
            dexterity=monster.ability_scores.dex, zone_id=foe_zone,
            monster_template_slug=monster.slug, xp_value=xp,
            creature_type=monster.creature_type, base_speed=monster.movement.walk or 0)]
        if is_prototype:
            planned = []
            for actor in encounter.actors:
                monster=loader.get_monster(actor.monster)
                if monster is None:raise ValueError('Unknown encounter monster: '+actor.monster)
                planned.append(dict(entity_id=actor.id,entity_type='Monster',name=monster.name,
                    hp=monster.hp,ac=monster.ac,
                    attack_bonus=actor.attack_bonus if actor.attack_bonus is not None else 0,
                    dexterity=monster.ability_scores.dex,zone_id=cell_id(actor.position.x,actor.position.y),
                    monster_template_slug=monster.slug,xp_value=actor.xp_value,creature_type=monster.creature_type,
                    base_speed=monster.movement.walk or 0))
        rules = rules_of(state)
        companions = [item for item in state.get('party') or [] if item.get('role') == 'companion']
        planned, settings_notes = scale_encounter(rules, planned, party_size=1 + len(companions))
        # L02: игрок отказался пересобирать героя — Мастер ведёт опасность под
        # его уровень. Число врагов считается от героя, а не от книги: одиночка
        # N-го уровня встречает не больше N противников, даже если сценарий
        # написал отряд на четырёх героев.
        if payload.get('balance_override'):
            # Число и сила врагов — от героя, а не от книги: одиночка N-го
            # уровня встречает не больше N противников и не сильнее бюджета
            # опасности этого уровня, даже если сценарий написал отряд на
            # четырёх героев. Враг, чья сила в закреплённом датасете не
            # подтверждена, сюда не ставится: непроверенной опасности нет.
            from . import level_gate
            hero_level = max(1, int((state.get('hero') or {}).get('level') or 1))
            budget = level_gate.danger_budget(hero_level)
            kept, dropped = [], []
            for item in planned:
                strength = level_gate.monster_cr(item.get('monster_template_slug'))
                if strength is None or strength > budget + 1e-9 or len(kept) >= hero_level:
                    dropped.append(item.get('name') or str(item.get('monster_template_slug')))
                    continue
                kept.append(item)
            if dropped:
                settings_notes.append('Опасность от уровня героя: врагов ' + str(len(kept))
                                      + ' вместо ' + str(len(planned)) + ' — бюджет '
                                      + ('%g' % budget) + ' CR на героя ' + str(hero_level)
                                      + '-го уровня.')
                planned = kept
        foes = [EncounterMemberSpec(entity_id=item['entity_id'], entity_type=item['entity_type'],
            name=item['name'], initiative=None, hp_current=item['hp'], hp_max=item['hp'],
            ac=item['ac'], attack_bonus=item['attack_bonus'], dexterity=item['dexterity'],
            zone_id=item['zone_id'], monster_template_slug=item['monster_template_slug'],
            xp_value=item['xp_value'], creature_type=item['creature_type'],
            base_speed=item['base_speed']) for item in planned]
        # r214: the fight opens with the whole party, not with the hero alone.
        # Companions keep their own sheet, hit points and resources; the engine
        # rolls their initiative as it does for the hero.
        members = [dict(state["hero"], zone_id=hero_zone)]
        taken = {hero_zone}
        for index, companion in enumerate([item for item in state.get('party') or [] if item.get('role') == 'companion']):
            member = dict(companion['member'])
            zone = str(member.get('zone_id') or '')
            if not zone or zone in taken:
                x = min(int(grid.width) - 1, 1 + index + 1)
                zone = cell_id(x, 1)
            taken.add(zone)
            member['zone_id'] = zone
            members.append(member)
        initial = {"session_id": payload["session_id"] + ":" + command.request_id,
                   "seed": state["seed"], "party": members, "encounter": [f.model_dump(mode="json") for f in foes],
                   "grid": grid.model_dump(mode="json"),
                   "active_effects": state["active_effects"],
                   # r217: the terms of this fight are fixed when it opens and are
                   # saved with it, so a mid-campaign change of the rules applies
                   # to the next encounter, never to a fight already in progress.
                   "settings": rules.model_dump(mode="json"),
                   "settings_notes": list(settings_notes),
                   "death_policy": rules.death,
                   "lethal_risk_declared": bool(payload.get("lethal_risk_declared"))}
        if definition:
            initial.update(attack_weapon=definition.attack_weapon, allowed_features=definition.features,
                           encounter_id=action.encounter_id, definition_id=definition.id)
            if is_prototype:
                initial.update(allowed_spells=definition.prepared_spells,prototype={'equipment':list(definition.build.equipment),
                    'unbound_monster_attacks':[a.id for a in encounter.actors if a.attack_bonus is None]})
            elif definition.spellcasting:
                initial['allowed_spells'] = definition.spellcasting.prepared
        opened = await start_combat(session_id=initial["session_id"], rng_seed=initial["seed"],
            party=[PartyMemberSpec.model_validate(member) for member in members], encounter=foes,
            grid_scene=GridScene.model_validate(initial["grid"]),
            active_effects=[ActiveEffect.model_validate(effect) for effect in initial["active_effects"]])
        events = plain(drain_pending_events(opened.handle))
        snapshot = read_combat(opened.handle)
        state["combat"] = {"initial": initial, "initial_digest": digest({"snapshot": snapshot, "events": events}),
                           "engine": engine_identity(), "commands": [],
                           # r215: this fight is led by the master; a save without
                           # this key replays under the r214 pass policy.
                           "companion_policy": "master", **snapshot}
        sync_hero(state, snapshot)
        result = {"settings": describe(rules), "settings_notes": list(settings_notes)}
    else:
        if state["combat"] is None:
            raise ValueError("No active Nat20 combat")
        if len(state["combat"]["commands"]) >= 256:
            raise ValueError("Combat replay limit reached; unsupported extension requires review")
        handle = await replay(state["combat"], migration=migration, state=state)
        initial = state['combat']['initial']
        if action.kind == 'end_combat' and definition and not is_prototype and definition.spellcasting and state['active_effects']:
            raise ValueError('Post-combat concentration clock is pending; expire/drop before victory')
        companion_turns = await apply_action(handle, action, weapon=initial.get('attack_weapon', 'longsword'),
                           features=initial.get('allowed_features', []), spells=initial.get('allowed_spells', []), prototype=initial.get('prototype'),
                           state=state)
        # r215: companion turns drained their own events to read them one step at
        # a time; the command's event list keeps all of them, in order.
        events = [event for record in (companion_turns or []) for event in record.get('events') or []]
        events += plain(drain_pending_events(handle))
        snapshot = read_combat(handle)
        failures = [event for event in events if event["type"].endswith("_failed")
                    and str(event.get('actor_id') or 'hero') in ('hero', None)]
        # r215: a companion's refusal is not the hero's command failing — the
        # master recorded it in `companion_turns` with the engine's reason, and
        # the hero's own command still stands.
        unbound=(initial.get('prototype') or {}).get('unbound_monster_attacks',
            [a.id for a in definition.encounters[initial['encounter_id']].actors if a.attack_bonus is None] if is_prototype else [])
        if is_prototype and any(e['type']=='attack_rolled' and e['attacker_id'] in unbound for e in events):
            from .nat20_monsters import attack_profile
            refused=next(e['attacker_id'] for e in events if e['type']=='attack_rolled' and e['attacker_id'] in unbound)
            actor=next(a for a in definition.encounters[initial['encounter_id']].actors if a.id==refused)
            reason=attack_profile(actor.monster)['reason'] or 'Saved encounter predates automatic binding; no implicit data migration'
            raise ValueError('Native monster weapon attack bonus is unbound for '+actor.monster+': '+reason+'. No state committed.')
        # r194: молчаливая операция — дефект не только экспериментальной
        # ветки: кнопка, которая ничего не посчитала, не должна выглядеть
        # как сыгранное действие.
        if (is_prototype or action.kind == 'native_action') and not any(e['type']!='intent_submitted' for e in events) and snapshot=={key:state['combat'][key] for key in ('view','active_effects')}:
            raise ValueError('Native operation produced no supported mechanical effect')
        enemies = {a['entity_id'] for a in initial['encounter']}
        if failures:
            raise ValueError("Nat20 refused operation: " + json.dumps(failures, ensure_ascii=False))
        if action.kind == 'cast_spell' and not any(e['type'] == 'effect_applied' and e['effect']['changes'] for e in events):
            raise ValueError('Bless cast produced no mechanical effect; no state committed')
        # r217: the hero's death obeys the terms the fight was opened with.
        apply_death_terms(state, command, snapshot, initial, result)
        state["combat"]["commands"].append({"action": action.model_dump(mode="json"),
            "result_digest": digest({"snapshot": snapshot, "events": events})})
        state["combat"].update(snapshot)
        sync_hero(state, snapshot)
        if migration.get('mode') in ('migrated', 'unknown'):
            # Re-bind the save to the rules that just replayed it. Without this
            # the fight would carry the old engine's digests for ever, and the
            # next command - now on the same fingerprint - would be refused as a
            # real fault. The rewrite is the migration: the numbers above are
            # the current engine's, and the digests now describe them.
            state["combat"]["initial_digest"] = migration['digests'][0]
            for row, sha in zip(state["combat"]["commands"], migration['digests'][1:]):
                row["result_digest"] = sha
            state["combat"]["engine"] = migration['current_engine']
            history = state["combat"].setdefault('engine_migrations', [])
            history.append({'mode': migration['mode'], 'saved_engine': migration.get('saved_engine'),
                            'current_engine': migration['current_engine'],
                            'differences': list(migration.get('differences') or [])})
            del history[:-8]
            migration_result = {key: migration[key] for key in
                ('mode', 'saved_engine', 'current_engine', 'differences', 'text')}
        if action.kind == "end_combat":
            result = {"outcome": snapshot["view"]["final_outcome"], "closed_combat": state["combat"]}
            if definition and result['outcome']['ended_reason']=='victory':
                encounter_id = initial['encounter_id']
                reward = definition.encounters[encounter_id].reward
                outcome = result['outcome']
                if outcome['ended_reason'] != 'victory' or outcome['residual_hp'].get('hero', 0) <= 0:
                    raise ValueError('Registered reward requires a surviving victorious hero')
                if any(r['id'] == reward.id for r in state['rewards']):
                    raise ValueError('Reward has already been claimed')
                # A milestone scenario hands out no experience at all; the
                # episode itself grants the level. Booking the engine sum here
                # would reintroduce the very model the book replaced, and an
                # unpriced printed reward would become a zero receipt - the
                # dishonesty the contract forbids. Such a reward is reported
                # as awaiting adaptation instead.
                amount = 0
                if getattr(reward, 'kind', 'native_encounter_xp') == 'printed_experience':
                    amount = int(reward.experience or 0)
                elif not _milestone_mode(definition):
                    amount = outcome['xp_awarded'].get('hero', 0)
                if amount == 0:
                    # Not a paid reward: say so instead of banking a zero.
                    result['reward'] = None
                    result['reward_pending'] = dict(
                        id=reward.id, page=getattr(reward, 'page', None),
                        reason='Награда книги не оценена в текущих данных; она ожидает адаптации.')
                else:
                    # r217: the reward rule the player chose prices what is booked.
                    # The receipt keeps the amount actually paid, so the ledger and
                    # the experience counter still agree; the difference from the
                    # engine's own sum is named in the journal.
                    campaign_rules = fight_rules(initial, state)
                    printed_amount = amount
                    amount = reward_amount(campaign_rules, printed_amount)
                    line = reward_note(campaign_rules, printed_amount, amount)
                    if line:
                        result['reward_note'] = line
                    receipt = RewardReceipt(id=reward.id, encounter_id=encounter_id,
                        combat_id=initial['session_id'], experience=amount,
                        outcome_sha256=digest(outcome), source=reward.source).model_dump(mode='json')
                    state['experience'] += amount
                    state['rewards'].append(receipt)
                    result['reward'] = receipt
            state["combat"] = None
    if companion_turns:
        # r215: the companion's own turns, as facts: who acted, with which
        # numbers, and what the engine refused. The caller puts them into the
        # journal sentence of this same command.
        result = {**(result or {}), 'companion_turns': companion_turns}
    if migration_result and isinstance(result, dict):
        # The player-visible half of the migration: the caller puts this text
        # into the journal event and keeps the record with the command result.
        result = {**result, "engine_migration": migration_result}
    # r214: the hero's party entry is rebuilt from ``hero``/``sheet`` on every
    # validation. Returns must be canonical, otherwise a turn that moved the
    # hero (a heal, a rest, a hit) leaves the party one turn behind and the
    # screen reads the old hit points. One validation at the single exit.
    state = DurableState.model_validate(state).model_dump(mode="json")
    return {"state": state, "result": result, "events": events}


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(4 * 1024 * 1024 + 1))
        response = asyncio.run(execute(payload))
        print(json.dumps({"ok": True, **response}, ensure_ascii=False, allow_nan=False))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:1800]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
