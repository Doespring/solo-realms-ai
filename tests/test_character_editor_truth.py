"""The character editor tells the truth about what is still missing.

Each check names a way the screen used to claim progress the engine had not
confirmed: a step marked green when the engine could not count what is owed,
point-buy values outside the published range counted as free, a missing sheet
explained as "choose species and class" after both were chosen, and a
recommendation that erased the player's own decisions.
"""
import os
os.environ['SOLO_TEST'] = '1'
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

import dnd5e_engine.rules.advancement as advancement
from solo.nat20_wizard import WizardDraft, contract, recommend_draft, suggested_draft

ABILITIES = ['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']


def hero(**extra):
    base = dict(species_slug='human', species_base='human', class_slug='fighter',
                background_slug='soldier', name='Ирина')
    base.update(extra)
    return WizardDraft(**base)


def step(state, name):
    return next(row for row in state['steps'] if row['id'] == name)


def test_unknown_pending_never_marks_choices_complete(monkeypatch):
    """An unknown number of missing picks is not zero missing picks."""
    draft, _ = suggested_draft(class_slug='fighter', species_slug='human',
                               background_slug='soldier')
    before = contract(draft)
    assert [row for row in before['steps'] if row.get('choice_key')], 'a clean hero owes picks'

    def refused(*_args, **_kwargs):
        raise ValueError('Навык уже занят предысторией.')

    monkeypatch.setattr(advancement, 'pending_choices', refused)
    after = contract(draft)
    choices = [row for row in after['steps'] if row.get('choice_key')]
    assert choices, 'the choice steps are still listed'
    assert all(row['status'] == 'unavailable' for row in choices), \
        'nothing is called complete when the owed picks could not be counted: ' + str(
            [(row['title'], row['status']) for row in choices])
    assert all(int(row['missing']) > 0 for row in choices), 'an unknown debt is never zero'
    assert not after['ready'], 'a draft the engine refused to check is not ready'
    assert any('занят' in str(issue) for issue in after['issues'])


def test_point_buy_bounds_come_from_the_native_table():
    """1..20 is not the point-buy range, and an impossible value is not free."""
    illegal = hero(ability_score_method='point_buy',
                   ability_scores={name: 20 for name in ABILITIES})
    state = contract(illegal)
    scores = step(state, 'scores')
    assert scores['status'] == 'invalid', 'six impossible scores are not a finished step'
    assert scores['budget']['minimum'] == 8 and scores['budget']['maximum'] == 15
    assert scores['budget']['spent'] == 0 and scores['budget']['left'] == 27, \
        'an unpriced value must not be charged as zero and leave a wrong remainder'
    assert [row['maximum'] for row in scores['abilities']] == [15] * 6
    assert any('от 8 до 15' in reason for reason in scores['reasons'])
    assert not state['ready']


def test_legal_point_buy_set_is_still_complete():
    """The published set 12/8/13/10/14/15 is accepted in any order."""
    placed = dict(zip(ABILITIES, [12, 8, 13, 10, 14, 15]))
    state = contract(hero(ability_score_method='point_buy', ability_scores=placed))
    scores = step(state, 'scores')
    assert scores['status'] == 'complete', scores['reasons']
    assert scores['budget']['spent'] == 27 and scores['budget']['left'] == 0


def test_missing_sheet_names_the_engine_reason():
    """The contract carries why there is no sheet, not a guess by the screen."""
    # Species and class are chosen; the draft is refused for its background, which
    # is exactly the state the screen used to explain as "choose species and class".
    chosen = WizardDraft(species_slug='human', species_base='human', class_slug='fighter',
                         background_slug='nonexistent', name='Ирина')
    state = contract(chosen)
    assert state['sheet'] is None
    assert any('nonexistent' in str(issue) for issue in state['sheet_issues'])
    assert state['sheet_ready'] is False
    assert state['sheet_issues'], 'a missing sheet always has a reason'
    assert not any('вид и класс' in str(issue).lower() for issue in state['sheet_issues']), \
        'the reason must not ask for choices the player already made'


def test_recommendation_keeps_the_players_choices():
    """A suggestion fills the gaps and never rewrites what the table decided."""
    mine = hero()
    value = recommend_draft(mine)
    assert value['ok'], value['issues']
    filled = value['draft']
    assert filled['species_slug'] == 'human'
    assert filled['class_slug'] == 'fighter'
    assert filled['background_slug'] == 'soldier'
    assert filled['name'] == 'Ирина'
    assert 'class_slug' in value['kept'] and 'name' in value['kept']
    assert contract(WizardDraft.model_validate(filled))['ready'], 'the suggestion is legal'


def test_recommendation_does_not_confirm_anything():
    """Suggesting returns a draft; readiness is still the engine's own verdict."""
    value = recommend_draft(WizardDraft())
    assert value['ok'] and value['draft']['name'] == ''
    assert contract(WizardDraft.model_validate(value['draft']))['ready']
    # Without a name the final step still owes one: a suggestion is not a hero.
    assert WizardDraft.model_validate(value['draft']).name == ''
