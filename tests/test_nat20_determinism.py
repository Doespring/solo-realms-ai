"""Требование 9: воспроизводимость боя управляемой случайностью.

Критерий 14.8 требует определённой совместимости сохранений, а пункт 9
описывает, что «случайность должна контролироваться и воспроизводиться».
Бой восстанавливается не сериализацией `_LiveCombat`, а реплеем: начальные
условия плюс последовательность команд воспроизводятся с тем же зерном.
Поэтому проверяется главное свойство, на котором реплей держится:

* одинаковое зерно + одинаковая последовательность -> одинаковый результат;
* разные зёрна -> результат разный (иначе зерно ни на что не влияет и
  реплей «сходится» просто потому, что всё детерминировано без бросков).

Второе условие — защита от ложноположительного теста: если движок
перестанет бросать кости, первого условия одного мало для вывода.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=10,
)


def _fight(seed):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=5, ability_scores=SCORES,
    )
    fighter = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Fighter", zone_id="0,0", initiative=20,
            hp_max=44, hp_current=44,
        ),
        loader=BundledAssetLoader(),
    )
    return asyncio.run(
        O.start_combat(
            session_id="determinism", party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=200, hp_max=200, ac=12,
                    zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6), rng_seed=seed,
        )
    ).handle


def _attack_twice(handle):
    """Две атаки подряд; возвращает урон, снятый с цели."""
    O.drain_pending_events(handle)
    for _ in range(2):
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1",
                O.PlayerIntent(
                    intent_type="attack", target_id="m1", weapon_id="longsword",
                ),
            )
        )
        O.drain_pending_events(handle)
    live = O._get_live(handle)
    dummy = next(c for c in live.initiative if c.entity_id == "m1")
    return 200 - dummy.hp_current


def test_the_same_seed_and_script_replay_to_the_same_damage():
    """Реплей опирается на это: одно зерно — один и тот же бой."""
    first = _attack_twice(_fight(7))
    second = _attack_twice(_fight(7))
    assert first == second, (
        "одинаковое зерно дало разный урон: " + str((first, second))
    )
    assert first > 0, "бой не нанёс урона — проверять нечего: " + str(first)


def test_a_different_seed_produces_a_different_fight():
    """Защита от ложноположительности: зерно обязано влиять на бросок.

    Без этой проверки первая проходила бы и на движке, который вообще
    перестал бросать кости: реплей тогда «сходится» тривиально.
    """
    damages = {seed: _attack_twice(_fight(seed)) for seed in range(1, 9)}
    distinct = set(damages.values());
    assert len(distinct) > 1, (
        "все зёрна дали одинаковый урон, зерно не влияет на бросок: "
        + str(damages)
    )


def test_the_same_seed_replays_the_same_events():
    """События, а не только итог, должны совпадать: реплей сверяет их дайджест."""
    def types_for(seed):
        handle = _fight(seed)
        O.drain_pending_events(handle)
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1",
                O.PlayerIntent(
                    intent_type="attack", target_id="m1", weapon_id="longsword",
                ),
            )
        )
        return [e.type for e in O.drain_pending_events(handle)]

    assert types_for(5) == types_for(5), "одинаковое зерно дало разный поток событий"
