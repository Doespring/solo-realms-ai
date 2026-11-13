"""A06 на живой книге: приложения Тиамат — справочник, а вход эпизода назван книгой.

Прогон локальный и бесплатный: PDF читает наш же воркер импорта, нарезку делает
`AdventureLibrary.auto_split`, модель не зовётся.

    PYTHONPATH=. .venv/bin/python scripts/probe_a06_tiamat.py

Что печатает (и кладёт в reports/A06/tiamat.json):

* сколько модулей книга дала и сколько из них с кнопкой старта;
* `entry_scene` первого эпизода («Совет Глубоководья») и совпадает ли он с названием;
* долю сущностей со страницей-источником;
* что правка типа человеком переживает повторную нарезку.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault('SOLO_TEST', '1')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solo.adventure_library import AUTO_SPLIT_KEY, AdventureLibrary  # noqa: E402
from solo.prepared_world import source_span  # noqa: E402

BOOK = ROOT / 'runtime' / 'books' / 'The Rise of Tiamat RUS.pdf'
OUT = ROOT / 'reports' / 'A06'


def new_library(name):
    data = ROOT / 'runtime' / 'a06-probe' / name
    data.mkdir(parents=True, exist_ok=True)
    return AdventureLibrary(data)


def parse(library):
    """Импорт книги тем же путём, каким его делает приложение (воркер)."""
    bundle = library.create('The Rise of Tiamat (A06)', {AUTO_SPLIT_KEY: True, 'edition': '2014'})['id']
    job = library.enqueue(bundle, BOOK, BOOK.name, 'book')
    worker = [sys.executable, '-m', 'solo.adventure_worker', str(library.data), job['id']]
    done = subprocess.run(worker, cwd=ROOT, capture_output=True, text=True, timeout=900)
    record = library.job(job['id'])
    if record['status'] != 'complete':
        raise SystemExit('Импорт не завершился: %s %s %s' % (record['status'], record['message'],
                                                            done.stderr[-2000:]))
    return bundle, job['document'], record['message']


def rows_of(scene):
    return [*(scene.get('locations') or []), *(scene.get('npcs') or []),
            *(scene.get('checks') or []), *(scene.get('reference') or [])]


def coverage(library, bundle, document, modules):
    """Сколько сущностей называют свою страницу и несут напечатанную цитату."""
    total = missing = unproven = 0
    examples = []
    for module in modules:
        scene = module['scene']
        for row in rows_of(scene):
            total += 1
            provenance = row.get('provenance') or {}
            page = provenance.get('page') or row.get('page') or 0
            quote = provenance.get('quote') or row.get('quote') or ''
            if page < 1 or not (provenance.get('document') or row.get('document')):
                missing += 1
                examples.append(dict(module=module['id'], name=row.get('name'), why='нет страницы'))
                continue
            text = library.page_text(bundle, provenance.get('document') or document, int(page))
            if not quote or not source_span(text, quote):
                unproven += 1
                examples.append(dict(module=module['id'], name=row.get('name'), page=page,
                                     why='цитаты нет на названной странице'))
    return dict(total=total, with_page=total - missing - unproven, missing_page=missing,
                quote_not_on_page=unproven, share=round((total - missing - unproven) / total, 4)
                if total else 0, examples=examples[:5])


def main():
    if not BOOK.is_file():
        raise SystemExit('Нет книги: ' + str(BOOK))
    library = new_library(time.strftime('tiamat-%Y%m%d-%H%M%S'))
    started = time.monotonic()
    bundle, document, message = parse(library)
    modules = library.modules(bundle)

    kinds = {}
    for module in modules:
        kinds[module['kind']] = kinds.get(module['kind'], 0) + 1
    appendices = [module for module in modules if module['kind'] == 'appendix']
    report = dict(
        book=BOOK.name, bundle=bundle, document=document, job_message=message,
        seconds=round(time.monotonic() - started, 1), modules=len(modules), kinds=kinds,
        modules_titles=[module['title'] for module in modules],
        appendices=[dict(id=module['id'], title=module['title'], reference=module['reference'],
                         startable=module['startable'], entries=module['reference_entries'],
                         locations=module['locations'])
                    for module in appendices],
        appendices_with_start=len([module for module in appendices if module['startable']]),
        reference_entries=sum(module['reference_entries'] for module in appendices),
    )

    # Приёмка 2: вход эпизода — то, что книга назвала эпизодом.
    episode = next((module for module in modules
                    if 'Совет Глубоководья' in module['title']), None)
    if episode is None:
        report['entry_scene'] = dict(found=False, titles=report['modules_titles'][:6])
    else:
        entry = episode['entry_scene']
        same = entry.get('name', '').strip().casefold() in episode['title'].casefold()
        report['entry_scene'] = dict(found=True, module=episode['id'], title=episode['title'],
                                     entry=entry, matches_title=same,
                                     first_place=(episode['scene'].get('locations') or [{}])[0].get('name'))

    report['provenance'] = coverage(library, bundle, document, modules)

    # Приёмка 4: правка типа человеком переживает повторную нарезку.
    source = None
    for module in modules:
        if module['kind'] != 'adventure':
            continue
        for row in module['scene'].get('locations') or []:
            if (row.get('provenance') or {}).get('page'):
                source = (module, row)
                break
        if source:
            break
    if source:
        module, row = source
        page = int(row['provenance']['page'])
        library.edit_entity(bundle, module['id'], document, page, 'location', row['name'], 'monster')
        library.auto_split(bundle, document)
        again = next(item for item in library.modules(bundle) if item['id'] == module['id'])
        moved = [item for item in again['scene'].get('reference') or []
                 if item['name'] == row['name']]
        report['correction'] = dict(module=module['id'], name=row['name'], page=page,
                                    stayed_out_of_places=row['name'] not in
                                    [item['name'] for item in again['scene'].get('locations') or []],
                                    moved_to_reference=bool(moved),
                                    entity_type=moved[0].get('entity_type') if moved else '',
                                    edited=bool(moved[0].get('edited')) if moved else False,
                                    edits=len(again['scene'].get('edits') or []))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'tiamat.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
