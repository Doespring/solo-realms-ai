"""r179: разговор и влияние — отношение НИПа состояние, а проверка по правилу.

Проверяется исполнением, а не наличием строк:

* отношение НИПа живёт в состоянии кампании (`social.attitude_of`) и двигается
  исходом проверки Влияния: успех — на ступень к дружелюбию, провал — от него
  (шаг и направление провала — правило хоста; книга печатает только, что
  Влияние «меняет отношение существа», стр. 15);
* дружелюбие даёт преимущество на проверку Влияния — это напечатано книгой
  (стр. 18) и доезжает до броска движка через `nat20_api._social`;
* нужна ли кость — решает хост: подтверждённая реакция НИПа (согласие или
  отказ) проверку отменяет, Сл и его источник называет журнал, а не модель;
* кого именно убеждают — факт сцены: один НИП — он и есть цель, несколько —
  нужен `npc_id`, иначе отказ с именами вместо догадки;
* «Действие вне каталога» не отклоняется молча: интерпретатор получает правило
  спросить, что именно делает герой, а ответ хоста на отказ добавляет ту же
  просьбу (книга, стр. 15);
* статусы тринадцати записей главы совпадают с таблицей отчёта r179.
"""
import asyncio
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from solo import social
from solo.app import create_app
from solo.game_commands import GameCommand
from solo.llm import CommandMockLLM
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import SCENE_ACTIONS
from solo.nat20_worker import execute as worker_execute
from solo.storage import Store
from test_nat20_command_cycle import model, offline  # noqa: F401 — фикстуры
from test_nat20_social_actions import campaign, prepare, run


def state_npc(store, cid, index=0):
    return store.campaign(cid)['state']['locations'][0]['npc'][index]


def run_action(game, store, cid, request_id, action_id, skill, npc_id=None):
    """Одна сценная команда через движок; npc_id — цель Влияния."""
    current = store.campaign(cid)
    action = dict(kind='scene_action', action_id=action_id, skill=skill)
    if npc_id:
        action['npc_id'] = npc_id
    out = game.execute(cid, GameCommand(
        request_id=request_id, expected_version=current['version'], action=action))
    event = out['event'] if 'event' in out else out
    payload = event.model_dump()['payload'] if hasattr(event, 'model_dump') else event['payload']
    roll = next((effect['payload'] for effect in payload['effects'] if effect['type'] == 'roll'), {})
    return dict(command=payload['command'], outcome=payload['outcome'], roll=roll)


def test_transition_moves_one_step_both_ways():
    """Ступень за исход проверки: успех к дружелюбию, провал от него."""
    npc = dict(attitude='indifferent')
    up = social.attitude_change(npc, True)
    assert (up['before'], up['after'], up['changed']) == ('indifferent', 'friendly', True)
    assert npc['attitude'] == 'friendly', 'переход записан в состояние НИПа'
    down = social.attitude_change(npc, False)
    assert (down['before'], down['after']) == ('friendly', 'indifferent')
    assert social.attitude_change(npc, None)['changed'] is False, 'исход без Сл не двигает'
    # Края диапазона: книга печатает три отношения, четвёртого не выдумываем.
    assert social.shifted('friendly', 1) == 'friendly'
    assert social.shifted('hostile', -1) == 'hostile'


def test_attitude_is_state_and_moves_with_the_check(tmp_path):
    """Успех/провал Влияния видны в состоянии, а не только в прозе."""
    store, game, cid = campaign(tmp_path, name='r179-attitude')
    prepare(store, cid, npc=[dict(id='npc-1', name='Гоблин-стражник', attitude='indifferent')])
    assert social.attitude_of(state_npc(store, cid)) == 'indifferent'

    observed = []
    for attempt in range(1, 7):
        before = social.attitude_of(state_npc(store, cid))
        result = run(game, store, cid, 'attitude-%d' % attempt, 'influence', 'persuasion')
        success = result['roll']['success']
        after = social.attitude_of(state_npc(store, cid))
        expected = social.shifted(before, 1 if success else -1)
        assert after == expected, (observed, success, before, after)
        assert social.LABELS[after] in result['outcome'], result['outcome']
        assert 'Отношение НИПа «Гоблин-стражник»' in result['outcome']
        observed.append(dict(success=success, before=before, after=after,
                             dc=result['roll']['dc'], check=result['roll']['check_id']))
    assert observed, 'ни одного броска Влияния не сделано'
    assert {row['check'] for row in observed} == {'influence-persuasion'}
    assert {row['dc'] for row in observed} == {15}, 'Сл называет хост, а не модель'


def test_friendly_npc_rolls_with_advantage_inside_the_engine(tmp_path, monkeypatch):
    """Дружелюбие даёт преимущество (книга, стр. 18) — доезжает до движка."""
    import solo.nat20_checks as checks
    seen = []
    original = checks.resolve_state_check

    def spy(state, **kwargs):
        seen.append(kwargs['advantage'])
        return original(state, **kwargs)

    monkeypatch.setattr(checks, 'resolve_state_check', spy)

    store, game, cid = campaign(tmp_path, name='r179-advantage')
    prepare(store, cid, npc=[dict(id='npc-1', name='Исмарк', attitude='friendly')])
    current = store.campaign(cid)
    command = dict(request_id='advantage-1', expected_version=current['version'],
                   action=dict(kind='scene_action', action_id='influence', skill='persuasion'))

    # Хост собирает подсказку из состояния сцены, а не из слов модели.
    carried = Nat20GameEngine._social(current, GameCommand.model_validate(command))
    assert carried == dict(npc='npc-1', attitude='friendly',
                           check_id='influence-persuasion', advantage=True)

    base = dict(mode='execute', state=current['state']['nat20'], session_id=current['id'],
                scene_checks={})
    friendly = asyncio.run(worker_execute(dict(base, command=command, social=carried)))
    indiff = asyncio.run(worker_execute(dict(base, command=command, social={})))
    assert seen == [True, False], 'преимущество передано только за дружелюбие'
    assert friendly['state'] == indiff['state'], 'бросок не меняет состояние движка'
    assert friendly['result']['check_id'] == 'influence-persuasion'
    # Другой НИП — другой ответ хоста: преимущества нет.
    cool = dict(store.campaign(cid))
    cool['state']['locations'][0]['npc'][0]['attitude'] = 'hostile'
    assert Nat20GameEngine._social(cool, GameCommand.model_validate(command))['advantage'] is False


def test_social_check_is_skipped_when_the_reaction_is_already_known(tmp_path):
    """Кость бросается только при неопределённой реакции (книга, стр. 16)."""
    store, game, cid = campaign(tmp_path, name='r179-known')
    prepare(store, cid, npc=[dict(id='npc-1', name='Стражник',
                                  attitude='friendly', reaction='refuses')])
    before = store.campaign(cid)
    history_before = len(store.history(cid))
    with pytest.raises(ValueError, match='Проверка Влияния не нужна'):
        run(game, store, cid, 'known-1', 'influence', 'persuasion')
    assert store.campaign(cid) == before, 'отказ до броска не двигает состояние'
    assert len(store.history(cid)) == history_before, 'отказ до броска не пишет событие'

    store2, game2, cid2 = campaign(tmp_path, name='r179-undefined')
    prepare(store2, cid2, npc=[dict(id='npc-1', name='Стражник',
                                    attitude='indifferent')])
    rolled = run(game2, store2, cid2, 'undefined-1', 'influence', 'persuasion')
    assert rolled['roll']['dc'] == 15 and rolled['roll']['check_id'] == 'influence-persuasion'
    assert 'Сл — значение хоста' in rolled['outcome'], rolled['outcome']


def test_same_named_npcs_cannot_be_confused_by_name():
    """Два НИПа с одним именем — отказ, а не «повлиял на первого»."""
    scene = {"location": "двор",
             "locations": [{"name": "двор", "npc": [dict(name="Страж"), dict(name="Страж")]}]}
    with pytest.raises(ValueError, match='нельзя различить'):
        social.select(scene, 'Страж')
    with pytest.raises(ValueError, match='несколько НИПов'):
        social.select(scene)


def test_scenario_npc_carries_attitude_and_reaction():
    """У отношения и реакции есть путь ввода из сценария, а не только из теста."""
    from solo.world import WorldNPC
    npc = WorldNPC(name='Исмарк', attitude='friendly', reaction='agrees')
    assert (social.attitude_of(npc.model_dump()), npc.reaction) == ('friendly', 'agrees')
    assert WorldNPC(name='Страж').attitude is None, 'сцена может не задавать отношение'
    with pytest.raises(ValueError):
        WorldNPC(name='Страж', attitude='wary')  # пять ступеней не вводим


def test_influence_target_is_a_scene_fact_not_a_guess(tmp_path):
    """Один НИП — цель; несколько — имя; ни одного — просьба объявить."""
    store, game, cid = campaign(tmp_path, name='r179-targets')
    with pytest.raises(ValueError, match='нет НИПа'):
        run_action(game, store, cid, 'empty-1', 'influence', 'persuasion')

    prepare(store, cid, npc=[dict(id='npc-1', name='Мирабелла', attitude='indifferent'),
                             dict(id='npc-2', name='Гарет', attitude='indifferent')])
    with pytest.raises(ValueError, match='несколько НИПов'):
        run_action(game, store, cid, 'crowd-1', 'influence', 'persuasion')

    chosen = run_action(game, store, cid, 'chosen-1', 'influence', 'intimidation', npc_id='npc-2')
    assert 'Гарет' in chosen['outcome'], chosen['outcome']
    assert social.attitude_of(state_npc(store, cid, 0)) == 'indifferent', 'чужой НИП не тронут'
    assert social.attitude_of(state_npc(store, cid, 1)) != 'indifferent', 'цель названа, она и двигается'


class Recording(CommandMockLLM):
    """Запоминает системную инструкцию: что именно увидела модель."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.systems = []

    async def stream(self, system, user, **kwargs):
        self.systems.append((kwargs.get('mode'), system))
        async for chunk in super().stream(system, user, **kwargs):
            yield chunk


def test_unregistered_action_asks_to_declare_instead_of_refusing(tmp_path):
    """Действие вне каталога: просьба объявить, а не молчаливый отказ."""
    unknowable = 'Пробую уговорить луну упасть в колодец.'
    llm = Recording({unknowable: dict(status='unsupported', action=None,
                                      message='Такого действия нет в правилах.')})
    store = Store(tmp_path / 'r179-improvise.sqlite3')
    with TestClient(create_app(store.path, llm)) as client:
        cid = Nat20GameEngine(store).create_campaign('r179 improvise', seed=5)['id']
        version = store.campaign(cid)['version']
        answer = client.post(f'/api/campaigns/{cid}/command-turn', json=dict(
            request_id='improvise-1', text=unknowable, expected_version=version))
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body['status'] == 'unsupported'
        assert 'что именно делает герой' in body['narrative'], body['narrative']
        assert store.history(cid) == [], 'просьба объявить не пишет событие'
    instructions = [system for mode, system in llm.systems if mode == 'interpreter']
    assert instructions and any('Действие вне каталога' in text for text in instructions), \
        'правило спрашивать о нестандартном действии не доехало до модели'


#: Таблица отчёта r179: правило / что считается / статус после. Одна строка —
#: одна запись главы `phb-01/02-actions-social`, чтобы отчёт нельзя было
#: разойтись с корпусом.
TABLE = [
    ('player_and_gm', 'adjudication_required', None),
    ('play_cycle', 'adjudication_required', None),
    ('campaign_structure', 'adjudication_required', None),
    ('character_portrayal', 'adjudication_required', None),
    ('improvised_action', 'adjudication_required', 'social.declaration_prompt'),
    ('npc_attitude', 'engine_subset_verified', 'social.attitude_change'),
    ('social_roleplay', 'adjudication_required', None),
    ('social_check', 'engine_subset_verified', 'social.check_needed'),
    ('roleplay_styles', 'adjudication_required', None),
    ('social_example', 'blocked', None),
    ('action_hide', 'blocked', None),
    ('action_utilize', 'blocked', None),
    ('action_magic', 'blocked', None),
]


@pytest.mark.parametrize('key,status,handler', TABLE, ids=[row[0] for row in TABLE])
def test_chapter_statuses_match_the_table(key, status, handler):
    import json
    chapter = json.loads((ROOT / 'rules_data/v2/chapters/phb-01'
                          / '02-actions-social.rules.json').read_text(encoding='utf-8'))
    rule = next(row for row in chapter['rules'] if row['id'] == 'phb2024.phb01.' + key)
    assert (rule['readiness']['status'], rule['readiness']['handler']) == (status, handler)
    if status == 'blocked':
        assert rule['readiness']['blockers'], 'blocked без спецификации — не отказ, а отписка'
