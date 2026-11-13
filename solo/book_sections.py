"""What kind of section a printed heading opens, and what a reference indexes.

A book's appendix is not an adventure. «Приложение А: Монстры» prints stat
blocks, «Приложение Б: Магические предметы» prints item entries and a table
appendix prints tables: a reader looks a monster up by its page, and no start
ever begins inside a bestiary. The type is read from the printed heading and,
when the heading alone does not say it, from the cues the pages themselves
print («Класс Доспеха», a price in «зм», a «Таблица 3» line). Nothing here asks
a model, so the same pages give the same answer twice.

Every indexed entry keeps the page and the printed line it was read from; a
line that its own page does not contain is reported instead of stored.
"""
import re

from .prepared_world import normalized, source_span

#: The words a book prints in front of its reference sections. The appendix is
#: the only section type that is not a scenario; a chapter is an adventure even
#: when its title says nothing about play.
APPENDIX_WORDS = {'приложение', 'appendix'}

#: What a reference section indexes, in the book's own words. The first match
#: wins: «Приложение А: Монстры и предметы» indexes monsters.
REFERENCE_WORDS = (
    ('monsters', ('монстр', 'бестиарий', 'чудовищ', 'monster', 'bestiary')),
    ('items', ('предмет', 'сокровищ', 'магическ', 'item', 'treasure')),
    ('tables', ('таблиц', 'table', 'random')),
)

#: Printed cues of a stat block, an item entry and a table. The name stands on
#: the line above, which is why the anchor is the label and not the name.
_MONSTER_ANCHOR = re.compile(r'^(?:класс доспеха|опасность\b|хиты\b)', re.I)
_ITEM_ANCHOR = re.compile(r'^(?:оружие|доспех|щит|чудесный предмет|волшебный предмет|зелье|снаряжение|'
                          r'инструмент|кольцо|жезл|посох|свиток|боеприпас|драгоценный камень)\b', re.I)
_TABLE_ANCHOR = re.compile(r'^таблица\s*[\dа-яa-z]', re.I)
#: A name line is short and does not end a sentence; everything else on a stat
#: block is a label, a number or the prose of the block itself.
_LABEL = re.compile(r'^(?:класс доспеха|хиты|скорость|опасность|сопротивлени|иммунитет|'
                    r'чувства|языки|испытания|навыки|размер|тип|мировоззрение|бонус|'
                    r'спасброс|восприяти|атака|урон|попадание|эффект)', re.I)
#: Строка типа под именем монстра: «Средний гуманоид (человек), нейтрально-злой».
#: Книга печатает её между именем и «Класс Доспеха», поэтому имя — строкой выше,
#: а без этого пропуска в справочник попадал тип: «Средний гуманоид…» вместо имени.
_TYPE = re.compile(r'^(?:крошечн|маленьк|средн|больш|огромн|громадн|'
                   r'tiny|small|medium|large|huge|gargantuan)\w*\b', re.I)


def _type_line(line):
    """Whether a line is the monster's type, not its name.

    «Большой медведь» is a name and «Большой зверь, без мировоззрения» is a type
    line: the book separates the type with a comma or a parenthesis after the
    size word. Skipping every line that starts with a size word lost monsters
    whose names begin with one.
    """
    return bool(_TYPE.match(line)) and (',' in line or '(' in line)

_HEADING = re.compile(r'^(?:глава|эпизод|часть|приложение|введение|пролог|chapter|episode|'
                      r'part|appendix)\b', re.I)
_NUMBER = re.compile(r'^\d{1,4}$')
#: A reference volume prints hundreds of entries; the index stops before a
#: merged mega-document turns one appendix into a whole library of its own.
MAX_ENTRIES = 500
#: The section says what the appendix indexes in the plural («монстры»), the row
#: says what the entry itself is: one monster, one item, one table. The editor's
#: list of types is in the singular, so the row keeps the singular too.
SINGULAR = {'monsters': 'monster', 'items': 'item', 'tables': 'table'}


def is_reference(title):
    """Whether a printed heading opens an appendix instead of an episode."""
    words = normalized(title).split()
    return bool(words and words[0].strip(':.') in APPENDIX_WORDS)


def section(title):
    """The section a heading opens: an episode or an appendix."""
    return 'appendix' if is_reference(title) else 'adventure'


def reference_kind(title, text=''):
    """What an appendix indexes: `monsters`, `items`, `tables` or `reference`.

    The heading decides when it names the kind. A heading that does not («Приложение
    В») is read from its own pages: a stat block, a price in «зм» and a dice table
    are printed cues, and counting them is cheaper and steadier than guessing.
    """
    heading = normalized(title)
    for kind, words in REFERENCE_WORDS:
        if any(word in heading for word in words):
            return kind
    sample = str(text or '')
    if len(_MONSTER_ANCHOR.findall(sample)) >= 2:
        return 'monsters'
    if len(re.findall(r'(?<!\w)зм(?!\w)', sample)) >= 2:
        return 'items'
    if len(re.findall(r'(?<!\w)к\d{1,2}(?!\w)', sample.lower())) >= 3:
        return 'tables'
    return 'reference'


def _lines(text):
    return [(position, line.strip()) for position, line in enumerate(str(text or '').splitlines())
            if line.strip()]


def _name_line(lines, index):
    """The printed name above a stat block or an item entry, or ''.

    The book prints the name on the line just above the label, but a running
    header or an empty line may stand in between, so the walk goes back a few
    lines and stops at the first thing that is a name: short, not a sentence and
    not a bold label of the block itself.
    """
    for position in range(index - 1, max(-1, index - 6), -1):
        line = lines[position][1]
        if _LABEL.match(line) or _type_line(line) or _HEADING.match(line) or _NUMBER.match(line):
            continue
        if len(line) > 70 or line.endswith(('.', ':', ';')):
            continue
        if not line[:1].isalpha() or not line[:1].isupper():
            continue
        if len(re.findall(r'\w', line)) < 3:
            continue
        return line
    return ''


def _anchor(lines, pattern):
    """Lines that open an entry of one kind, with the name printed above them."""
    found = []
    for index, (_position, line) in enumerate(lines):
        if pattern.match(line):
            name = _name_line(lines, index)
            if name:
                found.append((name, line))
    return found


def entries(pages, kind, document=''):
    """Index one appendix: `(accepted, rejected)`, each entry with its page.

    `pages` maps a page number to the text the extractor read from it. A monster
    is named by the line printed above its stat block, an item by the line above
    its type line, a table by the «Таблица N» line itself. The quote is restored
    to the page's own spelling, and an entry whose line is not on its page is
    rejected instead of being stored.
    """
    accepted, rejected, seen = [], [], set()
    for number in sorted(pages):
        text = pages[number]
        lines = _lines(text)
        if kind == 'monsters':
            found = _anchor(lines, _MONSTER_ANCHOR)
        elif kind == 'items':
            found = _anchor(lines, _ITEM_ANCHOR)
        elif kind == 'tables':
            found = [(line, line) for _position, line in lines if _TABLE_ANCHOR.match(line)]
        else:
            found = []
        for name, line in found:
            key = (kind, normalized(name))
            if key in seen:
                continue
            try:
                quote = source_span(text, line)
            except ValueError:
                rejected.append(dict(name=name, kind=kind, page=number, quote=line,
                                     reason='Цитата не найдена на странице'))
                continue
            seen.add(key)
            accepted.append(dict(name=name[:120], kind=kind, entity_type=SINGULAR.get(kind, kind),
                                 page=number, document=document, quote=quote, scope='reference',
                                 source='printed',
                                 provenance=dict(document=document, page=number, quote=quote,
                                                 source='printed')))
            if len(accepted) >= MAX_ENTRIES:
                return accepted, rejected
    return accepted, rejected
