"""r254: роль мастера просит ровно то, что принимает валидатор.

r250 научил осмотр просить пересказ подтверждённой сцены, но для разговора и
хода с исходом роль по-прежнему просила «художественное описание»
(`solo/prompts.py:3`), а `narrative_grounding.verify` принимает только дословное
совпадение предложения с подтверждённым источником. Модель писала своё, текст
выбрасывался, игрок видел шаблон, платный вызов сгорал.

Сторож на каждый статус (осмотр, разговор, ход с исходом): дословный
подтверждённый ход проходит `engine_verified`, выдуманный факт — `CanonViolation`
и в ответ игроку не попадает. Сам `verify` не ослаблен: в каждом статусе тот же
пакет отбрасывает новое утверждение.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts.command_smoke import FixedDice, setup
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import CommandMockLLM, MockLLM
from solo.narrative_grounding import CanonViolation, verify
import solo.prompts as prompts

# Роль, на которой стоит проверка, и роль непроверяемого прежнего пути. Второй
# может не быть вовсе — тогда сторож падает на своём утверждении, а не на импорте.
NARRATOR_ROLE = prompts.NARRATOR_ROLE
ARTISTIC_NARRATOR_ROLE = getattr(prompts, 'ARTISTIC_NARRATOR_ROLE', '')
from solo.prototype_gm import present_intent

# Сцена из штатного START: NPC там зарегистрирован в памяти кампании, поэтому
# подтверждённая реплика проходит через настоящий store, а не через заглушку.
SCENE = 'Огонь в очаге, запах хвои. Ирина ждёт у стойки.'
FACT = 'Ирина просит найти её брата. На письме брата изображены три сосны.'
KNOWN = 'Брат ушёл к старой башне вчера и не вернулся.'
INVENTED = 'Дис поднимается из мглы, как оттиск чужой воли на самой плоти мира.'
OUTCOME = 'Выживание: 13 против Сл 12 — успех'


class _Scope:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _scene(npc=()):
    return dict(name='Таверна «Последний огонь»', description=SCENE, facts=[FACT], objects=[], npc=list(npc))


def _presenter(tmp_path, answer, dialogue=()):
    """`present_intent` с синтетическим рассказчиком: ни одного платного вызова."""
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Сцена', Hero(name='Мирон', hp=12, max_hp=12))['id']
    seen = {}
    llm = MockLLM()

    async def narrate(system, packet, **kwargs):
        seen.update(system=system, packet=json.loads(packet), grounded=kwargs.get('grounded'))
        llm.last_narrative = dict(description=answer, dialogue=list(dialogue), proposals=[])
        llm.last_narrative_policy = 'model_artistic'
        if False:
            yield ''

    llm.narrate_scene = narrate
    game = SimpleNamespace(llm=llm, model_scope=lambda *a, **k: _Scope(), store=store)
    return game, store, cid, seen


def _present(game, store, cid, *, status, target='', scene=None):
    context = dict(scene=scene or _scene(), confirmed=[FACT], memory_context=dict(memories=[]),
                   game_snapshot=dict(actors=[]))
    intent = SimpleNamespace(target=target, status=status)
    return asyncio.run(present_intent(game, store.campaign(cid), 'r254-' + status, 'Осматриваюсь',
                                      intent, context))


def _command(tmp_path, narrative):
    """Ход с исходом через настоящий `play_command`; системный текст перехвачен."""
    model = CommandMockLLM({'Исследую': dict(status='command', action=dict(kind='check', check_id='следы'),
                                             message='')}, narrative=narrative)
    systems, contracts = [], []
    original = model.stream

    async def stream(system, user, **kwargs):
        if kwargs.get('mode', 'narrator') != 'interpreter':
            systems.append(system)
            contracts.append(kwargs.get('grounded_contract'))
        async for chunk in original(system, user, **kwargs):
            yield chunk

    model.stream = stream
    store, cid, game = setup(tmp_path, llm=model)
    return model, store, cid, game, systems, contracts


def test_the_role_asks_for_the_restatement_the_validator_accepts():
    """Роль и проверка говорят об одном: дословный пересказ подтверждённого."""
    assert 'дословн' in NARRATOR_ROLE.casefold()
    assert 'художественное описание' not in NARRATOR_ROLE
    # Художественная форма осталась только там, где текст никто не сверяет.
    assert 'художественное описание' in ARTISTIC_NARRATOR_ROLE


def test_observation_restating_the_scene_is_verified(tmp_path):
    game, store, cid, seen = _presenter(tmp_path, SCENE)
    result = _present(game, store, cid, status='observe')
    assert seen['grounded'] is True, 'рассказчик обязан попросить сверяемый контракт'
    assert seen['system'].startswith(NARRATOR_ROLE), seen['system']
    assert 'художественное описание' not in seen['system']
    assert 'осматривается' in seen['system']
    assert result['narrative_policy'] == 'engine_verified', result
    assert SCENE in result['narrative'], result['narrative']
    assert 'Нового ничего не заметно' not in result['narrative']
    # Тот же пакет по-прежнему отбрасывает новое утверждение.
    with pytest.raises(CanonViolation):
        verify(dict(description=INVENTED, dialogue=[], proposals=[]), seen['packet'])


def test_observation_invented_scene_is_rejected(tmp_path):
    game, store, cid, seen = _presenter(tmp_path, INVENTED)
    result = _present(game, store, cid, status='observe')
    assert result['narrative_policy'] == 'engine_rejected', result
    assert result['narrative_reason'] == 'rejected_claim'
    assert 'оттиск' not in result['narrative'], result['narrative']


def test_conversation_restating_the_npc_line_is_verified(tmp_path):
    npc = dict(name='Ирина', known=KNOWN)
    game, store, cid, seen = _presenter(tmp_path, SCENE,
                                        dialogue=[dict(speaker='Ирина', text=KNOWN)])
    result = _present(game, store, cid, status='conversation', target='Ирина', scene=_scene([npc]))
    assert seen['system'].startswith(NARRATOR_ROLE), seen['system']
    assert 'художественное описание' not in seen['system']
    assert seen['grounded'] is True, 'рассказчик обязан попросить сверяемый контракт'
    assert 'собеседник — Ирина' in seen['system'], seen['system']
    assert result['narrative_policy'] == 'engine_verified', result
    assert KNOWN in result['narrative'], result['narrative']
    with pytest.raises(CanonViolation):
        verify(dict(description=SCENE, dialogue=[dict(speaker='Ирина', text='Следы ведут к реке.')],
                    proposals=[]), seen['packet'])


def test_conversation_invented_reply_is_rejected(tmp_path):
    npc = dict(name='Ирина', known=KNOWN)
    game, store, cid, seen = _presenter(tmp_path, SCENE,
                                        dialogue=[dict(speaker='Ирина', text='Следы ведут к реке, там прячется разбойник.')])
    result = _present(game, store, cid, status='conversation', target='Ирина', scene=_scene([npc]))
    assert result['narrative_policy'] == 'engine_rejected', result
    assert 'реке' not in result['narrative'], result['narrative']


def test_resolved_turn_restating_the_engine_line_is_verified(tmp_path):
    model, store, cid, game, systems, contracts = _command(tmp_path, dict(description=OUTCOME, dialogue=[], proposals=[]))
    result = asyncio.run(game.play_command(cid, 'one', 'Исследую', 0, rng=FixedDice((13,))))
    assert result['status'] == 'resolved', result
    assert result['narrative_policy'] == 'engine_verified', result
    assert result['narrative'] == OUTCOME, result['narrative']
    assert systems and systems[-1].startswith(NARRATOR_ROLE), systems
    assert 'художественное описание' not in systems[-1]
    assert 'outcomes' in systems[-1]
    assert contracts[-1] is True, 'ход с исходом идёт через сверяемый контракт'
    packet = model.packets[-1]['packet']
    with pytest.raises(CanonViolation):
        verify(dict(description='Герой нашёл 100 золотых.', dialogue=[], proposals=[]), packet)


def test_resolved_turn_inventing_a_fact_is_refused(tmp_path):
    """Выдуманный результат не доходит до игрока и не выдаётся за сбой провайдера."""
    model, store, cid, game, systems, contracts = _command(
        tmp_path, dict(description='Герой нашёл 100 золотых.', dialogue=[], proposals=[]))
    result = asyncio.run(game.play_command(cid, 'one', 'Исследую', 0, rng=FixedDice((13,))))
    assert result['status'] == 'resolved', result
    assert result['narrative_policy'] == 'engine_rejected', result
    assert '100 золотых' not in result['narrative'], result['narrative']
    assert result['narrative'] == OUTCOME, result['narrative']


def test_cloud_adapter_sends_the_grounded_contract_not_the_artistic_one(tmp_path):
    """Настоящее сообщение облака: художественный префикс не должен стоять
    перед сверяемым рассказчиком.

    До r254 system собирался как gm_narrative_contract_v1 + deepseek_narrator_v1:
    «Пиши живую художественную прозу… свободно перефразируй, используй образы» —
    прямо против того, что принимает verify. Проверяется собранное сообщение,
    а не строка, собранная call-site.
    """
    from scripts.playable_ai_readiness import mock_cloud
    from test_cloud import sse

    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        value = dict(description=SCENE, dialogue=[], proposals=[])
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(value, ensure_ascii=False)))]),
                    dict(choices=[], usage=dict(prompt_tokens=500, completion_tokens=50))])
    llm, _ = mock_cloud(tmp_path, respond)
    packet = json.dumps(dict(packet_version=2, place='Таверна', atmosphere=SCENE, confirmed=[FACT],
                             npc=[], memory=[], observation=True), ensure_ascii=False)
    async def run():
        try:
            async for _ in llm.narrate_scene('Сейчас герой осматривается.', packet,
                                             narrative_format='structured', grounded=True):
                pass
            return llm.last_narrative, llm.last_narrative_policy
        finally:
            await llm.close()
    structure, policy = asyncio.run(run())
    assert structure['description'] == SCENE and policy == 'model_artistic', (structure, policy)
    system = requests[0]['messages'][0]['content']
    assert 'дословн' in system.casefold(), system[:400]
    # «образы» встречается и в самом запрете, поэтому ищем формулировки
    # художественного контракта, а не слова.
    assert 'свободно перефразируй' not in system.casefold(), system[:400]
    assert 'чувственные детали' not in system.casefold(), system[:400]
    assert 'живую художественную прозу' not in system.casefold(), system[:400]
    assert llm.metrics[-1]['prompt_version'] == 'gm_grounded_contract_v1', llm.metrics[-1]


def test_every_narrator_call_states_its_contract():
    """Структурный сторож: у каждого вызова narrate_scene контракт назван явно.

    Флаг `grounded` по умолчанию выключен, поэтому забытый `grounded=True`
    молча вернёт художественный облачный контракт («свободно перефразируй…») —
    ровно ту болезнь, которую закрывает карточка. Художественный контракт
    разрешён РОВНО одному месту: непроверяемому `Game.narrate()`.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'solo'
    calls = []
    for path in sorted(root.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or getattr(node.func, 'attr', '') != 'narrate_scene':
                continue
            keywords = {word.arg: word.value for word in node.keywords}
            owner, cursor = '<module>', node
            while cursor in parents:
                cursor = parents[cursor]
                if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = cursor.name
                    break
            value = keywords.get('grounded')
            calls.append((path.name, owner, None if value is None else getattr(value, 'value', None)))
    assert calls, 'вызовы narrate_scene не найдены — сторож ослеп'
    silent = [(name, owner) for name, owner, value in calls if value is None]
    assert not silent, 'вызов без grounded= молча получит художественный контракт: ' + repr(silent)
    artistic = [(name, owner) for name, owner, value in calls if value is False]
    assert artistic == [('game.py', 'narrate')], artistic
    verified = {(name, owner) for name, owner, value in calls if value is True}
    assert {('game.py', 'play_command'), ('prototype_gm.py', 'present_intent')} <= verified, verified
