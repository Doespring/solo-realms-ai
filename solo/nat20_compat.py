"""Совместимость кампании Nat20 со старой записью и прежним обработчиком.

Две старые кампании не игрались по двум разным причинам, и обе всплывали при
входе в игру:

* ``world.events`` — события прежнего мирового обработчика. Список читался, но
  новый цикл их не исполнял, и отказ «не поддерживаются» запирал КАЖДОЕ
  действие (снимки S61/S63: «Тест Тиамат 08.11»).
* сохранённое определение кампании перестало совпадать с проверенной
  регистрацией после того, как авторская сцена получила разметку местности
  (r206). Отказ приходил английским текстом и валил экран целиком (S64:
  «Проверка стенда r192»).

Здесь три вещи: чтение старых событий как исторических (``world_events``),
проверка совместимости ДО ввода (``inspect``) и миграция на копии профиля
(``migrate``). События не удаляются: они переезжают в именованный архив, из
которого адаптер читает их и после обновления. Миграция меняет только то, что
названо в отчёте, и пишет журнальное событие «до/после».
"""
import copy
import hashlib
import json
import time

SUPPORTED_CONTRACT = ('nat20-state-v1',)
SUPPORTED_DEFINITIONS = ('nat20-campaign-v1', 'nat20-experimental-v1')
EVENTS_NONE = 'none'
EVENTS_LIVE = 'world-events-v1'
EVENTS_LEGACY = 'world-events-list-v0'
EVENTS_SCHEDULER = 'world-scheduler-v0'
ARCHIVE_KEY = 'legacy_events'
SCHEDULE_KEY = 'legacy_scheduler'

#: Три действия, которые экран предлагает при несовпадении. Одни и те же на
#: сервере и в браузере: подпись приходит отсюда, экран не придумывает свою.
ACTIONS = (
    dict(id='migrate', label='Сделать копию и обновить',
         hint='Сначала снимок профиля в backups, потом кампания переходит на текущую версию. '
              'История и герой сохраняются.'),
    dict(id='verify', label='Проверить базу',
         hint='Целостность SQLite и число записей истории. Кампанию не меняет.'),
    dict(id='export', label='Экспортировать кампанию',
         hint='Файл JSON со всей историей и состоянием. Работает при любом исходе.'),
)


def _short(value, limit=300):
    return str(value if value is not None else '')[:limit]


def _mapping(value):
    """Словарь или пустой: сохранение может прийти любой формы."""
    return value if isinstance(value, dict) else {}


def _event_items(raw):
    """Разложить записи событий на три уровня — формулировка отказа зависит от него.

    * ``readable`` — событие разбирается моделью ``WorldEvent``;
    * ``partial`` — id и текст есть, но модель не берёт (незнакомый ``kind``,
      лишние поля): показать игроку можно, исполнить всё равно не будем —
      это НЕ запирает кампанию (критик A02: ``kind='weather'`` запирал навсегда);
    * ``broken`` — нет даже id/текста: показать нечего, это замок с русской
      причиной, но миграция-архивация при этом разрешена.
    """
    from .world import WorldEvent
    readable, partial, broken = [], [], []
    for event in raw or ():
        try:
            WorldEvent.model_validate(event)
        except Exception:  # noqa: BLE001 - любая негодная запись = «не разбирается»
            displayable = isinstance(event, dict) and event.get('id') and (event.get('text') or event.get('title'))
            (partial if displayable else broken).append(event)
            continue
        readable.append(event)
    return readable, partial, broken


def world_events(state):
    """Мировые события кампании: современные, исторические или живой планировщик.

    Формат берётся из ``world.event_format``. Метка ``world-events-v1`` —
    события подготовленного сценария: их исполняет ``world.tick`` как раньше.
    Метки нет (или стоит v0) — запись прежнего обработчика: адаптер читает её
    как историю, а ``world.tick`` для кампании Nat20 такие события не исполняет.
    Словарь в ``world.events`` — очередь того же обработчика: если в ней остались
    ``pending``/``scheduled`` (или непустое расписание), очередь ещё живая, и
    молча её перепрыгивать нельзя.
    """
    state = state or {}
    world = _mapping(state.get('world'))
    events = world.get('events')
    archive = _mapping(world.get(ARCHIVE_KEY))
    scheduled = _mapping(world.get(SCHEDULE_KEY))
    triggered = {str(item) for item in (world.get('triggered') or ())}
    format = world.get('event_format')

    if format == EVENTS_LIVE and isinstance(events, list):
        readable, partial, broken = _event_items(events)
        items = [dict(id=_short(event.get('id'), 120), title=_short(event.get('text'), 200),
                      kind=_short(event.get('kind'), 40), location=_short(event.get('location'), 120),
                      happened=str(event.get('id')) in triggered) for event in readable]
        items += [dict(id=_short(event.get('id'), 120), title=_short(event.get('text'), 200),
                       kind=_short(event.get('kind'), 40), location=_short(event.get('location'), 120),
                       happened=str(event.get('id')) in triggered, executable=False) for event in partial]
        blocked = bool(partial or broken)
        return dict(format=EVENTS_LIVE, historical=False, blocking=blocked, count=len(items),
                    triggered=sum(1 for item in items if item['happened']), items=items,
                    broken=len(broken), partial=len(partial),
                    reason=('События сцены в текущем формате, но %d из них не разбираются: '
                            'действие не проходит — выгрузите историю и покажите кампанию разработчику.'
                            % (len(partial) + len(broken))) if blocked else '')

    queue = events if isinstance(events, dict) else {}
    archived_queue = _mapping(scheduled.get('events'))
    # Живая очередь — любая запись, которая ещё не закрыта. Незнакомый формат
    # значения (словарь вместо статуса) — тоже «ждёт хода», иначе очередь
    # молча исчезала бы с экрана (критик A02).
    pending = sorted(str(key) for key, status in queue.items() if str(status) != 'resolved')
    for key in sorted(_mapping(world.get('schedule'))):
        if str(key) not in pending:
            pending.append(str(key))
    if pending:
        total = len(queue) or len(_mapping(world.get('schedule')))
        return dict(format=EVENTS_SCHEDULER, historical=False, blocking=True, count=total,
                    triggered=sum(1 for status in queue.values() if str(status) == 'resolved'), items=[],
                    pending=pending,
                    reason=('Очередь мировых событий прежнего обработчика не пуста (%d из %d ждут хода), '
                            'а новый цикл её не исполняет. Очередь не удалена: обновите кампанию на копии '
                            'или выгрузите её только для чтения.' % (len(pending), max(total, len(pending)))))

    raw = []
    if isinstance(events, list):
        raw.extend(events)
    raw.extend(archive.get('events') if isinstance(archive.get('events'), list) else [])
    readable, partial, broken = _event_items(raw)
    items = []
    for event in readable + partial:
        identity = _short(event.get('id'), 120)
        items.append(dict(id=identity, title=_short(event.get('text'), 200) or identity,
                          kind=_short(event.get('kind'), 40), location=_short(event.get('location'), 120),
                          happened=identity in triggered,
                          **({'executable': False} if event in partial else {})))
    # Архив очереди после обновления: статусы остаются историей, а не исчезают с
    # экрана (критик A02: world_events читал только legacy_events).
    for key, status in archived_queue.items():
        items.append(dict(id=_short(key, 120), title='Событие прежней очереди: ' + str(key),
                          kind='scheduler', location='', happened=str(status) == 'resolved'))
    fired = sum(1 for item in items if item['happened'])
    if broken:
        return dict(format=EVENTS_LEGACY, historical=True, blocking=True, count=len(raw), triggered=fired,
                    items=items, broken=len(broken), partial=len(partial),
                    reason=('В кампании %d мировых событий прежнего обработчика, из них %d без описания: '
                            'их нельзя даже показать. Обновление кампании на копии уберёт их в архив — '
                            'история читается и выгружается.' % (len(raw), len(broken))))
    if not items:
        return dict(format=EVENTS_NONE, historical=False, blocking=False, count=0, triggered=0,
                    items=[], reason='')
    note = (' %d записей не разбираются движком — они показаны как есть.' % len(partial)) if partial else ''
    return dict(format=EVENTS_LEGACY, historical=True, blocking=False, count=len(items), triggered=fired,
                items=items, broken=0, partial=len(partial),
                reason=('События прежнего обработчика (%d) прочитаны как исторические: %d из них уже '
                        'сработали и записаны в истории, остальные этим циклом не исполняются. '
                        'Действия героя они больше не запирают.' % (len(items), fired)) + note)


def blocking_world_events(state):
    """Причина отказа для ``_preflight`` или None, если события не мешают ходу.

    Прежний код отказывал на ЛЮБОЙ непустой ``world.events`` и тем запирал
    кампанию, которую можно было играть. Теперь отказ остаётся только там, где
    запись действительно нельзя прочитать или где очередь ещё живая.
    """
    events = world_events(state)
    if not events['blocking']:
        return None
    return dict(code='world_events_' + ('unreadable' if events.get('broken') else 'pending'),
                reason=events['reason'])


def registration(definition):
    """Подпись регистрации: совпадает ли сохранённое определение с проверенным.

    Определение в старом сохранении может быть не словарём вовсе. Тогда это не
    «совпадает», а «сверить нельзя»: причина русская, поле названо, экран жив.
    """
    if definition is None:
        return dict(ok=True, code='none', detail='Авторского определения нет: сверять нечего.')
    if not isinstance(definition, dict):
        return dict(ok=False, code='invalid_form',
                    detail='Определение кампании в сохранении записано не объектом ('
                           + type(definition).__name__ + '): сверить его с проверенной регистрацией '
                           'нельзя, поэтому доступны чтение истории и выгрузка.')
    from .nat20_campaign import DefinitionRefused, validate_definition
    from pydantic import ValidationError
    try:
        validate_definition(definition)
    except DefinitionRefused as error:
        return dict(ok=False, code=getattr(error, 'code', 'refused'), detail=str(error))
    except ValidationError as error:
        # Сырой pydantic-текст — английский и с путями полей: игроку он ни о чём
        # не говорит (найдено критиком A02). Называем русские поля.
        return dict(ok=False, code='invalid_form',
                    detail='Определение кампании не проходит проверку формы (поля: '
                           + ', '.join(_field_names(error)) + '): играть на нём нельзя. Обновление на копии '
                           'перепривяжет определение к проверенной регистрации; история и герой целы.')
    except ValueError as error:
        return dict(ok=False, code='refused', detail=str(error))
    return dict(ok=True, code='registered', detail='Совпадает с проверенной регистрацией.')


_REVISIONS = {}


def revision(digest):
    """Ревизия содержимого движка, на которой собрана кампания.

    Проверка стоит ~0.3 с (пересчёт отпечатков дерева), поэтому ответ
    запоминается на процесс: экран открывают многократно, а ревизия у кампании
    одна. Непроверенная ревизия — это не «совпадает»: играть на правилах, которые
    сборка не подтвердила, нельзя. Причина русская: дефект сборки отличается от
    кампании, собранной на исчезнувшей ревизии.
    """
    digest = _short(digest, 64)
    if not digest:
        return dict(ok=False, code='missing', detail='В сохранении не записана ревизия содержимого: '
                                                    'правила, на которых шла кампания, не подтверждены. '
                                                    'Доступны чтение истории и выгрузка.')
    if digest in _REVISIONS:
        return dict(_REVISIONS[digest])
    try:
        from .nat20_content import release
        release(digest)
        result = dict(ok=True, code='pinned', detail='Совпадает с закреплённой ревизией содержимого.')
    except ValueError as error:
        broken_tree = 'SHA' in str(error) or 'verify' in str(error).lower() or 'differs' in str(error)
        result = (dict(ok=False, code='tree_broken',
                       detail='Ревизию сверить не удалось: закреплённое дерево содержимого не совпадает '
                              'с пиннингом. Это дефект сборки, а не кампании — но играть на непроверенных '
                              'правилах нельзя. Соберите дерево заново (`scripts/sync_engine.sh`), '
                              'пока доступны чтение истории и выгрузка.')
                  if broken_tree else
                  dict(ok=False, code='unknown_release',
                       detail='Ревизия содержимого ' + digest[:12] + ' не найдена среди закреплённых: '
                              'правила, на которых шла кампания, в этой сборке не воспроизводятся. '
                              'Доступны чтение истории и выгрузка.'))
    except Exception as error:  # noqa: BLE001 - проверка не имеет права валить экран
        result = dict(ok=False, code='unverified',
                      detail='Ревизию сверить не удалось (' + _short(error, 160) + '): '
                             'правила кампании не подтверждены. Доступны чтение истории и выгрузка.')
    _REVISIONS[digest] = dict(result)
    return dict(result)


def _field_names(error, limit=4):
    """Имена полей pydantic-ошибки без английской обвязки (валидаторы, типы).

    ``definition.function-after[scope(), CampaignDefinition]`` — это внутреннее
    имя проверки, а не поле, которое игрок может найти. Оставляем только
    простые сегменты пути: ``definition``.
    """
    names = []
    for item in error.errors()[:limit]:
        parts = [str(part) for part in (item.get('loc') or ()) if str(part).replace('_', '').isalnum()]
        name = '.'.join(parts) or 'состояние'
        if name not in names:
            names.append(name)
    return names


def _runtime_checks(state, runtime):
    """Те же гейты, что у ``Nat20GameEngine._bound``, но без запуска движка.

    Иначе экран говорит «совместима», а ввод падает 409-м сырым pydantic-текстом
    (критик A02: отсутствующий `exhaustion`, чужой набор правил, активный бой
    прежнего цикла). Замечания ТОЛЬКО к определению сценария сюда не относятся:
    их называет проверка регистрации, и миграция как раз переписывает это поле.
    Возвращает (checks, migratable).
    """
    from pydantic import ValidationError
    checks, migratable = [], True
    try:
        from .nat20_contracts import DurableState
        DurableState.model_validate(runtime)
    except ValidationError as error:
        paths = [tuple(str(part) for part in (item.get('loc') or ())) for item in error.errors()]
        only_definition = bool(paths) and all(path[:1] == ('definition',) for path in paths)
        if only_definition:
            checks.append(dict(id='state', label='Состояние движка', ok=True,
                               detail='Состояние читается; замечания — к регистрации сценария.'))
        else:
            checks.append(dict(id='state', label='Состояние движка', ok=False,
                               detail='Сохранённое состояние движка не проходит проверку формы (поля: '
                                      + ', '.join(_field_names(error)) + '): играть на нём нельзя, '
                                      'доступны чтение истории и выгрузка.'))
            migratable = False
    except Exception as error:  # noqa: BLE001 - проверка не валит экран
        checks.append(dict(id='state', label='Состояние движка', ok=False,
                           detail='Состояние движка проверить не удалось: ' + _short(error, 160)))
        migratable = False
    else:
        checks.append(dict(id='state', label='Состояние движка', ok=True,
                           detail='Состояние читается текущим циклом.'))
    try:
        from .rulesets import BUILTINS, active
        preset = active(state)
        if preset.model_dump() != BUILTINS['dnd2024'].model_dump():
            checks.append(dict(id='ruleset', label='Набор правил', ok=False,
                               detail='Кампания Nat20 закреплена за правилами D&D 2024: другой набор '
                                      'требует явной миграции. Откройте кампанию с D&D 2024 или '
                                      'перенесите её отдельной миграцией.'))
            migratable = False
        else:
            checks.append(dict(id='ruleset', label='Набор правил', ok=True, detail='D&D 2024.'))
    except Exception as error:  # noqa: BLE001
        checks.append(dict(id='ruleset', label='Набор правил', ok=False,
                           detail='Набор правил прочитать не удалось: ' + _short(error, 160)))
        migratable = False
    if (state.get('combat') or {}).get('active'):
        checks.append(dict(id='combat', label='Бой прежнего цикла', ok=False,
                           detail='В кампании идёт бой прежнего цикла: завершите его или откатите ход, '
                                  'прежде чем вести её движком Nat20.'))
        migratable = False
    return checks, migratable


def inspect(current):
    """Проверка совместимости ДО ввода: схема, состояние, правила, регистрация, ревизия.

    Возвращает готовый для экрана отчёт: ``status='ok'`` — играть можно,
    ``needs_choice`` — нужен выбор (обновить на копии / только чтение /
    экспорт). Функция тотальна: любая форма сохранения даёт отчёт, а не
    исключение, — иначе «экспорт при любом исходе» не выполняется.
    Ничего не пишет и ничего не меняет.
    """
    current = current if isinstance(current, dict) else {}
    state = _mapping(current.get('state'))
    runtime = state.get('nat20')
    events = world_events(state)
    native = isinstance(runtime, dict)
    # Действия экрана — по факту доступного. У записи без состояния движка
    # «Проверить базу» и «Обновить» отбиваются гейтом одного бэкенда, и показывать
    # их значит обещать кнопку, которая не работает (критик A02).
    actions = [action for action in ACTIONS if native or action['id'] == 'export']
    checks = []
    migratable = True

    if not native:
        checks.append(dict(id='schema', label='Схема состояния', ok=False,
                           detail='В кампании нет сохранённого состояния движка Nat20. '
                                  'Новый цикл её не открывает: доступна выгрузка истории, '
                                  'обновить такую запись нельзя.'))
        return dict(status='needs_choice', migratable=False, checks=checks, events=events,
                    actions=actions,
                    summary='Кампания сохранена без состояния движка: обновление невозможно, '
                            'история выгружается файлом.')

    contract = _short(runtime.get('contract_version'), 40)
    schema_ok = contract in SUPPORTED_CONTRACT
    checks.append(dict(id='schema', label='Схема состояния', ok=schema_ok,
                       detail=('Схема ' + contract + ' поддерживается текущим циклом.') if schema_ok else
                              ('Схема ' + (contract or 'не указана') + ' этим циклом не читается: '
                               'обновление невозможно, доступны чтение истории и выгрузка.')))
    migratable = migratable and schema_ok

    runtime_checks, runtime_ok = _runtime_checks(state, runtime)
    checks.extend(runtime_checks)
    migratable = migratable and runtime_ok

    definition = runtime.get('definition')
    definition_schema = _short(_mapping(definition).get('schema_version'), 60)
    if definition is not None and not isinstance(definition, dict):
        signed = registration(definition)
        checks.append(dict(id='registration', label='Регистрация сценария', ok=False,
                           detail=signed['detail'], code=signed['code']))
        migratable = False
    elif definition is not None and definition_schema not in SUPPORTED_DEFINITIONS:
        checks.append(dict(id='registration', label='Регистрация сценария', ok=False,
                           detail='Определение кампании записано в неизвестной схеме '
                                  '(' + (definition_schema or 'без версии') + '): сверить его с проверенной '
                                  'регистрацией нельзя, поэтому остаются чтение и выгрузка.'))
        migratable = False
    else:
        signed = registration(definition)
        checks.append(dict(id='registration', label='Регистрация сценария', ok=signed['ok'],
                           detail=signed['detail'], code=signed['code']))
        if not signed['ok']:
            # Перепривязать к текущей регистрации можно только авторское
            # определение: у собранного движком героя сцена строится из данных
            # книги, и «обновить» её значит собрать заново с нуля. Без id
            # перепривязывать нечего — только чтение и выгрузка (критик A02:
            # кнопка «обновить» отдавала 404 «id»).
            identity = _mapping(definition).get('id')
            has_id = isinstance(identity, str) and bool(identity.strip())
            registered = signed['code'] in ('registration_changed', 'invalid_form')
            migratable = migratable and registered and has_id and definition_schema == 'nat20-campaign-v1'

    world = revision(runtime.get('release_digest'))
    checks.append(dict(id='revision', label='Ревизия мира', ok=world['ok'],
                       detail=world['detail'], code=world['code']))
    migratable = migratable and world['ok']

    # Поля сцены, без которых экран не соберётся: иначе inspect скажет «ok»,
    # а project() упадёт KeyError (критик A02). Это дешёвая проверка, не движок.
    location = state.get('location')
    if not isinstance(location, str) or not location.strip() or not isinstance(state.get('locations'), list):
        checks.append(dict(id='scene', label='Сцена', ok=False,
                           detail='В сохранении нет текущего места или списка мест: экран сцены не '
                                  'собрать. Доступны выгрузка истории и обновление на копии.'))
        migratable = False

    if events['blocking']:
        # Архивная запись (список или очередь) переносится без разбора событий,
        # поэтому обновление остаётся возможным даже там, где событие не читается.
        migratable = migratable and events['format'] in (EVENTS_SCHEDULER, EVENTS_LEGACY)
        checks.append(dict(id='events', label='Мировые события', ok=False, detail=events['reason']))

    failed = [check for check in checks if not check['ok']]
    if not failed:
        return dict(status='ok', migratable=False, checks=checks, events=events, actions=[],
                    summary='Кампания совместима с текущим циклом.')
    return dict(status='needs_choice', migratable=migratable, checks=checks, events=events,
                actions=actions,
                summary=' '.join(check['detail'] for check in failed))


def apply_migration(state, report=None):
    """Переносит кампанию на текущий цикл. Меняет только названное в отчёте.

    Старые события не удаляются: список переезжает в ``world.legacy_events``
    (с отметками о срабатывании), и адаптер читает его оттуда. Живая очередь
    прежнего обработчика уходит в ``world.legacy_scheduler`` тем же способом.
    Повторное обновление ДОПИСЫВАЕТ архив, а не затирает его (критик A02: вторая
    миграция теряла первый архив). События текущего формата (v1) не трогаются:
    их исполняет ``world.tick``, и объявлять их историей нельзя.
    Определение кампании перепривязывается к проверенной регистрации.
    """
    report = report or inspect(dict(state=state))
    state = _mapping(state)
    changes = []
    runtime = _mapping(state.get('nat20'))
    definition = runtime.get('definition')
    signed = next((check for check in report['checks'] if check['id'] == 'registration'), None)
    if isinstance(definition, dict) and signed and not signed['ok']:
        if definition.get('schema_version') != 'nat20-campaign-v1':
            raise ValueError('Определение этой кампании собрано движком из книги: перепривязать его '
                             'к текущей регистрации нельзя, нужна новая сборка кампании.')
        identity = definition.get('id')
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('У определения кампании нет идентификатора: перепривязать его к регистрации '
                             'нельзя. Доступны чтение истории и выгрузка файлом.')
        from .nat20_campaign import load_definition
        fresh = load_definition(identity).model_dump(mode='json')
        before_sha = hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        after_sha = hashlib.sha256(json.dumps(fresh, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        runtime['definition'] = fresh
        state['nat20'] = runtime
        changes.append(dict(kind='registration', label='Регистрация сценария',
                            before=before_sha[:16], after=after_sha[:16],
                            text='Определение «' + str(definition.get('id')) + '» перепривязано к текущей '
                                 'проверенной регистрации; герой, опыт и история не тронуты.'))

    world = _mapping(state.get('world'))
    events = world.get('events')
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if isinstance(events, list) and events and world.get('event_format') != EVENTS_LIVE:
        archive = _mapping(world.get(ARCHIVE_KEY))
        kept = list(archive.get('events') or [])
        known = {str(item.get('id')) for item in kept if isinstance(item, dict)}
        added = [event for event in events if not isinstance(event, dict) or str(event.get('id')) not in known]
        merged = kept + copy.deepcopy(added)
        world[ARCHIVE_KEY] = dict(archived=archive.get('archived') or stamp,
                                  updated=stamp, source='прежний обработчик (world.events)',
                                  triggered=sorted({str(item) for item in (world.get('triggered') or ())}
                                                   | {str(item) for item in (archive.get('triggered') or ())}),
                                  events=merged)
        world['event_format'] = EVENTS_LEGACY
        world['events'] = []
        state['world'] = world
        changes.append(dict(kind='events', label='Мировые события',
                            before=str(len(events)), after='0 в очереди, ' + str(len(merged)) + ' в архиве',
                            text='События прежнего обработчика (' + str(len(events)) + ') убраны из очереди '
                                 'в архив legacy_events: ни одно событие не удалено' +
                                 (', прежний архив сохранён (' + str(len(kept)) + ')' if kept else '') +
                                 ', но и ходом они больше не управляют.'))
    elif isinstance(events, dict) and events:
        scheduled = _mapping(world.get(SCHEDULE_KEY))
        merged = dict(scheduled.get('events') or {})
        merged.update(copy.deepcopy(events))
        world[SCHEDULE_KEY] = dict(archived=scheduled.get('archived') or stamp, updated=stamp,
                                   source='прежний обработчик (world.events)', events=merged,
                                   schedule=copy.deepcopy(_mapping(world.get('schedule'))))
        world['event_format'] = EVENTS_SCHEDULER
        world['events'] = {}
        state['world'] = world
        changes.append(dict(kind='events', label='Мировые события',
                            before=str(len(events)), after='0 в очереди, ' + str(len(merged)) + ' в архиве',
                            text='Очередь прежнего обработчика (' + str(len(events)) + ' записей) убрана '
                                 'в архив legacy_scheduler: статусы сохранены, очередь больше не ждёт хода.'))
    # Расписание прежнего обработчика переносится в архив независимо от формы
    # событий: список событий + непустой world.schedule раньше давал вечный замок
    # (миграция «проходила», а проверка снова видела живую очередь). Критик A02.
    schedule = _mapping(world.get('schedule'))
    if schedule:
        scheduled = _mapping(world.get(SCHEDULE_KEY))
        merged = dict(scheduled.get('events') or {})
        for key in schedule:
            merged.setdefault(str(key), 'scheduled')
        world[SCHEDULE_KEY] = dict(archived=scheduled.get('archived') or stamp, updated=stamp,
                                   source='прежний обработчик (world.schedule)', events=merged,
                                   schedule=copy.deepcopy(schedule))
        world['schedule'] = {}
        state['world'] = world
        changes.append(dict(kind='schedule', label='Расписание мира',
                            before=str(len(schedule)), after='0 в расписании, ' + str(len(schedule)) + ' в архиве',
                            text='Расписание прежнего обработчика (' + str(len(schedule)) + ' записей) убрано '
                                 'в архив legacy_scheduler: статусы сохранены, новый цикл его не ждёт.'))
    if not changes:
        raise ValueError('В кампании нет записи прежнего обработчика, которую можно перенести: '
                         'остались проверки, которые обновление не исправляет. Смотрите причины выше; '
                         'доступны чтение истории и выгрузка.')
    return changes


def measurement(store, campaign):
    """Слепок «до/после» для отчёта о миграции."""
    current = store.campaign(campaign)
    with store.connection() as db:
        counts = {name: db.execute('SELECT COUNT(*) FROM ' + name + ' WHERE campaign=?', (campaign,)).fetchone()[0]
                  for name in ('events', 'actions', 'gm_messages', 'conversations')}
    from .user_data import sqlite_health
    health = sqlite_health(store.path)
    return dict(version=current['version'], counts=counts,
                state_sha256=hashlib.sha256(json.dumps(current['state'], sort_keys=True,
                                                       ensure_ascii=False).encode()).hexdigest(),
                integrity=health['integrity'], broken_links=len(health['foreign_keys']),
                foreign_keys=health['foreign_keys'],
                compatibility=inspect(current))


def _record_migration(store, campaign, planned, text, expected_version=None):
    """Журнальное событие обновления: тот же журнал, что у хода, без хода героя.

    Своя запись, а не ``record_scenario``: тот зовёт ``world.tick`` и исполнил бы
    события прежнего обработчика прямо во время обновления.

    ``expected_version`` — версия, прочитанная ДО копии и проверки. Если за это
    время кампанию изменили, UPDATE не найдёт строку, и обновление откатывается
    транзакцией: чужая запись не теряется (критик A02).
    """
    from .contracts import Plan
    from .storage import uid, dump
    with store.connection(True) as db:
        current = store.campaign(campaign, db)
        if expected_version is not None and current['version'] != expected_version:
            raise ValueError('Кампанию изменили, пока шло обновление: ничего не записано. '
                             'Повторите обновление — оно снова снимет копию и покажет разницу.')
        before = dump(current['state'])
        action, event = uid(), uid()
        request = 'compat-migrate-' + event[:8]
        summary = text[:400]
        plan = Plan(summary=summary, steps=[dict(kind='other', text=summary)])
        metrics = {'route': 'compatibility', 'llm': [], 'engine_signature': None,
                   'ruleset': {'id': 'dnd2024', 'version': 1}}
        db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',
                   (action, campaign, request, summary, plan.model_dump_json(), 'applied',
                    current['version'], dump(metrics), time.time()))
        db.execute('INSERT INTO events(id,action,campaign,before_state,after_state,results,created) '
                   'VALUES(?,?,?,?,?,?,?)',
                   (event, action, campaign, before, dump(planned),
                    dump([dict(type='scripted', text=text)]), time.time()))
        if expected_version is None:
            db.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?', (dump(planned), campaign))
        else:
            changed = db.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=? AND version=?',
                                 (dump(planned), campaign, expected_version)).rowcount
            if changed != 1:
                raise ValueError('Кампанию изменили, пока шло обновление: ничего не записано. '
                                 'Повторите обновление.')
        store.memory.ingest(db, event)
        return store.event_for_action(action, db)


def _snapshot(store, label='before-a02-migration'):
    """Снимок базы кампаний до первой записи: SQLite backup API, только чтение.

    Своя копия, а не ``user_data.backup``: тот перечисляет известные имена файлов
    профиля, а здесь важно снять именно ту базу, которую открыл Store (у тестов
    и проб она называется иначе). Рядом кладётся snapshot.json и, если есть
    находки, integrity-report.json — как у копий профиля A01.
    """
    import sqlite3
    import uuid
    from contextlib import closing
    from .user_data import REPORT_NAME, sqlite_health
    folder = store.path.parent / 'backups' / (
        label + '-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    target = folder / store.path.name
    try:
        with closing(sqlite3.connect('file:' + str(store.path.resolve()) + '?mode=ro', uri=True)) as source, \
                closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        health = sqlite_health(target)
        (folder / 'snapshot.json').write_text(json.dumps(dict(
            created=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), source=str(store.path),
            files=[target.name], health={target.name: health}), ensure_ascii=False, indent=2),
            encoding='utf-8')
        if not health['ok']:
            (folder / REPORT_NAME).write_text(json.dumps(dict(
                created=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), copy=folder.name,
                summary=('Целостность ' + health['integrity'] + '; нарушенных связей: '
                         + str(len(health['foreign_keys'])) + '.'), files={target.name: health}),
                ensure_ascii=False, indent=2), encoding='utf-8')
    except BaseException:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def migrate(store, campaign, *, backup=True, request_id=None):
    """Обновление кампании: сначала копия базы, потом изменение, потом сверка.

    ``backup`` кладёт снимок базы кампаний в ``<data>/backups`` до первой записи.
    Изменение идёт одним журнальным событием, поэтому у игрока есть и запись
    «до/после», и откат существующими средствами.
    """
    current = store.campaign(campaign)
    report = inspect(current)
    if report['status'] == 'ok':
        return dict(status='ok', migrated=False, report=report,
                    message='Кампания уже совместима с текущим циклом: обновление не нужно.')
    if not report['migratable']:
        raise ValueError('Кампанию нельзя обновить автоматически. ' + report['summary'] +
                         ' Доступны чтение истории и выгрузка (кнопка «Экспортировать кампанию»).')
    before = measurement(store, campaign)
    folder = _snapshot(store) if backup else None
    planned = copy.deepcopy(current['state'])
    changes = apply_migration(planned, report)
    planned_nat20 = _mapping(_mapping(planned).get('nat20'))
    if planned_nat20.get('definition') is not None and any(c['kind'] == 'registration' for c in changes):
        # Проверка плана движком ДО записи: несовместимое обновление не должно
        # оставить кампанию в состоянии, которое не открывается.
        from .nat20_api import Nat20GameEngine
        Nat20GameEngine._worker({'mode': 'verify', 'state': planned_nat20})
    text = 'Кампания обновлена до текущей версии: ' + '; '.join(change['text'] for change in changes)
    event = _record_migration(store, campaign, planned, text, expected_version=current['version'])
    after = measurement(store, campaign)
    problems = []
    if after['counts']['events'] < before['counts']['events']:
        problems.append('число записей истории уменьшилось: ' + str(before['counts']['events']) + ' → '
                        + str(after['counts']['events']))
    if after['integrity'] != 'ok':
        problems.append('целостность базы после обновления: ' + str(after['integrity']))
    result = dict(status='ok' if not problems else 'needs_choice', migrated=not problems,
                  campaign=campaign, event_id=event['id'] if isinstance(event, dict) else None,
                  backup=str(folder) if folder else None, changes=changes,
                  before=before, after=after, problems=problems,
                  history_kept=after['counts']['events'] >= before['counts']['events'],
                  integrity_ok=after['integrity'] == 'ok',
                  message=(text if not problems else 'Обновление записано, но проверка нашла: '
                           + '; '.join(problems) + '. Восстановите копию из backups.'))
    return result


def integrity(store, campaign):
    """Проверка базы и истории без единого изменения в кампании.

    ``PRAGMA integrity_check`` — жёсткий гейт (как в A01). Нарушенные внешние
    связи — находки в отчёте, а не отказ: они бывают наследством удалённых
    кампаний и к обновлению этой отношения не имеют. Поэтому они называются
    отдельно, с таблицами, и сравниваются до/после.
    """
    current = store.campaign(campaign)
    before = measurement(store, campaign)
    with store.connection() as db:
        rows = db.execute('SELECT seq,id,undone FROM events WHERE campaign=? ORDER BY seq', (campaign,)).fetchall()
    findings = before.get('foreign_keys') or []
    tables = sorted({item['table'] for item in findings})
    healthy = before['integrity'] == 'ok'
    if healthy:
        summary = ('Целостность базы в порядке; записей истории: ' + str(before['counts']['events']) + '.')
    else:
        # Раньше summary было «в порядке» при любом исходе — кнопка «Проверить
        # базу» показывала ложный положительный вывод (критик A02).
        summary = ('Целостность базы не подтверждена: ' + _short(before['integrity'], 200)
                   + '. Историю и состояние уже нельзя считать надёжными: снимайте копию профиля и '
                     'восстанавливайте её из последнего снимка в backups, прежде чем продолжать.')
    if findings:
        # Имена таблиц — не для экрана: игрок видит счёт и что это его не касается,
        # а перечень уходит в поле отчёта и в integrity-report.json копии профиля.
        own = sorted(set(tables) & {'events', 'actions', 'campaigns', 'gm_messages', 'conversations'})
        summary += ' Нарушенных внешних связей в других таблицах: ' + str(len(findings)) + '. '
        if own:
            summary += ('Среди них таблицы самой кампании (' + ', '.join(own) + '): история может быть '
                        'неполной. Снимите копию профиля и покажите её разработчику.')
        else:
            summary += ('История кампании и её герой не затронуты: находки в других таблицах, '
                        'перечень — в отчёте проверки копии профиля.')
    return dict(ok=healthy, campaign=campaign,
                version=current['version'], integrity=before['integrity'],
                broken_links=len(findings), broken_tables=tables,
                counts=before['counts'], last_event=dict(rows[-1]) if rows else None,
                compatibility=before['compatibility'], summary=summary)


def export(store, campaign):
    """Выгрузка кампании: состояние, вся история и вердикт совместимости.

    Работает при любом исходе, в том числе когда кампания не открывается:
    ничего не считает движком и ничего не меняет.
    """
    current = store.campaign(campaign)
    with store.connection() as db:
        rows = db.execute('SELECT e.id,e.seq,e.before_state,e.after_state,e.results,e.narrative,'
                          'e.narrative_status,e.error,e.undone,e.created,a.original,a.request_id '
                          'FROM events e JOIN actions a ON a.id=e.action WHERE e.campaign=? ORDER BY e.seq',
                          (campaign,)).fetchall()
        messages = db.execute('SELECT id,request_id,question,answer,status,created FROM gm_messages '
                              'WHERE campaign=? ORDER BY created', (campaign,)).fetchall()
    history = []
    for row in rows:
        item = dict(row)
        for key in ('before_state', 'after_state', 'results'):
            try:
                item[key] = json.loads(item[key])
            except (TypeError, ValueError):
                pass
        history.append(item)
    return dict(format='solo-campaign-export-v1',
                exported=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                campaign=dict(id=current['id'], name=current['name'], version=current['version'],
                              created=current['created']),
                compatibility=inspect(current), master=current['master'],
                state=current['state'], history=history,
                messages=[dict(row) for row in messages],
                counts=dict(events=len(history), messages=len(messages)))


def register_nat20_compat_api(app, store, game):
    """Маршруты экрана совместимости: чтение, проверка, обновление, выгрузка."""
    import asyncio
    from fastapi import HTTPException
    from fastapi.responses import Response

    def require(campaign):
        # Экран совместимости читает любую кампанию: у записи без состояния
        # движка inspect честно скажет «обновление невозможно», но история и
        # выгрузка должны работать — «экспорт при любом исходе».
        return store.campaign(campaign)

    @app.get('/api/campaigns/{campaign}/compat')
    def compat(campaign: str):
        return inspect(require(campaign))

    @app.post('/api/campaigns/{campaign}/compat/verify')
    async def verify(campaign: str):
        async with game.operation:
            require(campaign)
            return await asyncio.to_thread(integrity, store, campaign)

    @app.post('/api/campaigns/{campaign}/compat/migrate')
    async def update(campaign: str):
        async with game.operation:
            require(campaign)
            result = await asyncio.to_thread(migrate, store, campaign)
            if result.get('problems'):
                raise HTTPException(409, dict(code='migration_incomplete',
                                              message=result['message'], backup=result.get('backup'),
                                              before=result['before'], after=result['after']))
            return result

    @app.get('/api/campaigns/{campaign}/compat/export')
    def download(campaign: str):
        require(campaign)
        payload = export(store, campaign)
        name = 'campaign-' + str(campaign)[:8] + '.json'
        return Response(json.dumps(payload, ensure_ascii=False, indent=1), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="' + name + '"'})
