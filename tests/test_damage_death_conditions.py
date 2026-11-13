"""r181 — урон, смерть и состояния: правила становятся числами, а не прозой.

Источник: PHB 2024 RU, глава 1 «Опускание Хитов до 0», «Спасброски от смерти»,
«Стабилизация персонажа», «Временные хиты», «Нокаутирование существа»
(печатные стр. 28–30; дословный разбор — `rules_data/v2/evidence/phb-008-033.txt`).

Урок постановки: пункт «сбит с ног даёт автопровал спасброска Ловкости» в
карточке был неверен. По книге Сбитый с ног меняет только броски атаки, а
автопровал спасбросков Силы и Ловкости дают Парализован, Ошеломлён, Окаменел
и Без сознания. Проверка ниже держит правило книги, а не текст карточки.
"""
import pytest

from solo import damage_conditions as dc
from solo.combat import Combatant, CombatEngine
from solo.contracts import Hero
from solo.geometry import Position, rectangular_map
from solo.rulesets import BUILTINS, RulesetEngine
from test_rulesets import Dice


def state(*, cursor=0, risk=False, enemy_position=(1,2,0), ally=False):
    hero = Combatant(id='hero', stats=Hero(name='Герой', weapon_proficiencies=['martial']),
                     position=Position(x=1, y=1), team='hero', weapon='longsword')
    foe = Combatant(id='foe', stats=Hero(name='Противник', hp=20, max_hp=20, ac=10),
                    position=Position(x=enemy_position[0], y=enemy_position[1], z=enemy_position[2]),
                    team='enemy', weapon='club')
    actors = [hero, foe]
    order = ['hero', 'foe']
    if ally:
        helper = Combatant(id='ally', stats=Hero(name='Спутник'), position=Position(x=2, y=1), team='ally')
        actors = [helper] + actors
        order = ['ally', 'hero', 'foe']
    return dict(hero=hero.stats.model_dump(), ruleset=BUILTINS['dnd2024'].model_dump(),
                combat=dict(active=True, map=rectangular_map(10, 10).model_dump(),
                            actors=[a.model_dump() for a in actors], order=order, cursor=cursor, round=1,
                            lethal_risk_declared=risk))


def test_zero_hp_starts_death_saves_at_the_start_of_the_turn():
    """PHB 2024, стр. 29: «Каждый раз, когда вы начинаете свой ход с 0 Хитов…»."""
    s = state(cursor=1); engine = CombatEngine(s, rng=Dice(14))  # ход противника
    hero = engine.actors['hero']; hero.stats.hp = 0
    operations = engine.end_turn('foe')
    saves = [op for op in operations if op and op.get('kind') == 'death_save']
    assert saves, operations
    assert saves[0]['actor'] == 'hero' and saves[0]['die'] == 14 and saves[0]['successes'] == 1


def test_three_failures_mean_death_and_ask_the_player():
    """Три провала — смерть; без объявленного риска исход ждёт решения игрока."""
    s = state(); engine = CombatEngine(s, rng=Dice(2, 3, 4))
    hero = engine.actors['hero']; hero.stats.hp = 0
    engine.death_save(hero); engine.death_save(hero); last = engine.death_save(hero)
    assert last['failures'] == 3 and last['pending_user_decision']
    assert engine.data['pending_death']['cause'] == 'death_saves' and not hero.dead


def test_natural_twenty_returns_one_hit_point_and_leaves_prone():
    s = state(); engine = CombatEngine(s, rng=Dice(20))
    hero = engine.actors['hero']; hero.stats.hp = 0; hero.stats.conditions = ['unconscious', 'prone']
    engine.death_save(hero)
    assert hero.stats.hp == 1 and 'unconscious' not in hero.stats.conditions
    assert 'prone' in hero.stats.conditions and hero.death_failures == 0


def test_damage_above_maximum_hit_points_is_instant_death():
    """PHB 2024, стр. 28: излишек урона не меньше максимума Хитов — смерть."""
    s = state(); engine = CombatEngine(s)
    outcome = engine.damage(engine.actors['hero'], 24, 'fire')
    assert outcome['hp_after'] == 0 and outcome['pending_user_decision']
    assert engine.data['pending_death']['cause'] == 'massive_damage'


def test_knockout_leaves_one_hit_point_and_unconsciousness():
    """PHB 2024, стр. 30: «Нокаутирование существа» — 1 Хит и Бессознательность."""
    s = state(); engine = CombatEngine(s)
    foe = engine.actors['foe']
    outcome = engine.damage(foe, 25, 'bludgeoning', nonlethal=True)
    assert outcome['knockout'] and foe.stats.hp == 1 and foe.knockout and not foe.dead
    assert 'unconscious' in foe.stats.conditions and not foe.stable


def test_first_aid_ends_knockout_unconsciousness():
    """Там же: первая помощь (Медицина Сл 10) снимает состояние досрочно."""
    s = state(ally=True, cursor=0); engine = CombatEngine(s, rng=Dice(15))
    foe = engine.actors['foe']; engine.damage(foe, 25, 'bludgeoning', nonlethal=True)
    engine.actors['ally'].position = foe.position.model_copy()
    result = engine.stabilize('ally', 'foe')
    assert result['first_aid'] and not foe.knockout and 'unconscious' not in foe.stats.conditions


def test_stabilization_stops_death_saves_and_keeps_unconscious():
    """PHB 2024, стр. 29: стабилизированный не бросает спасброски, но без сознания."""
    s = state(ally=True, cursor=0); engine = CombatEngine(s, rng=Dice(15, 3))
    hero = engine.actors['hero']; hero.stats.hp = 0; hero.stats.conditions = ['unconscious', 'prone']
    engine.actors['ally'].position = hero.position.model_copy()
    result = engine.stabilize('ally', 'hero')
    assert result['stabilized'] and hero.stable and 1 <= hero.stabilized_hours <= 4
    assert 'unconscious' in hero.stats.conditions
    assert engine.death_save(hero) is None, 'стабилизированный не бросает спасброски от смерти'


def test_damage_ends_stability_and_restarts_death_saves():
    """Там же: «Если существо получает урон, оно перестаёт быть Стабилизированным»."""
    s = state(); engine = CombatEngine(s)
    hero = engine.actors['hero']; hero.stats.hp = 0; hero.stable = True
    outcome = engine.damage(hero, 3, 'piercing')
    assert not hero.stable and hero.death_failures == 1 and outcome['hp_after'] == 0


def test_stabilized_creature_regains_one_hit_point_after_d1d4_hours():
    s = state(); engine = CombatEngine(s)
    hero = engine.actors['hero']; hero.stats.hp = 0; hero.stats.conditions = ['unconscious']
    hero.stable = True; hero.stabilized_hours = 2; hero.stabilized_at = 0
    s['elapsed_minutes'] = 121
    recovery = engine.stabilized_recovery(hero)
    assert recovery and hero.stats.hp == 1 and not hero.stable
    assert 'unconscious' not in hero.stats.conditions


def test_temporary_hit_points_do_not_stack_and_the_player_chooses():
    """PHB 2024, стр. 29: 10 + 12 даёт 12 или 10, но не 22."""
    s = state(); engine = CombatEngine(s)
    engine.grant_temp_hp('hero', 10)
    new = engine.grant_temp_hp('hero', 12, keep='new')
    assert new['value'] == 12 and new['stack_forbidden'] == 22 and new['discarded'] == 10
    old = engine.grant_temp_hp('hero', 12, keep='existing')
    assert old['value'] == 12, 'прежние 12 уже выбранных хитов остаются'


def test_temporary_hit_points_keep_the_old_ones_when_the_player_says_so():
    s = state(); engine = CombatEngine(s)
    engine.grant_temp_hp('hero', 10)
    kept = engine.grant_temp_hp('hero', 4, keep='existing')
    assert kept['value'] == 10 and kept['discarded'] == 4


def test_temporary_hit_points_are_spent_first_and_are_not_healing():
    s = state(); engine = CombatEngine(s)
    hero = engine.actors['hero']; hero.temp_hp = 5
    outcome = engine.damage(hero, 7, 'slashing')
    assert outcome['temp_hp_absorbed'] == 5 and hero.temp_hp == 0 and hero.stats.hp == 10
    unconscious = engine.actors['foe']; unconscious.stats.hp = 0
    unconscious.stats.conditions = ['unconscious', 'prone']
    engine.grant_temp_hp('foe', 8)
    assert unconscious.stats.hp == 0, 'Временные хиты не считаются лечением'
    assert 'unconscious' in unconscious.stats.conditions


def test_temporary_hit_points_end_at_a_long_rest():
    """PHB 2024, стр. 29: Временные хиты живут до исчерпания или Долгого отдыха."""
    hero = Hero(name='Герой', temp_hp=7)
    plain = dict(hero=hero.model_dump(), elapsed_minutes=0)
    result = RulesetEngine(BUILTINS['dnd2024']).rest(plain, 'long', rng=Dice())
    assert result['rest'] == 'long' and plain['hero']['temp_hp'] == 0


def test_prone_gives_disadvantage_on_attacks_and_does_not_auto_fail_dex_saves():
    """Правило книги: Сбитый с ног — про атаки, а не про спасброски Ловкости."""
    adjustments = dc.attack_adjustments(attacker=['prone'], target=[], distance_ft=5)
    assert 'нападающий сбит с ног' in adjustments['disadvantage']
    assert not dc.save_rule(['prone'], 'dexterity')['auto_fail']
    assert not dc.save_rule(['prone'], 'dexterity')['disadvantage']


def test_unconscious_auto_fails_strength_and_dexterity_saves():
    """Глоссарий: Парализован/Ошеломлён/Окаменел/Без сознания — автопровал Силы и Ловкости."""
    for condition in ('paralyzed', 'stunned', 'petrified', 'unconscious'):
        assert dc.save_rule([condition], 'dexterity')['auto_fail'], condition
        assert dc.save_rule([condition], 'strength')['auto_fail'], condition
        assert not dc.save_rule([condition], 'wisdom')['auto_fail'], condition


def test_engine_rolls_no_die_for_an_auto_failed_save():
    """Автопровал не тратит бросок: генератор остаётся нетронутым."""
    rng = Dice(); engine = RulesetEngine(BUILTINS['dnd2024'])
    result = engine.saving_throw(Hero(name='Герой', conditions=['unconscious']), 'dexterity', 12, rng=rng)
    assert result['auto_fail'] and not result['success'] and result['dice'] == [] and rng.calls == 0


def test_restrained_gives_dexterity_save_disadvantage():
    assert dc.save_rule(['restrained'], 'dexterity')['disadvantage']
    assert not dc.save_rule(['restrained'], 'dexterity')['auto_fail']


def test_prone_target_gives_advantage_within_five_feet_and_disadvantage_beyond():
    close = dc.attack_adjustments(attacker=[], target=['prone'], distance_ft=5)
    far = dc.attack_adjustments(attacker=[], target=['prone'], distance_ft=30)
    assert 'цель сбита с ног' in close['advantage']
    assert 'цель сбита с ног вдалеке' in far['disadvantage']


def test_every_condition_from_the_card_is_in_the_table_with_numbers():
    card = ('blinded', 'deafened', 'frightened', 'grappled', 'incapacitated', 'poisoned',
            'prone', 'paralyzed', 'stunned', 'unconscious', 'restrained')
    for condition in card:
        assert condition in dc.CONDITIONS, condition
        effect = dc.CONDITIONS[condition]
        assert effect.name and effect.name != condition, 'у состояния нет русского имени'
    assert dc.CONDITIONS['grappled'].speed_zero and dc.CONDITIONS['incapacitated'].blocks_actions
    assert dc.CONDITIONS['paralyzed'].auto_crit_within_5ft


def test_unknown_condition_is_refused_instead_of_silently_ignored():
    s = state(); engine = CombatEngine(s)
    with pytest.raises(ValueError, match='Неизвестное состояние'):
        engine.apply_condition('hero', 'sобранность')


def test_condition_duration_expires_at_the_end_of_the_bearers_turn():
    s = state(cursor=1); engine = CombatEngine(s)
    foe = engine.actors['foe']
    engine.apply_condition('foe', 'stunned', rounds=1)
    operations = engine.end_turn('foe')
    assert any(op and op.get('kind') == 'condition_end' for op in operations), operations
    assert 'stunned' not in foe.stats.conditions and not foe.condition_rounds


def test_bloodied_at_half_hit_points_and_below():
    s = state(); engine = CombatEngine(s)
    hero = engine.actors['hero']
    outcome = engine.damage(hero, 6, 'slashing')
    assert outcome['hp_after'] == 6 and outcome['bloodied'] is True
    outcome = engine.damage(hero, 1, 'slashing')
    assert outcome['hp_after'] == 5 and outcome['bloodied'] is True
    full = CombatEngine(state())
    assert full.damage(full.actors['hero'], 1, 'slashing')['bloodied'] is False
