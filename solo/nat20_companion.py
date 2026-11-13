"""Companion turns in a fight (r215): the master decides, Nat20 counts.

A companion is a full participant (owner's decision 03.11.2026, SPEC-COMPANIONS
items 1-3): its own initiative, its own Action and Bonus Action, its own
resources. The player does not drive it; the master does — and the master's
decision is a *host policy*, not a second rules engine: it names an intent and
lets Nat20 accept or refuse it, exactly like ``_run_uncommanded_summon_turn``
does for a summon the caster did not command.

So this module computes no rule. What it does:

* picks a target and a carried weapon out of the engine's own weapon data
  (``lib_loader`` range/reach), not out of a host table of "good weapons";
* orders the steps of one turn — Bonus Action, movement, Action — and hands
  each one to ``submit_player_intent`` for the companion's own ``entity_id``;
* never swallows a refusal: an ``IntentRejectedError`` and an engine
  ``*_failed`` event both become a journal line with the engine's reason;
* keeps the plan inside what the sheet allows: Rage is not re-activated while
  its effect is on (the engine accepts a repeat as a free no-op — measured
  r215, counter stayed at ``spent: 1``), and a companion in heavy armour does
  not rage (the engine does not check the armour).

A refusal that produced no event and no exception at all — a feature the
corpus cannot execute, for instance — is reported as exactly that, not as a
success: silence is a defect, not an answer.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum

RAGE_FEATURE = 'rage'
RAGE_EFFECT_ID = 'effect:rage'
CELL_FEET = 5
# One companion turn is a handful of intents; a bound keeps a hostile reading of
# the view from turning into an endless fight loop inside one command.
MAX_STEPS = 8
# How many companion turns one command may play (a party of eight in a long
# round must still fit into one journal line set).
MAX_TURNS = 16


def _plain(value):
    """The engine's own dataclasses/pydantic models as JSON-shaped data.

    ``dataclasses.asdict`` is not enough here: ``Combatant`` inside the view is a
    pydantic model, and ``asdict`` would leave those objects in place — the host
    then reads a ``Combatant`` with ``dict.get`` and dies on its turn. Recursing
    by hand keeps one shape all the way down.
    """
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def row_of(view, entity_id):
    """The initiative row of ``entity_id`` (plain dict), or ``None``."""
    return next((row for row in (view.get('initiative') or ())
                 if row.get('entity_id') == entity_id), None)


def features_of(member):
    """Feature slugs the engine derived for this companion's own sheet."""
    sheet = member.get('sheet') or {}
    return {str(item) for item in sheet.get('features') or () if item}


def equipment_of(member):
    return [str(slug) for slug in (member.get('build') or {}).get('equipment') or () if slug]


def _cell(value):
    try:
        x, y = (int(part) for part in str(value).split(','))
        return x, y
    except (TypeError, ValueError):
        return None


def distance_feet(view, first, second):
    """Grid distance between two entities in feet, or ``None`` when untracked.

    The scene is one 5-ft square per cell (``GridScene``), so this is a count of
    cells, not a rule: reach and range are still the engine's own gate.
    """
    zones = view.get('actor_zone') or {}
    one, two = _cell(zones.get(first)), _cell(zones.get(second))
    if one is None or two is None:
        return None
    return max(abs(one[0] - two[0]), abs(one[1] - two[1])) * CELL_FEET


def living_enemies(view):
    dead = set(view.get('dead_ids') or ())
    hp = view.get('tracked_hp') or {}
    rows = []
    for entity_id in sorted(view.get('encounter_ids') or ()):
        row = row_of(view, entity_id)
        if row is None or entity_id in dead or row.get('is_alive') is False:
            continue
        if (hp.get(entity_id, row.get('hp_current') or 0) or 0) <= 0:
            continue
        rows.append(row)
    return rows


def living_allies(view, actor_id):
    dead = set(view.get('dead_ids') or ())
    hp = view.get('tracked_hp') or {}
    rows = []
    for entity_id in sorted(view.get('party_ids') or ()):
        if entity_id == actor_id:
            continue
        row = row_of(view, entity_id)
        if row is None or entity_id in dead or row.get('is_alive') is False:
            continue
        if (hp.get(entity_id, row.get('hp_current') or 0) or 0) <= 0:
            continue
        rows.append(row)
    return rows


def carried_weapons(member, loader=None):
    """This companion's weapons split by how they reach: ``melee``/``thrown``/``ranged``.

    Classification comes from the engine's own weapon asset (``range.kind`` and
    its distance), so a new weapon in the corpus needs no host edit. An unknown
    slug is skipped instead of guessed.
    """
    if loader is None:
        from dnd5e_engine.lib_loader import get_lib_loader

        loader = get_lib_loader()
    found = {'melee': [], 'thrown': [], 'ranged': []}
    for slug in equipment_of(member):
        weapon = loader.get_weapon(slug)
        if weapon is None:
            continue
        data = weapon.model_dump(mode='json')
        reach = data.get('range') or {}
        kind = reach.get('kind')
        if kind == 'ranged':
            found['ranged'].append(slug)
        elif reach.get('value'):
            found['thrown'].append(slug)
        else:
            found['melee'].append(slug)
    return found


def weapon_for(member, feet, loader=None):
    """The first carried weapon that can reach ``feet``, in the sheet's own order."""
    weapons = carried_weapons(member, loader)
    if feet is None or feet <= CELL_FEET:
        for group in ('melee', 'thrown', 'ranged'):
            if weapons[group]:
                return weapons[group][0]
        return None
    for group in ('thrown', 'ranged'):
        if weapons[group]:
            return weapons[group][0]
    # Nothing but a melee weapon in the pack: the engine refuses the swing with
    # its own ``out_of_range`` and that refusal is what the journal keeps.
    return weapons['melee'][0] if weapons['melee'] else None


def _free_cells(view, grid, target_id, actor_id):
    """Cells adjacent to ``target_id`` that a creature may stand in, nearest first."""
    zones = view.get('actor_zone') or {}
    target = _cell(zones.get(target_id))
    actor = _cell(zones.get(actor_id))
    if target is None or actor is None:
        return []
    occupied = {str(cell) for entity, cell in zones.items()
                if entity != actor_id and cell}
    blocked = {str(cell) for cell in (grid.get('blocked_cells') or ())}
    width = int(grid.get('width') or 0)
    height = int(grid.get('height') or 0)
    cells = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if not dx and not dy:
                continue
            x, y = target[0] + dx, target[1] + dy
            if x < 0 or y < 0 or (width and x >= width) or (height and y >= height):
                continue
            cell = str(x) + ',' + str(y)
            if cell in occupied or cell in blocked or cell == zones.get(actor_id):
                continue
            cells.append((max(abs(x - actor[0]), abs(y - actor[1])), cell))
    cells.sort()
    return [cell for _, cell in cells]


def plan_turn(view, member, actor_id, *, grid=None, effects=(), loader=None):
    """The master's plan for this companion's turn, in order, as intent steps.

    A step is ``{'stage': 'bonus'|'move'|'action', 'intent': {...}, 'label': ...,
    'target': <name or None>}``; ``intent`` holds ``PlayerIntent`` keyword
    arguments only, so this function stays a pure decision over the view.
    """
    row = row_of(view, actor_id)
    if row is None:
        return []
    name = row.get('name') or member.get('name') or actor_id
    steps = []
    if row.get('action_available') is False:
        return [dict(stage='action', intent=dict(intent_type='pass'), label='пропуск хода',
                     target=None, actor=name)]
    targets = living_enemies(view)
    # Bonus Action first: Rage is the barbarian's own economy, and the engine
    # applies it before the swing's damage roll (measured r215).
    features = features_of(member)
    if (RAGE_FEATURE in features and row.get('bonus_action_available') is not False
            and also_raging(view, actor_id, effects) is False
            and str(row.get('worn_armor') or '') != 'heavy'):
        steps.append(dict(stage='bonus', intent=dict(intent_type='use_feature', feature_id=RAGE_FEATURE),
                          label='Ярость', target=None, actor=name))
    # Second Wind is the fighter's own capped resource. The master decides WHEN
    # to spend it (below half hit points); how many uses are left is the
    # engine's bookkeeping, and an exhausted feature is refused with its own
    # ``no_uses_remaining`` instead of being invented here.
    hp = view.get('tracked_hp') or {}
    current_hp = hp.get(actor_id, row.get('hp_current') or 0) or 0
    maximum = row.get('hp_max') or member.get('member', {}).get('hp_max') or 0
    if ('second-wind' in features and row.get('bonus_action_available') is not False
            and maximum and current_hp * 2 <= maximum):
        steps.append(dict(stage='bonus', intent=dict(intent_type='use_feature', feature_id='second-wind'),
                          label='Второе дыхание', target=None, actor=name))
    if not targets:
        steps.append(dict(stage='action', intent=dict(intent_type='dodge'), label='уклонение',
                          target=None, actor=name))
        return steps
    grid = grid or {}
    nearest, feet = None, None
    for target in targets:
        found = distance_feet(view, actor_id, target['entity_id'])
        if feet is None or (found is not None and found < feet):
            nearest, feet = target, found
    target_id = nearest['entity_id']
    target_name = nearest.get('name') or target_id
    if feet is not None and feet > CELL_FEET:
        for cell in _free_cells(view, grid, target_id, actor_id)[:3]:
            steps.append(dict(stage='move', intent=dict(intent_type='move', target_zone_id=cell),
                              label='шаг к цели', target=target_name, actor=name))
    weapon = weapon_for(member, feet, loader)
    if weapon:
        steps.append(dict(stage='action',
                          intent=dict(intent_type='attack', target_id=target_id, weapon_id=weapon),
                          label='атака', target=target_name, actor=name))
    # No weapon that reaches: give advantage to an ally already in the enemy's
    # face, or keep out of the way — both are actions the engine resolves.
    helper = _help_target(view, actor_id, targets)
    if helper is not None:
        steps.append(dict(stage='action', intent=dict(intent_type='help', target_id=helper['entity_id']),
                          label='помощь', target=helper.get('name') or helper['entity_id'], actor=name))
    steps.append(dict(stage='action', intent=dict(intent_type='dodge'), label='уклонение',
                      target=None, actor=name))
    return steps


def _help_target(view, actor_id, targets):
    """A living ally within 5 ft of a living enemy: the Help action's own target."""
    allies = living_allies(view, actor_id)
    if not allies:
        return None
    for ally in allies:
        for target in targets:
            feet = distance_feet(view, ally['entity_id'], target['entity_id'])
            if feet is not None and feet <= CELL_FEET:
                return ally
    return None


def also_raging(view, actor_id, effects):
    """True when the actor already carries the Rage effect (engine's own identity)."""
    for effect in effects or ():
        if effect.get('id') == RAGE_EFFECT_ID and effect.get('target_id') == actor_id \
                and not effect.get('disabled'):
            return True
    return False


def _action_line(step, events):
    """One line about a resolved step, built from the engine's events only."""
    label = step.get('label') or step.get('intent', {}).get('intent_type')
    target = step.get('target')
    kinds = [event.get('type') for event in events]
    mine = [event for event in events
            if event.get('attacker_id') in (None, step.get('actor_id'))
            or event.get('target_id') == step.get('intent', {}).get('target_id')]
    if step.get('intent', {}).get('intent_type') == 'attack':
        rolled = next((event for event in mine if event.get('type') == 'attack_rolled'), None)
        if rolled is None:
            return label + ': броска нет', 'refused'
        if rolled.get('is_crit'):
            outcome = 'критическое попадание'
        elif rolled.get('is_hit'):
            outcome = 'попадание'
        else:
            outcome = 'промах'
        damage = sum(event.get('amount') or 0 for event in mine
                     if event.get('type') == 'damage_applied')
        line = label + ': ' + outcome
        if rolled.get('natural') is not None:
            line += ' (бросок ' + str(rolled.get('natural')) + ' + ' + \
                    str(rolled.get('modifier')) + ' = ' + str(rolled.get('roll_total')) + ')'
        if damage:
            line += ', урон ' + str(damage)
        if target:
            line += ' по цели ' + str(target)
        return line, ('hit' if rolled.get('is_hit') else 'miss')
    if 'actor_moved' in kinds:
        return label + ((' к цели ' + str(target)) if target else ''), 'moved'
    if 'effect_applied' in kinds:
        return label + ': применено движком', 'applied'
    if step.get('intent', {}).get('intent_type') in ('dodge', 'help', 'pass'):
        if 'intent_submitted' in kinds or step.get('submitted'):
            return label + ((': ' + str(target)) if target else ''), 'done'
        return label + ': движок не ответил событием', 'silent'
    if any(str(kind or '').endswith('_failed') for kind in kinds):
        return label + ': отбито движком', 'refused'
    return label + ': принято', 'done'


async def play_turn(handle, actor_id, member, *, grid=None, effects=(), loader=None):
    """Play one companion's turn through the engine; return its journal record.

    The record names the companion, every step attempted, the engine's numbers
    and every refusal with its own reason. Nothing is invented: a step with no
    event and no exception is kept as ``silent`` so the next reader sees the
    hole instead of a clean line.
    """
    from dnd5e_engine import (IntentRejectedError, PlayerIntent, drain_pending_events,
                              get_live, submit_player_intent)

    view = _plain(get_live(handle))
    plan = plan_turn(view, member, actor_id, grid=grid, effects=effects, loader=loader)
    record = dict(id=actor_id, name=(row_of(view, actor_id) or {}).get('name') or member.get('name') or actor_id,
                  actions=[], refused=[], silent=[], events=[])
    drained = record['events']
    moved = False
    for step in plan:
        if len(record['actions']) >= MAX_STEPS:
            break
        current = _plain(get_live(handle))
        if current.get('ended'):
            break
        row = row_of(current, actor_id)
        if row is None:
            break
        turn = current.get('current_turn_index')
        order = current.get('initiative') or []
        if not (0 <= int(turn or 0) < len(order)) or order[int(turn)]['entity_id'] != actor_id:
            break
        if step.get('stage') == 'move' and moved:
            # The first affordable step was taken; the rest of the move steps
            # were alternatives, not a route to walk twice.
            continue
        intent_args = dict(step['intent'])
        if intent_args.get('intent_type') == 'attack':
            # The weapon is chosen at the moment of the swing: after the move
            # the enemy is in reach, and a thrown weapon chosen from the old
            # distance would be the wrong one to hit with.
            chosen = weapon_for(member, distance_feet(current, actor_id, intent_args.get('target_id')),
                                loader)
            if not chosen:
                continue
            intent_args['weapon_id'] = chosen
        intent = PlayerIntent(**intent_args)
        # Events already queued belong to the hero's command that led here; they
        # are kept (the journal and the replay digest need them) but not read as
        # this step's own outcome.
        drained.extend(_plain(event) for event in drain_pending_events(handle))
        try:
            await submit_player_intent(handle, actor_id, intent)
        except IntentRejectedError as error:
            record['refused'].append(dict(kind=step.get('label'), reason=str(error)))
            continue
        events = [_plain(event) for event in drain_pending_events(handle)]
        drained.extend(events)
        line, outcome = _action_line({**step, 'actor_id': actor_id, 'submitted': True}, events)
        if outcome == 'refused':
            failed = next((event for event in events
                           if str(event.get('type') or '').endswith('_failed')), {})
            reason = str(failed.get('reason') or 'движок отказал без причины')
            if reason == 'no_uses_remaining':
                # The engine's own bookkeeping is the number the player needs to
                # see: "2 из 2" is a fact from the counter, not a host guess at
                # the cap.
                counters = (current.get('custom_counters_by_entity') or {}).get(actor_id) or {}
                spent = ', '.join(str(key) + '=' + str((value or {}).get('spent'))
                                  for key, value in sorted(counters.items()))
                reason += ' (счётчики: ' + (spent or 'пусто') + ')'
            record['refused'].append(dict(kind=step.get('label'), reason=reason))
            continue
        if outcome == 'silent':
            record['silent'].append(dict(kind=step.get('label'),
                                         reason='на интент не пришло ни события, ни отказа'))
        record['actions'].append(dict(kind=step.get('intent', {}).get('intent_type'),
                                      label=step.get('label'), target=step.get('target'),
                                      outcome=outcome, line=line))
        if step.get('stage') == 'move':
            moved = True
        elif step.get('stage') == 'action' and step['intent'].get('intent_type') != 'pass':
            # The Action is spent; the turn itself does not end (SRD 5.2), so the
            # master hands it over explicitly. Without this the fight stalls on
            # the companion and the hero never gets the next round.
            current = _plain(get_live(handle))
            order = current.get('initiative') or []
            turn = int(current.get('current_turn_index') or 0)
            if not current.get('ended') and order and order[turn]['entity_id'] == actor_id:
                try:
                    await submit_player_intent(handle, actor_id, PlayerIntent(intent_type='pass'))
                    drained.extend(_plain(event) for event in drain_pending_events(handle))
                except IntentRejectedError as error:
                    record['refused'].append(dict(kind='передача хода', reason=str(error)))
            return record
    record['moved'] = moved
    # Only blank steps reached this point: the companion must still hand over.
    current = _plain(get_live(handle))
    order = current.get('initiative') or []
    turn = int(current.get('current_turn_index') or 0)
    if not current.get('ended') and order and order[turn]['entity_id'] == actor_id:
        try:
            await submit_player_intent(handle, actor_id, PlayerIntent(intent_type='pass'))
            drained.extend(_plain(event) for event in drain_pending_events(handle))
            record['actions'].append(dict(kind='pass', label='пропуск хода', target=None,
                                          outcome='done', line='пропуск хода'))
        except IntentRejectedError as error:
            record['refused'].append(dict(kind='передача хода', reason=str(error)))
    return record


def sentence(record):
    """The journal line of one companion turn: who acted, what the engine did."""
    name = str(record.get('name') or record.get('id') or 'спутник')
    parts = [str(action.get('line')) for action in record.get('actions') or ()
             if action.get('line') and action.get('kind') != 'pass']
    for refusal in record.get('refused') or ():
        parts.append(str(refusal.get('kind')) + ' отбито: ' + str(refusal.get('reason')))
    for silent in record.get('silent') or ():
        parts.append(str(silent.get('kind')) + ': движок промолчал (' + str(silent.get('reason')) + ')')
    if not parts:
        parts.append('ход передан без действия')
    return name + ' (спутник): ' + '; '.join(parts) + '.'


def journal_text(records):
    """All companion turns of one command as one journal sentence block."""
    lines = [sentence(record) for record in records or ()]
    return ' '.join(lines)
