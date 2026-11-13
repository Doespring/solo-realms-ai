"""r173: карта покрытия главы 1 приводится к статусам после прогона.

Пять записей `01-core.rules.json` вышли из `blocked`; в карте покрытия меняются
handler/status/test_kind и сводные счётчики. Ничего другого не трогается.
"""
import json

PATH = 'rules_data/v2/coverage/phb-01.json'

CHANGES = {
    'phb2024.phb01.heroic_inspiration': ('RulesetEngine.d20', 'engine_subset_verified', 'formal_behavior'),
    'phb2024.phb01.single_die_replacement': ('rulesets.replace_die', 'engine_subset_verified', 'formal_behavior'),
    'phb2024.phb01.base_ac': ('armor_class.armor_class', 'engine_subset_verified', 'formal_behavior'),
    'phb2024.phb01.attack_ability': ('CombatEngine.attack', 'engine_subset_verified', 'formal_behavior'),
    'phb2024.phb01.voluntary_save_failure': ('RulesetEngine.saving_throw', 'engine_subset_verified', 'formal_behavior'),
}


def main():
    document = json.load(open(PATH, encoding='utf-8'))
    changed = 0
    for row in document['rules']:
        change = CHANGES.get(row['id'])
        if not change:
            continue
        handler, status, test_kind = change
        assert row['status'] in ('blocked', 'engine_subset_verified'), row['id']
        if row['status'] == status and row['handler'] == handler:
            continue
        if row['status'] == 'blocked':
            document['readiness_counts']['blocked'] -= 1
            document['readiness_counts'][status] += 1
        row.update(handler=handler, status=status, test_kind=test_kind)
        changed += 1
    assert changed == len(CHANGES), changed
    # Файл хранится с CRLF; пишем в том же виде, чтобы diff не был переписан
    # целиком из-за концов строк.
    text = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    with open(PATH, 'w', encoding='utf-8', newline='\r\n') as stream:
        stream.write(text)
    print('updated', changed, document['readiness_counts'])


if __name__ == '__main__':
    main()
