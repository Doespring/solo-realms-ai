"""r121: заклинание с настоящим числом целей адресуется через ``target_ids``.

Дефект (НАЙДЕНО r119b): ``_reject_untargeted_cast`` отказывал касту, у
которого нет ``target_id``, если есть ``target_ids`` — то есть гасил ровно тот
случай, который заклинание с ``target.affects.count`` (Magic Missile,
``2 + @item.level``) обязано поддерживать. ``magic-missile`` через
``target_ids`` не кастовался НИКОГДА: ``CastFailed / target_invalid`` при любом
наборе целей, слот не тратился, хиты не двигались.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine import PlayerIntent, orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

MISSILE_ACTIVITY = 'dnd5eactivity000'


def _wizard(spells: tuple[str, ...]) -> object:
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='wizard', background_slug='soldier', level=5,
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=12, charisma=10,
        ),
    )
    inst = CombatInstance(
        entity_id='pc1', name='W', zone_id='0,0', initiative=20,
        hp_max=30, hp_current=30, spells_known=spells,
    )
    return build_party_member(spec, inst, loader=BundledAssetLoader())


def _fight(tag: str):
    handle = asyncio.run(
        O.start_combat(
            session_id=tag, party=[_wizard(('magic-missile',))],
            encounter=[
                EncounterMemberSpec(
                    entity_id='rat-a', name='A', entity_type='Monster',
                    initiative=10, hp_current=20, hp_max=20, ac=10, zone_id='0,2',
                ),
                EncounterMemberSpec(
                    entity_id='rat-b', name='B', entity_type='Monster',
                    initiative=9, hp_current=20, hp_max=20, ac=10, zone_id='1,2',
                ),
            ],
            grid_scene=GridScene(width=10, height=10), rng_seed=1,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _cast(handle, target_ids: tuple[str, ...], slot_level: int = 1):
    live = O._get_live(handle)
    slots_before = dict(live.spell_slots_by_entity['pc1'])
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            PlayerIntent(
                intent_type='cast_spell', spell_id='magic-missile',
                activity_id=MISSILE_ACTIVITY, target_ids=target_ids,
                slot_level=slot_level,
            ),
        )
    )
    events = list(O.drain_pending_events(handle))
    return live, slots_before, events


def _names(events) -> list[tuple[str, str]]:
    return [
        (type(e).__name__, getattr(e, 'reason', ''))
        for e in events
        if type(e).__name__ in ('CastFailed', 'SpellCast', 'EffectApplied')
    ]


def test_count_spell_cast_through_target_ids_resolves_and_spends_the_slot() -> None:
    """Одна цель через ``target_ids`` — каст, а не ``target_invalid``."""
    handle = _fight('mm-one')
    live, slots_before, events = _cast(handle, ('rat-a',))
    assert [r for _, r in _names(events) if r] == [], _names(events)
    assert live.tracked_hp['rat-a'] < 20
    assert live.tracked_hp['rat-b'] == 20
    assert live.spell_slots_by_entity['pc1'][1] == slots_before[1] - 1


def test_count_spell_spreads_darts_across_two_named_targets() -> None:
    """Две цели через ``target_ids`` — обе получают урон."""
    handle = _fight('mm-two')
    live, slots_before, events = _cast(handle, ('rat-a', 'rat-b'))
    assert [r for _, r in _names(events) if r] == [], _names(events)
    assert live.tracked_hp['rat-a'] < 20
    assert live.tracked_hp['rat-b'] < 20
    assert live.spell_slots_by_entity['pc1'][1] == slots_before[1] - 1


def test_count_spell_refuses_more_targets_than_it_has_darts() -> None:
    """Отказ по числу целей остаётся: на слоте 1 снарядов три, не четыре."""
    handle = _fight('mm-over')
    live, slots_before, events = _cast(handle, ('rat-a', 'rat-b', 'rat-a', 'rat-b'))
    assert _names(events) == [('CastFailed', 'target_invalid')]
    assert live.spell_slots_by_entity['pc1'] == slots_before
    assert live.tracked_hp['rat-a'] == 20 and live.tracked_hp['rat-b'] == 20


def test_count_spell_accepts_fewer_targets_than_its_maximum() -> None:
    """SRD 5.2: Magic Missile — ``up to`` three darts; две цели законны.

    Поэтому гейт обязан проверять «не больше N», а не «ровно N»:
    ``len(ids) != n`` отверг бы законный каст по двум целям.
    """
    handle = _fight('mm-fewer')
    live, slots_before, events = _cast(handle, ('rat-a',))
    assert [r for _, r in _names(events) if r] == [], _names(events)
    assert live.spell_slots_by_entity['pc1'][1] == slots_before[1] - 1
