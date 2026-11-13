"""r183: a failed interpretation leaves a trace in the log, not only a polite phrase.

The player's refusal must stay readable; the reason must land in the journal with
the exception class, the request id, the scene/step and the head of the model's
own answer. `CloudUnavailable` stays a separate, honest refusal.
"""
import asyncio
import logging

import pytest

import json

from solo.app import PlayRequest
from solo.cloud import CloudUnavailable
from solo.cloud_cost import BudgetExceeded
from solo.contracts import Hero
from solo.game import Game
from solo.llm import CommandMockLLM
from solo.model_protocol import ModelBehaviour
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store

REQUEST = 'Разбираю странный знак на стене'
# A truncated JSON object is what a model actually returns when it breaks its
# contract; the validator must reject it and the log must keep this head.
BROKEN = '{"status": "command", "action": '


class BrokenInterpreter:
    """Offline interpreter that answers like a model which broke its contract."""

    metrics = []

    def __init__(self, exc):
        self.exc = exc

    async def interpret(self, text, scene, *, seed=None):
        self.exc.model_reply = getattr(self.exc, 'model_reply', None) or BROKEN
        raise self.exc


@pytest.fixture
def legacy(tmp_path):
    store = Store(tmp_path / 'r183.sqlite3')
    campaign = store.create('Журнал разбора', Hero(name='Мирон'))['id']
    return store, campaign


def play(store, campaign, model, request_id='r183-one'):
    return asyncio.run(Game(store, model).play(campaign, PlayRequest(request_id=request_id, text=REQUEST)))


def journal(caplog, level=None):
    return [r for r in caplog.records if r.name == 'solo.game' and (level is None or r.levelno == level)]


def test_broken_model_reply_is_logged_with_class_and_request_id(legacy, caplog):
    store, campaign = legacy
    with caplog.at_level(logging.WARNING, logger='solo.game'):
        result = play(store, campaign, BrokenInterpreter(ValueError('Модель вернула ответ не по схеме')))
    assert 'Не понял намерение' in result['reply']
    record = next(r for r in journal(caplog) if 'не разобрался' in r.getMessage())
    message = record.getMessage()
    assert 'exc=ValueError' in message
    assert 'request_id=r183-one' in message
    assert f'reply_len={len(BROKEN)}' in message
    assert BROKEN[:20] in message


def test_code_defect_is_logged_as_defect_with_traceback(legacy, caplog):
    store, campaign = legacy
    with caplog.at_level(logging.ERROR, logger='solo.game'):
        result = play(store, campaign, BrokenInterpreter(TypeError("'NoneType' is not subscriptable")), 'r183-defect')
    # Игрок получает тот же вежливый отказ, но в журнале это дефект, а не плохая реплика.
    assert 'Не понял намерение' in result['reply']
    record = journal(caplog, logging.ERROR)[-1]
    assert 'дефект кода' in record.getMessage()
    assert 'exc=TypeError' in record.getMessage()
    assert record.exc_info and record.exc_info[0] is TypeError


def test_last_resort_failure_is_logged_not_swallowed(legacy, caplog):
    store, campaign = legacy
    with caplog.at_level(logging.ERROR, logger='solo.game'):
        result = play(store, campaign, BrokenInterpreter(RuntimeError('провайдер вернул пустой поток')), 'r183-last')
    assert 'Мастер пока не смог разобрать' in result['reply']
    record = journal(caplog, logging.ERROR)[-1]
    assert 'последнем рубеже' in record.getMessage()
    assert 'request_id=r183-last' in record.getMessage()
    assert record.exc_info and record.exc_info[0] is RuntimeError


def test_unavailable_model_keeps_its_own_refusal_and_no_defect_log(legacy, caplog):
    store, campaign = legacy
    with caplog.at_level(logging.WARNING, logger='solo.game'):
        result = play(store, campaign, BrokenInterpreter(CloudUnavailable('Мастер недоступен: соединение потеряно')))
    assert 'Мастер недоступен' in result['reply']
    assert not journal(caplog), 'недоступность модели не дефект разбора'


def test_interpreter_attaches_the_raw_answer_to_the_error():
    raw = '{"summary": "план", '  # оборванный JSON

    class Streamed(ModelBehaviour):
        metrics = []

        async def stream(self, system, user, **kwargs):
            yield raw

    with pytest.raises(ValueError) as caught:
        asyncio.run(Streamed().interpret(REQUEST, {}))
    assert getattr(caught.value, 'model_reply', None) == raw


def test_command_turn_parse_failure_is_logged(tmp_path, caplog):
    store = Store(tmp_path / 'r183-command.sqlite3')
    campaign = Nat20GameEngine(store).create_campaign('Журнал команды', seed=5)['id']
    model = CommandMockLLM({REQUEST: dict(status='непонятно', action=None, message='')})
    with caplog.at_level(logging.WARNING, logger='solo.game'):
        result = asyncio.run(Game(store, model).play_command(campaign, 'r183-two', REQUEST, 0))
    assert result['status'] == 'clarification'
    assert 'Не удалось однозначно разобрать намерение' in json.dumps(result, ensure_ascii=False)
    record = next(r for r in journal(caplog) if 'stage=command' in r.getMessage())
    assert 'request_id=r183-two' in record.getMessage()
    assert 'exc=ValidationError' in record.getMessage()
    # Оборванный ответ модели тоже оставляет след: длина и начало.
    assert 'reply_len=unknown' not in record.getMessage()
    # Сцена боевого пакета лежит в ключе 'scene'; её имя тоже должно попасть в след.
    assert 'scene=' in record.getMessage()


def test_spent_budget_is_a_refusal_not_a_defect(tmp_path, caplog):
    store = Store(tmp_path / 'r183-budget.sqlite3')
    campaign = Nat20GameEngine(store).create_campaign('Журнал бюджета', seed=5)['id']

    class Spent(ModelBehaviour):
        metrics = []

        async def stream(self, system, user, **kwargs):
            raise BudgetExceeded(0, 0, 'RUB', ['Месячный лимит 50 ₽ исчерпан.'])
            yield ''

    with caplog.at_level(logging.WARNING, logger='solo.game'):
        result = asyncio.run(Game(store, Spent()).play_command(campaign, 'r183-budget', REQUEST, 0))
    assert result['status'] == 'ai_error'
    assert 'лимит расходов исчерпан' in journal(caplog, logging.WARNING)[-1].getMessage()
    assert not journal(caplog, logging.ERROR), 'исчерпанный бюджет — не дефект разбора'
