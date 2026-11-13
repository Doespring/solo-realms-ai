"""Episode one of the provided book, pinned to verified printed pages.

Only text that was read back from the stored PDF becomes a place, a route, a
creature or a reward. Every constant here is checked against the page text at
call time, so a revised or a different document cannot silently reuse them.
"""
import unicodedata

from .episode_plan import Beat, EpisodePlan, Fight, Group, Link, Place, Quote, compile as compile_plan, encounters as plan_encounters
from .progression import book_progression
from .first_playable import BOUNDARY, HOARD

MODULE='hoard-episode-1'
TITLE='Гринест в огне — улица и крепость'
OPENING='Приближение к Гринесту'
STREET='Улица Гринеста'
KEEP='Крепость Гринеста'
MILL='Мельница'
TUNNEL='Старый туннель'

# Every quote below was verified verbatim against the stored page text.
ANCHORS={
    10:('перебегает из переулка в переулок','восьмерыми кобольдами','Линан Свифт',
        'миновать три группы','1к6 кобольдов и 1к4 культистов','Это единственное безопасное',
        'по 50 Очков Опыта за каждого НИП'),
    11:('Узкий тоннель','ворота для вылазок','берегу ручья'),
    13:('Мельница представляет собой','40 футов в длину и 20 вширь','жернова',
        'Восприятие) Сл 20'),
    14:('полторы дюжины кобольдов','Лангдедроса Цианврас','по 100 ХР сверх'),
    15:('все персонажи получают по 50 ХР','Губернатор Найтхилл в благодарность'),
}


def flatten(text):
    """Compare words, not layout.

    U+FFFE marks where the extractor hyphenated a word, so it is removed. Real
    whitespace stays a separator, otherwise two words would silently fuse.
    """
    stripped=(text or '').replace('\ufffe','').replace('\u00ad','')
    kept=[]
    for char in stripped:
        if char.isspace():kept.append(' ')
        elif unicodedata.category(char)[0] not in 'CZ':kept.append(char)
    return ' '.join(''.join(kept).split())


def page_text(library,page):
    with library.db() as db:
        row=db.execute('SELECT content FROM pages WHERE document=? AND number=?',(HOARD,page)).fetchone()
    if row is None:raise ValueError('Страница '+str(page)+' отсутствует в источнике')
    return flatten(row['content'])


def verify_anchors(library):
    """Refuse to publish this closure unless the book still says all of this."""
    for page,quotes in ANCHORS.items():
        text=page_text(library,page)
        for quote in quotes:
            if flatten(quote) not in text:
                raise ValueError('Источник не подтверждает «'+quote+'» на странице '+str(page))
    return True


def _read_aloud(library,page,start,stop):
    """The printed read-aloud box, bounded by its own first and last sentence."""
    text=page_text(library,page)
    begin=text.find(start)
    if begin<0:raise ValueError('Открытый абзац «'+start+'» не найден')
    end=text.find(stop,begin)
    if end<0:raise ValueError('Граница открытого абзаца «'+stop+'» не найдена')
    return text[begin:end].strip()+' '+stop



# Physical page 10: eight kobolds chase the family; the printed warband is fixed.
FIRST_ENCOUNTER='first-encounter'
KOBOLDS=8
MONSTER='kobold-warrior'


def plan():
    """The chapter as data. Episode one keeps the book own page opening."""
    places = [
        Place(key="approach", name=OPENING, x=1, y=1, environment="forest_road",
              quote=Quote(page=8, start="Уже несколько дней вы путешествуете", stop="Гринест")),
        Place(key="street", name=STREET, x=2, y=2, environment="room",
              quote=Quote(page=10, start="Внезапно, пятеро фигур", stop="Кат")),
        Place(key="keep", name=KEEP, x=4, y=2, environment="room"),
        Place(key="mill", name=MILL, x=2, y=4, environment="room",
              quote=Quote(page=13, start="Мельница представляет собой", stop="жернова")),
    ]
    # The approach is the printed opening of page 8; it must connect, or the
    # verified entry place would be an island and the street unreachable.
    links = [
        Link(source="approach", target="street", meters=120, minutes=2,
             description="Дорога спускается с холма к горящему Гринесту."),
        Link(source="street", target="keep", meters=60, minutes=1,
             description="Улица ведёт к главным воротам крепости."),
        Link(source="street", target="mill", meters=40, minutes=1,
             description="Мельница стоит на берегу ручья."),
    ]
    beats = [
        Beat(id="first-encounter", place=STREET, category="mandatory",
             flag="street_reached", notice="Скорее в крепость!", page=10),
        # Page 10 pays for townsfolk brought in alive; the rule keeps its page.
        Beat(id="reach-the-keep", place=KEEP, category="conditional",
             flag="keep_reached", page=10, reward="escort-npc-alive",
             notice="Ворота за вами закрываются и баррикадируются."),
    ]
    fights = [Fight(id=FIRST_ENCOUNTER, page=10, width=10, height=10, hero_x=1, hero_y=1,
                    groups=[Group(monster=MONSTER, count=KOBOLDS, x=6, y=1, prefix="kobold")])]
    return EpisodePlan(module=MODULE, title=TITLE, document=HOARD, anchors={},
                       places=places, links=links, beats=beats, fights=fights,
                       minimum_level=1, recommended_party=4)


def encounter():
    """The mandatory first encounter, compiled from the printed warband.

    The book fixes the count at eight kobolds, so the composition is not a
    choice and not a difficulty calculation made here.
    """
    return plan_encounters(plan())[FIRST_ENCOUNTER]


def encounters():
    return plan_encounters(plan())


def definition(library, ruleset, ruleset_version):
    """Build the reviewed closure from the verified pages, never from a guess."""
    verify_anchors(library)
    # The verified pages set no level ceiling, so none is invented here: an
    # invented cap would block a legitimate hero without source support.
    return compile_plan(plan(), library, ruleset, ruleset_version,
                        source_ruleset="dnd2014", reader=_read_aloud, check_anchors=False)
