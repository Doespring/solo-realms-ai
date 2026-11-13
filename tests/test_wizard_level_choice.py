"""Уровень героя — параметр сборки, а не подпись на листе (L01).

Редактор обязан собрать героя на уровне кампании целиком: подкласс, повышения
характеристик на 4/8/12, заклинания всех доступных уровней и ячейки. Лист после
создания обязан быть листом этого уровня. Ниже — короткие свои фрагменты, а не
книжный текст.
"""
import pytest

from solo.nat20_api import Nat20GameEngine
from solo.nat20_wizard import contract, fit_level_picks, suggested_draft
from solo.playable_ui import native_character
from solo.prototype_prepared import prepared_start_readiness
from solo.storage import Store

FIGHTER_SCORES = dict(strength=15, dexterity=14, constitution=13,
                      intelligence=12, wisdom=10, charisma=8)
WIZARD_SCORES = dict(strength=8, dexterity=14, constitution=13,
                     intelligence=15, wisdom=12, charisma=10)


def recommend(klass, level, scores):
    return suggested_draft(class_slug=klass, species_slug='human',
                           background_slug='soldier', level=level,
                           ability_scores=scores)


def test_recommendation_builds_a_complete_hero_at_level_eight():
    draft, state = recommend('fighter', 8, FIGHTER_SCORES)
    assert state['ready'], state['issues']
    assert draft.subclass_slug == 'champion', 'подкласс выбран, а не оставлен шагом'
    keys = [step.get('choice_key') or '' for step in state['steps']]
    assert any(key.startswith('class:fighter:asi:4') for key in keys), keys
    assert any(key.startswith('class:fighter:asi:8') for key in keys), keys
    assert state['sheet']['proficiency_bonus'] == 3
    # Повтор одной и той же особенности не отвечает второму шагу: токены — набор.
    assert len(set(draft.selected_choices)) == len(draft.selected_choices)


def test_second_copy_of_a_pick_is_a_different_option():
    # У чемпиона второй стиль боя и у колдуна воззвания: одинаковый slug не
    # закрывает второй шаг, поэтому предложение обязано брать свободный вариант.
    for klass, level, scores in (('fighter', 8, FIGHTER_SCORES),
                                 ('warlock', 8, WIZARD_SCORES)):
        draft, state = recommend(klass, level, scores)
        assert state['ready'], (klass, state['issues'])


def test_caster_at_level_eight_offers_spells_of_every_open_level():
    draft, state = recommend('wizard', 8, WIZARD_SCORES)
    assert state['ready'], state['issues']
    assert draft.subclass_slug, 'подкласс волшебника выбирается с 3-го уровня'
    slots = state['sheet']['spell_slots']
    assert set(slots) >= {'1', '2', '3', '4'}, slots
    magic = next(step for step in state['steps'] if step['id'] == 'magic')
    assert int(magic.get('missing') or 0) == 0, magic.get('reasons')
    assert draft.prepared_spells, 'заклинания всех уровней предложены, а не только 1-го'


def test_creating_a_level_eight_hero_stores_a_level_eight_sheet(tmp_path):
    _draft, state = recommend('fighter', 8, FIGHTER_SCORES)
    api = Nat20GameEngine(Store(tmp_path / 'level-eight.sqlite3'))
    cid = api.create_experimental_campaign('Храбрец', build=state['build'],
                                           prepared_spells=[], seed=5,
                                           encounters={})['id']
    current = api.store.campaign(cid)
    assert current['state']['hero']['level'] == 8
    assert current['state']['nat20']['hero']['subclass_slug'] == 'champion'
    sheet = native_character(current)
    assert sheet['identity']['level'] == 8
    assert sheet['sheet']['proficiency_bonus'] == 3, '+3 — бонус мастерства 8-го'
    assert sheet['sheet']['hp_max'] > 30, 'хиты считаются на 8-м, а не на 1-м'


def test_a_first_level_hero_on_the_same_book_stays_first_level(tmp_path):
    _draft, state = recommend('fighter', 1, FIGHTER_SCORES)
    api = Nat20GameEngine(Store(tmp_path / 'level-one.sqlite3'))
    cid = api.create_experimental_campaign('Новобранец', build=state['build'],
                                           prepared_spells=[], seed=5,
                                           encounters={})['id']
    current = api.store.campaign(cid)
    assert current['state']['hero']['level'] == 1
    assert not current['state']['nat20']['hero'].get('subclass_slug')
    sheet = native_character(current)
    assert sheet['identity']['level'] == 1
    assert sheet['sheet']['proficiency_bonus'] == 2


def test_lowering_the_level_drops_the_picks_that_level_cannot_take():
    draft, state = recommend('fighter', 8, FIGHTER_SCORES)
    assert state['ready']
    lower = draft.model_copy(update={'level': 4})
    fitted, answers, dropped, problems = fit_level_picks(lower, draft.choices)
    assert not problems, problems
    assert fitted.level == 4
    titles = ' '.join(str(row.get('title') or row.get('key')) for row in dropped)
    assert dropped, 'снятые выборы названы, а не пропали молча'
    state = contract(fitted)
    assert state['sheet_ready'], state['sheet_issues']
    assert all('asi:8' not in key for key in answers), answers
    assert state['sheet']['proficiency_bonus'] == 2


class _Pipeline:
    """Только то, что читает `prepared_start_readiness`, без книги и модели."""

    def __init__(self, definition):
        self.definition = definition

    def latest_run(self, draft_id):
        return dict(done=12, total=12)

    def world_result(self, draft_id):
        return None, 'run-1', dict(definition=self.definition, quarantine=[],
                                   full_ready=True, ready=True)


class _Preparation:
    def __init__(self, body, definition):
        self.body = body
        self.preparation_pipeline = _Pipeline(definition)

    def get(self, draft_id):
        return dict(id=draft_id, revision=7, body=self.body)


def test_the_book_names_the_level_it_is_written_for():
    definition = dict(ruleset='dnd2024', source_ruleset='dnd2024',
                      minimum_level=8, maximum_level=8, recommended_party=4)
    body = dict(name='Тиамат', world_confirmation=dict(run_id='run-1'))
    readiness = prepared_start_readiness(_Preparation(body, definition), 'draft-1')
    assert readiness['book'] == 'Тиамат', 'откуда требование — тоже факт книги'
    assert readiness['minimum_level'] == 8 and readiness['maximum_level'] == 8
    assert readiness['hero_level'] == 8
    assert readiness['ready'], readiness['reasons']


def test_a_book_that_states_a_range_opens_at_its_lowest_level():
    definition = dict(ruleset='dnd2024', minimum_level=5, maximum_level=8,
                      recommended_party=4)
    body = dict(name='Своя книга', world_confirmation=dict(run_id='run-1'))
    readiness = prepared_start_readiness(_Preparation(body, definition), 'draft-2')
    assert (readiness['minimum_level'], readiness['maximum_level']) == (5, 8)
    assert readiness['hero_level'] == 5


def test_the_screen_may_lower_the_level_and_a_direct_caller_may_not(tmp_path, monkeypatch):
    """Понижение уровня — заявление экрана о своей истории, а не лазейка.

    ``from_level`` говорит: «было 8, стало 4, сними то, чего 4-й не берёт»,
    и снятое попадает в ``dropped``. Без него тот же черновик идёт прежним
    путём: ответы пересобираются обычным ``resolve_answers``, а отчёт о снятом
    не появляется. Гейт правил не ослаблен — подкласс ниже своего порога
    по-прежнему отказ (`test_prototype_wizard_api.py::test_an_illegal_subclass_is_refused`).
    """
    from fastapi.testclient import TestClient

    from solo.app import create_app
    from solo.preview_mock import PreviewMock

    monkeypatch.setattr('solo.nat20_api.secrets.randbits', lambda bits: 5)
    app = create_app(tmp_path / 'lower.sqlite3', PreviewMock(),
                     experimental=True, offline_mock=True)
    draft, state = recommend('fighter', 8, FIGHTER_SCORES)
    assert state['ready']
    body = dict(draft=draft.model_dump(mode='json'), answers=draft.choices)
    with TestClient(app) as api:
        high = api.post('/api/prototype/wizard/contract', json=body).json()
        assert high['ready'], high['issues']
        lowered = dict(body, from_level=8, draft=dict(body['draft'], level=4))
        low = api.post('/api/prototype/wizard/contract', json=lowered).json()
        assert low['dropped'], 'снятые выборы названы'
        assert low['ready'], low['issues']
        assert low['sheet']['proficiency_bonus'] == 2
        quiet = api.post('/api/prototype/wizard/contract',
                         json=dict(body, draft=dict(body['draft'], level=4))).json()
        assert quiet['dropped'] == [], 'без заявления о понижении отчёта нет'
        assert all('asi:8' not in key for key in quiet['draft']['selected_choices']), \
            quiet['draft']['selected_choices']


def test_lowering_after_a_suggestion_drops_its_picks():
    """«Рекомендовать» заполняет черновик, а не лист ответов.

    Экран после предложения держит ``answers={}``: все выборы лежат в
    ``draft.choices``. Если читать только ответы, понижение уровня не снимает
    ничего, движок отказывает, и лист пропадает из-за пустого запроса.
    """
    draft, state = recommend('fighter', 8, FIGHTER_SCORES)
    assert state['ready'] and draft.choices, 'предложение закрывает шаги'
    fitted, answers, dropped, problems = fit_level_picks(
        draft.model_copy(update={'level': 4}), {})
    assert not problems, problems
    assert answers, 'выборы взяты из черновика, раз экран ответов пуст'
    assert dropped, 'снятые выборы названы'
    assert all('asi:6' not in key and 'asi:8' not in key
               for key in fitted.selected_choices), fitted.selected_choices
    fitted_state = contract(fitted)
    assert fitted_state['sheet_ready'], fitted_state['issues']
    assert fitted_state['sheet']['proficiency_bonus'] == 2


def test_lowering_a_caster_drops_the_spells_the_new_slots_cannot_pay():
    """Заклинание — такой же выбор уровня, как повышение характеристик."""
    draft, state = recommend('wizard', 8, WIZARD_SCORES)
    assert draft.prepared_spells, 'на 8-м заклинания предложены'
    fitted, answers, dropped, problems = fit_level_picks(
        draft.model_copy(update={'level': 4}), draft.choices)
    assert not problems, problems
    assert any(str(row['title']).startswith('заклинания') for row in dropped), dropped
    magic = next(step for step in contract(fitted)['steps'] if step['id'] == 'magic')
    pool = {row['id'] for row in (magic['cantrips'] + magic['spells'])}
    assert set(fitted.prepared_spells) <= pool, 'ни одного заклинания выше новых ячеек'
    assert magic['status'] == 'complete', magic['reasons']
