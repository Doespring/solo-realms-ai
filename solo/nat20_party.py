"""Party membership of a Nat20 campaign (r214).

The campaign keeps one list of members: the main hero (rebuilt from the fields
that own the hero, see ``DurableState.hero_leads_the_party``) and every
companion, each with its own build, its own sheet derived by Nat20, its own hit
dice and its own resources. A party of one is a party.

Nothing here computes a rule. A companion's sheet is ``derive_sheet``, its
combat projection is ``build_party_member``, and its levels are ``advance``.
The module only states the host policies: a companion joins at the hero's level,
and an advancement the rules make the player decide is not spent by the host -
it is refused with the question that still has to be answered.
"""
from __future__ import annotations

MAX_COMPANIONS = 7


def next_id(party=()):
    """The first free ``companion-N`` identifier."""
    taken = {str(item.get('id')) for item in party or ()}
    index = 1
    while 'companion-' + str(index) in taken:
        index += 1
    return 'companion-' + str(index)


def _feature_counters(build):
    """A fresh member starts with its class resources untouched.

    Same policy as ``nat20_worker.initialize``: the engine stores *spent*
    uses in ``custom_counters``, so a new member carries an explicit zero for
    the resources its own class grants at this level. Anything the engine does
    not price here stays absent instead of being invented.
    """
    if build.class_slug == 'fighter':
        return {'feature_use:second-wind': {'spent': 0}}
    return {}


def advance_to(build, level, loader, choices=None):
    """This build at ``level``, advanced one engine step at a time.

    ``choices`` carries the player's answers for the questions this level asks
    (``selected_choices`` / ``subclass_slug``). A question the player has not
    answered is not filled in by the host: the refused level names the member
    and what it still owes, and the whole transaction stays uncommitted.
    """
    from dnd5e_engine.rules.advancement import advance, validate_choices

    choices = dict(choices or {})
    current = build
    target = int(level)
    if target < current.level:
        raise ValueError('Уровень спутника не понижается вместе с героем.')
    while current.level < target:
        current = advance(current, class_slug=choices.get('class_slug') or current.class_slug, loader=loader)
    payload = current.model_dump(mode='json')
    if choices.get('selected_choices'):
        payload['selected_choices'] = list(choices['selected_choices'])
    if choices.get('subclass_slug'):
        payload['subclass_slug'] = choices['subclass_slug']
    from dnd5e_engine import CharacterBuildSpec

    result = CharacterBuildSpec(**payload)
    try:
        validate_choices(result, loader=loader)
    except ValueError as error:
        if 'advancement choices still owed' in str(error):
            raise ValueError('Спутник ' + str(result.class_slug) + ' до уровня ' + str(target)
                             + ' ещё не готов: ' + str(error).strip().rstrip('.')
                             + '. Выберите это за спутника и повторите повышение.') from None
        raise
    return result


def record(build, member_id, name, loader, *, zone_id='1,1', spells=(), role='companion',
           traits=(), relation=0):
    """One member record: build, engine sheet, engine combat projection, pool.

    r216: the record also keeps what the companion thinks of the hero - the
    relation number and the traits that decide his answers. Both are plain data
    of the campaign state; only an event moves them (``solo/nat20_orders.py``).
    """
    from dnd5e_engine import CharacterBuildSpec, CombatInstance, build_party_member, derive_sheet
    from dnd5e_engine.rules.character import hit_die_size
    from .nat20_orders import clamp, normalise_traits

    build = build if isinstance(build, CharacterBuildSpec) else CharacterBuildSpec.model_validate(build)
    sheet = derive_sheet(build, loader=loader)
    member = build_party_member(build, CombatInstance(entity_id=member_id, name=name, initiative=None,
        zone_id=zone_id, hp_current=None, spells_known=tuple(spells or ())), loader=loader)
    member_dump = member.model_dump(mode='json', exclude_unset=True)
    member_dump.setdefault('custom_counters', _feature_counters(build))
    hit_die = hit_die_size(str(loader.get_class(build.class_slug).hit_die))
    return dict(id=member_id, role=role, name=name, build=build.model_dump(mode='json'),
                sheet=sheet.model_dump(mode='json'), member=member_dump,
                hit_dice={'hit_die_size': int(hit_die), 'dice_remaining': int(build.level),
                          'dice_total': int(build.level)},
                exhaustion=0, relation=clamp(relation), traits=normalise_traits(traits))


def add_companion(state, spec, loader, fingerprint):
    """Join one companion to ``state``, at the hero's level. Worker-side.

    ``state`` is already validated and its release checked. Adding a member
    during a fight is refused: turn order does not grow mid-encounter, and the
    party screen is not a combat action.
    """
    from dnd5e_engine import CharacterBuildSpec

    if state.get('combat'):
        raise ValueError('Спутник не присоединяется посреди боя: закончите бой.')
    party = list(state.get('party') or [])
    companions = [item for item in party if item.get('role') == 'companion']
    if len(companions) >= MAX_COMPANIONS:
        raise ValueError('В партии уже ' + str(MAX_COMPANIONS) + ' спутников: больше не помещается.')
    name = str(spec.get('name') or '').strip()
    if not 1 <= len(name) <= 80:
        raise ValueError('Имя спутника: от 1 до 80 знаков.')
    member_id = str(spec.get('id') or '').strip() or next_id(party)
    if any(item.get('id') == member_id for item in party):
        raise ValueError('Член партии с таким id уже есть: ' + member_id)
    build = CharacterBuildSpec.model_validate(spec['build'])
    hero_level = int((state.get('definition') or {}).get('build', {}).get('level') or 1)
    if build.level != hero_level:
        # BG3 rule, decided by the owner: companions always stand at the hero's
        # level. The host does not invent the picks this step asks for; an
        # unanswered question is refused with the question itself.
        build = advance_to(build, hero_level, loader, spec.get('choices'))
    member = record(build, member_id, name, loader,
                    zone_id=spec.get('zone_id') or '1,1', spells=spec.get('prepared_spells') or (),
                    traits=spec.get('traits') or (), relation=spec.get('relation') or 0)
    state['party'] = [*party, member]
    from .nat20_contracts import DurableState

    return {'state': DurableState.model_validate(state).model_dump(mode='json'),
            'member': member, 'events': []}


def projection(runtime):
    """What the campaign screen shows about the party. Read from derived data."""
    from dnd5e_engine import PartyMemberSpec

    rows = []
    combat = (runtime.get('combat') or {})
    view = combat.get('view') or {}
    for item in runtime.get('party') or []:
        build = item.get('build') or {}
        sheet = item.get('sheet') or {}
        member = item.get('member') or {}
        identifier = str(item.get('id'))
        hp_current = (view.get('tracked_hp') or {}).get(identifier, member.get('hp_current'))
        rows.append(dict(id=identifier, role=item.get('role') or 'companion',
            name=item.get('name') or member.get('name') or identifier,
            level=build.get('level'), classes=dict(build.get('classes') or {}),
            species=build.get('species_slug'), background=build.get('background_slug'),
            subclass=build.get('subclass_slug'),
            hp_current=hp_current, hp_max=member.get('hp_max'), ac=member.get('ac'),
            abilities={key: member.get(key) for key in ('strength', 'dexterity', 'constitution',
                                                       'intelligence', 'wisdom', 'charisma')},
            hit_dice=item.get('hit_dice'), exhaustion=item.get('exhaustion', 0),
            # r218: where this companion came from, when he was a person of the
            # material and not built in the editor. Empty for a hero or a
            # companion the player created by hand.
            origin=dict(item.get('origin') or {}),
            # r216: what this companion thinks of the hero, as data the screen
            # reads - not a number the narrator invents in prose.
            **({'relation': None, 'traits': []} if (item.get('role') or 'companion') == 'hero'
               else _relation_of(item)),
            resources={key: (value.get('spent') if isinstance(value, dict) else value)
                       for key, value in (member.get('custom_counters') or {}).items()},
            spell_slots=dict(member.get('spell_slots') or {}),
            conditions=sorted((view.get('active_conditions') or {}).get(identifier, [])),
            dead=identifier in (view.get('dead_ids') or [])))
    return rows


def _relation_of(item):
    """One companion's relation block for the screen (r216)."""
    from .nat20_orders import projection

    return projection(item)

