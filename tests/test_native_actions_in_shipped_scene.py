"""r194: посчитанные движком действия доходят до игрока в ШТАТНОЙ кампании.

До правки гейт хоста отбивал ЛЮБУЮ нативную операцию в авторской сцене
(`ValueError: Generic native actions require explicit experimental campaign`):
замер на `fighter-standard-array-2024-v1` дал `experimental=false`,
`native_operations=[]`, а экран в бою предлагал только «Атаковать» и
«Второе дыхание». Правила при этом уже считались движком (r187/r129).

Проверяется исполнением пути хоста (`Nat20GameEngine.execute`), а не
наличием строк:

* `grapple` в штатной сцене записывает цели `grappled` и называет бросок —
  подпись берётся из `solo/event_display.py`, а не «Действие героя
  подтверждено»;
* `shove` даёт `prone`, `shove_push` отбрасывает цель;
* `dodge`/`disengage`/`help` принимаются и тратят Действие (механику помехи
  от `dodge` и отсутствие провокации после `disengage` измеряет
  `tests/test_nat20_dodge_full.py` и r187-прогон движка);
* `stand_up` и `escape_grapple` доходят до движка и получают ЕГО правило
  («не сбит с ног», «не схвачен») — то есть хостовый гейт их больше не
  отбивает;
* операции, которых пин не считает (`pass`, `reaction`, нативная
  `use_feature`), остаются отказом с причиной;
* измеренные дефекты пина (`hide` не тратит Действие, `ready` снимает запись
  без разрешения) остаются отказом — кнопки игроку не обещают.
"""
import socket

import pytest

from solo.game_commands import GameCommand
from solo.nat20_api import Nat20GameEngine
from solo.nat20_prototype import SHIPPED_NATIVE_OPERATIONS
from solo.nat20_screen import Nat20Screen
from solo.storage import Store

SCENE = 'fighter-standard-array-2024-v1'
FOE = 'road-raider'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External calls are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local)


def campaign(tmp_path, name='r194', seed=1):
    store = Store(tmp_path / (name + '.sqlite3'))
    engine = Nat20GameEngine(store)
    cid = engine.create_campaign(name, definition_id=SCENE, seed=seed)['id']
    return store, engine, cid


def view(store, cid):
    return ((store.campaign(cid)['state'].get('nat20') or {}).get('combat') or {}).get('view') or {}


def send(engine, store, cid, request_id, action):
    return engine.execute(cid, GameCommand(request_id=request_id,
                                           expected_version=store.campaign(cid)['version'], action=action))


def refusal(call):
    try:
        call()
    except ValueError as error:
        return str(error)
    raise AssertionError('ожидался отказ, команда прошла')


def hero_turn(engine, store, cid, prefix='turn'):
    """Ход героя: инициатива в штатной сцене не всегда за ним.

    request_id уникален: повтор одного id с другой командой хост отвергает.
    """
    for attempt in range(4):
        initiative = view(store, cid).get('initiative') or []
        if not initiative:
            return
        current = initiative[view(store, cid).get('current_turn_index') or 0]['entity_id']
        if current == 'hero':
            return
        send(engine, store, cid, '%s-%d' % (prefix, attempt), dict(kind='end_turn'))
    raise AssertionError('ход героя не наступил')


def adjacent_fight(tmp_path, name='r194'):
    store, engine, cid = campaign(tmp_path, name)
    send(engine, store, cid, 'start', dict(kind='start_combat', encounter_id='basic'))
    hero_turn(engine, store, cid)
    send(engine, store, cid, 'move', dict(kind='move', position=dict(x=2, y=1, z=0)))
    return store, engine, cid


def hero_row(store, cid):
    return next(a for a in view(store, cid).get('initiative') or [] if a['entity_id'] == 'hero')


def conditions(store, cid, entity):
    row = next((a for a in view(store, cid).get('initiative') or [] if a['entity_id'] == entity), {})
    return [item['condition'] for item in row.get('conditions') or []]


def test_capabilities_name_the_shipped_actions(tmp_path):
    store, engine, cid = campaign(tmp_path)
    capabilities = engine.capabilities(store.campaign(cid))
    assert capabilities['experimental'] is False, 'сцена остаётся штатной, в экспериментальную не переведена'
    assert 'native_action' in capabilities['actions']
    assert set(capabilities['native_operations']) == set(SHIPPED_NATIVE_OPERATIONS)
    assert set(SHIPPED_NATIVE_OPERATIONS) == {'dodge', 'disengage', 'drop_prone',
                                              'escape_grapple', 'grapple', 'help',
                                              'shove', 'stand_up'}


def test_grapple_reaches_the_player_with_its_own_numbers(tmp_path):
    store, engine, cid = adjacent_fight(tmp_path, 'r194-grapple')
    answer = send(engine, store, cid, 'grapple',
                  dict(kind='native_action', operation='grapple', target_id=FOE))
    assert 'grappled' in conditions(store, cid, FOE)
    events = [event['type'] for event in answer['native_events']]
    assert 'condition_applied' in events and 'save_rolled' in events
    sentence = answer['event'].payload['outcome']
    assert 'Действие героя подтверждено' not in sentence, sentence
    assert 'Захват: цель схвачена' in sentence and 'спасбросок' in sentence, sentence


def test_shove_knocks_the_target_prone(tmp_path):
    store, engine, cid = adjacent_fight(tmp_path, 'r194-shove')
    answer = send(engine, store, cid, 'shove',
                  dict(kind='native_action', operation='shove', target_id=FOE, shove_push=False))
    assert 'prone' in conditions(store, cid, FOE)
    assert 'Толчок: цель сбита с ног' in answer['event'].payload['outcome']


def test_shove_push_reports_exactly_what_the_engine_did(tmp_path):
    """Отброшена — только если движок правда сдвинул цель.

    Толчок направлен от героя: у края сетки сдвига нет, и `combatant_moved`
    не приходит. Замер на этом дереве: сцена 7x5, цель на `2,0` — сдвига нет,
    а прежняя строка всё равно говорила «отброшена на 5 футов».
    """
    store, engine, cid = adjacent_fight(tmp_path, 'r194-push')
    zone = lambda: (view(store, cid).get('actor_zone') or {}).get(FOE)
    before = zone()
    answer = send(engine, store, cid, 'shove-push',
                  dict(kind='native_action', operation='shove', target_id=FOE, shove_push=True))
    events = [event['type'] for event in answer['native_events']]
    sentence = answer['event'].payload['outcome']
    assert 'Толчок' in sentence and 'спасбросок' in sentence, sentence
    if 'combatant_moved' in events:
        assert zone() != before, (before, zone())
        assert 'отброшена' in sentence, sentence
    else:
        assert zone() == before, (before, zone())
        assert 'отброшена' not in sentence and 'некуда' in sentence, sentence


def test_the_sentence_never_claims_a_push_the_engine_did_not_make():
    from solo.event_display import native_sentence
    save = dict(type='save_rolled', roll_total=13, dc=12, succeeded=False)
    pushed = native_sentence(dict(operation='shove', shove_push=True),
                             [save, dict(type='combatant_moved')])
    blocked = native_sentence(dict(operation='shove', shove_push=True), [save])
    assert 'отброшена' in pushed, pushed
    assert 'отброшена' not in blocked and 'некуда' in blocked, blocked


def test_dodge_and_disengage_are_accepted_and_spend_the_action(tmp_path):
    store, engine, cid = campaign(tmp_path, 'r194-dodge')
    send(engine, store, cid, 'start', dict(kind='start_combat', encounter_id='basic'))
    hero_turn(engine, store, cid)
    dodged = send(engine, store, cid, 'dodge', dict(kind='native_action', operation='dodge'))
    assert 'Уклонение' in dodged['event'].payload['outcome']
    # Действие потрачено самим Уклонением, а не следующим ходом.
    assert hero_row(store, cid)['action_available'] is False
    hero_turn(engine, store, cid, 'back')
    assert hero_row(store, cid)['action_available'] is True, 'новый ход возвращает Действие'
    left = send(engine, store, cid, 'disengage', dict(kind='native_action', operation='disengage'))
    assert 'Отход' in left['event'].payload['outcome']
    assert hero_row(store, cid)['action_available'] is False


def test_help_is_accepted_by_the_host_even_without_an_ally(tmp_path):
    """Гейт хоста пропускает Помощь; что грант истекает без союзника — правило
    движка (r129), поэтому экран такую кнопку и не предлагает."""
    store, engine, cid = adjacent_fight(tmp_path, 'r194-help')
    answer = send(engine, store, cid, 'help',
                  dict(kind='native_action', operation='help', target_id=FOE))
    assert 'Помощь' in answer['event'].payload['outcome'], answer['event'].payload['outcome']


def test_stand_up_and_escape_reach_their_own_engine_rule(tmp_path):
    """Гейт хоста снят: отказ приходит от правила пина, а не «нужна
    экспериментальная кампания»."""
    store, engine, cid = adjacent_fight(tmp_path, 'r194-stand')
    standing = refusal(lambda: send(engine, store, cid, 'stand_up',
                                    dict(kind='native_action', operation='stand_up')))
    assert 'not Prone' in standing and 'experimental' not in standing, standing
    escape = refusal(lambda: send(engine, store, cid, 'escape',
                                  dict(kind='native_action', operation='escape_grapple', target_id=FOE)))
    assert 'not Grappled' in escape and 'experimental' not in escape, escape


@pytest.mark.parametrize('operation, extra, expected', [
    # Движок эти операции считает — просто своей командой. Отказ обязан
    # назвать её, а не говорить «движок не умеет».
    ('pass', {}, "use kind='end_turn'"),
    ('use_feature', dict(feature_id='second-wind', target_id='hero'), "use kind='use_feature'"),
    # Реакция в пине есть, но хост отдельной команды для неё не отдаёт.
    ('reaction', {}, 'is not exposed by the host as a native action'),
])
def test_other_operations_are_refused_with_the_real_reason(tmp_path, operation, extra, expected):
    store, engine, cid = adjacent_fight(tmp_path, 'r194-' + operation)
    message = refusal(lambda: send(engine, store, cid, 'op',
                                   dict(kind='native_action', operation=operation, **extra)))
    assert expected in message, message
    assert 'not computed by the pinned engine' not in message, message


def test_attack_and_move_name_their_own_command(tmp_path):
    """Отказ для атаки не говорит «движок не умеет»: у неё своя команда."""
    store, engine, cid = adjacent_fight(tmp_path, 'r194-attack')
    message = refusal(lambda: send(engine, store, cid, 'atk',
                                    dict(kind='native_action', operation='attack',
                                         weapon_id='longsword', target_id=FOE)))
    assert "use kind='attack'" in message, message
    assert 'not computed by the pinned engine' not in message, message


def test_measured_pin_defects_stay_refused(tmp_path):
    store, engine, cid = adjacent_fight(tmp_path, 'r194-hide')
    version = store.campaign(cid)['version']
    hidden = refusal(lambda: send(engine, store, cid, 'hide', dict(kind='native_action', operation='hide')))
    ready = refusal(lambda: send(engine, store, cid, 'ready',
                                 dict(kind='native_action', operation='ready', reaction_trigger='hit_by_attack')))
    assert 'pinned Hide does not spend' in hidden, hidden
    assert 'pinned Ready resolves only a pre-armed SPELL' in ready, ready
    assert store.campaign(cid)['version'] == version, 'отказ не должен тратить ни ход, ни Действие'


def test_screen_offers_the_shipped_actions_and_nothing_the_host_refuses(tmp_path):
    store, engine, cid = adjacent_fight(tmp_path, 'r194-screen')
    packet = Nat20Screen().project(store.campaign(cid))
    offered = [(command['label'], command['parameters']) for command in packet['commands']]
    labels = [label for label, _ in offered]
    assert 'Уклонение' in labels and 'Отход' in labels, labels
    assert any(parameters.get('operation') == 'grapple' for _, parameters in offered), labels
    assert any(parameters.get('operation') == 'shove' and parameters.get('shove_push') is False
               for _, parameters in offered), labels
    assert any(parameters.get('operation') == 'shove' and parameters.get('shove_push') is True
               for _, parameters in offered), labels
    assert not [label for label in labels if label in ('Засада', 'Подготовка')], labels
    native = [parameters['operation'] for _, parameters in offered
              if parameters.get('kind') == 'native_action']
    assert native and set(native) <= set(SHIPPED_NATIVE_OPERATIONS), native
    # Кнопка «Помощь» без союзника не обещает грант, который истечёт сам.
    assert not [label for label in labels if label.startswith('Помощь')], labels
