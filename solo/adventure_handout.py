"""A confirmed map or handout lands on the table when the player lays it out.

r151 cut every printed map and handout out of the book page and left it waiting
for a person; the review screen is where that person confirms one. This module
answers the other half of the chain: the player says «Показываю игрокам карту
лагеря» or «Раздаю карту со стр. 19», and the host finds the material the
campaign already holds by its confirmed printed name or by its page, so the row
travels into `known_media` and onto the «Раздатки и карты» screen instead of
staying inside the book.

Nothing here guesses. A phrase without a name and without a page answers with
what the campaign really has; a material nobody confirmed is named as waiting
for a person; a Master's map is named as closed.
"""
import re

# The act of laying material out. A plain observation («смотрю на карту») is
# not a reveal and must fall through to the ordinary turn.
_VERB = re.compile(r'\b(?:показыва\w*|покажу|покажем|показать|'
                   r'разда(?:ю|ёшь|ешь|ёт|ет|ём|ем|ют|й|йте|л|ла|ло|ли|м|дим|дите|ть|ва\w*)|'
                   r'выкладыва\w*|выложу|выложим|выложить|переда\w*|передам|передадим|передать|'
                   r'отда(?:ю|ёшь|ешь|ёт|ет|ём|ем|ют|й|йте|л|ла|ло|ли|м|дим|дите|ть|ва\w*)|'
                   r'кладу|положу|положить|достаю|достал\w*)\b', re.I)
# What a table can be handed. Without one of these the phrase is not about
# material at all («показываю игрокам сцену»).
_MATERIAL = re.compile(r'\b(?:карт\w*|письм\w*|раздатк\w*|схем\w*|план\w*|рисун\w*|изображени\w*|'
                       r'иллюстраци\w*|документ\w*|записк\w*|листовк\w*|грамот\w*|свит\w*|лист\w*|'
                       r'страниц\w*)\b', re.I)
# "стр. 19", "страница 19", "с. 19" — the page the book printed the material on.
_PAGE = re.compile(r'\b(?:стр|страниц\w*|с)\.?\s*(\d{1,4})\b', re.I)
_QUOTED = re.compile(r'[«"„“]([^»"”]{2,120})[»"”]')
_GENERIC = frozenset(('карт', 'письм', 'раздатк', 'схем', 'план', 'рисун', 'изображени', 'иллюстраци',
                      'документ', 'записк', 'листовк', 'грамот', 'свит', 'лист', 'страниц'))
# Words that describe the act or the audience, not the material: they are dropped
# before the printed name is compared, so «карту лагеря игрокам» is «карта лагеря».
_FILLER = frozenset(('игрокам', 'игроку', 'игроков', 'игрок', 'всем', 'все', 'партии', 'партия',
                     'группе', 'группа', 'для', 'на', 'в', 'во', 'с', 'со', 'стол', 'столе', 'стола',
                     'пожалуйста', 'давай', 'эту', 'этот', 'это', 'эти', 'мою', 'мой', 'моя', 'свою',
                     'свой', 'своя', 'потом', 'сначала', 'теперь', 'вот', 'вам', 'нам'))
# Words that point at a material without describing it («та карта, где река»):
# they carry no detail, so they never take part in the description match.
_POINTER = frozenset(('ту', 'та', 'то', 'те', 'тот', 'эта', 'этом', 'этого', 'где', 'про', 'что',
                      'как', 'какой', 'какая', 'какие', 'которая', 'который', 'которое', 'которые',
                      'котором', 'которой', 'там', 'здесь', 'ним', 'ней', 'нее', 'нему', 'одну', 'один',
                      'стр', 'страниц'))
# The kind of material a phrase names. A word the player used narrows the
# search («раздаю письмо» is not a map); it never widens it.
_KIND_OF = (('карт', ('map',)), ('план', ('map',)), ('схем', ('map',)),
            ('рисун', ('map', 'image', 'illustration')), ('изображ', ('map', 'image', 'illustration')),
            ('иллюстрац', ('map', 'image', 'illustration')),
            ('письм', ('handout',)), ('раздатк', ('handout',)), ('листовк', ('handout',)),
            ('грамот', ('handout',)), ('свит', ('handout',)), ('записк', ('handout',)),
            ('документ', ('handout',)))
_ENDINGS = ('ами', 'ями', 'ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ой', 'ый', 'ий', 'ая', 'ое', 'ые',
            'ие', 'ов', 'ев', 'ам', 'ям', 'ах', 'ях', 'ом', 'ем', 'ую', 'юю', 'а', 'о', 'у', 'ы', 'и',
            'е', 'ю', 'я', 'ь', 'й')


def normalise(text):
    """Printed names and typed phrases compared on the same letters."""
    lowered = str(text or '').lower().replace('ё', 'е')
    return re.sub(r'\s+', ' ', re.sub(r'[^0-9a-zа-я]+', ' ', lowered)).strip()


def stem(word):
    """A crude Russian stem: enough to match a typed case to a printed name."""
    for ending in _ENDINGS:
        if len(word) - len(ending) >= 3 and word.endswith(ending):
            return word[:-len(ending)]
    return word


def _keep(word):
    """Words a printed name may carry: only the act and the audience are gone.

    A describing word is not dropped here: the search for a material by its own
    printed name must still see «План один» or «Карта там», and the words that
    describe nothing are dropped in `content_of` instead.
    """
    return len(word) > 1 and word not in _FILLER


# Words a phrase carries but that describe nothing: a copula, a quantity, a
# service phrase. They never take part in the description match.
_SERVICE = frozenset(stem(word) for word in (
    'не', 'нет', 'без', 'кроме', 'есть', 'был', 'была', 'было', 'были', 'будет', 'нужен', 'нужно',
    'можно', 'стоит', 'надо', 'ещё', 'уже', 'только', 'очень', 'весь', 'вся', 'все', 'всё', 'любой',
    'любая', 'другой', 'другая', 'себе', 'между', 'через', 'либо', 'или'))
# A phrase that negates («карта, где НЕТ реки») describes what the material must
# not show: matching it would put the opposite card on the table.
_NEGATION = frozenset(('не', 'нет', 'без', 'кроме'))


def negated(key):
    """True when the phrase says what the material does not show."""
    return any(word in _NEGATION for word in key)


def tokens(text):
    """Stems of the words that can name a material; fillers are dropped."""
    return [stem(word) for word in normalise(text).split() if _keep(word)]


def key_of(text):
    """What is left of a phrase once the act and the audience are gone.

    A bare noun («раздаю карту») is not a name: only a page can resolve it, and
    guessing the only map in the campaign would be the host inventing a choice.
    """
    found = [stem(word) for word in normalise(text).split() if _keep(word)]
    return found if any(token not in _GENERIC for token in found) else []


def page_of(text):
    match = _PAGE.search(str(text or ''))
    return int(match[1]) if match else None


def asked(text):
    """The reveal the player typed, or None when the phrase is something else."""
    line = ' '.join(str(text or '').split())
    if not line or not _VERB.search(line):
        return None
    quoted = _QUOTED.findall(line)
    page = page_of(line)
    if quoted:
        return dict(key=tokens(quoted[-1]), page=page)
    if not _MATERIAL.search(line) and page is None:
        return None
    return dict(key=key_of(_VERB.sub(' ', line)), page=page)


def printed_name(row):
    """The name a person confirmed, or the caption the book printed."""
    return str(row.get('public_name') or row.get('caption') or '').strip()


def is_open(row):
    """The same gate the engine's own `handout` effect applies."""
    return (row.get('status') == 'confirmed' and bool(row.get('reviewed')) and bool(row.get('sanitized'))
            and row.get('scope') in ('player', 'character'))


def score(name, key):
    """How many printed words of a material the phrase actually carries.

    0 means "not this material". The longer name wins, so «Карта лагеря
    налетчиков» is preferred over «Карта лагеря» when both match.
    """
    if not name or not key:
        return 0
    printed = tokens(name)
    if not printed:
        return 0
    if all(token in key for token in printed) and any(token not in _GENERIC for token in printed):
        return len(printed)
    if all(token in printed for token in key):
        return len(key)
    return 0


def _printed_text(row):
    """Everything the book prints about a material that a person left open.

    The extracted page text is deliberately not here: `public_media` keeps it
    behind the review screen, and a search reading it would answer with words
    the table was never allowed to see. The model's markup of the picture is:
    it names what is visible on the material («река», «мост»), which is exactly
    how a player describes a card he holds, and it says nothing the book prints
    next to it.
    """
    from .material_markup import searchable
    printed = ' '.join(str(row.get(field) or '') for field in ('public_name', 'caption', 'public_text'))
    return (printed + ' ' + searchable(row)).strip()


def content_of(key):
    """The words a phrase uses to describe a material, without its kind.

    «Ту карту, где река» describes by «река»: «карта» is the kind of material
    asked for, and a search counting it would match every map in the book.
    """
    return [word for word in key if word not in _GENERIC and word not in _POINTER
            and word not in _SERVICE and not word.isdigit()]


def kinds_of(key):
    """The material kinds the phrase itself names, or () when it names none."""
    wanted = []
    for word in key:
        for prefix, kinds in _KIND_OF:
            if word.startswith(prefix):
                for kind in kinds:
                    if kind not in wanted:
                        wanted.append(kind)
    return tuple(wanted)


def of_kind(rows, key):
    """Rows the phrase's own kind word allows; every row when it names none.

    A row may answer as more than its importer kind: a picture the model marked
    up as a map is a map even when the importer stored it as an illustration.
    """
    from .material_markup import kinds_of_row
    kinds = kinds_of(key)
    if not kinds:
        return list(rows)
    return [row for row in rows if set(kinds_of_row(row)) & set(kinds)]


def described(rows, key, universe=None):
    """Materials whose printed text carries the describing words of a phrase.

    A describing word is worth what the book itself says about it: a word the
    bundle prints about one material is worth 2, about two or three materials 1,
    and about more than three nothing at all — such a word describes a class of
    materials («сокровище», «комната»), not one of them. A phrase is matched at
    2 points, and that is the threshold because a wrong map on the table is
    worse than «не понял, какую»: either one word the bundle prints about a
    single material («та карта, где река»), or two narrower words landing in the
    same material.

    Returns every row tied for the best score, and that score.
    """
    wanted = content_of(key)
    if not wanted:
        return [], 0
    universe = list(universe if universe is not None else rows)
    printed = {row.get('id'): set(tokens(_printed_text(row))) for row in universe}
    # A word is weighed by how many *materials* carry it, not by how many rows
    # do: the importer sometimes cuts one picture twice with the same printed
    # caption, and counting the copy as a second material halved the weight of
    # a word that describes exactly one card («Рог единорога», стр. 7, r190).
    def identity(row):
        return row.get('page'), printed_name(row) or row.get('id')

    weight = {}
    for word in wanted:
        spread = len({identity(row) for row in universe if word in printed.get(row.get('id'), ())})
        weight[word] = 2 if spread == 1 else 1 if spread <= 3 else 0
    ranked = []
    for row in rows:
        words = printed.get(row.get('id')) or set(tokens(_printed_text(row)))
        points = sum(weight[word] for word in wanted if word in words)
        if points:
            ranked.append((points, row))
    if not ranked:
        return [], 0
    top = max(points for points, _ in ranked)
    return [row for points, row in ranked if points == top], top


def _listed(rows, limit=3):
    shown = ['«' + (printed_name(row) or 'без названия') + '» (стр. ' + str(row.get('page')) + ')'
             for row in rows[:limit]]
    if len(rows) > limit:
        shown.append('и ещё ' + str(len(rows) - limit))
    return ', '.join(shown)


def resolve(materials, *, key=(), page=None):
    """Find the material the phrase names, or say honestly why it cannot.

    Returns one of: found / ambiguous / pending / closed / missing, with the
    material where one was found and a player-readable `text` otherwise.
    """
    # A full-page scan is the page itself, not a card a player is handed: the
    # importer leaves those rows with kind 'page'. Only a picture cut out of the
    # page can be laid on the table, so those rows never answer a reveal.
    rows = [row for row in materials or [] if row.get('kind') != 'page']
    open_rows = [row for row in rows if is_open(row)]
    closed_rows = [row for row in rows if row.get('status') == 'confirmed' and not is_open(row)]
    waiting = [row for row in rows if row.get('status') != 'confirmed']

    def best(rows):
        ranked = sorted(((score(printed_name(row), key), row) for row in rows), key=lambda pair: -pair[0])
        ranked = [pair for pair in ranked if pair[0] > 0]
        if not ranked:
            return []
        top = ranked[0][0]
        return [row for points, row in ranked if points == top]

    def on_page(rows):
        return [row for row in rows if page is None or row.get('page') == page]

    if key:
        hits = best(on_page(open_rows))
        if len(hits) == 1:
            return dict(status='found', material=hits[0])
        if hits:
            return dict(status='ambiguous', materials=hits,
                        text='Под эту фразу подходит несколько подтверждённых материалов: ' + _listed(hits)
                             + '. Назовите страницу, чтобы Мастер показал нужный.')
        waiting_hits = best(on_page(waiting))
        if len(waiting_hits) == 1:
            return dict(status='pending', material=waiting_hits[0],
                        text='Материал «' + (printed_name(waiting_hits[0]) or 'без названия') + '» (стр. '
                             + str(waiting_hits[0].get('page')) + ') ещё ждёт подтверждения человеком в проверке '
                             'комплекта: показать его сейчас нельзя.')
        closed_hits = best(on_page(closed_rows))
        if len(closed_hits) == 1:
            return dict(status='closed', material=closed_hits[0],
                        text='Материал «' + (printed_name(closed_hits[0]) or 'без названия') + '» (стр. '
                             + str(closed_hits[0].get('page')) + ') подтверждён как материал Мастера и игрокам не '
                             'открывается.')
        # Second path: the player described the material instead of naming it
        # («показываю ту карту, где река», «раздаю письмо про Даффита»). The
        # same gate decides what may be laid out, so a candidate is never
        # revealed; only what a person left open is read — the printed name, the
        # caption and the text marked public.
        if negated(key):
            # «Карта, где НЕТ реки» describes the opposite of a match: a word
            # match would lay the wrong card on the table, so the host says what
            # it cannot do instead of guessing.
            return dict(status='missing',
                        text='Фразу с отрицанием Мастер по описанию не разбирает: назовите, '
                             'что на материале есть, или его страницу.' + _have(open_rows))
        pool = of_kind(rows, key)
        found_desc, points = described(of_kind(on_page(open_rows), key), key, pool)
        if points >= 2:
            if len(found_desc) == 1:
                return dict(status='found', material=found_desc[0])
            if found_desc:
                return dict(status='ambiguous', materials=found_desc,
                            text='По этому описанию подходит несколько подтверждённых материалов: '
                                 + _listed(found_desc) + '. Какой именно показать? Назовите страницу.')
        elif len(found_desc) > 1:
            # The description is narrowed but not settled («карта, где река»
            # over two maps): the host asks instead of choosing a card.
            return dict(status='ambiguous', materials=found_desc,
                        text='По этому описанию подходит несколько подтверждённых материалов: '
                             + _listed(found_desc) + '. Какой именно показать? Назовите страницу.')
        waiting_desc, points = described(of_kind(on_page(waiting), key), key, pool)
        if points >= 2 and waiting_desc:
            # A candidate nobody reviewed is named by its page, never by the
            # caption the review screen exists to hide: the phrase described it,
            # it did not confirm it.
            return dict(status='pending', material=waiting_desc[0],
                        text='Материал, который вы описываете (стр. ' + str(waiting_desc[0].get('page'))
                             + '), ещё ждёт подтверждения человеком в проверке комплекта: показать его сейчас нельзя.')
        closed_desc, points = described(of_kind(on_page(closed_rows), key), key, pool)
        if points >= 2 and closed_desc:
            return dict(status='closed', material=closed_desc[0],
                        text='Материал, который вы описываете (стр. ' + str(closed_desc[0].get('page'))
                             + '), подтверждён как материал Мастера и игрокам не открывается.')
    if page is not None:
        # A page alone is enough when it holds exactly one open material.
        rows = on_page(open_rows)
        if len(rows) == 1:
            return dict(status='found', material=rows[0])
        if len(rows) > 1:
            return dict(status='ambiguous', materials=rows,
                        text='На странице ' + str(page) + ' несколько подтверждённых материалов: '
                             + _listed(rows) + '. Назовите нужный.')
        rows = on_page(waiting)
        if rows:
            # Something is printed on that page and nobody has confirmed it yet:
            # saying "there is nothing there" would be a lie about the book.
            return dict(status='pending', material=rows[0],
                        text='Материал на стр. ' + str(page) + ' ещё ждёт подтверждения человеком: '
                             'показать его сейчас нельзя.')
        return dict(status='missing',
                    text='На стр. ' + str(page) + ' нет подтверждённой карты или раздатки.' + _have(open_rows))
    if content_of(key):
        return dict(status='missing',
                    text='По этому описанию нет подтверждённой карты или раздатки.' + _have(open_rows))
    return dict(status='missing',
                text='В кампании нет подтверждённой карты или раздатки с таким названием.' + _have(open_rows))


def _have(open_rows):
    """What the module does hold, when the phrase matched nothing.

    Five names are enough for a player to recognise his card, and the rest is
    counted instead of listed: a refusal that prints forty names is a wall, not
    an answer.
    """
    if not open_rows:
        return ' Подтверждённых карт и раздаток в кампании пока нет.'
    return ' Вот что есть: ' + _listed(open_rows, 5) + '.'
