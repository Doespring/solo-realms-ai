"""Одна правда о готовности книги — вместо двух спорящих сообщений.

Карточка библиотеки, экран комплекта и закрытая подготовка читают ОДНО
состояние: шесть шагов `upload → extract → index → prepare → validate →
playable`, счётчики из ОДНОЙ ревизии комплекта и два разных ответа на вопрос
«готово ли»:

* ``start_ready`` — можно начинать: стартовая сцена первого эпизода собрана из
  страниц книги и сверена с ними;
* ``book_ready`` — готова вся книга: извлечены все источники, нарезаны и
  размечены все эпизоды.

Раньше эти два ответа жили в разных местах и разными словами: карточка брала
статус из одной сохранённой записи инструкций, список модулей — из таблицы
модулей, а сообщение прошлой операции («Модулей: 12… инструкции по-прежнему
требуют проверки») висело рядом как текущее состояние. Отсюда пара чисел 12/11
и «инструкции проверены» над списком непроверенных модулей.

Правила этого модуля:

1. Все числа читаются в одной транзакции. `revision` возвращается вместе с
   ними, поэтому экран не может сложить счётчики двух разных ревизий.
2. Сообщения прошлых операций (`jobs`) сюда не входят вовсе: это история, её
   место — отдельный раздел «История операций», а не строка статуса.
3. У каждого предупреждения есть действие из закрытого набора
   :data:`ACTIONS` — «исправить», «повторить» или «подробности». Предупреждение
   без действия бесполезно: игрок не знает, что с ним делать.
"""

from __future__ import annotations

import json

#: Шаги книги по порядку. Экран печатает ровно эти подписи.
STEPS = (
    ('upload', 'Источники загружены'),
    ('extract', 'Текст извлечён'),
    ('index', 'Книга нарезана и размечена'),
    ('prepare', 'Стартовая сцена собрана'),
    ('validate', 'Стартовая сцена сверена'),
    ('playable', 'Можно играть'),
)

#: Действия, которые экран умеет показать к предупреждению.
ACTIONS = {'fix', 'retry', 'details'}


def _json(value, default=None):
    """Словарь из поля JSON. Список или мусор — это не словарь: отдаём default."""
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value or '')
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, dict) else default


def _module_rows(db, bundle):
    """Модули книги с тем, что о них известно без догадок."""
    from .book_scene import requirements
    rows = []
    scenes = {row['module']: _json(row['body'], {}) or {}
              for row in db.execute('SELECT module,body FROM module_scenes WHERE bundle=?', (bundle,))}
    for raw in db.execute('SELECT body FROM adventure_modules WHERE bundle=? ORDER BY id', (bundle,)):
        body = _json(raw['body'], {}) or {}
        identifier = str(body.get('id') or '')
        flags = body.get('alternate_flags') if isinstance(body.get('alternate_flags'), dict) else {}
        spans = [span for span in body.get('source_ranges') or []
                 if isinstance(span, dict) and span.get('first_page')]
        first = min((int(span['first_page']) for span in spans), default=0)
        scene = scenes.get(identifier) or {}
        unsupported = [str(note) for note in body.get('unsupported') or [] if str(note).strip()]
        rows.append(dict(
            id=identifier,
            title=str(body.get('title') or ''),
            # Тип раздела берётся из разбора страниц: тело инструкций мастер правит,
            # и правка не должна превращать справочник в стартуемое приключение.
            kind='appendix' if (scene.get('section') or {}).get('kind') == 'appendix'
            or flags.get('kind') == 'appendix' else 'adventure',
            # Границы, сверенные при нарезке, — не то же самое, что сверенные
            # игровые инструкции. Одно поле отвечает за одно.
            boundaries=bool(body.get('reviewed')),
            unsupported=unsupported,
            ready=bool(body.get('reviewed')) and not unsupported,
            first_page=first,
            last_page=max((int(span.get('last_page') or 0) for span in spans), default=0),
            document=str(spans[0].get('document') or '') if spans else '',
            indexed=identifier in scenes,
            scene=scene,
            requirements=requirements(scene, identifier),
        ))
    return rows


def _numbers(scene):
    return dict(locations=len(scene.get('locations') or []),
                npcs=len(scene.get('npcs') or []),
                checks=len(scene.get('checks') or []))


def _start_of(modules, page_lengths):
    """Первый эпизод книги и то, можно ли с него начать.

    Приложение книги — не эпизод: его места остаются местами книги, но начать
    с него нельзя никогда. Эпизод без найденного места или без прочитанной
    первой страницы начать тоже нельзя, и причина называется словами.
    """
    episodes = sorted((row for row in modules if row['kind'] != 'appendix'),
                      key=lambda row: (row['first_page'], row['id']))
    if not episodes:
        return None, 'Книга не нарезана на эпизоды: начинать не с чего'
    first = episodes[0]
    if first['unsupported']:
        return first, 'Эпизод «' + (first['title'] or first['id']) + '» не проверен: ' \
            + '; '.join(first['unsupported'])
    counts = _numbers(first['scene'])
    if not first['indexed']:
        return first, 'Эпизод «' + (first['title'] or first['id']) + '» не размечен: у него нет сцены'
    if not counts['locations']:
        return first, 'Стартовая сцена пуста: в эпизоде «' \
            + (first['title'] or first['id']) + '» не найдено ни одного названного места'
    if first['first_page'] and not page_lengths.get((first['document'], first['first_page'])):
        return first, 'Первая страница эпизода ' + str(first['first_page']) \
            + ' не прочитана: текста для стартовой сцены нет'
    if not first['boundaries']:
        return first, 'Границы эпизода «' + (first['title'] or first['id']) \
            + '» не сверены с книгой: старт не на что опереть'
    return first, ''


def readiness(library, bundle):
    """Состояние книги: шаги, счётчики одной ревизии, два «готово», предупреждения."""
    with library.db() as db:
        # WAL: BEGIN … ROLLBACK даёт один снимок базы. Между двумя SELECT
        # импорт или правка могли бы записать новую ревизию, и тогда «12
        # модулей» и «11 модулей» снова оказались бы на одном экране.
        db.execute('BEGIN')
        try:
            book = db.execute('SELECT id,title,revision,metadata FROM bundles WHERE id=?', (bundle,)).fetchone()
            if book is None:
                raise KeyError('Комплект не найден')
            # Один источник — одна строка: комплект может ссылаться на документ
            # под двумя именами, и тогда JOIN по имени удвоил бы счётчики.
            documents = [dict(row) for row in db.execute(
                'SELECT d.id,d.pages,d.status,d.quality FROM documents d '
                'JOIN (SELECT DISTINCT document FROM bundle_files WHERE bundle=?) f '
                'ON f.document=d.id', (bundle,))]
            modules = _module_rows(db, bundle)
            page_lengths = {(row['document'], int(row['number'])): int(row['length'] or 0)
                            for row in db.execute(
                                'SELECT p.document,p.number,length(p.content) AS length FROM pages p '
                                'WHERE EXISTS(SELECT 1 FROM bundle_files f WHERE f.bundle=? AND f.document=p.document)',
                                (bundle,))}
            jobs = [dict(row) for row in db.execute(
                'SELECT id,document,purpose,status,done,total,message,created FROM jobs '
                'WHERE bundle=? ORDER BY created DESC', (bundle,))]
            definition = db.execute('SELECT body,revision FROM definitions WHERE bundle=?', (bundle,)).fetchone()
            saved = _json(definition['body'], {}) if definition else None
        finally:
            db.rollback()

    total_documents = len(documents)
    extracted = sum(1 for row in documents if row.get('status') == 'extracted')
    blank_pages = 0
    for row in documents:
        quality = _json(row.get('quality'), {}) or {}
        try:
            blank_pages += int(quality.get('blank_pages') or 0)
        except (TypeError, ValueError):
            blank_pages += 0
    episodes = [row for row in modules if row['kind'] != 'appendix']
    appendices = [row for row in modules if row['kind'] == 'appendix']
    indexed = sum(1 for row in modules if row['indexed'])
    start, start_problem = _start_of(modules, page_lengths)
    start_counts = _numbers(start['scene']) if start else dict(locations=0, npcs=0, checks=0)

    # Строгие инструкции книги — отдельный факт: одна сохранённая и сверенная
    # запись событий на комплект. Она не делает модули проверенными и наоборот.
    instructions = bool(saved and saved.get('reviewed') and not saved.get('unsupported'))

    done = {
        'upload': total_documents > 0,
        'extract': bool(total_documents) and extracted == total_documents,
        'index': bool(modules) and indexed == len(modules),
        'prepare': bool(start) and bool(start_counts['locations']) and start['indexed'],
        'validate': bool(start) and not start_problem,
        'playable': bool(start) and not start_problem,
    }
    steps = []
    current = None
    for identifier, title in STEPS:
        if done[identifier]:
            state = 'done'
        elif current is None:
            state = current = 'current'
        else:
            state = 'pending'
        steps.append(dict(id=identifier, title=title, state=state,
                          detail=_step_detail(identifier, dict(
                              documents=total_documents, extracted=extracted, pages=sum(
                                  int(row.get('pages') or 0) for row in documents),
                              modules=len(modules), episodes=len(episodes), appendices=len(appendices),
                              indexed=indexed, boundaries=sum(1 for row in modules if row['boundaries']),
                              blank_pages=blank_pages, start=start, start_problem=start_problem,
                              start_counts=start_counts, instructions=instructions))))

    start_ready = done['validate']
    book_ready = done['extract'] and done['index'] and bool(episodes) \
        and all(row['ready'] for row in episodes)

    warnings = _warnings(documents=documents, total_documents=total_documents, extracted=extracted,
                         jobs=jobs, blank_pages=blank_pages, modules=modules, episodes=episodes,
                         appendices=appendices, indexed=indexed, start=start,
                         start_problem=start_problem, instructions=instructions)

    counts = dict(documents=total_documents, extracted=extracted,
                  pages=sum(int(row.get('pages') or 0) for row in documents),
                  modules=len(modules), episodes=len(episodes), appendices=len(appendices),
                  indexed=indexed, boundaries=sum(1 for row in modules if row['boundaries']),
                  blank_pages=blank_pages, locations=sum(_numbers(row['scene'])['locations'] for row in modules),
                  npcs=sum(_numbers(row['scene'])['npcs'] for row in modules),
                  checks=sum(_numbers(row['scene'])['checks'] for row in modules))
    revision = int(book['revision'] or 0)
    metadata = _json(book['metadata'], {}) or {}
    saved_body = saved or {}
    headline, status = _headline(counts, steps, start, start_problem, start_ready, book_ready, instructions)
    start_row = None if start is None else dict(
        id=start['id'], title=start['title'], page=start['first_page'],
        document=start['document'], boundary=start['last_page'],
        **start_counts)
    return dict(bundle=book['id'], title=book['title'], revision=revision,
                metadata=metadata, jobs=jobs,
                ruleset=saved_body.get('ruleset'), ruleset_version=saved_body.get('ruleset_version'),
                import_complete=bool(total_documents and extracted == total_documents),
                stage=next((row['id'] for row in steps if row['state'] == 'current'), 'playable'),
                steps=steps, counts=counts, start=start_row, start_problem=start_problem,
                start_ready=start_ready, book_ready=book_ready,
                instructions_checked=instructions, headline=headline, status=status,
                warnings=warnings, modules=modules)


def _step_detail(identifier, facts):
    start = facts['start']
    if identifier == 'upload':
        return 'Источников: ' + str(facts['documents'])
    if identifier == 'extract':
        return 'Извлечено ' + str(facts['extracted']) + ' из ' + str(facts['documents']) \
            + ', страниц ' + str(facts['pages'])
    if identifier == 'index':
        detail = 'Модулей ' + str(facts['modules']) + ': эпизодов ' + str(facts['episodes'])
        if facts['appendices']:
            detail += ', приложений ' + str(facts['appendices'])
        return detail + '; размечено ' + str(facts['indexed'])
    if identifier == 'prepare':
        if facts['start_problem']:
            return facts['start_problem']
        if not start:
            return facts['start_problem']
        counts = facts['start_counts']
        detail = 'Стр. ' + str(start['first_page']) + ': мест ' + str(counts['locations']) \
            + ', людей ' + str(counts['npcs']) + ', проверок ' + str(counts['checks'])
        return detail
    if identifier == 'validate':
        return 'Стартовая сцена сверена со страницей ' + str(start['first_page']) \
            if start and not facts['start_problem'] else facts['start_problem']
    return 'Готов старт' if start and not facts['start_problem'] else facts['start_problem']


def _headline(counts, steps, start, start_problem, start_ready, book_ready, instructions):
    """Одна строка статуса. Никаких двух чисел и двух «проверено» рядом."""
    if not counts['documents']:
        return ('Комплект пуст: источников нет. Загрузите книгу, чтобы начать.',
                'catalog_only')
    if counts['extracted'] < counts['documents']:
        return ('Импорт не завершён: извлечено ' + str(counts['extracted']) + ' из '
                + str(counts['documents']) + ' источников.', 'import_incomplete')
    if not counts['modules']:
        return ('Текст извлечён, но книга не нарезана на эпизоды: играть пока нельзя.',
                'documents_imported')
    if not start_ready:
        return ('Играть пока нельзя: ' + start_problem.rstrip('.') + '.', 'requires_review')
    if book_ready:
        return ('Можно играть и готова вся книга: эпизодов ' + str(counts['episodes']) + '.',
                'instructions_reviewed' if instructions else 'requires_review')
    pending = sum(1 for row in steps if row['state'] != 'done')
    return ('Можно играть: готов старт, эпизод «' + str(start['title'] or start['id'])
            + '» со страницы ' + str(start['first_page']) + '. Вся книга ещё не готова — '
            + str(pending) + ' шаг(а) впереди.', 'requires_review')


def _warning(code, severity, text, action, **extra):
    if action not in ACTIONS:
        raise ValueError('У предупреждения нет действия: ' + code)
    return dict(code=code, severity=severity, text=text, action=action, **extra)


def _warnings(*, documents, total_documents, extracted, jobs, blank_pages, modules, episodes,
              appendices, indexed, start, start_problem, instructions):
    rows = []
    failed = [row for row in jobs if row.get('status') == 'failed']
    if failed:
        row = failed[0]
        rows.append(_warning('job_failed', 'error',
                             'Извлечение не завершилось: ' + str(row.get('message') or 'причина не записана'),
                             'retry', job=row.get('id')))
    if total_documents and extracted < total_documents:
        rows.append(_warning('extract_incomplete', 'error',
                             'Извлечено ' + str(extracted) + ' из ' + str(total_documents)
                             + ' источников: текст прочитан не целиком.', 'retry'))
    if blank_pages:
        rows.append(_warning('blank_pages', 'warning',
                             str(blank_pages) + ' страниц без уверенного текста: часть книги Мастер не прочитал.',
                             'details', pages=blank_pages))
    if not modules and total_documents:
        rows.append(_warning('modules_missing', 'error',
                             'Книга не разрезана на эпизоды: границы глав не найдены.', 'fix'))
    unindexed = len(modules) - indexed
    if unindexed:
        rows.append(_warning('scenes_missing', 'error',
                             str(unindexed) + ' модулей без размеченной сцены: места и проверки не перенесены.', 'fix',
                             count=unindexed))
    unchecked = [row for row in episodes if not row['boundaries']]
    if unchecked:
        rows.append(_warning('boundaries_unchecked', 'warning',
                             'Границы не сверены у ' + str(len(unchecked)) + ' эпизодов.', 'fix',
                             count=len(unchecked), modules=[row['id'] for row in unchecked[:20]]))
    unsupported = [row for row in modules if row['unsupported'] and row['kind'] != 'appendix']
    for row in unsupported[:5]:
        rows.append(_warning('module_unsupported', 'error',
                             'Эпизод «' + (row['title'] or row['id']) + '»: ' + '; '.join(row['unsupported']),
                             'details', module=row['id']))
    for row in appendices[:5]:
        rows.append(_warning('appendix', 'info',
                             'Приложение книги «' + (row['title'] or row['id'])
                             + '» — справочные страницы; старт отсюда не начинается, но места в нём есть.',
                             'details', module=row['id'], locations=_numbers(row['scene'])['locations']))
    if start_problem and modules and total_documents and extracted == total_documents:
        rows.append(_warning('start_invalid', 'error', start_problem, 'fix',
                             module=(start or {}).get('id')))
    elif start:
        missing = [row for row in start['requirements'] if row.get('kind') in ('minimum_level', 'recommended_party')]
        if missing:
            parts = []
            for row in missing:
                if row['kind'] == 'minimum_level':
                    parts.append(str(row['value']) + '-й уровень')
                else:
                    parts.append('группа из ' + str(row['value']) + (', до ' + str(row['maximum'])
                                                                    if row.get('maximum', row['value']) != row['value'] else ''))
            page = next((row.get('page') for row in missing if row.get('page')), None)
            rows.append(_warning('level_requirement', 'warning',
                                 'Книга рассчитана на ' + ' и '.join(parts)
                                 + (' (стр. ' + str(page) + ')' if page else '')
                                 + '. Это условие книги, а не требование движка.', 'details',
                                 page=page, requirements=missing))
    if total_documents and not instructions:
        rows.append(_warning('instructions_missing', 'info',
                             'Строгие инструкции книги (события и таблицы) не проверены: '
                             'старт откроет первую страницу эпизода, а не сценарий событий.', 'fix'))
    return rows


def summary(report):
    """Короткая часть для карточки: без сцен, но с теми же числами."""
    return dict(revision=report['revision'], stage=report['stage'], steps=report['steps'],
                counts=report['counts'], start=report['start'], start_ready=report['start_ready'],
                # Причина, по которой старт не открыт, живёт рядом с флагом: карточка
                # печатает «Старт не готов» и тут же — почему, без второго запроса.
                start_problem=report['start_problem'],
                book_ready=report['book_ready'], headline=report['headline'],
                status=report['status'], warnings=report['warnings'],
                instructions_checked=report['instructions_checked'])
