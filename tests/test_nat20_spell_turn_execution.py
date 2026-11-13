"""Требование 7: заклинание проверяется ИСПОЛНЕНИЕМ в бою, а не по данным.

Найдено собственной мутацией: `test_nat20_spell_execution.py` вызывает
`resolve_cast` напрямую и не проверяет путь хода. Мутация `resolve_cast -> return`
оставляла **9 passed**.

Здесь заклинание идёт через `submit_player_intent`, как настоящий ход: событие
`spell_cast`, спасбросок и фактическое изменение хитов обязаны произойти.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.orchestrator import PlayerIntent
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=14,
    intelligence=17, wisdom=10, charisma=8,
)


def _wizard(level=5):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=level, ability_scores=SCORES,
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Wizard", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )


def _fight(seed=5):
    return asyncio.run(
        O.start_combat(
            session_id="spell",
            party=[_wizard()],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Target", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=10, zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=seed,
        )
    ).handle


def _hp_of(handle, entity_id):
    return next(c.hp_current for c in O._get_live(handle).initiative if c.entity_id == entity_id)


def test_fireball_cast_through_a_turn_changes_the_target_hit_points():
    """Fireball: 8d6 огня, половина при успешном спасброске, дистанция 150 ft."""
    handle = _fight()
    before = _hp_of(handle, "m1")
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            PlayerIntent(intent_type="cast_spell", spell_id="fireball", target_id="m1"),
        )
    )
    events = O.drain_pending_events(handle)
    kinds = {e.type for e in events}
    assert "spell_cast" in kinds, "заклинание не было исполнено: " + str(sorted(kinds))
    assert "save_rolled" in kinds, "Fireball требует спасброска ЛОВ"
    after = _hp_of(handle, "m1")
    lost = before - after
    # 8d6 = 8..48; половина при успехе = 4..24.
    assert 4 <= lost <= 48, f"урон {lost} вне диапазона 8d6 (с половиной)"


def test_the_same_seed_reproduces_the_same_damage():
    """Случайность воспроизводима в пределах принятой архитектуры (п.9)."""
    def roll(seed):
        handle = _fight(seed)
        before = _hp_of(handle, "m1")
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1",
                PlayerIntent(intent_type="cast_spell", spell_id="fireball", target_id="m1"),
            )
        )
        O.drain_pending_events(handle)
        return before - _hp_of(handle, "m1")
    assert roll(11) == roll(11)


def test_an_unknown_spell_is_refused_rather_than_substituted():
    """Отсутствующее заклинание не заменяется похожим (п.2 и п.8).

    Движок отвергает запрос событием `cast_failed` с причиной `unknown_spell`
    и НЕ тратит ячейку. Раньше события не было вовсе: игрок видел
    `intent_submitted` и тишину — дефект, найденный этим тестом.
    """
    handle = _fight()
    before = _hp_of(handle, "m1")
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            PlayerIntent(intent_type="cast_spell", spell_id="wish-2024", target_id="m1"),
        )
    )
    events = O.drain_pending_events(handle)
    failed = [e for e in events if e.type == "cast_failed"]
    assert failed, "отказ не назван: " + str(sorted({e.type for e in events}))
    assert failed[0].reason == "unknown_spell"
    assert failed[0].spell_id == "wish-2024"
    # Отказ не должен ничего менять: ни хитов, ни ресурса.
    assert _hp_of(handle, "m1") == before
    assert "spell_cast" not in {e.type for e in events}

