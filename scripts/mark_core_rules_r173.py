"""r173: пять правил ядра выходят из blocked по прогону, а не по желанию.

Скрипт правит ровно три поля каждой записи: ``implementation``, ``readiness``
и ``revision``. Текст правил, факты и источники не меняются. После правки
главы нужно перепинить ``solo/hybrid_rules.CHAPTER_SHA256`` — файл закреплён
по sha256 (это и был реальный блокер: правка главы ломает hybrid-пакет).
"""
import hashlib
import json
import sys

PATH = 'rules_data/v2/chapters/phb-01/01-core.rules.json'

# id -> (handler, scope, статус, блокеры)
CHANGES = {
    'phb2024.phb01.heroic_inspiration': (
        'RulesetEngine.d20',
        'Вдохновение: ресурс, переброс, передача',
        'engine_subset_verified',
        ['Ресурс живёт в Hero.resources["heroic_inspiration"]; трата перебрасывает одну к20 и '
         'обязана использовать новую кость, излишек передаётся другому герою группы и не копится '
         '(solo/rulesets.py, CombatEngine.gain_inspiration/give_inspiration). На нативном пути '
         '(nat20_worker, vendor/nat20) Героического вдохновения нет вовсе — там нужна правка '
         'вендора: ресурс, операция переброса с обязательным новым результатом и передача.'],
    ),
    'phb2024.phb01.single_die_replacement': (
        'rulesets.replace_die',
        'Замена или переброс одной выбранной к20',
        'engine_subset_verified',
        ['Замена меняет ровно одну кость записанного броска (остальные остаются теми же, '
         'генератор не вызывается) — solo/rulesets.replace_die. Отдельной команды «заменить '
         'кость после броска» на экране боя нет: операцию зовёт переброс вдохновением.'],
    ),
    'phb2024.phb01.base_ac': (
        'armor_class.armor_class',
        'Базовый КЗ и единственный расчёт',
        'engine_subset_verified',
        ['Единственный расчёт — solo/armor_class.py: лист, экран и бой читают одно число, второй '
         'базовый расчёт (Unarmored Defense рядом с бронёй) не складывается и назван в ignored. '
         'Магические предметы с бонусом к КЗ и заклинания вроде Mage Armor в каталоге снаряжения '
         'не зарегистрированы, поэтому их бонус в расчёт пока не входит.'],
    ),
    'phb2024.phb01.attack_ability': (
        'CombatEngine.attack',
        'Выбор характеристики атаки',
        'engine_subset_verified',
        ['Характеристика берётся из правила (solo/combat.attack_ability): рукопашное — Сила, '
         'дальнобойное — Ловкость, фехтовальное — Сила или Ловкость по выбору игрока, чужой выбор '
         'отвергается. Заклинательные атаки (характеристика заклинателя) в хостовом бою не '
         'зарегистрированы — это остаток правила.'],
    ),
    'phb2024.phb01.voluntary_save_failure': (
        'RulesetEngine.saving_throw',
        'Добровольный провал спасброска без броска',
        'engine_subset_verified',
        ['Добровольный провал — параметр voluntary: кость не бросается (draws=0), ресурсы не '
         'тратятся, обычный спасбросок этот путь не подменяет. Отдельной кнопки «провалить» на '
         'экране и операции в vendor/nat20 нет — вызов идёт от доверенного решения игрока.'],
    ),
}


def replace_rule(raw, rule_id, handler, scope, status, blockers):
    marker = '"id": "%s"' % rule_id
    start = raw.index(marker)
    end = raw.index('\r\n    }', start)
    block = raw[start:end]
    import re
    implementations = re.findall(r'"implementation": "([a-z_]+)"', block)
    assert len(implementations) == 1, (rule_id, implementations)
    block = block.replace('"implementation": "%s"' % implementations[0],
                          '"implementation": "existing_engine_subset"')
    lines = ['      "readiness": {',
             '        "status": "%s",' % status,
             '        "handler": "%s",' % handler,
             '        "scope": "%s",' % scope,
             '        "blockers": [']
    for index, text in enumerate(blockers):
        comma = ',' if index < len(blockers) - 1 else ''
        lines.append('          %s%s' % (json.dumps(text, ensure_ascii=False), comma))
    lines += ['        ]', '      }']
    old_start = block.index('      "readiness": {')
    old_end = block.index('\r\n      }', old_start) + len('\r\n      }')
    block = block[:old_start] + '\r\n'.join(lines) + block[old_end:]
    assert block.count('"revision": 1') == 1, rule_id
    block = block.replace('"revision": 1', '"revision": 2')
    return raw[:start] + block + raw[end:]


def main():
    raw = open(PATH, encoding='utf-8', newline='').read()
    for rule_id, (handler, scope, status, blockers) in CHANGES.items():
        raw = replace_rule(raw, rule_id, handler, scope, status, blockers)
    json.loads(raw)  # файл обязан остаться разбираемым JSON
    open(PATH, 'w', encoding='utf-8', newline='').write(raw)
    print('new CHAPTER_SHA256 =',
          hashlib.sha256(raw.replace('\r\n', '\n').encode('utf-8')).hexdigest())


if __name__ == '__main__':
    sys.exit(main())
