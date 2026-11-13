"""Требование 6: временные хиты поглощаются первыми (§ «Temporary Hit Points»).

Правило: «If you have Temporary Hit Points and take damage, those points are lost
first, and the same amount of damage is then dealt to your Hit Points».

Проверяется боем, а не прямой функцией: `apply_damage` ТОЛЬКО вычисляет и
эмитит `DamageApplied` — хиты списывает оркестратор, обрабатывая событие
(`orchestrator.py:5214-5219`: `absorbed = min(temp, remaining)`).
Изолированный вызов с пустым эмиттером хиты не меняет — так и задумано.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=14,
    intelligence=17, wisdom=10, charisma=8,
)


def _fireball_with_temp(temp_hp):
    """Fireball (8d6, seed 5 даёт 40) по цели с заданными временными хитами."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=5, ability_scores=SCORES,
    )
    wizard = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Wizard", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="temp",
            party=[wizard],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=12, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    live = O._get_live(handle)
    live.tracked_temp_hp["m1"] = temp_hp
    for combatant in live.initiative:
        if combatant.entity_id == "m1":
            combatant.temp_hp = temp_hp
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="cast_spell", spell_id="fireball", target_id="m1"),
        )
    )
    events = O.drain_pending_events(handle)
    # Волшебник стоит в области своего огня: событий урона ДВА, первое — по нему.
    amounts = [
        e.amount
        for e in events
        if e.type == "damage_applied" and e.target_id == "m1"
    ]
    state = next(c for c in O._get_live(handle).initiative if c.entity_id == "m1")
    return amounts[0] if amounts else None, (state.temp_hp or 0), state.hp_current


def test_damage_without_temp_hits_the_hit_points():
    dealt, temp, hp = _fireball_with_temp(0)
    assert dealt is not None, "урон не нанесён"
    assert temp == 0
    assert hp == 100 - dealt, f"ушло в HP {100 - hp} при уроне {dealt}"


def test_temp_hits_absorb_before_the_hit_points():
    """40 временных хитов при уроне 40: HP не тронуты вовсе."""
    dealt, temp, hp = _fireball_with_temp(40)
    assert dealt is not None
    expected_temp = max(0, 40 - dealt)
    assert hp == 100, f"временные хиты не поглотили урон: HP {hp} вместо 100"
    assert temp == expected_temp, f"остаток временных хитов {temp}, ожидалось {expected_temp}"


def test_excess_temp_hits_are_a_shield():
    """200 временных хитов: урон их уменьшает, HP целы."""
    dealt, temp, hp = _fireball_with_temp(200)
    assert dealt is not None
    assert hp == 100
    assert temp == 200 - dealt

