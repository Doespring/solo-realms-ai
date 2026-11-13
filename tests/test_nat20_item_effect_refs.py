"""SRD 5.2 Net — the effect ref on the activity must reach the TARGET.

The bundled ``canonical/items/net.json`` refs an effect id its own
``passive_effects`` does not define (see
``activities/effects.py::_single_unnamed_effect_fallback``). Before the fix
the throw resolved its save and then emitted nothing at all: no
``effect_applied``, no ``condition_applied``, and the target was never
Restrained — a legal-looking list with a mandatory ability that did nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dnd5e_engine import orchestrator as O
from dnd5e_engine.activities import save as SAVE_ACTIVITY
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

#: The net's save activity (``canonical/items/net.json``).
NET_ACTIVITY_ID = "3u15g2fvhp6MyN5O"


def _throw_the_net(monkeypatch: Any) -> tuple[list[str], list[str], list[str]]:
    """Throw a net at m1 with the save FORCED to fail.

    Returns the emitted event types, m1's condition names and the names of
    the effects carried by m1 — the three readings a Restrained target must
    show.
    """
    # The SRD DC is 8 + Dex + PB; the roll itself is the harness's business,
    # so the DC is raised out of reach to make the failure deterministic.
    monkeypatch.setattr(SAVE_ACTIVITY, "_resolve_dc", lambda activity, ctx: 30)
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        ability_scores=AbilityScores(
            strength=16,
            dexterity=14,
            constitution=14,
            intelligence=10,
            wisdom=12,
            charisma=10,
        ),
    )
    hero = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0", initiative=20, hp_max=30, hp_current=30),
        loader=loader,
    )
    hero.equipment = [*list(getattr(hero, "equipment", []) or []), "net"]
    handle = asyncio.run(
        O.start_combat(
            session_id="net",
            party=[hero],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Target",
                    entity_type="Monster",
                    initiative=5,
                    hp_current=50,
                    hp_max=50,
                    ac=12,
                    zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=7,
        )
    ).handle
    O.drain_pending_events(handle)
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="use_item",
                item_id="net",
                activity_id=NET_ACTIVITY_ID,
                target_id="m1",
            ),
        )
    )
    events = O.drain_pending_events(handle)
    live = O._get_live(handle)
    target = next(c for c in live.initiative if c.entity_id == "m1")
    effects = [effect.name for effect in live.active_effects.get("m1", [])]
    return (
        [event.type for event in events],
        [condition.condition for condition in target.conditions],
        effects,
    )


def test_the_net_restrains_a_creature_that_fails_its_save(monkeypatch: Any) -> None:
    event_types, conditions, effects = _throw_the_net(monkeypatch)

    assert "save_rolled" in event_types
    assert "effect_applied" in event_types, event_types
    assert effects == ["Caught in Net"], effects
    assert "condition_applied" in event_types, event_types
    assert conditions == ["restrained"], conditions


def test_the_dangling_ref_fallback_stays_narrow(monkeypatch: Any) -> None:
    """Two effect definitions invite a guess, so the warning must stay.

    The fallback exists because the pairing is unambiguous (one ref, one
    definition). With a second definition it is ambiguous again and the
    engine must not silently pick one.
    """
    from dnd5e_engine.activities import effects as EFFECTS

    loader = BundledAssetLoader()
    item = loader.get_item("net")
    activity = item.activities[0]
    definition = item.passive_effects[0]
    assert (
        EFFECTS._single_unnamed_effect_fallback(
            activity,
            {definition.id: definition},
            ref_id=activity.effects[0].id,
        )
        is definition
    )
    twin = definition.model_copy(update={"id": "effect:twin", "name": "Twin"})
    assert (
        EFFECTS._single_unnamed_effect_fallback(
            activity,
            {definition.id: definition, twin.id: twin},
            ref_id=activity.effects[0].id,
        )
        is None
    )
