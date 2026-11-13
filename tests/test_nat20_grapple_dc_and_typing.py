"""r203 — КС «быстрого захвата» из книги и типизированный спасбросок захвата.

r199 сделал захват настоящим у двух действий из 58. Здесь закрыты два остатка,
записанных в журнале r199:

1. **КС.** `bugbear-stalker` / Quick Grapple считался по Ловкости
   (`save.dc.calculation: "dex"` → 8 + PB 2 + мод 2 = **12**), а SRD 5.2.1
   печатает **13** (8 + PB 2 + мод Силы 3). Число живёт в данных корпуса —
   правка там, в хосте второго расчёта нет.
2. **Типизация.** Ещё девять ссылок `activity.effects[].id` у сейв-действий с
   захватом вели в общий пак `monsterfeatures24`, которого в каноническом срезе
   нет: движку было нечего накладывать. Определения добавлены в
   `Monster.passive_effects` по напечатанному тексту (статусы + КС освобождения).

Третий пункт — следствие второго, найденный живым прогоном: у эффекта с
несколькими статусами успешное освобождение снимало только `grappled`, а
`blinded`/`restrained` оставались на цель до конца боя. Движок теперь снимает
все состояния, которые наложил эффект (`_release_grapple_conditions`).

Броски читаются из событий движка, а не пересчитываются в тесте.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

ROOT = Path(__file__).resolve().parents[1]
MONSTERS = (ROOT / "vendor/nat20/packages/dnd5e-srd-data/src/"
            "dnd5e_srd_data/canonical/monsters")
SCORES = AbilityScores(strength=16, dexterity=14, constitution=14,
                       intelligence=10, wisdom=10, charisma=8)
LOADER = BundledAssetLoader()

#: Напечатанные КС (SRD 5.2.1) сверяются в probe_r203_grapple_typed.py с
#: адресом страницы; тест держит те же числа.
PRINTED_SAVE_DC = {('bugbear-stalker', 'Quick Grapple'): 13}

#: Типизированные r203 ссылки: (существо, действие, id эффекта) ->
#: (напечатанные статусы, напечатанный КС освобождения).
TYPED = {
    ("behir", "Constrict", "Hd0Gy3wJtPfObERq"): ({"grappled", "restrained"}, 16),
    ("couatl", "Constrict", "Hd0Gy3wJtPfObERq"): ({"grappled", "restrained"}, 13),
    ("giant-constrictor-snake", "Constrict", "Hd0Gy3wJtPfObERq"): ({"grappled"}, 14),
    ("marilith", "Constrict", "Hd0Gy3wJtPfObERq"): ({"grappled", "restrained"}, 14),
    ("salamander", "Constrict", "Hd0Gy3wJtPfObERq"): ({"grappled", "restrained"}, 14),
    ("invisible-stalker", "Vortex", "fWPQye4Mr7VNbX9J"): ({"grappled"}, 13),
    ("shambling-mound", "Engulf", "vsa7d6xWOAvWdOWM"): (
        {"grappled", "blinded", "restrained"}, 14),
    ("water-elemental", "Whelm", "rN76liiHkUoMRMJN"): ({"grappled", "restrained"}, 14),
    ("kraken", "Fling", "UovyViBfR9aAlt0y"): ({"prone"}, None),
}

#: Ссылки, которые по книге НЕ типизируются как состояние цели. Причина —
#: probe_r203_grapple_typed.py:ADJUDICATION (та же таблица, здесь только id).
DECLARED_ADJUDICATION = {
    "VOHv1LyO3hHolZXo",  # Swallow: провал сразу проглатывает, Grappled тут же снимается
    "aS5FyMP72vn3OKf1",  # Swallow: CON-спасбросок бросает САМО существо
    "5ReKSrBu11ume7GP",  # Chuul: Poisoned+Paralyzed с повтором спасброска в конце хода
}


def _monster_record(slug: str) -> dict:
    return json.loads((MONSTERS / f"{slug}.json").read_text(encoding="utf-8"))


def _fight(slug: str, seed: int) -> object:
    spec = CharacterBuildSpec(species_slug="human", class_slug="fighter",
                              background_slug="soldier", level=1,
                              ability_scores=SCORES, selected_choices=("archery", "skilled"))
    hero = build_party_member(
        spec,
        CombatInstance(entity_id="hero", name="Fighter", zone_id="0,0",
                       initiative=20, hp_max=60, hp_current=60),
        loader=BundledAssetLoader())
    return asyncio.run(O.start_combat(
        session_id="r203", party=[hero],
        encounter=[EncounterMemberSpec(entity_id="foe", name="Foe", entity_type="Monster",
                                       initiative=1, hp_current=120, hp_max=120, ac=13,
                                       zone_id="1,0", monster_template_slug=slug,
                                       base_speed=30)],
        grid_scene=GridScene(width=8, height=5), rng_seed=seed)).handle


def _monster_turn(handle) -> list:
    """Довести бой до хода существа и отыграть его."""
    events = []
    for _ in range(6):
        live = O._get_live(handle)
        if live.ended:
            break
        current = live.initiative[live.current_turn_index]
        if current.entity_id == "foe":
            asyncio.run(O.advance_monster_turn(handle))
            events += O.drain_pending_events(handle)
            break
        asyncio.run(O.submit_player_intent(handle, "hero",
                                           O.PlayerIntent(intent_type="pass")))
        events += O.drain_pending_events(handle)
    return events


def _conditions(handle) -> dict:
    live = O._get_live(handle)
    row = next(c for c in live.initiative if c.entity_id == "hero")
    return {ac.condition: ac for ac in row.conditions}


def _escape_when_it_is_the_heros_turn(handle):
    for _ in range(4):
        live = O._get_live(handle)
        if live.ended or live.initiative[live.current_turn_index].entity_id == "hero":
            break
        asyncio.run(O.advance_monster_turn(handle))
        O.drain_pending_events(handle)
    asyncio.run(O.submit_player_intent(handle, "hero",
                                       O.PlayerIntent(intent_type="escape_grapple")))
    events = O.drain_pending_events(handle)
    return next(event for event in events if event.type == "check_rolled")


# --- 1. КС спасброска = напечатанному --------------------------------------


def test_quick_grapple_save_dc_is_the_printed_thirteen():
    """Было 12 (мод Ловкости 2), напечатано 13 (мод Силы 3) — замер r199/r203."""
    monster = _monster_record("bugbear-stalker")
    action = next(a for a in monster["actions"] if a["name"] == "Quick Grapple")
    save = next(a for a in action["activities"] if a["kind"] == "save")
    assert save["save"]["ability"] == ["dex"], "цель спасается Ловкостью — это не менялось"
    assert save["save"]["dc"]["calculation"] == "str", save["save"]["dc"]

    # «Было» — тот же расчёт по прежней характеристике.
    scores = monster["ability_scores"]
    was = 8 + monster["proficiency_bonus"] + (scores["dex"] - 10) // 2
    now = 8 + monster["proficiency_bonus"] + (scores["str"] - 10) // 2
    assert (was, now) == (12, PRINTED_SAVE_DC[("bugbear-stalker", "Quick Grapple")])

    # Движок в живом бою бросает тот же КС, что напечатан.
    handle = _fight("bugbear-stalker", seed=1)
    events = _monster_turn(handle)
    save_rolled = next(e for e in events if e.type == "save_rolled")
    assert save_rolled.dc == 13, save_rolled
    assert _conditions(handle)["grappled"].save_dc == 13


# --- 2. Каждая ссылка разрешается или объяснена ----------------------------


def test_every_grapple_save_effect_ref_is_typed_or_declared():
    """Ноль «висящих» ссылок: либо определение, либо причина строкой."""
    unresolved, typed = [], 0
    for path in sorted(MONSTERS.glob("*.json")):
        monster = json.loads(path.read_text(encoding="utf-8"))
        slug = monster.get("slug") or path.stem
        effects = {e["id"] for e in monster.get("passive_effects") or []}
        for action in monster.get("actions") or []:
            text = (action.get("name", "") + " " + str(action.get("description", ""))).lower()
            if "rapple" not in text:
                continue
            for activity in action.get("activities") or []:
                if activity.get("kind") != "save":
                    continue
                for ref in activity.get("effects") or []:
                    if ref["id"] in effects:
                        typed += 1
                    elif (slug, action["name"], ref["id"]) in TYPED:
                        unresolved.append((slug, action["name"], ref["id"], "в типизации"))
                    else:
                        unresolved.append((slug, action["name"], ref["id"], "adjudication"))
    declared = [row for row in unresolved if row[3] == "adjudication" and row[2] in DECLARED_ADJUDICATION]
    unexplained = [row for row in unresolved if row[3] != "adjudication"]
    assert not unexplained, unexplained
    assert not [row for row in unresolved if row[3] == "adjudication"
                and row[2] not in DECLARED_ADJUDICATION], unresolved
    assert typed == 11, f"типизированных ссылок {typed}: 2 (r199) + 9 (r203)"
    assert len(declared) == 11, declared


def test_the_typed_escape_dc_is_the_passive_athletics_the_corpus_prints():
    """КС освобождения = «10 + мод Силы (+ владение)» — та же формула, что
    печатает типизированная активность «Escape Check» (@skills.ath.passive)."""
    for (slug, action_name, effect_id), (statuses, escape_dc) in TYPED.items():
        monster = _monster_record(slug)
        effect = next(e for e in monster["passive_effects"] if e["id"] == effect_id)
        assert set(effect["statuses"]) == statuses, (slug, effect)
        skills = monster.get("skills") or {}
        athletics = skills.get("athletics")
        strength = monster["ability_scores"]["str"]
        bonus = athletics if athletics is not None else (strength - 10) // 2
        computed = 10 + bonus
        if escape_dc is None:
            # Fling накладывает Prone — состояния, из которого не вырываются.
            assert effect["escape_dc"] is None, (slug, effect)
            continue
        assert computed == escape_dc == effect["escape_dc"], (slug, computed, escape_dc)


# --- 3. Живой бой: напечатанные состояния и их снятие ----------------------


def test_engulf_lands_the_printed_conditions_and_escape_clears_them_all():
    """Shambling Mound: провал спасброска → Grappled+Blinded+Restrained.

    Успешное освобождение снимает ВСЕ три: «until the grapple ends» — их
    единственный срок. До правки движка оставались Blinded и Restrained.
    """
    held_seed, freed_seed = 1, 3  # замер: сид 1 — провал освобождения, 3 — успех
    for seed, should_escape in ((held_seed, False), (freed_seed, True)):
        handle = _fight("shambling-mound", seed=seed)
        _monster_turn(handle)
        row = _conditions(handle)
        assert set(row) == {"grappled", "blinded", "restrained"}, (seed, row)
        assert row["grappled"].save_dc == 14, row["grappled"]
        check = _escape_when_it_is_the_heros_turn(handle)
        assert check.dc == 14, check
        assert check.succeeded is should_escape, (seed, check.roll_total)
        remaining = set(_conditions(handle))
        assert remaining == (set() if should_escape else {"grappled", "blinded", "restrained"}), (
            seed, remaining)
