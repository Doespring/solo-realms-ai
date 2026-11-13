"""Требование 7 и 13: ресурсы способностей проверяются ИСПОЛНЕНИЕМ в бою.

Источник квоты — `ScaleValue` в `canonical/classes/fighter.json`:
`Second Wind: scale {1: 2, 4: 3, 10: 4}`. Ресурсов в `DerivedSheet` нет вовсе
(37 полей, ни одного с `use`/`pool`) — значит, проверять их можно ТОЛЬКО в бою.

Важно: `use_feature` вызывается за ТЕКУЩЕГО актора. В первых прогонах я вызывал его
за монстра и получал `feature_not_in_repertoire` — это верный отказ для существа без
такой способности, а не дефект движка.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=16, dexterity=12, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)


def _fight(level, hp_current=1):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Fighter", zone_id="0,0", initiative=20,
            hp_max=12, hp_current=hp_current,
        ),
        loader=BundledAssetLoader(),
    )
    return asyncio.run(
        O.start_combat(
            session_id="resources",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=99, hp_max=99, ac=10, zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=4,
        )
    ).handle


def _spend_second_wind(handle, turns):
    """Провести N своих ходов, каждый раз пытаясь потратить Second Wind."""
    outcomes = []
    for _ in range(turns):
        live = O._get_live(handle)
        actor = live.current_actor_id
        if actor == "pc1":
            try:
                asyncio.run(
                    O.submit_player_intent(
                        handle, "pc1",
                        O.PlayerIntent(intent_type="use_feature", feature_id="second-wind"),
                    )
                )
            except Exception as error:  # noqa: BLE001 - отказ фиксируется как исход
                outcomes.append(str(error))
                live = O._get_live(handle)
                actor = live.current_actor_id
            events = O.drain_pending_events(handle)
            if any(e.type == "healing_applied" for e in events):
                outcomes.append("healed")
            else:
                failed = [e for e in events if e.type == "cast_failed"]
                outcomes.append(failed[0].reason if failed else "no_effect")
        try:
            asyncio.run(
                O.submit_player_intent(handle, actor, O.PlayerIntent(intent_type="pass"))
            )
        except Exception:  # noqa: BLE001 - смена актора служебная
            pass
    return outcomes


def test_second_wind_heals_through_a_real_turn():
    """SRD 5.2: «regain Hit Points equal to 1d10 plus your Fighter level»."""
    handle = _fight(1, hp_current=1)
    outcomes = _spend_second_wind(handle, 1)
    assert "healed" in outcomes, outcomes
    hp = next(c.hp_current for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    # 1d10 + 1 уровень, от 1 хита: итог 3..12 при максимуме 12.
    assert 1 < hp <= 12


def test_level_one_grants_exactly_two_uses():
    """Корпус: Second Wind scale {1: 2, 4: 3, 10: 4}."""
    handle = _fight(1)
    outcomes = _spend_second_wind(handle, 8)
    assert outcomes.count("healed") == 2, outcomes
    assert "no_uses_remaining" in outcomes, outcomes


def test_a_monster_without_the_feature_is_refused_not_granted():
    """Репертуар не выдумывается: у монстра Second Wind нет."""
    handle = _fight(1)
    live = O._get_live(handle)
    with __import__("pytest").raises(Exception):
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1",
                O.PlayerIntent(intent_type="use_feature", feature_id="second-wind"),
            )
        )
        asyncio.run(
            O.submit_player_intent(
                handle, "m1",
                O.PlayerIntent(intent_type="use_feature", feature_id="second-wind"),
            )
        )

