"""Command/DTO adapter over existing Python engines and their atomic journal.

No dice arithmetic, AI, new state store, or prose-to-state extraction lives here.
One command_resolved GameEvent groups typed effects in one existing transaction.
"""
import copy
import hashlib


def _BASE_CHECKS():
    """Every check id the D&D 2024 engine can resolve for a character."""
    try:
        from .nat20_content import CHECKS
        return set(CHECKS)
    except Exception:
        return set()


def _BASE_WEAPONS():
    """Every weapon id the D&D 2024 dataset names, for attack_weapon."""
    try:
        from .nat20_content import weapons
        return weapons()
    except Exception:
        return {}

from .contracts import Hero, Step
from .combat import CombatEngine
from .combat_service import CombatService, CombatCommand
from .engine_memory_contracts import GameEvent, GameSnapshot, pair
from .first_playable import require_full_scene
from .game_commands import GameCommand
from .rulesets import RulesetEngine, active, digest


class StateVersionError(ValueError):
    pass


class CommandEngine:
    def __init__(self, store, *, rng=None):
        self.store, self.rng = store, rng
        self.combat = CombatService(store, rng=rng)

    @staticmethod
    def snapshot(current, *, event_id=None, branch_id=None, sequence=None):
        state = current['state']
        head = state.get('engine_snapshot', {})
        battle = state.get('combat', {})
        engine = CombatEngine(state) if battle else None
        hero_actor = engine.actors.get('hero') if engine else None
        actors = []
        raw_actors = list(engine.actors.values()) if engine else [None]
        for actor in raw_actors:
            aid = actor.id if actor else 'hero'
            hero = aid == 'hero'
            stats = Hero.model_validate(state['hero']) if hero else actor.stats
            seen = hero or bool(hero_actor and engine.sees(hero_actor, actor))
            access = dict(scope='public' if seen else 'secret', known_by=['hero', aid] if seen and not hero else [aid])
            own = dict(scope='private', known_by=[aid])
            resources = {key: dict(current=value.current, **own) for key, value in stats.resources.items()}
            resources['hit_dice'] = dict(current=stats.level if stats.hit_dice_available is None else stats.hit_dice_available, **own)
            if actor and battle.get('active'):
                resources.update({key: dict(current=int(value), **own) for key, value in
                                  [('action', actor.action), ('ammo', actor.ammo), ('movement_spent', actor.movement_spent)]})
            actors.append(dict(id=aid, kind='hero' if hero else 'npc', name=stats.name,
                               hp=dict(current=stats.hp, maximum=stats.max_hp, **access),
                               resources=resources, conditions=[dict(id=c, **access) for c in stats.conditions],
                               position=dict(coordinates=actor.position.key() if actor else (0, 0, 0), **access), **access))
        order = battle.get('order', [])
        return GameSnapshot.model_validate(dict(
            campaign_id=current['id'], event_id=event_id or head.get('event_id', 'initial'),
            branch_id=branch_id or head.get('branch_id', 'main'), sequence=sequence or head.get('sequence', 1),
            location=state['location'], actors=actors,
            # The location may narrow the discipline to what the scene offers.
            # When it says nothing, fall back to the engine's own registry: an
            # empty list told the player a full character sheet could make no
            # roll at all.
            available_checks=sorted(next((p.get('checks', {}) for p in state['locations']
                                          if p['name'] == state['location']), {}) or _BASE_CHECKS()),
            # Without this list a player could not name a dagger: the engine
            # resolved 86 weapons, the chat offered four hard-coded ones.
            available_weapons=sorted(_BASE_WEAPONS()),
            combat=dict(id=battle.get('command_combat_id', 'none'), active=bool(battle.get('active')),
                        round=battle.get('round', 0), turn=order[battle['cursor']] if order and battle.get('active') else None,
                        scope='public', known_by=['hero'])))

    @staticmethod
    def visible(snapshot):
        """A hero projection, preserving the memory boundary's per-field ACL."""
        def allowed(value):
            return value.scope not in ('secret', 'gm') and 'hero' in value.known_by
        actors = []
        for actor in snapshot.actors:
            if not allowed(actor):
                continue
            row = dict(id=actor.id, kind=actor.kind, name=actor.name,
                       resources={k: v.current for k, v in actor.resources.items() if allowed(v)},
                       conditions=[c.id for c in actor.conditions if allowed(c)])
            if allowed(actor.hp):
                row['hp'] = actor.hp.model_dump(exclude={'scope', 'known_by'})
            if allowed(actor.position):
                row['position'] = list(actor.position.coordinates)
            actors.append(row)
        combat = snapshot.combat.model_dump(exclude={'scope', 'known_by'}) if allowed(snapshot.combat) else None
        if combat and combat['turn'] not in {a['id'] for a in actors}:
            combat['turn'] = None
        result = dict(event_id=snapshot.event_id, branch_id=snapshot.branch_id, sequence=snapshot.sequence,
                    location=snapshot.location, actors=actors, combat=combat, available_checks=snapshot.available_checks)
        if snapshot.effects is not None:
            result['effects'] = [e.model_dump(exclude={'scope', 'known_by'}) for e in snapshot.effects if allowed(e)]
        return result

    def execute(self, campaign, command, *, original=None):
        command = GameCommand.model_validate(command.model_dump() if isinstance(command, GameCommand) else command)
        from .command_backend import native_backend
        backend = native_backend(self.store, self.store.campaign(campaign))
        if backend is not None:
            # Delegate before opening the legacy transaction: Native owns its
            # existing single journal/pair commit and rereads authoritative state.
            return backend.execute(campaign, command, original=original)
        signature = digest(command.model_dump(exclude={'request_id'}))
        with self.store.connection(True) as db:
            current = self.store.campaign(campaign, db)
            def transition(state):
                if current['version'] != command.expected_version:
                    raise StateVersionError('Состояние изменилось. Уточните действие по обновлённой сцене; команда не выполнена.')
                if 'nat20' in state:
                    raise ValueError('Эта кампания использует backend Nat20.')
                require_full_scene(state)
                if state.get('world', {}).get('events') or state.get('adventure'):
                    raise ValueError('Этот вертикальный цикл пока не поддерживает сценарные и отложенные мировые события.')
                before = self.snapshot(current)
                results = self.resolve(current, command)
                head = state.get('engine_snapshot')
                sequence = head['sequence'] + 1 if head else 1
                branch = head['branch_id'] if head else 'main'
                used = db.execute('SELECT MAX(sequence) FROM memory_engine_events WHERE campaign=? AND branch_id=?',
                                  (campaign, branch)).fetchone()[0]
                if used is not None and used >= sequence:
                    branch = 'fork-' + hashlib.sha256((branch + ':' + command.request_id).encode()).hexdigest()[:24]
                event_id = 'command:' + command.request_id
                after = self.snapshot(current, event_id=event_id, branch_id=branch, sequence=sequence)
                effects = self.effects(before, after, results)
                outcome = ' '.join(r.get('text', '') for r in results).strip() or 'Команда выполнена движком.'
                event = GameEvent.model_validate(dict(
                    id=event_id, campaign_id=campaign, branch_id=branch, sequence=sequence,
                    parent_event_id=head['event_id'] if head else None, type='command_resolved',
                    payload=dict(command=command.action.kind, outcome=outcome, effects=effects),
                    subjects=sorted({'hero', *(e['payload']['target'] for e in effects if 'target' in e['payload']),
                                     *(a for e in effects for a in e['payload'].get('participants', []))}),
                    source_ref='Python Game Engine:' + command.action.kind,
                    game_minutes=int(state.get('elapsed_minutes', 0)), scope='private', known_by=['hero']))
                pair(event, after)
                state['engine_snapshot'] = after.model_dump(mode='json')
                return [dict(type='scripted', game_event=event.model_dump(mode='json'),
                             game_snapshot=after.model_dump(mode='json'), game_command=command.model_dump(mode='json'))]
            journal = self.store.engine_event(db, current, command.request_id, original or command.action.kind, transition, signature)
        result = journal['results'][0]
        return dict(journal=journal, events=[GameEvent.model_validate(result['game_event'])],
                    snapshot=GameSnapshot.model_validate(result['game_snapshot']))

    def resolve(self, current, command):
        from .command_backend import native_backend
        backend = native_backend(self.store, current)
        if backend is not None:
            # Availability callers require a result list without a journal or
            # persistent RNG change. Only execute() may commit a Native command.
            return backend.resolve(current, command)
        state, action = current['state'], command.action
        kind = action.kind
        if kind in ('dash', 'use_feature', 'cast_spell', 'drop_concentration', 'native_action'):
            raise ValueError('Это действие подключено только для проверенных кампаний Nat20.')
        if kind in ('start_combat', 'attack', 'end_turn', 'end_combat', 'move'):
            # Nat20 is the only D&D calculator. A campaign without it has no
            # engine to resolve a fight, and falling back to the Python combat
            # here would be the silent legacy path the integration forbids.
            # The refusal is an ordinary rejected turn, not an exception out of
            # the transaction: raising here escaped engine_event and reached the
            # player as a server error instead of 'the master refused'.
            raise ValueError('Бой ведёт только движок Nat20; у этой кампании его нет.')
        if kind == 'check':
            if state.get('combat', {}).get('active'):
                raise ValueError('Исследовательская проверка недоступна в бою.')
            place = next(p for p in state['locations'] if p['name'] == state['location'])
            value = place.get('checks', {}).get(action.check_id)
            if value is None:
                # Not printed by the material is not the same as not allowed: the
                # master may call for a check the book never listed. Only an id the
                # engine cannot resolve at all is refused, and then in those words.
                from .rulesets import SKILLS
                from .nat20_content import CHECKS as ENGINE_CHECKS
                kind_of, skill, ability, dc = ENGINE_CHECKS.get(action.check_id, (None, None, None, None))
                if kind_of is None:
                    raise ValueError('Движок не знает такой проверки. Назовите навык, характеристику или спасбросок.')
                value = dict(kind=kind_of, skill=skill, ability=ability, dc=dc or 15)
                source = False
            else:
                source = True
            # The stored check describes what is rolled (skill / ability / saving
            # throw); Step.kind is the shape of the attempt, which is always a
            # check here. Passing both was a duplicate-keyword crash, so a check
            # printed by the material could never be resolved at all.
            fields = copy.deepcopy(value)
            discipline = fields.pop('kind', 'skill')
            if discipline == 'saving_throw':
                fields.setdefault('ability', str(fields.get('ability') or '').lower())
            step = Step(kind='check', text=action.check_id,
                        origin='source' if source else 'master', **fields)
            result = RulesetEngine(active(state)).check(Hero.model_validate(state['hero']), step, self.rng)
            return [dict(type='scripted', check=result, text='Действие удалось.' if result['success'] else 'Попытка не удалась.')]
        if kind in ('short_rest', 'long_rest'):
            result = RulesetEngine(active(state)).rest(
                state, 'short' if kind == 'short_rest' else 'long',
                hit_dice=action.hit_dice if kind == 'short_rest' else 0,
                food_water=action.food_water if kind == 'long_rest' else False, rng=self.rng)
            return [result]
        raise ValueError('Эту команду ведёт только движок Nat20; у этой кампании его нет.')

    def npc_responses(self, current, request_id):
        results = []
        engine = CombatEngine(current['state'], rng=self.rng)
        for _ in range(len(engine.actors)):
            if not engine.data['active'] or engine.current().team == 'hero' or engine.data.get('pending_reaction') or engine.data.get('pending_death'):
                break
            npc = CombatCommand(request_id=request_id, kind='npc_turn')
            results += self.combat.execute_transition(current, npc, current['state'])
            engine = CombatEngine(current['state'], rng=self.rng)
        return results

    @staticmethod
    def effects(before, after, results):
        effects = []
        def add(effect_type, **payload):
            effects.append(dict(type=effect_type, payload=payload))
        for result in results:
            rolls = [dict(kind='check', roll=result['check'], hit=result['check']['success'])] if 'check' in result else []
            rolls += [op for op in result.get('operations', []) if op.get('kind') in ('attack', 'initiative') and op.get('roll')]
            for op in rolls:
                roll = op['roll']
                add('roll', kind=op['kind'], dice=roll['dice'], total=roll['total'], success=op.get('hit'))
        old = {a.id: a for a in before.actors}
        for actor in after.actors:
            prior = old.get(actor.id)
            if prior is None or actor.scope in ('secret', 'gm'):
                continue
            if prior.hp.current != actor.hp.current:
                add('healing' if actor.hp.current > prior.hp.current else 'damage', target=actor.id,
                    amount=abs(actor.hp.current - prior.hp.current), before=prior.hp.current, after=actor.hp.current)
            if before.combat.id == after.combat.id and before.combat.id != 'none' and prior.position.coordinates != actor.position.coordinates:
                add('movement', target=actor.id, before=prior.position.coordinates, after=actor.position.coordinates)
            for key, value in actor.resources.items():
                previous = prior.resources.get(key)
                if actor.id == 'hero' and previous is not None and previous.current != value.current:
                    add('resource', target=actor.id, resource=key, before=previous.current, after=value.current)
            for condition in {c.id for c in prior.conditions} ^ {c.id for c in actor.conditions}:
                add('condition', target=actor.id, condition=condition, active=condition in {c.id for c in actor.conditions})
        if before.combat.active != after.combat.active:
            add('combat_started' if after.combat.active else 'combat_ended', combat_id=after.combat.id,
                participants=[a.id for a in after.actors if a.scope not in ('secret', 'gm')])
        return effects
