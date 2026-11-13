"""SRD 5.2 Overchannel: the backlash scales with how often it was used.

The corpus states the second-and-later use as "(2 + @item.uses.spent -
1)d12" Necrotic damage. Two seams were missing: the engine did not resolve
``@item.uses.spent`` at all (the cast raised "Unhandled roll-data token"),
and ``_fold_arithmetic_dice_count`` only collapsed a ``floor(...)`` group, so
"(2 + 1 - 1)d12" never became a dice count. Both are fixed; these tests pin
the resulting damage to the dice the book asks for.
"""
import asyncio
import json
from pathlib import Path

from dnd5e_engine import (
    GridScene,
    PlayerIntent,
    drain_pending_events,
    get_live,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "nat20"
FEATURE = "overchannel"


def _activities():
    path = next(VENDOR.rglob("canonical/**/overchannel.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    return {a["name"]: a["id"] for a in data["activities"]}


def _fight(seed=1):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="acolyte",
        level=14, subclass_slug="evoker",
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=10, charisma=10,
        ),
    )
    party = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0",
                       initiative=20, hp_max=99, hp_current=99),
        loader=loader,
    )
    handle = asyncio.run(start_combat(
        session_id="s", party=[party],
        encounter=[EncounterMemberSpec(
            entity_id="m1", name="Target", entity_type="Monster",
            initiative=1, hp_current=999, hp_max=999, ac=2, zone_id="1,0")],
        grid_scene=GridScene(width=10, height=10), rng_seed=seed,
    )).handle
    drain_pending_events(handle)
    return handle


def _use(handle, name, seed_warmup=0):
    asyncio.run(submit_player_intent(
        handle, "pc1",
        PlayerIntent(intent_type="use_feature", feature_id=FEATURE,
                     activity_id=_activities()[name]),
    ))
    return drain_pending_events(handle)


def _damage(events):
    return [(e.amount, e.damage_type) for e in events if e.type == "damage_applied"]


def _spent(handle):
    live = get_live(handle)
    counters = getattr(live, "custom_counters_by_entity", {}).get("pc1", {})
    return counters.get("feature_use:" + FEATURE, {}).get("spent")


def test_the_first_use_has_no_backlash():
    """\"The first time you do so, you suffer no adverse effect.\" The
    First Use activity carries an empty damage part on purpose."""
    events = _use(_fight(), "First Use")
    assert _damage(events) == []


def test_a_later_use_deals_necrotic_damage_without_raising():
    """An unhandled @item.uses.spent used to raise out of the entry point."""
    events = _use(_fight(), "Second+ Use")
    assert _damage(events), "ожидался некротический урон"
    assert _damage(events)[0][1] == "necrotic"


def test_the_use_is_counted():
    handle = _fight()
    assert _spent(handle) in (0, None)
    _use(handle, "Second+ Use")
    assert _spent(handle) == 1


def test_the_damage_grows_with_the_count():
    """(2 + spent - 1)d12 is 1d12 on the first counted use... but the counter
    is incremented before the roll, so a later use rolls more dice and can
    exceed 12 — which 1d12 alone never could."""
    totals = [sum(a for a, _ in _damage(_use(_fight(seed), "Second+ Use")))
              for seed in (1, 2, 3, 4, 5, 6)]
    assert all(t >= 1 for t in totals), totals
    assert max(totals) > 12, f"2d12 должен перекрывать 12: {totals}"
