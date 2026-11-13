"""A resolved check reaches the player as numbers, and only one module says how.

The engine already returns the roll (``total``/``dc``/``success``; the Nat20
worker returns ``roll_total``/``natural_roll``). Three host paths used to throw
those numbers away and print "Действие удалось." instead, each with its own copy
of the phrase. These tests pin the one formatter, the numbers it is allowed to
use, and the guard that keeps a second copy from growing back.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solo.check_text import NO_RECORD, check_text  # noqa: E402
from solo.command_engine import CommandEngine  # noqa: E402
from solo.contracts import Hero, Step  # noqa: E402
from solo.narrative_grounding import fallback, sources  # noqa: E402
from solo.rulesets import SKILL_NAMES, RulesetEngine, active  # noqa: E402
from scripts.command_smoke import FixedDice, setup  # noqa: E402


def roll(tmp_path, values, **step):
    """A real RulesetEngine check from the smoke scenario, not a hand-made dict."""
    store, campaign, _ = setup(tmp_path)
    state = store.campaign(campaign)['state']
    body = dict(kind='check', text='следы', ability='wisdom', skill='survival', dc=12)
    body.update(step)
    return RulesetEngine(active(state)).check(Hero.model_validate(state['hero']), Step(**body),
                                              FixedDice(values))


def test_success_and_failure_read_differently_and_name_the_numbers(tmp_path):
    ok = check_text(roll(tmp_path / 'ok', (13,)))
    bad = check_text(roll(tmp_path / 'bad', (8,)))
    assert ok == 'Выживание: 13 против Сл 12 — успех'
    assert bad == 'Выживание: 8 против Сл 12 — неудача'
    for line in (ok, bad):
        assert '13' in line or '8' in line
        assert 'Сл 12' in line


def test_numbers_are_copied_from_the_result_not_recomputed(tmp_path):
    result = roll(tmp_path, (13,))
    line = check_text(result)
    assert str(result['total']) in line and str(result['dc']) in line
    # The engine's own arithmetic stays visible even when it looks wrong: the
    # formatter must not quietly repair a total the player could check by hand.
    assert check_text({**result, 'total': 99}) == 'Выживание: 99 против Сл 12 — успех'
    assert check_text({**result, 'dc': 3, 'success': False}) == 'Выживание: 13 против Сл 3 — неудача'


def test_russian_skill_name_comes_from_the_table_not_the_slug(tmp_path):
    line = check_text(roll(tmp_path, (13,)))
    assert line.startswith(SKILL_NAMES['survival'] + ':')
    assert 'survival' not in line
    assert 'wisdom' not in line


def test_ability_and_saving_throw_are_named_in_russian(tmp_path):
    store, campaign, _ = setup(tmp_path)
    state = store.campaign(campaign)['state']
    hero = Hero.model_validate(state['hero'])
    engine = RulesetEngine(active(state))
    ability = check_text(engine.check(hero, Step(kind='check', text='x', ability='strength', dc=15),
                                      FixedDice((11,))))
    save = check_text(engine.saving_throw(hero, 'wisdom', 15, rng=FixedDice((9,))))
    assert ability == 'Сила: 21 против Сл 15 — успех'
    assert save == 'Спасбросок Мудрости: 9 против Сл 15 — неудача'


def test_advantage_disadvantage_and_natural_dice_are_named(tmp_path):
    store, campaign, _ = setup(tmp_path)
    state = store.campaign(campaign)['state']
    hero = Hero.model_validate(state['hero'])
    engine = RulesetEngine(active(state))
    advantage = check_text(engine.check(hero, Step(kind='check', text='x', ability='wisdom',
                                                   skill='survival', dc=12, advantage=True),
                                        FixedDice((4, 17))))
    natural = check_text(engine.check(hero, Step(kind='check', text='x', ability='wisdom',
                                                 skill='survival', dc=12), FixedDice((20,))))
    # A discarded 20 under disadvantage is not a natural 20.
    dropped = check_text(engine.check(hero, Step(kind='check', text='x', ability='dexterity',
                                                 skill='stealth', dc=15, disadvantage=True),
                                      FixedDice((20, 5))))
    assert advantage == 'Выживание: 17 против Сл 12 — успех (преимущество)'
    assert natural == 'Выживание: 20 против Сл 12 — успех (натуральная 20)'
    assert dropped == 'Скрытность: 7 против Сл 15 — неудача (помеха)'


def test_nat20_result_shape_is_formatted_by_the_same_function():
    native = dict(kind='skill', skill='athletics', ability=None, roll_total=18, modifier=5,
                  dc=15, success=True, natural_roll=13, is_proficient=True, effect_breakdown=[])
    assert check_text(native) == 'Атлетика: 18 против Сл 15 — успех'
    save = dict(kind='saving_throw', skill='', ability='WIS', roll_total=15, modifier=0,
                dc=15, success=True, natural_roll=15)
    assert check_text(save) == 'Спасбросок Мудрости: 15 против Сл 15 — успех'


def test_a_record_without_numbers_keeps_the_phrase_but_never_invents_one():
    assert check_text(dict(success=True)) == 'Действие удалось.'
    assert check_text(dict(success=False)) == 'Попытка не удалась.'
    # No verdict either: a partial record is not guessed at.
    assert check_text(dict(total=17, dc=15)) == NO_RECORD
    assert check_text(None) == NO_RECORD


def test_old_outcome_word_reaches_the_player_through_the_same_function():
    assert sources(dict(outcomes=[dict(outcome='успех')]))['description_sources'] == ['Действие удалось.']
    assert sources(dict(outcomes=[dict(outcome='неудача')]))['description_sources'] == ['Попытка не удалась.']


def test_a_numeric_line_passes_grounding_untouched():
    line = 'Выживание: 13 против Сл 12 — успех'
    assert sources(dict(outcomes=[dict(outcome=line)]))['description_sources'] == [line]


def test_command_engine_answers_a_check_with_the_roll(tmp_path):
    """The stored, player-visible outcome carries the numbers, not a verdict."""
    store, campaign, _ = setup(tmp_path)
    answer = CommandEngine(store, rng=FixedDice((13,))).execute(
        campaign, dict(request_id='check-numbers', expected_version=0,
                       action=dict(kind='check', check_id='следы')))
    outcome = answer['journal']['results'][0]['game_event']['payload']['outcome']
    assert outcome == 'Выживание: 13 против Сл 12 — успех'
    # ...and the presentation path repeats it instead of falling back to a phrase.
    assert fallback(dict(outcomes=[dict(outcome=outcome)]))['description'] == outcome


def test_no_second_copy_of_the_placeholder_on_the_main_paths():
    """The guard: one phrase, one module. A fresh copy turns this red."""
    main_path = (ROOT / 'solo' / 'check_text.py').read_text(encoding='utf-8')
    for phrase in ('Действие удалось.', 'Попытка не удалась.'):
        assert phrase in main_path, 'the phrase must stay defined in the one formatter'
    for path in ('solo/command_engine.py', 'solo/narrative_grounding.py', 'web/play.js'):
        source = (ROOT / path).read_text(encoding='utf-8')
        for phrase in ('Действие удалось.', 'Попытка не удалась.', 'Попытка удалась.'):
            assert phrase not in source, path + ' writes its own check verdict again'
    client = (ROOT / 'web' / 'play.js').read_text(encoding='utf-8')
    assert "r.type==='check'?(r.formula||r.text" in client, \
        'the client must show the server line or the engine formula, never a verdict of its own'


def test_the_plan_path_packet_carries_the_roll_too(tmp_path):
    """The free-text plan path kept the numbers in `result` and sent 'успех'."""
    import json as _json
    from solo.contracts import Plan
    from solo.game import Game
    from solo.llm import CommandMockLLM
    from solo.storage import Store
    store = Store(tmp_path / 'plan.sqlite3')
    campaign = store.create('Новая история', Hero(name='Мирон'))['id']
    plan = Plan(summary='Осмотр', steps=[Step(kind='check', text='Осматриваю следы',
                                              ability='wisdom', dc=12)])
    action = store.stage(campaign, 'one', 'Осмотр', plan,
                         store.campaign(campaign)['version'], {'llm': []})
    event = store.apply(action['id'], FixedDice((13,)))
    _, packet, _ = Game(store, CommandMockLLM({})).compiler.narrator(store.campaign(campaign), event)
    outcomes = _json.loads(packet)['outcomes']
    assert len(outcomes) == 1 and outcomes[0]['action'] == 'Осматриваю следы'
    # The packet carries the formatter's line, not the bare word the narrator
    # used to receive (and not the hero's own words either).
    assert outcomes[0]['outcome'] == check_text(event['results'][0])
    assert 'против Сл 12' in outcomes[0]['outcome']
    assert outcomes[0]['outcome'] != 'успех'


def test_native_check_outcome_is_the_roll_not_engine_speak(tmp_path):
    """The Nat20 path used to publish 'Nat20 confirmed: check' to the screen."""
    from solo.nat20_api import GameCommand, Nat20GameEngine
    from solo.storage import Store
    api = Nat20GameEngine(Store(tmp_path / 'nat20.sqlite3'))
    campaign = api.create_campaign('r133', seed=5, hp_current=7, second_wind_spent=2)['id']
    answer = api.execute(campaign, GameCommand(
        request_id='check', expected_version=0, action=dict(kind='check', check_id='athletics')))
    native = answer['result']
    outcome = answer['event'].payload['outcome']
    assert outcome == check_text(native)
    assert outcome.startswith('Атлетика: ') and str(native['roll_total']) in outcome
    assert 'Сл %d' % native['dc'] in outcome
    assert 'Nat20 confirmed' not in outcome
    assert ('натуральная 20' in outcome) == (native['natural_roll'] == 20)


def test_visible_line_survives_the_player_text_filter():
    from solo.game import player_text
    line = 'Выживание: 13 против Сл 12 — успех'
    assert player_text(line) == line
