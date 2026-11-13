"""Chat-only shell (?ui=chat) — one screen: the story feed and the composer.

Every panel is built by its own script, so the mode drops those <script> tags and
the markup between the cut markers of web/index.html. Nothing is hidden with CSS:
a hidden panel stays in the DOM, stays clickable by accident and stays readable by
a screen reader. The list of what survives is declared once, in
solo.app.CHAT_ONLY_SCRIPTS, and this file reads that list instead of keeping a
second copy that would drift away from the application.
"""
import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo.app import CHAT_ONLY_SCRIPTS, ROOT, create_app
from solo.contracts import Plan, Step
from solo.llm import CommandMockLLM

# The list from the card: these panels must not be loaded in the chat shell.
PANEL_SCRIPTS = ('game-screen.js', 'inventory.js', 'tabletop.js', 'prototype.js',
                 'workspace.js', 'chronicle.js', 'adventure.js',
                 'navigation.js', 'preparation.js', 'rulesets.js', 'world.js',
                 'obsidian.js', 'desktop.js')

# Решение владельца 08.11: в игровом экране нет кнопок действия — только лента
# и поле ввода. Панель «Оружие, магия, предметы и цели» (prototype-actions.js)
# снята со страницы целиком, а не спрятана: скрытая панель остаётся в DOM и
# остаётся достижимой с клавиатуры. Сторож ниже держит это правило.
REMOVED_FROM_FULL_PAGE = ('prototype-actions.js',)

# The body web/play.js sends from the create dialog on the chat screen: the
# ruleset/species/class fields belong to rulesets.js, which the mode does not
# load, so the dialog falls back to the same defaults the server already uses.
CREATE_BODY = dict(name='Тайна трёх сосен', ruleset='dnd2024', world_mode='story', map_travel=False,
                   hero=dict(name='Мирон', background='', species='', class_name='', origin='',
                             level=1, hp=12, max_hp=12, ac=14,
                             abilities=dict(strength=14, dexterity=14, constitution=12,
                                            intelligence=10, wisdom=12, charisma=10)))


def scripts_of(html):
    """Names of the scripts a browser would load — commented-out tags excluded."""
    live = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    return [src.split('?')[0].rsplit('/', 1)[-1]
            for src in re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', live)]


@pytest.fixture
def app(tmp_path, monkeypatch):
    connect = socket.socket.connect

    def local_only(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External AI/network is forbidden')

    monkeypatch.setattr(socket.socket, 'connect', local_only)
    import solo.nat20_api
    monkeypatch.setattr(solo.nat20_api.secrets, 'randbits', lambda bits: 5)
    model = CommandMockLLM({'Смотрю вокруг': dict(status='command', action=dict(kind='check', check_id='perception'), message='')})
    model.plan = Plan(summary='Осмотр', steps=[Step(kind='observe', text='Осмотр')])
    return create_app(tmp_path / 'chat-only.sqlite3', model)


def test_chat_shell_drops_every_panel_and_keeps_the_composer(app):
    with TestClient(app) as client:
        page = client.get('/?ui=chat')
        assert page.status_code == 200
        html = page.text
    loaded = scripts_of(html)
    assert set(loaded) == set(CHAT_ONLY_SCRIPTS), loaded  # nothing beyond the declared minimum
    for name in PANEL_SCRIPTS:
        assert name not in loaded, name
    assert 'data-view=' not in html            # no navigation buttons at all
    assert 'class="sidebar"' not in html       # the sidebar is gone, not merely hidden
    assert 'style="display:none' not in html   # and nothing was hidden instead of removed
    assert html.count('class="view') == 1      # exactly one screen: #play
    for marker in ('id="chat"', 'id="action-form"', 'id="player-input"', 'id="send"', 'id="story-status"'):
        assert marker in html, marker


def test_chat_shell_has_no_buttons_beyond_the_composer(app):
    with TestClient(app) as client:
        html = client.get('/?ui=chat').text
    # The create dialog is the only way to start an adventure on this screen; it
    # is a dialog, not a panel, so its own two buttons are counted separately.
    outside = re.sub(r'<dialog id="create-dialog".*?</dialog>', '', html, flags=re.S)
    labels = [re.sub(r'<[^>]+>', '', button).strip()
              for button in re.findall(r'<button[^>]*>(.*?)</button>', outside, re.S)]
    # r220: кнопка подписи — из утверждённого макета «Стол и лист» (03-play):
    # «Сказать» вместо «Отправить →». Набор кнопок тот же: начало, стоп, отправка.
    assert labels == ['Начать приключение', 'Остановить ответ', 'Сказать'], labels
    assert 'id="create-dialog"' in html and 'id="create-form"' in html
    assert 'id="campaign-list"' not in html


def test_full_page_loses_nothing(app):
    with TestClient(app) as client:
        full = client.get('/').text
        chat = client.get('/?ui=chat').text
        # Only an explicit ?ui=chat switches the shell; every other query stays full.
        assert scripts_of(client.get('/?ui=play').text) == scripts_of(full)
        assert client.get('/?ui=chat').status_code == 200
    full_scripts, chat_scripts = scripts_of(full), scripts_of(chat)
    assert set(chat_scripts) == set(CHAT_ONLY_SCRIPTS)
    assert set(chat_scripts) < set(full_scripts)          # strictly fewer
    assert set(full_scripts) - set(chat_scripts)         # something really was cut
    for name in PANEL_SCRIPTS:
        assert name in full_scripts, name
    for name in REMOVED_FROM_FULL_PAGE:
        assert name not in full_scripts, name
    assert not re.search(r'<html[^>]*data-ui', full)     # the full page keeps its own shell
    # The full page still has the navigation and every section it had before.
    # r221 adds the colophon: one nav button and one section, no second shell.
    assert full.count('data-view=') == 9
    assert full.count('class="view') == 9
    assert 'data-view="about"' in full and 'id="about"' in full
    # The colophon owns no <footer>: eight modules insert their panels before
    # main's footer and would land inside #about.
    assert 'about__foot' in full and '<footer class="about__foot">' not in full


def test_chat_shell_starts_from_one_source_and_reaches_the_hero_editor(app):
    with TestClient(app) as client:
        html = client.get('/?ui=chat').text
        full = client.get('/').text
    # The step is static markup, not something a script invents later: a screen
    # reader and this test both see the same element.
    assert 'id="source-file"' in html and 'type="file"' in html
    assert 'accept=".pdf,.txt,.md"' in html
    assert 'разберёт места, NPC и проверки' in html
    assert 'id="source-summary"' in html and 'id="source-status"' in html
    assert 'id="hero-editor"' in html and 'id="hero-back"' in html
    # Nothing was re-introduced: one screen, no navigation.
    assert html.count('class="view') == 1 and 'data-view=' not in html
    # The editor itself is the Nat20 wizard; the chat shell has to load it.
    assert 'wizard.js' in CHAT_ONLY_SCRIPTS and 'wizard.js' in scripts_of(html)
    # r210: ручного листа нет ни на одном экране — полная страница открывает
    # тот же диалог источника и героя, что и чат.
    assert 'id="begin-steps"' in full and 'id="source-file"' in full
    for gone in ('id="manual-sheet"', 'id="new-name"', 'id="new-hero"', 'id="create-abilities"'):
        assert gone not in full, gone


def test_chat_client_uploads_to_the_preparation_source_route():
    play = (ROOT / 'web' / 'play.js').read_text(encoding='utf-8')
    assert "api('/preparation/drafts'" in play            # the draft is created once
    assert "'/api/preparation/drafts/'+current.id+'/sources'" in play
    assert "dataset.ui==='chat'" not in play               # one creation entry on every screen
    assert 'window.soloWizard.open' in play               # the editor is reused, not re-implemented


def test_source_upload_reports_pages_structure_and_a_readable_refusal(app):
    raw = (Path(__file__).with_name('fixtures') / 'obsidian-scenario.md').read_bytes()
    with TestClient(app) as client:
        draft = client.post('/api/preparation/drafts').json()
        uploaded = client.post('/api/preparation/drafts/' + draft['id'] + '/sources',
                               files={'file': ('obsidian-scenario.md', raw, 'text/markdown')})
        assert uploaded.status_code == 200, uploaded.text
        quality = uploaded.json()['sources'][-1]['quality']
        # A refused file is a typed answer the dialog can print, not a crash.
        refused = client.post('/api/preparation/drafts/' + draft['id'] + '/sources',
                              files={'file': ('scan.docx', b'PK\x03\x04 not a scenario',
                                              'application/octet-stream')})
    assert quality['status'] == 'usable' and quality['pages'] >= 1 and quality['characters'] > 0
    assert quality['structure'] == 'structured'
    assert quality['locations'] >= 1 and quality['npc'] >= 1 and quality['events'] >= 1
    assert quality['sections'] >= 1
    assert refused.status_code == 409 and refused.json()['detail'], refused.text


def test_source_without_text_is_stored_with_a_failed_quality(app):
    # The extractor stores the upload and says why it is unusable: HTTP 200 with
    # status 'failed', not an error. That is exactly the answer the dialog must
    # not print as «принят».
    with TestClient(app) as client:
        draft = client.post('/api/preparation/drafts').json()
        stored = client.post('/api/preparation/drafts/' + draft['id'] + '/sources',
                             files={'file': ('scan.txt', b'12 34 56 !!! ###', 'text/plain')})
    assert stored.status_code == 200, stored.text
    quality = stored.json()['sources'][-1]['quality']
    assert quality['status'] == 'failed' and quality['warnings']
    play = (ROOT / 'web' / 'play.js').read_text(encoding='utf-8')
    assert "const usable=quality.status==='usable'" in play   # a stored file is not automatically a success
    assert 'next.hidden=!usable' in play                      # and it cannot unlock the next step
    assert 'if(!file||uploading)return' in play               # one upload at a time, one quality
