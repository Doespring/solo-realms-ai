'''The mastery the player picks must reach the derived sheet.

An independent critic caught this: weapon_masteries was declared on the sheet,
the party spec and the combatant, but nothing ever filled it from the hero's
choice, so a Fighter who picked a weapon earned no mastery at all. Picks now
travel through the same selected_choices stream as feats and features.
'''
import sys, pathlib, os

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import solo.nat20_content as nc  # noqa: E402
from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet  # noqa: E402

BASE = dict(
    species_slug='human', class_slug='fighter', level=1,
    ability_scores=dict(strength=16, dexterity=14, constitution=14,
                        intelligence=10, wisdom=12, charisma=8),
    equipment=('longsword', 'chain-mail', 'shield'),
)
SKILLS = ('skill:athletics', 'skill:perception')


def sheet(*picks):
    loader, _, _ = nc.release()
    build = CharacterBuildSpec(selected_choices=(*SKILLS, *picks), **BASE)
    return derive_sheet(build, loader=loader)


def test_hero_who_chose_nothing_earns_no_mastery():
    assert sheet().weapon_masteries == frozenset()


def test_chosen_weapon_reaches_the_sheet():
    assert sheet('longsword').weapon_masteries == frozenset({'longsword'})


def test_choosing_does_not_disturb_other_picks():
    picked = sheet('longsword')
    plain = sheet()
    assert picked.skill_proficiencies == plain.skill_proficiencies
