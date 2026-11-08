'''The engine resolved attacks against 86 weapons while the chat named four.

combat.py carried its own table (unarmed, club, longsword, shortbow) with
hard-coded dice, and the snapshot offered no weapon list at all, so a player
could not say 'I draw a dagger' -- nothing told the model the word existed.
'''
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_dataset_names_every_weapon_the_engine_resolves():
    from solo.nat20_content import weapons
    found = weapons()
    assert len(found) >= 80, len(found)
    assert 'dagger' in found and 'longsword' in found and 'shortbow' in found


def test_weapon_records_carry_damage_in_the_engine_s_own_words():
    from solo.nat20_content import weapons
    sword = weapons()['longsword']
    assert sword['damage'] == [('1d8', 'slashing')]
    assert sword['category'] == 'martial_melee'
    bow = weapons()['shortbow']
    assert bow['damage'] == [('1d6', 'piercing')]


def test_the_old_hard_coded_table_no_longer_decides_what_a_weapon_is():
    source = (ROOT / 'solo' / 'combat.py').read_text(encoding='utf-8')
    # The legacy table may stay for old campaigns, but the registry must come
    # from the dataset: a fourth hard-coded weapon would mean two truths.
    assert "dice_count" in source
    from solo.nat20_content import weapons
    assert len(weapons()) > len(source.split("Weapon(id=")) - 1
