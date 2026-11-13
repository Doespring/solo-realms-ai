"""r185: хрупкий перехват намерений закрыт таблицей, а не переписан.

Быстрый путь ``natural.quick`` перехватывает только ясные низкорисковые
конструкции, остальное обязан разбирать Interpreter. Эта таблица живых реплик
фиксирует границу: что движок исполняет сам, что он отказывается исполнять, и
что уходит модели. Правило сломается не «обвалом», а лишним вопросом игроку
(фраза ушла модели) или потерянным ходом (склейка двух действий в одно) —
поэтому каждая такая фраза здесь посчитана.

Три проверки держат именно риск этой карточки:

* ``SECOND_ACTION``: «иду к реке и атакую» больше не превращается в одно
  движение с молча потерянным ударом;
* ``MOVE``-синонимы: «шагаю», «идём», «продолжаю путь» перехватываются, а не
  уходят модели впустую;
* ``DELIBERATION``: «но» в легальном действии — не рассуждение модели.
"""
import asyncio

import pytest

from solo import natural
from solo.app import PlayRequest
from solo.contracts import Hero, Plan, Step
from solo.game import Game, UNCLEAR, explanation_text
from solo.llm import MockLLM
from solo.storage import Store

RIVER = 'Берег реки'
TOWER = 'Старая башня'
TAVERN = 'Таверна «Последний огонь»'


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'intent.sqlite3')


@pytest.fixture
def cid(store):
    return store.create('Новая история', Hero(name='Мирон'))['id']


def play(store, cid, text, key='one', plan=None):
    return asyncio.run(Game(store, MockLLM(plan)).play(cid, PlayRequest(request_id=key, text=text)))


# Одно движение, которое движок исполняет сам, без модели и без вопроса.
MOVES = [
    ('Иду к реке', RIVER),
    ('Двигаюсь к реке', RIVER),
    ('Идём к реке', RIVER),
    ('Шагаю к реке', RIVER),
    ('Топаю к реке', RIVER),
    ('Продолжаю путь к реке', RIVER),
    ('Отправляюсь к старой башне', TOWER),
    ('Направляюсь в сторону реки', RIVER),
    ('Отступаю к реке', RIVER),
    ('Покидаю таверну и иду к башне', TOWER),
    ('Выхожу из таверны и направляюсь к реке', RIVER),
    # «чтобы …» — цель пути, а не второе действие в той же реплике.
    ('Иду к реке, чтобы найти следы', RIVER),
    ('Иду к реке, чтобы атаковать', RIVER),
]

# Отрицание движения: отказ, а не путешествие и не вызов модели.
REFUSALS = [
    ('Не иду к реке', RIVER),
    ('Не пойду к реке', RIVER),
    ('Я не собираюсь идти к реке', RIVER),
    ('Я не собираюсь идти к реке, остаюсь тут', RIVER),
]

# Сюда быстрый путь не лезет: план, условие, два действия, перенос речи.
# None — это не «не понял», а «разбирает Interpreter».
DELEGATED = [
    ('Сразу после боя пойду в трактир', 'план на будущее'),
    ('Планирую завтра идти к реке', 'план на будущее'),
    ('Вечером пойду в таверну', 'план на будущее'),
    ('Собираюсь к реке', 'план на будущее'),
    ('Если он побежит, иду к реке', 'условие'),
    ('Если он побежит — стреляю', 'условие'),
    ('Когда стемнеет, пойду к реке', 'условие'),
    ('Но это означает, что мне надо идти к реке', 'рассуждение'),
    ('Пойду к башне, потом к реке', 'два движения'),
    ('Сначала ищу следы, потом иду к реке', 'два действия'),
    ('Иду к реке и атакую', 'два действия'),
    ('Иду к реке и беру факел', 'два действия'),
    ('Не пойду к реке, но осматриваю берег', 'отказ и действие'),
    ('Говорю Ирине, что иду к реке', 'перенос речи'),
    # Известная хрупкость: «мимо» отрезает цель, голое «к реке» без глагола
    # направления — оба случая уходят модели, и это безопасное поведение.
    ('Иду мимо реки к башне', 'направление с «мимо»'),
    ('К реке', 'голое направление'),
]


@pytest.mark.parametrize('text,target', MOVES)
def test_move_phrase_is_executed_by_the_fast_path(store, cid, text, target):
    plan = natural.quick(text, store.campaign(cid)['state'])
    assert plan is not None, f'фраза ушла модели: {text}'
    assert [s.kind for s in plan.steps] == ['move']
    assert plan.steps[0].target == target and plan.steps[0].disposition == 'now'
    checked, question = natural.guard(plan, text, store.campaign(cid)['state'])
    assert checked is not None and not question


@pytest.mark.parametrize('text,target', REFUSALS)
def test_negated_move_is_a_refusal(store, cid, text, target):
    plan = natural.quick(text, store.campaign(cid)['state'])
    assert plan is not None, f'отказ ушёл модели: {text}'
    assert plan.steps[0].kind == 'move' and plan.steps[0].disposition == 'negated'
    assert plan.steps[0].target == target
    checked, question = natural.guard(plan, text, store.campaign(cid)['state'])
    assert checked is not None and not question


@pytest.mark.parametrize('text,why', DELEGATED)
def test_phrase_is_left_to_the_interpreter(store, cid, text, why):
    assert natural.quick(text, store.campaign(cid)['state']) is None, f'{why}: {text}'


def test_move_without_a_second_action_still_works_after_the_compound_rule(store, cid):
    """Правило второго действия не съело обычный путь и путь с целью «чтобы»."""
    state = store.campaign(cid)['state']
    for text in ('Иду к реке', 'Иду к реке, чтобы атаковать'):
        assert natural.quick(text, state) is not None, text


def test_compound_move_and_attack_keeps_both_actions(store, cid):
    """«Иду к реке и атакую»: оба действия доходят до движка, ни одно не пропало.

    Быстрый путь раньше исполнял только движение и молча терял удар. Теперь
    реплику разбирает модель, а план из двух шагов исполняется целиком: пути
    нет — ход остановлен, но действие зарегистрировано, а не выброшено.
    """
    plan = Plan(summary='Иду и атакую', steps=[
        Step(kind='move', text='Иду к реке', target=RIVER),
        Step(kind='attack', text='Атакую стража', target='Страж')])
    llm = MockLLM(plan)
    r = asyncio.run(Game(store, llm).play(cid, PlayRequest(request_id='both', text='Иду к реке и атакую')))
    assert llm.metrics, 'реплику должен разбирать Interpreter'
    assert r['kind'] == 'event'
    kinds = [x['type'] for x in r['event']['results']]
    assert 'unsupported' in kinds, kinds


def test_move_phrase_with_new_synonym_reaches_the_engine(store, cid):
    r = play(store, cid, 'Шагаю к реке')
    assert r['kind'] == 'event' and r['event']['results'][0]['type'] == 'move'
    assert store.campaign(cid)['state']['location'] == RIVER


def test_negated_move_does_not_move_the_hero(store, cid):
    before = store.campaign(cid)['state']['location']
    r = play(store, cid, 'Я не собираюсь идти к реке')
    assert r['kind'] == 'reply' and 'не совершаете' in r['reply']
    assert store.campaign(cid)['state']['location'] == before


def test_legal_action_with_but_is_an_action_not_deliberation(store, cid):
    """«Но я всё-таки …» — легальное действие: движок его исполняет.

    Рассуждением модели считается «но <маркер>» в её собственной прозе, а не
    союз «но» в реплике игрока. Тот же текст, отданный модели, остаётся
    ответом и не подменяется на «не понял действие».
    """
    plan = Plan(summary='Иду к реке', steps=[Step(kind='move', text='Иду к реке', target=RIVER)])
    r = play(store, cid, 'Но я всё-таки иду к реке', plan=plan)
    assert r['kind'] == 'event' and store.campaign(cid)['state']['location'] == RIVER
    assert explanation_text('Но я всё-таки атакую.') == 'Но я всё-таки атакую.'


@pytest.mark.parametrize('text', [
    'Но я всё-таки атакую.',
    'Гоблин отступает, но я преследую его.',
    'Мост рухнул, но я перепрыгиваю.',
    'Ты можешь идти, но я остаюсь здесь.',
    'Иду к реке, но сначала обыскиваю тело.',
])
def test_but_inside_a_player_action_is_not_deliberation(text):
    assert explanation_text(text) == text


@pytest.mark.parametrize('text', [
    'Но это означает, что бой уже начался.',
    'Однако текущий контекст показывает, что двери нет.',
])
def test_model_reasoning_is_still_replaced(text):
    assert explanation_text(text) == UNCLEAR


def test_conditional_move_is_deferred_not_executed(store, cid):
    """Условие — не «не понял»: шаг откладывается и ждёт события."""
    plan = Plan(summary='Условие', steps=[Step(kind='move', text='Иду к реке', target=RIVER,
                                               disposition='conditional', condition='он побежит')])
    r = play(store, cid, 'Если он побежит, иду к реке', plan=plan)
    assert r['kind'] == 'event' and r['event']['results'][0]['type'] == 'deferred'
    assert store.campaign(cid)['state']['location'] != RIVER
