'''A full character sheet must reach the table, not three skills out of eighteen.

A live snapshot listed 31 check ids while the chat registry offered 3, and a
location without its own checks produced an empty list -- the player was told a
trained character could make no roll at all.'''
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from solo.nat20_content import CHECKS  # noqa: E402

SKILLS = ['acrobatics', 'animal_handling', 'arcana', 'athletics', 'deception', 'history',
          'insight', 'intimidation', 'investigation', 'medicine', 'nature', 'perception',
          'performance', 'persuasion', 'religion', 'sleight_of_hand', 'stealth', 'survival']
ABILITIES = ['str', 'dex', 'con', 'int', 'wis', 'cha']


def test_every_skill_is_reachable():
    assert [name for name in SKILLS if name not in CHECKS] == []


def test_every_ability_and_save_is_reachable():
    assert [name for name in ABILITIES if name not in CHECKS] == []
    assert [name for name in ABILITIES if 'save-' + name not in CHECKS] == []


def test_entries_carry_kind_skill_ability_dc():
    for name, value in CHECKS.items():
        kind, skill, ability, dc = value
        assert kind in ('skill', 'ability', 'saving_throw'), name
        assert isinstance(dc, int) and 1 <= dc <= 30, name
        assert ability is None or ability.lower() in ABILITIES, name


def test_saving_throws_are_not_plain_abilities():
    assert CHECKS['save-wis'][0] == 'saving_throw'
    assert CHECKS['wis'][0] == 'ability'


def test_snapshot_falls_back_to_the_registry():
    source = (pathlib.Path(__file__).resolve().parents[1] / 'solo' / 'command_engine.py').read_text(encoding='utf-8')
    assert '_BASE_CHECKS()' in source
