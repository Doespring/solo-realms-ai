"""Журнал действий владельца: что он нажал и что ответил сервер.

Зачем: после теста с телефона нужен точный след — какой шаг, какая кнопка, какой
запрос, какой код и какой текст ошибки. Без этого разбор превращается в угадывание
по скриншотам.

Две дороги в один файл:

* клиент (`web/telemetry.js`) присылает клики, шаги, ошибки JS и ответы API на
  `POST /api/telemetry` — источник `ui`;
* серверный middleware пишет каждый запрос сам — источник `http`.

Связка: клиент помечает все свои запросы заголовком `X-Solo-Sid`, middleware
складывает его в запись, поэтому «нажал → ушёл запрос → пришло 422» читается как
одна цепочка по времени.

Правила записи: тело запроса не пишем никогда (там могут быть пароли и описания),
значения полей ввода не пишем, ответы обрезаем, файл ротируем по 20 МБ.
"""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

#: Что не засоряет журнал: статика, проверка живости, сам приём событий.
SKIP_PREFIX = ('/static/', '/favicon.ico')
SKIP_EXACT = {'/api/health', '/api/telemetry'}

#: Предел одного файла и сколько старых держим рядом.
MAX_BYTES = 20 * 1024 * 1024
KEEP = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _rotate(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size >= MAX_BYTES:
            oldest = path.with_suffix(path.suffix + f'.{KEEP}')
            if oldest.exists():
                oldest.unlink()
            for index in range(KEEP - 1, 0, -1):
                src = path.with_suffix(path.suffix + f'.{index}')
                if src.exists():
                    src.rename(path.with_suffix(path.suffix + f'.{index + 1}'))
            path.rename(path.with_suffix(path.suffix + '.1'))
    except OSError:
        pass  # журнал не должен ронять приложение


def _write(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _clip(value, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


#: Куда пишет `conflict()`: ставится install(). До подключения журнала — None.
PATH: Path | None = None

#: Чей это запрос: браузер присылает `X-Solo-Sid`, автотест и curl — нет.
#: Ставится middleware на время запроса, поэтому отказ, записанный из движка или
#: экрана, тоже знает источник — иначе отказы автотестов считались поведением игрока.
_SID: ContextVar[str] = ContextVar('solo_sid', default='')


def source() -> str:
    """`browser` для запроса с клиентской сессией, иначе `script`."""
    return 'browser' if _SID.get() else 'script'


def conflict(campaign: str, revision, *, expected=None, route: str = '', answer: str = 'http_409') -> None:
    """Отказ по версии на живом ходе — отдельной строкой, с кампанией и ревизией.

    Соседняя http-строка знает только код ответа, поэтому «сколько раз игроку
    сказали, что сцена изменилась» приходилось вычитывать глазами и путать с
    отказами автотестов. Здесь есть и кампания, и ревизия, которую сервер считал
    актуальной, — такие случаи считаются числом. `answer` различает две формы
    одного отказа: HTTP 409 на действие экрана и тело 200 со `state_version_error`
    на текстовый ход.
    """
    if PATH is None:
        return
    record = {'ts': _now(), 'src': 'conflict', 'kind': 'version_conflict', 'answer': answer,
              'via': source(), 'campaign_id': campaign, 'revision': revision}
    if _SID.get():
        record['sid'] = _SID.get()[:16]
    if expected is not None:
        record['expected'] = expected
    if route:
        record['p'] = route
    _write(PATH, record)


def install(app, log_path: Path) -> None:
    """Подключить журнал к приложению. Зовётся один раз при сборке app."""
    global PATH
    path = Path(log_path)
    PATH = path

    @app.middleware('http')
    async def owner_actions(request, call_next):
        route = request.url.path
        if route in SKIP_EXACT or route.startswith(SKIP_PREFIX):
            return await call_next(request)
        started = time.perf_counter()
        status = 500
        failure = ''
        token = _SID.set(request.headers.get('x-solo-sid', '')[:16] or '')
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:  # noqa: BLE001 — пишем причину и пропускаем дальше
            failure = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            _SID.reset(token)
            record = {
                'ts': _now(),
                'src': 'http',
                'm': request.method,
                'p': route,
                'c': status,
                'ms': round((time.perf_counter() - started) * 1000),
            }
            sid = request.headers.get('x-solo-sid')
            if sid:
                record['sid'] = sid[:16]
            if failure:
                record['exc'] = _clip(failure, 600)
            if status >= 400:
                record['why'] = _clip(request.headers.get('referer', ''), 120)
                # Отказ игрока и отказ скрипта — разные строки: браузер присылает
                # X-Solo-Sid, автотест и curl — нет. Без этого признака 452 отказа
                # автотестов на живом профиле тонули и считались поведением игрока.
                record['via'] = source()
            _write(path, record)

    @app.post('/api/telemetry')
    async def telemetry(request: Request):
        """Приём событий браузера: клики, шаги, ответы API, ошибки JS."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — мусор от старой вкладки не должен падать
            return JSONResponse({'ok': False}, status_code=400)
        events = body.get('events') if isinstance(body, dict) else None
        if not isinstance(events, list):
            return JSONResponse({'ok': False}, status_code=400)
        header_sid = request.headers.get('x-solo-sid', '')[:16]
        for event in events[:80]:
            if not isinstance(event, dict):
                continue
            record = {'ts': _now(), 'src': 'ui'}
            if header_sid:
                record['sid'] = header_sid
            for key in ('type', 'label', 'step', 'path', 'url', 'status', 'ms', 'msg', 'nav'):
                value = event.get(key)
                if value not in (None, ''):
                    record[key] = _clip(value, 300) if isinstance(value, str) else value
            _write(path, record)
        return JSONResponse({'ok': True})
