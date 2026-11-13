"""r214 — доказательство приёмки партии: числа из вывода Nat20, своих не считаем.

Запуск: .venv/bin/python reports/r223/r214-acceptance.py <каталог профиля>
Печатает: состав партии, листы (класс, уровень, hp, КД), числа до/после
повышения уровня героя и состав соло-кампании.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from solo.nat20_api import Nat20GameEngine          # noqa: E402
from solo.nat20_wizard import suggested_build      # noqa: E402
from solo.storage import Store                     # noqa: E402


def build(class_slug, level=1):
    return suggested_build(class_slug=class_slug, species_slug='human',
                           background_slug='soldier', level=level)


def show(title, party):
    print(title)
    for row in party:
        classes = ','.join(row.get('classes') or {}) or '-'
        print('  {id:12} {role:9} {name:8} {cls:8} lvl={lvl} hp={cur}/{mx} ac={ac} '
              'abilities={ab}'.format(
                  id=row['id'], role=row['role'], name=row['name'], cls=classes,
                  lvl=row['level'], cur=row['hp_current'], mx=row['hp_max'], ac=row['ac'],
                  ab=json.dumps(row['abilities'], ensure_ascii=False)))
    print()


def main(root):
    store = Store(Path(root) / 'r214.sqlite3')
    api = Nat20GameEngine(store)

    solo = api.create_experimental_campaign('Соло', build=build('fighter'), encounters={},
                                            title='Соло')
    print('СОЛО-кампания:', len(api.party(store.campaign(solo['id']))), 'член партии')
    show('  состав:', api.party(store.campaign(solo['id'])))

    campaign = api.create_experimental_campaign('Партия', build=build('fighter'), encounters={},
                                                title='Партия')
    cid = campaign['id']
    for name, slug in (('Мира', 'wizard'), ('Торн', 'rogue')):
        joined = api.add_companion(cid, name=name, build=build(slug), request_id='join-' + slug)
        print('присоединился:', joined['member'], 'событие', joined['event'])

    before = api.party(store.campaign(cid))
    show('ПАРТИЯ ПОСЛЕ ПРИСОЕДИНЕНИЯ:', before)

    picks = list(build('wizard')['selected_choices']) + ['expertise:arcana']
    api.level_up(cid, request_id='level-two', expected_version=store.campaign(cid)['version'],
                 class_slug='fighter', milestone=2,
                 party_choices={'companion-1': {'selected_choices': picks}})
    after = api.party(store.campaign(cid))
    show('ПАРТИЯ ПОСЛЕ ПОВЫШЕНИЯ ГЕРОЯ ДО 2:', after)

    print('было / стало (hp_max, level, hit_dice):')
    for old, new in zip(before, after):
        print('  {id:12} hp {a}/{b} -> {c}/{d}  lvl {e} -> {f}  dice {g} -> {h}'.format(
            id=old['id'], a=old['hp_current'], b=old['hp_max'],
            c=new['hp_current'], d=new['hp_max'], e=old['level'], f=new['level'],
            g=(old['hit_dice'] or {}).get('dice_remaining'),
            h=(new['hit_dice'] or {}).get('dice_remaining')))
    print('журнал:', [row.get('title') or row.get('original')
                      for row in store.history(cid)][-3:])


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'runtime/r214-acceptance')
