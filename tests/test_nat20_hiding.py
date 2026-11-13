"""Требование 6: скрытность и обнаружение (§ «Hiding» SRD 5.2).

Правило: «With the Hide action, you try to conceal yourself. To do so, you must
succeed on a DC 15 Dexterity (Stealth) check while you are Heavily Obscured or
behind Three-Quarters or Total Cover». Успех даёт состояние Невидимый.

Проверяется боевым путём: `start_combat` → `submit_player_intent(hide)`.
Отрицательный случай — открытая местность, положительный — сильное затемнение.
Оба идут через один и тот же обработчик, меняя единственное обстоятельство сцены.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=10, dexterity=16, constitution=12,
    intelligence=10, wisdom=10, charisma=10,
)
PICKS = (
    "skill:stealth", "skill:sleight-of-hand", "skill:acrobatics", "skill:perception",
    "expertise:stealth", "expertise:acrobatics",
)


def _handle(scene):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="rogue", background_slug="criminal",
        level=1, ability_scores=SCORES, selected_choices=PICKS,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Rogue", zone_id="0,0", initiative=20,
            hp_max=20, hp_current=20,
        ),
        loader=BundledAssetLoader(),
    )
    return asyncio.run(
        O.start_combat(
            session_id="hide",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=99, hp_max=99, ac=12, zone_id="5,0",
                )
            ],
            grid_scene=scene,
            rng_seed=5,
        )
    ).handle


def _conditions(handle):
    live = O._get_live(handle)
    actor = next(c for c in live.initiative if c.entity_id == "pc1")
    return [str(c.condition) for c in (getattr(actor, "conditions", ()) or ())]


def test_hiding_in_the_open_is_refused():
    """Открытая местность: прятаться не за чем — конкретный отказ, не тишина."""
    handle = _handle(GridScene(width=8, height=6))
    with pytest.raises(O.IntentRejectedError, match="cover|Obscured"):
        asyncio.run(
            O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="hide"))
        )
    assert "invisible" not in _conditions(handle)


def test_hiding_in_heavy_obscurement_rolls_and_conceals():
    """Heavily Obscured: проверка ЛОВ (Скрытность) и состояние Невидимый."""
    handle = _handle(
        GridScene(width=8, height=6, obscurement_cells={"0,0": "heavy"})
    )
    asyncio.run(
        O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="hide"))
    )
    events = O.drain_pending_events(handle)
    checks = [e for e in events if e.type == "check_rolled"]
    assert checks, "скрытность не сделала проверку"
    check = checks[0]
    assert check.skill == "stealth", check.skill
    assert check.dc == 15, "СЛ скрытности по SRD 5.2 — 15"
    applied = [e for e in events if e.type == "condition_applied"]
    assert any(e.condition == "invisible" for e in applied), (
        "успешная скрытность не дала состояние Невидимый: " + str(applied)
    )
    assert "invisible" in _conditions(handle)


def test_darkness_is_also_enough_to_hide():
    """Тьма делает существ Heavily Obscured — прятаться можно."""
    handle = _handle(GridScene(width=8, height=6, default_lighting="dark"))
    asyncio.run(
        O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="hide"))
    )
    assert "invisible" in _conditions(handle)

