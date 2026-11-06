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

from .nat20_content import CHECKS, digest, release
from .nat20_contracts import DurableState, GameCommand
from .nat20_campaign import validate_definition, RewardReceipt
from .nat20_prototype import experimental, RELEASE as PROTOTYPE_RELEASE, known_issue


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


async def replay(combat):
    initial = combat["initial"]
    opened = await start_combat(session_id=initial["session_id"], rng_seed=initial["seed"],
        party=[PartyMemberSpec.model_validate(item) for item in initial["party"]],
        encounter=[EncounterMemberSpec.model_validate(item) for item in initial["encounter"]],
        grid_scene=GridScene.model_validate(initial["grid"]),
        active_effects=[ActiveEffect.model_validate(item) for item in initial["active_effects"]])
    handle = opened.handle
    events = plain(drain_pending_events(handle))
    if digest({"snapshot": read_combat(handle), "events": events}) != combat["initial_digest"]:
        raise ValueError("Nat20 initial replay diverged")
    if len(combat["commands"]) > 256:
        raise ValueError("Combat replay limit exceeded (256); no unsafe partial restore")
    for record in combat["commands"]:
        action = GameCommand.model_validate({"request_id": "replay", "expected_version": 0, "action": record["action"]}).action
        await apply_action(handle, action, weapon=initial.get('attack_weapon', 'longsword'),
                           features=initial.get('allowed_features', []), spells=initial.get('allowed_spells', []), prototype=initial.get('prototype'))
        events = plain(drain_pending_events(handle))
        if digest({"snapshot": read_combat(handle), "events": events}) != record["result_digest"]:
            raise ValueError("Nat20 replay diverged; command was not applied")
    if read_combat(handle) != {key: combat[key] for key in ("view", "active_effects")}:
        raise ValueError("Nat20 durable snapshot does not match replay")
    return handle


async def apply_action(handle, action, *, weapon='longsword', features=(), spells=(), prototype=None):
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
        if current == "hero":
            await submit_player_intent(handle, "hero", PlayerIntent(intent_type="pass"))
        for _ in range(len(view.initiative) + 1):
            view = get_live(handle)
            if view.ended or 'hero' in view.dead_ids or view.initiative[view.current_turn_index].entity_id in view.party_ids:
                return
            await advance_monster_turn(handle)
        raise ValueError("Monster turn did not converge")
    if current != "hero":
        raise ValueError("Hero is not the current actor; end_turn advances the registered monster policy")
    if action.kind == 'native_action':
        if prototype is None:
            raise ValueError('Generic native actions require explicit experimental campaign')
        issue=known_issue('actions',action.operation)
        if issue:raise ValueError(issue)
        values=action.model_dump(exclude_none=True,exclude={'kind','operation','position'})
        for field in ('target_id','target_ids','excluded_target_ids'):
            ids=values.get(field,[])
            ids=[ids] if isinstance(ids,str) else ids
            if set(ids)-set(view.party_ids)-set(view.encounter_ids):
                raise ValueError('Unknown native target')
        for field in ('weapon_id','item_id'):
            if values.get(field) and values[field] not in prototype['equipment']:
                raise ValueError('Native equipment reference is not owned by this build')
        if action.operation=='attack' and not action.weapon_id and not action.stat_block_action_id:
            values['weapon_id']=weapon
        # Native's repertoire gate checks the actual class/species/subclass
        # grants. A second static adapter feature whitelist hides valid handlers.
        if action.operation=='use_feature':
            issue=known_issue('features',action.feature_id)
            if issue and issue.startswith('blocked:'):raise ValueError(issue)
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
        if state["combat"]:
            await replay(state["combat"])
        return {"state": state}
    if payload["mode"] == "level_up":
        # A level change is a definition change, so it goes through the same
        # validated path as creation instead of a partial state patch.
        from .nat20_levelup import level_up

        return level_up(dict(payload, state=state), loader, fingerprint)
    if payload['mode'] == 'availability':
        await execute(dict(mode='verify', state=state), (loader, fingerprint, profile))
        available = []
        for candidate in payload['commands']:
            try:
                await execute(dict(mode='execute', state=state, command=candidate,
                                   session_id=payload['session_id']), (loader, fingerprint, profile))
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
    if action.kind in ("check", "short_rest", "long_rest"):
        if definition and not is_prototype and definition.spellcasting and state['active_effects']:
            raise ValueError('Bless elapsed-time boundary is pending; no implicit effect expiry')
        if state["combat"]:
            raise ValueError("Standalone checks/rest cannot run during this combat")
        if state["hero"]["hp_current"] <= 0:
            raise ValueError('Standalone check/rest cannot bypass dying or death; active native combat uses end_turn for death saves')
        rng = random.Random(int(digest([state["seed"], command.request_id, command.expected_version]), 16))
        if action.kind == "check":
            checks = definition.checks if definition else CHECKS
            if action.check_id not in checks:
                raise ValueError("Check is not in the trusted scene registry")
            if definition:
                check = checks[action.check_id]
                kind, skill, ability, dc = check.kind, check.skill, check.ability, check.dc
            else:
                kind, skill, ability, dc = CHECKS[action.check_id]
                kind, skill, ability, dc = CHECKS[action.check_id]
            # A difficulty the master ruled travels with the command. It is used
            # only when the master says so; a printed difficulty is the book's and
            # is never overridden by the speaker of a sentence.
            if action.source == 'master' and action.dc:
                dc = action.dc
            elif check_dc := getattr(action, 'dc', None):
                if check_dc != dc:
                    raise ValueError('Difficulty may only be set by the master ruling, not passed alongside a printed check')
            from .nat20_checks import resolve_state_check
            result=plain(resolve_state_check(state,kind=kind,skill=skill,ability=ability,dc=dc,
                advantage=check.advantage if is_prototype else False,
                disadvantage=check.disadvantage if is_prototype else False,rng=rng))
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
        foe = EncounterMemberSpec(entity_id=foe_id, entity_type="Monster", name=monster.name,
            initiative=None, hp_current=monster.hp, hp_max=monster.hp, ac=monster.ac,
            attack_bonus=profile.get("attack_bonus",0),
            dexterity=monster.ability_scores.dex, zone_id=foe_zone, monster_template_slug=monster.slug, xp_value=xp,
            base_speed=monster.movement.walk or 0)
        foes=[foe]
        if is_prototype:
            foes=[]
            for actor in encounter.actors:
                monster=loader.get_monster(actor.monster)
                if monster is None:raise ValueError('Unknown encounter monster: '+actor.monster)
                foes.append(EncounterMemberSpec(entity_id=actor.id,entity_type='Monster',name=monster.name,
                    initiative=None,hp_current=monster.hp,hp_max=monster.hp,ac=monster.ac,
                    attack_bonus=actor.attack_bonus if actor.attack_bonus is not None else 0,
                    dexterity=monster.ability_scores.dex,zone_id=cell_id(actor.position.x,actor.position.y),
                    monster_template_slug=monster.slug,xp_value=actor.xp_value,creature_type=monster.creature_type,
                    base_speed=monster.movement.walk or 0))
        party = dict(state["hero"], zone_id=hero_zone)
        initial = {"session_id": payload["session_id"] + ":" + command.request_id,
                   "seed": state["seed"], "party": [party], "encounter": [f.model_dump(mode="json") for f in foes],
                   "grid": grid.model_dump(mode="json"),
                   "active_effects": state["active_effects"]}
        if definition:
            initial.update(attack_weapon=definition.attack_weapon, allowed_features=definition.features,
                           encounter_id=action.encounter_id, definition_id=definition.id)
            if is_prototype:
                initial.update(allowed_spells=definition.prepared_spells,prototype={'equipment':list(definition.build.equipment),
                    'unbound_monster_attacks':[a.id for a in encounter.actors if a.attack_bonus is None]})
            elif definition.spellcasting:
                initial['allowed_spells'] = definition.spellcasting.prepared
        opened = await start_combat(session_id=initial["session_id"], rng_seed=initial["seed"],
            party=[PartyMemberSpec.model_validate(party)], encounter=foes,
            grid_scene=GridScene.model_validate(initial["grid"]),
            active_effects=[ActiveEffect.model_validate(effect) for effect in initial["active_effects"]])
        events = plain(drain_pending_events(opened.handle))
        snapshot = read_combat(opened.handle)
        state["combat"] = {"initial": initial, "initial_digest": digest({"snapshot": snapshot, "events": events}),
                           "commands": [], **snapshot}
        sync_hero(state, snapshot)
    else:
        if state["combat"] is None:
            raise ValueError("No active Nat20 combat")
        if len(state["combat"]["commands"]) >= 256:
            raise ValueError("Combat replay limit reached; unsupported extension requires review")
        handle = await replay(state["combat"])
        initial = state['combat']['initial']
        if action.kind == 'end_combat' and definition and not is_prototype and definition.spellcasting and state['active_effects']:
            raise ValueError('Post-combat concentration clock is pending; expire/drop before victory')
        await apply_action(handle, action, weapon=initial.get('attack_weapon', 'longsword'),
                           features=initial.get('allowed_features', []), spells=initial.get('allowed_spells', []), prototype=initial.get('prototype'))
        events = plain(drain_pending_events(handle))
        snapshot = read_combat(handle)
        failures = [event for event in events if event["type"].endswith("_failed")]
        unbound=(initial.get('prototype') or {}).get('unbound_monster_attacks',
            [a.id for a in definition.encounters[initial['encounter_id']].actors if a.attack_bonus is None] if is_prototype else [])
        if is_prototype and any(e['type']=='attack_rolled' and e['attacker_id'] in unbound for e in events):
            from .nat20_monsters import attack_profile
            refused=next(e['attacker_id'] for e in events if e['type']=='attack_rolled' and e['attacker_id'] in unbound)
            actor=next(a for a in definition.encounters[initial['encounter_id']].actors if a.id==refused)
            reason=attack_profile(actor.monster)['reason'] or 'Saved encounter predates automatic binding; no implicit data migration'
            raise ValueError('Native monster weapon attack bonus is unbound for '+actor.monster+': '+reason+'. No state committed.')
        if is_prototype and not any(e['type']!='intent_submitted' for e in events) and snapshot=={key:state['combat'][key] for key in ('view','active_effects')}:
            raise ValueError('Native operation produced no supported mechanical effect')
        enemies = {a['entity_id'] for a in initial['encounter']}
        if failures:
            raise ValueError("Nat20 refused operation: " + json.dumps(failures, ensure_ascii=False))
        if action.kind == 'cast_spell' and not any(e['type'] == 'effect_applied' and e['effect']['changes'] for e in events):
            raise ValueError('Bless cast produced no mechanical effect; no state committed')
        state["combat"]["commands"].append({"action": action.model_dump(mode="json"),
            "result_digest": digest({"snapshot": snapshot, "events": events})})
        state["combat"].update(snapshot)
        sync_hero(state, snapshot)
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
                    receipt = RewardReceipt(id=reward.id, encounter_id=encounter_id,
                        combat_id=initial['session_id'], experience=amount,
                        outcome_sha256=digest(outcome), source=reward.source).model_dump(mode='json')
                    state['experience'] += amount
                    state['rewards'].append(receipt)
                    result['reward'] = receipt
            state["combat"] = None
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
