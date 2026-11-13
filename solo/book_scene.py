"""A chapter cut from a book becomes a scene: places, people and printed checks.

A book states its own structure far more often than people expect: a place is
introduced with its kind noun («Городок Гринест [Greenest]»), a person with
«по имени», and a check as an ability, an optional skill and a printed DC
(«проверку на Ловкость (Скрытность) Сл 10»). Reading those printed forms is
deterministic, so it costs nothing and gives the same answer twice; a model is
only needed where a page prints none of them, and this module never asks for
one.

Every recovered fact keeps the page and the sentence it was read from. The
sentence is validated against the page before it is stored, and an entity whose
name does not occur in its own quote is rejected instead of being written into
the world: a scene the book never printed is worse than a scene without it.

Reading order matters: a check or a person belongs to the place its sentence
names, then to the only place of the chapter, and only then to the chapter's
own scene. Which of the three happened is kept in `binding`, so a reader can
tell a printed link from a chapter-wide one.
"""
import json, re

from .preparation_contracts import SourceQuote
from .prepared_world import name_in_quote, normalized, source_span
from .source_checks import SKILL_LABELS

#: The extractor leaves layout marks inside words (U+FFFE soft breaks, U+00AD
#: shy hyphens, zero-width joiners). They are neither letters nor whitespace, so
#: a sentence copied from a page would fail to match its own text unless they are
#: removed first. Unlike the comparison normaliser this one keeps the printed
#: case: a place name and a common noun differ only in their first letter.
_BREAKS = '\ufffe\u200b\u200c\u200d\u2060'
_DROPS = '\u00ad\ufeff'
_MAX_SENTENCE = 3000
#: A running header repeats on every page of its chapter and carries the page
#: number under it. Joined into prose it becomes part of the first sentence and
#: makes an otherwise clean quote look like column bleed, so a line the book
#: prints as «Эпизод 3: Драконья кладка» or as a bare page number is dropped
#: before sentences are cut. Nothing else is dropped: every other line is the
#: book's own text.
_HEADER = re.compile(r'^(?:глава|эпизод|часть|приложение|введение|пролог|chapter|episode|part|appendix)\b', re.I)
_NUMBER = re.compile(r'^\d{1,4}$')

def clean(text):
    """The page as printed, without the extractor's layout marks."""
    kept = []
    for char in str(text or ''):
        if char in _BREAKS: kept.append(' ')
        elif char in _DROPS: continue
        elif char.isspace(): kept.append(' ')
        else: kept.append(char)
    return ' '.join(''.join(kept).split())

def body(text):
    """The page without its running header and page number."""
    lines = []
    for line in str(text or '').splitlines():
        bare = ' '.join(line.replace('\ufffe', ' ').replace('\u00ad', '').split())
        if not bare or _NUMBER.match(bare) or _HEADER.match(bare): continue
        lines.append(line)
    return '\n'.join(lines)

def sentences(text):
    """The printed sentences of a page, in order and in their printed case."""
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+', clean(body(text))) if len(part.strip()) >= 12]

def quotable(text):
    """The sentences a quote can be taken from whole.

    A quote is never cut: a truncated sentence is not on the page, so validating
    it against its own page would reject the fact even though the page prints
    it. A page-long run-on without a full stop is therefore skipped rather than
    shortened.
    """
    return [sentence for sentence in sentences(text) if len(sentence) <= _MAX_SENTENCE]

#: A printed ability is written in any case of the noun; only the stem is stable.
_ABILITIES = (('сил', 'strength'), ('ловкост', 'dexterity'), ('телослож', 'constitution'),
              ('интеллект', 'intelligence'), ('мудрост', 'wisdom'), ('харизм', 'charisma'))

#: The skill is printed in the case the sentence needs («Скрытность»,
#: «Скрытности», «Восприятия»). The stems are what stays the same, and the book
#: spells the wisdom skill «Восприятие» — the word 2024 prints as
#: «Внимательность», so both spellings map to one engine skill.
_SKILL_STEMS = (('ловкост\\s+рук', 'sleight_of_hand'), ('обращени\\w*\\s+с\\s+животн\\w*', 'animal_handling'),
                ('скрытн', 'stealth'), ('восприят', 'perception'), ('внимательн', 'perception'),
                ('убежд', 'persuasion'), ('запугив', 'intimidation'), ('обман', 'deception'),
                ('атлетик', 'athletics'), ('акробат', 'acrobatics'), ('маги', 'arcana'),
                ('истори', 'history'), ('проницат', 'insight'), ('расследов', 'investigation'),
                ('медицин', 'medicine'), ('природ', 'nature'), ('выступл', 'performance'),
                ('религи', 'religion'), ('выживан', 'survival'))
_DC = r'\bСл\s*(\d{1,2})\b'
_ABILITY = r'(?P<ability>' + '|'.join(stem for stem, _ in _ABILITIES) + r')[\w-]*'
_SKILL = r'(?P<skill>' + '|'.join(stem for stem, _ in _SKILL_STEMS) + r')[\w .-]*'

def _ability(value):
    low = str(value or '').casefold()
    for stem, full in _ABILITIES:
        if low.startswith(stem): return full
    return ''

def _skill(value):
    low = ' '.join(str(value or '').casefold().split())
    for stem, key in _SKILL_STEMS:
        if re.match(stem, low): return key
    return ''

def checks(page_text):
    """Printed ability/skill checks with their DC, one per printed sentence.

    The sentence is the unit on purpose: it carries the ability, the skill the
    book attached to it, the DC and the condition («если персонажи пытаются
    прокрасться ... каждый персонаж должен пройти проверку»), and all four are
    needed for the check to mean anything at the table.
    """
    found = []
    for sentence in quotable(page_text):
        dc = re.search(_DC, sentence, re.I)
        if not dc: continue
        # «Спасбросок Телосложения Сл 5 плюс урон, нанесённый атакой» prints its DC
        # as a formula: five is not the number to roll against, and storing it turns
        # a save into a certain miss. The printed sentence stays in the book, not in
        # the world, until a plain number is printed for it.
        if re.match(r'\s*(?:плюс|минус|умнож|делён|делен|за\s+кажд|[+×*])', sentence[dc.end():], re.I): continue
        money = re.search(r'\b\d+\s*зм\b|\bзм\b', sentence, re.I)
        if money and not re.search(r'проверк|спасброс|бросок', sentence, re.I): continue
        ability = ''
        skill = ''
        paired = re.search(_ABILITY + r'\s*\(?' + _SKILL + r'\)?', sentence, re.I)
        if paired:
            ability, skill = _ability(paired['ability']), _skill(paired['skill'])
        else:
            single = re.search(_ABILITY, sentence, re.I)
            if single: ability = _ability(single['ability'])
            else:
                only_skill = re.search(_SKILL, sentence, re.I)
                if only_skill: skill = _skill(only_skill['skill'])
        if not ability and not skill: continue
        value = int(dc[1])
        if not 1 <= value <= 40: continue
        found.append(dict(page=0, quote=sentence, ability=ability, skill=skill, dc=value,
                          saving_throw=bool(re.search(r'спасброс', sentence, re.I)),
                          trigger=sentence[:240]))
    return found

#: The noun a printed place name is built on. It is what tells a place from a
#: person: «Городок Гринест» is a town, «Монах Леосин» is not a monastery.
#: «Болото» and «Колодец» are here because both books name places with them
#: («Болото Мертвецов», «Колодец Драконов»): without the noun the name after it
#: has no head to attach to and the swamp is never read as a place at all.
_PLACE_TYPES = {'городок': 'town', 'город': 'city', 'деревня': 'village', 'село': 'village',
                'поселение': 'village', 'замок': 'castle', 'крепость': 'fort', 'таверна': 'inn',
                'харчевня': 'inn', 'пещера': 'cave', 'пещеры': 'cave', 'грот': 'cave',
                'каверна': 'cave', 'лагерь': 'camp', 'поместье': 'mansion', 'храм': 'temple',
                'башня': 'tower', 'рудник': 'mine', 'шахта': 'mine', 'порт': 'port',
                'мост': 'bridge', 'долина': 'valley', 'лес': 'forest', 'усадьба': 'estate',
                'руины': 'ruins', 'подземелье': 'dungeon', 'тракт': 'road',
                'болото': 'swamp', 'колодец': 'well'}

#: The mark a printed line break leaves in the text `places` reads. A page joins
#: its lines into sentences, and both halves of a name can end up on either side
#: of a break: «…расширяющимся Болотом / Мертвецов» is still one name, while
#: «Подземелье / Хотя культисты заняли…» is a heading and a new sentence. The
#: pattern is what tells them apart, so the break is kept while matching and
#: dropped again when the sentence becomes a quote.
_LINE = '\u00b6'

_PLACE = (r'(?<![\w-])(?P<kind>' + '|'.join(sorted(_PLACE_TYPES, key=len, reverse=True)) + r')s?'
          r'(?P<gap>[\s' + _LINE + r']+)(?P<first>[А-ЯЁ][\w\'’-]+)'
          # The second word of a name sits on the same printed line as the first:
          # «Мост Боарескир» followed by a running caption «Карта 9-2» is a bridge
          # and a caption, not a bridge called «Боарескир Карта».
          r'(?:\s+(?P<tail>[А-ЯЁ][\w\'’-]+))?'
          r'(?:\s*\[(?P<gloss>[A-Z][^\]\n]{1,40})\])?')


#: A capitalised word after a place noun is not yet a name: a sentence starts
#: with a capital too («Город расположен на холме»), and the book prints a
#: common noun the same way («лагерь Культа Дракона»). The book titles a place
#: by capitalising the noun as well, so that is required here, and the word
#: after it must not be one of the adverbs and pronouns a sentence opens with.
_NOT_A_NAME = {'когда', 'если', 'после', 'перед', 'через', 'пока', 'потому', 'затем', 'также',
               'теперь', 'здесь', 'внутри', 'вокруг', 'каждый', 'каждая', 'любой', 'такой',
               'это', 'эта', 'этот', 'тот', 'весь', 'все', 'некоторые', 'только', 'они', 'она',
               'он', 'там', 'потом', 'сейчас', 'наконец', 'вероятно', 'возможно', 'зачастую',
               # A crowd standing in a place is not the place: «Лагерь Культисты разбили
               # свой лагерь» is a sentence about cultists, not a camp called
               # «Культисты».
               'культисты', 'культист', 'персонажи', 'герои', 'налётчики', 'налетчики',
               'стражи', 'враги', 'солдаты', 'монстры', 'жители', 'пленники', 'торговцы',
               'охранники', 'слуги', 'кобольды', 'эльфы', 'гоблины', 'драконы'}

def marked(text):
    """The page's text with a mark where each printed line ends.

    `clean` joins the lines, which loses the only evidence that separates a name
    wrapped over two lines from a heading standing on its own. The mark is
    `_LINE`, and dropping it from a sentence gives exactly the sentence `clean`
    would have produced, so a quote is still the page's own words.
    """
    lines = []
    for line in body(text).splitlines():
        value = ' '.join(line.replace('\ufffe', ' ').replace('\u00ad', '').split())
        if value: lines.append(value)
    return _LINE.join(lines)

def marked_sentences(text):
    """The sentences of a page, with `_LINE` where one crosses a line break."""
    joined = marked(text)
    return [part.strip() for part in re.split(r'(?<=[.!?])[\s' + _LINE + r']+', joined)
            if len(part.strip()) >= 12]

def _heading(sentence, gap_start, kind):
    """Does the kind noun stand alone on its printed line, i.e. as a heading?

    «Подземелье / Хотя культисты заняли верхние уровни…» and «15. Мост / Крутая
    лестница» read as one phrase once the lines are joined, but the page prints
    them as a heading and a new sentence: the word after the break names the
    sentence that follows, not the heading above it.
    """
    start = sentence.rfind(_LINE, 0, gap_start) + 1
    line = re.sub(r'^[\s\d.)\-–—•]+', '', sentence[start:gap_start].rsplit(_LINE, 1)[-1]).strip(' :.-–—')
    return line.casefold() == kind.casefold()

def places(page_text):
    """Places the page names by their kind noun, with the printed spelling.

    «Городок Гринест [Greenest]» and «Замок Неритар» name a place; «храм
    Тиамат» names a temple and its owner, and «Город расположен на холме» names
    no place at all. The difference the page itself prints is capitalisation:
    the book capitalises the whole phrase it coined. A candidate therefore needs
    a capitalised kind noun and a capitalised name that is not a sentence
    opener, and it is kept only when the chapter repeats it or the translator
    glossed it — one capitalised word is not enough evidence for a name.

    `phrase` is the kind and the name as printed («Город Чудес»). Whether that
    phrase or the bare word is the place is decided by the chapter, in
    `standalone_words`, because one page cannot show which of the two the book
    prints on its own.
    """
    found = []
    for sentence in marked_sentences(page_text):
        for match in re.finditer(_PLACE, sentence, re.I):
            kind = match['kind']
            if not kind[:1].isupper(): continue
            first = ' '.join(match['first'].split())
            # `re.I` is needed for the kind noun, and it also makes the Cyrillic
            # class accept lowercase; the printed capital of the name is checked
            # here instead of being left to the pattern.
            if not first[:1].isupper() or first.casefold() in _NOT_A_NAME: continue
            if _LINE in (match['gap'] or '') and _heading(sentence, match.start('gap'), kind): continue
            tail = ' '.join((match['tail'] or '').split())
            # `re.I` also lets the name class accept a lowercase word; a word the
            # page prints in lower case continues the sentence («Болото Мертвецов
            # дикое и опасное место»), not the name.
            if tail and not tail[:1].isupper(): tail = ''
            found.append(dict(name=first, tail=tail,
                              kind=_PLACE_TYPES[kind.casefold()], phrase=kind + ' ' + first,
                              gloss=(match['gloss'] or '').strip(), quote=sentence.replace(_LINE, ' ')))
    return found

#: Case endings a name is not printed in when it is the name itself. A word
#: after a kind noun that ends like an oblique case is the dependent of that
#: noun: «Оявигатона» (a cave), «Драконов» (a well), «Ледяных» (a village) are
#: genitives of the name, and the place is what the kind noun says it is. A name
#: the book prints in the nominative («Гринест», «Неритар», «Нэтвотч») ends in a
#: consonant and keeps its own form.
_INFLECTED = re.compile(r'(?:[аяуюоеэиы]|[оеё]в|[еи]й|[ыи]х)$', re.I)

def standalone_words(pages):
    """Words the chapter prints where a name can stand on its own.

    A toponym the book uses by itself is a name: «Гринест» follows «через», «в
    Гринест» and opens a heading. A word it never prints except after another
    noun is a dependent: «Чудес» occurs only in «Город Чудес» and «Дом Чудес»,
    «Мертвецов» only after the cases of «Болото», «Драконов» after «Колодец».
    A kind noun in front of a dependent names nothing, so the place is the whole
    printed phrase and not the tail of it; in front of a name it is a
    description, and the place keeps the name the book uses by itself.

    Standing alone means: at the start of the text, after a preposition, or after
    punctuation. After any other lowercase word the capitalised word is governed
    by it («атакуют Гринест» is evidence only because the same chapter also
    prints «в Гринест»), and after another capitalised word it is the tail of
    that phrase.
    """
    standing = set()
    for text in pages.values():
        joined = clean(body(text))
        for match in re.finditer(r'(?<![\w])[А-ЯЁ][\w\'’\u2019-]+', joined):
            before = re.search(r"([\w'’\u2019-]+)\s*$", joined[:match.start()])
            if before and (before[1][:1].isupper() or before[1].casefold() not in _PREPOSITIONS): continue
            standing.add(normalized(match[0]))
    return standing


_PERSON = r'(?:по\s+имени|по\s+прозвищу|по\s+кличке|по\s+фамилии)\s+(?P<name>[А-ЯЁ][\w\'’-]+(?:\s+(?:[А-ЯЁ][\w\'’-]+|«[^»]{1,30}»))?)'

def people(page_text):
    """People the page introduces by name, with the sentence that does it.

    «Монах по имени Леосин Эрлантар [Leosin Erlanthar]» names one person; the
    same page also calls him «Леосин» and «монах», and neither is a second one.
    A gloss in brackets is the translator's, kept as an alias rather than as a
    second name.
    """
    found = []
    for sentence in quotable(page_text):
        for match in re.finditer(_PERSON, sentence, re.I):
            words = match['name'].split()
            # `re.I` is needed for the preposition, and it also lets the class
            # accept a lowercase word; «Варрам как Говорящего» is one name and a
            # conjunction, so the name stops at the first word the book did not
            # capitalise.
            kept = []
            for word in words:
                if kept and not (word[:1].isupper() or word.startswith('«')): break
                kept.append(word)
            name = ' '.join(kept).strip(' .,;:')
            if not name or len(name) > 80: continue
            gloss = ''
            after = sentence[match.end():match.end() + 80]
            bracket = re.match(r'\s*\[([A-Z][^\]]{1,60})\]', after)
            if bracket: gloss = bracket[1].strip()
            found.append(dict(name=name, gloss=gloss, quote=sentence))
    return found

#: A book states what a chapter is for in two printed shapes: what the party
#: should already be («для персонажей 3го уровня») and what it becomes by the end
#: («персонажи достигают 4го уровня»). Both are the book's own statement; a
#: number from a spell level, a tower floor or a monster's caster level is not,
#: which is why `level_bounds` is only asked about a sentence that first matched
#: one of these two shapes instead of about every sentence on the page.
_END_LEVEL = r'(?:достиг\w*|достижени\w*|получат|получают)\D{0,40}?(\d{1,2})\s*[-–—]?\s*(?:го|й|ый|ой)?\s*уров\w*'
_START_LEVEL = r'(?:для|рассчитан\w*\s+на|персонаж|парти|групп|геро)\w*\D{0,25}?(\d{1,2})\s*[-–—]?\s*(?:го|й|ый|ой)?\s*уров\w*'
_RANGE_LEVEL = r'(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*[-–—]?\s*(?:го|й|ый|ой)?\s*уров\w*'
_NOT_A_LEVEL = r'(заклинател|волшебник|свиток|ячейк|област|ступен|этаж|level)'
#: Требование книги — это предложение О приключении, а не любое предложение с
#: числом и словом «группа». Настоящая книга печатает рядом и «уничтожить
#: несколько групп 15 уровня», и «эффект заклинания на группу из 9 персонажей»:
#: без проверки контекста такие строки становились требованием к игроку.
#: Поэтому уровень засчитывается только в предложении, которое говорит о
#: персонажах, игроках, приключении или сценарии, и не говорит о заклинании,
#: эффекте, уроне или здоровье.
_LEVEL_CONTEXT = re.compile(r'(персонаж|игрок|геро|приключени|сценари|модул|парти|отряд)', re.I)
_NOT_A_REQUIREMENT = re.compile(
    r'(заклинани|эффект|свиток|свёрток|экипаж|здоровь|урон|уничтож|существ|'
    r'опасност|штраф|битв|сражени|ослаблен)', re.I)

def levels(page_text):
    """The level span the chapter prints, or nothing when it prints none.

    Each bound keeps the sentence that carried it. A span assembled from several
    pages can end up with a minimum and a maximum printed far apart — the last
    page of an adventure prints «для персонажей 1-7 уровней» about the whole
    book — and a number whose sentence is not stored cannot be checked.
    """
    bottom = top = 0; proof = ''
    low_proof = high_proof = ''

    def printed(sentence):
        """The page's own slice for this sentence, or '' when it is not there.

        A quote is compared against the page by the same test the entities use,
        so a bound that says «на страницах модуля» can be opened and read there
        instead of being a re-flowed copy of the sentence.
        """
        try: return source_span(page_text, sentence[:_MAX_SENTENCE])
        except ValueError: return ''

    for sentence in quotable(page_text):
        if re.search(_NOT_A_LEVEL, sentence, re.I): continue
        # Строка о заклинании, уроне или уничтожении — не условие игры.
        if _NOT_A_REQUIREMENT.search(sentence): continue
        if not _LEVEL_CONTEXT.search(sentence): continue
        # A bound is stored only with the page's own words for it: a level nobody
        # can open on its page is a number the module cannot defend.
        quote = printed(sentence)
        if not quote: continue
        span = re.search(_RANGE_LEVEL, sentence, re.I)
        if span:
            low, high = int(span[1]), int(span[2])
            if 1 <= low <= high <= 20:
                if not bottom or low < bottom: bottom, low_proof = low, quote
                if not top or high > top: top, high_proof = high, quote
                proof = proof or quote
            continue
        ending = re.search(_END_LEVEL, sentence, re.I)
        if ending:
            value = int(ending[1])
            if 1 <= value <= 20:
                if not top or value < top: top, high_proof = value, quote
                proof = proof or quote
            continue
        starting = re.search(_START_LEVEL, sentence, re.I)
        if starting:
            value = int(starting[1])
            if 1 <= value <= 20:
                if not bottom or value < bottom: bottom, low_proof = value, quote
                proof = proof or quote
    if bottom and top and bottom > top: return dict(minimum=None, maximum=None, quote='', minimum_quote='', maximum_quote='')
    return dict(minimum=bottom or None, maximum=top or None, quote=proof,
                minimum_quote=low_proof if bottom else '', maximum_quote=high_proof if top else '')

def merge_levels(values):
    """One span for a chapter out of the spans its pages print.

    A chapter states its end level on one page and the level it expects on
    another; neither page alone answers both, and a page that prints nothing
    must not erase what its neighbour printed. `quote` is the first printed
    sentence that carried any bound; `minimum_quote` and `maximum_quote` name
    the sentence behind each winning bound, so a span read from two pages stays
    checkable on both. A caller that knows which page a value came from passes
    it as `page`, and the winning bound keeps it: a requirement on a screen has
    to name the page it is printed on, not only the sentence.
    """
    bottom = top = None; proof = ''; low_proof = high_proof = ''
    low_page = high_page = None
    for value in values:
        minimum = value.get('minimum')
        if minimum and (bottom is None or minimum < bottom):
            bottom, low_proof = minimum, value.get('minimum_quote') or value.get('quote') or ''
            low_page = value.get('minimum_page') or value.get('page')
        maximum = value.get('maximum')
        if maximum and (top is None or maximum > top):
            top, high_proof = maximum, value.get('maximum_quote') or value.get('quote') or ''
            high_page = value.get('maximum_page') or value.get('page')
        proof = proof or value.get('quote') or ''
    if bottom and top and bottom > top:
        return dict(minimum=None, maximum=None, quote='', minimum_quote='', maximum_quote='',
                    minimum_page=None, maximum_page=None)
    return dict(minimum=bottom, maximum=top, quote=proof, minimum_quote=low_proof,
                maximum_quote=high_proof, minimum_page=low_page if bottom else None,
                maximum_page=high_page if top else None)

#: «для группы из четырёх героев», «партия из 3–5 персонажей», «на двух
#: игроков». A number written in words is as much the book's own statement as a
#: digit, and Russian prints the genitive plural after «из», so the stem list
#: covers the forms a party sentence actually uses.
_PARTY_NUMBER = (r'(\d{1,2}|дв[уо]х|дв[уо]м|дв[уо]мя|тр[её]х|тр[её]м|тр[её]мя|'
                 r'четыр[её]х|четыр[её]м|четыр[её]мя|пят[иью]|шест[иью])')
_PARTY = re.compile(r'(?:парти\w*|групп\w*|отряд\w*|состав\w*|для|на)\D{0,20}?'
                    + _PARTY_NUMBER + r'(?:\s*[-–—]\s*' + _PARTY_NUMBER + r')?\s*'
                    r'(?:игрок\w*|персонаж\w*|геро\w*|участник\w*|человек\w*)', re.I)
_PARTY_WORDS = {'двух': 2, 'двум': 2, 'двумя': 2, 'трех': 3, 'трёх': 3, 'трем': 3, 'трём': 3,
                'тремя': 3, 'четырех': 4, 'четырёх': 4, 'четырем': 4, 'четырём': 4,
                'четырьмя': 4, 'пяти': 5, 'пятью': 5, 'шести': 6, 'шестью': 6}

def _count(value):
    text = str(value or '').strip().casefold()
    if text.isdigit():
        number = int(text)
        return number if 2 <= number <= 12 else None
    return _PARTY_WORDS.get(text)

def party(page_text):
    """How many heroes the page says the party has, with its own sentence.

    A party size is the book's requirement exactly as a level span is, and it is
    printed in prose: «Это приключение рассчитано на четырёх персонажей». Only a
    sentence that names both a party word and its number counts, and only when
    the sentence is really on the page, so a spell that targets «двух существ»
    never becomes a party size.
    """
    for sentence in quotable(page_text):
        # Как и уровень: размер группы — условие приключения, а не описание
        # заклинания, свитка или экипажа. «Идеальный размер группы» и
        # «приключение рассчитано на» — да; «эффект на группу из 9 персонажей» — нет.
        if _NOT_A_REQUIREMENT.search(sentence): continue
        if not re.search(r'(приключени|идеальн|рассчитан|парти|игрок|сценари|модул)', sentence, re.I):
            continue
        match = _PARTY.search(sentence)
        if not match: continue
        low = _count(match[1])
        high = _count(match[2]) if match[2] else low
        if not low or not high or low > high: continue
        quote = _quote(page_text, sentence)
        if not quote: continue
        return dict(size=low, maximum=high, quote=quote, text=sentence[:300], page=None)
    return None

def merge_party(values):
    """The smallest party the chapter asks for, on the page that printed it."""
    best = None
    for value in values:
        if not value: continue
        if best is None or value.get('size', 0) < best.get('size', 0):
            best = dict(value)
        elif value.get('maximum', 0) > best.get('maximum', 0):
            best['maximum'] = value['maximum']
    return best

#: A continuation is a printed condition, not a genre label: «является прямым
#: продолжением», «если ваши персонажи прошли первое приключение». A chapter
#: that merely says «продолжение следует» names no condition and is skipped.
_CONTINUATION = re.compile(
    r'(?:явля\w*\s+(?:прямым\s+)?продолжением|прямое\s+продолжение|'
    r'продолжение\s+(?:приключения|истории|кампании|трилогии)|'
    r'(?:если|когда)\s+(?:вы|ваши\s+(?:персонажи|герои)|персонажи|герои)\s+'
    r'(?:играли|прошли|завершили|закончили)|'
    r'начина\w*\s+после\s+(?:приключения|истории)|'
    r'следует\s+за\s+(?:приключением|историей)|продолжение\s+(?:\d+\s+)?том\w*)', re.I)

def continuation(page_text):
    """The condition the page prints for playing this chapter after another."""
    for sentence in quotable(page_text):
        if _NOT_A_REQUIREMENT.search(sentence): continue
        if not _CONTINUATION.search(sentence): continue
        quote = _quote(page_text, sentence)
        if not quote: continue
        return dict(text=sentence[:300], quote=quote, page=None)
    return None

def printed_requirements(pages):
    """The book's requirements among `{page: text}`, each with its own page.

    One function for a chapter and for the pages printed in front of it: the
    introduction of a book states the level span and the ideal party size on a
    title page that belongs to the first episode, but is not part of the chapter
    text. Reading both places with the same rules keeps one fact one fact.
    """
    numbers = sorted(number for number in pages if pages[number])
    level = merge_levels([dict(levels(pages[number]), page=number) for number in numbers])
    sizes = []
    afters = []
    for number in numbers:
        found = party(pages[number])
        if found: sizes.append(dict(found, page=number))
        after = continuation(pages[number])
        if after: afters.append(dict(after, page=number))
    return dict(levels=level, party=merge_party(sizes), continuation=afters[0] if afters else None,
                pages=numbers)

def page_requirements(library, document, first, last):
    """`printed_requirements` for a page range read from the library."""
    with library.db() as db:
        rows = db.execute('SELECT number,content FROM pages WHERE document=? AND number BETWEEN ? AND ?',
                          (document, first, last)).fetchall()
    return printed_requirements({int(row['number']): row['content'] for row in rows})

def with_front_requirements(library, document, first, last, scene):
    """Add the requirements printed before the first chapter to its scene.

    A book prints «сценарий для персонажей, начиная с 8 уровня» and «идеальный
    размер группы — 4 персонажа» in its introduction, pages before the first
    chapter boundary. Those pages belong to the first episode as material, so
    its requirements are read there too; places, people and checks still come
    only from the chapter itself, so the introduction's credits never become a
    location. Values the chapter prints win on a tie; a printed range keeps both
    bounds.
    """
    extra = page_requirements(library, document, first, last)
    levels_now = scene.get('levels') if isinstance(scene.get('levels'), dict) else {}
    parties_now = scene.get('party') if isinstance(scene.get('party'), dict) else None
    after_now = scene.get('continuation') if isinstance(scene.get('continuation'), dict) else None
    scene = dict(scene)
    scene['levels'] = merge_levels([levels_now, extra['levels']])
    scene['party'] = merge_party([parties_now, extra['party']])
    scene['continuation'] = after_now or extra['continuation']
    return scene

def _quote(page_text, quote):
    """The page's own slice for this sentence, or '' when it is not on the page."""
    try: return source_span(page_text, quote)
    except ValueError: return ''

def grounded(pages, candidates):
    """Split candidates into those the page proves and those it does not.

    `pages` maps a page number to its text; a candidate is `kind`, `name` (when
    it has one), `page` and `quote`. A candidate survives only when its quote
    occurs on its own page (the quote is then restored to the page's exact
    spelling and line breaks) and, for a named entity, when the name occurs in
    that quote. Everything else is reported with the reason instead of being
    stored, which is what keeps an invented person out of the world.
    """
    accepted = []; rejected = []
    for candidate in candidates:
        text = pages.get(candidate.get('page'))
        if not text: rejected.append(dict(candidate, reason='Страница не извлечена')); continue
        quote = _quote(text, candidate.get('quote') or '')
        if not quote: rejected.append(dict(candidate, reason='Цитата не найдена на странице')); continue
        name = str(candidate.get('name') or '').strip()
        if name and candidate.get('kind') != 'check' and not name_in_quote(name, normalized(quote)):
            rejected.append(dict(candidate, reason='Имя не встречается в собственной цитате')); continue
        accepted.append(dict(candidate, quote=quote))
    return accepted, rejected

def _frequency(pages, names):
    """How often each printed name occurs in the chapter, case-insensitively.

    Whole words only: «чудес» occurs inside «чудесный» as a substring, and a
    substring count would make a chapter look like it repeats a place name it
    never printed twice.
    """
    counts = {name: 0 for name in names}
    for text in pages.values():
        low = normalized(text)
        for name in counts:
            needle = normalized(name)
            if needle: counts[name] += len(re.findall(r'(?<!\w)' + re.escape(needle) + r'(?!\w)', low))
    return counts

def module_scene(library, document, first, last, title='', reference=False):
    """Read one page range as a scene: its places, its people, its checks.

    Nothing is asked of a model here. A chapter whose pages print none of the
    forms above yields an empty scene, and the caller keeps it a page range
    without claiming entities the book never named.
    """
    with library.db() as db:
        rows = db.execute('SELECT number,content FROM pages WHERE document=? AND number BETWEEN ? AND ? ORDER BY number',
                          (document, first, last)).fetchall()
    pages = {int(row['number']): row['content'] for row in rows}
    if not pages: return dict(document=document, pages=[first, last], locations=[], npcs=[], checks=[],
                              levels=dict(minimum=None, maximum=None, quote='', minimum_quote='', maximum_quote='',
                                          minimum_page=None, maximum_page=None),
                              party=None, continuation=None, rejected=[],
                              entry=entry_scene(title, [], {}, document),
                              section=dict(kind='adventure', reference=''), reference=[], edits=[],
                              counts=dict(locations=0, npcs=0, checks=0))
    place_candidates = []
    # The chapter, not one page, says whether a word is a name of its own: the
    # same word can stand alone on page 8 and only follow a kind noun on page 9.
    standalone = standalone_words(pages)
    for number in sorted(pages):
        for item in places(pages[number]):
            # A word the chapter never prints on its own, or one printed in an
            # oblique case, is the dependent of the kind noun in front of it
            # («Город Чудес», «Болото Мертвецов», «Крепость Неронвейна»), so the
            # place is the whole printed phrase. A word the book also prints after
            # a preposition in the nominative is a name («Гринест», «Парнаст») and
            # the kind noun before it only describes it.
            merged = bool(_INFLECTED.search(item['name'])) or normalized(item['name']) not in standalone
            name = item['phrase'] if merged else item['name']
            if merged and item.get('tail'):
                # The book capitalises its own phrase word by word, so for a
                # dependent every capitalised word after the kind belongs to the
                # name («Деревня Ледяных Охотников»), and no shorter form of it
                # is offered: «Деревня Ледяных» would be the same fragment this
                # reading exists to remove.
                place_candidates.append(dict(item, name=name + ' ' + item['tail'], page=number,
                                             source='prose', evidence=item['name']))
                continue
            place_candidates.append(dict(item, name=name, page=number, source='prose', evidence=item['name']))
            # «Деревня Парнаст Маленькая деревня в тайне…» is one name and then a
            # new phrase; «Лагерь Налётчиков» is two words of one name. Both are
            # offered and the chapter's own repetition decides: a pair the chapter
            # never repeats is the sentence's capitalisation, not the place's name.
            if item.get('tail'):
                place_candidates.append(dict(item, name=name + ' ' + item['tail'], page=number, source='prose'))

    # The chapter's own title is printed on every page of the chapter, so the
    # page it is read from is the module's first page and the quote is that
    # printed line — not a sentence the analysis assembled from it.
    named = title_place(title)
    if named:
        for number in sorted(pages):
            line = next((line.strip() for line in str(pages[number]).splitlines()
                         if normalized(line).endswith(normalized(named)) and named.casefold() in line.casefold()), '')
            if line:
                kind = next((label for key, label in _PLACE_TYPES.items() if named.casefold().startswith(key)), 'place')
                place_candidates.insert(0, dict(name=named, kind=kind, gloss='', quote=line, page=number, source='title'))
                break
    names = []
    for item in place_candidates:
        for value in (item['name'], item.get('evidence') or item['name']):
            if value not in names: names.append(value)
    frequency = _frequency(pages, names)
    # A kind noun with one capitalised word after it is a sentence, not always a
    # place; a name the chapter repeats, or the translator glossed, is. A name
    # the chapter prints in its own title needs no further evidence. The count
    # for a phrase («Город Чудес») is asked of the word the chapter repeats
    # («Чудес»), because the book prints the phrase once and refers to its tail
    # afterwards; that is the same evidence the bare name used to pass on.
    def repeated(item):
        return max(frequency.get(item['name'], 0), frequency.get(item.get('evidence') or '', 0))
    plausible = [item for item in place_candidates
                 if item.get('source') == 'title' or item['gloss'] or repeated(item) > 1]

    accepted_places, rejected = grounded(pages, plausible)
    locations = []
    for item in accepted_places:
        known = next((place for place in locations if normalized(place['name']) == normalized(item['name'])), None)
        if known:
            known['forms'].add(item['name'])
            continue
        # «Замок Неритар» and «Неритар» are one castle: the book prints the long
        # form where it names the place and the short one where it refers to it.
        # The long form wins — it is what the chapter titles the place with — so
        # a short reference joins it instead of renaming it. Every spelling that
        # joined is kept in `forms`, so the place is counted once per spelling
        # instead of once per sentence it was met in. A phrase is longer at the
        # front as well («Деревня Ледяных» and «Деревня Ледяных Охотников»), so
        # the comparison looks at both ends of the name.
        shorter = next((place for place in locations
                        if normalized(place['name']) != normalized(item['name'])
                        and (normalized(place['name']).endswith(' ' + normalized(item['name']))
                             or normalized(place['name']).startswith(normalized(item['name']) + ' '))), None)
        longer = next((place for place in locations
                       if normalized(item['name']) != normalized(place['name'])
                       and (normalized(item['name']).endswith(' ' + normalized(place['name']))
                            or normalized(item['name']).startswith(normalized(place['name']) + ' '))), None)

        if shorter:
            shorter['forms'].add(item['name'])
            continue
        if longer:
            longer.update(name=item['name'], kind=item['kind'] or longer['kind'], page=item['page'], quote=item['quote'],
                          provenance=dict(document=document, page=item['page'], quote=item['quote'],
                                          source='title' if item.get('source')=='title' else 'printed'))
            longer['forms'].add(item['name'])
            continue
        # The provenance is what the candidate came from: a place the chapter's own
        # title named is `title`, one the prose named is `printed`, so a reader can
        # tell a headline from a sentence.
        locations.append(dict(name=item['name'], kind=item['kind'], gloss=item['gloss'], page=item['page'], document=document,
                              quote=item['quote'], forms={item['name']},
                              binding='title' if item.get('source')=='title' else 'printed',
                              entity_type='place', scope='scenario',
                              provenance=dict(document=document, page=item['page'], quote=item['quote'],
                                              source='title' if item.get('source')=='title' else 'printed')))
    # `mentions` is how often the chapter prints the place, counted once per
    # spelling. Adding the chapter-wide frequency on every alias encounter read
    # «Замок Неритар» as 499 mentions where the chapter prints it 31 times
    # (31 + 9 × 52), and that inflated count decided which place the player
    # starts in, since the list is sorted by it.
    for place in locations:
        place['mentions'] = sum(frequency.get(form, 0) for form in place.pop('forms')) or 1
    locations.sort(key=lambda place: (-place['mentions'], place['page']))
    # A chapter whose prose names no place of a known kind still prints its own
    # title on every page, and a book about a dragon hatchery uses that title as
    # the name of the place the episode happens in («Эпизод 3: Драконья кладка»).
    # The fallback is taken only from a line the chapter itself printed: a title
    # that occurs nowhere in its pages names no place, and the scene stays empty
    # rather than inventing one.
    if not locations and title.strip() and not reference:
        wanted = normalized(title)
        for number in sorted(pages):
            line = next((line.strip() for line in str(pages[number]).splitlines()
                         if normalized(line) == wanted
                         or re.fullmatch(re.escape(wanted) + r'\s*\d{0,4}', normalized(line))), '')
            quote = _quote(pages[number], line) if line else ''
            if not quote: continue
            # The printed line is «Эпизод 3: Драконья кладка»; the episode number is
            # how the book numbers its chapters, not part of the name, and `kind`
            # says the name is an anchor for the chapter rather than a printed
            # toponym.
            phrase = re.sub(r'^\s*(?:эпизод|глава|часть|приложение|episode|chapter|part|appendix)\s*\d{1,3}\s*[:.\-–—]\s*',
                            '', title.strip(), flags=re.I) or title.strip()
            locations.append(dict(name=phrase[:120], kind='chapter', gloss='', page=number,
                                  document=document, quote=quote, mentions=_frequency(pages, [title])[title] or 1,
                                  binding='title', entity_type='chapter', scope='scenario',
                                  provenance=dict(document=document, page=number, quote=quote, source='title')))
            break
    primary = locations[0]['name'] if locations else ''

    def bind(sentence, page):
        """Which place this sentence belongs to, and on what evidence."""
        named = [place['name'] for place in locations if name_in_quote(place['name'], normalized(sentence))]
        if len(named) == 1: return named[0], 'sentence'
        if len(locations) == 1: return locations[0]['name'], 'chapter'
        if primary and any(normalized(place['name']) in normalized(sentence) for place in locations):
            return primary, 'chapter'
        return (primary, 'chapter') if primary else ('', '')

    person_candidates = []
    for number in sorted(pages):
        for item in people(pages[number]):
            place, binding = bind(item['quote'], number)
            person_candidates.append(dict(item, page=number, location=place, binding=binding or 'chapter'))
    npcs, rejected_npcs = grounded(pages, [dict(item, kind='npc') for item in person_candidates])
    rejected += rejected_npcs
    check_candidates = []
    for number in sorted(pages):
        for item in checks(pages[number]):
            place, binding = bind(item['quote'], number)
            check_candidates.append(dict(item, page=number, location=place, binding=binding or 'chapter',
                                         kind='check', name=''))
    accepted_checks, rejected_checks = grounded(pages, check_candidates)
    rejected += rejected_checks
    npcs = [dict(name=item['name'], gloss=item.get('gloss', ''), page=item['page'], document=document,
                 quote=item['quote'], location=item['location'], binding=item['binding'],
                 entity_type='npc', scope='scenario',
                 provenance=dict(document=document, page=item['page'], quote=item['quote'],
                                 source='printed')) for item in npcs]
    # One person named on two pages is one person: the world contract refuses two
    # NPCs with one name (import_package raises «Имена новых NPC должны однозначно
    # определять личность»), so a chapter that introduces Леосин twice would make
    # the campaign fail to start. The first printed sentence is kept and later
    # mentions only raise the count.
    seen = {}; unique_npcs = []
    for item in npcs:
        key = normalized(item['name'])
        if key in seen:
            seen[key]['mentions'] += 1
            if not seen[key]['location'] and item['location']: seen[key]['location'] = item['location']
            continue
        entry = dict(item, mentions=1)
        seen[key] = entry; unique_npcs.append(entry)
    unique_checks = []
    keys = set()
    for item in accepted_checks:
        key = (item['location'], item['ability'] or item['skill'], item['saving_throw'], item['dc'])
        if key in keys: continue
        keys.add(key)
        unique_checks.append(dict(ability=item['ability'], skill=item['skill'], dc=item['dc'],
                                  saving_throw=item['saving_throw'], trigger=item['trigger'],
                                  page=item['page'], document=document, quote=item['quote'],
                                  location=item['location'], binding=item['binding'],
                                  entity_type='check', scope='scenario',
                                  provenance=dict(document=document, page=item['page'], quote=item['quote'],
                                                  source='printed')))
    level = merge_levels([dict(levels(pages[number]), page=number) for number in sorted(pages)])
    # The party size and the continuation condition are the book's own printed
    # requirements, read the same way a check DC is: a sentence that is really on
    # its page, kept together with that page. They stay out of the fiction — the
    # scene keeps places, people and checks for play, and these two travel as
    # `requirements` for the start screen.
    sizes, afters = [], []
    for number in sorted(pages):
        found = party(pages[number])
        if found: sizes.append(dict(found, page=number))
        after = continuation(pages[number])
        if after: afters.append(dict(after, page=number))
    size = merge_party(sizes)
    after = afters[0] if afters else None
    return dict(document=document, pages=[first, last], locations=locations, npcs=unique_npcs,
                checks=unique_checks, levels=level, party=size, continuation=after, rejected=rejected,
                entry=entry_scene(title, locations, pages, document),
                section=dict(kind='adventure', reference=''), reference=[], edits=[],
                counts=dict(locations=len(locations), npcs=len(unique_npcs), checks=len(unique_checks)))

def requirements(scene, module=''):
    """What the chapter prints about starting it, each fact with its page.

    These are the book's requirements, not the fiction: the level span the party
    should be, the party size it expects and the condition for playing the
    chapter after another. Every row keeps the sentence and the page it was read
    from, so the start screen can open the book where the number is printed.
    """
    rows = []
    levels = scene.get('levels') if isinstance(scene.get('levels'), dict) else {}
    if levels.get('minimum'):
        rows.append(dict(kind='minimum_level', value=levels['minimum'],
                         page=levels.get('minimum_page'),
                         quote=levels.get('minimum_quote') or levels.get('quote') or '',
                         document=scene.get('document') or '', module=module))
    if levels.get('maximum') and levels.get('maximum') != levels.get('minimum'):
        rows.append(dict(kind='maximum_level', value=levels['maximum'],
                         page=levels.get('maximum_page'),
                         quote=levels.get('maximum_quote') or levels.get('quote') or '',
                         document=scene.get('document') or '', module=module))
    party_size = scene.get('party') if isinstance(scene.get('party'), dict) else {}
    if party_size.get('size'):
        rows.append(dict(kind='recommended_party', value=party_size['size'],
                         maximum=party_size.get('maximum') or party_size['size'],
                         page=party_size.get('page'), quote=party_size.get('quote') or '',
                         document=scene.get('document') or '', module=module))
    after = scene.get('continuation') if isinstance(scene.get('continuation'), dict) else {}
    if after.get('text'):
        rows.append(dict(kind='continuation', text=after['text'], page=after.get('page'),
                         quote=after.get('quote') or '',
                         document=scene.get('document') or '', module=module))
    return rows

def _slug(text):
    value = re.sub(r'[^a-z0-9]+', '-', str(text).casefold()).strip('-')
    return value[:40] or 'place'

#: A chapter title names its own setting more often than the prose repeats it:
#: «Эпизод 2: Лагерь Налётчиков», «Эпизод 6: Замок Неритар». The part after the
#: number is the book's own noun phrase, so it is a place when it is built on a
#: place noun and names one with a capital.
_TITLE = re.compile(r'^\s*(?:эпизод|глава|часть|приложение|chapter|episode|part|appendix)\s*\d{1,3}\s*[:.\-–—]\s*(?P<title>.+)$', re.I)

#: «Эпизод 8: Замок в облаках» titles a castle by a phrase, not by a name: the
#: words after the place noun start with a preposition instead of naming it.
_PREPOSITIONS = {'в', 'во', 'на', 'у', 'из', 'к', 'ко', 'с', 'со', 'о', 'об', 'под', 'над',
                 'за', 'при', 'без', 'для', 'от', 'до', 'по', 'сквозь', 'через'}

def title_place(title):
    """The place a chapter titles itself with, or '' when it names none.

    A title is the book's own headline, so the words after the place noun may be
    lowercase there («Эпизод 2: Лагерь налетчиков»): the capitalisation rule that
    keeps prose out belongs to sentences, not to a title. What a title still
    cannot do is name a place with a preposition — that is a phrase about a
    place, not its name.
    """
    match = _TITLE.match(str(title or ''))
    phrase = ' '.join((match['title'] if match else '').split())
    if not phrase or len(phrase) > 60: return ''
    for kind in sorted(_PLACE_TYPES, key=len, reverse=True):
        if not phrase.casefold().startswith(kind + ' '): continue
        rest = phrase[len(kind):].strip()
        first = rest.split()[0].casefold() if rest else ''
        if not rest or first in _NOT_A_NAME or first in _PREPOSITIONS: return ''
        return phrase
    return ''

def _title_phrase(title):
    """The module's own name, without the book's numbering («Эпизод 1: …»)."""
    text = ' '.join(str(title or '').split())
    match = _TITLE.match(text)
    phrase = match['title'] if match else text
    return ' '.join(phrase.split())[:120]


def _printed_title_line(pages, title):
    """The line where the chapter prints its own heading, and the page, or ''."""
    wanted = normalized(title)
    if not wanted:
        return '', 0
    for number in sorted(pages):
        for line in str(pages[number]).splitlines():
            if normalized(line) == wanted or re.fullmatch(re.escape(wanted) + r'\s*\d{0,4}', normalized(line)):
                return line.strip(), number
    return '', 0


def entry_scene(title, locations, pages, document=''):
    """Where the module opens, checked against the module's own name.

    The first place a chapter mentions is not where it starts. «Эпизод 1: Совет
    Глубоководья» prints «Башня Ксонтала» on its pages, and a castle mentioned
    often is not the council the episode is about — yet the editor used to show
    the castle as the entry and nothing said that was a guess.

    Two facts are kept apart. `name` is the scene the book itself names: the
    printed heading of the module, or the place a heading that names a toponym
    («Эпизод 7: Башня Ксонтала») points at. `place` is the ground the hero stands
    on, which is that toponym when the title names one and the most-mentioned
    printed place otherwise. `confidence` says the name is printed, and
    `place_confidence` says whether the ground is printed or a guess — so the
    editor can mark the guess instead of hiding it.
    """
    phrase = _title_phrase(title)
    named = title_place(title)
    line, number = _printed_title_line(pages, title)
    by_name = {normalized(place.get('name') or ''): place for place in locations}
    primary = locations[0] if locations else None
    page = number or (primary['page'] if primary else 0)
    quote = line or ''
    if named and named in by_name:
        found = by_name[named]
        return dict(name=found['name'], title=str(title or ''), place=found['name'],
                    page=found['page'], quote=found.get('quote') or '', document=document,
                    binding='title', confidence='printed', place_confidence='printed')
    if phrase:
        return dict(name=phrase, title=str(title or ''),
                    place=primary['name'] if primary else '',
                    page=page, quote=quote, document=document,
                    binding='title' if line else 'chapter',
                    confidence='printed' if line else 'check',
                    place_confidence='check' if primary else 'check',
                    note='' if primary else 'Название модуля не называет место, и страницы не печатают ни одного')
    if primary:
        return dict(name=primary['name'], title=str(title or ''), place=primary['name'],
                    page=primary['page'], quote=primary.get('quote') or '', document=document,
                    binding='chapter', confidence='check', place_confidence='check',
                    note='Название модуля не называет место: стартовая сцена — первое найденное место')
    return dict(name='Сцена из книги', title=str(title or ''), place='', page=0, quote='',
                document=document, binding='chapter', confidence='check', place_confidence='check')


def with_reference(world, extras):
    """The book's appendix places, added to an episode's world.

    An appendix is not a scenario, but the places it prints are places of the
    same book: a campaign that starts in episode one can walk into the town the
    appendix describes. They are added as known-less locations with their own
    node ids, so nothing in the episode is overwritten and no start ever lands
    there. A place the episode already has is left as the episode wrote it, and
    an NPC whose name already exists is not added twice — `import_package`
    refuses both, and refusing the whole start over a reference page would be
    worse than leaving that one page out.
    """
    if world is None or not extras:
        return world
    names = {place.name for place in world.locations}
    nodes = {node.id for node in world.nodes}
    people = {npc.name for place in world.locations for npc in place.npc}
    for extra in extras:
        for place in extra.locations:
            if place.name in names:
                continue
            copy = place.model_copy(deep=True)
            copy.known_to_hero = False
            copy.npc = [npc.model_copy(deep=True) for npc in copy.npc if npc.name not in people]
            people.update(npc.name for npc in copy.npc)
            names.add(copy.name)
            world.locations.append(copy)
        for node in extra.nodes:
            if node.name not in names or node.name in {n.name for n in world.nodes}:
                continue
            identifier = 'apx-' + node.id
            while identifier in nodes:
                identifier += 'x'
            nodes.add(identifier)
            world.nodes.append(node.model_copy(update=dict(id=identifier, known=False), deep=True))
    return world

def scene_world(title, scene):
    """The extracted scene as the world contract the engine runs on.

    A check is stored where the engine looks for it: `WorldLocation.checks`,
    keyed by the place it belongs to, carrying its page and quote as provenance.
    That is the same table the player's «Проверить:» list is built from, so a
    printed check is playable and its DC still says which page printed it.
    """
    from .source_checks import SourceCheck
    from .world import ScenarioPackage, WorldLocation, WorldNPC, WorldNode
    document = scene.get('document') or ''
    entry = scene.get('entry') if isinstance(scene.get('entry'), dict) else {}
    # The ground the hero stands on is the entry's own `place`, not whatever the
    # page mentioned first: the entry records which of them the book named and
    # which one is only a guess.
    entry_place = normalized(str(entry.get('place') or entry.get('name') or '')) if entry else ''
    places = []
    index = {}
    for position, place in enumerate(scene.get('locations') or [], 1):
        slug = 'l' + str(position)
        index[normalized(place['name'])] = slug
        known = normalized(place['name']) == entry_place if entry_place else position == 1
        places.append(WorldLocation(name=place['name'],
                                    sealed_text=(place.get('quote') or '')[:3000],
                                    known_to_hero=known))
    if not places: return None, []
    by_slug = {slug: place for slug, place in zip(index.values(), places)}
    for npc in scene.get('npcs') or []:
        slug = index.get(normalized(npc.get('location') or ''))
        if not slug: continue
        by_slug[slug].npc.append(WorldNPC(name=npc['name'],
                                          private_knowledge=[(npc.get('quote') or '')[:1000]],
                                          known=npc.get('gloss') or ''))
    stored = 0
    for check in scene.get('checks') or []:
        slug = index.get(normalized(check.get('location') or ''))
        if not slug: continue
        flavour = 'saving_throw' if check.get('saving_throw') else ('skill' if check.get('skill') else 'ability')
        target = check.get('skill') or check.get('ability') or ''
        key = 'loc:' + slug + ':' + flavour + ':' + (target or 'check') + ':' + str(check['dc'])
        try:
            value = SourceCheck(ability=check.get('ability') or None, skill=check.get('skill') or '',
                                dc=int(check['dc']), origin='source',
                                source=SourceQuote(document=document, page=int(check['page']),
                                                   quote=(check.get('quote') or '')[:3000]),
                                trigger=(check.get('trigger') or '')[:240])
        except ValueError:
            continue
        by_slug[slug].checks[key] = value; stored += 1
    nodes = [WorldNode(id='scene-' + str(position), name=place.name,
                       coordinates=[2 + (position - 1) % 10 * 2, 2 + (position - 1) // 10 * 2],
                       known=place.known_to_hero)
             for position, place in enumerate(places, 1)]
    package = ScenarioPackage(name=str(title)[:120] or 'Сцена из книги', source='book-scene-v1',
                              locations=places, nodes=nodes)
    return package, [dict(**check, stored=bool(index.get(normalized(check.get('location') or ''))))
                     for check in (scene.get('checks') or [])]
