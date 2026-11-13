"""Публичная сцена: что видит игрок — отдельно от книги и половины Мастера.

Аудит 08.11 (F07): «Осматриваюсь» возвращал страницу книги — заголовок эпизода,
обзор приключения и планы ведущего, — а тот же длинный текст стоял под историей
как статус. Один текст отвечал за четыре разные вещи. Здесь они разделены:

* ``source_text`` — страница книги, как напечатана: для проверки и отчёта;
* ``private_facts`` — половина Мастера: тайны, планы, указания ведущему;
* ``public_scene`` — что действительно видит игрок, стоя в месте;
* ``runtime_status`` — служебная строка полосы под историей (не длиннее 120).

Правило одно: в сцену игрока попадает только то, что источник сам делает
публичным. Служебные слова — «эпизод», «совет», «сессия» — не сцена: они
называют единицу книги и рабочий план ведущего, а не то, что видно с места.
Поэтому же в статусе не повторяется сцена: статус — одна короткая строка.
"""
import re

#: Слова, которые называют рабочий слой (эпизод книги, план ведущего, сеанс
#: игры), а не то, что видно с места. Предложение с ними в сцену не идёт.
SERVICE = re.compile(r'\b(?:эпизод\w*|совет\w*|сесси\w*)\b', re.I)

#: Заголовок книги («Эпизод 1: Гринест в огне», «Глава 3»): это подпись страницы,
#: а не scenery. Раздел, начинающийся так, тоже не сцена.
HEADING = re.compile(
    r'^\s*(?:эпизод|глава|часть|книга|приложение|дополнение|введение|пролог|'
    r'обзор\s+приключения|секреты|организация|планы|предыстори|мотивация|'
    r'дальнейшее|послесловие|награда|таблица|примечани|'
    r'chapter|episode|part|appendix|introduction|prologue)\b', re.I)

#: Оглавление и колонцифра: строка без прозы.
_PAGE_NUMBER = re.compile(r'^\s*\d{1,4}\s*$')

#: Сколько знаков сцены и одной её фразы отдавать игроку.
SCENE_LIMIT = 900
SENTENCE_LIMIT = 400
STATUS_LIMIT = 120


def sentences(text):
    """Предложения абзаца. Перенос строки — тоже граница (книга так и печатает)."""
    if not isinstance(text, str):
        return []
    return [part.strip() for part in re.split(r'(?<=[.!?…])\s+|\n+', text) if part.strip()]


def is_service(sentence):
    """Служебное слово в предложении: единица книги или план ведущего."""
    return bool(SERVICE.search(str(sentence or '')))


def is_public(sentence, name=''):
    """Предложение можно показать игроку."""
    text = ' '.join(str(sentence or '').split())
    if not text:
        return False
    if _PAGE_NUMBER.match(text) or HEADING.match(text):
        return False
    if is_service(text):
        return False
    return not (name and text.rstrip('.!?…').casefold() == ' '.join(name.split()).casefold())


def _clip(sentence, limit=SENTENCE_LIMIT):
    if len(sentence) <= limit:
        return sentence
    head = sentence[:limit]
    cut = head.rsplit(' ', 1)[0] if ' ' in head else head
    return cut.rstrip(' ,;:—–-') + '…'


def public_scene_text(text, *, name='', limit=SCENE_LIMIT):
    """Только открытые предложения источника, без повторов и без обрыва книги."""
    kept, total = [], 0
    for raw in sentences(text):
        if not is_public(raw, name=name):
            continue
        sentence = _clip(' '.join(raw.split()))
        if sentence[-1] not in '.!?…':
            sentence += '.'
        if any(sentence.casefold() == known.casefold() for known in kept):
            continue
        if kept and total + len(sentence) > limit:
            break
        kept.append(sentence)
        total += len(sentence)
        if len(kept) >= 6:
            break
    return ' '.join(kept)


def private_facts(text):
    """Половина Мастера: предложения источника, которые игроку не показывают."""
    kept = []
    for raw in sentences(text):
        sentence = ' '.join(raw.split())
        if not sentence or _PAGE_NUMBER.match(sentence):
            continue
        if is_public(sentence) or sentence.casefold() in {known.casefold() for known in kept}:
            continue
        kept.append(_clip(sentence))
        if len(kept) >= 8:
            break
    return kept


def status_line(text, *, limit=STATUS_LIMIT):
    """Строка статуса под историей: одна фраза, не длиннее ``limit`` знаков."""
    value = ' '.join(str(text or '').split()).strip(' .')
    if len(value) <= limit:
        return value
    head = value[:limit]
    cut = head.rsplit(' ', 1)[0] if ' ' in head else value[:limit - 1]
    return cut.rstrip(' ,;:—–-') + '…'


def runtime_status(place, *, limit=STATUS_LIMIT):
    """Служебная строка места: имя и состав сцены, а не пересказ сцены.

    Полоса под историей не должна повторять сцену — ни длинную страницу книги
    (F07), ни короткое описание: игрок уже прочитал сцену в ленте. Здесь только
    то, что нужно для ориентира: где герой и кто рядом.
    """
    place = place or {}
    name = ' '.join(str(place.get('name') or '').split())
    people = []
    for person in place.get('npc') or []:
        if not person.get('present', True):
            continue
        label = ' '.join(str(person.get('name') or person.get('id') or '').split())
        if label and label not in people:
            people.append(label)
    line = (name + ' · ' if name else '') + ('рядом: ' + ', '.join(people) if people else 'рядом: никого')
    return status_line(line, limit=limit)


def for_player(text, *, fallback=''):
    """Сцена для игрока из ответа рассказчика: служебные фразы не проходят.

    Рассказчик повторяет подтверждённые предложения, но в его пакет попадают и
    воспоминания кампании, куда страница книги уже легла. Поэтому фильтр стоит и
    там, где текст рождается: одна фраза «В этом эпизоде…» в ответе — и игрок
    снова читает книгу вместо сцены.
    """
    parts = sentences(text)
    kept = [part for part in parts if part and not HEADING.match(part) and not is_service(part)]
    if len(kept) == len([part for part in parts if part]):
        # Ничего не выброшено: текст остаётся дословным. Пересборка предложений
        # рвала подтверждённую сцену на абзацы и ломала сверку с источником.
        return str(text).strip()
    return ' '.join(kept).strip() or str(fallback or '').strip()


def scene_contract(place, *, source_text=''):
    """Четыре разных текста места, которые нельзя смешивать в один.

    ``place`` — вид места (solo.world.location_view) или его словарь из состояния.
    Ключи ответа — та же четвёрка, что в задании A04: что напечатала книга, что
    знает только Мастер, что видит игрок, и короткая служебная строка.
    """
    place = place or {}
    raw = str(place.get('description') or '')
    name = str(place.get('name') or '')
    source = str(source_text or raw)
    # Имя места не выбрасывается: описание уходит рассказчику дословно, и
    # «Город Мглистый.» в его начале — часть опубликованной сцены, а не повтор.
    public = public_scene_text(raw)
    if not public:
        # Книга не дала ни одного открытого предложения: честнее назвать место,
        # чем показать пустую сцену или выдать половину Мастера.
        public = name
    return dict(source_text=source,
                private_facts=private_facts(place.get('sealed_text') or source),
                public_scene=public,
                runtime_status=runtime_status(place))
