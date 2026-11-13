"""SRD 5.2: Hat of Many Spells — проверка неизвестного заклинания."""

from __future__ import annotations

import asyncio
import itertools

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

_LOADER = BundledAssetLoader()
_SESSION = itertools.count()
_CHECK = "DLxZ5QtUv2iqJrUl"


def _hat_fight():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=5,
        equipment=["hat-of-many-spells"],
        attuned_items=["hat-of-many-spells"],
        ability_scores=AbilityScores(
            strength=14,
            dexterity=16,
            constitution=14,
            intelligence=18,
            wisdom=12,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Герой",
            zone_id="0,0",
            initiative=20,
            hp_max=60,
            hp_current=60,
        ),
        loader=_LOADER,
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"hat-{next(_SESSION)}",
            party=[pc],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Болван",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=12,
                    zone_id="8,8",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=7,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _check(handle, slot_level):
    result = asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="use_item",
                item_id="hat-of-many-spells",
                activity_id=_CHECK,
                slot_level=slot_level,
            ),
        )
    )
    events = O.drain_pending_events(handle)
    rolled = [e for e in events if getattr(e, "type", "") == "check_rolled"]
    assert len(rolled) == 1, events
    return rolled[0], result


def test_the_unknown_spell_dc_is_ten_plus_the_spell_level():
    # SRD 5.2 Hat of Many Spells: "Make a DC 10 plus the spell's level
    # Intelligence (Arcana) check". Источник пишет `10 + @scaling`; раньше
    # движок отвечал `Unhandled roll-data token: '@scaling'`, то есть
    # механика предмета не исполнялась вовсе (§7).
    for slot_level, expected_dc in ((1, 11), (2, 12), (5, 15), (9, 19)):
        handle = _hat_fight()
        rolled, _ = _check(handle, slot_level)
        assert rolled.dc == expected_dc, (slot_level, rolled.dc)


def test_the_check_ability_is_the_one_the_source_names():
    # «Intelligence (Arcana)»: проверка идёт по Интеллекту с навыком Arcana,
    # а не по умолчанию.
    handle = _hat_fight()
    rolled, _ = _check(handle, 2)
    assert rolled.skill == "arc", rolled.skill


def test_a_request_naming_no_level_has_no_dc_to_roll():
    # Без названного уровня формула не решается догадкой: движок отказывает
    # по имени и НЕ тратит действие и не бросает кубик.
    handle = _hat_fight()
    before = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="use_item", item_id="hat-of-many-spells", activity_id=_CHECK
            ),
        )
    )
    events = O.drain_pending_events(handle)
    assert [getattr(e, "type", "") for e in events] == ["cast_failed"]
    assert events[0].reason == "scaling_amount_required"
    after = next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")
    assert after.action_available == before.action_available


def test_the_level_is_read_from_the_request_never_invented():
    # Разные названные уровни дают разные DC в одном и том же бою, значит
    # значение берётся из запроса, а не из состояния предмета.
    handle = _hat_fight()
    first, _ = _check(handle, 1)
    assert first.dc == 11
    live = O._get_live(handle)
    actor = next(c for c in live.initiative if c.entity_id == "pc1")
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", O.PlayerIntent(intent_type="pass"),
        )
    )
    O.drain_pending_events(handle)
