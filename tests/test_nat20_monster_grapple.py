"""r199 — захват монстром настоящий: данные, выбор действия, бросок освобождения.

r198 замерил три вещи (`scripts/probe_r198_monster_grapple.py`): у 58 действий
монстров захват есть только в ТЕКСТЕ (эффект-ссылка ведёт в никуда), селектор
берёт мультиатаку или первую атаку, а принудительно выбранный захват даёт
`save_rolled` и пустые `conditions`. Этот файл держит исправление:

1. **Данные.** `Monster.passive_effects` — то, что Foundry хранит у актора;
   канонический срез поле потерял, поэтому КАЖДАЯ ссылка `activity.effects[].id`
   у монстров висела (`effect_ref_unresolved`). У `bugbear-stalker`
   Quick Grapple и `constrictor-snake` Constrict эффект типизирован
   (`statuses: ["grappled"]`) вместе с напечатанным КС освобождения, а КС
   сверяется с типизированной активностью «Escape Check» того же действия.
2. **Селектор.** `rank_monster_actions` получает ярус «действие, которое
   накладывает состояние, которого у цели ещё нет» — ВЫШЕ мультиатаки и только
   когда вызывающий передал предикат. Монстр без типизированных эффектов
   выбирает ровно то же, что и раньше (проверяется по всем 341 монстрам).
3. **Побочная дыра r198.** `grapple` с `target_id` самого героя движок
   принимал и бросал спасбросок; по SRD 5.2 Unarmed Strike цель — другое
   существо, поэтому теперь `IntentRejectedError("target_invalid")`.

Броски читаются из событий движка, а не пересчитываются в тесте.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.activities.monster_actions import (expand_action_to_parts,
                                                     rank_monster_actions)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader
from dnd5e_srd_data.schema.common import CheckActivity, SaveActivity

ROOT = Path(__file__).resolve().parents[1]
SCORES = AbilityScores(strength=16, dexterity=14, constitution=14,
                       intelligence=10, wisdom=10, charisma=8)

#: Напечатанный в SRD 5.2.1 КС освобождения (не вычисленный движком).
PRINTED = {'bugbear-stalker': ('Quick Grapple', 13), 'constrictor-snake': ('Constrict', 12)}
LOADER = BundledAssetLoader()


# --- 1. Данные --------------------------------------------------------------


def test_the_corpus_types_the_grapple_the_stat_block_prints():
    """Ссылка эффекта обязана разрешаться: иначе движку нечего накладывать."""
    for slug, (action_name, escape_dc) in PRINTED.items():
        monster = LOADER.get_monster(slug)
        action = next(a for a in monster.actions if a.name == action_name)
        refs = [ref for activity in action.activities
                for ref in (activity.effects or [])]
        assert refs, '%s/%s без ссылки на эффект' % (slug, action_name)
        by_id = {effect.id: effect for effect in monster.passive_effects}
        resolved = [by_id.get(ref.id) for ref in refs]
        assert all(resolved), (slug, [ref.id for ref in refs], sorted(by_id))
        assert all('grappled' in effect.statuses for effect in resolved), resolved
        assert [effect.escape_dc for effect in resolved] == [escape_dc] * len(resolved)

        # КС не выдуман: он равен тому, что печатает типизированная активность
        # «Escape Check» ЭТОГО ЖЕ действия (Foundry: «escape DC @skills.ath.passive»).
        checks = [activity for activity in action.activities
                  if isinstance(activity, CheckActivity)]
        assert len(checks) == 1, (slug, [a.kind for a in action.activities])
        assert checks[0].check.dc.formula == '@skills.ath.passive', checks[0].check.dc
        assert sorted(checks[0].check.associated) == ['acr', 'ath'], checks[0].check


def test_the_printed_escape_dc_is_the_passive_athletics_the_corpus_prints():
    """КС освобождения = «10 + мод Силы + владение» (пассивная Атлетика Foundry).

    Это сверка ДАННЫХ с их же источником: число в эффекте обязано совпадать с
    формулой, которую печатает активность, иначе две копии разъедутся молча.
    """
    for slug, (_, escape_dc) in PRINTED.items():
        monster = LOADER.get_monster(slug)
        athletics = monster.skills.athletics
        proficient = athletics is not None
        strength = monster.ability_scores.str
        bonus = athletics if proficient else strength // 2 - 5
        computed = 10 + bonus
        assert computed == escape_dc, (slug, computed, escape_dc)


# --- бой --------------------------------------------------------------------


def fight(slug, seed=5, foe_zone='1,0'):
    spec = CharacterBuildSpec(species_slug='human', class_slug='fighter',
                              background_slug='soldier', level=1,
                              ability_scores=SCORES, selected_choices=('archery', 'skilled'))
    hero = build_party_member(
        spec,
        CombatInstance(entity_id='hero', name='Fighter', zone_id='0,0',
                       initiative=20, hp_max=40, hp_current=40),
        loader=BundledAssetLoader())
    return asyncio.run(O.start_combat(
        session_id='r199', party=[hero],
        encounter=[EncounterMemberSpec(entity_id='foe', name='Foe', entity_type='Monster',
                                       initiative=1, hp_current=40, hp_max=40, ac=13,
                                       zone_id=foe_zone, monster_template_slug=slug,
                                       base_speed=30)],
        grid_scene=GridScene(width=8, height=5), rng_seed=seed)).handle


def reach_turn(handle, actor, limit=8):
    """Довести бой до хода ``actor`` (ход самого ``actor`` ещё не отыгран)."""
    events = []
    for _ in range(limit):
        live = O._get_live(handle)
        if live.ended or live.initiative[live.current_turn_index].entity_id == actor:
            break
        if live.initiative[live.current_turn_index].entity_type == 'Character':
            asyncio.run(O.submit_player_intent(handle, live.initiative[
                live.current_turn_index].entity_id, O.PlayerIntent(intent_type='pass')))
        else:
            asyncio.run(O.advance_monster_turn(handle))
        events += O.drain_pending_events(handle)
    return events


def turn_of(handle, actor):
    """Отыграть ход ``actor`` и вернуть его события."""
    events = reach_turn(handle, actor)
    if actor == 'foe':
        asyncio.run(O.advance_monster_turn(handle))
        events += O.drain_pending_events(handle)
    return events


def conditions(handle, entity):
    live = O._get_live(handle)
    row = next(c for c in live.initiative if c.entity_id == entity)
    return {ac.condition: ac for ac in row.conditions}


def escape(handle):
    asyncio.run(O.submit_player_intent(handle, 'hero',
                                       O.PlayerIntent(intent_type='escape_grapple')))
    events = O.drain_pending_events(handle)
    check = next(event for event in events if event.type == 'check_rolled')
    return check, events


# --- 2. Ход монстра накладывает настоящий захват ----------------------------


@pytest.mark.parametrize('slug', sorted(PRINTED))
def test_the_monster_grapple_lands_on_the_hero_with_the_printed_escape_dc(slug):
    """Живой бой: ход существа → на герое `grappled` с КС, а не пустой список."""
    _, escape_dc = PRINTED[slug]
    handle = fight(slug, seed=1)
    events = turn_of(handle, 'foe')
    row = conditions(handle, 'hero')
    assert set(row) == {'grappled'}, (events, row)
    assert row['grappled'].save_dc == escape_dc, row['grappled']
    assert row['grappled'].source_effect_id, row['grappled']
    live = O._get_live(handle)
    effect = next(effect for effect in live.active_effects['hero']
                  if effect.id == row['grappled'].source_effect_id)
    assert effect.statuses == {'grappled'}, effect
    # Источник называет существ: r198 читает имя захватчика из origin.
    assert effect.origin.split(':')[0] in {'cast', 'grapple'}, effect.origin
    assert effect.origin.split(':')[2] == 'foe', effect.origin


def test_the_monster_turn_does_not_roll_the_victims_escape_check():
    """«Escape Check» — действие ЖЕРТВЫ, а не монстра (SRD 5.2 Ending a Grapple).

    До правки принудительно выбранный захват давал `save_rolled` + `check_rolled`
    (жертва «освобождалась» в момент захвата) — замер r198, шаг 3.
    """
    handle = fight('bugbear-stalker', seed=1)
    events = turn_of(handle, 'foe')
    checks = [event for event in events if event.type == 'check_rolled']
    assert checks == [], [(event.actor_id, event.dc) for event in checks]
    assert any(event.type == 'save_rolled' for event in events), events
    # И в список активностей действия захват-проверка не попадает.
    monster = LOADER.get_monster('bugbear-stalker')
    action = next(a for a in monster.actions if a.name == 'Quick Grapple')
    kinds = [activity.kind for _, activity in expand_action_to_parts(monster, action)]
    assert kinds == ['save'], kinds


# --- 3. Освобождение: успех снимает, провал оставляет -----------------------


def escape_outcomes(slug, seeds=range(1, 9)):
    """Сколько успехов и провалов даёт бросок освобождения на разных сидах."""
    outcomes = {}
    for seed in seeds:
        handle = fight(slug, seed=seed)
        turn_of(handle, 'foe')
        if 'grappled' not in conditions(handle, 'hero'):
            continue
        turn_of(handle, 'hero')
        check, _ = escape(handle)
        assert check.dc == PRINTED[slug][1], check
        held = 'grappled' in conditions(handle, 'hero')
        assert held is (not check.succeeded), (seed, check.roll_total, check.dc, held)
        outcomes[check.succeeded] = outcomes.get(check.succeeded, 0) + 1
    return outcomes


def test_escape_succeeds_and_fails_on_fixed_seeds():
    outcomes = escape_outcomes('bugbear-stalker')
    assert set(outcomes) == {True, False}, outcomes


# --- 4. Селектор ------------------------------------------------------------


def _ranked(slug, *, target=None, seed=1):
    monster = LOADER.get_monster(slug)
    predicate = O._monster_control_predicate(monster, target)
    return [action.slug for action in rank_monster_actions(
        monster.actions, is_available=lambda a: True, imposes_new_condition=predicate)]


def test_the_selector_prefers_the_control_action_only_while_it_lands_something():
    """Захват выбирается, пока цель его не имеет; после — обычная мультиатака."""
    handle = fight('bugbear-stalker', seed=1)
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == 'hero')
    assert _ranked('bugbear-stalker', target=hero)[0] == 'quick-grapple'
    assert _ranked('constrictor-snake', target=hero)[0] == 'constrict'

    turn_of(handle, 'foe')
    grappled_hero = next(c for c in O._get_live(handle).initiative if c.entity_id == 'hero')
    assert _ranked('bugbear-stalker', target=grappled_hero)[0] == 'multiattack'
    assert _ranked('constrictor-snake', target=grappled_hero)[0] == 'bite'


def test_the_selection_of_every_monster_without_typed_effects_is_unchanged():
    """Старые бои — байт-в-байт: без типизированных эффектов выбор прежний.

    Прежнее правило (документировано в `select_typed_monster_action`):
    мультиатака, иначе первая активность с атакой/спасброском. Предикат
    вызывающего не должен его менять НИ У ОДНОГО такого монстра.
    """
    corpus = ROOT / 'vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/monsters'
    checked = 0
    for path in sorted(corpus.glob('*.json')):
        monster = LOADER.get_monster(path.stem)
        if monster is None or monster.passive_effects:
            continue
        old = rank_monster_actions(monster.actions, is_available=lambda a: True)
        new = rank_monster_actions(monster.actions, is_available=lambda a: True,
                                   imposes_new_condition=O._monster_control_predicate(
                                       monster, _FakeTarget()))
        assert [a.slug for a in old] == [a.slug for a in new], path.stem
        checked += 1
    assert checked >= 300, checked


class _FakeTarget:
    """Цель без состояний: предикат по ней ничего не накладывает."""

    entity_id = 'fake'
    conditions: list = []
    condition_immunities: list = []


# --- 5. Побочная дыра r198: схватить себя нельзя -----------------------------


def test_grappling_yourself_is_refused_without_spending_the_action():
    handle = fight('bugbear-stalker', seed=1)
    turn_of(handle, 'hero')
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == 'hero')
    assert hero.action_available is True
    O.drain_pending_events(handle)
    with pytest.raises(O.IntentRejectedError) as rejected:
        asyncio.run(O.submit_player_intent(handle, 'hero', O.PlayerIntent(
            intent_type='grapple', target_id='hero')))
    assert rejected.value.reason == 'target_invalid', rejected.value
    assert conditions(handle, 'hero') == {}, 'условие не должно появиться'
    assert O.drain_pending_events(handle) == [], 'спасбросок бросать не на что'
    live = O._get_live(handle)
    assert next(c for c in live.initiative if c.entity_id == 'hero').action_available is True
