"""Compile source-grounded proposals into existing world/event contracts."""
import copy,hashlib,json,re
from .preparation_contracts import PreparationBatch,PreparedEntity
from .world import WorldLocation,WorldNPC,WorldNode,WorldRoute,ScenarioPackage
from .adventure_contracts import AdventureDefinition,ScenarioEvent,RollTable,Tracker,SurvivalRule,Companion,WildernessProcedure

# The PDF extractor inserts layout marks (U+FFFE soft breaks, U+00AD shy hyphens,
# zero-width joiners). They are not letters and not whitespace, so a quote copied
# from a page would fail to match its own text unless they are removed first.
_INVISIBLE={'Cf','Cc','Cn'}
# U+FFFE and the zero-width marks are the extractor's layout noise. In this book
# they are overwhelmingly intra-word (6597 joins against 3 real separators:
# 'по\ufffemeстьем', 'Ка\ufffeзарина'), so treating them all as spaces invents
# word breaks the book never printed and hides place nouns from matching. A mark
# between two letters joins them; a mark standing where a space belongs still
# separates. A soft hyphen (U+00AD) always splits one word and disappears.
_BREAKS={'\ufffe','\u200b','\u200c','\u200d','\u2060'}
_DROPS={'\u00ad','\ufeff'}

def _visible(text):
    import unicodedata
    kept=[]
    for char in str(text):
        if char in _BREAKS:kept.append(' ')
        elif char in _DROPS:continue
        elif char.isspace() or unicodedata.category(char) not in _INVISIBLE:kept.append(char)
    return ''.join(kept)

def _visible_positions(text):
    """The comparison text together with each character's index in the page.

    One decomposition for both sides is what makes a verbatim quote match its
    own page. A PDF extractor can emit 'и' plus a combining breve where the book
    prints 'й'; decomposed, both spellings are the same two characters, so the
    comparison no longer depends on how the letter was encoded. Composing would
    merge those two characters and lose the position of the letter in the page,
    which source_span() needs to return the exact span.
    """
    import unicodedata
    chars=[];positions=[]
    for index,char in enumerate(str(text)):
        if char in _BREAKS:piece=' '
        elif char in _DROPS:continue
        elif char.isspace() or unicodedata.category(char) not in _INVISIBLE:piece=char
        else:continue
        if piece.isspace():
            # Every run of whitespace is one separator: a page breaks a line
            # between two words and the quote joins them with a space, and the
            # two must compare equal. Keeping the raw \r or \n would make a
            # quote that spans a printed line break fail against its own page.
            if chars and chars[-1]!=' ':chars.append(' ');positions.append(index)
            continue
        for lower in unicodedata.normalize('NFD',piece.casefold().replace('ё','е')):
            chars.append(lower);positions.append(index)
    while chars and chars[-1]==' ':chars.pop();positions.pop()
    return ''.join(chars),positions

try:
    from functools import lru_cache
except ImportError:lru_cache=None

if lru_cache is not None:
    # `normalized` вызывается десятки тысяч раз для ОДНИХ И ТЕХ ЖЕ цитат: сборка
    # мира сравнивает каждое имя с каждой цитатой, а разбор строки идёт посимвольно
    # через NFD. Кэш — это чистый memoise, смысл сравнения не меняется.
    @lru_cache(maxsize=1024)
    def _normalized_cached(text):return _visible_positions(text)[0]
    def normalized(text):return _normalized_cached(str(text) if not isinstance(text,str) else text)
else:
    def normalized(text):return _visible_positions(text)[0]

# A page writes a small count as a word as often as a digit: 'three kobolds
# guard the far arch'. The number is still printed, so a fact that carries it must
# pass the same grounding test; only its spelling differs.
_NUMBERS={0:'zero',1:'one',2:'two',3:'three',4:'four',5:'five',6:'six',7:'seven',8:'eight',9:'nine',10:'ten',
    11:'eleven',12:'twelve',13:'thirteen',14:'fourteen',15:'fifteen',16:'sixteen',17:'seventeen',18:'eighteen',19:'nineteen',20:'twenty'}
# The same words read backwards, for a sentence that counts its creatures:
# 'Three kobolds guard the far arch' names three opponents, not one.
_COUNT_WORDS={word:value for value,word in _NUMBERS.items() if value>=2}
_COUNT_WORDS.update({'a pair of':2,'a couple of':2,'a dozen':12})

def printed_number(value):
    """The digit, or the word a page may use instead of it."""
    if isinstance(value,float) and value.is_integer():value=int(value)
    return str(value).removesuffix('.0')

def number_in_quote(value,quote):
    digit=printed_number(value)
    if re.search(r'(?<![\d.,+\-])'+re.escape(digit)+r'(?![\d.,])',quote):return True
    word=_NUMBERS.get(int(value)) if isinstance(value,(int,float)) and not isinstance(value,bool) and float(value).is_integer() else None
    return bool(word) and bool(re.search(r'(?<!\w)'+word+r'(?!\w)',quote))

# A book states its recommended level in digits or in words, as one level or as
# a range, and either way it is the same fact about the same adventure. Russian
# numerals are matched by stem around the level word, so 'пятого уровня' and
# '5 уровня' both name level five.
_RU_ORDINALS=((word,value) for word,value in (
    ('двадцат',20),('девятнадцат',19),('восемнадцат',18),('семнадцат',17),('шестнадцат',16),
    ('пятнадцат',15),('четырнадцат',14),('тринадцат',13),('двенадцат',12),('одиннадцат',11),
    ('десят',10),('девят',9),('восьм',8),('седьм',7),('шест',6),('пят',5),('четверт',4),('трет',3),('втор',2),('перв',1)))
_RU_ORDINALS=tuple(sorted(_RU_ORDINALS,key=lambda row:-len(row[0])))
_RU_CARDINALS={'один':1,'одного':1,'двух':2,'двоих':2,'два':2,'две':2,'трёх':3,'трех':3,'троих':3,'три':3,
    'четырёх':4,'четырех':4,'четверых':4,'четыре':4,'пяти':5,'пятерых':5,'пять':5,'шести':6,'шесть':6,
    'семи':7,'семь':7,'восьми':8,'восемь':8,'девяти':9,'девять':9,'десяти':10,'десять':10}

_LEVEL_WORD=r'(?:уровн\w*|level\w*|lvl\.?)'

def level_bounds(quote):
    """Every recommended level the quote prints, as (low,high) pairs.

    A range is printed as a range; a single recommended level is printed as one
    number with no dash. Both answer the same question, so a book that says
    'для 4-5 игроков 5 уровня' offers the level five, and demanding a range from
    it would reject the book's own statement.
    """
    text=normalized(quote);ranges=[];single=[]
    def ok(low,high):return 1<=low<=20 and 1<=high<=20 and low<=high
    for pattern in (r'(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*'+_LEVEL_WORD,
                    r'(\d{1,2})(?:st|nd|rd|th)?\s*[-–—]\s*(\d{1,2})(?:st|nd|rd|th)\s*[- ]?\s*level\b',
                    _LEVEL_WORD+r'\s*(\d{1,2})\s*[-–—]\s*(\d{1,2})'):
        for match in re.finditer(pattern,text):
            low,high=int(match.group(1)),int(match.group(2))
            if ok(low,high):ranges.append((low,high,match.start(),match.end()))
    def covered(at):
        # The high end of '5-8 уровней' is a range end, not a second, separate
        # recommendation: reading it as one would let a campaign claim level 8
        # from a book that only printed a span.
        return any(first<=at<last for _,_,first,last in ranges)
    # '7th level' and '7th-level characters' are the same statement with the noun
    # on either side of the number; both are printed, so both are read.
    for pattern in (r'(\d{1,2})(?:st|nd|rd|th)\s*[- ]?\s*level\b',
                    r'(\d{1,2})\s*(?:-?\s*(?:го|й|ый|ой))?\s*'+_LEVEL_WORD,
                    _LEVEL_WORD+r'\s*(\d{1,2})(?!\d)'):
        for match in re.finditer(pattern,text):
            value=int(match.group(1))
            if ok(value,value) and not covered(match.start(1)):single.append((value,value,match.start()))
    for stem,value in _RU_ORDINALS:
        for pattern in (stem+r'\w*\s+'+_LEVEL_WORD,_LEVEL_WORD+r'\s*(?:на\s+)?'+stem+r'\w*'):
            for match in re.finditer(pattern,text):
                if not covered(match.start()):single.append((value,value,match.start()))
    found=sorted(ranges+[(low,high,at,at) for low,high,at in single],key=lambda row:row[2])
    return [(low,high) for low,high,_,_ in found]

_PARTY_WORD=r'(?:игрок\w*|персонаж\w*|геро\w*|участник\w*|characters?|players?|party)'

def party_size_in_quote(size,quote):
    """True when the printed group size names this count, alone or as a range end.

    'для 4-5 игроков' states both four and five; a campaign that records five
    players is reading the book correctly, and refusing it because the page also
    prints four would be an error of the checker, not of the extraction.
    """
    text=normalized(quote)
    if re.search(rf'(?<!\d){size}\s*(?:[-–—]\s*(\d{{1,2}})\s*)?{_PARTY_WORD}',text):return True
    for match in re.finditer(rf'(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s*{_PARTY_WORD}',text):
        if size in (int(match.group(1)),int(match.group(2))):return True
    for word,value in _RU_CARDINALS.items():
        if value==size and re.search(rf'\b{word}\b\s*(?:\w+\s+){{0,2}}{_PARTY_WORD}',text):return True
    return False

# A transition the book names without printing a distance still exists. The
# shortest legal step keeps it walkable; the definition records that the number
# is assumed, so no reader mistakes it for a printed measurement.
ASSUMED_STEP_METERS=10
ASSUMED_STEP_MINUTES=1

def validation_reason(exc):
    if hasattr(exc,'errors'):
        return '; '.join(('.'.join(map(str,e['loc'])) or '$')+': '+e['msg'] for e in exc.errors(include_input=False,include_url=False,include_context=False)[:3])[:600]
    return str(exc)

def source_span(page,quote):
    """Restore source whitespace; model-normalized quotes cannot erase boundaries.

    The page and the quote pass through the same normalizer, so a quote copied
    from the page matches even when the extractor left soft breaks, soft
    hyphens or a decomposed letter inside it. Composition, not length, is the
    reason the two used to disagree: the page kept the invisible characters
    while the quote had already dropped them.
    """
    text,positions=_visible_positions(page)
    needle=normalized(quote)
    start=text.find(needle)
    if start<0 or not needle:raise ValueError('Цитата не подтверждена страницей')
    end=start+len(needle)-1
    return page[positions[start]:positions[end]+1]

def _inflected(word,other):
    """Same Russian noun in another case: a shared stem plus a short ending."""
    if word==other:return True
    if len(word)<4 or len(other)<4:return False
    common=0
    for a,b in zip(word,other):
        if a!=b:break
        common+=1
    # Cyrillic case endings are 1-3 letters; a longer difference is a new word.
    return common>=max(4,len(word)-3,len(other)-3)


_TRANSLIT={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ж':'zh','з':'z','и':'i','й':'i','к':'k','л':'l','м':'m',
    'н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch',
    'ъ':'','ы':'y','ь':'','э':'e','ю':'iu','я':'ia','ё':'e'}


def _translit(text):
    """A Latin skeleton so a translated proper name can be compared with the print.

    This is a comparison key, not a name: it never becomes the published value.
    """
    plain=normalized(text)
    return ''.join(_TRANSLIT.get(char,char) for char in plain if char.isalnum())


def _distance(left,right):
    """Levenshtein distance, bounded by the caller's tolerance."""
    if left==right:return 0
    previous=list(range(len(right)+1))
    for i,a in enumerate(left,1):
        current=[i]
        for j,b in enumerate(right,1):
            current.append(min(previous[j]+1,current[j-1]+1,previous[j-1]+(a!=b)))
        previous=current
    return previous[-1]


def _same_word_cached(name,other):
    """Inflection, or the same name in another script.

    Russian localization inserts or drops a letter (Даргерфорд for Daggerford),
    so one letter of difference on a long word is still the same name. Anything
    further apart stays a different word, so a real fabrication is still caught.
    """
    if _inflected(name,other):return True
    left,right=_translit(name),_translit(other)
    if len(left)<5 or len(right)<5:return False
    tolerance=max(1,min(len(left),len(right))//8)
    return _distance(left,right)<=tolerance

if lru_cache is not None:_same_word=lru_cache(maxsize=65536)(_same_word_cached)
else:_same_word=_same_word_cached


def name_in_quote(name,quote):
    """A proper name is evidence even where the book inflects or transliterates it.

    Word order and word count must match exactly, so an unrelated or invented
    name never passes. A translated spelling (Даргерфорд for Daggerford) is
    verified against the printed Latin form instead of being called a forgery.
    """
    words=normalized(name).split()
    if not words or not normalized(quote):return False
    quoted=normalized(quote).split()
    for start in range(len(quoted)-len(words)+1):
        if all(_same_word(w,quoted[start+i]) for i,w in enumerate(words)):return True
    return False


def sentences(text):
    """The printed sentences of a value, in order, with the closing mark kept."""
    return [part for part in re.split(r'(?<=[.!?;:])\s+', ' '.join(normalized(text).split())) if part]


def spans_in_order(value,quote):
    """True when every printed sentence of the value occurs verbatim, in order.

    A page puts headings, map notes and side text between the sentences that
    describe one place, so a faithful summary quotes them without the
    interruptions. Each sentence must still be the book's own words and must
    still appear in the book's order, and the whole value must match
    continuously when it was taken as one span. A paraphrase, a reordering or a
    sentence that is absent from the page all fail.
    """
    value=value.strip()
    if not value:return True
    if normalized(value) in quote:return True
    cursor=0
    for sentence in sentences(value):
        found=quote.find(sentence,cursor)
        if found<0:return False
        cursor=found+len(sentence)
    return True

_KIND_NOUNS={'башня':'башн','башню':'башн','башни':'башн','tower':'tower',
    'поместье':'помест','поместья':'помест','поместьем':'помест','поместью':'помест','mansion':'mansion',
    'кузня':'кузн','кузню':'кузн','кузни':'кузн','forge':'forge','smithy':'smithy',
    'пещера':'пещер','пещеры':'пещер','пещерам':'пещер','caves':'cave','cave':'cave',
    'рудник':'рудник','рудника':'рудник','mine':'mine',
    'городок':'город','городе':'город','town':'town','city':'city'}

def kind_of(name):
    """The generic place noun a printed name is built on, or ''.

    'Фамильное поместье Даффита' is a mansion, 'Пещеры кобольдов' are caves. The
    kind is what the prose uses when it shortens the name ('к поместью'), so it
    is how a short reference finds its place without guessing from sound.
    """
    for word in re.findall(r'[^\W\d_]+',_visible(name).casefold()):
        if word in _KIND_NOUNS:return _KIND_NOUNS[word]
    return ''

def place_by_kind(sentence,locations):
    """Places a sentence could mean by its place noun: one, several, or none.

    Prose shortens a name to its kind ('затем группу отводят к поместью'). When
    exactly one confirmed place has that kind the reference is that place. When
    several do, the reference is ambiguous and is reported as such instead of
    being attached to whichever place happens to come first.
    """
    words={w for w in re.findall(r'[^\W\d_]+',_visible(sentence).casefold())}
    kinds={kind_of(name) for name in locations if kind_of(name)}
    hits=[name for name in locations if kind_of(name) and kind_of(name) in _KIND_NOUNS.values()
          and any(_KIND_NOUNS.get(w)==kind_of(name) for w in words)]
    return hits


def _open_questions(unknown_settings,metadata,start):
    """What the material did not say, phrased as one useful question each.

    A missing setting is a question the Master asks in dialogue, never a reason
    to refuse the table. Only genuinely absent values are listed: a book that
    printed "для 5 уровня" has answered already, so it is not asked again.
    """
    questions=[];asked=set()
    levels={m['minimum_level'] for m in metadata}|{m['maximum_level'] for m in metadata}
    parties={m['party_size'] for m in metadata}
    for entry in unknown_settings:
        if entry['field']=='start' and start:
            continue
        asked.add(entry['field']);questions.append(dict(field=entry['field'],question=entry['question']))
    if 'level' not in asked and levels=={None}:
        questions.append(dict(field='level',question='С какого уровня начинаем?'))
    if 'party_size' not in asked and parties=={None}:
        questions.append(dict(field='party_size',question='Сколько героев за столом?'))
    return questions


def prose_roads(locations,pages):
    """Roads, their printed conditions and the ambiguities the prose leaves.

    A chapter says where the group goes in ordinary sentences, not in a route
    table: 'затем группу отводят к поместью', 'если группа решит отправиться
    дальше по лазу ... они попадут на рудник'. Each sentence is read once, its
    mover checked (a named person walking is not the party), its destination
    resolved by the place noun the prose uses, and its condition kept when the
    book prints one. Nothing is joined by similar names alone: a destination is
    either the full printed name or the one place of that kind, and a kind that
    fits several places stays ambiguous instead of picking one.
    """
    roads=[];ambiguous=[]
    # An original campaign has no source pages to read, so it derives no roads
    # from prose; only an imported book has pages to quote.
    for (document,page),text in sorted((pages or {}).items(),key=lambda row:(row[0][1] or 0)):
        for sentence in re.split(r'(?<=[.!?])\s+',text):
            if re.search(_TRAVEL_VERB,sentence,re.I) is None:continue
            if _mentions_person_travel(sentence):continue
            targets=[name for name in locations if transition_in(sentence,name) and _moves_to(sentence,name)]
            kind_hits=place_by_kind(sentence,locations)
            if not targets and len(kind_hits)==1 and _moves_by_kind(sentence,locations,kind_hits[0]):
                targets=[kind_hits[0]]
            elif not targets and len(kind_hits)>1:
                # The prose says 'к поместью' and two places are mansions: the
                # sentence does not choose between them, so neither does this.
                ambiguous.append(dict(page=page,reason='Ссылка на место по роду неоднозначна: '+', '.join(kind_hits),
                                       sentence=sentence.strip()[:300]));continue
            if len(targets)!=1:continue
            target=targets[0]
            condition=_printed_condition(sentence)
            roads.append(dict(to=target,page=page,document=document,sentence=sentence.strip()[:400],
                              condition=condition))
    return roads,ambiguous


_ARRIVAL=r'(?:прибудут|прибытие|приходят|попадают|попадут|выбираясь|прид[уе]т|приводит вас|' \
    r'приводят вас|отводят|arrives?|reaches?)'


def _moves_by_kind(sentence,locations,name):
    """True when the verb's own clause aims at a place of this name's kind.

    Prose shortens 'Фамильное поместье Даффита' to 'к поместью'. The clause that
    carries the movement must contain that place noun after a directional word,
    so a kind noun in an unrelated clause is not a road either.
    """
    kind=kind_of(name)
    if not kind:return False
    for clause in _clause_with_verb(sentence):
        low=_visible(clause)
        for word in re.findall(r'[^\W\d_]+',low):
            if _KIND_NOUNS.get(word)!=kind:continue
            index=low.find(word)
            before=low[max(0,index-40):index]
            if re.search(_DIRECTIONAL_WORD+r'\s+(?:\S+\s+){0,2}$',before):return True
    return False


def arrival_kind(sentence):
    """Which end of the sentence the party ends up at: 'to', 'from' or ''.

    A transition names both where the group leaves and where it arrives, and the
    verb decides which is which: 'из подземелья ... прибудут в поместье' arrives
    at the mansion, while 'отправятся из поместья в пещеры' leaves it. Reading
    the wrong end would walk the party backwards along the book's own road.
    """
    low=sentence.casefold()
    # 'из-за чего прибудут в поместье' is a consequence clause, not a departure:
    # a hyphenated 'из-за' is a different word from 'из'. A single-letter 'с' is
    # too common in Russian to read as 'from' at all, so it is not a marker.
    leaving=bool(re.search(r'(?:\bиз(?!-)|\bот\b)',low))
    heading=bool(re.search(r'(?:\bв\b|\bво\b|\bна\b|\bк\b|\bко\b)',low))
    if re.search(_ARRIVAL,sentence,re.I) and heading:return 'to'
    if leaving and not heading:return 'from'
    return 'to'


# The book marks its own turning points: 'развязка', 'концовка', 'после заката',
# 'когда группа'. An event is a printed consequence, not every sentence: it needs
# a marker, a place it happens in, and its own words, so a narration of the world
# is never turned into a scripted cutscene.
_EVENT_MARK=r'(?:развязка|концовка|итог|финал|прибытие на закате|после заката|' \
    r'на закате|на втором ходу|освобождение|когда группа|если группа|' \
    r'finale|conclusion|when the party|after sunset)'

def prose_events(locations,pages):
    """The turning points the pages print, with their place and condition.

    A one-shot book states its own set pieces in prose ('развязка 3: прибытие на
    закате', 'освобождение чёрного дракона из под поместья'). These are events,
    not atmosphere: each names where it happens and often what triggers it. Only
    sentences carrying such a marker become events, and each keeps the sentence
    it came from, so nothing invented is scheduled.
    """
    found=[]
    for (document,page),text in sorted((pages or {}).items(),key=lambda row:(row[0][1] or 0)):
        for sentence in re.split(r'(?<=[.!?])\s+',text):
            if re.search(_EVENT_MARK,sentence,re.I) is None:continue
            if _mentions_person_travel(sentence) and not re.search(r'групп|игрок|вы\b|party|players',sentence,re.I):
                continue
            where=[name for name in locations if name_in_quote(name,sentence)]
            kind_hits=place_by_kind(sentence,locations)
            if len(where)!=1 and len(kind_hits)==1:where=kind_hits
            if len(where)!=1:continue
            found.append(dict(location=where[0],page=page,document=document,
                              text=sentence.strip()[:400],condition=_printed_condition(sentence)))
    return found


def _printed_condition(sentence):
    """The condition the book prints for a transition, or ''.

    'если группа решит отправиться' and 'на закате' are both conditions: the road
    works when they hold, not always. The clause carrying the marker is kept
    verbatim so the world can show why a road is open or closed.
    """
    match=re.search(_CONDITION+r'[^,.;:]{0,120}',sentence,re.I)
    return match.group(0).strip() if match else ''


def derived_pairs(locations,provenance,pages=None,named=None):
    """Pairs of confirmed places where one place's own page names the other.

    A chapter prints the way on in prose ("the stair climbs to the walk", "see
    page 4") instead of listing a route record. The connection is then a fact of
    the text, not a gap. Both ends must be confirmed places and the naming must
    occur on the citing page; the pair is directional as the prose names it.

    A place is known to the analysis by its key, not by its printed name, and the
    two rarely resemble each other: the page heads a section "Кузня" while the key
    is "location_kuznya", and the mansion is printed as "Фамильное поместье
    Даффита" under the key "daffit_mansion". Comparing the printed name to the key
    therefore finds only the places that happen to share a word. ``named`` is the
    canonical name decided for every key before compiling, so the evidence of a
    place is found by the name the world actually uses.
    """
    keys_of={}
    for key,canonical in (named or {}).items():
        keys_of.setdefault(str(canonical),[]).append(str(key))
    pairs=set()
    for first in locations.values():
        proof=[]
        candidates=list(keys_of.get(first.name) or [])
        candidates.extend(str(key) for key,entry in provenance.items()
                          if isinstance(entry,dict) and same_key(first.name,str(key)))
        for key in candidates:
            entry=provenance.get(key)
            if not isinstance(entry,dict):continue
            proof=entry.get('evidence') or []
            if proof:break
        if not proof:continue
        for second in locations.values():
            if second.name==first.name:continue
            for item in proof:
                if not isinstance(item,dict):continue
                # The excerpt is what the analysis chose to quote; the page is
                # what the book prints. A road is named in prose that may stand
                # outside the quoted span, so the whole cited page is searched.
                printed=item.get('quote') or ''
                if pages:
                    printed=pages.get((item.get('document'),item.get('page'))) or printed
                if names_place(second.name,printed):
                    pairs.add((first.name,second.name));break
    return pairs


_GENERIC_PLACE_WORDS={'location','place','area','npc','room','hall','route','object','event','scene','chapter',
    'town','city','village','tower','mansion','mine','cave','caves','house','camp','castle','inn','basement','cellar',
    'городе','городок','город','башня','поместье','пещера','пещеры','кузня','дом','комната','зал'}

def same_key(name,key):
    """A place and its analysis key name the same thing: 'Bell Landing' / 'bell_landing'.

    A heading and a sentence disagree about the article: the section is called
    'The Sunken Gallery' and its key is 'sunken_gallery'. The article is not part
    of the name, so it is dropped before the comparison. A key also carries the
    kind of thing it is ('palikh_town'), and that word describes the place rather
    than identifying it, so it is allowed on either side but not required.
    """
    def words(text):
        return re.sub(r'[^0-9a-zа-яё]+',' ',normalized(text)).strip()
    def bare(text):
        return re.sub(r'^(?:the|a|an)\s+','',words(text))
    left,right=bare(name),bare(key)
    if left==right:return True
    # 'палих' / 'palikh_town': every word that identifies the place must be on
    # both sides, and the kind word may sit on either side alone.
    def distinctive(text):
        return {word for word in text.split() if word and word not in _GENERIC_PLACE_WORDS}
    one,two=distinctive(left),distinctive(right)
    if not one or not two:return bool(left==right)
    # Russian inflects place names: 'Казарина' and 'kazarin_tower' are the same.
    return all(any(_same_word(a,b) for b in two) for a in one) and \
        all(any(_same_word(a,b) for a in one) for b in two)


# A transition is the book saying that the group MOVES from here to there, with a
# condition when it prints one. English and Russian both count, because the same
# code serves every book; a travel verb alone is not enough, the place must be
# the destination of that verb.
_TRAVEL_VERB=r'(?:попадут|попадет|попадёт|попасть|попадают|отправит|отправят|отправиться|отправля|'     r'придут|придёт|придет|приходят|прибытие|прибудут|прибыть|добраться|добираются|добирают|'     r'привест|приведет|приведёт|приводит|приводят|перейти|переход|спуск|спуст|подним|вернут|возврат|'     r'проведут|провести|дойти|доходят|идут|идет|идёт|пойдут|пойти|пойдёт|пойдет|направл|'     r'отводят|отвести|отвед|ведут|ведет|ведёт|отправляются|'     r'\bto\b|towards?|reaches?|climbs?|leads?|opens? onto|overlooks?|arrives?|travels?|returns?)'
_CONDITION=r'(?:если|когда|после|как только|прежде чем|при условии|в случае|if|when|after|once|unless|before)'

_DIRECTIONAL_WORD=r'(?:в|во|на|к|ко|до|из|от|с|со|через|into|to|towards?|from|through)'

def _clause_with_verb(sentence):
    """The clauses of a sentence that contain a movement verb.

    A long line says several things: 'они направляются к ним: ваши родные мерли
    в этом же городе'. Only the clause carrying the movement verb describes a
    road, so the rest of the line cannot turn a mention of a town into one.
    """
    parts=re.split(r'[,;:]|\s[—–]\s',sentence)
    return [part for part in parts if re.search(_TRAVEL_VERB,part,re.I)]


def _moves_to(sentence,name):
    """True when this clause moves the group INTO the named place.

    The place must be led by a directional preposition inside the clause that
    carries the verb: 'приводит вас к поместью' goes to the mansion, while
    'направляются к ним' goes to people and merely mentions a town elsewhere.
    """
    target=_visible(name).strip()
    if not target:return False
    for clause in _clause_with_verb(sentence):
        low=_visible(clause)
        index=low.find(target)
        if index<0:continue
        before=low[max(0,index-40):index]
        if re.search(_DIRECTIONAL_WORD+r'\s+(?:\S+\s+){0,2}$',before):return True
    return False


def transition_in(sentence,name,source_name=''):
    """The printed sentence that moves the group into this place, or ''.

    A place is a destination only where the sentence says the group goes there:
    a travel verb must be present and this place must be its argument, not a
    bystander mentioned in the same line. A conditional marker ('если', 'when',
    'после') is reported so the road carries the condition the book printed
    instead of a road that always works.
    """
    if not name.strip() or not sentence:return ''
    if not name_in_quote(name,sentence):return ''
    if re.search(_TRAVEL_VERB,sentence,re.I) is None:return ''
    # 'казарин придёт к поместью' is a person's errand, not the group's road.
    if _mentions_person_travel(sentence):return ''
    return sentence.strip()

def _mentions_person_travel(sentence):
    """True when the mover is a named person, not the party.

    The book writes 'Казарин со своими учениками придёт к поместью': the mage
    walks, the players do not. A road that copies this would let the party be
    somewhere the book never put them, so a personal subject is refused.
    """
    low=sentence.casefold()
    if re.search(r'\bгруппа|\bигроки|\bвы\b|\bперсонаж|\bпартия|\bparty|\bplayers?',low):return False
    return bool(re.search(r'\bказарин\w*|\bдаффит\w*|\bученик\w*',low))


def _travel_sentences(quote):
    """Печатные предложения, в которых группа куда-то приходит.

    Только такое предложение может назвать место переходом: `transition_in`
    всё равно требует глагол движения и отказывает, когда идёт именной
    персонаж. Отбор здесь — не новое правило, а тот же, но один раз на
    страницу: книга называет переход в двух-трёх предложениях из сотни, а
    страницу сравнивают с каждым местом мира.
    """
    found=[]
    for sentence in re.split(r'(?<=[.!?])\s+',quote):
        if re.search(_TRAVEL_VERB,sentence,re.I) is None:continue
        if _mentions_person_travel(sentence):continue
        found.append(sentence)
    return tuple(found)


if lru_cache is not None:_travel_sentences_cached=lru_cache(maxsize=512)(_travel_sentences)
else:_travel_sentences_cached=_travel_sentences


def names_place(name,quote):
    """True when the quoted page names this place as a place, not in passing.

    Страница разбирается на предложения один раз за прогон (`lru_cache`): на
    книге из сотни мест и 128 блоков прежний вариант заново делил и читал ту же
    страницу тысячи раз, и сборка мира шла десятки минут вместо секунд.
    """
    if not name.strip() or not quote:return False
    return any(name_in_quote(name,sentence) for sentence in _travel_sentences_cached(quote))

def read_aloud(evidence):
    """Only the first marked source line is public without verified PDF box geometry."""
    return [text.strip() for proof in evidence for text in re.findall(r'(?:прочитайте|зачитайте|read aloud)[^\r\n:]{0,120}(?::|\r?\n)[ \t]*(?:\r?\n[ \t]*)?([^\r\n]+)',proof['quote'],re.I)]

_GM_ONLY=re.compile(r'\b(?:нгроки|игроки)\b[^.]{0,80}\bне знают\b|\b(?:тайный|секретн\w*|спрятан\w*|скрыт\w*|ловушк\w*|пароль|западн\w*)\b',re.I)
# A page also prints lines meant for the Master, not for the table: DCs, saves,
# staging notes and section headings. Such a sentence is not scenery even when
# it carries no hidden-fact marker, so it never becomes public either.
_GM_INSTRUCTION=re.compile(r'\bСл\s*\d|\bспасброс|\bпроверк[аеуи]\b|\bОтыгрыш\b|\bИнформация о месте\b|\bРазмеры\s+[Ии]\s+Рельеф\b|\bПримеры столкновений\b|\bКорректировка столкновения\b',re.I)
_SENTENCE=re.compile(r'[^.!?]+[.!?]?')
# The host's own prompt is not book prose: it reads like a sentence and would
# otherwise be republished as scenery ('Вы находитесь в месте «Дис».').
_HOST_PROMPT=re.compile(r'^\s*(?:Вы находитесь в месте «|Что делает ваш герой\?)',re.I)

def _place_field(place,name,default=None):
    """One field of a place, whether it is a compiled model or a stored mapping."""
    value=place.get(name) if isinstance(place,dict) else getattr(place,name,None)
    return default if value is None else value

def _dehyphenate(text):
    """Склеить перенос внутри слова и убрать невидимый мусор экстрактора.

    PDF-экстрактор режет слово мягким разрывом ('охватыва￾ет'), а TXT и MD —
    обычным дефисом в конце строки. Печатать игроку дефис посреди слова нельзя,
    поэтому разрыв между буквами исчезает, а перенос строки становится пробелом.
    """
    value=str(text or '').replace('\r','')
    value=re.sub(r'(?<=[^\W\d_])-\s*\n\s*(?=[^\W\d_])','',value)
    value=re.sub(r'(?<=[^\W\d_])[\ufffe\u00ad](?=[^\W\d_])','',value)
    value=re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]','',value)
    return value

# A sentence that belongs to the Master's half of the page: numbers with game
# units, staged conditions, treasure and the party as a game object. Such a
# sentence ends the public paragraph instead of being published: what follows a
# stat block is another section, not scenery.
_GM_STRUCTURE=re.compile(r'\bСл\s*\d|\bКД\b|\bКД\s*\d|\bхит|\bурон|\bспасброс|\bпроверк[аеуи]\b|'
    r'\bОтыгрыш\b|\bИнформация о месте\b|\bРазмеры\s+[Ии]\s+Рельеф\b|\bПримеры столкновений\b|'
    r'\bКорректировка столкновения\b|\bСокровищ\w*|\bНаград\w*|\bклад\b|\bопыт[а-я]*\b|'
    r'\bперсонаж\w*|\bпартия\b|\bпартию\b|\bигрок\w*|\bМастер\w*|\bГМ\b|\bDM\b|'
    r'\bесли\b|\bDC\s*\d|\bthe (?:characters|party|DM)\b|\bhit points\b|\battack\b',re.I)

def _page_blocks(lines):
    """Абзацы страницы: группы непустых строк между пустыми (r253)."""
    block=[]
    for line in lines:
        if line.strip():
            block.append(line)
            continue
        if block:yield block;block=[]
    if block:yield block


# Колонтитул и номер страницы — вёрстка, а не проза: печатать их игроку нельзя
# ('42 Глава 1 | Десять Городов Начало поисков', '12 Добро пожаловать на Крайний
# Север'). Строки книги переносятся как есть, поэтому начало первого абзаца на
# странице без пустых строк — это колонтитул.
_PAGE_FURNITURE=re.compile(r'^\s*\d{1,4}\s*$|^\s*\d{1,4}\s+[А-ЯЁ]|'
    r'^\s*[Гг]лава\s*[IVXLC\d]+\s*\|[^.!?]{0,40}\b\d{1,4}\s*$')
# Номерная комната ('C12. Спальня Кадрота', 'U7. Логово') — следующая сцена
# подземелья, а не продолжение абзаца.
_SECTION_HEADING=re.compile(r'^\s*[A-ZА-Я]\d{1,3}[.)]\s')


def page_paragraph_state(page_text,name,others=()):
    """Печатный абзац места и причина, если его нет (r253).

    Сначала — как раньше: место начинает абзац своим заголовком отдельной
    строкой. Русские издания печатают заголовок лишь у части мест (по книге
    r237 — у 86 из 127), поэтому если заголовка нет, берётся первый публичный
    абзац, где место названо в прозе. Границы те же: пустая строка, заголовок
    чужого места или номерная комната, половина мастера. Половина мастера
    обрывает абзац с обеих сторон: колонтитул, номер страницы и предложение
    ведущего над местом не публикуются, а текст за первым таким предложением
    уже не сцена (r237, стр. 42: указания капитану Арлагат стоят до и после
    публичной фразы о Восточной Гавани). Если книга не делит страницу пустыми
    строками, абзац начинается с фразы, где место названо.

    Возвращает `(sentences, reason)`: `reason=''` — абзац найден; `page-empty` —
    страницы нет; `page-unreadable` — страница закреплена, но её текст не
    открылся (это решает вызывающий); `no-heading` — страница не называет место
    ни заголовком, ни в прозе; `only-gm` — место названо, но публичного
    предложения с ним нет.
    """
    name=' '.join(str(name or '').split())
    if not name:return [],'no-heading'
    if not page_text or not str(page_text).strip():return [],'page-empty'
    def value_of(line):return ' '.join(line.split()).rstrip('.!?…:').casefold()
    def is_heading(line,want):
        # Номерной заголовок ('T6. Склад') — тот же заголовок без номера.
        value=value_of(re.sub(r'^\s*(?:[A-ZА-Я]?\d+|[IVXLC]+)[.)]?\s+','',line))
        if value==want:return True
        words=' '.join(line.split()).casefold()
        return words.startswith(want) and len(words)<=len(want)+10
    def is_gm(sentence):
        return bool(_GM_STRUCTURE.search(sentence) or _GM_ONLY.search(sentence))
    def as_sentences(body,split_colons=False):
        if not split_colons:return [part.strip() for part in _SENTENCE.findall(' '.join(body)) if part.strip()]
        # В запасном поиске двоеточие и точка с запятой тоже делят речь: публичная
        # фраза часто приклеена к реплике ведущего через ':' ('…к месту казни
        # Дазона: Перед ратушей собралась толпа'). Путь по заголовку остаётся
        # прежним, чтобы не менять уже проверенные сцены.
        text=' '.join(' '.join(body).split())
        return [part.strip() for part in re.split(r'(?<=[.!?;:])\s+',text) if part.strip()]
    want=value_of(name)
    stops={value_of(str(other)) for other in others if str(other or '').strip() and value_of(str(other))!=want}
    others_words=[' '.join(str(other).split()).casefold() for other in others
        if str(other or '').strip() and value_of(str(other))!=want]
    def foreign(line):
        """Строка начинает чужое место — его абзац, а не этот."""
        value=value_of(line)
        if value in stops:return True
        numbered=re.sub(r'^\s*\d{1,3}[.)]?\s+','',line)
        plain=' '.join(numbered.split()).casefold()
        candidates=[' '.join(line.split()).casefold(),plain,
            re.sub(r'^(?:в|во|на|из|к|ко|по|у|с|со|за|под|над|о|об|при|для|до|от)\s+','',plain)]
        if any(words.startswith(other) and len(words)<=len(other)+10
                for words in candidates for other in others_words):return True
        # Нумерованный пункт списка о другом месте ('4 Остерегайтесь… в Глухолесье!'):
        # номер открывает чужой пункт, даже когда название стоит в середине фразы.
        if re.match(r'^\s*\d{1,3}[.)]?\s+\S',line):
            return any(name_in_quote(str(other),numbered) for other in others if str(other or '').strip())
        return False
    lines=[line for line in _dehyphenate(page_text).split('\n')]
    index=-1
    for position,line in enumerate(lines):
        if is_heading(line,want):index=position;break
    if index>=0:
        body=[]
        for line in lines[index+1:]:
            if not line.strip():break
            if value_of(line) in stops:break
            body.append(line.strip())
        printed=as_sentences(body)
        # Заголовок без абзаца (или абзац целиком из половины мастера) не должен
        # закрывать путь прозе ниже по странице: ищем дальше.
        if printed:return printed,''
    # Место названо только в прозе: ищем публичное предложение с местом.
    mentioned=name_in_quote(name,' '.join(lines))
    for number,block in enumerate(_page_blocks(lines)):
        trim=(number==0);chunks=[];body=[]
        for line in block:
            if trim and _PAGE_FURNITURE.search(line):continue
            trim=False
            if _SECTION_HEADING.search(line):
                # Номерная комната начинает свою сцену: дальше текст соседней
                # комнаты, а не продолжение этого абзаца.
                if body:chunks.append(body)
                body=[];break
            if foreign(line):
                # Чужое место обрывает абзац; нумерованный пункт списка — это
                # ещё и начало своего пункта, поэтому с него читаем дальше.
                if body:chunks.append(body)
                body=[]
                if re.match(r'^\s*\d{1,3}[.)]?\s+\S',line):
                    body=[line.strip()]
                    continue
                break
            body.append(line.strip())
        while body and _PAGE_FURNITURE.search(body[-1]):body.pop()
        if body:chunks.append(body)
        for body in chunks:
            printed=as_sentences(body,split_colons=True)
            if not printed:continue
            gm=[position for position,sentence in enumerate(printed) if is_gm(sentence)]
            # Абзац начинается с фразы, где место названо публично: до неё могла
            # стоять половина мастера, а после неё сцена уже кончилась.
            naming=next((position for position,sentence in enumerate(printed)
                if position not in gm and name_in_quote(name,sentence)
                and sentence.rstrip('.!?…:').casefold()!=want),None)
            if naming is None:continue
            after=[position for position in gm if position>naming]
            chosen=printed[naming:after[0] if after else len(printed)]
            # Номер строки таблицы/списка ('3 В Восточной Гавани…') — вёрстка, а
            # не начало фразы; убирается только перед заглавной.
            chosen[0]=re.sub(r'^\d{1,3}[.)]?\s+(?=[А-ЯЁ])','',chosen[0])
            return chosen,''
    return [],('only-gm' if mentioned else 'no-heading')


def page_paragraph(page_text,name,others=()):
    """Печатный абзац этого места со страницы, от его заголовка вниз.

    Место в книге начинается со своего заголовка отдельной строкой, а дальше
    идёт абзац, который книга и читает вслух. Берётся только этот абзац, не вся
    страница: ниже на той же странице стоят чужие места, заметки мастеру и
    блоки характеристик. Чужое место обрывает абзац, пустая строка — тоже:
    иначе заголовок следующей сцены прилипает к последней фразе
    ('… и он уже оказывается посреди Улица Богов').

    Заголовка нет — место ищется по вхождению названия в абзац (r253,
    `page_paragraph_state`), чтобы проза страницы не терялась из-за вёрстки.
    """
    return page_paragraph_state(page_text,name,others)[0]

def observable_sentences(place,page_text='',others=()):
    """The sentences of a place the party can see on arrival.

    Only text the source itself makes public is taken: the compiled description
    when it is real prose, the place's confirmed facts and the paragraph the
    verified page prints under the place's own heading. `sealed_text` is never
    read: the compile keeps it apart from what the party may see (solo/world.py),
    and publishing it would hand the Master's half of the page to the player.

    A hidden-fact sentence is skipped; a Master's-half sentence (DC, damage,
    treasure, a staged condition, the party as a game object) ends the paragraph
    — nothing after a stat block is scenery. When nothing public remains the
    caller keeps a neutral scene rather than a false one.
    """
    name=' '.join(str(_place_field(place,'name','')).split())
    texts=[str(fact) for fact in (_place_field(place,'facts',[]) or [])[:6]]
    texts.extend(_SENTENCE.findall(str(_place_field(place,'description','') or '')))
    # The page contributes only its own paragraph, and only until the Master's
    # half starts: what follows a stat block, a hoard or a staged condition is
    # another section, and stitching it in would publish the wrong scene.
    printed=[]
    for raw in page_paragraph(page_text,name,others):
        if _GM_STRUCTURE.search(raw):break
        printed.append(raw)
    texts.extend(printed)
    kept=[];total=0
    for raw in texts:
        sentence=' '.join(str(raw).split())
        if not sentence:continue
        if _HOST_PROMPT.search(sentence):continue
        if _GM_ONLY.search(sentence) or _GM_STRUCTURE.search(sentence):continue
        if sentence[-1] not in '.!?…':sentence+='.'
        if sentence.rstrip('.!?…').casefold()==name.rstrip('.!?…').casefold():continue
        if any(sentence.casefold()==known.casefold() for known in kept):continue
        if len(sentence)>400:sentence=sentence[:400].rsplit(' ',1)[0]+'.'
        # The first sentence is always kept: a scene with no sentence at all is
        # the neutral stub this function exists to replace.
        if kept and total+len(sentence)>420:break
        kept.append(sentence);total+=len(sentence)
        if len(kept)>=5:break
    return kept

def observable_opening(place,page_text='',others=()):
    """A scene built only from what the party can perceive on arrival.

    The place's name, its declared objects and the sentences observable_sentences
    accepts are all that reaches the table. The Master's half of the page is
    never published; when nothing public remains the caller keeps a neutral
    scene rather than a false one.
    """
    name=' '.join(str(_place_field(place,'name','')).split())
    parts=[name.rstrip('.')+'.'] if name else []
    objects=[' '.join(str(item).split()) for item in (_place_field(place,'objects',[]) or [])[:12] if str(item).strip()]
    if objects:parts.append('Здесь видно: '+', '.join(objects)+'.')
    for sentence in observable_sentences(place,page_text,others):
        # The name is already the first line of the scene; repeating it as a
        # sentence ('Город Дис.') adds nothing to see.
        if any(sentence.rstrip('.!?…').casefold()==part.rstrip('.!?…').casefold() for part in parts):continue
        parts.append(sentence)
    return ' '.join(parts).strip() if len(parts)>1 or (parts and name) else ''

def public_from_read_aloud(description,quote):
    """True when a place description begins at the read-aloud marker.

    A printed read-aloud box runs over several lines, and a page may put a map
    note between two of its sentences, so a faithful description is not always
    one contiguous run. It must, however, begin exactly where the book starts
    reading: text that merely sits further down the same page is GM material and
    never becomes public just because the page also has a read-aloud box.
    """
    value=normalized(description)
    if not value:return False
    text=normalized(quote)
    for marker in re.finditer(r'(?:прочитайте|зачитайте|read aloud)[^\r\n:]{0,120}:',text,re.I):
        rest=text[marker.end():].lstrip()
        if not rest:continue
        # The description must START where the book starts reading. Prose that
        # merely occurs later on the same page is GM material, even when the page
        # also carries a read-aloud box.
        parts=sentences(description)
        if not parts or not rest.startswith(parts[0]):continue
        cursor=len(parts[0])
        for sentence in parts[1:]:
            found=rest.find(sentence,cursor)
            if found<0:break
            cursor=found+len(sentence)
        else:return True
    return False


# Words that carry no fact on their own; a retelling is judged on the content
# words it uses, not on the connective tissue that joins them.
_STOPWORDS=set('''и в во на с со у к не ни из от до по за для что как это этот эта эти его её их он она они
но а или же бы был была было были быть при над под о об про через также уже ещё еще очень который которая
которые когда где тут там свой своя свои весь вся всё все так тогда чем кто чему ним них него неё нее'''.split())

def _content_words(text):
    return [word for word in re.findall(r"[^\W\d_]+",normalized(text)) if len(word)>3 and word not in _STOPWORDS]

# Negation, actors and conditions are the parts of a sentence that word counting
# cannot see. 'Даффит живёт в одиночестве' and 'Даффит живёт не один' share every
# noun and differ in meaning; the same overlap survives a changed number, a
# swapped actor and an added condition. Each is therefore checked as its own
# fact instead of being averaged into the coverage score.
_NEGATIONS=('не','ни','без','никогда','нет','никто','ничто','not','no','never','without')
_CONDITION_WORDS=('если','когда','после','прежде','пока','лишь','только','until','unless','before','after','once','unless')
_NUMBER_WORDS={'один':1,'одна':1,'одно':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,
    'восемь':8,'девять':9,'десять':10,'одиннадцать':11,'двенадцать':12,'двадцать':20,'тридцать':30,
    'сорок':40,'пятьдесят':50,'сто':100,'двести':200,'тысяча':1000}

def _numbers_in(text):
    """Every quantity a phrase states, as digits, so '5' and 'пять' agree."""
    found=set()
    for token in re.findall(r"\d+",normalized(text)):found.add(int(token))
    for word in re.findall(r"[^\W\d_]+",normalized(text)):
        if word in _NUMBER_WORDS:found.add(_NUMBER_WORDS[word])
    return found

def _actors_in(text):
    """Capitalised words that are not the first word of the sentence.

    A name is the part of a sentence that says WHO acts. 'Даффит' and 'Казарин'
    are different stories even when every other word is identical, so the actors
    a retelling uses must be actors the page uses.
    """
    names=set()
    for sentence in re.split(r'(?<=[.!?])\s+',_visible(text)):
        words=re.findall(r"[^\W\d_]+",sentence)
        for word in words[1:]:
            if word[:1].isupper() and len(word)>2:names.add(word.casefold())
    return names

def _unknown_actors(value,quote):
    """Proper names a retelling uses that the page it cites never prints.

    The comparison cannot be 'which words are capitalised on both sides': this
    book's extracted text is lowercase throughout ('поместье не нужно было
    казарину'), so the page offers no capitals to compare with. The name a
    retelling uses is instead looked for in the cited words directly, with the
    same inflection tolerance the coverage check allows, so 'Казарин' matches
    'казарину' while a name the page never prints stays a fault.
    """
    printed=re.findall(r"[^\W\d_]+",normalized(quote))
    missing=[]
    for name in _actors_in(value):
        if name in printed or any(_inflected(name,word) for word in printed):continue
        missing.append(name)
    return sorted(missing)

def _negated(text):
    return any(word in _NEGATIONS for word in re.findall(r"[^\W\d_]+",normalized(text)))

def _conditional(text):
    words=set(re.findall(r"[^\W\d_]+",normalized(text)))
    return sorted(words & set(_CONDITION_WORDS))

def _matched_sentence(value,quote):
    """The sentence of the page a retelling is actually restating.

    A cited page holds many sentences, and a page-wide check is both too strict
    and too generous: one 'не' anywhere on the page would license a negation the
    matched sentence never states, while an unrelated clause would contradict a
    faithful retelling. The claim is compared with the sentence it restates.
    """
    wanted=set(_content_words(value))
    if not wanted:return quote
    best='';score=-1
    for sentence in re.split(r'(?<=[.!?])\s+',_visible(quote)):
        words=set(_content_words(sentence))
        current=len(wanted & words)
        if current>score:best,score=sentence,current
    return best or quote

def retelling_faults(value,quote):
    """The meaning-carrying claims a retelling states that its page does not.

    Numeric, negational, actor and conditional facts are checked one by one and
    reported as faults. Coverage alone is not evidence of support: a sentence can
    reuse every noun of the page while saying the opposite, naming another actor,
    changing a count or adding a condition the book never printed.
    """
    faults=[]
    quote=_matched_sentence(value,quote)
    stated=_numbers_in(value);printed=_numbers_in(quote)
    if stated-printed:faults.append('Число не подтверждено цитатой: '+', '.join(str(n) for n in sorted(stated-printed)))
    # Polarity is checked in one direction on purpose. A retelling may restate the
    # affirmative part of a sentence and leave a negative clause out ('поместье не
    # нужно было Казарину, Даффит жил там один' -> 'где Даффит живёт в одиночестве'):
    # that omits content. Turning an affirmative into a negation is not omission,
    # it is a claim the book never makes ('в одиночестве' -> 'не один'), so an added
    # negation is refused while a dropped one is not.
    if _negated(value) and not _negated(quote):faults.append('Отрицание не подтверждено цитатой')
    unknown=_unknown_actors(value,quote)
    if unknown:faults.append('Действующее лицо не названо цитатой: '+', '.join(unknown))
    extra=[word for word in _conditional(value) if word not in _conditional(quote)]
    if extra:faults.append('Условие не подтверждено цитатой: '+', '.join(extra))
    return faults

def supported_retelling(value,quote,threshold=0.6):
    """True when a summary's own words are the page's words, allowing inflection.

    The directive separates a literal quote from a supported fact and a
    retelling, and only a book's own wording may be presented as a quote. A
    faithful retelling still uses the page's nouns and verbs, in any case form;
    a fabrication replaces them. The measure is therefore coverage of the
    summary's content words by words of the cited page, with a shared stem
    accepted as the same word. A summary that swaps the subject matter scores
    far below the threshold, so this accepts a retelling without accepting an
    unrelated claim that merely sits next to a real quote.

    Word overlap on its own is not enough, and the tests prove it: changing a
    number, negating the sentence, swapping the actor or adding a condition all
    keep coverage above the threshold while changing what the sentence says. So
    those four facts are verified separately, and a retelling that fails any of
    them is refused however familiar its words look.
    """
    wanted=_content_words(value)
    if not wanted:return False
    printed=_content_words(quote)
    if not printed:return False
    hit=0
    for word in wanted:
        if word in printed or any(_inflected(word,other) for other in printed):hit+=1
    if hit/len(wanted)<threshold:return False
    return not retelling_faults(value,quote)

# Headings that head a section of the book rather than name a place. A page's
# first line is only a name when it is the thing itself, not a chapter marker.
_NARRATIVE_HEADS=('содержание','введение','предыстория','настоящее время','прошлое','начало приключения',
    'развязка','итог','послесловие','приложение','благодарности','персонажи игроков','карта','раздатка',
    # A one-shot's own section headings. They organise the text; they do not
    # name the place a reader stands in.
    'завязка','сюжет','события','важные персонажи','неигровые персонажи','противники','награды','сокровища',
    'места','локации','путь','дороги','правила','уровни','уровень','вступление','пролог','эпилог','итоги',
    'обзор','краткое содержание','подготовка','советы','статблоки','бои','встречи','таблицы')

def printed_heading(quote):
    """The printed name a page puts above its own text, or '' when it has none.

    A source writes the place name as the first line of its section and then
    describes it. The heading is the book's own name for the place, so it is
    preferred over a key the analysis invented; a boilerplate or narrative
    heading is not a name and is refused.
    """
    lines=[line.strip() for line in str(quote or '').splitlines() if line.strip()]
    if not lines:return ''
    # Markdown is a real source format here. Its top-level '#' heads the whole
    # document, so it names the material and never the place inside it; a place
    # is introduced by a deeper heading. Without that distinction every place in
    # a Markdown one-shot would take the document's own title as its name.
    while lines and re.match(r'#\s',lines[0]):
        lines=lines[1:]
    if not lines:return ''
    # A markdown section may be introduced by a heading, or a quote may start in
    # the middle of a body of prose. Of the headings this quote actually shows,
    # the last one that starts a section is the one its text belongs to: the
    # chapter heading further up belongs to the chapter, not to the place.
    heads=[re.sub(r'^#{2,6}\s*','',line).strip() for line in lines if re.match(r'#{2,6}\s',line)]
    candidates=[head for head in heads if head] or [' '.join(lines[0].split()).strip(' :')]
    for head in reversed(candidates):
        if not 2<=len(head)<=70:continue
        if head.endswith(('.','!','?',';','»','"')):continue
        plain=normalized(head)
        if any(plain.startswith(mark) for mark in _NARRATIVE_HEADS):continue
        if re.search(r'[^\W\d_]*\d',plain):continue
        if re.search(r'приключен|игрок|уровн|часов|систем|редакц',plain):continue
        if len(plain.split())>5:continue
        return head
    return ''
    # A cover line prints the book's own service details ('4-7часовое
    # приключение для 4-5 игроков'); a place name does not count its hours.
    if re.search(r'[^\W\d_]*\d',plain):return ''
    if re.search(r'приключен|игрок|уровн|часов|систем|редакц',plain):return ''
    # A line of prose is not a heading: a name is short and has few words.
    if len(plain.split())>5:return ''
    return head

def printed_name_for_key(key,quote):
    """A printed name for an analysis key, matched by sound not by spelling.

    The analysis keys a place 'palikh_town' while the page prints 'Палих'. The
    key's own words are compared with the words the page capitalises, allowing
    the one-letter difference a localization makes; the printed spelling wins.
    Nothing is invented: a key word with no printed counterpart yields ''.

    A possessive personal name is never a place name. The key 'kazarin_tower'
    carries the mage's name, and the page prints that name in the genitive on
    nearly every line ('мага Казарина', 'учеников Казарина'). Taking those words
    as the place's name would print a person where the book put a tower, so a
    match must be a place-shaped name: either it is followed by a place noun, or
    it stands alone as a capitalised heading, never as the object of a person.
    """
    text=_visible(str(quote or ''))
    printed=[word for word in re.findall(r'[^\W\d_]{3,}',text) if word[:1].isupper()]
    if not printed:return ''
    generic={'location','npc','route','object','event','opponent','reward','chapter','scene','place','area',
        'town','city','village','tower','mansion','mine','cave','caves','room','hall','house','camp','castle','inn',
        'городок','город','деревня','башня','поместье','пещера','пещеры','кузня','дом','комната','зал','место','район'}
    pieces=[];complete=True
    for token in re.split(r'[^0-9a-zа-яё]+',key.lower()):
        if len(token)<3 or token in generic:continue
        match=next((word for word in printed if _same_word(token,word.lower())),'')
        if not match:complete=False;break
        if match not in pieces:pieces.append(match)
    # Every distinctive word of the key must be printed. A partial match would
    # name a place after one word of its identifier and could hand a place the
    # name of a person the page also mentions.
    if not complete:return ''
    # The matched words must describe a place, not a person standing near it.
    return ' '.join(pieces) if _names_a_place(' '.join(pieces),text) else ''


_PERSON_ROLES={'мага','маг','учеников','ученик','ученика','учениками','брата','брат','братом',
    'жителей','жители','господина','господа','учителя','учитель','наставника','наставник'}


def _names_a_place(name,page):
    """True when the page attaches this name to a place, not to a person.

    'Казарина' alone is a man's name in the genitive. The same word becomes a
    place only where the page builds one from it ('башня Казарина', 'поместье
    Даффита'), so the test is what the book puts next to the name.
    """
    if not name.strip():return False
    words=_visible(page).split()
    pieces=name.lower().split()
    for start in range(len(words)-len(pieces)+1):
        if not all(_same_word(piece,words[start+i]) for i,piece in enumerate(pieces)):
            continue
        before=words[start-1] if start else ''
        after=words[start+len(pieces)] if start+len(pieces)<len(words) else ''
        # A place noun next to the name makes it a place: 'башня Казарина'.
        if before.casefold().strip('«».,:;!?()') in _PLACE_NOUNS:return True
        if after.casefold().strip('«».,:;!?()') in _PLACE_NOUNS:return True
        # Otherwise the name must not be someone's role or possession.
        if before.casefold().strip('«».,:;!?()') in _PERSON_ROLES:return False
    return False


_PLACE_NOUNS={'башня','башню','башни','поместье','поместья','поместьем','дом','дома','домом',
    'кузня','кузню','кузни','пещера','пещеры','пещерам','рудник','рудника','городок','город','города',
    'таверна','трактир','храм','церковь','мост','кладбище','лес','гора','горы','долина','улица',
    'tower','mansion','house','mine','caves','cave','town','city','inn','temple','bridge','forest','hill','vale'}


def _merge_element(previous,current,kind):
    """Combine two descriptions of one element, or report a real contradiction.

    The same book describes a place in several blocks: one block prints its name,
    another adds its description, a third lists the people there. None of them
    contradicts the others, and treating the difference as a conflict discards a
    place the pages prove. A field both blocks fill with different values is a
    genuine contradiction and still fails, so a fabrication cannot be laundered
    by restating it beside a verified fact.
    """
    left=copy.deepcopy(previous.data);right=copy.deepcopy(current.data)
    for key,value in right.items():
        if value in (None,'',[],{}) and left.get(key) not in (None,'',[],{}):continue
        if key not in left or left[key] in (None,'',[],{}):left[key]=value
        elif left[key]!=value:
            if kind=='location' and key in ('description','secrets','facts'):left[key]=left[key]
            else:return None
    merged=previous.model_copy(deep=True);merged.data=left
    if not merged.location and current.location:merged.location=current.location
    if not merged.evidence:merged.evidence=current.evidence
    merged.essential=previous.essential or current.essential
    return merged

def looks_like_key(text):
    """True when a reference is an identifier, not the name a page prints."""
    value=str(text or '').strip()
    return bool(value) and ('_' in value or value.isascii() and value.lower()!=value and ' ' not in value)

def printed_key(name):
    """A stable ASCII key for a fact recovered from a printed line.

    Book wording is not ASCII, and the entity key carries the identity used for
    de-duplication, so the readable slug is kept when it is already ASCII and a
    digest is used otherwise. The same line always produces the same key.
    """
    slug=re.sub(r'[^0-9a-z]+','-',str(name).lower()).strip('-')[:48]
    return 'printed-'+slug if slug else 'printed-'+hashlib.sha256(str(name).encode()).hexdigest()[:16]


def public_literal(text,excerpts):
    """Печатает ли книга этот текст дословно. `None` — значит «не печатает».

    Модель отвечает `"description": null` наравне с пустой строкой, и падать на
    этом нельзя: раньше один такой блок ронял сборку всего мира, и владелец
    видел «Подготовка не завершена» без причины.
    """
    if text is None:return False
    text=str(text)
    if not text.strip():return False
    return any(normalized(text) in normalized(p) for p in excerpts)


def road_pair(road):
    """The two places a transition joins, as one key, or None for a non-road.

    A road is the same road whichever end the book starts from: a page that names
    its neighbour from both sides gives 'p11→p12' and 'p12→p11', and
    ``import_package`` (`solo/world.py:306`) compares two roads exactly as this
    unordered pair. A second record of the same pair therefore makes the whole
    assembled world unimportable — the player sees 'Маршрут между узлами уже
    существует' on «Начать» instead of a campaign.
    """
    record=road if isinstance(road,dict) else road.model_dump(by_alias=True)
    start,target=record.get('from'),record.get('to')
    if not start or not target or start==target:return None
    return frozenset((start,target))


def _ends(road):
    """Both ends of a record, in the order it names them."""
    if isinstance(road,dict):return road.get('from'),road.get('to')
    return road.from_,road.to

def _allows_way_back(road):
    """Does this record let the party come back the same way?"""
    return not (road.get('one_way') if isinstance(road,dict) else getattr(road,'one_way',False))

def add_road(roads,road):
    """Keep one road per pair of places; False when the pair already has one.

    The one place that decides what a duplicate road is, so derived pairs, prose
    sentences and the analysis's own route records cannot each invent their own
    idea of it. The first record wins; which record that is belongs to the caller,
    who alone knows which evidence is stronger.

    The pair is passable in every direction its records name: a duplicate that is
    two-way, or one that names the opposite direction, widens the road instead of
    leaving a direction without a way through.
    """
    pair=road_pair(road)
    if pair is None:return False
    for existing in roads:
        if road_pair(existing)!=pair:continue
        if _allows_way_back(road) or _ends(road)==_ends(existing)[::-1]:
            if isinstance(existing,dict):existing['one_way']=False
            else:existing.one_way=False
        return False
    roads.append(road);return True


class PreparedWorldValidator:
    def __init__(self,library,documents,*,original=False,original_document=''):
        # Full printed pages, loaded once per compile. An excerpt is what the
        # analysis chose to quote; a fact the page prints elsewhere still belongs
        # to the book, so the page cache answers both grounding and the roads.
        self._page_cache=None
        self.library=library;self.documents=set(documents);self.original=original;self.original_document=original_document
    def evidence(self,entity):
        if entity.origin=='original':
            if not self.original:raise ValueError('Генерация выдана за факты приложенной книги')
            if entity.evidence:raise ValueError('У оригинального элемента не должно быть выдуманного источника')
            return []
        if not entity.evidence:raise ValueError('Нет происхождения факта')
        verified=[]
        with self.library.db() as db:
            for source in entity.evidence:
                if source.document not in self.documents:raise ValueError('Источник не выбран для этой подготовки')
                row=db.execute('SELECT content,label FROM pages WHERE document=? AND number=?',(source.document,source.page)).fetchone()
                if row is None:raise ValueError('Цитата не подтверждена страницей')
                proof=source.model_dump();proof['quote']=source_span(row['content'],source.quote)
                verified.append(dict(**proof,printed_page=row['label']))
        return verified
    def mentioned(self,name,evidence):
        """True when the cited source pages already use this place name."""
        target=normalized(name)
        if not target:return False
        pages={(p['document'],p['page']) for p in evidence}
        if not pages:return False
        with self.library.db() as db:
            for document,page in pages:
                row=db.execute('SELECT content FROM pages WHERE document=? AND number=?',(document,page)).fetchone()
                if row and name_in_quote(target,normalized(row['content'])):return True
        return False
    def page_text(self,document,page):
        """One printed page, normalised once and remembered."""
        if self._page_cache is None:self._page_cache={}
        key=(document,page)
        if key not in self._page_cache:
            with self.library.db() as db:
                row=db.execute('SELECT content FROM pages WHERE document=? AND number=?',key).fetchone()
            self._page_cache[key]=normalized(row['content']) if row else ''
        return self._page_cache[key]

    def pages_of(self,evidence):
        """The full printed text of every page an element cites.

        An excerpt is what the analysis chose to quote; the page is what the
        book prints. A name that stands further down the same page is still the
        book's own naming, so a name is checked against the page as well as
        against the excerpt, and a cross-page link is checked against both
        pages it joins.
        """
        pages={}
        for item in evidence:
            if not isinstance(item,dict):continue
            key=(item.get('document'),item.get('page'))
            if key in pages:continue
            pages[key]=self.page_text(*key)
        return pages

    def grounded(self,entity,evidence):
        """Prove every printed assertion, and report which ones were retold.

        A value either quotes the page continuously, names what the page names,
        or is a retelling built from the page's own words. The third case is
        recorded so the field is published as a supported summary rather than
        shown to the player as the book's literal wording.
        """
        if entity.origin=='original':return dict(retold=[],dropped=[])
        quote=normalized(' '.join(q['quote'] for q in evidence));retold=[]
        printed_pages=' '.join(self.pages_of(evidence).values())
        # Literal prose, identities and magnitudes must occur in the cited text.
        # Classification words and identifiers are checked by typed contracts.
        text_keys={'name','description','character','biography','motivation','mood','goals','known','private_knowledge','facts','secrets','objects','text','label','title','instruction_text'}
        # One unsupported sentence must not discard a place the page proves. A
        # single unproven statement is removed from the item and reported, so the
        # rest of the grounded content still reaches the player; an item left with
        # nothing proven at all is still a fabrication and is quarantined whole.
        dropped=[]
        def walk(value,key='',parent=None,index=None):
            if isinstance(value,dict):
                for k,v in list(value.items()):
                    if k!='source':walk(v,k,value,None)
            elif isinstance(value,list):
                for position,item in list(enumerate(value)):
                    walk(item,key,value,position)
            elif isinstance(value,str) and key in text_keys and value.strip():
                # A summary may quote several printed sentences in their printed
                # order while leaving out what stood between them, because a page
                # interleaves the place text with headings and side notes. Each
                # sentence must therefore be in the source, in order, verbatim:
                # the words are still the book's own, and nothing invented joins
                # them. A reworded or reordered summary still fails.
                if spans_in_order(value,quote):return
                # A name is an identity, and the page prints it wherever it
                # stands: an excerpt that stops before the name is still the
                # page that names the thing.
                if key=='name' and (name_in_quote(value,quote) or name_in_quote(value,printed_pages)):return
                if supported_retelling(value,quote):retold.append(key);return
                # A link between two pages states a fact neither excerpt alone
                # carries, so the summary is checked against both cited pages.
                if supported_retelling(value,printed_pages):retold.append(key);return
                dropped.append(dict(field=key,text=value[:300]))
                if parent is None:raise ValueError('Текстовое сведение не подтверждено цитатой (поле '+key+')')
                if isinstance(parent,dict):parent.pop(key,None)
                else:parent[index]=None
            elif isinstance(value,(int,float)) and not isinstance(value,bool) and not number_in_quote(value,quote):
                # A magnitude is a mechanical fact: a plausible number with no
                # printed source stays a refusal, never a quietly deleted field.
                raise ValueError('Число не подтверждено цитатой')
        walk(entity.data)
        if isinstance(entity.data,dict):
            for key,value in list(entity.data.items()):
                if isinstance(value,list) and any(item is None for item in value):
                    entity.data[key]=[item for item in value if item is not None]
            # A field the contract types as one line of prose that arrived as a
            # list is a shape the world cannot store. Dropping the field keeps
            # the place, the NPC and everything else that did verify; keeping
            # the list would fail the whole element on a formatting mistake.
            for key in ('name','character','biography','motivation','mood','known'):
                if isinstance(entity.data.get(key),list):
                    entity.data.pop(key,None)
                    dropped.append(dict(field=key,text='значение пришло списком'))
        if dropped and not any(isinstance(v,str) and v.strip() for v in entity.data.values() if not isinstance(v,(list,dict))):
            raise ValueError('Текстовое сведение не подтверждено цитатой (поле '+dropped[0]['field']+')')
        if entity.kind=='object' and entity.data.get('kind')=='door':
            lock=entity.data.get('lock','unknown')
            patterns={'locked':r'(?<!не )(?<!не)заперт|locked','unlocked':r'не\s*заперт|отпер|unlocked'}
            if lock in patterns and not re.search(patterns[lock],quote):raise ValueError('Состояние замка не подтверждено')
        return dict(retold=retold,dropped=dropped)
    def compile(self,batches,ruleset,*,module='prepared',complete=True,source_ruleset='',mechanics_ruleset=''):
        parsed=[PreparationBatch.model_validate(b) for b in batches];quarantine=[];accepted=[];keys={};conflicts=set();metadata=[]
        # Which accepted fields restate the page in their own words instead of
        # quoting it. Reported, so a retelling is never presented as the book's
        # literal wording, and so the player sees a supported summary.
        retold_fields={}
        # Every place name the analysis proposed, accepted or not, so an NPC bound to a
        # real-but-unproven place is reported as a gap rather than a fabrication.
        candidate_places={normalized(e.data.get('name')) for b in parsed for e in b.entities
            if e.kind=='location' and isinstance(e.data.get('name'),str) and e.data.get('name').strip()}
        # The name of a place is a fact about the place, not about the block that
        # mentioned it. Decided once per key before any block is compiled: the
        # heading the book prints wins, then the name a block states, then the
        # name the block's own reference used, and only then the identifier.
        from .preparation_contracts import PreparedEntity as _Entity
        location_names={}
        for batch in parsed:
            for entity in batch.entities:
                if entity.kind!='location':continue
                quote=''
                for source in entity.evidence:
                    row=self._page_text(source.document,source.page) if hasattr(self,'_page_text') else None
                    if row is None:
                        with self.library.db() as db:
                            found=db.execute('SELECT content FROM pages WHERE document=? AND number=?',(source.document,source.page)).fetchone()
                        row=found['content'] if found else ''
                    quote=quote or row or ''
                    if printed_heading(row or ''):quote=row;break
                stated=' '.join(str((entity.data or {}).get('name') or '').split())
                # The heading is read from the element's own citation, not from the
                # whole page: a page carries the headings of every section it holds,
                # so a page-wide heading would let one place take a neighbour's name.
                cited=' '.join(source.quote for source in entity.evidence)
                heading=printed_heading(cited) or printed_heading(quote)
                keyed=printed_name_for_key(entity.key,cited or quote)
                own=' '.join(str(entity.location or '').split())
                # The analysis stated the name it read, and that statement is checked
                # against the citation separately. A heading is the book's own wording
                # and outranks an identifier, but never the name the element carries.
                derived=stated or heading or (own if own and not looks_like_key(own) else '') or keyed
                if not derived:
                    slug=' '.join(str(entity.key).replace('_',' ').replace('-',' ').split())
                    derived=slug.title() if slug.islower() else slug
                previous=location_names.get(entity.key)
                if previous is None or (stated and previous!=stated and looks_like_key(previous)):
                    location_names[entity.key]=derived
        for batch in parsed:
            metadata.append(dict(title=batch.title,start=batch.start,ruleset=batch.ruleset,minimum_level=batch.minimum_level,maximum_level=batch.maximum_level,party_size=batch.party_size))
            if not self.original and any(v is not None for v in (batch.minimum_level,batch.maximum_level,batch.party_size)):
                try:
                    proof=self.evidence(PreparedEntity(key='metadata',kind='chapter',evidence=batch.metadata_evidence))
                    quote=normalized(' '.join(p['quote'] for p in proof))
                    bounds=level_bounds(quote)
                    # A book may recommend one level ("для 5 уровня") as often as a
                    # range ("уровни 1–5"), and either statement is the same fact
                    # about the same adventure. A range yields a span; a single
                    # printed level yields that one level as both ends.
                    if batch.minimum_level is not None and not any(a==batch.minimum_level for a,b in bounds):
                        raise ValueError('Минимальный уровень не подтверждён цитатой')
                    if batch.maximum_level is not None and not any(b==batch.maximum_level for a,b in bounds):
                        raise ValueError('Максимальный уровень не подтверждён цитатой')
                    if batch.party_size is not None and not party_size_in_quote(batch.party_size,quote):
                        raise ValueError('Размер группы не подтверждён цитатой')
                except ValueError as exc:quarantine.append(dict(kind='metadata',reason=validation_reason(exc),essential=True))
            for entity in batch.entities:
                try:
                    if len(json.dumps(entity.data,ensure_ascii=False))>40000:raise ValueError('Элемент слишком велик')
                    if entity.kind=='location' and entity.key in location_names:
                        # One place, one name. Two blocks of the same book describe
                        # the same place under the same key, and the second block
                        # may leave the name out or print it differently. The name
                        # is decided once for the key, so the blocks merge instead
                        # of contradicting each other.
                        entity=entity.model_copy(deep=True);entity.data=dict(entity.data,name=location_names[entity.key])
                    evidence=self.evidence(entity);proof=self.grounded(entity,evidence)
                    retold,dropped=proof['retold'],proof['dropped']
                    identity=(entity.kind,entity.key)
                    if identity in keys:
                        # A later block of the same book describes the same element
                        # in more detail, or restates it. A field the second block
                        # leaves empty does not contradict the first, so the two
                        # are merged; only a field both blocks fill differently is
                        # a real conflict, and that is reported as one.
                        merged=_merge_element(keys[identity],entity,entity.kind)
                        if merged is None:
                            conflicts.add(identity);raise ValueError('Противоречивые версии одного элемента')
                        entity=merged;keys[identity]=entity
                        accepted=[pair for pair in accepted if (pair[0].kind,pair[0].key)!=identity]
                        accepted.append((entity,evidence));continue
                    keys[identity]=entity;accepted.append((entity,evidence))
                    if retold:retold_fields[entity.key]=retold
                    if dropped:
                        quarantine.append(dict(key=entity.key,kind=entity.kind,essential=False,
                            reason='Непроверенное утверждение снято с элемента: '+', '.join(sorted({row['field'] for row in dropped}))))
                except ValueError as exc:quarantine.append(dict(key=entity.key,kind=entity.kind,reason=validation_reason(exc),essential=entity.essential or (entity.kind,entity.key) in conflicts))
        accepted=[pair for pair in accepted if (pair[0].kind,pair[0].key) not in conflicts]
        # What the pages print but the analysis summarised away. A model may
        # report "rewards are listed in a table" without listing them, or treat a
        # creature that has no statblock as no creature at all. Both are printed
        # facts, so they are recovered from the cited text itself; nothing is
        # guessed. A reward needs its number on the same printed line, a creature
        # needs an exact reviewed alias found in the page text.
        from .preparation_contracts import PreparedEntity as _PreparedEntity, SourceQuote as _SourceQuote
        from .mechanics_mapping import MONSTER_ALIASES as _ALIASES
        have={(e.kind,normalized(e.key)) for e,_ in accepted}
        quoted={}
        if self.documents:
            with self.library.db() as db:
                for row in db.execute('SELECT document,number,content FROM pages WHERE document IN (%s) ORDER BY document,number'%','.join('?'*len(self.documents)),tuple(sorted(self.documents))):
                    quoted[(row['document'],row['number'])]=row['content']
        def recover_rewards():
            found=[]
            for (document,number),text in quoted.items():
                for line in text.splitlines():
                    match=re.match(r'^[ \t]*(\D{3,120}?)[ \t]*(\d{1,6})[ \t]*(?:experience|опыт\w*|xp)\b[.,;]?[ \t]*$',line,re.I)
                    if not match:continue
                    name=' '.join(match.group(1).split())
                    if not name or normalized(name) in {n for k,n in have if k=='reward'}:continue
                    key=printed_key(name)
                    if (key,normalized(key)) in {(e.kind,normalized(e.key)) for e,_ in found}:continue
                    found.append((_PreparedEntity(key=key,kind='reward',essential=False,data=dict(name=name,experience=int(match.group(2))),
                        evidence=[_SourceQuote(document=document,page=number,quote=line.strip())]),None))
            return found
        def recover_creatures():
            # A creature is recovered from the sentence that counts it, not from a
            # loose word match: 'they are paid in salt and answer to the overseer'
            # contains 'guard' in no sense, and 'three kobolds guard the far arch'
            # is the sentence that names three opponents. Matching the alias alone
            # would also fire on unrelated prose, so the plural and the count are
            # read from the same printed line.
            found=[]
            seen={normalized(e.key) for e,_ in found}
            for (document,number),text in quoted.items():
                for sentence in re.split(r'(?<=[.!?])\s+',' '.join(text.split())):
                    bare=sentence.strip(' .,;:!?')
                    if not bare or len(bare)<8:continue
                    hit=[]
                    for wording in sorted(_ALIASES,key=len,reverse=True):
                        if len(wording)<4:continue
                        if re.search(r'\b'+re.escape(wording)+r'(?:s|es)?\b',bare,re.I):hit.append(wording)
                    if not hit:continue
                    wording=hit[0]
                    key=printed_key(wording)
                    if normalized(key) in seen:continue
                    seen.add(normalized(key))
                    count=None
                    for token,value in _COUNT_WORDS.items():
                        if re.search(r'\b'+token+r'\b\s+(?:\w+\s+){0,1}'+re.escape(wording),bare,re.I):
                            count=value;break
                    found.append((_PreparedEntity(key=key,kind='opponent',essential=False,
                        data=dict(name=wording,**({'count':count} if count else {})),
                        evidence=[_SourceQuote(document=document,page=number,quote=bare[:1000])]),None))
            return found
        for entity,_ in recover_rewards()+recover_creatures():
            if (entity.kind,normalized(entity.key)) in have:continue
            try:
                proof=self.evidence(entity);self.grounded(entity,proof)
            except ValueError as exc:
                quarantine.append(dict(key=entity.key,kind=entity.kind,reason=validation_reason(exc),essential=False));continue
            accepted.append((entity,proof))
        locations={};npcs=[];unplaced=[];unhandled=[];routes=[];objects=[];instructions={k:[] for k in ('events','tables','trackers','survival','companions','wilderness')};annotations=[];provenance={}
        annotations += [dict(key=key,kind=next((e.kind for e,_ in accepted if e.key==key),''),field='retold',
            reason='Сведение пересказано словами источника; дословная цитата не выдаётся',fields=fields)
            for key,fields in retold_fields.items()]
        # Extracted opponents and rewards are the two kinds that used to stop at
        # "no handler". Both now go through the shared mapping: a resolved
        # monster becomes an encounter actor, a printed reward becomes a priced
        # rule, and anything unresolved is reported instead of dropped.
        creatures=[];payments=[]
        models={'event':('events',ScenarioEvent),'table':('tables',RollTable),'tracker':('trackers',Tracker),'survival':('survival',SurvivalRule),'companion':('companions',Companion),'wilderness':('wilderness',WildernessProcedure)}
        for entity,evidence in accepted:
            try:
                data=copy.deepcopy(entity.data)
                if entity.kind=='location':
                    # An analysis keys a place ('sunken_gallery') and may leave the
                    # name to the key, because the key already is one. The heading
                    # printed on the page is the better name, and both are the same
                    # place, so the missing field is filled from the key instead of
                    # discarding a location the book clearly describes.
                    if not str(data.get('name') or '').strip():
                        # The page heads its own section with the place's printed
                        # name, and the analysis key is the same place spelled its
                        # own way. The printed heading is authoritative; the key
                        # is only a fallback, because a key is an identifier and
                        # never the book's wording.
                        printed=printed_heading(evidence[0]['quote']) if evidence else ''
                        named=printed_name_for_key(entity.key,evidence[0]['quote']) if evidence else ''
                        # What the analysis called the place in its own reference
                        # field is still a name a page printed, so it beats an
                        # identifier; it never overrides a printed heading.
                        own=' '.join(str(entity.location or '').split())
                        if printed:data['name']=printed
                        else:
                            guessed=named or (own if own and not looks_like_key(own) else '') or \
                                ' '.join(str(entity.key).replace('_',' ').replace('-',' ').split())
                            if guessed:data['name']=guessed.title() if guessed.islower() else guessed
                    data.update(npc=[],interactions=[],known_to_hero=False)
                    place=WorldLocation.model_validate(data)
                    if entity.origin=='source':
                        # Raw book prose is GM material. Only explicit read-aloud
                        # excerpts can become initially observable descriptions.
                        public=read_aloud(evidence)
                        starts=public_from_read_aloud(place.description,evidence[0]['quote']) if evidence else False
                        if place.description and not (public_literal(place.description,public) or starts):
                            # The prose stays GM-only, but it is not discarded: the
                            # place keeps the words the book printed for it, so the
                            # opening scene can be described in the book's own terms
                            # instead of a stub, and a GM turn has something to tell.
                            place.sealed_text=place.description[:2000]
                            place.secrets=[*place.secrets,place.description[:1000]][:80];place.description=''
                        place.secrets=[*place.secrets,*place.facts,*place.objects][:80];place.facts=[];place.objects=[]
                    duplicate=next((n for n in locations if same_key(n,place.name)),None)
                    if duplicate:
                        # Two blocks of one book describe one place. Fields the
                        # second block leaves empty describe nothing, so they do
                        # not contradict the first; a field both blocks fill
                        # differently is reported, not used to delete the place.
                        existing=locations[duplicate]
                        a=existing.model_dump();b=place.model_dump();a.pop('name');b.pop('name')
                        if a!=b:
                            clash=[key for key in a if a[key]!=b.get(key) and b.get(key) not in (None,'',[],{})]
                            annotations.append(dict(key=entity.key,kind='location',field='duplicate',
                                reason='Второй блок описывает это место иначе; принят первый проверенный вариант',
                                fields=clash))
                            place=existing
                        provenance[entity.key]=dict(kind=entity.kind,origin=entity.origin,evidence=evidence,canonical_name=duplicate)
                        continue
                    locations[place.name]=place
                elif entity.kind=='npc':
                    npc=WorldNPC.model_validate(data)
                    if entity.origin=='source':
                        public=read_aloud(evidence)
                        npc.present=public_literal(npc.name,public)
                        if not npc.present or not public_literal(npc.character,public):
                            npc.private_knowledge=[*npc.private_knowledge,*([npc.character] if npc.character else [])][:20];npc.character=''
                        if not public_literal(npc.mood,public):npc.mood=''
                    npcs.append((entity,npc))
                elif entity.kind=='route':
                    if entity.origin=='source' and not public_literal(data.get('description',''),read_aloud(evidence)):data['description']='Путь'
                    routes.append((entity,data))
                elif entity.kind=='object':
                    if entity.origin=='source':
                        public=read_aloud(evidence);data['visible']=public_literal(data.get('name',''),public)
                        if not public_literal(data.get('description',''),public):data['description']=''
                    # An analysis names a thing and may leave out the identifier the
                    # scene contract needs. The key it already chose for the element
                    # is one, and it obeys the same rule, so the missing id is filled
                    # from it instead of discarding an object the page prints. Nothing
                    # else is invented: an object without a printed name still fails.
                    if not str(data.get('id') or '').strip():data['id']=entity.key
                    objects.append((entity,data))
                elif entity.kind in models:
                    target,model=models[entity.kind]
                    if entity.origin=='source':
                        public=read_aloud(evidence)
                        # A quotation proves its existence, not permission to show
                        # a GM paragraph. Public scripted prose needs separate proof.
                        effects=list(data.get('effects',[]))
                        if entity.kind=='table':effects += [effect for row in data.get('rows',[]) for effect in row.get('effects',[])]
                        if any(effect.get('public') and effect.get('text') and not public_literal(effect['text'],public) for effect in effects):
                            raise ValueError('Публикация текста события не подтверждена открытым абзацем источника')
                    if not evidence:
                        # Explicitly labelled authored content is not an official citation.
                        if not self.original_document:raise ValueError('Авторский источник не зарегистрирован')
                        data['source']=dict(document=self.original_document,page=1,review='confirmed',layer='house_rule')
                    else:data['source']=dict(document=evidence[0]['document'],page=evidence[0]['page'],printed_page=evidence[0]['printed_page'],review='confirmed')
                    instructions[target].append(model.model_validate(data))
                elif entity.kind=='media':
                    # No arbitrary path or URL supplied by a model becomes a playable asset.
                    raise ValueError('Связь с проверенным медиаресурсом ещё не подтверждена')
                elif entity.kind=='opponent':
                    # An extracted monster is content, not an actor. The shared
                    # mapping decides whether the pinned corpus really has it; a
                    # phrase with no exact entry waits for adaptation rather than
                    # becoming a lookalike monster or a dropped one.
                    from .mechanics_mapping import resolve_monster
                    wording=' '.join(str(data.get('name') or data.get('monster') or '').split())
                    substitution=resolve_monster(wording,evidence[0]['page'] if evidence else None)
                    if substitution.target and substitution.basis:
                        creatures.append((entity,substitution,data))
                        provenance[entity.key]=dict(kind=entity.kind,origin=entity.origin,evidence=evidence,target=substitution.target)
                    else:
                        quarantine.append(dict(key=entity.key,kind='opponent',reason=substitution.reason,essential=False))
                elif entity.kind=='reward':
                    # A reward the book prints is data with its page. A number is
                    # only taken when it survives grounded(): it must occur in the
                    # cited quote, so a plausible price cannot be invented here.
                    from .progression import RewardRule
                    amount=data.get('experience',data.get('amount'))
                    if amount is not None and not isinstance(amount,int):
                        amount=None
                    rule=dict(id='prepared-'+str(entity.key).lower().replace('_','-')[:60],
                              amount=amount,page=evidence[0]['page'] if evidence else 1,
                              reason='Награда извлечена из источника и подтверждена цитатой.')
                    payments.append((entity,rule))
                else:
                    # Opponents, rewards and special rules are real extracted content,
                    # but Nat20 has no registered handler for them. Report the gap as a
                    # non-blocking limitation instead of discarding the whole world.
                    annotations.append(dict(key=entity.key,kind=entity.kind,data=data,evidence=evidence,origin=entity.origin))
                    # A rule that rewrites the game must never be dropped silently, so a
                    # missing handler vetoes. Monsters, rewards and chapter labels are
                    # content Nat20 expresses elsewhere; they are reported, not fatal.
                    unhandled.append(dict(key=entity.key,kind=entity.kind,essential=entity.essential and entity.kind=='special_rule'))
                provenance[entity.key]=dict(kind=entity.kind,origin=entity.origin,evidence=evidence)
                if entity.kind=='location':
                    # Имя, под которым место вошло в мир. Ключ анализа бывает
                    # латиницей и не совпадает с напечатанным названием; без
                    # этой записи вход не находит цитату стартовой сцены и
                    # никогда не открывается, хотя мир проверен.
                    resolved=next((name for name in locations if same_key(name,str(data.get('name') or ''))),'')
                    if resolved:provenance[entity.key]['name']=resolved
            except ValueError as exc:quarantine.append(dict(key=entity.key,kind=entity.kind,reason=validation_reason(exc),essential=entity.essential))
        # No registered handler means the element cannot execute, so it is reported
        # as a limitation rather than silently dropped. Only an unhandled rule vetoes.
        for row in unhandled:
            quarantine.append(dict(key=row['key'],kind=row['kind'],reason='Механический обработчик этого элемента не зарегистрирован',essential=row['essential']))
        # A reference names a place either by the name the book prints or by the
        # analysis key the batch used for it. Both refer to the same place, so a
        # reference is resolved through the alias instead of being called unknown.
        aliases={}
        # The canonical name of every place key, decided before compiling, is the
        # most reliable alias there is: the analysis key and the printed name are
        # two spellings of one place. A merged duplicate contributes its own keys
        # on top, because a second block may key the same place differently.
        for key,canonical in location_names.items():
            if canonical in locations:aliases[normalized(key)]=canonical
        for key,entry in provenance.items():
            if not isinstance(entry,dict) or entry.get('kind')!='location':continue
            canonical=entry.get('canonical_name') or next((name for name in locations if same_key(name,str(key))),'')
            if canonical:aliases[normalized(key)]=canonical
        def resolve_place(reference):
            text=' '.join(str(reference or '').split())
            if not text:return ''
            exact=[name for name in locations if normalized(name)==normalized(text)]
            if len(exact)==1:return exact[0]
            alias=aliases.get(normalized(text))
            if alias in locations:return alias
            similar=[name for name in locations if same_key(name,text)]
            return similar[0] if len(similar)==1 else ''
        for entity,npc in npcs:
            place=resolve_place(entity.location)
            if not place:
                proof=provenance.get(entity.key,{}).get('evidence',[])
                linked={n for n in locations for p in proof if re.search(re.escape(normalized(npc.name))+r'.{0,100}\b(?:находится|стоит|живёт|живет)\s+(?:в|у)\s+'+re.escape(normalized(n))+r'(?!\w)',normalized(p['quote']))}
                if len(linked)==1:place=linked.pop();provenance[entity.key]['local_binding']=place
            if place not in locations:
                # Reject an unsupported location claim, not the whole adventure.
                # The NPC itself passed validation above; an invalid relation must
                # not invent a place or teleport the NPC into the opening scene.
                # Keep its provenance for later clarification and quarantine the
                # relation as a local, non-blocking gap.
                named=bool(entity.location.strip())
                known=named and (normalized(entity.location) in candidate_places or self.mentioned(entity.location,provenance.get(entity.key,{}).get('evidence',[])))
                invented=named and not known
                # The relation is quarantined, never the adventure. An NPC being
                # essential means the story needs that NPC, not that it needs a
                # guessed address for him: the hero can still meet him, and the
                # missing place is a question asked when a scene depends on it.
                # Carrying entity.essential here closed every adventure whose only
                # gap was one unconfirmed location, beside confirmed ones.
                quarantine.append(dict(key=entity.key,kind='npc',reason='Местонахождение NPC не подтверждено источником',essential=False))
                provenance[entity.key].update(location_status='unresolved',location_claim=entity.location,
                    location_claim_rejected=invented)
                if not invented:unplaced.append(npc)
                continue
            # No NPC private motivation is published as observed appearance.
            npc.private_knowledge=[*npc.private_knowledge,*npc.goals,*([npc.motivation] if npc.motivation else []),*([npc.biography] if npc.biography else []),*([npc.known] if npc.known else [])][:20]
            npc.goals=[];npc.motivation='';npc.biography='';npc.known='';locations[place].npc.append(npc)
        from .interactions import SceneObject
        from .inventory import CATALOG,new_item
        for entity,data in objects:
            try:
                place=resolve_place(entity.location)
                if place not in locations:raise ValueError('Неизвестная локация объекта')
                if data.get('kind')=='item':
                    item=data.get('item',{});catalog=item.get('catalog')
                    if catalog not in CATALOG:raise ValueError('Неизвестный предмет')
                    data['item']=new_item(catalog,item.get('quantity',1),'scene','Подготовленное приключение')
                locations[place].interactions.append(SceneObject.model_validate(data))
            except ValueError as exc:quarantine.append(dict(key=entity.key,kind='object',reason=validation_reason(exc),essential=entity.essential))
        nodes=[WorldNode(id='p'+str(i),name=name,coordinates=[i%10*2,i//10*2],known=False) for i,name in enumerate(locations)]
        ids={n.name:n.id for n in nodes};linked=[]
        def node_of(name):
            """The node a source names, tolerating the article a page may print.

            A chapter writes 'the Overseer Walk' in one sentence and heads the
            section 'The Overseer Walk', while the place itself is 'Overseer Walk'.
            The article is not part of the name, so a road whose end is spelled
            with it is the same road, not an unknown place.
            """
            text=str(name or '').strip()
            if text in ids:return ids[text]
            bare=re.sub(r'^(?:the|a|an)\s+','',text,flags=re.I)
            for known_name,node_id in ids.items():
                if re.sub(r'^(?:the|a|an)\s+','',known_name.strip(),flags=re.I).casefold()==bare.casefold():
                    return node_id
            return None
        # A printed route record is the strongest evidence of a road: it carries the
        # distance and the description the book states. It takes its pair of places
        # first, and the derived mention and the prose sentence then fill in only the
        # pairs no record covers. That order is also the whole duplicate rule — see
        # `add_road` — because a book prints the same road twice as well (a table and
        # a chapter, or both ends of one road listed apart), and a world with two
        # roads between the same places cannot be imported at all.
        for entity,data in routes:
            try:
                start_id=node_of(resolve_place(data.get('from')) or data.get('from'))
                end_id=node_of(resolve_place(data.get('to')) or data.get('to'))
                if not start_id or not end_id:raise ValueError('Неизвестный конец маршрута: '+repr((data.get('from'),data.get('to'))))
                if start_id==end_id:
                    # A road from a place to itself is no road, and import_package
                    # rejects the whole world for it: reported, never added.
                    annotations.append(dict(key=entity.key,kind='route',field='loop',
                        reason='Маршрут из места в само себя не добавлен'))
                    continue
                # A printed link between two places is a real transition even when the
                # book prints no distance: chapters say 'the stair climbs to the walk'
                # and leave the metres to the table. Such a link takes the shortest
                # legal step instead of vanishing, and the world records that the
                # step is assumed rather than quoted, so nothing pretends the book
                # printed a number it did not.
                data=dict(data)
                assumed=[]
                if not isinstance(data.get('meters'),int) or isinstance(data.get('meters'),bool):
                    data['meters']=ASSUMED_STEP_METERS;assumed.append('meters')
                if not isinstance(data.get('minutes'),int) or isinstance(data.get('minutes'),bool):
                    data['minutes']=ASSUMED_STEP_MINUTES;assumed.append('minutes')
                if not str(data.get('description') or '').strip():data['description']='Путь'
                data={**data,'from':start_id,'to':end_id}
                if not add_road(linked,WorldRoute.model_validate(data)):
                    annotations.append(dict(key=entity.key,kind='route',field='duplicate',
                        reason='Второй маршрут между теми же местами не добавлен: между двумя местами одна дорога'))
                    continue
                if assumed:annotations.append(dict(key=entity.key,kind='route',field='step',
                    reason='Книга не печатает расстояние для этого перехода; принят минимальный шаг',assumed=assumed))
            except ValueError as exc:quarantine.append(dict(key=entity.key,kind='route',reason=validation_reason(exc),essential=entity.essential))
        # A chapter often prints the way on in prose ("the stair climbs to the walk",
        # "see page 4") instead of listing a route record. The connection is then a
        # fact of the text, not a gap: it is derived from the cited page of one place
        # naming the other, and it is marked as derived so a reader can tell it from a
        # route the analysis proposed outright. Nothing is invented: both ends must be
        # confirmed places and the naming must be on the citing page.
        named_pairs=derived_pairs(locations,provenance,self._page_cache,location_names)
        for source_name,target_name in sorted(named_pairs):
            link=WorldRoute(**{'from':ids[source_name],'to':ids[target_name],
                'meters':ASSUMED_STEP_METERS,'minutes':ASSUMED_STEP_MINUTES,'timing':'scenario',
                'description':'Путь','known':True})
            joined=add_road(linked,link)
            # A pair already covered by a stronger record keeps this evidence as a
            # note: the page still names the way on, and that is worth knowing.
            annotations.append(dict(key=source_name,kind='route',field='derived',joined=joined,
                reason='Переход назван на странице места; расстояние не напечатано' if joined
                    else 'Переход назван на странице места; дорога уже добавлена другой записью'))
        # The book also states where the group goes in ordinary prose. Each such
        # sentence becomes a road from the place whose page prints it, carrying
        # the condition the book printed. A reference the prose leaves ambiguous
        # is reported, never resolved by picking a likely place.
        roads,ambiguous=prose_roads(list(locations),self._page_cache)
        for note in ambiguous:
            quarantine.append(dict(key=None,kind='route',essential=False,
                reason='Неоднозначный переход не соединён: '+note['reason']))
            annotations.append(dict(key=None,kind='route',field='ambiguous',
                reason='Переход по роду места не выбран',detail=note))
        for road in roads:
            target_name=road['to']
            # The sentence itself may name the place it starts from: 'из пещер ...
            # прибудут в поместье'. Where it does, that end is the source and the
            # page it stands on is only where the sentence was printed.
            named_here=[name for name in locations
                        if name!=target_name and name_in_quote(name,road['sentence'])]
            source_name=named_here[0] if len(named_here)==1 else ''
            if not source_name:
                source_name=next((name for name,entry in provenance.items()
                                  if isinstance(entry,dict) and entry.get('kind')=='location'
                                  and any(item.get('page')==road['page'] for item in (entry.get('evidence') or []))),'')
                source_name=resolve_place(source_name) if source_name else ''
            if not source_name or source_name==target_name:
                # A sentence can stand on the destination's own page or in a chapter
                # section rather than beside the place it starts from: 'и вот мощеная
                # дорога приводит вас к поместью', 'жители просят пойти в поместье'.
                # The page then names where the group arrives but not where it
                # leaves, and the book's opening place is where it leaves from.
                # Recorded as such, so the road is never mistaken for one the book
                # printed with both ends.
                if road['page'] and arrival_kind(road['sentence'])=='to':
                    # The book's opening place, resolved the same way the world
                    # resolves its start; a road may not invent where it begins.
                    source_name=next((name for name in (resolve_place(m['start']) for m in metadata)
                                      if name in locations),'')
            if not source_name or source_name==target_name:continue
            description='Переход, названный в тексте'
            if road['condition']:description+='; условие: '+road['condition']
            link=WorldRoute(**{'from':ids[source_name],'to':ids[target_name],
                'meters':ASSUMED_STEP_METERS,'minutes':ASSUMED_STEP_MINUTES,'timing':'scenario',
                'description':description[:400],'known':True})
            joined=add_road(linked,link)
            # The sentence is kept even when the pair is already covered: the
            # condition the book printed lives only here, and dropping it would
            # silently lose «если…» from the world.
            annotations.append(dict(key=source_name,kind='route',field='prose',joined=joined,
                reason='Переход описан прозой страницы' if joined
                    else 'Переход описан прозой страницы; дорога уже добавлена другой записью',
                to=target_name,condition=road['condition'],sentence=road['sentence']))
        title=next((m['title'] for m in metadata if m['title']),'Новое приключение')
        start=next((name for name in (resolve_place(m['start']) for m in metadata) if name in locations),'')
        # The edition a model claims per block is a proposal, not evidence: the same
        # book yields 2014 and 2024 labels across blocks. The player's explicit choice
        # is authoritative, and a mismatch is reported without discarding the world.
        editions={m['ruleset'] for m in metadata if m['ruleset']!='unknown'}
        source_edition=next(iter(editions)) if len(editions)==1 else ''
        # Two separate facts. `ruleset` is the mechanics preset the player runs, and it
        # alone gates execution. `source_ruleset` is the edition the book itself was
        # written for. They legitimately differ, so the difference is recorded, not
        # treated as a fault.
        book_edition=source_ruleset or source_edition
        mechanics=mechanics_ruleset or ruleset
        if editions and editions!={mechanics}:
            quarantine.append(dict(kind='ruleset',reason='Редакция исходного текста: '+', '.join(sorted(editions))+'; механика игры: '+mechanics+'. Текст сохраняется как есть, механика считается по '+mechanics+'.',essential=False))
        # A missing starting setting is a question for the Master, not a refusal to
        # play. The material may say nothing about where the story opens, and the
        # player may still sit down and agree it in dialogue. Only a page that does
        # not yield its text stays a real blocker.
        unknown_settings=[]
        if not start:
            unknown_settings.append(dict(field='start',question='С какого места начинаем?',
                reason='Место начала не названо материалом'))
            quarantine.append(dict(kind='start',reason='Место начала не названо материалом; Мастер уточнит его в диалоге',essential=False))
        if not complete:quarantine.append(dict(kind='source',reason='Есть страницы без доступного текста',essential=True))
        # An original premise is a wish, not a book: it needs no two linked places
        # and no printed NPC, because the world is created around the player. An
        # imported book keeps the stricter bar, since there the pages promise play.
        if self.original and (len(locations)<2 or not linked or not any(p.npc for p in locations.values())):
            quarantine.append(dict(kind='playable',reason='Мало готовых деталей мира: Мастер дополнит их в диалоге',essential=False))
        if not locations:
            quarantine.append(dict(kind='locations',reason='Не получено ни одной проверенной локации',essential=True))
            definition=AdventureDefinition(id=module,title=title,ruleset=mechanics,ruleset_version=1,
                source_ruleset=book_edition,source_ruleset_version=1,reviewed=False)
            return dict(unknown_settings=unknown_settings,questions=_open_questions(unknown_settings,metadata,''),definition=definition.model_dump(by_alias=True),provenance=provenance,quarantine=quarantine,annotations=annotations,start='',title=title,ready=False,source_presence_verified=True,semantic_model_validation=False)
        # Only the selected starting place is initially known; source notes stay GM-only.
        # A place the book names on a printed road is a place the hero has heard of:
        # the name is on the page and the road leads to it. The node stays unvisited
        # and its prose stays private, so the party can walk on without the world
        # handing out a description the source never read aloud. A road the source
        # keeps hidden keeps both of its ends hidden as well.
        if start:
            # A route stores node ids, so the ends are collected as ids first and only
            # then turned into the names a reader recognises.
            noted=set()
            for route in linked:
                record=route.model_dump()
                if record.get('known',True):noted.update(filter(None,(record.get('from'),record.get('to'))))
            named={node.name for node in nodes if node.id in noted}|{start}
            for node in nodes:node.known=node.name in named
            # The place carries the same fact as its node: the hero has heard of it.
            # Its printed prose is still not read aloud -- an imported place publishes
            # a neutral stub until the party stands there and observes the page, so
            # being named on a road never hands out a description.
            for name in named:
                if name in locations:locations[name].known_to_hero=True
            # The opening scene is the one place the party is standing in, and a
            # world whose start prints no read-aloud box must still open with
            # something to see. What it must NOT do is publish the sealed prose:
            # that paragraph mixes ordinary scenery with hidden facts (a cache
            # behind a wall, a motive, a trap), so copying it in by whole opens
            # every secret at once. Only explicit observable material is used.
            if start in locations and not locations[start].description.strip():
                opening=observable_opening(locations[start])
                if opening:locations[start].description=opening[:1500]
        constraints={}
        for target,key in [('minimum_level','minimum_level'),('maximum_level','maximum_level'),('recommended_party','party_size')]:
            values={m[key] for m in metadata if m[key] is not None}
            if len(values)>1:quarantine.append(dict(kind='metadata',reason='Противоречивые ограничения приключения: '+key,essential=True))
            elif values:constraints[target]=values.pop()
        # The analyzer's own notes are about what the book does NOT print: no
        # printed distances, no statblocks, no level range. That is a true report
        # about the source, not a defect in the extraction, so it is shown as an
        # adaptation note and does not close a world whose opening scene is
        # confirmed. A real failure is reported by the checks that own it.
        if any(b.problems for b in parsed):
            quarantine.append(dict(kind='coverage',reason='Анализатор отметил то, чего книга не печатает; сведение показано как адаптация',essential=False))
        # A resolved opponent becomes an encounter the world already offers, so a
        # prepared book reaches a fight without a hand-written episode module.
        from .maps import MapDescription
        from .world import WorldEvent
        world_events=[]
        # One encounter per creature and place. An analysis that names 'kobolds'
        # and a page that prints 'Three kobolds guard the far arch' describe the
        # same group, so the second reading must not offer the fight twice.
        offered=set()
        # Which place a cited page describes. A location's evidence is the page it
        # is printed on, so a creature cited to that page stands in that place
        # even when the analysis left its own location field empty.
        # The page a place is printed on, keyed by the analysis key that cited it.
        # Comparing the finished name with the key would miss every place the book
        # renamed: 'kazarin_tower' compiles to 'Уединённый домик в горах', and a
        # creature cited to that page must still stand there. The key-to-name map
        # built for the locations is the same identity, so it is reused.
        page_place={}
        for key,entry in provenance.items():
            if not isinstance(entry,dict) or entry.get('kind')!='location':continue
            name=location_names.get(str(key)) or entry.get('canonical_name') or ''
            if name not in locations:continue
            for quote in entry.get('evidence') or []:
                if isinstance(quote,dict) and quote.get('page') is not None:
                    page_place.setdefault((quote.get('document'),quote.get('page')),name)
        def place_on_page(evidence):
            for quote in evidence or []:
                if isinstance(quote,dict):
                    found=page_place.get((quote.get('document'),quote.get('page')))
                    if found:return found
            return ''
        for entity,substitution,data in creatures:
            place=resolve_place(entity.location)
            if not place:
                # An opponent named inside a place's own paragraph belongs to that
                # place even when the analysis left the field empty: the cited page
                # is the evidence of where the creature stands.
                place=place_on_page(provenance.get(entity.key,{}).get('evidence',[]))
            place=place or start
            if not place:
                quarantine.append(dict(key=entity.key,kind='opponent',reason='Противнику не назначено подтверждённое место',essential=False))
                continue
            count=data.get('count',1)
            count=count if isinstance(count,int) and 1<=count<=20 else 1
            identity=(substitution.target,place)
            if identity in offered:
                annotations.append(dict(key=entity.key,kind='opponent',field='duplicate',
                    reason='Этот противник уже предложен в этом месте'))
                continue
            offered.add(identity)
            node=next((n for n in nodes if n.name==place),None)
            environment=node.environment if node else 'room'
            encounter=WorldEvent(id='foe-'+str(entity.key)[:60],kind='encounter',location=place,
                text='Противник из источника: '+substitution.source,
                encounter=MapDescription(environment=environment))
            world_events.append(encounter)
            # A page that names a monster often also prints how to pass it. The
            # condition travels with the place so a GM can offer the way around,
            # and the encounter stays an offer: naming a creature never makes the
            # fight obligatory.
            condition=' '.join(str(data.get('condition') or '').split())
            if condition and place in locations:
                locations[place].secrets=[*locations[place].secrets,condition[:1000]][:80]
                annotations.append(dict(key=entity.key,kind='opponent',field='condition',
                    reason='Условие, при котором бой можно не начинать',quote=condition[:400]))
            provenance[entity.key]={'kind':'opponent','target':substitution.target,'count':count,
                'source':provenance.get(entity.key,{}).get('evidence',[])}
        # Book rewards travel with the definition, priced only where the source
        # proved the amount; the rest keep their page and wait for adaptation.
        from .progression import Progression, RewardRule
        rewards=[]
        for entity,rule in payments:
            try:rewards.append(RewardRule(**rule))
            except ValueError as exc:quarantine.append(dict(key=entity.key,kind='reward',reason=validation_reason(exc),essential=False))
        progression=Progression(mode='experience',rewards=rewards) if rewards else None
        # The book's printed turning points become timed events in the place they
        # name. A 'развязка' sentence is a real consequence the world can reach,
        # and it is scheduled with the condition the page printed beside it.
        for entry in prose_events(list(locations),self._page_cache):
            place=entry['location']
            event_id='prose-'+str(len(world_events)+1)
            if any(event.id==event_id for event in world_events):continue
            world_events.append(WorldEvent(id=event_id,kind='notice',location=place,
                text=entry['text'][:800]))
            annotations.append(dict(key=place,kind='event',field='prose',
                reason='Поворотный момент, напечатанный в книге',page=entry['page'],
                condition=entry['condition'],text=entry['text'][:300]))
        package=ScenarioPackage(name=title,source='campaign-prepare-v1',locations=list(locations.values()),nodes=nodes,routes=linked,events=world_events)
        try:definition=AdventureDefinition(id=module,title=title,ruleset=mechanics,ruleset_version=1,
            source_ruleset=book_edition,source_ruleset_version=1,world=package,reviewed=not any(q['essential'] for q in quarantine),**constraints,**instructions,**({'progression':progression} if progression else {}))
        except ValueError:
            quarantine.append(dict(kind='dependencies',reason='Не проверены ссылки или зависимости механических инструкций',essential=True))
            definition=AdventureDefinition(id=module,title=title,ruleset=mechanics,ruleset_version=1,
                source_ruleset=book_edition,source_ruleset_version=1,world=package,reviewed=False)
        # Keep incomplete definitions out of the launchable registry.
        questions=_open_questions(unknown_settings,metadata,start)
        return dict(definition=definition.model_dump(by_alias=True),provenance=provenance,quarantine=quarantine,annotations=annotations,start=start,title=title,ready=definition.reviewed,source_presence_verified=True,semantic_model_validation=False,source_edition=source_edition,unknown_settings=unknown_settings,questions=questions)
