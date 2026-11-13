"""r240: страница книги разбирается на «переходы» один раз, а не на каждое место.

Причина, по которой сборка мира из 128 принятых блоков шла десятки минут:
`names_place` для каждой пары мест заново делил целую страницу на предложения и
искал в них глагол движения. Правило от этого не меняется — меняется цена:
предложения с переходом отбираются один раз на страницу и кладутся в кэш.

Здесь два утверждения:

1. ответ `names_place` совпадает с прежним правилом «пройти по предложениям и
   спросить `transition_in`» — на настоящих формулировках книги, включая
   именного персонажа и предложения без глагола движения;
2. та же страница не разбирается повторно: на полсотни мест — один разбор.
"""
import re
import pytest
from solo.prepared_world import _travel_sentences, _travel_sentences_cached, names_place, transition_in

PAGE=('Дом1. Зал с колоннами и факелами. Вход охраняет стража. '
    'Отсюда группа попадёт в Кузню по узкой лестнице. '
    'Казарин со своими учениками придёт к поместью Даффита. '
    'Лестница ведёт на галерею. ')


def sentence_by_sentence(name,quote):
    """Прежнее правило: каждое предложение страницы — вопрос к `transition_in`."""
    if not name.strip() or not quote:return False
    return any(transition_in(sentence,name) for sentence in re.split(r'(?<=[.!?])\s+',quote))


@pytest.mark.parametrize('name',['Кузня','кузни','Галерея','галерею','Поместье Даффита',
    'поместью Даффита','Зал','Дом1','Стража','Оранжерея',''])
def test_page_naming_rule_matches_the_sentence_by_sentence_rule(name):
    """Отбор предложений не меняет ответ: что называло место, то и называет."""
    assert names_place(name,PAGE)==sentence_by_sentence(name,PAGE),(name,names_place(name,PAGE))


def test_only_sentences_that_move_the_party_are_kept():
    """Именной персонаж и предложения без движения отсеиваются до сравнения."""
    kept=_travel_sentences(PAGE)
    assert any('Кузню' in sentence for sentence in kept),kept
    assert any('галерею' in sentence for sentence in kept),kept
    assert not any('Казарин' in sentence for sentence in kept),kept
    assert not any('Вход охраняет' in sentence for sentence in kept),kept


def test_the_same_page_is_split_once_for_all_places():
    """Полсотни мест против одной страницы — один разбор страницы."""
    _travel_sentences_cached.cache_clear()
    names=['Кузня','Галерея','Зал','Стража','Оранжерея']+['Место'+str(index) for index in range(45)]
    for name in names:names_place(name,PAGE)
    info=_travel_sentences_cached.cache_info()
    assert info.misses==1,info
    assert info.hits==len(names)-1,info
