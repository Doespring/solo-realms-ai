"""Требование 6: скрытность и обнаружение (SRD 5.2 §Hide).

* Hide требует проверки Ловкость (Скрытность) против DC 15 и укрытия три
  четверти / полного, тяжёлого затемнения или темноты;
* при успехе существо получает состояние Invisible, пока скрыто;
* атака из скрытности идёт с преимуществом и раскрывает существо.

Проверяется ФАКТИЧЕСКОЕ состояние: `hidden_entities`, список состояний и
преимущество на броске атаки, а не только факт события.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=10, dexterity=16, constitution=14,
    intelligence=10, wisdom=12, charisma=10,
)

# seed=5 даёт проверку Скрытности 25 — успех против DC 15.
SEED = 5


def _hide_fight(target_zone, *, three_quarters=True):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="rogue", background_slug="criminal",
        level=1, ability_scores=SCORES,
    )
    rogue = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Rogue", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    scene = GridScene(
        width=8, height=6,
        cover_cells={"0,0": "three_quarters"} if three_quarters else {},
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="hide", party=[rogue],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Foe", entity_type="Monster",
                    initiative=1, hp_current=50, hp_max=50, ac=12, zone_id=target_zone,
                )
            ],
            grid_scene=scene, rng_seed=SEED,
        )
    ).handle
    asyncio.run(
        O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="hide"))
    )
    return handle, O.drain_pending_events(handle)


def test_impossible_cover_blocks_hide_without_rolling():
    """Без укрытия и затемнения Hide запрещён ДО броска — случайность не тратится."""
    try:
        _hide_fight("5,0", three_quarters=False)
    except O.IntentRejectedError as exc:
        assert "target_invalid" in str(exc), "ожидался отказ по обстоятельствам"
    else:
        raise AssertionError("Hide без укрытия обязан быть отвергнут")


def test_a_successful_hide_grants_invisible_and_hidden_state():
    handle, events = _hide_fight("5,0")
    check = next(e for e in events if e.type == "check_rolled")
    assert check.dc == 15, "DC скрытности должен быть 15"
    assert check.succeeded is True, "seed 5 обязан пройти DC 15"
    live = O._get_live(handle)
    assert "pc1" in live.hidden_entities, "успешный Hide не пометил существо скрытым"
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    names = [c.condition for c in hero.conditions]
    assert "invisible" in names, "скрытое существо должно быть Invisible: " + str(names)


def test_attacking_from_hiding_has_advantage_and_breaks_the_hide():
    """Атака из скрытности: преимущество, затем скрытность и Invisible сняты."""
    handle, _ = _hide_fight("1,0")
    live = O._get_live(handle)
    assert "pc1" in live.hidden_entities, "предусловие: существо скрыто"
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="attack", target_id="m1", weapon_id="dagger"),
        )
    )
    events = O.drain_pending_events(handle)
    attack = next(e for e in events if e.type == "attack_rolled")
    assert attack.advantage == "advantage", (
        "атака из скрытности должна идти с преимуществом, получено: " + str(attack.advantage)
    )
    live = O._get_live(handle)
    assert "pc1" not in live.hidden_entities, "атака обязана раскрыть существо"
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    names = [c.condition for c in hero.conditions]
    assert "invisible" not in names, "после атаки Invisible обязан сняться: " + str(names)

