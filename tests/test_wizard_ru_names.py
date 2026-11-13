"""r249: редактор героя называет виды и предыстории по-русски.

Движок отдаёт имена SRD как есть — «Dragonborn», «Wizard», «Acolyte», — и на
экране это стена для человека, который правил не знает. Русские подписи живут в
`solo/character_catalog.py`; здесь проверяется, что договор редактора берёт их,
что ключи движка (slug) при этом не подменяются переводом и что непереведённое
имя честно остаётся английским, а не превращается в пустую строку.
"""
import os
os.environ['SOLO_TEST'] = '1'
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from solo.character_catalog import (
    BACKGROUND_NAMES,
    CLASS_NAMES,
    SPECIES_NAMES,
    SPECIES_NAMES_ENGLISH,
    display_name,
)
from solo.nat20_wizard import WizardDraft, contract

LATIN = re.compile(r'[A-Za-z]')
#: Шаги, где игрок выбирает вид, подвид, класс, подкласс и предысторию. Магия и
#: особенности сюда не входят: имена заклинаний и черт приходят из данных SRD
#: целыми сотнями и переводятся отдельной работой.
TRANSLATED_STEPS = ('species', 'lineage', 'class', 'subclass', 'background')


def step(state, name):
    return next((row for row in state['steps'] if row['id'] == name), None)


@pytest.mark.parametrize('draft', [
    WizardDraft(),
    WizardDraft(species_base='Elf'),
    WizardDraft(species_slug='elf-drow', class_slug='wizard', background_slug='sage'),
    WizardDraft(species_slug='gnome-forest', class_slug='fighter', background_slug='soldier'),
    WizardDraft(species_slug='tiefling-infernal', class_slug='rogue',
                background_slug='criminal', level=5),
    WizardDraft(species_slug='goliath', class_slug='cleric', background_slug='acolyte', level=3),
    WizardDraft(species_slug='halfling', class_slug='barbarian', background_slug='acolyte'),
], ids=['пустой', 'эльф-без-подвида', 'дроу-волшебник', 'лесной-гном-воин',
        'тифлинг-плут-5', 'голиаф-жрец-3', 'полурослик-варвар'])
def test_no_step_offers_a_latin_name(draft):
    """Ни один вариант вида, подвида, класса, подкласса и предыстории — не латиница."""
    state = contract(draft)
    seen = 0
    for row in state['steps']:
        if row['id'] not in TRANSLATED_STEPS:
            continue
        assert row['options'], 'у шага ' + row['id'] + ' нет ни одного варианта'
        for option in row['options']:
            seen += 1
            assert not LATIN.search(str(option['name'])), (row['id'], option)
    assert seen >= 4, 'договор не отдал ни одного переведённого шага'


def test_species_names_are_the_ones_named_on_the_screen():
    """Приёмка r249: семь названий, которые владелец ждёт на шаге «Вид»."""
    names = [option['name'] for option in step(contract(WizardDraft()), 'species')['options']]
    for wanted in ('Дварф', 'Эльф', 'Полурослик', 'Голиаф', 'Человек',
                   'Драконорождённый', 'Гном'):
        assert wanted in names, (wanted, names)


def test_lineage_names_are_russian_and_detail_matches():
    state = contract(WizardDraft(species_base='Elf'))
    lineage = step(state, 'lineage')
    assert [option['name'] for option in lineage['options']] == [
        'Эльф, дроу', 'Эльф, высший', 'Эльф, лесной'
    ], lineage['options']
    species = step(state, 'species')
    elf = next(option for option in species['options'] if option['id'] == 'Elf')
    assert elf['name'] == 'Эльф'
    assert 'Эльф, дроу' in elf['detail']


def test_engine_slugs_are_untouched():
    """Перевод — только подпись: по id движок находит вид, класс и предысторию."""
    state = contract(WizardDraft())
    species = step(state, 'species')
    assert {option['id'] for option in species['options']} == set(SPECIES_NAMES_ENGLISH)
    lineages = [variant['slug'] for option in species['options'] for variant in option['variants']]
    assert 'elf-high' in lineages and 'tiefling-chthonic' in lineages
    assert {option['id'] for option in step(state, 'class')['options']} == set(CLASS_NAMES)
    assert {option['id'] for option in step(state, 'background')['options']} == set(BACKGROUND_NAMES)


def test_catalog_covers_every_variant_the_engine_offers():
    """Движок добавит вид или подвид — тест скажет, а не покажет англицизм."""
    state = contract(WizardDraft())
    for option in step(state, 'species')['options']:
        assert option['id'] in SPECIES_NAMES_ENGLISH, option['id']
        for variant in option['variants']:
            assert variant['slug'] in SPECIES_NAMES, variant['slug']
    for option in step(state, 'class')['options']:
        assert option['id'] in CLASS_NAMES, option['id']
    for option in step(state, 'background')['options']:
        assert option['id'] in BACKGROUND_NAMES, option['id']


def test_untranslated_name_stays_the_engine_name():
    """Нет перевода — показываем имя движка: пустая подпись хуже английской."""
    assert display_name('subclass', 'phb2024.wizard.illiuzionist', fallback='Иллюзионист') == 'Иллюзионист'
    assert display_name('class', 'unknown-class', fallback='Unknown') == 'Unknown'
    assert display_name('class', None) == ''
