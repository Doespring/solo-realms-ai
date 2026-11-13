"""Pictures printed inside a book page become their own reviewable material.

A bought module prints its maps and handouts inside the book; it does not ship
them as a folder of images. The layout index already says where every picture
sits (PDF points, bottom-left origin) and which text lines were printed around
it, so the book itself decides what is worth offering: a picture smaller than a
fifth of the page area, or shorter than 200 pt on a side, is an ornament and not
a map; a caption printed beside the picture names its kind; a picture that is the
page, with no paragraph printed on it, is a map even when the page prints no word
for one (`map_evidence`); everything else is an illustration.

Nothing here is playable. A cut-out picture is a `candidate` with `scope='gm'`
until a person confirms it in the review screen, which is the same decision a
page material already needs.
"""
import json
import re
from collections import Counter

#: Below this share of its page a picture is an ornament: an initial letter, a
#: rule, a small emblem. It cannot be a map or a handout.
MIN_AREA_SHARE = 0.15
#: Both sides in PDF points; 200 pt is about 7 cm at print size.
MIN_SIDE = 200.0
#: A picture the size of its page is the page: a page-sized background sits under
#: the text of a scanned book, and a full-page plate is already offered as the
#: page material. Either way it is not a second, separate asset.
BACKGROUND_SHARE = 0.9
#: How far from the picture its printed caption may sit, in PDF points, and how
#: long that caption may be. A caption is a line, not a paragraph.
CAPTION_GAP = 40.0
CAPTION_MAX_CHARS = 140
CAPTION_LINES = 6
#: Two pictures on the same rectangle are one picture and its soft mask.
SAME_PICTURE = 0.9
#: Paths thinner than this on a side are page furniture (rules, table lines,
#: underlines); they would stretch a drawing's box to the page margins.
MIN_PATH_SIDE = 2.0
#: A drawing is a picture only when the printed text does not run through it. A
#: framed column of words is a text box drawn as paths, not a map: the column of
#: a book covers about a quarter of its frame, a drawing's own labels far less.
MAX_TEXT_COVER = 0.2
#: A picture that covers this much of its page, on a page that prints no prose
#: beside it, is the page's own content: a map printed edge to edge, the way a
#: scanned module prints one. A plate of art is printed smaller — the maps of the
#: module this was measured on run 0.81–0.84 of the page, its title plate 0.59 —
#: so the share is what tells a map from a plate when the book printed no word.
MAP_PAGE_SHARE = 0.75
#: A map prints keys for itself: room numbers down the column, a scale of feet
#: per square. A page of prose about a dungeon prints the same numbers, so they
#: only count on a page that prints no prose at all.
ROOM_KEY = re.compile(r'^\s*\d{1,2}[.)]\s+\S', re.M)
SCALE_KEY = re.compile(r'масштаб|\bscale\b|клетк\w*', re.I)
#: The name a picture gets when the page printed none of its own: its kind and
#: the page it was cut from, so review is a list of named cards rather than empty
#: ones. It is a service name, not an invention — a person renames the picture.
SERVICE_NAMES = {'map': 'Карта', 'handout': 'Раздатка', 'illustration': 'Иллюстрация'}

#: A caption names the kind, so it must start with the word. A sentence that
#: merely mentions «план культистов» is prose, not the name of a picture.
MAP_CAPTION = re.compile(
    r'^\W*(?:карт[аеуоы]\w*|плана|планы|плану|планом|плане|план\b|схем[аеуы]\w*'
    r'|map\w*|plans?\b|schematic\w*)\b', re.I)
HANDOUT_CAPTION = re.compile(
    r'^\W*(?:раздайте|выдайте|покажите|раздаточн\w*|письмо|письма|письмом|письме'
    r'|записка|листовка|handout|give|show this|letter)\b', re.I)
#: A line of a table of contents is not a caption, even when it is printed next
#: to a picture: its dot leaders run to a page number.
CONTENTS_LINE = re.compile(r'\.{4,}')

#: The word for a map inside a printed line beside the picture («обозначения на
#: карте показывают…»). It is narrower on purpose than the caption test above: a
#: sentence about a «план культистов» is prose, a sentence about a «карта» names
#: the picture the sentence sits beside.
MENTIONS_MAP = re.compile(r'(\bкарт[аеуыо]\w*|\bmap\b)', re.I)

#: A module prints «Приложение B. Карты» once and then several uncaptioned map
#: pages; the heading is the book's own name for what follows.
SECTION_HEADING = re.compile(r'^\W*(?:приложение|appendix)\s+[a-zа-я0-9]', re.I)
KINDS = ('map', 'handout', 'illustration')

#: A printed line belongs to a box when at least this share of it lies inside; a
#: line that merely touches the edge is the neighbour's text, not the box's.
LINE_INSIDE = 0.5
#: A name is a printed line, not the prose printed beside the picture: a whole
#: short line that starts with a capital and could stand alone as a title.
NAME_MAX_CHARS = 80
#: A name does not end in a full stop (that is a sentence cut out of the page) and
#: does not end in a word that cannot end a name (a line cut by the column).
NOT_LAST = frozenset((
    'в', 'во', 'на', 'с', 'со', 'по', 'для', 'из', 'от', 'до', 'за', 'при', 'о', 'об', 'обо',
    'под', 'над', 'без', 'к', 'ко', 'у', 'и', 'а', 'но', 'или', 'же', 'ли', 'то', 'не', 'что',
    'как', 'чтобы', 'если', 'чем', 'это', 'пока',
    'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by', 'and', 'or',
    'not', 'that', 'this', 'as', 'while',
))
#: A chapter or an appendix heading names the section, not the picture below it.
SECTION_TITLE = re.compile(
    r'^\W*(?:эпизод\w*|глава\w*|часть\w*|книга\w*|приложение|дополнение'
    r'|appendix|chapter|part|episode|book)\b', re.I)
#: A caption that numbers the picture («Карта 3. Лагерь», «Map 2-1: …») is a
#: name even when it carries a full stop inside.
CAPTION_NUMBER = re.compile(
    r'^\W*(?:карт[аеуоы]\w*|план\w*|схем\w*|map\w*|plans?\b|schematic\w*)\s*[№#]?\s*\d', re.I)
#: Two pieces of one printed line sit closer than any column gutter: a wider gap
#: starts the next column even when both pieces share a baseline. A printed word
#: space is 3 pt, and a book leaves 20 pt or more between columns.
LINE_GAP = 12.0
#: Two pieces are one printed line when their heights overlap by this share of
#: the shorter one.
SAME_LINE = 0.5
#: Lines whose left edges differ by this much start at the same margin, i.e. in
#: the same column; a printed first-line indent stays inside the column.
COLUMN_SLACK = 24.0
#: A line starts a paragraph when an empty gap this many line heights follows it,
#: or when it is indented against the column's edge by more than INDENT points.
PARAGRAPH_GAP = 0.6
INDENT = 4.0
#: The soft break the extractor leaves where a word was hyphenated at a line end.
SOFT_BREAK = '\ufffe'


def _box(bounds):
    """(left, bottom, right, top) in PDF points, or None when unusable."""
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
        return None
    try:
        left, bottom, right, top = (float(value) for value in bounds[:4])
    except (TypeError, ValueError):
        return None
    if left != left or bottom != bottom or right != right or top != top:
        return None
    if right < left:
        left, right = right, left
    if top < bottom:
        bottom, top = top, bottom
    if right - left <= 0 or top - bottom <= 0:
        return None
    return left, bottom, right, top


def _same(first, second):
    """Whether two rectangles describe the same picture (mask over image)."""
    left, bottom, right, top = _box(first)
    other_left, other_bottom, other_right, other_top = _box(second)
    width, height = right - left, top - bottom
    overlap = (min(right, other_right) - max(left, other_left)) * (
        min(top, other_top) - max(bottom, other_bottom))
    if overlap <= 0:
        return False
    union = width * height + (other_right - other_left) * (other_top - other_bottom) - overlap
    return overlap / union >= SAME_PICTURE


def _clean(value):
    """One printed line as a person reads it: a soft break is a broken word.

    The extractor leaves `U+FFFE` where a word was hyphenated at a line end and
    the two halves are printed one after the other; dropping the mark glues the
    word back together instead of showing «располо￾жение».
    """
    value = re.sub(SOFT_BREAK + r'\s*', '', str(value or ''))
    value = value.replace('\u00ad', '')
    return ' '.join(value.split()).strip()


def _key(value):
    """The same printed line compared between pages: case and spaces ignored."""
    return ' '.join(_clean(value).split()).lower()


def _name(value, body=False):
    """The picture's printed name: one whole short line, or '' when it has none.

    A caption is a line, not a paragraph: a fragment of prose beside a picture
    (its beginning cut off by the column, its end cut by the page) is not a name.
    `body` marks one line of a paragraph: a line of prose is never a name.
    """
    raw = str(value or '').strip()
    if not raw or raw.endswith(SOFT_BREAK) or body:
        return ''
    words = _clean(raw)
    if not words or len(words) > NAME_MAX_CHARS:
        return ''
    if SECTION_HEADING.match(words) or SECTION_TITLE.match(words):
        return ''
    numbered = CAPTION_NUMBER.match(words)
    if CONTENTS_LINE.search(words) or '•' in words or ('. ' in words and not numbered):
        return ''
    if words.endswith(('.', ',', ';', ':', '…', '...', '-')):
        return ''
    stripped = words.lstrip('«»"„“„\'`([-—– ')
    if not stripped or not stripped[0].isalpha() or not stripped[0].isupper():
        return ''
    last = re.findall(r'[0-9A-Za-zА-Яа-яЁё]+', stripped)
    if not last or last[-1].lower() in NOT_LAST:
        return ''
    if len(re.findall(r'[A-Za-zА-Яа-яЁё]', stripped)) < 3:
        return ''
    return words


def repeated_lines(texts):
    """Lines a book prints on more than one page: running heads, not names.

    A chapter's head («Эпизод 1: Гринест в огне») is printed above every page of
    the chapter and sits as close to a picture as its own caption would. It names
    the chapter, so a line that repeats elsewhere in the same book never becomes
    the name of a picture.
    """
    seen = Counter()
    for value in texts:
        # A line printed twice on one page (a caption above and the same caption
        # in the margin) is still one page's line; a running head is the one that
        # the pages of a chapter share.
        printed = set()
        for line in (value or '').splitlines():
            key = _key(line)
            if not key:
                continue
            printed.add(key)
            # A head and its folio can come out of the extractor as one string
            # («Эпизод 1: Гринест в огне 41»), while the layout splits them; the
            # head is the same line either way.
            bare = re.sub(r'\s+\d{1,4}$', '', key)
            if bare and bare != key:
                printed.add(bare)
        for key in printed:
            seen[key] += 1
    return {line for line, count in seen.items() if count >= 2}


def _caption(layout, text, box):
    """Printed lines that sit just beside the picture, below or above it.

    A line printed *on* the picture (a room number, a label inside a map) is not
    a caption and must not name the kind.
    """
    left, bottom, right, top = box
    lines = []
    for region in layout.get('regions') or []:
        start, end = region.get('start'), region.get('end')
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        value = ' '.join((text[start:end] or '').split()).strip()
        if not value or len(value) > CAPTION_MAX_CHARS or CONTENTS_LINE.search(value):
            continue
        for rect in region.get('bounds') or []:
            found = _box(rect)
            if not found:
                continue
            region_left, region_bottom, region_right, region_top = found
            horizontal = min(right, region_right) - max(left, region_left)
            if horizontal < 0.5 * min(right - left, region_right - region_left):
                continue
            if region_top <= bottom:
                gap = bottom - region_top
            elif region_bottom >= top:
                gap = region_bottom - top
            else:
                continue
            if gap > CAPTION_GAP:
                continue
            lines.append(value)
            break
    return list(dict.fromkeys(lines))[:CAPTION_LINES]


def _printed_lines(layout, text):
    """Every printed line of the page, its pieces put back together.

    The layout hands back text runs, not lines: one run can be a whole paragraph
    and another a bold phrase inside it. Gathering the pieces by their geometry
    gives the lines a person reads, which is what a caption is. Pieces are
    gathered by the middle of their height, because a caption mixes sizes on one
    baseline («Карта» set larger than its title) and sorting by the top would
    interleave them.
    """
    pieces = []
    for region in layout.get('regions') or []:
        start, end = region.get('start'), region.get('end')
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        value = (text[start:end] or '').strip()
        if not value:
            continue
        rects = [found for found in (_box(raw) for raw in region.get('bounds') or []) if found]
        if rects:
            pieces.extend(_split_over(value, rects))
    rows = []
    for rect, value in sorted(pieces, key=lambda item: (-(item[0][1] + item[0][3]) / 2, item[0][0])):
        left, bottom, right, top = rect
        middle = (bottom + top) / 2
        for row in rows:
            shorter = min(top - bottom, row['top'] - row['bottom'])
            if shorter <= 0 or abs(middle - (row['bottom'] + row['top']) / 2) > 0.5 * shorter:
                continue
            gap = max(left - row['right'], row['left'] - right, 0.0)
            if gap > LINE_GAP:
                continue
            row['pieces'].append((rect, value))
            row['left'] = min(row['left'], left)
            row['right'] = max(row['right'], right)
            row['top'] = max(row['top'], top)
            row['bottom'] = min(row['bottom'], bottom)
            break
        else:
            rows.append(dict(left=left, right=right, top=top, bottom=bottom, pieces=[(rect, value)]))
    # Pieces of one line can be met out of order when their heights differ (a
    # word set larger sorts above its neighbours), so rows on one baseline are
    # joined in a second pass instead of trusting the order they were built in.
    merged = True
    while merged:
        merged = False
        for first in range(len(rows)):
            for second in range(first + 1, len(rows)):
                one, other = rows[first], rows[second]
                shorter = min(one['top'] - one['bottom'], other['top'] - other['bottom'])
                if shorter <= 0 or abs((one['bottom'] + one['top']) / 2 - (other['bottom'] + other['top']) / 2) > 0.5 * shorter:
                    continue
                gap = max(one['left'] - other['right'], other['left'] - one['right'], 0.0)
                if gap > LINE_GAP:
                    continue
                one['pieces'].extend(other['pieces'])
                one['left'] = min(one['left'], other['left'])
                one['right'] = max(one['right'], other['right'])
                one['top'] = max(one['top'], other['top'])
                one['bottom'] = min(one['bottom'], other['bottom'])
                del rows[second]
                merged = True
                break
            if merged:
                break
    return rows


def _prose_lines(lines):
    """The printed lines of a page that are one line of a paragraph.

    A title or a caption is set apart: its size differs from the body text, or
    the space around it is wider than the leading of a paragraph. A line of the
    body has a neighbour of the same size at ordinary leading, which is what
    makes it prose — and what a fragment cut out of the page can never be named.
    """
    heights = sorted(line['top'] - line['bottom'] for line in lines if line['top'] > line['bottom'])
    if not heights:
        return set()
    body_height = heights[len(heights) // 2]
    prose = set()
    for index, line in enumerate(lines):
        height = line['top'] - line['bottom']
        if abs(height - body_height) > 0.15 * body_height:
            continue
        for other_index, other in enumerate(lines):
            if index == other_index:
                continue
            other_height = other['top'] - other['bottom']
            if abs(other_height - body_height) > 0.15 * body_height:
                continue
            if min(line['right'], other['right']) - max(line['left'], other['left']) <= 0:
                continue
            gap = max(other['bottom'] - line['top'], line['bottom'] - other['top'], 0.0)
            if gap <= 1.45 * max(height, other_height):
                prose.add(index)
                break
    return prose


def _line_text(line):
    return ' '.join(value for _, value in sorted(line['pieces'], key=lambda item: item[0][0])).strip()


def _continued(lines, index, prose=frozenset()):
    """A heading that the book broke over two lines is read whole.

    («Что будет» / «дальше?»): the next line continues it when it starts with a
    lower-case word and is set apart from the body. Nothing is joined on a guess:
    the second line must be printed at ordinary leading in the same type.
    """
    value = _line_text(lines[index])
    line = lines[index]
    for _ in range(2):
        height = line['top'] - line['bottom']
        below = None
        for other_index, other in enumerate(lines):
            if other_index == index or other_index in prose:
                continue
            other_height = other['top'] - other['bottom']
            if abs(other_height - height) > 0.15 * max(height, other_height):
                continue
            if other['top'] > line['bottom'] + 1:
                continue
            gap = line['bottom'] - other['top']
            if gap > 1.45 * max(height, other_height):
                continue
            if min(line['right'], other['right']) - max(line['left'], other['left']) <= 0:
                continue
            tail = _line_text(other)
            if not tail or not tail[:1].islower():
                continue
            if below is None or gap < below[0]:
                below = (gap, other_index)
        if below is None:
            break
        tail = _line_text(lines[below[1]])
        if len(value) + len(tail) + 1 > NAME_MAX_CHARS:
            break
        value = value + ' ' + tail
        line = lines[below[1]]
        index = below[1]
    return value


def _title(layout, text, lines, box, repeated=(), prose=frozenset()):
    """The printed name of the picture, or '' when the book prints none.

    Chosen in this order, so the most deliberate line wins over a neighbour's
    prose: a caption that names a kind («Карта 2-1: …», «Map: …», «Покажите
    игрокам…») taken as the book printed it, a whole text run of its own; the
    nearest whole title line just above or below the box; the first whole label
    printed inside the box (a map's own title drawn with it). A line that only
    looks like a title is still refused by `_name`.

    The nearest line is the picture's own line: when it is a section heading or
    the running head of the chapter, the picture has no name and no further line
    is borrowed for it — naming a picture by the heading above it is exactly the
    misnaming this rule exists to stop.
    """
    # A caption that names the kind is read from its own text run: a title set in
    # two sizes on one baseline («Карта» larger than «2-1: Логово») falls apart
    # into pieces when the line is rebuilt from rectangles.
    printed_lines = {_key(_line_text(lines[index])) for index in prose}
    for value in _caption(layout, text, box):
        cleaned = _clean(value)
        if MAP_CAPTION.search(cleaned) or HANDOUT_CAPTION.search(cleaned):
            named = _name(cleaned)
            # A sentence of the body can begin with the word for a map («Карте
            # лагеря не хватает…»); only a numbered caption is a caption in the
            # body's own type.
            # A line that starts with the word for a kind is a caption by its
            # shape: it is not a running head even when the book prints the same
            # caption twice (a map and the list of maps), so the repeat filter
            # does not apply to it.
            if named and (CAPTION_NUMBER.match(named) or _key(named) not in printed_lines):
                return named
    left, bottom, right, top = box
    near, covered = [], []
    for index, line in enumerate(lines):
        value = _line_text(line)
        if not value:
            continue
        span = min(right - left, line['right'] - line['left'])
        horizontal = min(right, line['right']) - max(left, line['left'])
        if span <= 0 or horizontal < 0.5 * span:
            continue
        body = index in prose
        if line['top'] <= bottom:
            near.append((bottom - line['top'], index, body))
        elif line['bottom'] >= top:
            near.append((line['bottom'] - top, index, body))
        elif line['bottom'] >= bottom and line['top'] <= top:
            covered.append((top - line['top'], index, body))
    for gap, index, body in sorted((item for item in near if item[0] <= CAPTION_GAP),
                                   key=lambda item: item[0]):
        head = _clean(_line_text(lines[index]))
        if SECTION_HEADING.match(head) or SECTION_TITLE.match(head) or _key(head) in repeated:
            # The nearest line is the chapter's head, not the picture's: the name
            # is not taken from the lines below it either — a title deeper in the
            # page belongs to its own picture.
            break
        if body:
            continue
        named = _name(_continued(lines, index, prose))
        if named:
            return named
    # A label printed inside the picture's own box (a map's title, a creature's
    # name) belongs to the picture even when a running head sits above it.
    for gap, index, body in sorted(covered, key=lambda item: item[0]):
        if body:
            continue
        head = _clean(_line_text(lines[index]))
        if SECTION_HEADING.match(head) or SECTION_TITLE.match(head) or _key(head) in repeated:
            continue
        named = _name(_continued(lines, index, prose))
        if named:
            return named
    return ''


def section_kind(text):
    """The kind an appendix announces for the pictures that follow it.

    Returns `None` when the page prints no heading, `''` when it prints one that
    names neither maps nor handouts (music, monsters): a new section must clear
    the previous one instead of guessing.
    """
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not SECTION_HEADING.match(line):
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ''
        if MAP_CAPTION.search(following):
            return 'map'
        if HANDOUT_CAPTION.search(following):
            return 'handout'
        return ''
    return None


def service_name(kind, page=0):
    """The name a picture gets when the page printed none of its own.

    «Карта · стр. 87» says what the picture was taken for and where it stands, so
    a person reads the review as a list of pictures and not of empty cards. It
    names the kind only — the printed name, when there is one, always wins.
    """
    label = SERVICE_NAMES.get(kind, 'Материал')
    try:
        number = int(page or 0)
    except (TypeError, ValueError):
        number = 0
    return '%s · стр. %s' % (label, number) if number > 0 else label


def _page_number(layout, region=''):
    """The number of the page a layout belongs to, or 0 when it does not say.

    The layout names its page in the ids it hands out («p87.i2», «p87.render»),
    which is the page of the book as the reader sees it.
    """
    for value in (region, layout.get('render_ref') or ''):
        match = re.match(r'p(\d+)', str(value or ''))
        if match:
            return int(match.group(1))
    return 0


def map_evidence(layout, text, box, page_area=None, lines=None, prose=None):
    """Why a picture of a page reads as a map: its geometry, not a printed word.

    A scanned module prints its maps as one picture that is the page: it covers
    most of the page, and the page sets no paragraph anywhere — what text there
    is is a running head, a folio, a room number drawn inside the picture. A
    picture set into a page of prose is an illustration of that prose, however
    large, and a plate of art is printed smaller than a map (see
    `MAP_PAGE_SHARE`).

    Returns the signals that made the picture a map, so the decision is written
    into its caption and a person can dispute it; None when they do not hold.
    """
    found = _box(box)
    if not found:
        return None
    try:
        width, height = float(layout.get('width') or 0), float(layout.get('height') or 0)
    except (TypeError, ValueError):
        return None
    page_area = page_area or width * height
    if page_area <= 0:
        return None
    left, bottom, right, top = found
    share = (right - left) * (top - bottom) / page_area
    if share < MIN_AREA_SHARE:
        return None
    cover = _text_cover(layout, text, found)
    if cover > MAX_TEXT_COVER:
        return None
    if lines is None:
        lines = _printed_lines(layout, text or '')
    if prose is None:
        prose = _prose_lines(lines)
    # A page that sets a paragraph is a text page, and its picture illustrates
    # that paragraph: prose is what the body of a book is, and a map page has
    # none. The prose counts wherever it is printed — under the picture as an
    # invisible layer of a scan as well as beside it. Only the keys a map prints
    # for itself are not prose, even set at the leading of the body.
    for index in sorted(prose):
        value = _line_text(lines[index])
        if ROOM_KEY.match(value) or SCALE_KEY.search(value):
            continue
        return None
    rooms = len(ROOM_KEY.findall(text or ''))
    scale = bool(SCALE_KEY.search(text or ''))
    if share < MAP_PAGE_SHARE and not (rooms or scale):
        return None
    signals = ['вся страница' if share >= MAP_PAGE_SHARE else 'страница без прозы']
    signals.append('прозы вокруг нет')
    if rooms:
        signals.append('номера комнат: %d' % rooms)
    if scale:
        signals.append('масштаб/сетка')
    return dict(share=round(share, 4), cover=round(cover, 4), rooms=rooms, scale=scale,
                signals=tuple(signals), because=', '.join(signals))


def kind(caption_lines, page_kinds=(), section=None, evidence=None):
    """The kind a picture is offered as: what its caption says, then its page.

    A page-level hint (`hints` of the layout, e.g. a printed «Раздаточный
    материал» heading) names the picture only when the page holds exactly one
    candidate, so a caption of one picture never relabels its neighbour. A
    picture whose page reads as a map by its geometry (`map_evidence`: the
    picture is the page, no prose is set beside it) is a map even when the page
    prints no word for one; the book's own headings still come first.
    """
    printed = _clean(' '.join(caption_lines))
    if MAP_CAPTION.search(printed) or MENTIONS_MAP.search(printed):
        return 'map'
    # A map page often prints «Покажите игрокам карту 7.1» in the margin; that
    # instruction names a map, so the map test comes first.
    if HANDOUT_CAPTION.search(printed):
        return 'handout'
    if 'map' in page_kinds:
        return 'map'
    if 'handout' in page_kinds:
        return 'handout'
    if evidence:
        return 'map'
    if section in KINDS:
        return section
    return 'illustration'


def _text_cover(layout, text, box):
    """Share of a rectangle that printed text covers.

    A drawing is drawn; a framed column of words is typed. The lines are known
    from the layout, so their rectangles say which of the two the page holds.
    """
    left, bottom, right, top = box
    covered = 0.0
    for region in layout.get('regions') or []:
        start, end = region.get('start'), region.get('end')
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not (text[start:end] or '').strip():
            continue
        for rect in region.get('bounds') or []:
            found = _box(rect)
            if not found:
                continue
            region_left, region_bottom, region_right, region_top = found
            width = min(right, region_right) - max(left, region_left)
            height = min(top, region_top) - max(bottom, region_bottom)
            if width > 0 and height > 0:
                covered += width * height
    return min(1.0, covered / ((right - left) * (top - bottom)))


def _vector_box(layout, page_width, page_height):
    """The rectangle the page's vector drawing occupies, in PDF points.

    A drawing is many path objects (curves, strokes, filled areas drawn one by
    one), so the union of their boxes is the drawing's own box. Hairlines are
    dropped first: a rule under a heading is not part of the picture and would
    stretch the box to the margins. The union is clipped to the page.
    """
    boxes = []
    for graphic in layout.get('vectors') or []:
        box = _box((graphic or {}).get('bounds'))
        if not box:
            continue
        if box[2] - box[0] < MIN_PATH_SIDE or box[3] - box[1] < MIN_PATH_SIDE:
            continue
        boxes.append(box)
    if not boxes:
        return None
    left = max(0.0, min(box[0] for box in boxes))
    bottom = max(0.0, min(box[1] for box in boxes))
    right = min(page_width, max(box[2] for box in boxes))
    top = min(page_height, max(box[3] for box in boxes))
    if right - left <= 0 or top - bottom <= 0:
        return None
    return left, bottom, right, top


def vector_candidate(layout, text, page_area, repeated=()):
    """A map drawn as vector curves on a page that prints a word for a map.

    A book that is typeset rather than scanned prints its drawings as paths:
    there is no picture object to find, so the page yields no image candidate and
    the map would be lost. The page must print «карта»/«план»/«map» itself — a
    drawing is not called a map by guesswork — and the drawing must look like a
    picture: big enough to be one, not the page itself, and not a frame around a
    column of text.
    """
    printed = text or ''
    if not (MAP_CAPTION.search(printed) or MENTIONS_MAP.search(printed)):
        return None
    try:
        width, height = float(layout.get('width') or 0), float(layout.get('height') or 0)
    except (TypeError, ValueError):
        return None
    page_area = page_area or width * height
    if page_area <= 0:
        return None
    box = _vector_box(layout, width, height)
    if not box:
        return None
    left, bottom, right, top = box
    share = (right - left) * (top - bottom) / page_area
    if right - left < MIN_SIDE or top - bottom < MIN_SIDE or share < MIN_AREA_SHARE:
        return None
    if share >= BACKGROUND_SHARE:
        return None
    if _text_cover(layout, printed, box) > MAX_TEXT_COVER:
        return None
    # The same rule as for a picture: the drawing is named by a printed line of
    # its own, and a page of prose that merely mentions a map leaves it unnamed
    # (the kind gives it a name a person can still use).
    lines = _printed_lines(layout, printed)
    name = _title(layout, printed, lines, box, repeated, _prose_lines(lines))
    if not name:
        # A drawing often covers the whole page, so its caption is printed far
        # from the drawing's box; it is still the page's own name for the map.
        for line in lines:
            cleaned = _clean(_line_text(line))
            if MAP_CAPTION.search(cleaned):
                named = _name(cleaned)
                if named:
                    name = named
                    break
    page = str((layout.get('vectors') or [{}])[0].get('id') or 'v').split('.')[0]
    printed = (name or service_name('map', _page_number(layout, page + '.v1')))[:240]
    return dict(region=page + '.v1',
                bounds=[round(value, 2) for value in box],
                area_share=round(share, 4),
                caption=printed,
                public_name=printed[:120],
                kind='map')


def candidates(layout, text, section=None, repeated=(), page=None):
    """Pictures of one laid-out page that are worth offering, with their kind.

    Each entry carries the region id the layout gave the picture, the box it
    occupies in PDF points (the same box the review screen can cut), the share
    of the page it covers and the name the page printed for it, so a person can
    check the decision instead of trusting it. A map is recognised by its caption
    or, when the book printed no word for one, by the geometry of its page
    (`map_evidence`): a picture that is the page, with no prose set beside it.
    A picture whose page prints no name of its own is named by its kind and page
    («Карта · стр. 87»), and a map found by geometry carries the signals that
    made it one, so a person can dispute the decision in review and rename it.
    """
    text = text or ''
    try:
        width, height = float(layout.get('width') or 0), float(layout.get('height') or 0)
    except (TypeError, ValueError):
        return []
    page_area = width * height
    if page_area <= 0:
        return []
    found = []
    page_lines = page_prose = None
    for position, image in enumerate(layout.get('images') or []):
        box = _box((image or {}).get('bounds'))
        if not box:
            continue
        left, bottom, right, top = box
        share = (right - left) * (top - bottom) / page_area
        if right - left < MIN_SIDE or top - bottom < MIN_SIDE or share < MIN_AREA_SHARE:
            continue
        if share >= BACKGROUND_SHARE:
            continue
        if page_lines is None:
            page_lines = _printed_lines(layout, text)
            page_prose = _prose_lines(page_lines)
        found.append(dict(region=(image or {}).get('id') or 'i%d' % position,
                          bounds=[round(value, 2) for value in box],
                          area_share=round(share, 4),
                          lines=_caption(layout, text, box),
                          name=_title(layout, text, page_lines, box, repeated, page_prose)))
    unique = []
    for item in found:
        if any(_same(item['bounds'], other['bounds']) for other in unique):
            continue
        unique.append(item)
    if not unique:
        # A page can print its map as vector curves instead of a picture; only
        # when there is no picture at all, so a real cut-out is never replaced by
        # a drawing of the whole page.
        drawn = vector_candidate(layout, text, page_area, repeated)
        return [drawn] if drawn else []
    page_kinds = {hint.get('kind') for hint in layout.get('hints') or []}
    # A page-level hint names the picture only on a page that holds exactly one.
    # A printed table of contents names maps on every line and holds one picture,
    # so its dot leaders must not turn that picture into a map.
    contents = sum(1 for line in text.splitlines() if CONTENTS_LINE.search(line))
    if len(unique) != 1 or contents >= 3:
        page_kinds = set()
    for item in unique:
        evidence = map_evidence(layout, text, item['bounds'], page_area, page_lines, page_prose)
        item['kind'] = kind(item['lines'], page_kinds, section, evidence)
        # The name is what the page printed for the picture; when it printed
        # none, the picture is still named by its kind and page instead of left
        # as an empty card, and a map found by geometry says which signals made
        # it one (r161).
        name = item.pop('name')
        item['public_name'] = (name or service_name(
            item['kind'], page or _page_number(layout, item['region'])))[:120]
        caption = item['public_name']
        # The signals that made a picture a map belong to the review screen, not
        # to the name a player is told when the material is not shown yet.
        if evidence and not name:
            caption = '%s (%s)' % (caption, evidence['because'])
        item['caption'] = caption[:240]
        item.pop('lines', None)
    return unique


def render_name(document, page, region, width, suffix):
    """The one file name a material is rendered to; the region is part of it.

    Two materials of the same page (the page itself and a picture cut out of
    it) must not share a preview file.
    """
    clean = re.sub(r'[^0-9a-z.]', '', str(region or ''))
    return str(document) + '-p' + str(page) + ('-r' + clean if clean else '') + '-w' + str(width) + suffix


def crop_box(material, page_width, page_height):
    """The picture's rectangle clipped to its page, or None for a whole page.

    Bounds in a book file can stick out of the page by a few points (a picture
    bleeding to the edge), so they are clipped rather than trusted as printed.
    """
    raw = material.get('bounds') if isinstance(material, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    box = _box(raw)
    if not box or page_width <= 0 or page_height <= 0:
        return None
    left = max(0.0, min(box[0], page_width))
    bottom = max(0.0, min(box[1], page_height))
    right = max(0.0, min(box[2], page_width))
    top = max(0.0, min(box[3], page_height))
    if right - left < 8 or top - bottom < 8:
        return None
    return left, bottom, right, top


def _overlap(first, second):
    """Width and height two rectangles share, both never negative."""
    return (max(0.0, min(first[2], second[2]) - max(first[0], second[0])),
            max(0.0, min(first[3], second[3]) - max(first[1], second[1])))


def _split_over(value, rects):
    """One extracted line's text spread over the rectangles it was printed in.

    A line of a two-column page can come out of the extractor as one string with
    two rectangles, one per column («left … right …»): the columns are glued
    together at the point where the extractor stopped a line, and the words of
    the right column sit in the middle of the left one. Each rectangle says where
    its words were printed, so the string is cut by the share of the line's width
    every rectangle covers, at a word boundary, so no word is torn in half.
    """
    words = value.split()
    if not words:
        return []
    if len(rects) == 1:
        return [(rects[0], value)]
    widths = [max(rect[2] - rect[0], 0.01) for rect in rects]
    total = sum(widths)
    # Positions are counted in characters of the line as printed, spaces between
    # the words included: that is what a rectangle's width stands for.
    length = sum(len(word) for word in words) + len(words) - 1
    starts, position = [], 0
    for word in words:
        starts.append(position)
        position += len(word) + 1
    groups, walked, first = [[] for _ in rects], 0.0, 0
    for index, width in enumerate(widths):
        walked += width
        target = length * walked / total
        cut = first
        while cut < len(words) and starts[cut] < target:
            cut += 1
        # A rectangle's share of the line is known only to a fraction of a
        # character, so the cut is put on the word boundary nearest to it.
        if first < cut < len(words) and starts[cut] - target > target - starts[cut - 1]:
            cut -= 1
        groups[index].extend(words[first:cut])
        first = cut
    return [(rect, ' '.join(group)) for rect, group in zip(rects, groups) if group]


def _printed_pieces(layout, text, box):
    """The printed pieces inside a box, and how many lines were left outside.

    Every rectangle of a laid-out line is kept or dropped on its own: a line of a
    two-column page can straddle the box, and its left half belongs to the box
    while its right half is the neighbour's text.
    """
    kept = dropped = 0
    pieces = []
    for region in layout.get('regions') or []:
        start, end = region.get('start'), region.get('end')
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        value = (text[start:end] or '').strip()
        if not value:
            continue
        rects = [found for found in (_box(raw) for raw in region.get('bounds') or []) if found]
        if not rects:
            dropped += 1
            continue
        for rect, piece in _split_over(value, rects):
            if _inside_with(rect, box):
                pieces.append((rect, piece))
                kept += 1
            else:
                dropped += 1
    return pieces, kept, dropped


def _inside_with(rect, box):
    """Whether a rectangle lies inside the box, by the share of its own area."""
    left, bottom, right, top = rect
    area = (right - left) * (top - bottom)
    width, height = _overlap(rect, box)
    return area > 0 and width * height / area >= LINE_INSIDE


def _same_column(left, column):
    """Whether a line starts at the column's margin: its left edge, within a
    printed indent. A running head printed across both columns starts at the left
    margin and belongs to the first column: were the column known by its width, one
    such line would make the two columns one and interleave their text.
    """
    return abs(left - column['left']) <= COLUMN_SLACK


def _lines(pieces):
    """Printed lines of the page, gathered from the pieces, top to bottom.

    A printed line can come back from the extractor cut into several pieces (a
    footnote mark, a soft hyphen, a gap in the text run), so the pieces are put
    back together by their geometry: the same height and no wide gap between
    them. Tops of pieces of one line differ by fractions of a point, so the sort
    rounds them — a piece must not jump over its neighbour on the same line.
    """
    lines = []
    for rect, value in sorted(pieces, key=lambda item: (-round(item[0][3]), item[0][0])):
        left, bottom, right, top = rect
        if lines:
            line = lines[-1]
            overlap = min(top, line['top']) - max(bottom, line['bottom'])
            shorter = min(top - bottom, line['top'] - line['bottom'])
            gap = max(left - line['right'], line['left'] - right, 0.0)
            if shorter > 0 and overlap >= SAME_LINE * shorter and gap <= LINE_GAP:
                line['pieces'].append((rect, value))
                line['left'] = min(line['left'], left)
                line['right'] = max(line['right'], right)
                line['top'] = max(line['top'], top)
                line['bottom'] = min(line['bottom'], bottom)
                continue
        lines.append(dict(left=left, right=right, top=top, bottom=bottom, pieces=[(rect, value)]))
    return lines


def _columns(lines):
    """Printed lines grouped by the column they belong to, left to right."""
    columns = []
    for line in lines:
        for column in columns:
            if _same_column(line['left'], column):
                column['left'] = min(column['left'], line['left'])
                column['right'] = max(column['right'], line['right'])
                column['lines'].append(line)
                break
        else:
            columns.append(dict(left=line['left'], right=line['right'], lines=[line]))
    # A line that starts far right («Второй Акт» beside the first) can open a
    # column of its own before the real column has grown to its full width;
    # columns that overlap are one column, however they were built.
    merged = True
    while merged:
        merged = False
        for first in range(len(columns)):
            for second in range(first + 1, len(columns)):
                if _same_column(columns[second]['left'], columns[first]):
                    columns[first]['left'] = min(columns[first]['left'], columns[second]['left'])
                    columns[first]['right'] = max(columns[first]['right'], columns[second]['right'])
                    columns[first]['lines'].extend(columns[second]['lines'])
                    del columns[second]
                    merged = True
                    break
            if merged:
                break
    return sorted(columns, key=lambda column: column['left'])


def _paragraph(lines):
    """Lines of one paragraph as flowing text, in the order they were printed.

    A word hyphenated at a printed line end comes back whole: the extractor's
    soft break is dropped and the next line is joined without a space.
    """
    value = ''
    for index, line in enumerate(lines):
        if not value:
            value = line['text']
            continue
        if lines[index - 1]['broken']:
            value = value.rstrip('-') + line['text']
        elif value.endswith('-') and line['text'][:1].islower():
            value = value[:-1] + line['text']
        else:
            value = value + ' ' + line['text']
    return value


def _reading_text(pieces):
    """Paragraphs of every column, read column by column, top to bottom."""
    lines = []
    for line in _lines(pieces):
        texts = [value for _, value in sorted(line['pieces'], key=lambda item: item[0][0])]
        joined = ' '.join(texts).strip()
        broken = joined.endswith(SOFT_BREAK)
        value = joined.replace(SOFT_BREAK, '').strip()
        if value:
            lines.append(dict(text=value, broken=broken, left=line['left'], right=line['right'],
                              top=line['top'], bottom=line['bottom']))
    paragraphs = []
    for column in _columns(lines):
        column_lines = column['lines']
        heights = sorted(line['top'] - line['bottom'] for line in column_lines)
        height = heights[len(heights) // 2] or 1.0
        # The column's margin is the left edge most of its lines share; a printed
        # title may stick out further left and must not turn the body indented.
        edges = Counter(round(line['left']) for line in column_lines)
        edge = min(edges, key=lambda left: (-edges[left], left))
        paragraph = []
        previous = None
        for line in column_lines:
            indented = line['left'] >= edge + INDENT
            initial = line['text'][:1]
            # An indented line opens a paragraph of a book; a line that continues
            # a sentence in the middle of a word was only pushed right by the
            # extractor's own fragmenting, and stays in the same paragraph.
            started = previous is not None and (
                previous['bottom'] - line['top'] > PARAGRAPH_GAP * height
                or (indented and not previous['indented'] and initial.isupper())
                or initial in '•·')
            if paragraph and (previous is None or started):
                paragraphs.append(_paragraph(paragraph))
                paragraph = []
            paragraph.append(line)
            previous = dict(bottom=line['bottom'], indented=indented)
        if paragraph:
            paragraphs.append(_paragraph(paragraph))
    return [value for value in paragraphs if value]


def text_in_box(layout, text, bounds):
    """Printed text inside a printed box, in the order a person reads it.

    A handout or a map is printed in its own box on the page. The lines of the
    page that lie in that box are the material's own text; the rest of the page
    belongs to its neighbours and must not come with it. Lines are gathered into
    columns first and paragraphs second, so a two-column page is never read
    across the gutter and a line of the right column never lands in the middle of
    a line of the left one.

    Returns the text, how many printed pieces fell inside the box and how many
    were left outside — the counts are evidence for the caller, not a guess. A box
    that cannot be read, or a layout that is not a layout, raises ValueError, so a
    caller can tell "the book has no text here" from "the box is broken".
    """
    if isinstance(bounds, str):
        try:
            bounds = json.loads(bounds)
        except ValueError:
            raise ValueError('границы материала не читаются')
    box = _box(bounds)
    if not box:
        raise ValueError('границы материала пусты или искажены')
    if not isinstance(layout, dict):
        raise ValueError('раскладка страницы отсутствует')
    pieces, kept, dropped = _printed_pieces(layout, text or '', box)
    return dict(text='\n\n'.join(_reading_text(pieces)), lines=kept, dropped=dropped)
