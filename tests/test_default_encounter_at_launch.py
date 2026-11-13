"""A campaign prepared without an authored encounter must still be fightable.

Real players prepare from a PDF; nobody hands launch_prepared a hand-built
encounter. The default must therefore be a real registered Nat20 encounter,
not an empty dict and not a phantom id.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_preparation_without_encounters_still_seeds_a_registered_fight():
    from solo.prototype_prepared import default_encounters
    fights = default_encounters()
    assert fights, 'preparation must not leave the table without a fight'
    for name, encounter in fights.items():
        assert encounter.actors, name
        for actor in encounter.actors:
            from dnd5e_srd_data.loader import BundledAssetLoader
            assert BundledAssetLoader().get_monster(actor.monster) is not None, actor.monster


def test_the_limitation_note_is_no_longer_emitted_for_a_seeded_fight():
    source = (ROOT / 'solo' / 'prototype_prepared.py').read_text(encoding='utf-8')
    seeded = source.index('encounters = default_encounters()')
    noted = source.index('Боевые встречи не подтверждены')
    assert seeded < noted, 'the note must only fire when seeding also failed'
