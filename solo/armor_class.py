"""Class Защиты считается ровно один раз (PHB 2024, «Класс Защиты»).

Правило книги: у героя может быть несколько *вариантов* расчёта КЗ (броня,
Unarmored Defense, заклинание), но применяется только один. Второй базовый
расчёт не складывается с первым: он попадает в список ``ignored`` с причиной.

Этот модуль — единственное место, где базовое КЗ превращается в число.
``solo/inventory.py`` пишет его в лист, лист показывает то же число, а бой
читает его из листа (``CombatEngine.start`` пересчитывает перед боем, чтобы
старая копия не разошлась с листом). Второго пути нет.

Формулы повторяют вендорный расчёт Nat20 (``dnd5e_engine.rules.character``,
``armor_class``), а не придумывают свои: ``default`` = 10 + модификатор
Ловкости; броня — её собственный base_ac с ограничением Ловкости; тяжёлая
броня модификатор Ловкости не получает.
"""
from .contracts import Hero
from typing import Literal

BASE = 10

#: Вторые варианты расчёта: имя умения -> характеристика, которую оно даёт.
#: Слаг — тот же, что в листе (``unarmored_barbarian``), иначе умение не
#: распознаётся и молча даст 10 + Ловкость.
UNARMORED_DEFENSE: dict[str, Literal['strength','dexterity','constitution','intelligence','wisdom','charisma']] = {
    'unarmored_barbarian': 'constitution',
    'unarmored_monk': 'wisdom',
    'unarmored_bard': 'charisma',
}


def modifier(score):
    return (int(score) - 10) // 2


def armor_class(hero, *, armor=None, shield=None, confirmed_base=None):
    """Одно значение КЗ и запись о том, откуда оно взялось.

    ``armor``/``shield`` — параметры снаряжения из ``solo.inventory.CATALOG``
    (``base_ac``/``dex_cap`` и ``ac_bonus``). ``confirmed_base`` — база,
    подтверждённая листом героя, когда расчёт по снаряжению невозможен
    (герой без учёта экономики): она и есть единственный базовый расчёт.
    """
    if not isinstance(hero, Hero):
        hero = Hero.model_validate(hero)
    dex = modifier(hero.abilities['dexterity'])
    candidates = []
    for feature in hero.features:
        if feature in UNARMORED_DEFENSE:
            ability = UNARMORED_DEFENSE[feature]
            candidates.append(dict(id=feature, base=BASE, ability=ability,
                                   contribution=dex + modifier(hero.abilities[ability]),
                                   note='Unarmored Defense: 10 + Ловкость + ' + ability))
    if armor:
        cap = armor.get('dex_cap', 100)
        contribution = 0 if cap == 0 else min(dex, cap)
        # Броня — первый кандидат: второй базовый расчёт (Unarmored Defense)
        # остаётся в списке кандидатов и попадает в ``ignored``, а не складывается.
        candidates.insert(0, dict(id='armor', base=int(armor.get('base_ac', BASE)),
                                  ability='dexterity', contribution=contribution,
                                  note='броня задаёт собственное базовое КЗ'))
    else:
        if not candidates and confirmed_base is not None:
            candidates.append(dict(id='confirmed_sheet', base=int(confirmed_base),
                                   ability=None, contribution=0,
                                   note='база подтверждена листом героя'))
        if not candidates:
            candidates.append(dict(id='default', base=BASE, ability='dexterity',
                                   contribution=dex, note='10 + модификатор Ловкости'))
    chosen, ignored = candidates[0], candidates[1:]
    ac = chosen['base'] + chosen['contribution']
    parts: list[dict] = [dict(
        id=chosen['id'], value=ac,
        formula='%d + %+d' % (chosen['base'], chosen['contribution']) if chosen['ability'] else str(chosen['base']),
        calculation=True)]
    notes = [chosen['note']]
    if armor and 'fighting_style_defense' in hero.features:
        ac += 1
        parts.append(dict(id='fighting_style_defense', value=1, formula='+1', calculation=False))
        notes.append('Боевой стиль «Оборона»: +1 в броне')
    if shield:
        bonus = int(shield.get('ac_bonus', 2))
        ac += bonus
        parts.append(dict(id='shield', value=bonus, formula='+%d' % bonus, calculation=False))
        notes.append('щит прибавляет к любому расчёту')
    ignored = [dict(id=item['id'], reason='второй базовый расчёт не складывается с выбранным')
               for item in ignored]
    total = max(1, min(40, ac))
    return dict(ac=total, base=BASE, ability=chosen['ability'],
                calculation=chosen['id'], calculations_used=1, ignored=ignored,
                parts=parts, notes=notes,
                formula='КЗ %d = %d %+d (%s)%s' % (
                    total, chosen['base'], chosen['contribution'], chosen['id'],
                    ''.join(' %s' % part['formula'] for part in parts if not part['calculation'])))
