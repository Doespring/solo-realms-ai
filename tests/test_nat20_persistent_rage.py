"""Persistent Rage (SRD 5.2, Barbarian 15): возврат применений при инициативе.

Правило: «When you roll Initiative, you can regain all expended uses of Rage.
After you regain uses of Rage in this way, you can't do so again until you
finish a Long Rest».

Проверки идут через публичный `start_combat`/`submit_player_intent` и смотрят
на состояние, а не на факт события: счётчик применений и следующий бой.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=18, dexterity=14, constitution=16,
    intelligence=8, wisdom=10, charisma=8,
)


def _fight(level: int, counters: dict) -> object:
    """Открыть бой за варвара с заданными счётчиками хоста; вернуть handle."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="barbarian", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    hero = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Barbarian", zone_id="0,0", initiative=20,
            hp_max=60, hp_current=60,
        ),
        loader=BundledAssetLoader(),
    )
    hero = hero.model_copy(update={'custom_counters': counters})
    handle = asyncio.run(
        O.start_combat(
            session_id="persistent-rage",
            party=[hero],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Foe", entity_type="Monster",
                    initiative=1, hp_current=99, hp_max=99, ac=10, zone_id='0,1',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=3,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _counters(handle: object, entity_id: str = 'pc1') -> dict:
    live = O._get_live(handle)
    return {
        k: v for k, v in live.custom_counters_by_entity.get(entity_id, {}).items()
    }


def test_initiative_regains_spent_rage_uses() -> None:
    """Все потраченные применения Ярости возвращаются при броске инициативы."""
    handle = _fight(15, {'feature_use:rage': {'spent': 4}})
    counters = _counters(handle)
    assert counters['feature_use:rage']['spent'] == 0
    assert counters['feature_use:persistent_rage_regained']['spent'] == 1
    activity = 'dnd5eactivity000'
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1',
            O.PlayerIntent(
                intent_type='use_feature', feature_id='rage',
                activity_id=activity,
            ),
        )
    )
    events = O.drain_pending_events(handle)
    assert any(type(e).__name__ == 'EffectApplied' for e in events), (
        f'возвращённое применение должно активировать Ярость, получено {events!r}'
    )


def test_the_regain_happens_once_per_long_rest() -> None:
    """Второе срабатывание в тот же день — no-op: флаг уже стоит."""
    handle = _fight(
        15,
        {
            'feature_use:rage': {'spent': 3},
            'feature_use:persistent_rage_regained': {'spent': 1},
        },
    )
    assert _counters(handle)['feature_use:rage']['spent'] == 3


def test_a_barbarian_without_the_feature_gains_nothing() -> None:
    """До 15 уровня правила нет: применениe остаётся потраченным."""
    handle = _fight(10, {'feature_use:rage': {'spent': 3}})
    counters = _counters(handle)
    assert counters['feature_use:rage']['spent'] == 3
    assert 'feature_use:persistent_rage_regained' not in counters


def test_a_long_rest_clears_the_once_per_rest_flag() -> None:
    """Флаг «уже возвращали» живёт под `feature_use:` — отдых его сбрасывает.

    Без этого правило срабатывало бы ровно один раз за всю жизнь
    персонажа: счётчик пережил бы любой отдых.
    """
    from dnd5e_engine.rest import recover_feature_uses

    counters = {
        'feature_use:rage': {'spent': 2},
        'feature_use:persistent_rage_regained': {'spent': 1},
    }
    class _Rule:
        """Правило восстановления флага: длительный отдых, полностью."""
        period = 'lr'
        type = 'recoverAll'
        formula = ''

    ruled = recover_feature_uses(
        counters, 'lr', {'persistent_rage_regained': [_Rule()]}
    )
    assert ruled['persistent_rage_regained'] == 0, (
        f'длительный отдых обязан снять флаг, получено {ruled!r}'
    )
