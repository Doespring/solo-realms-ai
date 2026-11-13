"""r130: Влияние, Изучение и Поиск вне боя — одна проверка движка, факт в журнал.

Проверяется исполнением, а не наличием строк:

* у движка нет интентов `influence`/`study`/`search` (`PlayerIntent.intent_type`
  — закрытый литерал), поэтому действие живёт в хосте как `scene_action` и
  катит ОДНУ проверку навыка через
  `nat20_worker -> nat20_checks.resolve_state_check`;
* в журнал попадает факт: действие, навык, Сл и итог броска (`dc`, `check_id`);
* Влияние двигает отношение НИПа (r179: состояние кампании, `solo/social.py`);
  старый контракт r130 «успех не двигает НИПа» отменён карточкой r179, а не
  ослаблен здесь. Согласие, помощь и мотив НИПа по-прежнему не назначаются
  проверкой (`solo-realms-contracts`: предложенное != совершённое);
* Изучение отдаёт только уже существующий факт сцены и записывает его как
  показанный; тот же факт второй раз не выдаётся; пустая сцена отказывает ДО
  броска, а не выдумывает улику;
* Поиск катит Восприятие/Расследование, и Сл сцены (`definition.checks`) имеет
  приоритет над реестром хоста — проверено на настоящей модели
  `CampaignDefinition`, а не на выдуманном словаре.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from dnd5e_engine import PlayerIntent

from solo.app import create_app
from solo.game_commands import GameCommand, SceneAction, tool_schemas
from solo.nat20_api import Nat20GameEngine
from solo.nat20_campaign import CampaignDefinition, load_definition
from solo.nat20_content import SCENE_ACTIONS, SCENE_CHECKS, scene_check
from solo.storage import Store
from solo import social
from test_nat20_command_cycle import model, offline  # noqa: F401 — фикстуры


def campaign(tmp_path, name='r130', seed=5):
    store = Store(tmp_path / (name + '.sqlite3'))
    game = Nat20GameEngine(store)
    cid = game.create_campaign(name, seed=seed)['id']
    return store, game, cid


def prepare(store, cid, *, npc=(), facts=()):
    """Сцена уже что-то знает: НИП с отношением и/или готовый факт места."""
    def transition(state):
        state['locations'][0]['npc'] = [dict(entry) for entry in npc]
        state['locations'][0]['facts'] = list(facts)
        return [dict(type='scripted', text='Сцена подготовлена')]
    store.record_scenario(cid, 'prepare-scene', 'Подготовка сцены', transition)


def run(game, store, cid, request_id, action_id, skill):
    """Одна сценная команда через движок; возвращает факт журнала и бросок."""
    current = store.campaign(cid)
    out = game.execute(cid, GameCommand(
        request_id=request_id, expected_version=current['version'],
        action=dict(kind='scene_action', action_id=action_id, skill=skill)))
    event = out['event'] if 'event' in out else out
    payload = event.model_dump()['payload'] if hasattr(event, 'model_dump') else event['payload']
    roll = next((effect['payload'] for effect in payload['effects'] if effect['type'] == 'roll'), {})
    return dict(command=payload['command'], outcome=payload['outcome'], roll=roll)


def get_screen(client, cid):
    response = client.get(f'/api/campaigns/{cid}/screen')
    assert response.status_code == 200, response.text
    return response.json()


def candidates(packet, **criteria):
    return [c for c in packet['commands']
            if all(c['parameters'].get(key) == value for key, value in criteria.items())]


def test_screen_offers_influence_and_search_out_of_combat_only(tmp_path):
    """Вне боя — кнопки сценных действий по факту сцены; в бою их нет вовсе."""
    store, game, cid = campaign(tmp_path)
    with TestClient(create_app(store.path, model())) as client:
        packet = get_screen(client, cid)
        # r179: Влияние адресуется существу — без НИПа в сцене кнопки нет,
        # Изучение без факта сцены тоже; Поиск остаётся.
        assert {c['parameters']['action_id'] for c in candidates(packet, kind='scene_action')} \
            == {'search'}, 'ни НИПа, ни факта — только Поиск'

    prepare(store, cid, npc=[dict(id='npc-1', name='Гоблин-стражник', attitude='indifferent')])
    with TestClient(create_app(store.path, model())) as client:
        packet = get_screen(client, cid)
        assert {c['parameters']['action_id'] for c in candidates(packet, kind='scene_action')} \
            == {'influence', 'search'}, 'появился НИП — появилось Влияние'
        assert {c['parameters']['skill'] for c in candidates(packet, action_id='influence')} \
            == set(SCENE_ACTIONS['influence']['skills']), 'пять навыков книги, стр. 15'
        assert {c['parameters']['skill'] for c in candidates(packet, action_id='search')} \
            == set(SCENE_ACTIONS['search']['skills'])
        # Сценная проверка не дублируется общей кнопкой «Проверить: …».
        plain = {c['parameters']['check_id'] for c in candidates(packet, kind='check')}
        assert not plain & set(SCENE_CHECKS)
        button = candidates(packet, action_id='influence')[0]
        clicked = client.post(f'/api/campaigns/{cid}/commands', json=dict(
            request_id='click-influence', expected_version=button['expected_version'],
            action=button['parameters']))
        assert clicked.status_code == 200, clicked.text
        assert store.campaign(cid)['version'] == button['expected_version'] + 1
    # Бой: сценных кнопок нет — это действия сцены, а не боя.
    with TestClient(create_app(store.path, model())) as client:
        packet = get_screen(client, cid)
        start = candidates(packet, kind='start_combat')[0]
        assert client.post(f'/api/campaigns/{cid}/commands', json=dict(
            request_id='start', expected_version=start['expected_version'],
            action=start['parameters'])).status_code == 200
        battle = get_screen(client, cid)
        assert battle['snapshot']['combat']['active'] is True
        assert not candidates(battle, kind='scene_action')


def test_influence_rolls_the_registry_check_and_moves_the_npc(tmp_path):
    """Влияние — проверка навыка; r179 двигает отношение НИПа по исходу."""
    store, game, cid = campaign(tmp_path)
    npc = [dict(id='npc-1', name='Гоблин-стражник', attitude='hostile')]
    prepare(store, cid, npc=npc)
    before = deepcopy(store.campaign(cid)['state']['locations'][0]['npc'])

    succeeded = None
    for attempt in range(1, 11):
        result = run(game, store, cid, 'influence-%d' % attempt, 'influence', 'persuasion')
        roll = result['roll']
        assert roll['check_id'] == 'influence-persuasion' and roll['dc'] == 15
        assert result['outcome'].startswith('Влияние (Убеждение) против Сл 15: бросок')
        assert 'Отношение НИПа «Гоблин-стражник»' in result['outcome']
        attached = store.campaign(cid)['state']['locations'][0]['npc'][0]
        assert attached['attitude'] == social.shifted(
            before[0]['attitude'], 1 if roll['success'] else -1), 'переход по исходу проверки'
        before[0]['attitude'] = attached['attitude']
        if roll['success']:
            succeeded = result
            break
    assert succeeded, 'десять провалов подряд: успех Влияния не проверен'
    assert succeeded['roll']['total'] >= succeeded['roll']['dc']


def test_study_shows_only_a_fact_the_scene_already_holds(tmp_path):
    """Изучение: пустая сцена отказывает до броска, факт выдаётся один раз."""
    store, game, cid = campaign(tmp_path)
    with TestClient(create_app(store.path, model())) as client:
        packet = get_screen(client, cid)
        assert not candidates(packet, action_id='study')
        before = store.campaign(cid)
        refused = client.post(f'/api/campaigns/{cid}/commands', json=dict(
            request_id='study-empty', expected_version=before['version'],
            action=dict(kind='scene_action', action_id='study', skill='insight')))
        assert refused.status_code == 409, refused.text
        assert 'нечего изучать' in refused.text
        assert store.campaign(cid)['version'] == before['version']
        assert store.history(cid) == [], 'отказ до броска не пишет событие'

    fact = 'На поясе гоблина — кожаный свиток с печатью.'
    prepare(store, cid, facts=[fact])
    with TestClient(create_app(store.path, model())) as client:
        packet = get_screen(client, cid)
        study = candidates(packet, action_id='study')
        assert len(study) == len(SCENE_ACTIONS['study']['skills'])
        assert {c['parameters']['skill'] for c in study} == set(SCENE_ACTIONS['study']['skills'])

    succeeded = None
    for attempt in range(1, 16):
        result = run(game, store, cid, 'study-%d' % attempt, 'study', 'insight')
        revealed = (store.campaign(cid)['state'].get('scene_actions') or {}).get('revealed', [])
        if result['roll']['success']:
            succeeded = result
            assert revealed == [fact], 'успех отдаёт ровно один уже существующий факт'
            assert fact in result['outcome']
            break
        assert revealed == [], 'провал не открывает факт сцены'
    assert succeeded, 'пятнадцать провалов подряд: Изучение не дошло до успеха'
    with pytest.raises(ValueError, match='нечего изучать'):
        run(game, store, cid, 'study-again', 'study', 'insight')


def test_search_rolls_perception_or_investigation_at_the_scene_dc(tmp_path):
    """Поиск катит Восприятие/Расследование; Сл сцены сильнее реестра хоста."""
    store, game, cid = campaign(tmp_path)
    looked = run(game, store, cid, 'search-p', 'search', 'perception')
    assert (looked['roll']['check_id'], looked['roll']['dc']) == ('search-perception', 12)
    assert 'Поиск (Восприятие) против Сл 12' in looked['outcome']
    traced = run(game, store, cid, 'search-i', 'search', 'investigation')
    assert (traced['roll']['check_id'], traced['roll']['dc']) == ('search-investigation', 15)

    # Зарегистрированная сцена сценных ключей не несёт — Сл берётся из реестра.
    pinned = load_definition('fighter-road-2024-v1')
    assert not set(SCENE_CHECKS) & set(pinned.checks)
    fallback = scene_check('search', 'perception', pinned)
    assert (fallback['dc'], fallback['source']) == (12, 'registry')

    # Сцена со своей записью Сл 18: проверка уважает её, а не реестр.
    raw = pinned.model_dump(mode='json')
    raw['checks']['search-perception'] = dict(
        kind='skill', skill='perception', ability=None, dc=18,
        source=deepcopy(raw['checks']['perception']['source']))
    scene = CampaignDefinition.model_validate(raw)
    chosen = scene_check('search', 'perception', scene)
    assert (chosen['dc'], chosen['source'], chosen['check_id']) == (18, 'scene', 'search-perception')

    # Запись не того вида не подменяет проверку навыка.
    raw['checks']['study-insight'] = dict(
        kind='ability', skill=None, ability='int', dc=20,
        source=deepcopy(raw['checks']['perception']['source']))
    ability_entry = scene_check('study', 'insight', CampaignDefinition.model_validate(raw))
    assert (ability_entry['dc'], ability_entry['source']) == (15, 'registry')


def test_no_scene_action_becomes_a_player_intent(tmp_path):
    """Ни одно из трёх действий не конструирует PlayerIntent, которого нет."""
    for name in ('influence', 'study', 'search'):
        with pytest.raises(ValidationError):
            PlayerIntent(intent_type=name)
    assert 'intent_type' not in SceneAction.model_fields
    with pytest.raises(ValidationError):
        SceneAction(kind='scene_action', action_id='search', skill='persuasion')
    for module in (ROOT / 'solo' / 'scene_actions.py', ROOT / 'solo' / 'nat20_content.py'):
        assert 'PlayerIntent' not in module.read_text(encoding='utf-8')

    store, game, cid = campaign(tmp_path)
    prepare(store, cid, facts=['Следы сапог уходят в кустарник.'],
            npc=[dict(id='npc-1', name='Гоблин-стражник', attitude='indifferent')])
    facts = [run(game, store, cid, 'i', 'influence', 'intimidation'),
             run(game, store, cid, 's', 'search', 'investigation'),
             run(game, store, cid, 'st', 'study', 'nature')]
    assert [fact['command'] for fact in facts] == ['scene_action'] * 3
    assert [fact['roll']['check_id'] for fact in facts] == [
        'influence-intimidation', 'search-investigation', 'study-nature']


def test_contract_snapshot_carries_scene_action():
    """Снимок контракта несёт ровно дельту r130 — сама правда, а не «стало зелёным».

    Сторож на текущую механику: исчезнет `scene_action` из модели, из снимка,
    из `oneOf` или из набора инструментов модели — этот тест упадёт.
    """
    schema = json.loads((ROOT / 'integration/nat20/schemas/game-command-v1.schema.json')
                        .read_text(encoding='utf-8'))
    scene = schema['$defs']['SceneAction']
    assert scene['additionalProperties'] is False
    assert scene['properties']['kind'] == {'const': 'scene_action', 'title': 'Kind', 'type': 'string'}
    assert scene['properties']['action_id']['enum'] == ['influence', 'study', 'search']
    assert scene['required'] == ['kind', 'action_id', 'skill']
    known_skills = {skill for spec in SCENE_ACTIONS.values() for skill in spec['skills']}
    assert set(scene['properties']['skill']['enum']) == known_skills
    assert schema['properties']['action']['discriminator']['mapping']['scene_action'] == '#/$defs/SceneAction'
    assert {'$ref': '#/$defs/SceneAction'} in schema['properties']['action']['oneOf']
    # Снимок — это контракт `solo.nat20_contracts.GameCommand` (а не ToolInput хоста).
    from solo.nat20_contracts import GameCommand as NativeCommand
    assert schema == NativeCommand.model_json_schema()
    assert 'scene_action' in {t['function']['name'] for t in tool_schemas()}
    action = SceneAction(kind='scene_action', action_id='search', skill='perception')
    command = GameCommand(request_id='scene-action-contract', expected_version=0, action=action)
    assert command.action.kind == 'scene_action' and command.action.action_id == 'search'
