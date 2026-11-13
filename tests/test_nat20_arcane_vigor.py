"""SRD 5.2: Arcane Vigor тратит кости хитов и лечит на их бросок."""

from __future__ import annotations

import asyncio
import itertools

import pytest

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_engine.lib_loader import configure_lib_loader
from dnd5e_srd_data.loader import BundledAssetLoader

# Корпус ставится явно на каждую сборку боя: другие тесты набора временно
# подменяют загрузчик, и тест не должен от них зависеть (state leaks between files).
_LOADER = BundledAssetLoader()


@pytest.fixture(autouse=True)
def _bundled_corpus():
    configure_lib_loader(_LOADER)
    yield
    configure_lib_loader(None)
_SESSION = itertools.count()
_D6 = "UnCRwxuITUjxr7On"  # Arcane Vigor, "Expend d6 Hit Dice"


def _wizard_fight():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=5,
        ability_scores=AbilityScores(
            strength=10,
            dexterity=14,
            constitution=14,
            intelligence=16,
            wisdom=14,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Волшебник",
            zone_id="0,0",
            initiative=20,
            hp_max=32,
            hp_current=10,
        ),
        loader=_LOADER,
    )
    pc = pc.model_copy(
        update={"spells_known": tuple(set(getattr(pc, "spells_known", ()) or ()) | {"arcane-vigor"})}
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"vigor-{next(_SESSION)}",
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
                    zone_id="9,9",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _cast(handle, **kwargs):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(intent_type="cast_spell", spell_id="arcane-vigor", **kwargs),
        )
    )
    return O.drain_pending_events(handle)


def _wizard(handle):
    return next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")


def test_a_level_five_wizard_holds_five_d6():
    # Мультиклассирование: «Add together the Hit Dice granted by all your
    # classes». Один класс 5 уровня с d6 — пять костей.
    assert _wizard(_wizard_fight()).hit_dice_total == {6: 5}


def test_the_cast_rolls_and_expends_two_hit_dice():
    # «Roll one or two of your unexpended Hit Point Dice ... Those dice are
    # then expended.»
    handle = _wizard_fight()
    before = _wizard(handle)
    events = _cast(handle, activity_id=_D6, target_id="pc1")
    after = _wizard(handle)
    assert before.hit_dice_remaining == {6: 5}
    assert after.hit_dice_remaining == {6: 3}
    healing = [e for e in events if e.type == "healing_applied"]
    assert len(healing) == 1
    # 2d6 + модификатор Интеллекта (+3): бросок лежит в 5..15.
    assert 5 <= healing[0].amount <= 15, healing[0].amount
    assert after.hp_current == min(32, before.hp_current + healing[0].amount)


def test_the_pool_never_goes_negative():
    # Пул в три кости, а каст тратит две: граница — два каста, третий
    # обязан отказать, а не увести счётчик в минус (§9).
    handle = _wizard_fight()
    O._update_combatant(O._get_live(handle), "pc1", hit_dice_remaining={6: 3})
    _cast(handle, activity_id=_D6, target_id="pc1")
    assert _wizard(handle).hit_dice_remaining == {6: 1}
    # Новый ход: ``pass`` сам передаёт очередь болвану, и он возвращает её.
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="pass")))
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    O.drain_pending_events(handle)
    events = _cast(handle, activity_id=_D6, target_id="pc1")
    assert not [e for e in events if e.type == "healing_applied"]
    assert [e.reason for e in events if e.type == "cast_failed"] == ["no_uses_remaining"]
    assert _wizard(handle).hit_dice_remaining == {6: 1}



def test_the_spend_is_visible_in_the_combat_view_the_host_reads():
    # Трата обязана пережить бой: хост берёт ресурсы героя из
    # custom_counters_by_entity вида боя, а не из живого состояния.
    handle = _wizard_fight()
    _cast(handle, activity_id=_D6, target_id="pc1")
    view = O.get_live(handle)
    assert view.custom_counters_by_entity["pc1"]["hit_dice_spent"]["spent"] == 2
