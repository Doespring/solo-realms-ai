"""Разбор реплики игрока на части: осмотр, вопрос Мастеру, разговор с NPC.

Аудит 08.11 (F08): «что вокруг, какие выходы, кто присутствует?» уходил в
разговор, получал «Здесь нет доступного собеседника», и составное намерение
целиком отменялось из-за одной части. Здесь намерения разделены:

* ``observe`` — осмотр и вопросы о самой сцене («что вокруг», «какие выходы»,
  «кто присутствует»). Собеседник не нужен: это вопрос к обстановке;
* ``talk`` — герой обращается к названному человеку. Только этой части нужна
  цель, и только она может не найти собеседника;
* ``ask_master`` — вопрос ведущему о правилах и о том, чего в сцене нет;
* ``other`` — всё остальное (механика, предметы, движение); этим занимается
  движок, и такие реплики сюда не подменяются.

Составная реплика не отменяется целиком: выполненная часть остаётся в
результате, невыполненная называется по-русски отдельной строкой.
"""
import re

OBSERVE = re.compile(
    r'\b(?:осматрива\w*|осмотре\w*|осмотр\w*|оглядыва\w*|оглядел\w*|оглянут\w*|озира\w*|'
    r'смотрю\s+вокруг|изуча\w*\s+обстановк\w*|'
    r'что\s+(?:вокруг|видно|здесь|там|передо\s+мной)|'
    r'какие\s+(?:выходы|двери|дороги|проходы)|'
    r'куда\s+(?:можно|тут|здесь)\s+(?:пройти|выйти|идти)|'
    r'кто\s+(?:здесь|там|вокруг|рядом|присутству\w*)|'
    r'(?:есть|видно)\s+ли\s+(?:кто|что))\b', re.I)

TALK = re.compile(
    r'\b(?:говор\w*|скаж\w*|сказ\w*|сказа\w*|спрашива\w*|спрос\w*|спроси\w*|'
    r'обраща\w*|обрати\w*|здоров\w*|поздорова\w*|приветств\w*|беседу\w*|'
    r'болта\w*|заговарива\w*|расскаж\w*|рассказыва\w*|отвеча\w*|кричу\w*|зову\b|'
    r'зов\w*|оклика\w*)\b', re.I)

#: Вопрос ведущему, а не персонажу: «Мастер, а можно ли…».
MASTER = re.compile(r'\b(?:мастер\w*|ведущ\w*|гм\b|dm\b)\b', re.I)

_WORD = re.compile(r'[а-яёa-z0-9-]+')


def words(text):
    return _WORD.findall(str(text or '').casefold())


def parts(text):
    """Части одной реплики. Кавычки не режутся: чужая речь — одна часть."""
    value = str(text or '')
    chunks, buffer, quoted = [], '', False
    for char in value:
        if char == '«':
            quoted = True
        elif char == '»':
            quoted = False
        if not quoted and char in '.;!?,':
            chunks.append(buffer)
            buffer = ''
            continue
        buffer += char
    chunks.append(buffer)
    result = []
    for chunk in chunks:
        # «осматриваюсь и спрашиваю стражника» — два действия, а не одно.
        for piece in re.split(r'\s+и\s+(?=[а-яё])', chunk, flags=re.I):
            piece = ' '.join(piece.split())
            # Часть без букв — обрывок разбиения («— », «?»), не намерение.
            if piece and words(piece) and any(char.isalpha() for char in piece):
                result.append(piece)
    return result


def classify(part, people=()):
    """Одно намерение части. Порядок важен: «спрашиваю Мастера» — не разговор."""
    value = str(part or '').casefold()
    if MASTER.search(value):
        return 'ask_master'
    if TALK.search(value):
        # Разговор — это обращение к человеку. «Скажи, что вокруг?» — вопрос
        # к сцене, а не к собеседнику, которого здесь может не быть.
        if speaks_to(part, people) or not OBSERVE.search(value):
            return 'talk'
    if OBSERVE.search(value):
        return 'observe'
    return 'other'


def plan(text, people=()):
    """Реплика, разобранная на части: [{'text', 'kind'}]."""
    items = [dict(text=part, kind=classify(part, people)) for part in parts(text)]
    merged = []
    for index, item in enumerate(items):
        following = items[index + 1] if index + 1 < len(items) else None
        if (item['kind'] == 'talk' and not speaks_to(item['text'], people)
                and following is not None and following['kind'] == 'observe'):
            # «Скажи, что вокруг?» — не разговор с отсутствующим человеком:
            # повод к речи и сам вопрос о сцене — одно намерение осмотра.
            following['text'] = item['text'] + ', ' + following['text']
            following['kind'] = 'observe'
            continue
        merged.append(item)
    return merged


def kinds(plan, kind):
    return [item for item in plan or [] if item['kind'] == kind]


def stem(name):
    """Основа имени для поиска в реплике: «Ирина» → «ирин», «Леосин» → «леосин»."""
    value = words(name)
    if not value:
        return ''
    first = value[0]
    return first[:-1] if first.endswith(('а', 'я')) else first


def speaks_to(text, people):
    """Имя человека из сцены, к которому обращаются в этой части. Иначе ''."""
    said = ' '.join(words(text))
    for person in people or []:
        name = str(person.get('name') or '')
        base = stem(name)
        identity = str(person.get('id') or '').casefold()
        if base and re.search(r'\b' + re.escape(base) + r'\w*\b', said):
            return name
        if identity and re.search(r'\b' + re.escape(identity) + r'\b', said):
            return name
    return ''


def unmet(plan, people, *, conversation=False):
    """Части, которые этот ход не выполнил, — с причиной по-русски.

    Выполненная часть не отменяется: строки дописываются к ответу, а не заменяют
    его. Разговор, который возможен, но в этот ход не состоялся, называется
    переносом, а не отказом: игрок сам решает, идти ли в него отдельной репликой.
    """
    if conversation:
        # Ход и есть разговор: его часть выполнена, даже если герой назвал
        # собеседника не по имени («спрашиваю местного жителя»).
        return []
    lines = []
    for item in plan or []:
        if item['kind'] != 'talk':
            continue
        name = speaks_to(item['text'], people)
        # Имя не склоняем: «разговор с Ирина» — не по-русски, а падежей у нас нет.
        reason = ('разговор идёт отдельной репликой — собеседник: ' + name) if name else 'здесь не с кем говорить'
        lines.append('Не выполнено: «' + item['text'] + '» — ' + reason + '.')
    return lines
