"""Adapt persisted conditions/effects to the public Native standalone resolver."""
from dnd5e_engine import ActiveEffect, CheckSpec, resolve_check
from dnd5e_engine.rules.conditions import (
    d20_test_penalty, project_passive_check_modifiers, project_passive_save_modifiers,
)
from dnd5e_engine.types.conditions import ActiveCondition


def resolve_state_check(state, *, kind, skill, ability, dc, advantage, disadvantage, rng):
    sheet=state['sheet']
    effects=[ActiveEffect.model_validate(e) for e in state['active_effects']
             if not e.get('disabled') and e.get('target_id')=='hero']
    statuses=sorted({s for e in effects for s in e.statuses})
    projected=project_passive_check_modifiers(statuses)
    saves=project_passive_save_modifiers(statuses)
    if kind=='saving_throw' and ability and ability.upper() in saves['passive_save_auto_fail']:
        # Native owns automatic-failure semantics; do not draw/fabricate a die.
        from dnd5e_engine.activities.context import ActivityResolutionContext
        from dnd5e_engine.activities.save_primitive import roll_save
        from dnd5e_engine.types.combat import Combatant
        actor=Combatant(entity_id='hero',entity_type='Character',name=state['hero']['name'],
            initiative=0,hp_current=state['hero']['hp_current'])
        ctx=ActivityResolutionContext(rng=rng,caster=actor,targets=[actor],event_emitter=lambda e:None,
            caster_abilities={},passive_save_auto_fail={'hero':saves['passive_save_auto_fail']})
        result=roll_save(ctx,actor,ability,dc or 0)
        return dict(kind=kind,skill='',ability=ability,roll_total=result.total,modifier=result.modifier,
            dc=dc,success=result.succeeded,natural_roll=None,is_proficient=ability in sheet['save_proficiencies'],
            effect_breakdown=['native:automatic_failure'])
    if kind in ('skill','ability'):
        disadvantage=disadvantage or 'all' in projected['passive_check_dis']
    else:
        disadvantage=disadvantage or ability.upper() in saves['passive_save_dis']
        advantage=advantage or ability.upper() in saves['passive_save_adv']
    exhaustion=[ActiveCondition(condition='exhaustion',source_entity_id='implied:exhaustion',scope='session',
        exhaustion_level=state['exhaustion'] or 1)] if state['exhaustion'] or 'exhaustion' in statuses else []
    penalty=d20_test_penalty(exhaustion)
    if penalty:
        effects.append(ActiveEffect(id='host:exhaustion',name='Native Exhaustion projection',origin='state:exhaustion',
            target_id='hero',changes=[dict(key=b,mode='add',value=penalty) for b in ('check.bonus','save.bonus')]))
    # Unsupported Foundry dotted paths must not silently look mechanically active.
    # The generic native check contract accepts check/save buckets and flags.
    relevant=('system.bonuses.abilities.','system.abilities.','system.skills.')
    for effect in effects:
        if any(c.key.startswith(relevant) for c in effect.changes):
            raise ValueError('Standalone check effect key needs a native alias binding: '+effect.id)
    return resolve_check(CheckSpec(kind=kind,skill=skill,ability=ability,dc=dc,
        ability_scores=sheet['ability_scores'],proficient_skills=tuple(sheet['skill_proficiencies']),
        proficient_saves=tuple(sheet['save_proficiencies']),proficiency_bonus=sheet['proficiency_bonus'],
        expertise_skills=tuple(sheet['skill_expertise']),advantage=advantage,disadvantage=disadvantage,
        jack_of_all_trades='jack-of-all-trades' in sheet['features'],reliable_talent='reliable-talent' in sheet['features'],
        active_effects=tuple(effects),rng=rng))
