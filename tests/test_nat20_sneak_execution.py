"""Требование 7: Скрытая атака проверяется ИСПОЛНЕНИЕМ, а не данными.

Найдено при собственной проверке тестов на ложноположительность:
`test_nat20_scaling_mechanics.py` читает `build_scale_values` (данные корпуса),
а не исполняет обработчик. Мутация `sneak_attack_dice -> None` в
`activities/attack.py:645` оставляла 20 passed — тест этого не замечал.

Здесь удар идёт через настоящий бой, поэтому сломанный обработчик обязан
изменить результат расчёта. Порог взят из правила, а не из наблюдения:
рапира даёт 1d8+3 (4..11), Скрытая атака добавляет 1d6 (1..6), значит
удар с преимуществом наносит не меньше 5 и может превысить максимум рапиры
(11) — только если кость Скрытой атаки действительно брошена.
"""
from __future__ import annotations

import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.orchestrator import PlayerIntent
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange
from dnd5e_srd_data.loader import BundledAssetLoader

# Рапира: 1d8 + ЛОВ 3 → 4..11. Критическое попадание удваивает КОСТИ
# (SRD 5.2), значит потолок крита — 2d8+3 = 19, а не 11.
RAPIER_MAX = 11
RAPIER_CRIT_MAX = 19


def _scores(str_, dex, con):
    return AbilityScores(
        strength=str_, dexterity=dex, constitution=con,
        intelligence=10, wisdom=10, charisma=8,
    )


def _member(class_slug, background, scores, weapon):
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug=class_slug,
        background_slug=background,
        level=1,
        ability_scores=scores,
        equipment=(weapon,),
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Hero", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )


def _fight(member, seed):
    return asyncio.run(
        O.start_combat(
            session_id="sneak",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Target", entity_type="Monster", initiative=1,
                    hp_current=500, hp_max=500, ac=5, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=6, height=4),
            rng_seed=seed,
            # Законный повод для Скрытой атаки: атака с преимуществом
            # (SRD 5.2: "you have Advantage on the attack roll").
            active_effects=[
                ActiveEffect(
                    id="hidden",
                    name="Hidden",
                    origin="probe:hidden",
                    target_id="pc1",
                    changes=[
                        ActiveEffectChange(
                            key="flags.advantage.attack", mode="override", value=True
                        )
                    ],
                )
            ],
        )
    ).handle


def _damage(member, weapon, seed):
    """Урон одного удара и признак крита — из событий боя."""
    handle = _fight(member, seed)
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            PlayerIntent(intent_type="attack", target_id="m1", weapon_id=weapon),
        )
    )
    amount = None
    for event in O.drain_pending_events(handle):
        name = type(event).__name__
        if name == "AttackRolled":
            crit = event.is_crit
        elif name == "DamageApplied":
            amount = event.amount
    if amount is None:
        raise AssertionError("удар не дал события DamageApplied")
    return amount, crit


def test_sneak_attack_adds_dice_beyond_the_weapon_maximum():
    """Плут: удар обязан превысить максимум рапиры — иначе кости не брошены.

    Перебираем зёрна, потому что урон случаен; правило не зависит от броска,
    а только от того, добавлена ли кость. Без Скрытой атаки потолок — 11,
    и ни одно зерно его не превысит.
    """
    rogue = _member("rogue", "criminal", _scores(10, 16, 14), "rapier")
    hits = [_damage(rogue, "rapier", seed) for seed in range(12)]
    plain = [amount for amount, crit in hits if not crit]
    assert max(plain) > RAPIER_MAX, (
        f"Скрытая атака не добавлена: некритические удары {plain} ни разу не "
        f"превысили максимум рапиры {RAPIER_MAX} — обработчик не исполняется"
    )


def test_a_fighter_never_exceeds_the_weapon_maximum():
    """Отрицательный случай: у воина потолок урона — сама рапира."""
    fighter = _member("fighter", "soldier", _scores(16, 14, 14), "rapier")
    hits = [_damage(fighter, "rapier", seed) for seed in range(12)]
    plain = [amount for amount, crit in hits if not crit]
    assert max(plain) <= RAPIER_MAX, (
        f"воин без Скрытой атаки превысил максимум рапиры: {plain}"
    )
    crits = [amount for amount, crit in hits if crit]
    assert all(amount <= RAPIER_CRIT_MAX for amount in crits), crits

