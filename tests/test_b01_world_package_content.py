"""B01 / F06 + F05: пакет мира — по содержимому, сравнение правил — чтением.

Аудит 08.11 (снимки S35, S37, S28):

* F06 — файл ``test-tiamat-08-11.soloworld (1).json`` (45 675 байт, побайтно
  тот же, что принятый ``audit-tiamat.soloworld.json``) отвергался с «Это не
  пакет мира»: решение принималось по имени файла, а браузер дописывает к
  скачанному файлу «(1)», «(2)» и пробелы.
* F05 — «Сравнить с правилами кампании» отвечало «Прежний обработчик
  отключён» вместо различий: сравнение шло POST-предпросмотром прежнего
  цикла, а в кампании Nat20 он закрыт гейтом одного бэкенда.

Здесь проверяется серверная половина: вердикт по содержимому с различимыми
причинами отказа и READ-ONLY сравнение правил. Клиентскую половину (имя файла
как подсказка, показ имени кампании до импорта) держит браузерный тест
``tests/test_b01_worlds_ui.py``.
"""
import json
import os
from copy import deepcopy

os.environ.setdefault('SOLO_TEST', '1')

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.llm import CommandMockLLM, MockLLM
from solo.nat20_api import Nat20GameEngine
from solo.rulesets import BUILTINS, RULE_PARAMETERS, RulesetRepository, preset_changes
from solo.storage import Store
from solo.world_service import export_package, import_world_package, inspect_package
from test_r236_worlds import world_campaign


def package_of(tmp_path, name='Письмо у фонаря'):
    store = Store(tmp_path / 'b01.sqlite3')
    campaign = world_campaign(store, name)
    return store, campaign, json.loads(json.dumps(export_package(store, campaign)))


def test_package_verdict_comes_from_content_reason_is_specific(tmp_path):
    store, _, package = package_of(tmp_path)
    look = inspect_package(package)
    assert look['ok'] is True and look['compatible'] is True
    assert look['name'] == package['name'] and look['system'] == 'D&D 2024'
    assert look['format'] == 'soloworld' and look['format_version'] == 1
    assert look['counts']['locations'] == 2 and 'совместим' in look['compatibility']

    # Имя файла в решение не входит вовсе: вердикт одинаков для того же
    # содержимого, как бы файл ни назывался («(1)», пробелы, регистр).
    for name in ('test-tiamat-08-11.soloworld (1).json',
                 'audit-tiamat.soloworld.json',
                 ' test (2).JSON', 'без-суффикса.json'):
        assert inspect_package(json.loads(json.dumps(package)))['ok'] is True, name
    assert import_world_package(store, package)['backend'] == 'nat20'


def test_foreign_content_is_still_refused_with_its_own_reason(tmp_path):
    _, _, package = package_of(tmp_path)
    foreign = inspect_package({**package, 'format': 'foundry-world'})
    assert foreign['ok'] is False and foreign['compatible'] is False
    assert 'soloworld' in foreign['reason']
    newer = inspect_package({**package, 'format_version': 99})
    assert newer['ok'] is False and 'более новой версией' in newer['reason']
    # Причина у чужого файла — своя, а не общая «это не пакет мира».
    assert foreign['reason'] != newer['reason']
    assert inspect_package([1, 2, 3])['ok'] is False
    assert inspect_package('не json')['ok'] is False


def test_inspect_and_import_over_http_with_a_browser_suffix(tmp_path):
    store, _, package = package_of(tmp_path)
    text = json.dumps(package, ensure_ascii=False)
    with TestClient(create_app(tmp_path / 'b01.sqlite3', MockLLM())) as client:
        before = len(client.get('/api/campaigns').json())
        looked = client.post('/api/worlds/inspect', content=text.encode('utf-8'))
        assert looked.status_code == 200, looked.text
        info = looked.json()
        assert info['ok'] is True and info['name'] == package['name']
        assert 'не пакет мира' not in looked.text.lower().replace('это не пакет мира solo realms', '')
        assert len(client.get('/api/campaigns').json()) == before  # осмотр ничего не создаёт
        created = client.post('/api/worlds/import', content=text.encode('utf-8'))
        assert created.status_code == 200, created.text
        assert len(client.get('/api/campaigns').json()) == before + 1
        foreign = client.post('/api/worlds/inspect', json={**package, 'format': 'чужой'})
        assert foreign.status_code == 200 and foreign.json()['ok'] is False
        assert 'soloworld' in foreign.json()['reason']


def test_rules_comparison_is_read_only_and_lists_differences(tmp_path):
    app = create_app(tmp_path / 'b01.sqlite3', CommandMockLLM({}), offline_mock=True)
    store = app.state.store
    campaign = Nat20GameEngine(store).create_campaign('Свои правила', seed=5)['id']
    before = deepcopy(store.campaign(campaign))
    with TestClient(app) as client:
        response = client.get('/api/campaigns/' + campaign + '/ruleset',
                              params={'ruleset': 'dnd2014'})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['read_only'] is True and data['can_apply'] is False
        assert data['source']['id'] == 'dnd2024' and data['source']['version'] == 1
        assert data['target']['id'] == 'dnd2014' and data['target']['version'] == 1
        assert data['source']['name'] != data['target']['name']  # две версии рядом
        fields = {row['field'] for row in data['changes']}
        assert {'exhaustion', 'surprise', 'long_rest_hit_dice', 'potion_action'} <= fields
        for row in data['changes']:
            assert row['label'] and row['before'] != row['after']
        # экран не получает текст отключённого обработчика
        assert 'Прежний обработчик отключён' not in response.text
        # чтение: ни состояния, ни версии, ни строки подтверждения
        assert store.campaign(campaign) == before
        with store.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM ruleset_conversions').fetchone()[0] == 0
        # сравнение набора с самим собой — «различий нет», а не пустой экран
        same = client.get('/api/campaigns/' + campaign + '/ruleset',
                          params={'ruleset': 'dnd2024'}).json()
        assert same['changes'] == [] and same['same'] is True
        # прежний POST-предпросмотр по-прежнему закрыт гейтом одного бэкенда:
        # сравнение его не воскрешает, оно идёт другим, читающим путём
        gated = client.post('/api/campaigns/' + campaign + '/ruleset/preview',
                            json={'ruleset': 'dnd2014'})
        assert gated.status_code == 409
        assert gated.json()['detail']['code'] == 'command_backend_required'


def test_rule_parameters_are_compared_one_list(tmp_path):
    store = Store(tmp_path / 'b01.sqlite3')
    campaign = Nat20GameEngine(store).create_campaign('Список', seed=2)['id']
    library = RulesetRepository(store)
    same = library.compare(campaign, 'dnd2024')
    assert same['changes'] == [] and same['same'] is True
    other = library.compare(campaign, 'dnd2014')
    changed = {row['field']: row for row in other['changes']}
    assert changed['surprise']['before'] == 'initiative_disadvantage'
    assert changed['surprise']['after'] == 'skip_first_turn'
    # тот же список полей держит правильную подпись для экрана
    assert set(changed) == {field for field, _ in RULE_PARAMETERS
                            if getattr(BUILTINS['dnd2024'], field) != getattr(BUILTINS['dnd2014'], field)}
    assert preset_changes(BUILTINS['dnd2024'], BUILTINS['dnd2024']) == []
