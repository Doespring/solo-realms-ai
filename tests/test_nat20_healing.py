"""Требование 6: лечение, подъём с нуля хитов и снятие состояний.

SRD 5.2:
* Cure Wounds — 2d8 + модификатор лечения, не превышает максимум хитов;
* «Unconscious» снимается, когда существо восстанавливает хотя бы 1 хит;
* «When this condition ends, you remain Prone»;
* обычное лечение НЕ снимает состояния, которые от него не зависят.

Проверяется фактическое изменение хитов и состояния, а не только событие
`healing_applied`.

ВАЖНО про сценарий подъёма: лечим ДРУГОЕ существо. Если лечить себя с 0 хитов,
в начале хода успевает сработать спасбросок от смерти, и состояние снимается
ещё до заклинания — тест тогда проходит вхолостую (проверено мутацией).
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_engine.types.conditions import ActiveCondition
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=14,
    intelligence=17, wisdom=10, charisma=8,
)

HP_MAX = 40


def _cure(target_hp, condition=None, target_hp_max=20, heal_self=True):
    """Лечит цель; возвращает (исцелено, условия цели, итоговые хиты цели)."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="cleric", background_slug="acolyte",
        level=5, ability_scores=SCORES,
    )
    cleric = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Cleric", zone_id="0,0", initiative=20,
            hp_max=HP_MAX, hp_current=HP_MAX - 1 if heal_self else 20,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="heal",
            party=[cleric],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Wounded", entity_type="Monster",
                    initiative=1, hp_current=target_hp, hp_max=target_hp_max,
                    ac=10, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    live = O._get_live(handle)
    target_id = "pc1" if heal_self else "m1"
    live.tracked_hp[target_id] = target_hp
    target = next(c for c in live.initiative if c.entity_id == target_id)
    target.hp_current = target_hp
    if condition is not None:
        target.conditions = [
            ActiveCondition(
                condition=condition, source_entity_id="implied:dying", scope="combat",
            )
        ]
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(
                intent_type="cast_spell", spell_id="cure-wounds", target_id=target_id,
            ),
        )
    )
    events = O.drain_pending_events(handle)
    live = O._get_live(handle)
    target = next(c for c in live.initiative if c.entity_id == target_id)
    healed = [e.amount for e in events if e.type == "healing_applied"]
    return (
        healed[0] if healed else None,
        [c.condition for c in target.conditions],
        live.tracked_hp.get(target_id),
    )


def test_healing_restores_hit_points_between_the_expected_bounds():
    """2d8 + 3 (МУД 16) даёт от 5 до 19 хитов."""
    amount, _, _ = _cure(10, heal_self=False)
    assert amount is not None, "лечение не произошло"
    assert 5 <= amount <= 19, "лечение вне диапазона 2d8+3: " + str(amount)


def test_healing_cannot_exceed_the_hit_point_maximum():
    """Цель на 19 из 20: лечение не поднимает хиты выше 20."""
    amount, _, total = _cure(19, heal_self=False)
    assert amount is not None, "лечение не произошло"
    assert total == 20, "хиты после лечения " + str(total) + ", максимум 20"


def test_ordinary_healing_does_not_remove_unrelated_conditions():
    """Яд не снимается лечением: он не зависит от хитов."""
    _, conditions, _ = _cure(10, "poisoned", heal_self=False)
    assert "poisoned" in conditions, "лечение напрасно сняло отравление"


def test_recovering_from_zero_hit_points_ends_unconscious():
    """Подъём другого существа с 0 хитов: Unconscious снят, остался Prone."""
    amount, conditions, total = _cure(0, "unconscious", heal_self=False)
    assert amount is not None, "подъём с нуля не произошёл"
    assert total > 0, "хиты не стали положительными: " + str(total)
    assert "unconscious" not in conditions, (
        "Unconscious остался после получения хитов: " + str(conditions)
    )
    assert "prone" in conditions, (
        "поднятый с 0 хитов должен быть Prone: " + str(conditions)
    )

