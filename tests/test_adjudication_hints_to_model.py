"""r178: там, где решает Мастер, пакет хода несёт вопрос правила и его числа.

r171 разложил 56 правил `adjudication_required` главы классов: 12 «Эпических
даров» кодом не исчислить (выгоды живут в записи книги, `facts` пуст), ещё у
тридцати правил есть `semantic_questions` — вопрос, на который отвечает Мастер.
До этой итерации пакет интерпретатора нёс лист героя и команды движка, а вопрос
правила модель не видела.

Файл держит четыре обещания:

1. **уровень**: подсказка «предложи выбрать эпический дар» появляется только с
   19 уровня и молчит, когда дар уже записан;
2. **содержание**: правило, названное в реплике, приходит в пакет вместе с
   вопросом корпуса и `facts` — модель называет числа правила, а не выдумывает;
3. **размер**: в пакет идут только правила, которых реплика касается; на
   нерелевантный ход подсказок нет вовсе, а раздел ограничен по числу и по
   оценке токенов;
4. **маршрут**: пять реплик через офлайн-мок доезжают до интерпретатора вместе
   с подсказкой (`PreviewMock`), то есть это пакет модели, а не поле в памяти.

Числа правил здесь не пересчитываются: `facts` берутся из корпуса
(`rules_data/v2/chapters/phb-03-classes/*.json`), страница — из `source` записи.
"""
import os
os.environ['SOLO_TEST'] = '1'

import json

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud_context import text_estimate
from solo.game import Game
from solo.nat20_api import Nat20GameEngine
from solo.nat20_content import EPIC_BOON_LEVEL, HINT_LIMIT, _small_facts, adjudication_hints
from solo.preview_mock import PreviewMock
from solo.storage import Store

#: Реплики — те же пять вопросов, что в отчёте r171 («решает Мастер»): каждая
#: называет правило главы классов, поэтому подсказка обязана доехать до модели.
REPLICAS = [
    ("Кому из союзников отдать кость бардовского вдохновения?", "bard.vardo_vdohnovenie"),
    ("В какого зверя мне превратиться Диким обликом?", "druid.dikii_oblik"),
    ("Какая нежить в пределах 30 футов попадает под Изгнание нежити?", "cleric.izgnanie_nezheti"),
    ("Какой эффект я создаю Проведением божественности?", "cleric.provedenie_bozhestvennosti"),
    ("Что именно резолвится перебросом от Контрочарования?", "bard.kontrocharovanie"),
]

#: Ходы, которых правила главы классов не касаются: подсказок быть не должно.
OFF_TOPIC = ["атакую гоблина", "короткий отдых", "Уклоняюсь", "Осматриваюсь", "Иду на клетку 3, 2"]


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    directory = tmp_path_factory.mktemp("r178-hints")
    store = Store(directory / "game.sqlite3")
    cid = Nat20GameEngine(store).create_campaign("r178 · подсказки", seed=7)["id"]
    return store, cid


def _hints(store, cid, query):
    return Game(store, None).compiler.command_context(store.campaign(cid), query=query)["adjudication_hints"]


def _rule_ids(hints):
    return [row["id"].rsplit(".", 1)[-1] for row in hints["rules"]]


def test_named_rule_reaches_packet_with_question_and_facts(campaign):
    """Правило, названное в реплике, идёт в пакет вопросом и числами корпуса."""
    store, cid = campaign
    hints = _hints(store, cid, "Кому из союзников отдать кость бардовского вдохновения?")
    assert _rule_ids(hints) == ["vardo_vdohnovenie"], _rule_ids(hints)
    row = hints["rules"][0]
    assert row["decision"] == "master"
    assert "60 футов" in row["question"]
    # Числа правила — из `facts` записи, а не из пересказа: 60 фт и 1 кость.
    assert row["facts"]["range_feet"] == 60
    assert row["facts"]["max_dice_per_creature"] == 1
    assert row["source"]["printed_page"], row["source"]
    assert hints["note"], "без пояснения модель может принять вопрос за описание умения"


def test_off_topic_turn_carries_no_rule_hints(campaign):
    """Нерелевантные ходу правила не идут: «атака» есть в трёх правилах главы."""
    store, cid = campaign
    for text in OFF_TOPIC:
        hints = _hints(store, cid, text)
        assert hints["rules"] == [], (text, _rule_ids(hints))
        assert hints["note"] is None, text


def test_hint_section_is_bounded(campaign):
    """Раздел подсказок ограничен: не больше `HINT_LIMIT` правил и бюджета токенов."""
    store, cid = campaign
    biggest = 0
    for text, _ in REPLICAS + [(row, None) for row in OFF_TOPIC]:
        context = Game(store, None).compiler.command_context(store.campaign(cid), query=text)
        hints = context["adjudication_hints"]
        assert len(hints["rules"]) <= HINT_LIMIT
        without = dict(context)
        without.pop("adjudication_hints")
        growth = text_estimate(json.dumps(context, ensure_ascii=False)) - \
            text_estimate(json.dumps(without, ensure_ascii=False))
        biggest = max(biggest, growth)
    # Замер r178: самый дорогой ход (Изгнание нежити: вопрос, пять facts и
    # страница) — 386 токенов по оценке приложения. Потолок держит рост
    # заметно ниже тысячи.
    assert biggest < 700, biggest


def test_hint_limit_and_skipped_are_reported():
    """Предел вопросов на ход работает, а отброшенные видны в `skipped`.

    Отдельная проверка механизма: на корпусе главы в один ход редко попадает
    больше одного-двух правил, поэтому предел проверяется явным `limit`.
    """
    state = {"hero": {"level": 1, "class_name": "cleric"}}
    query = "Изгнание нежити и Проведение божественности"
    loose = adjudication_hints(state, query, limit=HINT_LIMIT)
    assert len(loose["rules"]) >= 2, _rule_ids(loose)
    tight = adjudication_hints(state, query, limit=1)
    assert len(tight["rules"]) == 1
    assert tight["skipped"] == len(loose["rules"]) - 1
    # Своё правило идёт первым: у жреца — вопрос его Проведения, а не паладина.
    assert tight["rules"][0]["klass"] == "cleric"


def test_twins_of_one_rule_do_not_take_two_slots():
    """«Проведение божественности» есть у жреца и паладина — в пакет один вопрос."""
    hints = adjudication_hints({"hero": {"level": 5, "class_name": "cleric"}},
                               "Какой эффект я создаю Проведением божественности?")
    tails = [row["id"].rsplit(".", 1)[-1] for row in hints["rules"]]
    assert len(tails) == len(set(tails)), tails
    assert "provedenie_bozhestvennosti" in tails
    assert hints["rules"][0]["klass"] == "cleric"


def test_rule_facts_are_truncated_to_scalars():
    """`facts` не пересказываются целиком: числа и короткие строки, не больше восьми."""
    small = _small_facts(dict(long="Я" * 400, count=3, flag=True,
                              table={f"row{index}": index for index in range(10)},
                              nested={"inner": {"deep": 1}}, dropped=1))
    assert small["long"] == "Я" * 140
    assert small["count"] == 3 and small["flag"] is True
    assert len(small["table"]) <= 6
    assert "nested" not in small  # вложенный словарь пересказывать нечем
    assert small["dropped"] == 1  # скаляр в пределах восьми записей остаётся
    # Восемь записей — потолок, даже если в корпусе их больше.
    assert len(_small_facts({f"key{index}": index for index in range(12)})) == 8


def test_epic_boon_hint_starts_at_level_19():
    """Подсказка «выбери дар» — только с 19 уровня (§ PHB 2024, Эпические дары)."""
    for level in range(1, EPIC_BOON_LEVEL):
        hints = adjudication_hints({"hero": {"level": level, "class_name": "bard"}}, query="")
        assert hints["epic_boon"] is None, level
        assert hints["hero"]["level"] == level
    hints = adjudication_hints({"hero": {"level": EPIC_BOON_LEVEL, "class_name": "bard"}}, query="")
    boon = hints["epic_boon"]
    assert boon and boon["decision"] == "player"
    assert str(EPIC_BOON_LEVEL) in boon["text"]
    # Запись дара корпуса для своего класса — с книжной страницей, а не выдумкой.
    assert boon["id"].endswith("bard.epicheskii_dar")
    assert boon["source"]["printed_page"]
    # Выгоды не пересказываются: в данных движка их нет, подсказка зовёт в книгу.
    assert "книг" in boon["text"]


def test_epic_boon_hint_silent_when_boon_is_recorded():
    """Записанный дар гасит подсказку — иначе Мастер спрашивал бы каждый ход.

    Слаги настоящих даров — `boon-of-truesight`, `boon-of-speed` (SRD), а не
    «epic-…»: поиск по подстроке «epic» их не находил (замечание критика r178).
    """
    for hero in ({"level": 20, "class_name": "bard", "epic_boon": "boon-of-truesight"},
                 {"level": 20, "class_name": "bard", "epic_boons": ["boon-of-speed"]},
                 {"level": 20, "class_name": "bard", "features": ["boon-of-truesight"]}):
        assert adjudication_hints({"hero": hero}, query="")["epic_boon"] is None, hero


def test_epic_boon_hint_silent_for_class_outside_the_corpus():
    """Класса нет в главе — нет и страницы, на которую сослаться: молчим."""
    for hero in ({"level": 20}, {"level": 20, "class_name": "artificer"}):
        assert adjudication_hints({"hero": hero}, query="")["epic_boon"] is None, hero


def test_low_level_packet_has_no_epic_boon(campaign):
    """В настоящем пакете кампании 1 уровня подсказки о даре нет."""
    store, cid = campaign
    hints = _hints(store, cid, "Иду на клетку 3, 2")
    assert hints["hero"]["level"] == 1
    assert hints["epic_boon"] is None


def test_five_replies_reach_the_interpreter_with_their_hint(campaign):
    """Пять реплик на офлайн-моке: подсказка едет в пакете интерпретатора."""
    store, cid = campaign
    model = PreviewMock()
    app = create_app(store.path, model)
    with TestClient(app) as client:
        for index, (text, expected) in enumerate(REPLICAS):
            version = client.get(f"/api/campaigns/{cid}").json()["version"]
            response = client.post(f"/api/campaigns/{cid}/command-turn",
                                   json=dict(request_id=f"r178-{index}", text=text,
                                             expected_version=version))
            assert response.status_code == 200, response.text[:300]
            packets = [row["packet"] for row in model.packets if row["mode"] == "interpreter"]
            assert packets, f"{text}: реплика не дошла до модели"
            context = packets[-1]["context"]
            # Уточнение после ответа мока склеивается с прежней репликой
            # («...\nУточнение игрока: ...»), поэтому реплика проверяется вхождением.
            assert text in packets[-1]["player_intent"], packets[-1]["player_intent"]
            hints = context["adjudication_hints"]
            suffix = expected.rsplit(".", 1)[-1]
            assert suffix in _rule_ids(hints), (text, _rule_ids(hints))
            row = next(row for row in hints["rules"] if row["id"].endswith(expected))
            assert row["question"] and row["decision"] == "master"


def test_narrator_packet_keeps_no_master_questions(campaign):
    """Рассказчик описывает результат движка, а решения Мастера ему не отдаются."""
    store, cid = campaign
    compiler = Game(store, None).compiler
    event = dict(seq=0, results=[dict(game_event=dict(payload=dict(outcome="ok")))])
    system, user = compiler.command_narrator(store.campaign(cid), event)
    assert "adjudication_hints" not in json.loads(user)
    assert "adjudication_hints" in json.dumps(compiler.command_context(store.campaign(cid),
                                                                      query="Изгнание нежити"))
