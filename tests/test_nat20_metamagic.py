"""Приёмка §5/§7: метамагия колдуна доходит до расчёта, а не только до списка."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

FONT = 'feature_use:font-of-magic'


def _sorcerer(picks: tuple[str, ...], level: int = 5) -> Any:
    scores = AbilityScores(
        strength=8, dexterity=16, constitution=14,
        intelligence=10, wisdom=12, charisma=17,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='sorcerer', background_slug='soldier',
        level=level, subclass_slug='draconic', ability_scores=scores,
        selected_choices=picks,
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1', name='Sorcerer', zone_id='0,0', initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )


def _fight(member: Any, tag: str) -> Any:
    handle = asyncio.run(
        O.start_combat(
            session_id=tag,
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1', name='Foe', entity_type='Monster',
                    initiative=1, hp_current=9, hp_max=9, ac=10, zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _use(handle: Any, slug: str) -> Any:
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1', O.PlayerIntent(intent_type='use_feature', feature_id=slug)
        )
    )
    O.drain_pending_events(handle)
    return O._get_live(handle)


def test_a_metamagic_pick_reaches_the_character() -> None:
    """§5: обязательный выбор метамагии не теряется между листом и боем."""
    member = _sorcerer(('careful-spell', 'quickened-spell'))
    assert member.chosen_features == ('careful-spell', 'quickened-spell')
    handle = _fight(member, 'metamagic-picks')
    live = O._get_live(handle)
    caster = live.initiative[0]
    granted = O._granted_feature_slugs(caster)
    assert 'careful-spell' in granted, sorted(granted)
    assert 'quickened-spell' in granted, sorted(granted)


def test_a_careful_spell_spends_one_sorcery_point() -> None:
    """Careful Spell стоит 1 очко — списание видно по счётчику Font of Magic."""
    handle = _fight(_sorcerer(('careful-spell', 'quickened-spell')), 'careful')
    live = _use(handle, 'careful-spell')
    assert live.custom_counters_by_entity['pc1'][FONT]['spent'] == 1


def test_a_quickened_spell_spends_two_sorcery_points() -> None:
    """Quickened Spell стоит 2 очка — цена берётся из данных, а не из кода."""
    handle = _fight(_sorcerer(('careful-spell', 'quickened-spell')), 'quickened')
    live = _use(handle, 'quickened-spell')
    assert live.custom_counters_by_entity['pc1'][FONT]['spent'] == 2


def test_a_metamagic_not_picked_is_refused() -> None:
    """§7: чародей не может применить метамагию, которую не выбирал."""
    handle = _fight(_sorcerer(('careful-spell', 'quickened-spell')), 'notpicked')
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1', O.PlayerIntent(intent_type='use_feature', feature_id='heightened-spell')
        )
    )
    events = O.drain_pending_events(handle)
    dynamic = O._get_live(handle)
    spent = dynamic.custom_counters_by_entity.get('pc1', {}).get(FONT, {}).get('spent', 0)
    assert spent == 0, 'невыбранная метамагия не должна тратить очки'
    assert not any(type(e).__name__ == 'EffectApplied' for e in events), [
        type(e).__name__ for e in events
    ]

def test_a_pick_the_pool_never_offers_is_ignored() -> None:
    """§7: устаревший или подсунутый slug не протаскивает чужую способность."""
    member = _sorcerer(('careful-spell', 'quickened-spell'))
    smuggled = member.model_copy(
        update={'chosen_features': ('careful-spell', 'action-surge')}
    )
    handle = _fight(smuggled, 'smuggled')
    caster = O._get_live(handle).initiative[0]
    granted = O._granted_feature_slugs(caster)
    assert 'careful-spell' in granted
    assert 'action-surge' not in granted, 'чужой признак не должен попасть в репертуар'

def test_a_sorcerer_ten_holds_four_metamagic_options() -> None:
    """§5: 2 на 2-м + 2 на 10-м — квота пула складывается, а не берёт максимум."""
    picks = ('careful-spell', 'quickened-spell', 'heightened-spell', 'twinned-spell')
    member = _sorcerer(picks, level=10)
    granted = O._granted_feature_slugs(member)
    missing = [slug for slug in picks if slug not in granted]
    assert not missing, f'не дошли до репертуара: {missing}'


def test_a_sorcerer_ten_cannot_take_a_fifth_metamagic() -> None:
    """§5: превышение квоты отвергается с указанием причины, а не молча."""
    picks = (
        'careful-spell', 'quickened-spell', 'heightened-spell',
        'twinned-spell', 'subtle-spell',
    )
    with pytest.raises(ValueError) as excinfo:
        _sorcerer(picks, level=10)
    assert 'at most 4' in str(excinfo.value), str(excinfo.value)

INVOCATIONS = ('agonizing-blast', 'armor-of-shadows', 'ascendant-step', 'devils-sight',
     'eldritch-mind', 'eldritch-spear', 'fiendish-vigor', 'gift-of-the-depths',
     'mask-of-many-faces', 'misty-visions')


def test_a_warlock_twenty_holds_ten_invocations() -> None:
    """Тот же дефект квоты на другом пуле: 10 ступеней по 1 = 10 владений."""
    scores = AbilityScores(
        strength=8, dexterity=14, constitution=14,
        intelligence=10, wisdom=12, charisma=17,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='warlock', background_slug='soldier',
        level=20, subclass_slug='fiend', ability_scores=scores,
        selected_choices=INVOCATIONS,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1', name='Warlock', zone_id='0,0', initiative=20,
            hp_max=40, hp_current=40,
        ),
        loader=BundledAssetLoader(),
    )
    granted = O._granted_feature_slugs(member)
    missing = [slug for slug in INVOCATIONS if slug not in granted]
    assert not missing, f'владения не дошли до репертуара: {missing}'


def test_an_eleventh_invocation_is_refused() -> None:
    """§5: лишнее владение отвергается с указанием причины."""
    scores = AbilityScores(
        strength=8, dexterity=14, constitution=14,
        intelligence=10, wisdom=12, charisma=17,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='warlock', background_slug='soldier',
        level=20, subclass_slug='fiend', ability_scores=scores,
        selected_choices=(*INVOCATIONS, 'thirsting-blade'),
    )
    with pytest.raises(ValueError) as excinfo:
        build_party_member(
            spec,
            CombatInstance(
                entity_id='pc1', name='Warlock', zone_id='0,0', initiative=20,
                hp_max=40, hp_current=40,
            ),
            loader=BundledAssetLoader(),
        )
    assert 'at most 10' in str(excinfo.value), str(excinfo.value)

def test_spending_the_last_point_announces_the_refusal() -> None:
    """§5: отказ объясним. Исчерпанный пул даёт CastFailed, а не тишину.

    Отрицательный случай важнее положительного: раньше `submit_player_intent`
    возвращал `None` без единого события, и игрок не узнавал ни причину, ни то,
    что действие не состоялось.
    """
    handle = _fight(_sorcerer(('careful-spell', 'quickened-spell')), 'exhausted')
    live = O._get_live(handle)
    # 2 очка стоит Quickened Spell; на 5-м уровне пул — 5.
    live.custom_counters_by_entity.setdefault('pc1', {})[FONT] = {'spent': 5}
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            O.PlayerIntent(intent_type='use_feature', feature_id='quickened-spell'),
        )
    )
    events = O.drain_pending_events(handle)
    failures = [e for e in events if type(e).__name__ == 'CastFailed']
    assert failures, f'отказ прошёл молча: {[type(e).__name__ for e in events]}'
    assert failures[0].reason == 'no_uses_remaining', failures[0].reason
    spent = (
        O._get_live(handle).custom_counters_by_entity.get('pc1', {}).get(FONT, {}).get('spent', 0)
    )
    assert spent == 5, f'отказ не должен списывать: {spent}'

def _refusal(handle: Any, feature_id: str) -> Any:
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1', O.PlayerIntent(intent_type='use_feature', feature_id=feature_id),
        )
    )
    events = O.drain_pending_events(handle)
    failures = [e for e in events if type(e).__name__ == 'CastFailed']
    assert failures, f'отказ прошёл молча: {[type(e).__name__ for e in events]}'
    return failures[0].reason


def test_a_feature_outside_the_repertoire_is_announced() -> None:
    """§5: чужой признак отвергается вслух, а не молчаливым `return`."""
    handle = _fight(_sorcerer(('careful-spell', 'quickened-spell')), 'refuse-foreign')
    assert _refusal(handle, 'action-surge') == 'not_in_repertoire'


def test_an_unpicked_metamagic_is_announced_as_out_of_repertoire() -> None:
    """Метамагия из того же пула, но не выбранная — та же причина."""
    handle = _fight(_sorcerer(('careful-spell', 'quickened-spell')), 'refuse-unpicked')
    assert _refusal(handle, 'heightened-spell') == 'not_in_repertoire'
    spent = (
        O._get_live(handle).custom_counters_by_entity.get('pc1', {}).get(FONT, {}).get(
            'spent', 0
        )
    )
    assert spent == 0, f'отказ не должен списывать: {spent}'
