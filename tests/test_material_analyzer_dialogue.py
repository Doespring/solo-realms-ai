"""The Master asks what the material never said, in dialogue, and keeps the answer.

The question queue is not the proof: these tests run the same functions the
game runs, on a state that is written to and read back as JSON, so an answer
that only lives in memory would fail here.
"""
import os
os.environ['SOLO_TEST']='1'
import json
import pytest
from solo import open_settings


def state(*fields):
    return dict(preparation=dict(open_questions=[
        dict(field=field, question=question) for field, question in fields]))


QUESTIONS=[('level','С какого уровня начинаем?'),('party_size','Сколько героев за столом?')]


def test_the_master_asks_the_missing_question_and_keeps_the_answer():
    current=state(*QUESTIONS)
    waiting=open_settings.next_question(current)
    assert waiting['question']=='С какого уровня начинаем?'
    heard=open_settings.answer(current,'Пятый уровень')
    assert heard[:2]==('level',5)
    saved=current['preparation']['answers']['level']
    assert saved['value']==5 and saved['provenance']=='player_answer'


def test_a_vague_reply_is_not_squeezed_into_a_setting():
    current=state(*QUESTIONS)
    assert open_settings.answer(current,'Давай потом, сначала осмотрюсь.') is None
    # Nothing was recorded, so the Master still asks the same question.
    assert current['preparation'].get('answers',{})=={}
    assert open_settings.next_question(current)['field']=='level'


def test_the_answer_survives_a_restart_and_is_never_asked_again():
    current=state(*QUESTIONS)
    open_settings.answer(current,'5 уровень')
    # A restart is exactly the state read back from storage.
    restarted=json.loads(json.dumps(current,ensure_ascii=False))
    assert open_settings.next_question(restarted)['field']=='party_size'
    assert open_settings.answer(restarted,'5 уровень') is None
    assert restarted['preparation']['answers']['level']['value']==5


def test_every_question_is_answered_once_in_order():
    current=state(*QUESTIONS)
    seen=[]
    for text in ('пятый уровень','нас четверо'):
        entry=open_settings.next_question(current)
        seen.append(entry['field'])
        assert open_settings.answer(current,text)
    assert seen==['level','party_size']
    assert open_settings.next_question(current) is None


def test_a_level_dependent_step_waits_but_the_conversation_does_not():
    current=state(*QUESTIONS)
    assert open_settings.pending_for_mechanics(current)
    open_settings.answer(current,'седьмой уровень')
    assert open_settings.pending_for_mechanics(current)==''
    # Asking is never the same thing as refusing the table.
    assert open_settings.read_level('седьмой уровень')==7


@pytest.mark.parametrize('text,expected',[
    ('Пятый уровень',5),('5 уровень',5),('уровень 5',5),('уровень пятый',5),
    ('5-го уровня',5),('level 5',5),('5th level',5),('Поговорим о погоде',None)])
def test_level_phrases_the_player_actually_uses(text,expected):
    assert open_settings.read_level(text)==expected


@pytest.mark.parametrize('text,expected',[
    ('нас четверо',4),('четверо героев',4),('4 героя',4),
    ('группа из трёх игроков',3),('три игрока',3),('пойдём втроём',3),('неважно',None)])
def test_party_phrases_the_player_actually_uses(text,expected):
    assert open_settings.read_party(text)==expected


def test_an_unknown_place_name_is_classified_not_refused():
    current=dict(locations=[dict(name='Старая башня'),dict(name='Берег реки')])
    # Another name for a place the world already has.
    assert open_settings.match_place(current,'башня')==('known','Старая башня')
    assert open_settings.match_place(current,'Пойдём на Берег реки')==('known','Берег реки')
    # A wish the world does not have is an addition, kept as one.
    kind,name=open_settings.match_place(current,'Чёрная цитадель')
    assert kind=='add' and 'цитадель' in name.lower()
    # A word too vague to mean one place asks instead of guessing.
    two=dict(locations=[dict(name='Старая башня'),dict(name='Белая башня')])
    kind,message=open_settings.match_place(two,'башня')
    assert kind=='ask' and 'Белая башня' in message
