#!/usr/bin/env python3
"""n07 — замер интерфейса приложения: 10 состояний × 2 ширины × 2 темы.

Что делает:
  • поднимает СВОЙ инстанс приложения (свой `--data-dir`, свободный порт, живой
    профиль владельца и порт 19215 не трогаются) и сеет три кампании через
    Nat20-движок, чтобы главная и игровые экраны были не пустыми;
  • поднимает headless Chromium с отдельным `--user-data-dir` и говорит с ним
    по CDP (приём из `~/.hermes/scripts/solo_shot.py`: `/json/list` →
    `Page.navigate` → `Emulation.setDeviceMetricsOverride` →
    `Page.captureScreenshot`; websockets в этом .venv нет, поэтому клиент WS —
    свой, на stdlib);
  • для каждого кадра снимает PNG в `reports/design-review/<состояние>-<тема>-<ширина>.png`
    и считает замеры: перелив по X, обрезку (`scrollWidth > clientWidth + 1`),
    минимальный кегль, тап-таргеты < 44, контраст текста и плейсхолдеров < 4.5,
    границы контролов < 3:1 (WCAG 1.4.11), число видимых интерактивных
    элементов, число разных кеглей, цвета вне `web/design-tokens.css`. Контраст
    считается с альфа-композицией вложенных фонов; что посчитать нельзя
    (градиент, картинка) — попадает в `contrastSkipped` с селектором;
  • пишет `reports/design-review/01-measures.json` и `01-report.md`.

Запуск:
    .venv/bin/python tools/ui_audit.py                 # все 40 кадров
    .venv/bin/python tools/ui_audit.py --only home,menu --widths 375 --themes dark
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME = Path.home() / '.cache/ms-playwright/chromium-1248/chrome-linux64/chrome'
CHROME_DEPS = '/home/hermes/.hermes/chromium-deps/usr/lib/x86_64-linux-gnu'
OUT = ROOT / 'reports/design-review'
DEFAULT_DATA = ROOT / 'runtime/n07-audit'

STATES = ['home', 'new', 'hero', 'prep', 'prep-log', 'game', 'game-kb', 'menu', 'menu-app', 'notes']
WIDTHS = [(375, 812), (1440, 900)]
THEMES = ['dark', 'light']
KB_HEIGHT = 336  # высота клавиатуры в эталоне design/mobile (кадр 812 → 476)

# ── фикстура подготовки книги (кадры prep / prep-log) ───────────────────────
# Свой короткий текст: пять глав, ни одного чужого фрагмента. Лежит в
# `runtime/<audit>/books/` и загружается тем же приёмником, что у игрока.
# Главы длиннее половины предела секции, поэтому каждая становится отдельным
# блоком плана: полосе подготовки есть что показывать (5 частей, не «1 из 1»).
PREP_BOOK_NAME = 'Дорога к маяку.txt'
PREP_BOOK = """
Глава 1. Соляной ветер

Дорога к маяку начиналась у соляных амбаров, где ветер снимал с досок белую
пыль и уносил её в канал. Утро было ясным, вода стояла высоко, и лодочник
Тима говорил, что такой день годится для долгого разговора, но не для
дальнего пути. Он вытер руки о передник и рассказал, что третью ночь подряд
на маяке горит не тот огонь: красный вместо белого, и видно его не с моря, а
со стороны солончаков. Смотритель будто бы заперся в башне и не отвечает на
стук. Старики в амбарах спорили: одни считали, что в башне поселилась
чужая команда, другие — что смотритель болен и жжёт всё, что попадается под
руку. Тима не верил ни тем, ни другим. Он верил счёту: за три ночи мимо
мыса прошли четыре лодки, и ни одна не подала условного знака. По его
словам, это значило, что кто-то нарочно гасит огонь и ждёт, пока течение
вынесет судно на камни у восточной отмели. Мы стояли у воды, и я слушал,
как под досками шевелится прилив. Тима добавил, что если я пойду вверх по
тропе, то найду у калитки старую медную лампу, оставленную для таких, как
я, — чтобы было чем ответить, если в башне спросят пароль. Пароля он не
знал и советовал не выдумывать: маячный народ не любит, когда гость
торопится говорить первым.

Глава 2. Смотритель

Тропа к башне была посыпана крупным песком и пахла смолой. У калитки
действительно висела лампа, но стёкла её были вымыты и заправлены маслом,
как будто её ждали. Дверь в башню оказалась приоткрыта на ладонь, и за ней
слышалось, как кто-то переставляет тяжёлые вещи. Смотритель появился на
второй ступени винтовой лестницы: седой, в вязаной куртке, с ключом на
шнурке. Он не удивился гостю и сразу сказал, что огонь он не гасил. Красный
свет — не сигнал тревоги и не приманка, а старая метка для рыбаков, которые
в тумане идут по отмели и считают вспышки вслух. Смотритель показал на
журнал, где за три ночи было записано одно и то же: "ветер юго-западный,
огонь красный, лодка без огней прошла в полумиле". Он считал, что лодка
возит контрабанду и хочет, чтобы её не видели с берега, а маяк ей только
мешает. Поэтому он и менял свет: пусть чужие думают, что на башне сломались
часы. Когда я спросил, почему он не позвал стражу, смотритель ответил
просто: стража стоит на другом берегу, а течение не ждёт. Внизу хлопнула
калитка, и он замолчал, прислушиваясь. Потом взял с полки вторую лампу и
протянул мне, сказав, что ночью огонь придётся держать на двоих.

Глава 3. Пустая лодка

К вечеру ветер сменился, и с моря пришёл туман. Мы вынесли на площадку
запасной фитиль и сели ждать. Около полуночи из тумана выплыла лодка без
огней: низкая, с одной парой вёсел, она шла вдоль берега так тихо, что
слышно было, как вода хлопает о борт. Смотритель взял лампу и трижды
прикрыл её ладонью. Лодка не ответила. Тогда он спустился к калитке, а я
остался наверху и видел, как двое в тёмных плащах вытащили на песок
 тяжёлый ящик и оглянулись на башню. В этот миг с юга донёсся короткий
свисток, и люди в плащах бросили ящик и ушли в туман, не оглядываясь. Мы
спустились и осмотрели находку: в ящике лежали мокрые канаты, крюки и
связка писем, перевязанная смолёной ниткой. Письма были написаны по-русски,
но без имён: только даты, номера причалов и слово "груз". Смотритель
прочитал одну страницу и покачал головой: это был не его спор и не его
война, но огонь на маяке теперь принадлежал нам обоим. Он велел отнести
ящик в башню и никому не рассказывать о свистке. Я согласился, хотя уже
понимал, что утром кто-то придёт за ящиком и спросит, кто его видел.

Глава 4. Ночной сигнал

Утром я не ушёл. Мы просушили письма у очага и разложили их по датам.
Выяснилось, что лодка ходила к восточной отмели каждую третью ночь и
забирала груз с затонувшего судна, которое лежало там с прошлой зимы.
Смотритель сказал, что о судне знали трое: он, старый лоцман и человек с
соляных амбаров. Лоцман умер зимой, а человек из амбаров как раз и приходил
утром — в сером плаще, с фонарём, хотя солнце уже поднялось. Он не стал
стучать, а вошёл без приглашения и сразу спросил про ящик. Смотритель
ответил, что ящик у него, и предложил сесть. Разговор шёл медленно, как
разговор двух людей, которые знают цену каждому слову. Человек из амбаров
говорил, что груз принадлежит не ему, а тем, кто ждёт его на том берегу, и
что маяк не должен мешать чужой работе. Смотритель отвечал, что маяк никому
не мешает, пока горит правильный огонь, и что ночью на отмели могли погибнуть
люди. Тогда гость положил на стол монету и сказал, что это за молчание.
Смотритель накрыл монету ладонью и подвинул её обратно. Он сказал, что за
молчание платят не монетой, а честным именем, и попросил гостя назвать
того, кто послал лодку. В башне стало тихо. Я слышал, как за окном бьётся
флаг, и ждал, что гость встанет и уйдёт.

Глава 5. Утренний отлив

Гость назвал имя. Это был не он и не лоцман, а торговец с южного причала,
который держал половину складов и никогда сам не выходил в море. Смотритель
кивнул, будто ждал этого с самого начала, и спрятал письма в железный
ящик под лестницей. Гость ушёл, оставив монету на столе, и больше мы его
не видели. К полудню туман поднялся, и с отмели пришла рыбацкая лодка:
двое рыбаков рассказали, что ночью видели красный огонь и обошли камни
стороной. Смотритель выслушал их молча, а потом велел мне подняться на
площадку и проверить механизм. Огонь горел ровно, часы шли, и в журнале
появилась новая строка: "ветер северный, огонь белый, всё спокойно".
Смотритель подписал её и передал мне перо. Я поставил рядом своё имя,
которого он так и не спросил. Внизу, у соляных амбаров, Тима уже снимал
парус с просушки и махал нам рукой. Я спустился по тропе, неся лампу, и
думал, что на маяке не бывает пустых ночей: есть только ночи, о которых
никто не рассказывает. Смотритель остался на площадке, и его фигура была
видна далеко над водой — маленькая, упрямая, на самом краю света.
"""



# ── минимальный клиент WebSocket (stdlib) ────────────────────────────────────
class WS:
    def __init__(self, url: str, timeout: float = 60.0):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (('?' + u.query) if u.query else '')
        req = (f'GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n'
               'Upgrade: websocket\r\nConnection: Upgrade\r\n'
               f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError('ws handshake: connection closed')
            buf += chunk
        head, self.buf = buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n')[0] + b' ':
            raise IOError('ws handshake failed: ' + head[:200].decode('latin-1'))

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise IOError('ws closed')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode: int, data: bytes) -> None:
        head = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack('>H', n)
        else:
            head.append(0x80 | 127)
            head += struct.pack('>Q', n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode())

    def recv(self) -> str:
        data = bytearray()
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked, length = b2 & 0x80, b2 & 0x7F
            if length == 126:
                length = struct.unpack('>H', self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b''
            payload = self._recv_exact(length) if length else b''
            if masked:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 0x9:      # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise IOError('ws closed by peer')
            data += payload
            if fin:
                return data.decode('utf-8', 'replace')

    def close(self) -> None:
        try:
            self._send_frame(0x8, b'')
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class CDP:
    def __init__(self, ws_url: str):
        self.ws = WS(ws_url)
        self.seq = 0
        self.events: list[dict] = []

    def call(self, method: str, **params):
        self.seq += 1
        self.ws.send(json.dumps({'id': self.seq, 'method': method, 'params': params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get('id') == self.seq:
                if 'error' in message:
                    raise RuntimeError(f'{method}: {message["error"]}')
                return message.get('result', {})
            if 'method' in message:
                self.events.append(message)
                if len(self.events) > 400:
                    del self.events[:200]

    def eval(self, expression: str, await_promise: bool = True):
        result = self.call('Runtime.evaluate', expression=expression,
                           returnByValue=True, awaitPromise=await_promise)
        if result.get('exceptionDetails'):
            raise RuntimeError('JS: ' + json.dumps(result['exceptionDetails'])[:400])
        return result.get('result', {}).get('value')

    def wait_js(self, expression: str, timeout: float = 25.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval(expression):
                    return True
            except Exception:
                pass
            time.sleep(0.15)
        return False

    def close(self):
        self.ws.close()


def seed_prep_fixture(app, work: Path) -> dict:
    """Черновик и прогон подготовки на локальных данных — без вызовов модели.

    Панель `#creation-progress-panel .prep-panel` живёт только рядом с настоящим
    прогоном `creation_runs`; на пустом профиле кадр `prep` — это тот же
    `new` (n11). Настоящая подготовка требует облачного провайдера и платных
    вызовов, поэтому стенд собирает прогон сам, повторяя путь игрока там, где
    он не касается модели:

      • свой текст кладётся в `runtime/<audit>/books/`;
      • загружается тем же приёмником, что у игрока (`receive_source`: извлечение
        локальное, модель не нужна);
      • страницы режутся на блоки тем же `pipeline.source_batches`, что и в
        `run()`, части пишутся тем же `ensure_part`;
      • строка `creation_runs` получает те же поля, что пишет `run()`.

    Модель не зовётся ни разу (`configured()` у mock-Мастера нет), поэтому
    вызовов 0 и расход 0. Прогон остаётся «в работе»: кадр показывает живую
    полосу, фразу «сейчас» и журнал отказавшего блока.
    """
    import asyncio

    from solo.source_upload import receive_source
    from starlette.datastructures import UploadFile

    books = work / 'books'
    books.mkdir(parents=True, exist_ok=True)
    path = books / PREP_BOOK_NAME
    path.write_text(PREP_BOOK.lstrip(), encoding='utf-8')

    service = app.state.creation
    pipeline = app.state.preparation_pipeline
    draft = service.new()
    draft_id = draft['id']
    service.save(draft_id, 'Разобрать приложенную книгу и собрать приключение на маяке.',
                 draft['revision'])

    async def upload():
        handle = UploadFile(filename=PREP_BOOK_NAME, file=io.BytesIO(path.read_bytes()))
        bundle = service.owned(draft_id)['body']['adventure_bundle']
        return await receive_source(service.library, service.jobs, bundle, handle)

    job = asyncio.run(upload())
    deadline = time.time() + 180
    state, message = None, ''
    while time.time() < deadline:
        with service.library.db() as db:
            row = db.execute('SELECT status,message FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if row:
            state, message = row['status'], row['message'] or ''
            if state in ('complete', 'failed'):
                break
        time.sleep(0.25)
    if state != 'complete':
        raise SystemExit(f'фикстура подготовки: извлечение не завершилось ({state}): {message[:400]}')
    # Тот же шаг, что делает маршрут `/files` после приёма: документ
    # привязывается к черновику и попадает в allowed_documents.
    service.select(draft_id, job['document'])

    documents = sorted(service.allowed_documents(draft_id))
    batches = [batch for batch, _complete in pipeline.source_batches(documents)]
    if len(batches) < 3:
        raise SystemExit(f'фикстура подготовки: текст разрезан на {len(batches)} блок(а), нужно ≥3 '
                         '(предел секции SOLO_PREPARATION_SECTION_CHARACTERS)')

    total = len(batches)
    done = min(3, total - 2)
    run_id, request = str(uuid.uuid4()), str(uuid.uuid4())
    progress = {'phase': 'extracting', 'done': done, 'total': total, 'failed': 1,
                'elapsed_s': 96, 'eta_s': 180, 'section': done + 1, 'concurrency': 2}
    now = time.time()
    # Строка прогона — первой: `creation_part_links` ссылается на неё по FK.
    with pipeline.preparation.connection() as db:
        db.execute('INSERT INTO creation_runs(id,draft,request,fingerprint,status,done,total,message,'
                   'result,created,book_fingerprint,reused,progress,worker,heartbeat,resume,playable) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, draft_id, request, 'n11-fixture', 'running', done, total,
                    'AI структурирует источники. Сценарий пока не готов.', None, now,
                    '', 0, json.dumps(progress, ensure_ascii=False), 'n11-audit-fixture', now, '', 0))
    identity = {'wire_contract': 'source-world-v1', 'fixture': 'n11'}
    for index, batch in enumerate(batches):
        part = pipeline.ensure_part(run_id, identity, [dict(page) for page in batch], True, index * 1000)
        if index < done:
            pipeline.part_update(part, status='validated', body=json.dumps({'entities': []}),
                                 diagnostic='[]', metric=json.dumps({'bytes': 0}))
        elif index == done:
            # Один блок отказал — журнал подготовки не пустой (приёмка: видно,
            # какие блоки и почему). Причина бытовая и настоящая для mock-Мастера.
            pipeline.part_update(part, status='failed', attempts=1, diagnostic=json.dumps(
                [{'field': 'entities', 'reason': 'string_type',
                  'message': 'поле name пришло числом вместо строки'}], ensure_ascii=False))
    return {'draft': draft_id, 'run': run_id, 'book': str(path), 'blocks': total,
            'done': done, 'failed': 1}


def start_prep_fixture(app, work: Path, port: int) -> None:
    """Дождаться живого сервера и собрать фикстуру, не мешая старту приложения.

    `resume_pending()` на старте поднимает всё, что лежит в базе; если положить
    прогон до неё, приложение попробует его «продолжить» и закроет отказом.
    Поэтому фикстура пишется уже после того, как `/api/health` ответил, а
    `SOLO_PREPARATION_WATCHDOG=0` (в `serve()`) не даёт сторожу её трогать.
    Сердцебиение прогона обновляется потоком, пока жив стенд: он и есть
    «воркер» этой фикстуры.
    """
    target = work / 'fixture.json'
    temporary = work / 'fixture.tmp'
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2).read()
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError('сервер не ответил за 120 с')
        time.sleep(1.5)  # resume_pending() стартует сразу после подъёма приложения
        fixture = seed_prep_fixture(app, work)
    except BaseException as error:  # noqa: BLE001 — причину видно в fixture.json и логе
        import traceback
        traceback.print_exc()
        temporary.write_text(json.dumps({'error': str(error)[:600]}, ensure_ascii=False),
                             encoding='utf-8')
        temporary.replace(target)
        return
    temporary.write_text(json.dumps(fixture, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)

    pipeline = app.state.preparation_pipeline

    def keep_alive() -> None:
        while True:
            time.sleep(20)
            try:
                with pipeline.preparation.connection() as db:
                    db.execute("UPDATE creation_runs SET heartbeat=? WHERE id=? AND status='running'",
                               (time.time(), fixture['run']))
            except Exception:
                pass

    threading.Thread(target=keep_alive, name='n11-fixture-heartbeat', daemon=True).start()


# ── процесс приложения ───────────────────────────────────────────────────────
def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def serve(args) -> None:
    """Режим сервера: свой data-dir, свободный порт, три кампании."""
    import uvicorn

    sys.path.insert(0, str(ROOT))
    work = Path(args.data_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.environ['SOLO_TEST'] = '1'
    os.environ['SOLO_ACTIONS_LOG'] = str(work / 'actions.jsonl')
    # Фикстура подготовки (n11): пять глав становятся пятью блоками плана, а
    # сторож не поднимает прогон-фикстуру (у неё нет настоящего воркера).
    os.environ['SOLO_PREPARATION_SECTION_CHARACTERS'] = '2000'
    os.environ['SOLO_PREPARATION_WATCHDOG'] = '0'
    (work / 'actions.jsonl').write_text('', encoding='utf-8')
    for stale in ('server.json', 'fixture.json'):
        if (work / stale).exists():
            (work / stale).unlink()

    from solo.app import create_app
    from solo.llm import CommandMockLLM
    from solo.nat20_api import Nat20GameEngine

    app = create_app(work / 'n07.sqlite3', CommandMockLLM({}), local_port=args.port, experimental=True)
    engine = Nat20GameEngine(app.state.store)
    # n08: имя героя и название истории — разные вещи, как в продукте (имя героя
    # задаёт мастер создания героя, название истории — отдельно). Движок же
    # `create_campaign(name)` пишет ОДНО имя и в кампанию, и в героя, поэтому
    # стенд сеет кампанию именем героя, а название истории ставит отдельной
    # строкой таблицы. Состояние движка при этом не подменяется: в рантайме
    # герой так и остаётся «Мироном».
    seed_rows = [('Мирон', 'Тайна трёх сосен'), ('Сольвейг', 'Соляной тракт'), ('Кай', 'Дом у маяка')]
    campaigns = []
    for index, (hero_name, story_name) in enumerate(seed_rows):
        cid = engine.create_campaign(hero_name, seed=17 + index)['id']
        with app.state.store.connection(write=True) as db:
            db.execute('UPDATE campaigns SET name=? WHERE id=?', (story_name, cid))
        campaigns.append(cid)
    (work / 'server.json').write_text(
        json.dumps({'port': args.port, 'campaigns': campaigns}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'port': args.port, 'campaigns': campaigns}, ensure_ascii=False), flush=True)
    threading.Thread(target=start_prep_fixture, args=(app, work, args.port),
                     name='n11-prep-fixture', daemon=True).start()
    sock = socket.socket()
    sock.bind(('127.0.0.1', args.port))
    uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False)).run(sockets=[sock])


def start_server(data_dir: Path) -> tuple[subprocess.Popen, int, list[str], dict]:
    port = free_port()
    log = open(data_dir / 'server.log', 'wb')
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), '--serve',
         '--data-dir', str(data_dir), '--port', str(port)],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    info_path = data_dir / 'server.json'
    fixture_path = data_dir / 'fixture.json'
    deadline = time.time() + 90
    campaigns: list[str] = []
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit('сервер упал: ' + (data_dir / 'server.log').read_text(errors='replace')[-2000:])
        try:
            info = json.loads(info_path.read_text(encoding='utf-8'))
            if info.get('port') == port:
                campaigns = info['campaigns']
                for _ in range(120):
                    try:
                        urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2).read()
                        break
                    except Exception:
                        time.sleep(0.25)
                else:
                    continue
                break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        proc.kill()
        raise SystemExit('сервер не поднялся за 90 с')
    # Фикстура подготовки собирается уже на живом сервере (n11): извлечение
    # своего текста локальное, но потоку нужно время.
    deadline = time.time() + 240
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit('сервер упал на фикстуре подготовки: '
                             + (data_dir / 'server.log').read_text(errors='replace')[-2000:])
        if fixture_path.exists():
            fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
            if fixture.get('error'):
                proc.kill()
                raise SystemExit('фикстура подготовки не собралась: ' + fixture['error'])
            print(f'фикстура подготовки: черновик {fixture["draft"]}, прогон {fixture["run"]}, '
                  f'блоков {fixture["blocks"]} (готово {fixture["done"]}, отказ {fixture["failed"]}), '
                  f'книга {fixture["book"]}', flush=True)
            return proc, port, campaigns, fixture
        time.sleep(0.25)
    proc.kill()
    raise SystemExit('фикстура подготовки не собралась за 240 с: '
                     + (data_dir / 'server.log').read_text(errors='replace')[-2000:])


# ── Chromium ────────────────────────────────────────────────────────────────
def start_chrome(profile_dir: Path) -> tuple[subprocess.Popen, str]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = CHROME_DEPS
    proc = subprocess.Popen(
        [str(CHROME), '--headless=new', '--no-sandbox', '--disable-gpu',
         '--disable-dev-shm-usage', '--hide-scrollbars', '--mute-audio',
         '--disable-extensions', '--no-first-run', '--disable-background-networking',
         f'--remote-debugging-port={port}', f'--user-data-dir={profile_dir}',
         'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=1).read()
            return proc, port
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit('chrome не поднялся')


def page_target(port: int) -> str:
    for _ in range(60):
        try:
            targets = json.loads(urllib.request.urlopen(
                f'http://127.0.0.1:{port}/json/list', timeout=10).read().decode())
            pages = [t for t in targets if t.get('type') == 'page']
            if pages:
                return pages[0]['webSocketDebuggerUrl']
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit('в chrome нет вкладки')


# ── замер (в контексте страницы) ─────────────────────────────────────────────
MEASURE_JS = r"""
(() => {
  const allowed = new Set(window.__auditColors || []);
  const W = innerWidth, H = innerHeight;
  const de = document.documentElement;
  /* Цвет из computed style. Кроме rgb()/rgba() разбираем color(srgb …):
     именно в этой форме Chrome отдаёт результат `color-mix()` (он есть в
     themes.css и creation.css), и прежний разбор молча терял такие цвета.
     `null` — цвет не разобран, это причина пропуска, а не «контраст 0». */
  const colorOf = (value) => {
    const s = String(value || '').trim();
    if (!s) return null;
    if (s === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
    let m = s.match(/^rgba?\(([^)]+)\)$/);
    if (m) {
      const p = m[1].split(/[,\s\/]+/).filter(Boolean).map(parseFloat);
      if (p.length < 3 || !p.slice(0, 3).every(Number.isFinite)) return null;
      return { r: p[0], g: p[1], b: p[2],
               a: p.length > 3 ? (Number.isFinite(p[3]) ? p[3] : 1) : 1 };
    }
    m = s.match(/^color\(\s*srgb\s+([^)]+)\)$/);
    if (m) {
      const p = m[1].split(/[\s\/]+/).filter(Boolean).map(parseFloat);
      if (p.length < 3 || !p.slice(0, 3).every(Number.isFinite)) return null;
      return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255,
               a: p.length > 3 ? (Number.isFinite(p[3]) ? p[3] : 1) : 1 };
    }
    return null;
  };
  const rgb = (value) => {
    const c = colorOf(value);
    if (!c || c.a === 0) return null;
    const out = [c.r, c.g, c.b].map(Math.round);
    return { key: out.join(','), rgb: out };
  };
  const lum = (c) => {
    const f = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); const hi = Math.max(l1, l2), lo = Math.min(l1, l2); return (hi + 0.05) / (lo + 0.05); };
  const shown = (el) => {
    if (!el.getBoundingClientRect) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0.5 && r.height > 0.5;
  };
  const inView = (r) => r.right > 0 && r.bottom > 0 && r.left < W && r.top < H;
  const path = (el) => {
    const bits = [];
    let n = el;
    while (n && n.nodeType === 1 && bits.length < 5) {
      let s = n.tagName.toLowerCase();
      if (n.id) { bits.unshift(s + '#' + n.id); break; }
      const raw = n.className;
      const classText = typeof raw === 'string' ? raw : (raw && raw.baseVal) || '';
      const cls = String(classText).trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      const parent = n.parentElement;
      if (parent) {
        const same = [...parent.children].filter(c => c.tagName === n.tagName);
        if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(n) + 1) + ')';
      }
      bits.unshift(s);
      n = n.parentElement;
    }
    return bits.join(' > ');
  };
  const ownText = (el) => {
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    return own.trim().replace(/\s+/g, ' ');
  };
  /* Наложение слоя `fg` (со своей альфой) на непрозрачный фон `bg`. */
  const over = (fg, bg) => [fg.r * fg.a + bg[0] * (1 - fg.a),
                            fg.g * fg.a + bg[1] * (1 - fg.a),
                            fg.b * fg.a + bg[2] * (1 - fg.a)];
  const flat = (arr) => arr.map(v => Math.round(Math.max(0, Math.min(255, v))));
  /* Холст страницы: по CSS фон корня уезжает на холст. Если ни у html, ни у
     body непрозрачного фона нет — что там на самом деле (бывает fixed-подложка
     отдельным слоем), из дерева не видно: это пропуск, а не «белый». */
  const canvasColor = () => {
    for (const root of [document.documentElement, document.body]) {
      if (!root) continue;
      const c = colorOf(getComputedStyle(root).backgroundColor);
      if (c && c.a >= 0.999) return [c.r, c.g, c.b];
    }
    return null;
  };
  /* Пиксели фона под содержимым элемента.
     Слои `background-color` складываются от корня вниз; `opacity` элемента
     гасит его группу целиком — и фон, и текст внутри неё, — поэтому у
     выключенной кнопки (opacity .5) фон светлеет вместе с подписью, и
     контраст надо считать по отрендеренным пикселям, а не по палитре.
     Прежние версии брали первый слой с ненулевой альфой за непрозрачный
     (панель rgba(0,0,0,.8) над белым давала 4.7:1 вместо 2.8:1).
     Фон-картинка в цепочке — пропуск: такой контраст не посчитать.
     Ограничение: при ВЛОЖЕННЫХ группах прозрачности берётся подложка внешней
     группы; в приложении таких мест нет, случай отмечен здесь, а не спрятан. */
  const bgInfo = (el) => {
    if (!el || el.nodeType !== 1) return {skip: 'фон-не-определён', sel: '#document'};
    const canvas = canvasColor();
    if (!canvas) return {skip: 'фон-страницы-не-определён', sel: path(document.documentElement)};
    const chain = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) chain.unshift(n);
    let content = canvas;   /* пиксели, на которых рисуется содержимое */
    let behind = canvas;    /* пиксели за внешней группой прозрачности */
    let alpha = 1;
    for (const n of chain) {
      const cs = getComputedStyle(n);
      const img = cs.backgroundImage;
      if (img && img !== 'none') return {skip: 'фон-картинка', sel: path(n)};
      const c = colorOf(cs.backgroundColor);
      if (!c) return {skip: 'фон-не-разобран', sel: path(n)};
      const o = Number(cs.opacity);
      if (Number.isFinite(o) && o < 0.999) {
        if (alpha >= 0.999) behind = content;
        alpha *= Math.max(0, o);
      }
      content = over(c, content);
    }
    const render = (inside) => alpha >= 0.999
      ? flat(inside) : flat(over({r: inside[0], g: inside[1], b: inside[2], a: alpha}, behind));
    return {content: content, alpha: alpha, behind: behind, rgb: render(content)};
  };
  /* Пиксель краски, нарисованной внутри группы элемента: и текст, и рамка
     лежат в его группе прозрачности и гасятся одинаково. */
  const paintOf = (info, c) => {
    const inside = over(c, info.content);
    return info.alpha >= 0.999 ? flat(inside)
      : flat(over({r: inside[0], g: inside[1], b: inside[2], a: info.alpha}, info.behind));
  };

  const all = [...document.querySelectorAll('body *')];
  const fonts = {}, colors = {}, clipped = [], tapsBad = [], contrastBad = [], smallText = [],
        tinyText = [], borderBad = [], contrastSkipped = [];
  const skippedSeen = new Set();
  const frameSeen = new Set();  /* одна рамка-обёртка на несколько контролов — один раз */
  let textCount = 0, minFont = null, inter = 0, tapCount = 0, clippedCount = 0, colorOutside = 0;
  let contrastChecked = 0, controlChecked = 0, cellChecked = 0, placeholderChecked = 0, skipCount = 0;

  const INTER = 'button,a[href],input,select,textarea,summary,[role="button"],[role="tab"],[data-view]';
  const inters = new Set([...document.querySelectorAll(INTER)]);
  /* 1.4.11 — про элементы управления. Декоративные роли (`presentation`,
     `none`, `group`) и фокус-ловушки с `tabindex="-1"` контролами не являются:
     их рамка ни на что не влияет и в счёт не идёт. */
  const ROLES = /^(button|tab|textbox|searchbox|combobox|checkbox|radio|switch|link|menuitem|menuitemcheckbox|menuitemradio|option|slider|spinbutton|treeitem)$/;
  const CELLISH = /(^|[-_ ])(cell|field|input|tab|chip|score)($|[-_ ])/i;
  const classNameOf = (el) => {
    const c = el.className;
    return typeof c === 'string' ? c : (c && c.baseVal) || '';
  };
  const controlOf = (el) => {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return false;
    }
    if (inters.has(el)) return true;
    if (ROLES.test((el.getAttribute('role') || '').trim().toLowerCase())) return true;
    const ti = el.getAttribute('tabindex');
    return ti !== null && Number(ti) >= 0;
  };
  const cellOf = (el) => !controlOf(el) && CELLISH.test(classNameOf(el));
  /* Неактивные элементы WCAG 1.4.3/1.4.11 исключают — не нарушение, но и не
     тишина: пишем в `contrastSkipped` с причиной. */
  const inactiveOf = (el) => {
    try { if (el.matches(':disabled')) return true; } catch (e) { /* не форма */ }
    if (el.getAttribute('aria-disabled') === 'true') return true;
    return Boolean(el.closest && el.closest('fieldset[disabled],[aria-disabled="true"]'));
  };
  /* Не посчитали — говорим об этом вслух (`contrastSkipped`), а не пишем ноль.
     Селектор — виновника: если фон перекрыт градиентом на пятом предке, в
     отчёте должен стоять он, а не сам лист. */
  const skip = (sel, kind, reason) => {
    skipCount += 1;
    const key = sel + '|' + kind + '|' + reason;
    if (skippedSeen.has(key) || contrastSkipped.length >= 25) return;
    skippedSeen.add(key);
    contrastSkipped.push({sel: sel, kind: kind, reason: reason});
  };

  for (const el of all) {
    if (!shown(el)) continue;
    const r = el.getBoundingClientRect();
    const onScreen = inView(r);
    const watched = inters.has(el);
    const text = ownText(el);
    if (watched && onScreen) {
      inter += 1; tapCount += 1;
      if (r.width < 43.5 || r.height < 43.5) {
        tapsBad.push({sel: path(el), label: (el.getAttribute('aria-label') || text || el.tagName).slice(0, 40),
                      w: +r.width.toFixed(1), h: +r.height.toFixed(1)});
      }
    }
    const cs = getComputedStyle(el);
    const px = parseFloat(cs.fontSize) || 0;
    if (onScreen) {
      /* ── контраст: текст, плейсхолдер, границы контролов ─────────────────
         С прозрачностью и вложенными фонами; что посчитать нельзя — уходит в
         `contrastSkipped` с селектором и причиной, а не молча в ноль. */
      const sel = path(el);
      const inactive = inactiveOf(el);
      if (text) {
        const fg = colorOf(cs.color);
        const bg = bgInfo(el);
        if (!fg || fg.a === 0) {
          skip(sel, 'текст', 'цвет-не-разобран');
        } else if (bg.skip) {
          skip(bg.sel || sel, 'текст', bg.skip);
        } else if (inactive) {
          skip(sel, 'текст', 'неактивный-элемент');
        } else {
          const fgc = paintOf(bg, fg);
          const cr = ratio(fgc, bg.rgb);
          contrastChecked += 1;
          if (cr < 4.5) contrastBad.push({
            sel: sel, text: text.slice(0, 32), ratio: +cr.toFixed(2),
            fg: 'rgb(' + fgc.join(',') + ')', bg: 'rgb(' + bg.rgb.join(',') + ')',
            px: px, kind: 'текст'});
        }
      }
      /* Плейсхолдер: своего текста в узле нет, но читает его игрок. */
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        const hint = String(el.getAttribute('placeholder') || '').trim();
        if (hint) {
          const pc = colorOf(getComputedStyle(el, '::placeholder').color);
          const bg = bgInfo(el);
          if (!pc || pc.a === 0) skip(sel, 'плейсхолдер', 'цвет-не-разобран');
          else if (bg.skip) skip(bg.sel || sel, 'плейсхолдер', bg.skip);
          else if (inactive) skip(sel, 'плейсхолдер', 'неактивный-элемент');
          else {
            const pfg = paintOf(bg, pc);
            const cr = ratio(pfg, bg.rgb);
            placeholderChecked += 1;
            if (cr < 4.5) contrastBad.push({
              sel: sel, text: hint.slice(0, 32), ratio: +cr.toFixed(2),
              fg: 'rgb(' + pfg.join(',') + ')', bg: 'rgb(' + bg.rgb.join(',') + ')',
              px: px, kind: 'плейсхолдер'});
          }
        }
      }
      /* Граница контрола (WCAG 1.4.11, порог 3:1) против фона СНАРУЖИ.
         Границу образует не только `border`: у композера рамка лежит на
         форме-обёртке (`form#action-form.composer`), а у вкладок и полей —
         заливка. Поэтому берём ближайший (до трёх уровней) элемент, у
         которого граница вообще видна, и не даём уехать на целую секцию:
         площадь рамки не больше 2.5 площади самого контрола. */
      const isControl = controlOf(el);
      const isCell = !isControl && cellOf(el);
      if (isControl || isCell) {
        const kind = isCell ? 'ячейка' : 'граница';
        const own = el.getBoundingClientRect();
        const area = Math.max(1, own.width * own.height);
        const boundaryOfNode = (n) => {
          const out = bgInfo(n.parentElement || n);
          const mine = bgInfo(n);
          if (out.skip || mine.skip) {
            const bad = out.skip ? out : mine;
            return {skip: bad.skip, sel: bad.sel || path(n)};
          }
          const ncs = getComputedStyle(n);
          /* Рамку видно, если она контрастна ХОТЯ БЫ одной соседней стороне:
             снаружи (страница) или изнутри (заливка поля). Берём худшую из
             двух — у композера рамка 2.09:1 к своей заливке и 2.18:1 к
             странице, и норма 3:1 не выполнена ни там, ни там. */
          const bothSides = (b) => {
            const rOut = ratio(b, out.rgb);
            const rIn = ratio(b, mine.rgb);
            return {rOut: rOut, rIn: rIn, r: Math.min(rOut, rIn)};
          };
          let edge = null;
          for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
            const w = parseFloat(ncs['border' + side + 'Width']) || 0;
            const style = ncs['border' + side + 'Style'];
            if (w < 1 || !style || style === 'none' || style === 'hidden') continue;
            const bc = colorOf(ncs['border' + side + 'Color']);
            if (!bc || bc.a === 0) continue;
            const b = paintOf(mine, bc);
            const cr = bothSides(b);
            if (!edge || cr.r < edge.ratio) edge = {
              ratio: cr.r, what: 'рамка', side: side.toLowerCase(), w: w,
              fg: 'rgb(' + b.join(',') + ')', bg: 'rgb(' + out.rgb.join(',') + ')',
              ratioIn: +cr.rIn.toFixed(2), ratioOut: +cr.rOut.toFixed(2)};
          }
          if (!edge && ncs.boxShadow && ncs.boxShadow !== 'none' && /inset/.test(ncs.boxShadow)) {
            const m = ncs.boxShadow.match(/(rgba?\([^)]*\)|color\([^)]*\))/);
            const sc = m ? colorOf(m[0]) : null;
            if (sc && sc.a > 0) {
              const b = paintOf(mine, sc);
              const cr = bothSides(b);
              edge = {ratio: cr.r, what: 'внутренняя-тень', side: 'all', w: 1,
                      fg: 'rgb(' + b.join(',') + ')', bg: 'rgb(' + out.rgb.join(',') + ')',
                      ratioIn: +cr.rIn.toFixed(2), ratioOut: +cr.rOut.toFixed(2)};
            }
          }
          if (!edge) {
            const ow = parseFloat(ncs.outlineWidth) || 0, ostyle = ncs.outlineStyle;
            if (ow >= 1 && ostyle && ostyle !== 'none') {
              const oc = colorOf(ncs.outlineColor);
              if (oc && oc.a > 0) {
                const b = paintOf(mine, oc);
                const cr = bothSides(b);
                edge = {ratio: cr.r, what: 'контур', side: 'all', w: ow,
                        fg: 'rgb(' + b.join(',') + ')', bg: 'rgb(' + out.rgb.join(',') + ')',
                        ratioIn: +cr.rIn.toFixed(2), ratioOut: +cr.rOut.toFixed(2)};
              }
            }
          }
          /* Заливка — тоже граница (вкладки, композер): сравниваем с фоном
             снаружи; если она контрастна, граница есть, даже когда рамки нет
             или рамка с заливкой одного цвета. */
          const fill = ratio(mine.rgb, out.rgb);
          const ownBg = colorOf(ncs.backgroundColor);
          const hasFill = ownBg && ownBg.a > 0;
          let found = null;
          if (edge && edge.ratio >= 3) found = edge;
          else if (fill >= 3) found = {ratio: fill, what: 'заливка', side: 'all', w: 0,
                                       fg: 'rgb(' + mine.rgb.join(',') + ')',
                                       bg: 'rgb(' + out.rgb.join(',') + ')'};
          else if (edge) found = edge;
          else if (hasFill) found = {ratio: fill, what: 'заливка', side: 'all', w: 0,
                                     fg: 'rgb(' + mine.rgb.join(',') + ')',
                                     bg: 'rgb(' + out.rgb.join(',') + ')'};
          return found ? {found: found} : {none: true};
        };
        let res = null, frame = el;
        for (let depth = 0, n = el; n && n.nodeType === 1 && depth < 4;
             depth += 1, n = n.parentElement) {
          res = boundaryOfNode(n);
          if (res.skip || res.found) {
            if (res.found && depth > 0) {
              const rn = n.getBoundingClientRect();
              /* Рамка поля может быть обёрткой (композер: 3.2 площади поля),
                 но не целой секцией: ширина не больше двух ширин контрола,
                 площадь — не больше восьми. */
              if (rn.width > own.width * 2 || rn.width * rn.height > area * 8) {
                res = null; continue;
              }
            }
            frame = n;
            break;
          }
          res = null;
        }
        if (inactive) {
          skip(sel, kind, 'неактивный-элемент');
        } else if (!res) {
          skip(sel, kind, 'видимой-границы-нет');
        } else if (res.skip) {
          skip(res.sel || sel, kind, res.skip);
        } else {
          if (isCell) cellChecked += 1; else controlChecked += 1;
          const frameSel = path(frame);
          const shared = frame !== el && frameSeen.has(frameSel);
          if (res.found.ratio < 3 && !shared) {
            if (frame !== el) frameSeen.add(frameSel);
            borderBad.push({
              sel: frameSel, frameFor: frame === el ? undefined : sel,
              label: (el.getAttribute('aria-label') || text || el.tagName).slice(0, 40),
              side: res.found.side, w: res.found.w, what: res.found.what,
              ratio: +res.found.ratio.toFixed(2),
              fg: res.found.fg, bg: res.found.bg, kind: kind});
          }
        }
      }
    }
    if (!text) continue;
    if (onScreen) {
      textCount += 1;
      fonts[px] = (fonts[px] || 0) + 1;
      if (minFont === null || px < minFont) minFont = px;
      if (px < 14) smallText.push({sel: path(el), px: px, text: text.slice(0, 32)});
      /* n13: всё, что мельче 16 — к критерию «мин. кегль ≥ 16»: без списка
         не отличить служебную подпись (эталон: подпись ячейки 14) от строки,
         которую игрок читает и нажимает. */
      if (px < 16 && tinyText.length < 40) tinyText.push({sel: path(el), px: px, text: text.slice(0, 32)});
    }
    if (onScreen) {
      for (const prop of ['color', 'backgroundColor', 'borderTopColor']) {
        const c = rgb(cs[prop]);
        if (!c) continue;
        if (!allowed.has(c.key)) {
          colorOutside += 1;
          colors[c.key] = colors[c.key] || {count: 0, sample: path(el), prop};
          colors[c.key].count += 1;
        }
      }
    }
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      clippedCount += 1;
      if (clipped.length < 15) {
        clipped.push({sel: path(el), sw: el.scrollWidth, cw: el.clientWidth,
                      overflow: getComputedStyle(el).overflowX, text: text.slice(0, 32)});
      }
    }
  }

  contrastBad.sort((a, b) => a.ratio - b.ratio);
  borderBad.sort((a, b) => a.ratio - b.ratio);
  const outsideColors = Object.entries(colors)
    .sort((a, b) => b[1].count - a[1].count).slice(0, 12)
    .map(([key, v]) => ({color: 'rgb(' + key + ')', count: v.count, sample: v.sample, prop: v.prop}));

  /* ── n13: точечные проверки приёмки ────────────────────────────────────────
     Каждая — число или флаг, а не «на глаз»: поле ввода и призыв в первом
     кадре, подписи статов, шесть характеристик в окне, подписи меню без
     переносов, отсутствие внутренних слов. */
  const rectOf = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1),
            h: +r.height.toFixed(1), bottom: +r.bottom.toFixed(1), right: +r.right.toFixed(1),
            inView: r.top < H && r.bottom > 0 && r.left < W && r.right > 0};
  };
  /* Строки текста — по одному символу (Range) с группировкой по верхней
     координате: `height / line-height` не отличает перенос по словам от
     разрыва слова посередине. */
  const linesOf = (node) => {
    if (!node || node.nodeType !== 3) return {lines: 1, broken: []};
    const t = node.nodeValue || '';
    if (!t.trim()) return {lines: 0, broken: []};
    const range = document.createRange(), chars = [];
    for (let i = 0; i < t.length; i++) {
      range.setStart(node, i); range.setEnd(node, i + 1);
      const b = range.getBoundingClientRect();
      if (!b.width && !b.height) continue;
      chars.push({i: i, top: Math.round(b.top)});
    }
    const groups = [];
    for (const c of chars) {
      let g = groups.find(x => Math.abs(x.top - c.top) < 3);
      if (!g) { g = {top: c.top, from: c.i, to: c.i}; groups.push(g); }
      g.from = Math.min(g.from, c.i); g.to = Math.max(g.to, c.i);
    }
    groups.sort((a, b) => a.top - b.top);
    const broken = [];
    for (let i = 1; i < groups.length; i++) {
      const gap = t.slice(groups[i - 1].to + 1, groups[i].from);
      if (!/[\s\u00ad\-–—]/.test(gap) && gap !== '') broken.push(t.slice(groups[i - 1].from, groups[i].to + 1));
    }
    return {lines: groups.length, broken: broken};
  };
  /* Ширина подписи одной строкой: клон в absolute/nowrap. Без этого «Переносов
     0» нельзя проверить иначе как «на глаз»: ширина колонки и ширина строки
     расходятся на доли пикселя, и разница решает, будет ли перенос. */
  const singleLineWidth = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el), probe = document.createElement('span');
    probe.textContent = (el.textContent || '').replace(/\s+/g, ' ').trim();
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;white-space:nowrap;visibility:hidden;' +
      'font:' + cs.font + ';letter-spacing:' + cs.letterSpacing + ';font-kerning:' + cs.fontKerning;
    document.body.appendChild(probe);
    const w = probe.getBoundingClientRect().width;
    probe.remove();
    return Math.round(w * 10) / 10;
  };
  const navLabels = [...document.querySelectorAll('#nav-drawer button.nav, #nav-drawer a.nav')]
    .filter(shown).map((el) => {
      const label = el.querySelector('.nav-text') || el;
      const textNode = [...label.childNodes].find(n => n.nodeType === 3 && n.nodeValue.trim());
      const geo = linesOf(textNode), lcs = getComputedStyle(label);
      return {text: (label.textContent || '').replace(/\s+/g, ' ').trim(),
              lines: geo.lines, broken: geo.broken,
              textWidth: Math.round(label.getBoundingClientRect().width * 10) / 10,
              textScroll: label.scrollWidth,
              singleWidth: singleLineWidth(label),
              fontSize: lcs.fontSize, fontFamily: lcs.fontFamily.split(',')[0],
              letterSpacing: lcs.letterSpacing,
              ...rectOf(el)};
    });
  const desc = document.querySelector('#creation-description');
  const descLines = (() => {
    if (!desc) return null;
    const cs = getComputedStyle(desc), lh = parseFloat(cs.lineHeight) || 0;
    const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) +
                parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    return lh ? +((desc.getBoundingClientRect().height - pad) / lh).toFixed(1) : null;
  })();
  const prepare = [...document.querySelectorAll('#creation .button-row button')]
    .find(b => /Подготовить приключение|Проверить готовность/.test(b.textContent)) || null;
  const abilityCells = [...document.querySelectorAll('#character .scores-grid > *')]
    .map(el => ({text: (el.innerText || '').replace(/\s+/g, ' ').trim(), ...rectOf(el)}));
  const vitalsBox = document.querySelector('body > main > header .vitals');
  const bodyWords = (document.body.innerText || '').replace(/\s+/g, ' ');
  const probe = {
    viewportH: H,
    description: desc ? {rect: rectOf(desc), lines: descLines} : null,
    prepare: prepare ? {text: prepare.textContent.trim(), disabled: prepare.disabled, rect: rectOf(prepare)} : null,
    drop: rectOf(document.querySelector('#creation .creation-drop')),
    abilities: {
      count: abilityCells.length,
      columns: new Set(abilityCells.map(c => Math.round(c.x))).size,
      lastBottom: abilityCells.length ? Math.max(...abilityCells.map(c => c.bottom)) : null,
      allInside: abilityCells.length ? abilityCells.every(c => c.bottom <= H && c.y >= 0) : null,
      cells: abilityCells
    },
    navLabels: navLabels,
    navWrapped: navLabels.filter(l => l.lines > 1).length,
    navBroken: navLabels.filter(l => l.broken.length).length,
    headerVitals: vitalsBox ? {text: (vitalsBox.innerText || '').replace(/\s+/g, ' ').trim(),
                               rect: rectOf(vitalsBox)} : null,
    /* n14: вкладки шторки — сколько строк реально видно на открытой вкладке и
       из чего сложилась её высота. `rows` соседнего замера `drawer` считает
       строки всей разметки (скрытые тоже), поэтому здесь — только видимые. */
    drawerTabs: (() => {
      const d = document.querySelector('#nav-drawer');
      if (!d) return null;
      const tabs = [...d.querySelectorAll('button.nav-tab')];
      return {
        tab: d.dataset.tab || null,
        tabs: tabs.map(b => ({text: (b.textContent || '').trim(),
                              selected: b.getAttribute('aria-selected') === 'true'})),
        visible: tabs.filter(shown).length,
        rows: [...d.querySelectorAll('button.nav, a.nav')].filter(shown).length,
        perBlock: [...d.querySelectorAll('.nav-block')].map(b => ({
          title: ((b.querySelector('.nav-group-label') || {}).textContent || '').trim(),
          rows: [...b.querySelectorAll('button.nav, a.nav')].filter(shown).length
        })),
        overflow: Math.max(0, d.scrollHeight - d.clientHeight),
        children: [...d.children].filter(shown).map(c => {
          const r = c.getBoundingClientRect();
          return {cls: (c.className || c.tagName).toString().split(' ')[0],
                  y: Math.round(r.y), h: Math.round(r.height), bottom: Math.round(r.bottom)};
        })
      };
    })(),
    /* n14: главная — сколько секций-списков истории и есть ли «Все истории». */
    home: (() => {
      const w = document.querySelector('#welcome');
      if (!w) return null;
      const buttons = [...w.querySelectorAll('button, a')];
      return {
        sections: [...w.querySelectorAll('.start__stories')].filter(shown).length,
        lists: [...w.querySelectorAll('.start__list')].filter(shown).length,
        stories: [...w.querySelectorAll('.story')].filter(shown).length,
        continueBlocks: [...w.querySelectorAll('.start__continue')].filter(shown).length,
        allStories: buttons.filter(b => /Все истории/.test(b.textContent || '') && shown(b)).length,
        kickers: [...w.querySelectorAll('.start__stories-kicker')].filter(shown)
          .map(k => (k.textContent || '').trim())
      };
    })(),
    /* n14: блокнот — пустые секции, поле и кнопки сохранения. */
    journal: (() => {
      const j = document.querySelector('#journal');
      if (!j) return null;
      const known = j.querySelector('.known-journal'), events = j.querySelector('.notebook-events');
      const facts = known ? known.querySelectorAll('.prose').length : 0;
      const eventRows = events ? events.querySelectorAll('details').length : 0;
      const save = [...j.querySelectorAll('button')].filter(b => /Сохранить запись/.test(b.textContent || ''));
      const reload = [...j.querySelectorAll('button')].filter(b => /Обновить блокнот/.test(b.textContent || ''));
      /* Свободная ширина справа: критика №10 — 44 % ширины окна пусто при
         «Запись 0». Считаем по самому широкому видимому содержимому блокнота. */
      const jr = j.getBoundingClientRect();
      const inner = [...j.querySelectorAll('section, textarea, .tabletop-tabs')].filter(shown);
      const right = inner.length ? Math.max(...inner.map(e => e.getBoundingClientRect().right)) : jr.right;
      return {
        width: Math.round(jr.width),
        contentRight: Math.round(right),
        rightGap: Math.max(0, Math.round(W - right)),
        emptyRightPct: Math.max(0, Math.round((W - right) / W * 100)),
        sections: [...j.querySelectorAll('section')].filter(shown).length,
        emptySections: [known, events].filter(s => s && shown(s) &&
          !s.querySelector('.prose') && !s.querySelector('details')).length,
        knownVisible: Boolean(known && shown(known)),
        eventsVisible: Boolean(events && shown(events)),
        facts: facts, eventRows: eventRows,
        textareas: [...j.querySelectorAll('textarea')].filter(shown).length,
        saveButtons: save.filter(shown).length,
        reloadButtons: reload.filter(shown).length,
        empty: j.classList.contains('journal-empty')
      };
    })(),
    /* n14: панель «ВАШ ГЕРОЙ» — строки HP больше нет, хиты остаются в шапке.
       `hpText` — по тексту панели, `documentHp` — по всему видимому тексту
       документа: вторая подпись «HP» не должна вернуться ниоткуда. */
    heroPanel: (() => {
      const p = document.querySelector('#play #scene-hero');
      if (!p) return null;
      const text = (p.innerText || '').replace(/\s+/g, ' ').trim();
      const all = (document.body.innerText || '').replace(/\s+/g, ' ');
      return {
        hpNodes: p.querySelectorAll('.game-hp').length,
        hpText: /(^|\s)HP\s*\d/.test(text),
        hpWords: (text.match(/\bHP\b/g) || []).length,
        documentHp: (all.match(/\bHP\s*\d/g) || []).length,
        text: text.slice(0, 160)
      };
    })(),
    words: {
      hit: /хиты/i.test(bodyWords),
      ac: /защита/i.test(bodyWords),
      computer: /компьютере/i.test(bodyWords),
      neuralApi: /Нейросети и API/i.test(bodyWords),
      costsApi: /Расходы API/i.test(bodyWords),
      performance: /Производительность/i.test(bodyWords)
    }
  };

  return {
    probe: probe,

    viewport: {w: W, h: H, dpr: devicePixelRatio},
    scrollWidth: de.scrollWidth,
    scrollHeight: de.scrollHeight,
    overflowX: Math.max(0, de.scrollWidth - W),
    activeView: (document.querySelector('.view.active') || {}).id || null,
    campaignOpen: document.body.classList.contains('campaign-open'),
    kbOpen: document.body.classList.contains('kb-open'),
    textElements: textCount,
    fontMin: minFont,
    smallText: smallText.sort((a, b) => a.px - b.px).slice(0, 8),
    tinyText: tinyText.sort((a, b) => a.px - b.px).slice(0, 40),
    fontDistinct: Object.keys(fonts).length,
    fontSizes: fonts,
    tapCount,
    tapsBad: tapsBad.slice(0, 15),
    tapsBadCount: tapsBad.length,
    contrastBad: contrastBad.slice(0, 12),
    contrastBadCount: contrastBad.length,
    contrastMin: contrastBad.length ? contrastBad[0].ratio : null,
    contrastChecked: contrastChecked,
    placeholderChecked: placeholderChecked,
    borderBad: borderBad.slice(0, 12),
    borderBadCount: borderBad.length,
    borderMin: borderBad.length ? borderBad[0].ratio : null,
    borderChecked: controlChecked + cellChecked,
    controlChecked: controlChecked,
    cellChecked: cellChecked,
    contrastSkipped: contrastSkipped,
    contrastSkippedCount: skipCount,
    interactiveVisible: inter,
    clipped: clipped,
    clippedCount,
    colorsOutsideCount: colorOutside,
    colorsOutside: outsideColors,
    drawer: (() => {
      const d = document.querySelector('#nav-drawer');
      if (!d || !shown(d)) return null;
      const dr = d.getBoundingClientRect();
      if (!inView(dr)) return null;
      const a = document.querySelector('body > aside.sidebar');
      const box = (innerWidth < 760 && a) ? a : d;
      const blocks = [...d.querySelectorAll('.nav-block')];
      return {h: +dr.height.toFixed(1), top: Math.round(dr.top),
              /* С n14 в шторке две вкладки: строки считаются видимые (скрытая
                 группа в счёт не идёт), иначе «18 строк» врёт про кадр. */
              rows: [...d.querySelectorAll('button.nav, a.nav')].filter(shown).length,
              blocks: blocks.filter(shown).map(b => (b.querySelector('.nav-group-label') || {}).textContent || ''),
              scrollTop: Math.round(box.scrollTop), scroll: box.scrollHeight - box.clientHeight,
              content: Math.round(d.scrollHeight)};
    })()
  };
})()
"""

BLOCK_EXPL = r"""
(() => {
  const out = [];
  for (const el of document.querySelectorAll('#play #chat > *')) {
    const r = el.getBoundingClientRect();
    out.push({tag: el.className, h: Math.round(r.height), text: (el.innerText || '').slice(0, 60)});
  }
  return out;
})()
"""


# ── состояния ───────────────────────────────────────────────────────────────
def setup_home(w: int) -> str:
    return "(()=>{view('welcome');scrollTo(0,0);return true})()"


def setup_new(w: int) -> str:
    return ("(()=>{const b=document.querySelector('#welcome .start__btn--primary');"
            "if(b)b.click();return Boolean(b)})()")


def setup_hero(w: int) -> str:
    return "(()=>{view('character');return true})()"


def setup_game(w: int) -> str:
    return ("(()=>{view('play');const c=document.querySelector('#play #chat');"
            "if(c)c.scrollTop=c.scrollHeight;return true})()")


def setup_notes(w: int) -> str:
    return "(()=>{view('journal');scrollTo(0,0);return true})()"


def setup_menu(second: bool) -> str:
    """Открыть шторку. `second` — вкладка «Приложение» (n14: шторка разведена
    на вкладки «Кампания»/«Приложение»; до n14 состояния menu и menu-app были
    побайтово одинаковыми кадрами)."""
    pick = ("const tab=d.querySelector('button.nav-tab[data-nav-tab=\"app\"]');"
            "if(tab)tab.click();" if second else "")
    return ("(()=>{const wide=innerWidth>=760;"
            "if(wide){const b=document.querySelector('#nav-more');if(b)b.click();}"
            "else{const b=document.querySelector('#mobile-burger');if(b)b.click();}"
            "const d=document.querySelector('#nav-drawer');"
            "const a=document.querySelector('body > aside.sidebar');"
            "if(!d)return false;"
            + pick +
            "d.scrollTop=0;if(a)a.scrollTop=0;"
            "return true})()")


def setup_prep(draft: str | None) -> str:
    """Открыть экран подготовки книги тем же путём, что у игрока.

    С карточки черновика игрок попадает в экран через `soloOpenCreation(id)`
    (`web/chronicles.js:25`) — эту же функцию зовём здесь. Кадр снимается
    только когда на экране появилась панель подготовки: ждёт её селектор
    (`#creation-progress-panel .prep-panel`), а не слепой таймаут.
    """
    if draft:
        opening = f"await window.soloOpenCreation({json.dumps(draft)});"
    else:
        opening = ("const start=document.querySelector('#welcome .start__btn--primary');"
                   "if(start)start.click();")
    return ("(async()=>{" + opening +
            "for(let i=0;i<150&&!document.querySelector('#creation.view.active');i++)await new Promise(r=>setTimeout(r,100));"
            "return {view:Boolean(document.querySelector('#creation.view.active'))};})()")


def setup_prep_log() -> str:
    """Раскрыть журнал подготовки: история ошибок + список отказавших блоков."""
    return r"""(()=>{
  const details=[...document.querySelectorAll('#creation details')]
    .find(node=>/ошиб|журнал/i.test((node.querySelector('summary')||{}).textContent||''));
  if(details)details.open=true;
  const failed=document.querySelector('#creation-progress-panel .prep-failed');
  const target=failed||details;
  if(target)target.scrollIntoView({block:'center'});
  return {details:Boolean(details&&details.open),rows:failed?failed.querySelectorAll('li').length:0};
})()"""


# ── кадр ────────────────────────────────────────────────────────────────────
WAIT_READY = ("document.readyState === 'complete' && typeof view === 'function' "
              "&& !!document.querySelector('.runtime')")


def measure_frame(cdp: CDP, state: str, theme: str, width: int, base: str,
                  campaign: str, allowed: list[str], files: dict, tag: str = '01',
                  prep: dict | None = None) -> dict:
    height = WIDTHS[1][1] if width == 1440 else WIDTHS[0][1]
    if state == 'game-kb':
        height -= KB_HEIGHT
    mobile = width < 700
    cdp.call('Emulation.setDeviceMetricsOverride', width=width, height=height,
             deviceScaleFactor=1, mobile=mobile)
    url = f'{base}/?theme={theme}&campaign={campaign}&audit={state}-{width}'
    # Один кадр из прогона может не догрузиться (вкладка занята прошлым экраном):
    # повторяем навигацию один раз, иначе из-за одного зависшего кадра рвётся
    # весь замер и в отчёте нет цифр.
    for attempt in range(2):
        cdp.call('Page.navigate', url=url)
        # 90 с, а не 30: на общей машине под нагрузкой (стенд, живые прогоны)
        # ответы движка выходят за 20 с, и кадр 1440 не догружался вовсе.
        if cdp.wait_js(WAIT_READY, 90):
            break
        cdp.events.clear()
        time.sleep(1.0)
    else:
        try:
            probe = cdp.eval("({ready:document.readyState,url:location.href,"
                             "view:typeof view,runtime:!!document.querySelector('.runtime')})")
        except Exception as error:  # noqa: BLE001 — диагностика не должна скрывать причину
            probe = f'оценка страницы не ответила: {error}'
        files[state] = {'error': 'страница не догрузилась'}
        return {'state': state, 'theme': theme, 'width': width, 'height': height,
                'error': f'ready timeout (две навигации): {probe}'}
    # поздние скрипты оболочки и меню
    cdp.wait_js("!!window.soloMenu && !!document.querySelector('#nav-drawer')", 40)
    time.sleep(0.7)

    info = {'state': state, 'theme': theme, 'width': width, 'height': height}
    if state == 'home':
        cdp.eval(setup_home(width))
        cdp.wait_js("!!document.querySelector('#welcome.view.active')", 10)
        cdp.wait_js("!!(document.querySelector('#welcome .start__stories .story') ||"
                    " document.querySelector('#welcome .start__continue:not([hidden])'))", 10)
    elif state == 'new':
        cdp.eval(setup_new(width))
        cdp.wait_js("!!document.querySelector('#creation.view.active')", 15)
        cdp.wait_js("!!document.querySelector('#creation-description')", 10)
        cdp.wait_js("!!document.querySelector('#creation .button-row button')", 10)
        time.sleep(1.2)
    elif state == 'hero':
        cdp.wait_js("document.body.classList.contains('campaign-open')", 15)
        cdp.eval(setup_hero(width))
        cdp.wait_js("!!document.querySelector('#character.view.active')", 15)
        cdp.wait_js("!!document.querySelector('#character-content h2')", 10)
        # n13: характеристики приходят из листа героя (API); без ожидания сетки
        # кадр снимался в момент «имя есть, чисел нет».
        cdp.wait_js("document.querySelectorAll('#character .scores-grid > *').length > 0", 15)
        time.sleep(0.5)
    elif state in ('game', 'game-kb'):
        cdp.wait_js("document.body.classList.contains('campaign-open')", 15)
        cdp.eval(setup_game(width))
        cdp.wait_js("!!document.querySelector('#play.view.active')", 15)
        # n13: защита приходит отдельным запросом листа героя
        # (`loadArmorClass` в web/game-screen.js). Без ожидания кадр «game»
        # снимался то с двумя подписями, то с одной («12/12 хиты») — и
        # приёмка «в шапке есть хиты и защита» зависела от гонки, а не от кода.
        cdp.wait_js("document.querySelectorAll('body > main > header .vitals__label').length >= 2", 10)
        time.sleep(1.0)
        cdp.eval("(()=>{const c=document.querySelector('#play #chat');if(c)c.scrollTop=c.scrollHeight;})()")
        if state == 'game-kb':
            # В headless-браузере нет системной клавиатуры: состояние «клавиатура
            # открыта» воспроизводим тем же, что делает mobile.js при сжатии
            # visualViewport, — сжатым кадром (812 − 336 = 476) и классом kb-open.
            cdp.eval("document.body.classList.add('kb-open')")
            time.sleep(0.4)
    elif state == 'notes':
        cdp.wait_js("document.body.classList.contains('campaign-open')", 15)
        cdp.eval(setup_notes(width))
        cdp.wait_js("!!document.querySelector('#journal.view.active')", 15)
        time.sleep(0.5)
    elif state in ('menu', 'menu-app'):
        cdp.eval(setup_menu(state == 'menu-app'))
        cdp.wait_js("(()=>{const d=document.querySelector('#nav-drawer');"
                    "if(!d)return false;const s=getComputedStyle(d);"
                    "return s.display!=='none'&&s.visibility!=='hidden'})()", 10)
        time.sleep(0.5)
        if state == 'menu-app':
            # n14: вкладка «Приложение» переключается кнопкой в самой шторке
            # (setup_menu), прокручивать ко второму блоку больше нечего:
            # блок «Кампания» в этой вкладке скрыт.
            cdp.eval("(()=>{const d=document.querySelector('#nav-drawer');if(d)d.scrollTop=0;})()")
            time.sleep(0.3)
        info['debug'] = cdp.eval("""(()=>{const a=document.querySelector('body > aside.sidebar');
            const d=document.querySelector('#nav-drawer');const ra=a?a.getBoundingClientRect():null;
            const rd=d?d.getBoundingClientRect():null;
            return {bodyClass:document.body.className,
                    burger:Boolean(document.querySelector('#mobile-burger')),
                    aside:ra?{x:Math.round(ra.x),y:Math.round(ra.y),w:Math.round(ra.width),h:Math.round(ra.height)}:null,
                    drawer:rd?{x:Math.round(rd.x),y:Math.round(rd.y),w:Math.round(rd.width),h:Math.round(rd.height)}:null};})()""")
    elif state in ('prep', 'prep-log'):
        prep_draft = (prep or {}).get('draft')
        if not prep_draft:
            raise SystemExit('нет фикстуры подготовки: server.json.fixture.draft пуст')
        info['prep'] = cdp.eval(setup_prep(prep_draft))
        cdp.wait_js("!!document.querySelector('#creation.view.active')", 20)
        # Условие кадра — сама панель подготовки, а не «подождать и снять».
        # Пустая панель или её отсутствие попадают в отчёт как reached=false.
        panel_ok = cdp.wait_js("!!document.querySelector('#creation-progress-panel .prep-panel')", 45)
        info['reached'] = panel_ok
        if panel_ok:
            cdp.wait_js("!!document.querySelector('#creation-progress-panel .prep-fill')", 15)
            # Панель живёт ниже формы, и на 375 без прокрутки её журнал уходит
            # за сгиб. Кадр снимает саму панель: это и есть экран подготовки.
            cdp.eval("(()=>{const p=document.querySelector('#creation-progress-panel .prep-panel');"
                     "if(p)p.scrollIntoView({block:'center'});})()")
            time.sleep(0.6)
        if state == 'prep-log':
            info['prep_log'] = cdp.eval(setup_prep_log())
            time.sleep(0.5)
        panel = cdp.eval("(()=>{const p=document.querySelector('#creation-progress-panel .prep-panel');"
                         "if(!p)return null;const bar=p.querySelector('.prep-bar'),fill=p.querySelector('.prep-fill');"
                         "const failed=p.querySelector('.prep-failed');"
                         "const details=[...document.querySelectorAll('#creation details')]"
                         ".find(node=>/ошиб|журнал/i.test((node.querySelector('summary')||{}).textContent||''));"
                         "return {text:(p.innerText||'').slice(0,500),"
                         "title:((p.querySelector('.prep-title')||{}).textContent||'').trim(),"
                         "caption:((p.querySelector('.prep-caption')||{}).textContent||'').trim(),"
                         "now:((p.querySelector('.prep-now')||{}).textContent||'').trim(),"
                         "steps:((p.querySelector('.prep-steps')||{}).innerText||'').replace(/\\s+/g,' ').trim(),"
                         "barValue:bar?bar.getAttribute('aria-valuenow'):null,"
                         "barText:bar?bar.getAttribute('aria-valuetext'):null,"
                         "fillWidth:fill?getComputedStyle(fill).width:null,"
                         "failed:Boolean(failed&&!failed.hidden),"
                         "failedRows:failed?failed.querySelectorAll('li').length:0,"
                         "journalOpen:Boolean(details&&details.open)}})()")
        info['prep_panel'] = panel
    else:
        raise SystemExit('неизвестное состояние ' + state)

    cdp.eval(f"window.__auditColors={json.dumps(allowed)}")
    result = cdp.eval(MEASURE_JS) or {}
    if state in ('game', 'game-kb'):
        result['chatBlocks'] = cdp.eval(BLOCK_EXPL) or []
    shot = cdp.call('Page.captureScreenshot', format='png')
    name = f'{tag}-{state}-{theme}-{width}.png'
    (OUT / name).write_bytes(base64.b64decode(shot['data']))
    result.update(info)
    result['file'] = name
    errors = [e for e in cdp.events if e.get('method') == 'Runtime.exceptionThrown']
    result['jsErrors'] = [json.dumps(e['params'].get('exceptionDetails', {}).get('text', ''))[:120]
                          for e in errors[-5:]]
    cdp.events.clear()
    return result


# ── отчёт ───────────────────────────────────────────────────────────────────
def build_report(rows: list[dict], meta: dict) -> str:
    states = sorted({r['state'] for r in rows})
    widths = sorted({r['width'] for r in rows})
    themes = sorted({r['theme'] for r in rows})
    lines = [f'# {meta.get("tag", "01")} — замер интерфейса приложения '
             f'({len(rows)} кадров: {len(states)} состояний × {len(widths)} ширины × {len(themes)} темы)', '']
    lines.append(f'Коммит: `{meta["commit"]}` · кадров: **{meta["frames"]}** · '
                 f'приложение: `{meta["base"]}` · профиль: `{meta["data_dir"]}` · '
                 f'браузер: Chromium (ms-playwright 1248) по CDP')
    lines.append('')
    lines.append('Замеры сняты с живого приложения (FastAPI + Chromium): тема — ключ `solo-theme`, '
                 'кампании посеяны движком Nat20. Живой профиль `runtime/web-local/` и порт 19215 '
                 'не использовались. «Видимых интерактивных» — элементы в кадре, не за его краем; '
                 'обрезанные (`scrollWidth > clientWidth + 1`) ищутся по всему документу. '
                 'Порог тап-таргета 44 px — телефонный (рубрика, критерий 5); на 1440 он приведён '
                 'справочно, чтобы видеть разницу между полосой наведения и пальцем.')
    lines.append('')
    lines.append('## Таблица кадров')
    lines.append('')
    lines.append('| состояние | тема | ширина | перелив по X | мин. кегль | тап-таргетов < 44 | контраст < 4.5 | границ < 3:1 | видимых интерактивных | разных кеглей | обрезано |')
    lines.append('|---|---|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        if r.get('error'):
            lines.append(f'| {r["state"]} | {r["theme"]} | {r["width"]} | — | — | — | — | — | — | — | не открылось: {r["error"]} |')
            continue
        mark = ' ⚠' if r['state'] in ('prep', 'prep-log') and r.get('reached') is False else ''
        lines.append('| {state}{mark} | {theme} | {width} | {overflowX} | {fontMin} | {bad} | {con} | {bor} | {inter} | {fd} | {clip} |'.format(
            state=r['state'], mark=mark, theme=r['theme'], width=r['width'], overflowX=r['overflowX'],
            fontMin=r['fontMin'], bad=f"{r['tapsBadCount']} / {r['tapCount']}",
            con=f"{r['contrastBadCount']} (min {r['contrastMin']})" if r['contrastBadCount'] else '0',
            bor=(f"{r['borderBadCount']} (min {r['borderMin']})" if r.get('borderBadCount')
                 else ('0' if r.get('borderChecked') else '—')),
            inter=r['interactiveVisible'], fd=r['fontDistinct'], clip=r['clippedCount']))
    lines.append('')
    ok_rows = [r for r in rows if not r.get('error')]
    measured = [r for r in ok_rows if r.get('contrastChecked') is not None
                or r.get('borderChecked') is not None]
    borders_checked = sum(r.get('borderChecked') or 0 for r in ok_rows)
    borders_bad = sum(r.get('borderBadCount') or 0 for r in ok_rows)
    controls_checked = sum(r.get('controlChecked') or 0 for r in ok_rows)
    cells_checked = sum(r.get('cellChecked') or 0 for r in ok_rows)
    border_mins = [r['borderMin'] for r in ok_rows if r.get('borderMin') is not None]
    texts_checked = sum(r.get('contrastChecked') or 0 for r in ok_rows)
    placeholders = sum(r.get('placeholderChecked') or 0 for r in ok_rows)
    skipped = sum(r.get('contrastSkippedCount') or 0 for r in ok_rows)
    # Причины пропусков — фактические, а не «бывает и так»: иначе строка
    # читается как «226 градиентов», хотя их может не быть ни одного. Кадр
    # держит не больше 25 записей, поэтому поимённых может быть меньше счёта.
    reasons: dict[str, int] = {}
    for row in ok_rows:
        for item in row.get('contrastSkipped') or []:
            key = str(item.get('reason') or 'без-причины')
            reasons[key] = reasons.get(key, 0) + 1
    recorded = sum(reasons.values())
    if not measured:
        lines.append('Контраст: **эти поля этим файлом замера не снимались** (measures до N5) — '
                     'переснимите кадры, иначе «0 нарушений» будет пустым полем.')
    else:
        lines.append(f'Контраст: проверено {texts_checked} текстов и {placeholders} плейсхолдеров; '
                     f'ниже 4.5:1 — **{sum(r.get("contrastBadCount") or 0 for r in ok_rows)}**. '
                     f'Границы контролов (WCAG 1.4.11): границ < 3:1: **{borders_bad}** '
                     f'из {borders_checked} проверенных (контролов {controls_checked}, '
                     f'ячеек листа {cells_checked})'
                     + (f', минимум {min(border_mins)}:1' if border_mins else '')
                     + f'. Не посчитано: **{skipped}**'
                     + (f' (поимённо {recorded}; остальное срезано потолком 25 на кадр)' if recorded < skipped else '')
                     + ('; причины — ' + ', '.join(f'{k} {v}' for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
                        if reasons else '')
                     + '. Поштучно в `contrastSkipped` кадра.')
    lines.append('')
    lines.append('`prep` / `prep-log` — настоящий экран подготовки книги. Стенд кладёт свой короткий текст '
                 '(пять глав, ≤ 5 страниц, ни одного книжного фрагмента) в `runtime/<замер>/books/` и проводит '
                 'его тем же путём, что у игрока: `receive_source` (извлечение локальное, модель не нужна), '
                 'привязка документа к черновику, `source_batches` и `ensure_part` из `preparation_pipeline`. '
                 'Строка `creation_runs` получает те же поля, что пишет `run()`: три части готовы, одна показана '
                 'отказавшей (журнал не пустой), остальные в работе. Модель не зовётся ни разу — вызовов 0, '
                 'расход 0. Кадр снимается по появлению панели `#creation-progress-panel .prep-panel`, а не по '
                 'таймауту; `prep-log` раскрывает журнал (сохранённая история ошибок + список отказавших блоков).')
    lines.append('')
    lines.append('`game-kb` — клавиатура смоделирована так же, как её видит `web/mobile.js`: кадр сжат '
                 'до 476 px (812 − 336) и выставлен класс `kb-open`. Системной клавиатуры в headless '
                 'Chromium нет, поэтому полоса клавиатуры на снимке не рисуется — измеряется только '
                 'сжатое состояние экрана.')
    lines.append('')
    lines.append('Стенд: кампании сеет движок Nat20, и `create_campaign(name)` пишет одно имя и в '
                 'кампанию, и в героя (`solo/nat20_api.py:454`). Поэтому стенд с n08 сеет кампанию '
                 'именем героя («Мирон»), а название истории ставит отдельной строкой таблицы — '
                 'как в продукте, где имя героя и название истории задаются отдельно. Состояние '
                 'движка при этом не подменяется: в рантайме герой так и остаётся «Мироном».')
    lines.append('')
    prep_frames = [r for r in rows if r.get('state') in ('prep', 'prep-log') and r.get('file')]
    if prep_frames:
        import hashlib

        def digest(name: str) -> str | None:
            path = OUT / name
            return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else None

        lines.append('## Приёмка экрана подготовки (n11)')
        lines.append('')
        lines.append('| кадр | md5 | md5 `new` | отличается | полоса | «Сейчас» | журнал отказов | история ошибок |')
        lines.append('|---|---|---|---|---|---|---|---|')
        differ = compared = 0
        for r in prep_frames:
            panel = r.get('prep_panel') or {}
            mine = digest(r['file'])
            other = digest(f'{meta.get("tag", "01")}-new-{r["theme"]}-{r["width"]}.png')
            if other:
                compared += 1
                differ += int(mine != other)
                verdict = 'нет' if mine == other else 'да'
            else:
                verdict = '— (`new` не снимался)'
            bar = f'{panel.get("barText") or "—"} · {panel.get("fillWidth") or "—"}'
            lines.append(f'| `{r["file"]}` | `{mine}` | `{other or "—"}` | {verdict} | {bar} | '
                         f'{panel.get("now") or "нет"} | {panel.get("failedRows", 0)} строк | '
                         f'{"раскрыта" if panel.get("journalOpen") else "свёрнута"} |')
        lines.append('')
        if compared:
            lines.append(f'Кадров подготовки, отличных от `new` той же ширины и темы: **{differ} из {compared}**.')
            lines.append('')
    lines.append('## Приёмка n13 (точечные проверки кадра)')
    lines.append('')
    lines.append('Числа сняты одним и тем же JS-замером (`probe` в `MEASURE_JS`): '
                 '`y`/`bottom` — координаты в кадре, `строк` — сколько строк текста в поле ввода '
                 '(высота содержимого / line-height), переносы подписей меню считаются по границам '
                 'символов (`Range`), а не по высоте кнопки.')
    lines.append('')
    lines.append('| кадр | поле ввода: y / bottom / строк | «Подготовить» в кадре | шапка статов | '
                 'характеристик: колонок / низ последней / в окне | меню: подписей / переносов / разорванных | '
                 '«компьютере» | внутренние слова |')
    lines.append('|---|---|---|---|---|---|---|---|')
    for r in rows:
        p = r.get('probe') or {}
        d = p.get('description') or {}
        drect = d.get('rect') or {}
        prep = p.get('prepare') or {}
        prect = prep.get('rect') or {}
        ab = p.get('abilities') or {}
        words = p.get('words') or {}
        nav = p.get('navLabels') or []
        vit = (p.get('headerVitals') or {})
        inner = [name for name, flag in (('Нейросети и API', words.get('neuralApi')),
                                         ('Расходы API', words.get('costsApi')),
                                         ('Производительность', words.get('performance'))) if flag]
        lines.append('| `{file}` | {dy} / {db} / {dl} | {prep} | {vit} | {cols} / {low} / {inside} | '
                     '{nav} / {wrap} / {brk} | {comp} | {inner} |'.format(
                         file=r.get('file') or f'{r["state"]}-{r["theme"]}-{r["width"]}',
                         dy=drect.get('y', '—'), db=drect.get('bottom', '—'), dl=d.get('lines', '—'),
                         prep=('есть, y {:.0f}'.format(prect['y']) if prect.get('inView')
                               else 'НЕТ в кадре' if prep else '—'),
                         vit=(vit.get('text') or '—')[:34],
                         cols=ab.get('columns', '—'), low=ab.get('lastBottom', '—'),
                         inside=('да' if ab.get('allInside') else 'НЕТ') if ab.get('count') else '—',
                         nav=len(nav) or '—', wrap=p.get('navWrapped', '—'), brk=p.get('navBroken', '—'),
                         comp='ЕСТЬ' if words.get('computer') else '0',
                         inner=', '.join(inner) or '0'))
    lines.append('')

    lines.append('## Приёмка n14 (меню на вкладки, хиты, главная, блокнот)')
    lines.append('')
    lines.append('Числа сняты тем же JS-замером (`probe`): `вкладка` — открытая вкладка шторки и '
                 'сколько строк реально видно в её группе (скрытая группа не считается); '
                 '`HP` — сколько раз подпись «HP» с числом встречается в панели героя и во всём '
                 'видимом тексте документа; `главная` — секции-списки истории и кнопка «Все истории»; '
                 '`блокнот` — пустые секции, кнопки сохранения и свободная ширина справа.')
    lines.append('')
    lines.append('| кадр | вкладка шторки | строк в группе | строк в шторке | перелив шторки | '
                 '«HP» в панели / в документе | главная: секций / «Все истории» | '
                 'блокнот: пустых секций / сохранений / справа пусто |')
    lines.append('|---|---|---|---|---|---|---|---|')
    for r in rows:
        p = r.get('probe') or {}
        t = p.get('drawerTabs') or {}
        h = p.get('home') or {}
        j = p.get('journal') or {}
        hp = p.get('heroPanel') or {}
        tab = t.get('tab') or '—'
        if tab != '—':
            per = ' · '.join(f'{b["title"]}: {b["rows"]}' if b['title'] else f'{b["rows"]}'
                             for b in t.get('perBlock') or [] if b['rows'])
            tab = f'{tab} ({per or "нет строк"})'
        home = (f'{h.get("sections")} / {h.get("allStories")}'
                if h.get('sections') else '—')
        journal = (f'{j.get("emptySections")} / {j.get("saveButtons")} / {j.get("emptyRightPct")} %'
                   if j.get('width') else '—')
        lines.append('| `{file}` | {tab} | {rows} | {allrows} | {ov} | {hp} | {home} | {journal} |'.format(
            file=r.get('file') or f'{r["state"]}-{r["theme"]}-{r["width"]}',
            tab=tab,
            rows=(sum(b['rows'] for b in t.get('perBlock') or []) if t else '—'),
            allrows=t.get('rows', '—'), ov=t.get('overflow', '—'),
            hp=(f'{hp.get("hpWords")} / {hp.get("documentHp")}' if hp else '—'),
            home=home, journal=journal))
    lines.append('')
    menu_pairs = []
    for width in widths:
        for theme in themes:
            a = next((r for r in rows if r['state'] == 'menu' and r['width'] == width
                      and r['theme'] == theme and r.get('file')), None)
            b = next((r for r in rows if r['state'] == 'menu-app' and r['width'] == width
                      and r['theme'] == theme and r.get('file')), None)
            if a and b:
                menu_pairs.append((a, b))
    if menu_pairs:
        import hashlib

        def frame_md5(name: str) -> str:
            path = OUT / name
            return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else '—'

        lines.append('| ширина | тема | md5 `menu` | md5 `menu-app` | отличаются |')
        lines.append('|---|---|---|---|---|')
        differ = 0
        for a, b in menu_pairs:
            ma, mb = frame_md5(a['file']), frame_md5(b['file'])
            differ += int(ma != mb)
            lines.append(f'| {a["width"]} | {a["theme"]} | `{ma}` | `{mb}` | '
                         f'{"да" if ma != mb else "НЕТ — кадры равны"} |')
        lines.append('')
        lines.append(f'Кадров `menu-app`, отличных от `menu` той же ширины и темы: '
                     f'**{differ} из {len(menu_pairs)}**.')
        lines.append('')

    lines.append('## Что не открылось и почему')
    lines.append('')
    bad = [r for r in rows if r.get('error') or r.get('reached') is False]
    if not bad:
        lines.append(f'- нет: все {len(rows)} кадров сняты с живых экранов приложения.')
    for r in bad:
        reason = (r.get('error') or
                  'экран создания открыт, панели подготовки книги нет: фикстура подготовки не собралась '
                  '(см. `runtime/<замер>/fixture.json`)')
        lines.append(f'- `{r["state"]}-{r["theme"]}-{r["width"]}`: {reason}')
    lines.append('')

    def worst(getter, limit=6):
        frames: dict[tuple, list[dict]] = {}
        for row in rows:
            if row.get('error'):
                continue
            frames.setdefault((row['state'], row['width']), []).append(row)
        scored = []
        for (state, width), group in frames.items():
            best = max(group, key=lambda row: getter(row) or 0)
            value = getter(best) or 0
            if value:
                scored.append((value, state, width, len(group), best))
        scored.sort(key=lambda item: -item[0])
        return scored[:limit]

    lines.append('## Где именно болит (по числам)')
    lines.append('')
    overflow = [r for r in rows if r.get('overflowX')]
    if overflow:
        top = max(overflow, key=lambda r: r['overflowX'])
        lines.append(f'- горизонтальный перелив: **{len(overflow)}** кадров, худший — '
                     f'`{top["state"]}` {top["theme"]} {top["width"]}: +{top["overflowX"]} px '
                     f'(scrollWidth {top["scrollWidth"]} при окне {top["width"]})')
    else:
        # «во всех N кадрах» — только про СНЯТЫЕ: упавший кадр не измерен, и
        # приписывать ему ноль перелива значит врать тем же пустым полем.
        shot = [r for r in rows if not r.get('error') and r.get('scrollWidth') is not None]
        lines.append(f'- горизонтальный перелив: **0 px во всех {len(shot)} снятых кадрах** '
                     '(`scrollWidth` равен ширине окна)'
                     + (f'; не снято {len(rows) - len(shot)} — см. «Что не открылось и почему»'
                        if len(shot) < len(rows) else ''))
    for value, state, width, nthemes, r in worst(lambda row: row.get('tapsBadCount')):
        sample = '; '.join(f'`{t["sel"]}` {t["w"]}×{t["h"]}' for t in r['tapsBad'][:3])
        lines.append(f'- тап-таргеты < 44 px: **{value}** из {r["tapCount"]} — `{state}` {width} '
                     f'(в {nthemes} темах); худшие: {sample}')
    if any(r.get('tapsBadCount') and r['width'] >= 700 for r in rows):
        lines.append('- на 1440 порог 44 px — справочный (это мышь, а не палец): промахи там дают '
                     'боковое меню, знак и переключатель темы (`a.brand` 21 px, `button.nav` 38 px), '
                     'тот же набор, что на `new`; к панели подготовки претензий нет.')
    for value, state, width, nthemes, r in worst(lambda row: row.get('contrastBadCount')):
        sample = '; '.join(f'`{t["sel"]}` {t["ratio"]}:1' for t in r['contrastBad'][:3])
        lines.append(f'- контраст < 4.5:1: **{value}** текстов — `{state}` {width} (в {nthemes} темах); '
                     f'худшие: {sample}')
    for value, state, width, nthemes, r in worst(lambda row: row.get('borderBadCount')):
        sample = '; '.join(f'`{t["sel"]}` ({t.get("what", "рамка")}, {t["side"]}, {t["w"]} px) '
                           f'{t["ratio"]}:1' for t in r['borderBad'][:3])
        lines.append(f'- границы контролов < 3:1: **{value}** элементов из {r.get("borderChecked")} — '
                     f'`{state}` {width} (в {nthemes} темах); худшие: {sample}')
    for value, state, width, nthemes, r in worst(lambda row: 0 if row.get('fontMin') is None else 200 - row['fontMin']):
        sample = '; '.join(f'`{t["sel"]}` {t["px"]} px «{t["text"]}»' for t in (r.get('smallText') or [])[:3])
        tail = f'; мельче 14 px: {sample}' if sample else ''
        lines.append(f'- минимальный кегль **{r["fontMin"]} px** — `{state}` {width} '
                     f'(в {nthemes} темах, кеглей на экране: {r["fontDistinct"]}){tail}')
    for value, state, width, nthemes, r in worst(lambda row: row.get('clippedCount')):
        sample = '; '.join(f'`{c["sel"]}` {c["sw"]}>{c["cw"]}' for c in r['clipped'][:3])
        lines.append(f'- обрезано (scrollWidth > clientWidth + 1): **{value}** элементов — `{state}` {width} '
                     f'(в {nthemes} темах); {sample}')
    seen_drawer = set()
    for r in rows:
        drawer = r.get('drawer')
        if not drawer or (r['state'], r['width']) in seen_drawer:
            continue
        seen_drawer.add((r['state'], r['width']))
        lines.append(f'- шторка `{r["state"]}` {r["width"]}: высота {drawer["h"]} при окне {r["height"]}, '
                     f'строк {drawer["rows"]}, групп {len(drawer["blocks"])} ({", ".join(drawer["blocks"])}), '
                     f'прокрутка {drawer["scroll"]} px (scrollTop {drawer["scrollTop"]})')
    colors = {}
    for r in rows:
        for c in r.get('colorsOutside') or []:
            colors[(c['color'], c['sample'])] = colors.get((c['color'], c['sample']), 0) + c['count']
    top = sorted(colors.items(), key=lambda kv: -kv[1])[:8]
    lines.append('')
    lines.append('## Цвета вне `web/design-tokens.css`')
    lines.append('')
    if not top:
        lines.append('- все вычисленные цвета текста, фона и рамок входят в палитру `web/design-tokens.css` '
                     '(белый и чёрный считаются допустимыми).')
    for (color, sample), count in top:
        lines.append(f'- `{color}` — {count} применений, например `{sample}`')
    lines.append('')
    lines.append('## Как повторить')
    lines.append('')
    lines.append('```')
    lines.append('.venv/bin/python tools/ui_audit.py            # 40 кадров + <tag>-measures.json + <tag>-report.md')
    lines.append('.venv/bin/python tools/ui_audit.py --tag 02     # повторный замер в 02-report.md, кадры 01 не трогаются')
    lines.append('.venv/bin/python tools/ui_audit.py --only new,prep,prep-log   # экран подготовки против «Новое приключение»')
    lines.append('```')
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true', help='внутренний режим: только сервер')
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA))
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--only', default='')
    parser.add_argument('--widths', default='375,1440')
    parser.add_argument('--themes', default='dark,light')
    parser.add_argument('--no-server', action='store_true', help='сервер уже поднят, взять порт из server.json')
    parser.add_argument('--report-only', action='store_true', help='собрать 01-report.md из готового 01-measures.json')
    parser.add_argument('--tag', default='01', help='префикс файлов замера: <tag>-report.md, <tag>-measures.json, <tag>-<кадр>.png')
    parser.add_argument('--merge', action='store_true',
                        help='дописать кадры в готовый <tag>-measures.json (перезаписав те же состояние/тема/ширина)')
    args = parser.parse_args()

    if args.report_only:
        data = json.loads((OUT / f'{args.tag}-measures.json').read_text(encoding='utf-8'))
        data['meta']['tag'] = args.tag
        (OUT / f'{args.tag}-report.md').write_text(build_report(data['frames'], data['meta']) + '\n', encoding='utf-8')
        print(f'отчёт пересобран из {args.tag}-measures.json', flush=True)
        return 0

    if args.serve:
        serve(args)
        return 0

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if not os.path.exists(CHROME):
        raise SystemExit(f'нет Chromium: {CHROME}')

    only = [s for s in args.only.split(',') if s] or STATES
    widths = [int(w) for w in args.widths.split(',') if w]
    themes = [t for t in args.themes.split(',') if t]
    allowed = token_colors()

    t0 = time.time()
    server, port, campaigns, fixture = start_server(data_dir)
    base = f'http://127.0.0.1:{port}'
    print(f'сервер: {base} (профиль {data_dir})', flush=True)
    chrome, cdp_port = start_chrome(data_dir / 'chrome-profile')
    rows: list[dict] = []
    try:
        cdp = CDP(page_target(cdp_port))
        cdp.call('Page.enable')
        cdp.call('Runtime.enable')
        # Кэш Chromium переживает прогон: профиль `chrome-profile` лежит в
        # каталоге замера, и тот же `/static/prepare-progress.js?v=n11` после
        # правки файла отдавался со диска старым — на кадре была прежняя панель
        # (n11: нулевой кегль и подпись «Сейчас» искали не там).
        cdp.call('Network.enable')
        cdp.call('Network.setCacheDisabled', cacheDisabled=True)
        cdp.call('Page.addScriptToEvaluateOnNewDocument', source=(
            "(()=>{try{const q=new URLSearchParams(location.search);"
            "const t=q.get('theme');if(t)localStorage.setItem('solo-theme',t);"
            "localStorage.setItem('solo-start-page','welcome');"
            "const c=q.get('campaign');if(c)localStorage.setItem('solo-campaign',c);"
            "}catch(e){}})();"))
        total = len(only) * len(widths) * len(themes)
        done = 0
        for width in widths:
            for theme in themes:
                for state in only:
                    done += 1
                    try:
                        row = measure_frame(cdp, state, theme, width, base, campaigns[0], allowed, {}, tag=args.tag,
                                            prep=fixture)
                    except Exception as error:  # noqa: BLE001 — кадр может не открыться, это фиксируем
                        row = {'state': state, 'theme': theme, 'width': width, 'error': str(error)[:300]}
                        print(f'[{done}/{total}] {state} {theme} {width}: ОШИБКА {error}', flush=True)
                        continue
                    rows.append(row)
                    label = row.get('file') or f'{state}-{theme}-{width}'
                    print(f'[{done}/{total}] {label}: перелив {row.get("overflowX")}, '
                          f'кегль {row.get("fontMin")}, тап<44 {row.get("tapsBadCount")}, '
                          f'контраст<4.5 {row.get("contrastBadCount")}, '
                          f'границ<3:1 {row.get("borderBadCount")} '
                          f'из {row.get("borderChecked")}, скипнуто {row.get("contrastSkippedCount")}, '
                          f'элементов {row.get("interactiveVisible")}'
                          + (f', ОШИБКА {row["error"]}' if row.get('error') else ''),
                          flush=True)
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        chrome.terminate()
        server.terminate()
        for proc in (chrome, server):
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    if args.merge:
        # Долгий замер под нагрузкой рвётся на одном кадре: догрузить
        # недостающие дешевле, чем снимать все сорок заново.
        previous = OUT / f'{args.tag}-measures.json'
        if previous.exists():
            kept = {(r['state'], r['theme'], r['width']): r
                    for r in json.loads(previous.read_text(encoding='utf-8'))['frames']}
            for row in rows:
                kept[(row['state'], row['theme'], row['width'])] = row
            rows = sorted(kept.values(),
                          key=lambda r: (r['width'], r['theme'], STATES.index(r['state'])
                                         if r['state'] in STATES else 99))
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    meta = {'commit': commit, 'frames': len([r for r in rows if r.get('file')]),
            'base': base, 'data_dir': str(data_dir), 'tag': args.tag}
    (OUT / f'{args.tag}-measures.json').write_text(
        json.dumps({'meta': meta, 'frames': rows}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (OUT / f'{args.tag}-report.md').write_text(build_report(rows, meta) + '\n', encoding='utf-8')
    print(f'готово за {time.time() - t0:.0f} с: {meta["frames"]} кадров, '
          f'{OUT / (args.tag + "-report.md")}', flush=True)
    return 0


def token_colors() -> list[str]:
    text = (ROOT / 'web/design-tokens.css').read_text(encoding='utf-8')
    out = set()
    for m in re.finditer(r'#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b', text):
        h = m.group(1)
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        out.add(','.join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4)))
    for m in re.finditer(r'rgba?\(([^)]+)\)', text):
        parts = [p.strip() for p in m.group(1).replace('/', ',').split(',')]
        try:
            nums = [int(round(float(p))) for p in parts[:3]]
        except ValueError:
            continue
        if len(nums) == 3:
            out.add(','.join(map(str, nums)))
    out.update({'255,255,255', '0,0,0'})
    return sorted(out)


if __name__ == '__main__':
    sys.exit(main())
