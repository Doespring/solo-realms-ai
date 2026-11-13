"""r245: сквозной прогон нашёл три обрыва подготовки — сторожа на каждый.

Замер «книга → герой → первый ход» на живом приложении (06.11.2026) показал:

1. **Та же книга, разобранная целиком, но закрытая `partial`, платится заново.**
   В живой базе прогон `2e18c595` по «Rime of the Frostmaiden»: 96 из 96 блоков
   приняты, мир собран, `playable=1`, статус `partial`. `reuse_book` берёт
   только `ready`/`blocked`, поэтому новый черновик по той же книге ушёл в
   повторный разбор: 57 блоков, 17 вызовов, 3,08 ₽ и ETA 41 минута.
2. **Такой прогон не закрывается и сам:** `resume_pending` собирает только
   `blocked`/`partial` с несобранным миром (`needs_assembly`), а прогон с
   готовым `result` пропускает — и статус «частично готово» живёт вечно,
   хотя продолжать нечего (`pending=0`).
3. **`playable`-мир запирается, когда прогон перестал идти.** Сообщение
   «Можно играть: готово 8 из 57» показывается и после отмены, а
   `world-readiness`/`confirm-world` отвечают «Подготовка не завершена: 8 / 57»:
   у игрока нет ни игры, ни пути, кроме оплаченного продолжения.

Проверки ниже держат три инварианта и одну границу: полностью разобранный мир
не оплачивается дважды; готовый мир не остаётся «частично готовым»; открытый
вход не запирается отменой; прогон **без** открытого входа по-прежнему
требует продолжения.
"""
import asyncio
import json
import time

from fastapi.testclient import TestClient

from solo.app import create_app
from test_r236_worlds import book_draft, prepare_world
from test_r237_playable_start import PlayableFixture, book_text


def calls(model):
    return sum(row['kind'] == 'preparation_world' for row in model.pdf_smoke_packets)


def smoke(tmp_path):
    from scripts.smoke_fixture import make_pdf_world_smoke_llm
    return make_pdf_world_smoke_llm(tmp_path, smoke_test=True, phase='prepare')


def leave_partial(pipeline, run_id, message='Готово 1 из 1 блоков, остальные в очереди. Продолжу сам.'):
    """Живое состояние базы 06.11: блоки приняты, мир готов, статус — `partial`."""
    with pipeline.preparation.connection() as db:
        db.execute('UPDATE creation_runs SET status="partial",message=? WHERE id=?', (message, run_id))


def test_a_fully_read_book_left_partial_is_not_paid_for_twice(tmp_path):
    """Второй черновик той же книги берёт готовый разбор, а не новый счёт."""
    model = smoke(tmp_path)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', model, experimental=True, offline_mock=True)) as client:
        first_url = book_draft(client)
        first = prepare_world(client, first_url, 'r245-first')
        assert first['ready'], first
        assert calls(model) == 1
        pipeline = client.app.state.preparation_pipeline
        leave_partial(pipeline, first['id'])
        second_url = book_draft(client, 'Тот же текст, другой замысел')
        second = prepare_world(client, second_url, 'r245-second')
        assert second['reused'] is True, second
        assert second['status'] == 'ready', second
        assert 'Эта книга уже разобрана' in second['message']
        assert calls(model) == 1, model.pdf_smoke_packets


def test_a_ready_world_left_partial_is_closed_as_ready_at_start(tmp_path):
    """Готовый мир не висит «частично готовым»: счётчик полон, работы нет."""
    model = smoke(tmp_path)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', model, experimental=True, offline_mock=True)) as client:
        url = book_draft(client)
        run = prepare_world(client, url, 'r245-close')
        assert run['ready'], run
        pipeline = client.app.state.preparation_pipeline
        leave_partial(pipeline, run['id'])
        calls_before = calls(model)
        raised = asyncio.run(pipeline.resume_pending())
        assert run['id'] in raised, raised
        status = pipeline.status(run['id'])
        assert status['status'] == 'ready', status
        assert status['done'] == status['total'] and status['done'] > 0, status
        assert 'в очереди' not in status['message'], status['message']
        assert calls(model) == calls_before, 'закрытие статуса сходило в модель'


def test_a_playable_world_can_be_confirmed_after_the_run_stops(tmp_path, monkeypatch):
    """«Можно играть» — это вход, даже если разбор остановлен отменой."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '8000')
    llm = PlayableFixture(delay=0.5)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        draft = client.post('/api/creation/drafts').json()
        url = '/api/creation/drafts/' + draft['id']
        client.put(url, json=dict(description='Синтетическая книга, D&D 2014', revision=draft['revision']))
        client.post(url + '/files', files={'file': ('kniga.txt', book_text().encode())}).raise_for_status()
        deadline = time.monotonic() + 30
        while any(f['job']['status'] != 'complete' for f in client.get(url).json()['files']):
            assert time.monotonic() < deadline
            time.sleep(.02)
        started = client.post(url + '/prepare', json=dict(request_id='r245-cancel', world_structure=True,
                                                          ruleset='dnd2014', consent_to_send=True))
        assert started.status_code == 200, started.text
        run = started.json()['id']
        opened = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            view = client.get('/api/creation/runs/' + run).json()
            if view.get('playable') and view['status'] == 'running':
                opened = view
                break
            assert view['status'] in ('queued', 'running'), view
            time.sleep(.02)
        assert opened is not None, 'вход не открылся до отмены'
        cancelled = client.post('/api/creation/runs/' + run + '/cancel')
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()['status'] == 'cancelled'
        readiness = client.get(url + '/world-readiness')
        assert readiness.status_code == 200, readiness.text
        body = readiness.json()
        assert body['world_ready'], body
        confirmed = client.post(url + '/confirm-world', json=dict(
            expected_revision=body['revision'], run_id=body['run_id'], confirm=True))
        assert confirmed.status_code == 200, confirmed.text


def test_a_stopped_run_without_an_open_entry_still_requires_continuation(tmp_path, monkeypatch):
    """Граница: без открытого входа отмена не даёт играть — отказ остаётся."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '6000')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY', '1')
    from test_preparation_resume_r238 import PoisonFixture
    from test_preparation_sections import SectionFixture, book, upload
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '2600')
    llm = SectionFixture(limit=10 ** 9)
    with TestClient(create_app(tmp_path / 'solo.sqlite3', llm)) as client:
        draft = client.post('/api/creation/drafts').json()
        url = '/api/creation/drafts/' + draft['id']
        client.put(url, json=dict(description='По книге, D&D 2014', revision=draft['revision']))
        upload(client, url, book(chapters=2, paragraphs=12))
        started = client.post(url + '/prepare', json=dict(request_id='r245-nocancel',
                                                          ruleset='dnd2014', world_structure=True,
                                                          consent_to_send=True))
        assert started.status_code == 200, started.text
        run = started.json()['id']
        deadline = time.monotonic() + 90
        while True:
            view = client.get('/api/creation/runs/' + run).json()
            if view['status'] not in ('queued', 'running'):
                break
            assert time.monotonic() < deadline, view
            time.sleep(.02)
        pipeline = client.app.state.preparation_pipeline
        # Обрываем прогон до сборки мира: блоки прыгают в никуда, входа нет.
        with pipeline.preparation.connection() as db:
            db.execute('UPDATE creation_runs SET status="cancelled",playable=0,result="" WHERE id=?', (run,))
        readiness = client.get(url + '/world-readiness').json()
        assert readiness['world_ready'] is False, readiness
        assert readiness['reasons'], readiness
        assert 'Подготовка не завершена' in '; '.join(readiness['reasons'])
