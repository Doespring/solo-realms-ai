"""Provisional memory-side boundary, v1; not a claimed upstream engine API.

Only trusted Python engine code may deliver these values. No D&D resolution here.
Replace/import these DTOs when the shared engine contract becomes available.
"""
from typing import Literal
from pydantic import Field, model_validator, model_serializer
from .contracts import Strict

class Access(Strict):
    scope: Literal['public', 'private', 'secret', 'gm']
    known_by: list[str] = Field(min_length=1, max_length=100)


class HitPoints(Access):
    current: int = Field(ge=0)
    maximum: int = Field(ge=0)


class ResourceSnapshot(Access):
    current: int


class ConditionSnapshot(Access):
    id: str = Field(min_length=1, max_length=120)


class PositionSnapshot(Access):
    coordinates: tuple[int, int, int]


class ActorSnapshot(Access):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal['hero', 'npc']
    name: str = Field(min_length=1, max_length=120)
    hp: HitPoints
    resources: dict[str, ResourceSnapshot]
    conditions: list[ConditionSnapshot]
    position: PositionSnapshot


class CombatSnapshot(Access):
    id: str = Field(min_length=1, max_length=120)
    active: bool
    round: int = Field(ge=0)
    turn: str | None


class EffectDurationLimit(Strict):
    maximum_rounds: int | None = Field(default=None, ge=0)
    maximum_seconds: int | None = Field(default=None, ge=0)


class EffectSnapshot(Access):
    id: str = Field(min_length=1, max_length=120)
    origin: str = Field(min_length=1, max_length=300)
    target: str
    caster: str
    name: str = Field(min_length=1, max_length=120)
    concentration: bool
    duration_limit: EffectDurationLimit


class GameSnapshot(Strict):
    contract_version: Literal['memory-v1'] = 'memory-v1'
    campaign_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1, max_length=120)
    branch_id: str = Field(min_length=1, max_length=120)
    sequence: int = Field(ge=1)
    location: str = Field(min_length=1, max_length=120)
    actors: list[ActorSnapshot] = Field(min_length=1, max_length=100)
    combat: CombatSnapshot
    available_checks: list[str] = Field(default_factory=list, max_length=100)
    # Weapon ids for attack_weapon. Optional so older stored snapshots stay
    # readable; a new snapshot always fills it from the D&D 2024 dataset.
    available_weapons: list[str] | None = Field(default=None, max_length=200)
    # Encounter ids the engine registered for this scene. Without them the
    # model cannot name a fight, so 'start_combat' could never be chosen.
    available_encounters: list[str] | None = Field(default=None, max_length=64)
    effects: list[EffectSnapshot] | None = Field(default=None, max_length=100)

    @model_serializer(mode='wrap')
    def keep_historical_snapshot(self, handler):
        result = handler(self)
        if self.effects is None:
            result.pop('effects', None)
        return result

    @model_validator(mode='after')
    def identities(self):
        ids = {a.id for a in self.actors}
        if len(ids) != len(self.actors):
            raise ValueError('Duplicate actor ID')
        if sum(a.kind == 'hero' for a in self.actors) != 1 or not any(a.id == 'hero' and a.kind == 'hero' for a in self.actors):
            raise ValueError('Exactly one hero with stable ID hero is required')
        if self.combat.turn is not None and self.combat.turn not in ids:
            raise ValueError('Unknown combat turn actor')
        access = [self.combat]
        for actor in self.actors:
            access.extend([actor, actor.hp, actor.position, *actor.resources.values(), *actor.conditions])
        for effect in self.effects or []:
            access.append(effect)
            if {effect.target, effect.caster} - ids:
                raise ValueError('Unknown effect target/caster')
        if len({(e.target, e.id, e.origin) for e in self.effects or []}) != len(self.effects or []):
            raise ValueError('Duplicate effect identity')
        if any(set(a.known_by) - ids for a in access):
            raise ValueError('Unknown snapshot knowledge recipient')
        return self


class Roll(Strict):
    kind: Literal['check', 'save', 'attack', 'initiative']
    dice: list[int] = Field(min_length=1, max_length=100)
    total: int
    success: bool | None = None
    # A check the host names carries its own difficulty and identity, so the
    # journal states the fact instead of leaving it in prose. Older roll rows
    # keep both unset and stay valid.
    dc: int | None = None
    check_id: str | None = None
    # r226: the flat bonus the engine applied before the total. Copied out of
    # the result, never recomputed on the client: the plaque prints
    # «нужно · выпало · модификатор = итог», and the arithmetic must add up.
    modifier: int | None = None


class HPChange(Strict):
    target: str
    amount: int = Field(ge=0)
    before: int = Field(ge=0)
    after: int = Field(ge=0)


class ResourceChange(Strict):
    target: str
    resource: str = Field(min_length=1, max_length=120)
    before: int
    after: int


class ConditionChange(Strict):
    target: str
    condition: str = Field(min_length=1, max_length=120)
    active: bool


class EffectChange(Strict):
    target: str
    effect_id: str
    origin: str
    active: bool
    concentration: bool


class Movement(Strict):
    target: str
    before: tuple[int, int, int]
    after: tuple[int, int, int]


class CombatBoundary(Strict):
    combat_id: str
    participants: list[str] = Field(min_length=1, max_length=100)


PAYLOADS = {'roll': Roll, 'damage': HPChange, 'healing': HPChange,
            'resource': ResourceChange, 'condition': ConditionChange,
            'movement': Movement, 'combat_started': CombatBoundary, 'combat_ended': CombatBoundary}


class ResolvedEffect(Strict):
    type: Literal['roll', 'damage', 'healing', 'resource', 'condition', 'movement', 'combat_started', 'combat_ended', 'effect']
    payload: dict

    @model_validator(mode='after')
    def typed_payload(self):
        self.payload = PAYLOADS[self.type].model_validate(self.payload).model_dump(mode='json')
        return self


class CommandResolution(Strict):
    command: Literal['check', 'start_combat', 'move', 'attack', 'dash', 'end_turn', 'short_rest', 'long_rest', 'end_combat', 'use_feature', 'cast_spell', 'drop_concentration', 'native_action', 'scene_action', 'order']
    outcome: str = Field(min_length=1, max_length=3000)
    effects: list[ResolvedEffect]


PAYLOADS['effect'] = EffectChange
PAYLOADS['command_resolved'] = CommandResolution


class GameEvent(Access):
    contract_version: Literal['memory-v1'] = 'memory-v1'
    id: str = Field(min_length=1, max_length=120)
    campaign_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1, max_length=120)
    sequence: int = Field(ge=1)
    parent_event_id: str | None
    type: Literal['roll', 'damage', 'healing', 'resource', 'condition', 'movement', 'combat_started', 'combat_ended', 'command_resolved', 'effect']
    payload: dict
    subjects: list[str] = Field(min_length=1, max_length=100)
    source_ref: str = Field(min_length=1, max_length=400)
    game_minutes: int = Field(ge=0)

    @model_validator(mode='after')
    def structured_payload(self):
        self.payload = PAYLOADS[self.type].model_validate(self.payload).model_dump(mode='json')
        return self


def effects(event):
    """Typed mechanics only. Command outcome text is never memory evidence."""
    return event.payload['effects'] if event.type == 'command_resolved' else [dict(type=event.type, payload=event.payload)]


def effect_key(kind, payload):
    if kind == 'effect':
        return ('effect', payload['target'], payload['effect_id'], payload['origin'])
    if kind in ('damage', 'healing'):
        return ('hp', payload['target'])
    if kind == 'resource':
        return ('resource', payload['target'], payload['resource'])
    if kind == 'condition':
        return ('condition', payload['target'], payload['condition'])
    if kind == 'movement':
        return ('position', payload['target'])
    if kind in ('combat_started', 'combat_ended'):
        return ('combat',)
    return None


def pair(event, snapshot):
    # Revalidate model instances too: a caller may have mutated a DTO after construction.
    event = GameEvent.model_validate(event.model_dump() if isinstance(event, GameEvent) else event)
    snapshot = GameSnapshot.model_validate(snapshot.model_dump() if isinstance(snapshot, GameSnapshot) else snapshot)
    if (event.campaign_id, event.id, event.branch_id, event.sequence) != (snapshot.campaign_id, snapshot.event_id, snapshot.branch_id, snapshot.sequence):
        raise ValueError('Event/snapshot identity mismatch')
    actors = {a.id: a for a in snapshot.actors}
    if (set(event.subjects) | set(event.known_by)) - actors.keys():
        raise ValueError('Unknown event subject or knowledge recipient')
    final = {}
    for effect in effects(event):
        kind, p = effect['type'], effect['payload']
        if 'target' in p:
            if p['target'] not in actors:
                raise ValueError('Unknown effect target')
            if event.type != 'command_resolved' and p['target'] not in event.subjects:
                raise ValueError('Target must be an event subject')
        if kind in ('combat_started', 'combat_ended'):
            if set(p['participants']) - actors.keys():
                raise ValueError('Unknown combat participant')
            if event.type != 'command_resolved' and set(p['participants']) - set(event.subjects):
                raise ValueError('Combat participants must be event subjects')
        key = effect_key(kind, p)
        if key:
            prior = final.get(key)
            if prior and 'before' in p and prior['payload']['after'] != p['before']:
                raise ValueError('Discontinuous effect values')
            final[key] = effect
    for effect in final.values():
        kind, p = effect['type'], effect['payload']
        actor = actors.get(p.get('target'))
        if kind in ('damage', 'healing') and actor.hp.current != p['after']:
            raise ValueError('HP result does not match snapshot')
        if kind == 'resource' and (p['resource'] not in actor.resources or actor.resources[p['resource']].current != p['after']):
            raise ValueError('Resource result does not match snapshot')
        if kind == 'condition' and (p['condition'] in {c.id for c in actor.conditions}) != p['active']:
            raise ValueError('Condition result does not match snapshot')
        if kind == 'effect':
            if snapshot.effects is None:
                raise ValueError('Effect projection is required')
            match = next((e for e in snapshot.effects if (e.target, e.id, e.origin) == (p['target'], p['effect_id'], p['origin'])), None)
            if bool(match) != p['active'] or (match and match.concentration != p['concentration']):
                raise ValueError('Effect result does not match snapshot')
        if kind == 'movement' and list(actor.position.coordinates) != p['after']:
            raise ValueError('Movement result does not match snapshot')
        if kind in ('combat_started', 'combat_ended'):
            if snapshot.combat.id != p['combat_id']:
                raise ValueError('Combat identity mismatch')
            if snapshot.combat.active != (kind == 'combat_started'):
                raise ValueError('Combat boundary does not match snapshot')
    return event, snapshot
