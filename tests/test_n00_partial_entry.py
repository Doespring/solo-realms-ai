"""n00: после `partial` глава остаётся входом в игру, и экран говорит это словами.

Живой прогон r239 (книга 323 страницы) закончился `partial`: 96 из 96 блоков,
0 упавших. `world-readiness` отвечал `world_ready=false` с причиной
«Подготовка не завершена: 96 / 96. Продолжите сохранённые блоки.», и кнопки
входа исчезали. Замок «вход только пока идёт чтение» снят в r245 (dd88c4e6).

Здесь держится остальное, что нужно экрану:

* два разных вопроса названы по отдельности: `partial_usable` — «глава собрана,
  можно начинать» и `ready` — «мир подтверждён»; порог канона не ослаблен
  (`canon_ready` — та же проверка полного мира, что и раньше);
* вместе с флагом приходят числа главы (`blocks_done` / `blocks_total`);
* отказ входа, когда сборка не сошлась, называет кнопку, которая это лечит,
  а не «Продолжите сохранённые блоки».

Модель синтетическая: ни одного платного вызова.
"""
import json
import time

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import MockLLM
from nat20_builds import legal
from test_r237_playable_start import PlayableFixture, book_text


class ChapterFixture(PlayableFixture):
    """Готовит мир как r237, а на ход игрока отвечает мастером, а не падает."""

    async def stream(self, system, user, **kwargs):
        if kwargs.get('mode') == 'preparation_world':
            async for chunk in super().stream(system, user, **kwargs):
                yield chunk
            return
        async for chunk in MockLLM.stream(self, system, user, **kwargs):
            yield chunk


def opened_chapter(client, tag):
    """Черновик, книга и подготовка до момента «глава собрана, вход открыт»."""
    draft = client.post('/api/creation/drafts').json()
    url = '/api/creation/drafts/' + draft['id']
    client.put(url, json=dict(description='Синтетическая книга, D&D 2014', revision=draft['revision']))
    client.post(url + '/files', files={'file': ('kniga.txt', book_text().encode())}).raise_for_status()
    deadline = time.monotonic() + 30
    while any(f['job']['status'] != 'complete' for f in client.get(url).json()['files']):
        assert time.monotonic() < deadline
        time.sleep(.02)
    started = client.post(url + '/prepare', json=dict(request_id=tag, world_structure=True,
                                                      ruleset='dnd2014', consent_to_send=True))
    assert started.status_code == 200, started.text
    run = started.json()['id']
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        view = client.get('/api/creation/runs/' + run).json()
        if view.get('playable') and view['status'] == 'running':
            return url, run, view
        assert view['status'] in ('queued', 'running'), view
        time.sleep(.02)
    raise AssertionError('глава не открылась, пока книга читалась')


def leave_r239_partial(pipeline, run_id, *, playable=1):
    """Ровно та строка базы, что оставил прогон r239: всё разобрано, статус `partial`."""
    with pipeline.preparation.connection() as db:
        row = db.execute('SELECT done,total FROM creation_runs WHERE id=?', (run_id,)).fetchone()
        total = max(row['done'], row['total'])
        db.execute('UPDATE creation_runs SET status="partial",done=?,total=?,playable=? WHERE id=?',
                   (total, total, playable, run_id))


def app(tmp_path, llm):
    # Прототипный вход (`/api/prototype/campaigns`) живёт в экспериментальной сборке —
    # без флага сервер отвечает 404, и «кнопка ведёт в игру» проверялась бы не туда.
    return TestClient(create_app(tmp_path / 'solo.sqlite3', llm, experimental=True))


def test_a_partial_chapter_is_usable_and_says_so(tmp_path, monkeypatch):
    """96 из 96 и `partial`: глава — вход, и ответ это называет."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '8000')
    llm = PlayableFixture(delay=0.5)
    with app(tmp_path, llm) as client:
        url, run, opened = opened_chapter(client, 'n00-usable')
        client.post('/api/creation/runs/' + run + '/cancel')
        leave_r239_partial(client.app.state.preparation_pipeline, run)
        readiness = client.get(url + '/world-readiness')
        assert readiness.status_code == 200, readiness.text
        body = readiness.json()
        assert body['world_ready'] is True, body
        assert body['partial_usable'] is True, body
        assert body['ready'] is False, body
        assert body['blocks_done'] == body['blocks_total'] > 0, body
        assert 'Глава собрана' in ' '.join(body['reasons']), body
        assert 'Продолжите сохранённые блоки' not in ' '.join(body['reasons']), body


def test_the_entry_button_leads_to_a_turn(tmp_path, monkeypatch):
    """Кнопка входа ведёт в кампанию, и первый ход получает ответ мастера."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '8000')
    llm = ChapterFixture(delay=0.5)
    with app(tmp_path, llm) as client:
        url, run, _opened = opened_chapter(client, 'n00-entry')
        client.post('/api/creation/runs/' + run + '/cancel')
        leave_r239_partial(client.app.state.preparation_pipeline, run)
        readiness = client.get(url + '/world-readiness').json()
        assert readiness['partial_usable'] is True, readiness
        confirmed = client.post(url + '/confirm-world', json=dict(
            expected_revision=readiness['revision'], run_id=readiness['run_id'], confirm=True))
        assert confirmed.status_code == 200, confirmed.text[:800]
        after = client.get(url + '/world-readiness').json()
        assert after['ready'] is True and after['world_ready'] is True, after
        # Подтверждение мира само по себе меняет черновик (revision), и вход
        # обязан сверяться с той редакцией, что стоит сейчас, а не с той,
        # которую игрок видел до нажатия.
        assert after['revision'] > readiness['revision'], (readiness, after)
        draft = url.rsplit('/', 1)[-1]
        launched = client.post('/api/prototype/campaigns', json=dict(
            name='Рив', request_id='n00-start', build=legal(class_slug='fighter', species_slug='human', level=1),
            adventure='prepared:' + draft, prepared_spells=[], encounter_monsters=[],
            description='Синтетическая книга, D&D 2014', mechanics_ruleset='dnd2024',
            rules_confirmed=True, hero_confirmed=True, background_confirmed=True,
            hero_background='Ищет дорогу', adventure_revision=after['revision']))
        assert launched.status_code == 200, launched.text[:800]
        campaign = launched.json()['id']
        version = client.get('/api/campaigns/' + campaign).json()['version']
        turn = client.post('/api/campaigns/' + campaign + '/command-turn', json=dict(
            request_id='n00-turn-1', text='Осматриваюсь по сторонам.', expected_version=version))
        assert turn.status_code == 200, turn.text[:800]
        answer = turn.json()
        assert answer['narrative'].strip(), answer


def test_unassembled_chapter_names_the_button_that_heals_it(tmp_path, monkeypatch):
    """Сборка не сошлась: отказ говорит, какую кнопку нажать, и не врёт про остаток."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '8000')
    llm = PlayableFixture(delay=0.5)
    with app(tmp_path, llm) as client:
        url, run, _opened = opened_chapter(client, 'n00-unassembled')
        client.post('/api/creation/runs/' + run + '/cancel')
        leave_r239_partial(client.app.state.preparation_pipeline, run, playable=0)
        body = client.get(url + '/world-readiness').json()
        assert body['world_ready'] is False, body
        assert body['partial_usable'] is False, body
        reason = ' '.join(body['reasons'])
        assert 'Собрать мир из готовых блоков' in reason, body
        assert 'Продолжите сохранённые блоки' not in reason, body
        assert body['blocks_done'] == body['blocks_total'] > 0, body


def test_legacy_hero_flag_does_not_block_confirmation(tmp_path, monkeypatch):
    """Черновик прежнего мастера героя подтверждается, а не падает 409.

    r239-черновик несёт `hero_confirmed` — отметку прежнего мастера героя, которой
    новый договор уже не пользуется. Строгий разбор отвечал «Extra inputs are not
    permitted», и `confirm-world` на 96 / 96 отвечал 409: игрок снова оставался на
    экране подготовки со «Выбрать героя», которое никуда не ведёт.
    """
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS', '8000')
    llm = PlayableFixture(delay=0.5)
    with app(tmp_path, llm) as client:
        url, run, _opened = opened_chapter(client, 'n00-legacy')
        client.post('/api/creation/runs/' + run + '/cancel')
        leave_r239_partial(client.app.state.preparation_pipeline, run)
        draft = url.rsplit('/', 1)[-1]
        with client.app.state.preparation.connection() as db:
            body = json.loads(db.execute('SELECT body FROM drafts WHERE id=?', (draft,)).fetchone()['body'])
            body['hero_confirmed'] = False
            db.execute('UPDATE drafts SET body=? WHERE id=?', (json.dumps(body, ensure_ascii=False), draft))
        readiness = client.get(url + '/world-readiness').json()
        assert readiness['partial_usable'] is True, readiness
        confirmed = client.post(url + '/confirm-world', json=dict(
            expected_revision=readiness['revision'], run_id=readiness['run_id'], confirm=True))
        assert confirmed.status_code == 200, confirmed.text[:800]
        assert client.get(url + '/world-readiness').json()['ready'] is True
