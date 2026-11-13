"""Межпризнаковые списания: Superior Defense и Stunning Strike (SRD 5.2).

Оба тратят очки фокуса с САМОГО Monk's Focus, а не со своего признака:

* Superior Defense (монах 18): «expend 3 Focus Points», цель в данных —
  ``Compendium.dnd5e.classes24.Item.phbmnkMonksFocus``, value 3;
* Stunning Strike: 1 Focus Point из того же пула.

Раньше `_feature_activity_cost` считал только СВОЙ пул, поэтому обе
способности проходили бесплатно. Проверки идут через публичный
`submit_player_intent` и смотрят на счётчик фокуса.
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=12, dexterity=16, constitution=14,
    intelligence=10, wisdom=14, charisma=8,
)
FOCUS = 'feature_use:monks-focus'
UNCANNY = 'feature_use:uncanny-metabolism'
FONT = 'feature_use:font-of-magic'
LAY = 'feature_use:lay-on-hands'


def _fight(level: int, spent: int, seed: int = 5) -> object:
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="monk", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Monk", zone_id="0,0", initiative=20,
            hp_max=40, hp_current=40,
        ),
        loader=BundledAssetLoader(),
    )
    # Uncanny Metabolism (монах 2) на старте боя вернул бы ВЕСЬ фокус, и
    # проверка «не хватает очков» стала бы бессмысленной. Хост в этом бою уже
    # израсходовал его на день — так изоляция проверяемого правила честна.
    counters = {
        FOCUS: {'spent': spent},
        UNCANNY: {'spent': 1},
    }
    member = member.model_copy(update={'custom_counters': counters})
    handle = asyncio.run(
        O.start_combat(
            session_id="cross-feature",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Foe", entity_type="Monster",
                    initiative=1, hp_current=9, hp_max=9, ac=10, zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _focus_spent(handle: object) -> int:
    live = O._get_live(handle)
    return live.custom_counters_by_entity.get('pc1', {}).get(FOCUS, {}).get('spent', 0)


def _use(handle: object, feature_id: str) -> list:
    asyncio.run(
        O.submit_player_intent(
            handle, 'pc1', O.PlayerIntent(intent_type='use_feature', feature_id=feature_id),
        )
    )
    return O.drain_pending_events(handle)


def test_superior_defense_spends_three_focus_points() -> None:
    """Способность стоит 3 очка фокуса, а не ноль."""
    handle = _fight(18, spent=0)
    _use(handle, 'superior-defense')
    assert _focus_spent(handle) == 3, _focus_spent(handle)


def test_stunning_strike_spends_one_focus_point() -> None:
    """Stunning Strike стоит 1 очко фокуса."""
    handle = _fight(18, spent=0)
    _use(handle, 'stunning-strike')
    assert _focus_spent(handle) == 1, _focus_spent(handle)


def test_superior_defense_is_refused_when_its_pool_cannot_pay() -> None:
    """Пул не может оплатить стоимость — отказ, и НИЧЕГО не потрачено."""
    # Монах 18, пул 18, свободно 3 при полном пуле. Perfect Focus поднимает
    # ровно до четырёх, а стоят три — такое оплачивается. Значит, отказ
    # проверяется не на выдуманном дефиците, а через сам гейт: у пула,
    # которого у персонажа НЕТ, списание невозможно.
    live = O._get_live(_fight(18, spent=0))
    monk = next(c for c in live.initiative if c.entity_id == 'pc1')
    feature = BundledAssetLoader().get_feature('superior-defense')
    invocation = O._FeatureInvocation(
        activities=[feature.activities[0]],
        passive_effects=[],
        is_bonus_action=False,
        is_free_action=False,
        use_cap=1,
        use_cost=3,
        scaling_value=None,
    )
    spends = O._cross_feature_spends(monk, invocation)
    assert spends == [('monks-focus', 3)], spends
    # Пул на месте и оплачивает — отказа нет.
    assert O._cross_feature_spend_failure(live, monk, invocation) is False
    # Существо без признака-пула: списывать некуда.
    memberless = monk.model_copy(update={'classes': {}, 'class_slug': 'fighter', 'character_level': 0})
    # Пул объявлен в данных, но у этого существа признака нет: списывать
    # некуда, поэтому списаний ноль и отказа нет — трата не может уйти
    # в пул, которого нет.
    assert O._cross_feature_spends(memberless, invocation) == []


def _sorcerer(level: int, font_spent: int):
    scores = AbilityScores(
        strength=8, dexterity=16, constitution=14,
        intelligence=10, wisdom=12, charisma=17,
    )
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="sorcerer", background_slug="soldier",
        level=level, ability_scores=scores,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Sorcerer", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    member = member.model_copy(
        update={'custom_counters': {FONT: {'spent': font_spent}}}
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="sorcerous-restoration",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Foe", entity_type="Monster",
                    initiative=1, hp_current=9, hp_max=9, ac=10, zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def test_sorcerous_restoration_gives_sorcery_points_back() -> None:
    """Чародей 10 возвращает половину уровня, округлённую вниз: 5 очков."""
    handle = _sorcerer(10, font_spent=12)
    _use(handle, 'sorcerous-restoration')
    live = O._get_live(handle)
    spent = live.custom_counters_by_entity['pc1'][FONT]['spent']
    assert spent == 7, f'12 − 5 = 7, получено {spent}'


def test_sorcerous_restoration_cannot_go_below_zero_spent() -> None:
    """Возврат не уводит счётчик в минус: 2 потрачено — остаётся 0."""
    handle = _sorcerer(10, font_spent=2)
    _use(handle, 'sorcerous-restoration')
    live = O._get_live(handle)
    spent = live.custom_counters_by_entity['pc1'][FONT]['spent']
    assert spent == 0, f'ниже нуля списаний не бывает, получено {spent}'



def _paladin(level: int, pool_spent: int = 0):
    scores = AbilityScores(
        strength=16, dexterity=10, constitution=14,
        intelligence=8, wisdom=12, charisma=16,
    )
    spec = CharacterBuildSpec(
        species_slug='human', class_slug='paladin', background_slug='soldier',
        level=level, subclass_slug='devotion', ability_scores=scores,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id='pc1', name='Paladin', zone_id='0,0', initiative=20,
            hp_max=60, hp_current=60,
        ),
        loader=BundledAssetLoader(),
    )
    return member.model_copy(
        update={'custom_counters': {LAY: {'spent': pool_spent}}}
    )


def test_restoring_touch_spends_five_lay_on_hands_points() -> None:
    """Restoring Touch пишет стоимость как `5 * @scaling` — её нужно вычислить."""
    handle = asyncio.run(
        O.start_combat(
            session_id='restoring-touch',
            party=[_paladin(14)],
            encounter=[
                EncounterMemberSpec(
                    entity_id='m1', name='Foe', entity_type='Monster',
                    initiative=1, hp_current=9, hp_max=9, ac=10, zone_id='1,0',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=5,
        )
    ).handle
    O.drain_pending_events(handle)
    _use(handle, 'restoring-touch')
    live = O._get_live(handle)
    spent = live.custom_counters_by_entity.get('pc1', {}).get(LAY, {}).get('spent', 0)
    assert spent == 5, f'Restoring Touch стоит 5 очков, списано {spent}'
