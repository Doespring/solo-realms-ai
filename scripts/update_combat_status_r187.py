"""r187: статусы главы боя — только по замеру. Правит 04-combat.rules.json.

Замеры (см. tests/test_combat_opportunity_cover_grapple.py и прогон r187):
* исходное положение задаётся автором сцены и доходит до движка
  (`view.actor_zone = {'hero': '1,1', 'goblin-1': '2,1'}`) — правило считается;
* равенство инициативы движок решает сортировкой
  `(-initiative, -dexterity, entity_id)` (orchestrator.py:17249), то есть
  Ловкостью и id, а не выбором игроков — это местное решение, не процедура PHB;
* ни одного слова `mount` в движке нет, при этом данные ездовых в корпусе ЕСТЬ
  (`draft-horse`, `riding-horse`, `warhorse`, `pony`, `mule`, `camel`,
  `elephant`, `giant-seahorse`, `seahorse`, `mastiff`) — блокер вендорный,
  а не «нет контента»;
* ни одного слова `underwater` в движке нет — подводного окружения нет вовсе;
* добровольно лечь (`drop_prone`) хост/движок не умеют: `PlayerIntent`
  принимает только `stand_up`.

Скрипт идемпотентен: повторный запуск не меняет уже правленое.
"""
import json
import pathlib

PATH = pathlib.Path('rules_data/v2/chapters/phb-01/04-combat.rules.json')
MOUNTS = ('draft-horse, riding-horse, warhorse, pony, mule, camel, elephant, '
          'giant-seahorse, seahorse, mastiff')

EDITS = {
    'phb2024.phb01.combat_positions': dict(
        implementation='existing_engine_subset',
        scope=('Замер r187: сцена задаёт клетки героя и врагов, движок их принимает '
               '(view.actor_zone = {hero: 1,1, goblin-1: 2,1}) и ведёт бой по ним. '
               'Разметку сцены по-прежнему выбирает Мастер — это и есть '
               'adjudication_required.'),
        blockers=[],
    ),
    'phb2024.phb01.initiative_tie': dict(
        implementation='existing_engine_subset',
        scope=('Замер r187: равенство инициативы решается сортировкой '
               '(-initiative, -dexterity, entity_id) в orchestrator.py:17249 — '
               'Ловкостью и id, а не выбором игроков/GM, как в правиле. '
               'Это местное решение движка, поэтому строка не становится verified.'),
        blockers=[],
    ),
    'phb2024.phb01.drop_prone': dict(
        blockers=['No registered voluntary-drop command; stand_up must not be used as its inverse. '
                  'Замер r187: PlayerIntent не принимает добровольного «лечь» — Сбитым с ног '
                  'можно стать только от Толчка или падения. Нужен вендорный интент, '
                  'хост-обход запрещён (второй движок).'],
    ),
    'phb2024.phb01.mount_eligibility': dict(
        blockers=['Mounted combat state and eligibility not registered. '
                  f'Замер r187: в движке нет ни одного упоминания mount, при этом данные '
                  f'ездовых в корпусе есть ({MOUNTS}) — блокер в коде, а не в контенте.'],
    ),
    'phb2024.phb01.mount_cost': dict(
        blockers=['Arithmetic verified; mount state/occupancy transaction unavailable. '
                  'Замер r187: арифметика есть, состояния «верхом» движок не знает '
                  '(посадка/спешивание не переводят ни клетку, ни скорость).'],
    ),
    'phb2024.phb01.mount_control': dict(
        blockers=['No controlled/independent mount actor scheduling. '
                  f'Замер r187: нет ни mount-состояния, ни очереди скакуна; данные есть ({MOUNTS}).'],
    ),
    'phb2024.phb01.fall_from_mount': dict(
        blockers=['Save arithmetic available; mounted transition and destination validation unavailable. '
                  'Замер r187: падать неоткуда — «верхом» движок не различает.'],
    ),
    'phb2024.phb01.underwater_melee': dict(
        blockers=['Combat environment/swim-speed weapon resolver not registered. '
                  'Замер r187: слова underwater в движке нет вовсе; окружение боя '
                  'описывается только сеткой и светом.'],
    ),
    'phb2024.phb01.underwater_ranged': dict(
        blockers=['No underwater attack command; ordinary long-range resolver would be incorrect. '
                  'Замер r187: подводного признака нет ни у сцены, ни у атаки.'],
    ),
    'phb2024.phb01.underwater_fire': dict(
        blockers=['No trusted underwater environment fact; resistance must not be inferred from narrative text. '
                  'Замер r187: сопротивления по окружению движок не выводит — источника факта нет.'],
    ),
}


def main():
    data = json.loads(PATH.read_text(encoding='utf8'))
    changed = []
    for rule in data['rules']:
        edit = EDITS.get(rule['id'])
        if not edit:
            continue
        before = {'implementation': rule['implementation'],
                  'scope': rule['readiness']['scope'],
                  'blockers': list(rule['readiness']['blockers'])}
        if 'implementation' in edit:
            rule['implementation'] = edit['implementation']
        if 'scope' in edit:
            rule['readiness']['scope'] = edit['scope']
        if 'blockers' in edit:
            rule['readiness']['blockers'] = edit['blockers']
        after = {'implementation': rule['implementation'],
                 'scope': rule['readiness']['scope'],
                 'blockers': list(rule['readiness']['blockers'])}
        if before != after:
            rule['revision'] = rule.get('revision', 1) + 1
            changed.append(rule['id'].split('.')[-1])
    raw = PATH.read_bytes()
    crlf = b'\r\n' in raw
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if crlf:
        # Файл в репозитории с CRLF (`core.autocrlf=input` его не перепишет):
        # смена концов строк сделала бы диффом весь файл и тронула бы счётчики.
        text = text.replace('\n', '\r\n')
    PATH.write_bytes(text.encode('utf8'))
    print('changed:', ', '.join(changed) or 'ничего (уже правлено)')


if __name__ == '__main__':
    main()
