"""Живая проба A05 на настоящей книге: один статус, требования со страницы.

Проба не трогает живую базу и профиль: временный каталог, модель PreviewMock,
ни одного платного вызова. Книга читается из runtime/books (оригинал не
изменяется, копируется только байтами в загрузку).
"""
import json
import os

os.environ['SOLO_TEST'] = '1'

import shutil
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / 'runtime/books/The Rise of Tiamat RUS.pdf'
OUT = ROOT / 'reports/A05/live-probe.json'
folder = Path(tempfile.mkdtemp(prefix='a05-live-', dir=str(ROOT / 'runtime')))
app = create_app(folder / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True)
client = TestClient(app)
report = dict(book=str(BOOK), bundle=None, job=None, card=None, review=None, modules=[], checks={})

bundle = client.post('/api/adventure/library', json={'title': 'The Rise of Tiamat RUS (проба A05)',
                                                      'metadata': {'auto_split': True}}).json()
report['bundle'] = bundle['id']
data = BOOK.read_bytes()
started = time.monotonic()
job = client.post('/api/adventure/library/%s/files' % bundle['id'],
                  files={'file': (BOOK.name, data, 'application/pdf')},
                  data={'purpose': 'book'}).json()
deadline = started + 300
while time.monotonic() < deadline:
    job = client.get('/api/adventure/jobs/' + job['id']).json()
    if job.get('status') in ('complete', 'failed', 'cancelled'):
        break
    time.sleep(1)
report['job'] = {k: job.get(k) for k in ('id', 'status', 'done', 'total', 'message')}
report['seconds'] = round(time.monotonic() - started, 1)

card = next(row for row in client.get('/api/adventure/library').json() if row['id'] == bundle['id'])
readiness = card['readiness']
report['card'] = dict(revision=card['revision'], modules=card['modules'], documents=card['documents'],
                      pages=card['pages'], status=card['status'], jobs=[row['message'] for row in card['jobs']],
                      headline=readiness['headline'], counts=readiness['counts'],
                      start_ready=readiness['start_ready'], book_ready=readiness['book_ready'],
                      instructions_checked=readiness['instructions_checked'], stage=readiness['stage'],
                      steps=[(row['id'], row['state'], row['detail']) for row in readiness['steps']],
                      warnings=[(row['code'], row['action'], row['text']) for row in readiness['warnings']])

modules = client.get('/api/adventure/library/%s/modules' % bundle['id']).json()
report['modules'] = [dict(id=row['id'], title=row['title'], kind=row['kind'], ready=row['ready'],
                          pages=row['source_pages'], requirements=row['requirements'],
                          locations=row['locations']) for row in modules]

client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
review = client.get('/api/adventure/review/library/' + bundle['id']).json()
report['review'] = dict(revision=review['readiness']['revision'], counts=review['readiness']['counts'],
                        headline=review['readiness']['headline'],
                        modules=len(review['modules']))

# Приёмка 2: ни одной пары противоречащих чисел на экране.
numbers = [card['modules'], readiness['counts']['modules'], len(modules), len(review['modules'])]
report['checks']['one_module_number'] = len(set(numbers)) == 1
report['checks']['one_revision'] = len({card['revision'], readiness['revision'],
                                        review['readiness']['revision']}) == 1
report['checks']['history_not_status'] = ('Модулей: ' in str((card['jobs'][0] or {}).get('message') or '')
                                          and 'Модулей: ' not in readiness['headline'])
report['checks']['every_warning_has_action'] = all(
    row['action'] in ('fix', 'retry', 'details') for row in readiness['warnings'])
start_modules = [row for row in modules if row['kind'] != 'appendix']
report['checks']['appendix_never_start'] = bool(readiness['start']) and all(
    readiness['start']['id'] != row['id'] for row in modules if row['kind'] == 'appendix')
report['checks']['start_is_first_episode'] = bool(start_modules) and bool(readiness['start']) \
    and readiness['start']['id'] == start_modules[0]['id']
requirements = [item for row in modules for item in row['requirements']]
levels = [item for item in requirements if item['kind'] == 'minimum_level']
parties = [item for item in requirements if item['kind'] == 'recommended_party']
report['checks']['level_requirement_with_page'] = [dict(value=row['value'], page=row['page'],
                                                         quote=row['quote'][:160]) for row in levels]
report['checks']['party_requirement_with_page'] = [dict(value=row['value'], page=row['page'],
                                                        quote=row['quote'][:160]) for row in parties]
report['checks']['playable_only_with_valid_start'] = (
    readiness['start_ready'] == (not readiness['start_problem']) and
    ('Можно играть' in readiness['headline']) == readiness['start_ready'])

# Приёмка 1 на настоящей книге: книга просит 8-й уровень, у героя 1-й —
# движок не пускает в бой, пока сверка не решена, и называет оба пути.
from solo import level_gate
start_module = next((row for row in modules if readiness['start'] and row['id'] == readiness['start']['id']), None)
needs = {row['kind']: row for row in (start_module or {}).get('requirements', [])}
minimum = needs.get('minimum_level', {}).get('value')
state = {'adventure': {'definition': dict(title=(start_module or {}).get('title') or '',
                                          minimum_level=minimum,
                                          maximum_level=needs.get('maximum_level', {}).get('value'),
                                          recommended_party=needs.get('recommended_party', {}).get('value'))},
         'hero': {'level': 1}}
gate = level_gate.projection(state) or {}
blocked = False
try:
    level_gate.guard(state, {'kind': 'start_combat'})
except ValueError:
    blocked = True
report['checks']['book_level_gate'] = dict(
    book_level=minimum, page=needs.get('minimum_level', {}).get('page'), hero_level=1,
    gap=gate.get('gap'), combat_blocked=blocked,
    options=[row.get('id') for row in gate.get('options') or []])
report['checks']['level_1_hero_blocked_or_adapted'] = bool(minimum and minimum >= 8
                                                           and gate.get('gap') == 'lower' and blocked
                                                           and {'rebuild', 'accept'} <= {row.get('id') for row in gate.get('options') or []})

OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps({k: report[k] for k in ('seconds', 'job', 'checks')}, ensure_ascii=False, indent=2))
print('CARD', json.dumps(report['card'], ensure_ascii=False)[:1200])
print('OUT', OUT)
shutil.rmtree(folder, ignore_errors=True)
