"""Public Python-only Solo/Nat20 seam. No UI, LLM, new DB or combat mathematics.

Nat20 has no durable handle API. Each operation replays its bounded JSON recipe
in a fresh process, verifies the prior complete read-model, and commits through
Solo's existing transaction. A failed worker or failed commit leaves no live
provider state which could leak into a retry.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from dnd5e_engine import PartyMemberSpec, parse_cell

from .command_engine import CommandEngine, StateVersionError
from .game_commands import GameCommand as SoloGameCommand
from .contracts import Hero
from .engine_memory_contracts import pair
from . import nat20_recovery_service as recovery_service
from .nat20_content import CHECKS, ROOT, canonical, digest
from .nat20_contracts import DurableState, GameCommand, GameEvent, GameSnapshot
from .nat20_campaign import load_definition, validate_definition
from .rulesets import BUILTINS, active


def _weapon_ids():
    from .nat20_content import weapons
    return weapons()


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
    def capabilities(current):
        from .nat20_prototype import experimental,spell_status
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
            actions=['check','start_combat','move','attack','dash','end_turn','end_combat','short_rest','long_rest','level_up']+
                (['native_action'] if experimental(definition) else ['use_feature']),
            native_operations=list(get_args(NativeAction.model_fields['operation'].annotation)) if experimental(definition) else [],
            spell_execution={s:spell_status(s) for s in definition.get('prepared_spells',[])},
            known_issues={'hide':'Native does not spend Action','bless':'Incorrect save bonus / concentration and recast defects',
                'rage':'Native activation emits a disabled effect; this feature refuses without committing its cost',
                'monster_attack_bonus':'New encounters auto-project uniform constant to-hit with public Native stat-block resolver. Only unrepresentable profiles still require explicit source or refuse; existing saved bindings do not migrate.',
                'spell_components':'Native component metadata is exposed; full V/S/M inventory and free-hand legality is not implemented',
                'build_choices':'Preparation counts and class/feat choices are gated by the wizard contract; the advanced catalogue form still accepts a hand-assembled sheet'},
            verified={'spell_lists':'The wizard offers a class only the spells its printed list names (integration/nat20/playable/class-spell-lists.json, 877 links, CC-BY-4.0), and only up to a slot the build really has',
                'extracted_opponents':'An opponent extracted from any book reaches play through the shared mapping: a resolved corpus monster becomes an encounter actor, and an unresolved name stays a visible adaptation instead of a lookalike',
                'extracted_rewards':'A printed reward becomes a priced rule only when the cited quote proves the number; otherwise it is reported as awaiting adaptation, never booked as zero',
                'entry_stage':'The prepared entry is re-verified for any document: the cited file must still hash to itself and the opening text to what was reviewed'},
            targets=[a['entity_id'] for a in runtime['combat']['view']['initiative']] if runtime['combat'] else ['hero'])

    @staticmethod
    def _worker(payload):
        # The caller's environment is inherited, but PYTHONPATH must not be: if it
        # points at another copy of the project, 'python -m solo.nat20_worker'
        # could import that copy's package and this application would run code
        # from a folder it does not own. The worker always runs its own code.
        environment = dict(os.environ, SOLO_TEST="1", PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        # The same holds for the directory the interpreter starts from: a foreign
        # PYTHONSAFEPATH-less run ('python -c', REPL) puts '' first in sys.path,
        # and that would let a file in the working folder shadow a real module.
        environment.pop("PYTHONSAFEPATH", None)
        invocation = [sys.executable, '--nat20-worker'] if getattr(sys, 'frozen', False) else [sys.executable, "-X", "utf8", "-B", "-m", "solo.nat20_worker"]
        try:
            process = subprocess.run(invocation,
                input=canonical(payload), text=True, encoding="utf-8", capture_output=True, cwd=ROOT,
                env=environment, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired as expired:
            # The engine hung. Its own message names a worker process the player
            # never started, in English; the table needs to know the turn did
            # not happen and can be retried.
            raise ValueError('Движок не ответил вовремя; ход не выполнен, состояние не изменено.') from expired
        if process.returncode:
            raise ValueError("Nat20 worker could not complete; no state committed")
        try:
            response = json.loads(process.stdout)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Invalid Nat20 worker response") from error
        if response.get("ok") is not True:
            raise ValueError(response.get("error", "Nat20 operation rejected"))
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
            raise ValueError("Campaign has not explicitly selected the Nat20 backend")
        preset = active(current["state"])
        if preset.model_dump() != BUILTINS["dnd2024"].model_dump():
            raise ValueError("Nat20 is pinned to dnd2024; custom/2014 presets require explicit migration")
        if current["state"].get("combat", {}).get("active"):
            raise ValueError("Another combat backend is active")
        runtime = DurableState.model_validate(current["state"]["nat20"]).model_dump(mode="json")
        if runtime['definition'] is not None:
            validate_definition(runtime['definition'])
        return runtime

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
            # What the mechanics can resolve is the engine's own registry, not the
            # place's printed checks: place.checks are what the material prints, and
            # their absence never meant the hero could not attempt a skill.
            "available_checks": sorted(runtime['definition']['checks'] if runtime.get('definition') else CHECKS) if not combat else [],
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
        restored = self._worker({"mode": "verify", "state": runtime})["state"]
        if restored != runtime:
            raise ValueError("Restored runtime differs from canonical snapshot")
        # Complete native view includes initiative, turn economy, slots, HP,
        # conditions, counters and active effects; it is a trusted Python read.
        return {"snapshot": self.snapshot(current), "runtime": deepcopy(runtime), "version": current["version"]}

    @classmethod
    def _preflight(cls, current, command):
        """Shared guards for a committed command or a disposable screen probe."""
        runtime = cls._bound(current)
        state = current['state']
        if current['version'] != command.expected_version:
            raise StateVersionError("Stale state; Nat20 command not executed")
        from .first_playable import require_full_scene
        require_full_scene(state)
        if runtime['hero']['hp_current'] <= 0:
            combat=runtime['combat']
            dead=bool(combat and 'hero' in combat['view']['dead_ids'])
            if not combat or command.action.kind not in (('end_combat',) if dead else ('end_turn',)):
                raise ValueError('Hero at 0 HP: advance native death saves with end_turn; dead heroes may only close combat')
        from .lantern_scene import guard
        guard(state, command.action)
        # A prepared scenario runs its own reviewed events through adventure_engine
        # after each committed command, so Nat20 actions are allowed inside it. Only
        # the legacy deferred world scheduler has no handler on this path.
        if state.get('world', {}).get('events'):
            raise ValueError("Отложенные мировые события прежнего обработчика не поддерживаются этим циклом.")
        if command.action.kind == 'long_rest' and state.get('elapsed_minutes', 0) + 480 - state.get('last_long_rest', -1440) < 1440:
            raise ValueError("Long rest already completed within 24 hours")
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
                                 'session_id': current['id']})
        return [dict(type='scripted',kind='nat20_command',native_events=response['events'],
                     native_result=response['result'])]

    @staticmethod
    def _recover(campaign_state, db, campaign, campaign_id, events, runtime, *, started=0):
        """Score each event exactly once and persist it in the caller transaction.

        The charge change and the recovery_events row are written in the SAME
        BEGIN IMMEDIATE transaction as the command, so a crash between
        calculation and save cannot desynchronise them.
        """
        import time as _time
        from dnd5e_engine.lib_loader import BundledAssetLoader
        from dnd5e_engine import recover_item_and_activity_pools
        from .nat20_recovery import event_id, seed_for, check_order
        import random as _random
        loader = BundledAssetLoader()
        hero = runtime.get('hero') or {}
        counters = hero.setdefault('custom_counters', {})
        # Rows kept as history are not automatically the active timeline's.
        # A load or an undo puts the campaign back before turns that already
        # spent those minutes, and their events must not block a fresh legal
        # turn. The duplicate check itself stays on, scoped to the live line.
        # The command's starting clock, taken before anything moved it.
        frontier = int(started or 0)
        abandoned = recovery_service.abandoned(db, campaign_id, frontier)
        scored_here = set()
        for event, minutes in events:
            ident = event_id(campaign_id, event, minutes)
            if ident in scored_here:
                # Two identical events inside one command would collapse onto
                # the same row, silently losing one of the two charges.
                raise ValueError('Recovery event %r appears twice in one command' % ident)
            scored_here.add(ident)
            row = db.execute('SELECT game_minutes,event FROM recovery_events WHERE id=?', (ident,)).fetchone()
            if row is not None and ident not in abandoned:
                raise ValueError('Recovery event %r was already applied' % ident)
            last = db.execute('SELECT MAX(game_minutes) FROM recovery_events WHERE campaign=?'
                              ' AND (turn IS NULL OR turn IN (SELECT id FROM events WHERE campaign=?'
                              ' AND undone=0))',
                              (campaign_id, campaign_id)).fetchone()[0]
            # Strictly less: a different event at the SAME minute is legitimate
            # (a rest ending exactly at dawn). Redelivery is caught by the id above.
            if last is not None and int(minutes) < int(last):
                raise ValueError('Stale recovery event %r at minute %d: the campaign already'
                                 ' processed an event at minute %d' % (ident, minutes, last))
            slugs = recovery_service.carried_slugs(campaign_state)
            rng = _random.Random(seed_for(campaign_id, ident, (runtime.get('seed') or 0)))
            before = {k: dict(v) for k, v in counters.items()}
            if not slugs:
                # An empty list is a state we cannot explain: silently scoring nothing is
                # exactly how the state/runtime mix-up stayed invisible. Refuse loudly.
                raise ValueError('No hero item slugs reached the engine for recovery: the'
                                 ' campaign state carries no economy items and no build equipment')
            recovered = recover_item_and_activity_pools(before, event, slugs, loader, rng=rng)
            delta = {}
            for key, spent in recovered.items():
                was = int((before.get(key) or {}).get('spent', 0))
                counters[key] = {'spent': int(spent)}
                if int(spent) != was:
                    delta[key] = {'before': was, 'after': int(spent)}
            resolved = dict(event=event, minutes=int(minutes), event_id=ident, items=list(slugs),
                            recovered=recovered, delta=delta)
            # The turn that scored this event: the active timeline is defined by
            # the turns that are still applied, so the row records its owner.
            turn = db.execute('SELECT id FROM actions WHERE campaign=? ORDER BY rowid DESC LIMIT 1',
                              (campaign_id,)).fetchone()
            # A row of an abandoned future is replaced, not duplicated: the id
            # is the event's identity, and the live line now owns this minute.
            db.execute('INSERT OR REPLACE INTO recovery_events(id,campaign,event,game_minutes,seed,engine,'
                       'result,delta,created,turn) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (ident, campaign_id, event, int(minutes),
                        seed_for(campaign_id, ident, (runtime.get('seed') or 0)),
                        'dnd5e-engine 0.7.0+solo.2',
                        json.dumps(resolved, ensure_ascii=False),
                        json.dumps(delta, ensure_ascii=False), _time.time(),
                        turn[0] if turn else None))
        # The frontier only remembers what was actually scored: a later command
        # that gains minutes elsewhere catches up from here instead of losing
        # the boundaries in between.
        if events:
            frontier = max(int(item[1]) for item in events)
            runtime['recovery_minute'] = max(int(runtime.get('recovery_minute', 0) or 0), frontier)
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
                                         "session_id": current["id"]})
                state["nat20"] = response["state"]
                state["hero"] = self._hero(response["state"]).model_dump()
                from .lantern_scene import confirmed
                scene_memories = confirmed(state, command.action, response, 'nat20:'+command.request_id)
                if command.action.kind in ("short_rest", "long_rest"):
                    started = int(state.get("elapsed_minutes", 0) or 0)
                    state["elapsed_minutes"] = started + (60 if command.action.kind == "short_rest" else 480)
                    if command.action.kind == "long_rest":
                        state["last_long_rest"] = state["elapsed_minutes"]
                    events = [("sr" if command.action.kind == "short_rest" else "lr",
                               state["elapsed_minutes"])]
                    # Catch up from the scored frontier as well: minutes gained
                    # outside the recovery paths must not be skipped by a rest.
                    # Catch up on anything the cursor has not scored yet, then
                    # cover this rest. Nothing already scored is planned again.
                    # Never plan a minute the cursor already scored.
                    cursor = recovery_service.cursor_of(state)
                    frontier = min(started, cursor)
                    events += recovery_service.plan(frontier, state["elapsed_minutes"], state,
                                                    skip=cursor)
                    # Where this turn began: the window an undone future must clear.
                    # Kept off the durable state, which rejects stray keys.
                    runtime_started = started
                    # Chronological order: a rest that spans dawn must score the
                    # calendar event at its own minute, not after the rest.
                    events.sort(key=lambda row: row[1])
                    # Pass the live nat20 mapping, not a shallow copy: _recover
                    # advances the cursor on it and that advance must persist.
                    durable = state["nat20"]
                    self._recover(state, db, campaign, current["id"], events, durable,
                                 started=runtime_started)
            # state here is the FULL campaign state (the transition argument), whose
            # economy and build carry the hero items; runtime is only state["nat20"].
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
                    effects.append({"type": "roll", "payload": {"kind": "save" if check.get('kind')=='saving_throw' else "check", "dice": [check["natural_roll"]],
                        "total": check["roll_total"], "success": check["success"]}})
                for roll in response["events"]:
                    if roll['type'] == 'concentration_check' and roll.get('natural') is not None:
                        effects.append({'type': 'roll', 'payload': {'kind': 'save', 'dice': [roll['natural']],
                            'total': roll['roll_total'], 'success': roll['succeeded']}})
                    if roll["type"] == "attack_rolled" and roll.get("natural") is not None:
                        # Native API exposes only the kept d20, not discarded
                        # advantage draws. Never fabricate the missing die.
                        effects.append({"type": "roll", "payload": {"kind": "attack", "dice": [roll["natural"]],
                            "total": roll["roll_total"], "success": roll["is_hit"]}})
                from .nat20_prototype import experimental
                source_ref = ("Nat20 0.7.0 / pinned bundled canonical provenance / " if experimental(runtime.get('definition')) else "Nat20 0.7.0 / Foundry 5.3.3 SRD 5.2 / ") + runtime['release_digest']
                if runtime.get('definition'):
                    definition = runtime['definition']
                    source_ref += ' / campaign ' + definition['id'] + ' / authored ' + definition['source']['id'] + '@' + definition['source']['version']
                event = GameEvent.model_validate({"id": event_id, "campaign_id": campaign, "branch_id": branch,
                    "sequence": sequence, "parent_event_id": head["event_id"] if head else None,
                    "type": "command_resolved", "payload": {"command": command.action.kind,
                        "outcome": "Nat20 confirmed: " + command.action.kind,
                        "effects": effects},
                    "subjects": [a.id for a in after.actors], "source_ref": source_ref,
                    "game_minutes": int(state.get("elapsed_minutes", 0)), "scope": "private", "known_by": ["hero"]})
                pair(event, after)
                state["engine_snapshot"] = after.model_dump(mode="json")
                return [{"type": "scripted", "kind": "nat20_command", "memory_evidence": scene_memories, "game_event": event.model_dump(mode="json"),
                         "game_snapshot": after.model_dump(mode="json"), "game_command": shared.model_dump(mode="json"),
                         "native_events": response["events"], "native_result": response["result"],
                         "ai_proposal_applied": False}]
            journal = self.store.engine_event(db, current, command.request_id, original or "Nat20 " + command.action.kind, transition, signature)
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
        # store.decode_action has already parsed the metrics column.
        recorded = (previous["metrics"] or {}).get("engine_signature")
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
                 milestone=None):
        """Commit one advancement through the worker and the shared journal.

        The worker computes the whole new runtime and refuses before returning
        it if any rule fails, so the host never stores a half-advanced hero.
        The journal makes the change replayable, and a repeated request_id is
        answered from the journal instead of granting a second level.
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
            current = self.store.campaign(campaign)
            # The replay guard reads the journal row for this request_id, not the
            # campaign state: only an applied action carries the stored answer and
            # the signature that proves the id was used by the same command.
            used = self.store.request(campaign, request_id)
            previous = self._replayed_level_up(campaign, used, signature) if used else None
            if previous is not None:
                return previous
            if current["version"] != expected_version:
                raise ValueError("Этот план устарел: состояние изменилось. Отмените и повторите ввод.")
            runtime = dict(current["state"].get("nat20") or {})
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
                })
                state["nat20"] = response["state"]
                state["hero"] = self._hero(response["state"]).model_dump()
                return [dict(type="scripted", kind="nat20_level_up",
                             native_result=response["result"], native_events=[])]

            journal = self.store.engine_event(
                db,
                current,
                request_id,
                "Nat20 повышение уровня: " + class_slug,
                transition,
                signature,
            )
        row = journal["results"][0]
        fresh = self.store.campaign(campaign)
        return dict(row["native_result"], version=fresh["version"], request_id=request_id)

__all__ = ["Nat20GameEngine", "GameCommand", "GameEvent", "GameSnapshot"]