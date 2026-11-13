"""Спасбросок концентрации от урона (PHB 2024, Раздел «Заклинания»).

Пункт 7: заклинание не считается обработанным потому, что загрузилось или
вернуло успешный статус. Здесь проверено фактическое изменение состояния:
после урона текущая концентрация либо снимается (провал CON-спасброска),
либо остаётся (успех), и в обоих случаях есть событие `concentration_check`.

Формула PHB 2024: DC = 10 или половина полученного урона, что больше, но не
выше 30. Проверяются оба края — нижний (малый урон) и потолок 30.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=10, dexterity=10, constitution=8, intelligence=16, wisdom=10,
    charisma=10,
)


def _concentrating_wizard(seed=1, hp_max=400):
    """Волшебник 5 уровня под bless, уже держащий концентрацию."""

    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=5, ability_scores=SCORES,
    )
    wizard = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Wizard", zone_id="0,0", initiative=20,
            hp_max=hp_max, hp_current=hp_max,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="conc", party=[wizard],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=60, hp_max=60, ac=13,
                    zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6), rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="cast_spell", spell_id="bless", target_id="pc1"),
        )
    )
    O.drain_pending_events(handle)
    return handle


def _hit(handle, amount):
    """Нанести урон и вернуть (DC, исход, концентрация после)."""
    live = O._get_live(handle)
    O._emit_apply_damage(
        live,
        O.DamageApplied(
            target_id="pc1", amount=amount, damage_type="slashing",
            is_overkill=False,
        ),
    )
    events = O.drain_pending_events(handle)
    live = O._get_live(handle)
    wizard = next(c for c in live.initiative if c.entity_id == "pc1")
    check = next(e for e in events if e.type == "concentration_check")
    return check.dc, check.succeeded, wizard.concentration_effect_id



@pytest.mark.parametrize(
    ("amount", "expected_dc"),
    [
        (1, 10),   # половина меньше 10 — действует нижний порог
        (20, 10),  # ровно половина нижнего порога
        (21, 10),  # 10.5 -> 10 в целых; всё ещё нижний порог
        (40, 20),  # половина урона
        (60, 30),  # ровно потолок
        (200, 30), # выше потолка не растет
    ],
)
def test_the_dc_is_half_the_damage_floored_at_ten_and_capped_at_thirty(amount, expected_dc):
    """DC = max(10, amount // 2), но не выше 30 (PHB 2024)."""
    handle = _concentrating_wizard()
    dc, _succeeded, _concentration = _hit(handle, amount)
    assert dc == expected_dc, f"урон {amount} дал DC {dc}, ожидался {expected_dc}"


def test_a_failed_save_drops_the_concentration_and_the_effect():
    """Провал снимает и концентрацию, и сам эффект, а не только флаг."""
    handle = _concentrating_wizard()
    live = O._get_live(handle)
    start = next(c for c in live.initiative if c.entity_id == "pc1")
    assert start.concentration_effect_id is not None, "bless не начал концентрацию"
    assert [e.name for e in live.active_effects.get("pc1", [])] == ["Blessed"]
    _dc, succeeded, after = _hit(handle, 40)
    live = O._get_live(handle)
    names = [e.name for e in live.active_effects.get("pc1", [])]
    if succeeded:
        assert after is not None, "успешный спасбросок снял концентрацию"
        assert names == ["Blessed"], "успешный спасбросок снял эффект: " + str(names)
    else:
        assert after is None, "провал спасброска оставил концентрацию"
        assert names == [], "провал спасброска оставил эффект: " + str(names)


def test_both_outcomes_actually_occur_across_seeds():
    """Тест не ложноположительный: встречаются и провал, и успех."""
    # Урон 10 даёт нижний порог DC 10: при CON -1 успех требует 11 на d20,
    # то есть оба исхода реально достижимы. DC 20 (урон 40) на восьми зёрнах
    # не преодолевается ни разу — проверка «оба исхода» там была бы
    # ложноположительной.
    outcomes = set()
    for seed in range(1, 13):
        dc, succeeded, _after = _hit(_concentrating_wizard(seed=seed), 10)
        assert dc == 10
        outcomes.add(succeeded)
    assert outcomes == {True, False}, (
        "на двенадцати зёрнах встретился только один исход: " + str(outcomes)
    )
