"""Требование 7: способность не считается реализованной, потому что она в списке."""

import pytest

from dnd5e_engine.build_spec import derive_sheet, make_build_spec
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope='module')
def loader():
    return BundledAssetLoader()


FIGHTER = {'str': 16, 'dex': 14, 'con': 14, 'int': 10, 'wis': 10, 'cha': 8}


def _fighter(loader, level):
    spec = make_build_spec(
        class_slug='fighter', species_slug='human', background_slug='soldier',
        ability_scores=dict(FIGHTER), level=level,
    )
    return derive_sheet(spec, loader=loader)


def test_extra_attack_changes_the_number_not_just_the_sheet(loader):
    counts = [_fighter(loader, lv).extra_attack_count for lv in (1, 4, 5, 10, 11, 19, 20)]
    assert counts == [0, 0, 1, 1, 2, 2, 3]


def test_proficiency_bonus_follows_the_printed_table(loader):
    pbs = [_fighter(loader, lv).proficiency_bonus for lv in (1, 4, 5, 8, 9, 12, 13, 16, 17, 20)]
    assert pbs == [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]


def test_hit_points_grow_with_constitution_and_level(loader):
    hp1 = _fighter(loader, 1).hp_max
    hp20 = _fighter(loader, 20).hp_max
    assert hp1 < hp20
    # Воин: d10, ТЕЛ +2. 1 уровень = 10+2 = 12; 20 уровень = 12 + 19*(6+2) = 164.
    assert hp1 == 12
    assert hp20 == 164


def test_action_surge_is_a_resource_at_its_level_only(loader):
    assert 'action-surge' not in _fighter(loader, 1).features
    assert 'action-surge' in _fighter(loader, 2).features


def test_indomitable_appears_at_nine_not_eight(loader):
    assert 'indomitable' not in _fighter(loader, 8).features
    assert 'indomitable' in _fighter(loader, 9).features


def test_the_class_actually_changes_the_sheet(loader):
    'Разные классы дают разные листы при одинаковых характеристиках.'
    wizard = derive_sheet(
        make_build_spec(
            class_slug='wizard', species_slug='human', background_slug='soldier',
            ability_scores=dict(FIGHTER), level=5,
        ),
        loader=loader,
    )
    fighter = _fighter(loader, 5)
    assert wizard.hp_max != fighter.hp_max
    assert wizard.extra_attack_count != fighter.extra_attack_count

