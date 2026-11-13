"""Open settings the material never stated, asked and answered in dialogue.

A pasted premise or a book may name no level, no party size and no opening
place. None of that is a reason to refuse the table: the Master asks, the
player answers, and the answer is kept with where it came from. Nothing here
calls a model, and nothing guesses an answer.
"""
import re

_RU_ORDINALS={'перв':1,'втор':2,'трет':3,'трёх':3,'трех':3,'четверт':4,'пят':5,'шест':6,'седьм':7,'восьм':8,'девят':9,'десят':10}
_NUMBER_WORDS={'один':1,'одна':1,'два':2,'две':2,'три':3,'четыре':4,'четырех':4,'пять':5,'шесть':6,'семь':7,'восемь':8}
_COLLECTIVE={'вдво':2,'втро':3,'вчетвер':4,'впятер':5,'вшестер':6,'дво':2,'тро':3,'четверо':4,'пятеро':5,'шестеро':6}


def _norm(text):
    return ' '.join(str(text or '').casefold().replace('ё','е').split())


def _ordinal(word):
    for stem,number in _RU_ORDINALS.items():
        if str(word).startswith(stem):
            return number
    return None


def _word_number(word):
    word=str(word)
    value=_ordinal(word)
    if value:return value
    for stem,number in _NUMBER_WORDS.items():
        if word.startswith(stem):return number
    return None


def read_level(text):
    """The level a player states, or None when the sentence does not answer."""
    value=_norm(text)
    for pattern in (r'(\d{1,2})\s*(?:-?\s*(?:го|й|ый|ой))?\s*(?:уров\w*|level)',
                    r'(?:уров\w*|level)\s*[:—-]?\s*(\d{1,2})',
                    r'(\d{1,2})(?:st|nd|rd|th)?\s*level'):
        match=re.search(pattern,value)
        if match:
            level=int(match.group(1))
            return level if 1<=level<=20 else None
    # 'пятый уровень', 'уровень пятый'
    for match in re.finditer(r'([а-я]+)\s+(?:уров\w*|level)',value):
        number=_ordinal(match.group(1))
        if number:return number
    match=re.search(r'(?:уров\w*|level)\s+([а-я]+)',value)
    if match:
        number=_ordinal(match.group(1))
        if number:return number
    return None


def read_party(text):
    """How many heroes sit at the table, or None."""
    value=_norm(text)
    for pattern in (r'(\d{1,2})\s*(?:геро\w*|игрок\w*|персонаж\w*|человек\w*|players?|characters?)',
                    r'(?:геро\w*|игрок\w*|персонаж\w*|players?|characters?)\s*(?:из\s+)?(\d{1,2})'):
        match=re.search(pattern,value)
        if match:
            size=int(match.group(1))
            return size if 1<=size<=12 else None
    # 'группа из трёх игроков': the count hides behind a preposition.
    match=re.search(r'(?:из|в|нас)\s+([а-я]+)',value)
    if match:
        number=_word_number(match.group(1))
        if number:return number
    # 'нас четверо', 'четверо героев', 'пойдём втроём'.
    for stem,number in _COLLECTIVE.items():
        if re.search(r'\b'+stem,value):
            return number
    for stem,number in _NUMBER_WORDS.items():
        if re.search(r'\b'+stem,value):
            return number
    return None


# Endings that change under declension while the name stays the same place.
_CASE_TAIL=re.compile(r'(?:ами|ями|ов|ев|ей|ой|ый|ий|ом|ем|ам|ям|ах|ях|а|я|е|у|ю|ы|и|ь)$')
# Russian stems alternate a vowel ("Городок" / "Городка"), so compare without it.
_STEM=re.compile(r'[оеё]$')


# Replies that answer nothing: they must ask again, not invent a place.
_UNSURE=re.compile(r'^(?:не\s+знаю|не\s+уверен[аы]?|без\s+разницы|неважно|как\s+хочешь|как\s+скажешь|'
                   r'сам\s+реши|на\s+твое[йм]?\s+усмотрение|без\s+понятия|хз|любое|любой|что\s+угодно|'
                   r'где\s+мы|где\s+я|что\s+происходит|осмотреться|оглядываюсь|потом|давай\s+потом)')
# Words that signal the table is talking about places, not naming one.
_META=re.compile(r'(?:мест|назван|как\s+назыв|почему|зачем|подскаж|объясн|не\s+понял|вариант|выбер|вопрос)')


def _same_name(printed,said):
    """One place under declension: «Городок» and «Городке» share a long stem.

    The common prefix must cover most of the shorter word and stay at least
    four letters, so «Мост»/«Мосту» matches while «Старая башня» does not
    swallow «Новая башня».
    """
    shortest=min(len(printed),len(said))
    if shortest<4:return False
    common=0
    for left,right in zip(printed,said):
        if left!=right:break
        common+=1
    return common>=4 and common>=shortest-3


def _norm_place(text):
    value=_norm(text)
    value=re.sub(r'^(?:пойдем|пойдём|идем|идём|отправимся|начнем|начнём|стартуем|давай|давайте|начинаем)\s+(?:в|во|на)\s+','',value)
    value=re.sub(r'^(?:в|во|на)\s+','',value)
    return value.strip(' .,!?;:«»"\'')


def match_place(state, text):
    """What the player's words mean against the places this world printed.

    Returns one of:
      ('known', name)   the words name a place the world already has, under
                        another spelling: "старая башня" for «Старая башня»
      ('add', name)     a place the world does not have, named clearly enough
                        to be added as an original addition
      ('ask', message)  the words are too vague or contradict the setting, so
                        the Master asks again instead of guessing
    """
    value=_norm_place(text)
    places=[place.get('name','') for place in state.get('locations') or [] if place.get('name')]
    if not places:
        return ('ask','В этом мире нет ни одного названного места. Как его назвать?')
    if not value or len(value)<3:
        return ('ask','Уточните название места, пожалуйста.')
    for name in places:
        if _norm(name)==value:
            return ('known',name)
    # Russian names inflect: "в Городке" and "Городок" are one place. Only a
    # shared stem is enough to recognise it, and only when a single place
    # answers, so that two nearby names never merge into one.
    tail=value.split()[-1] if value.split() else ''
    inflected=[name for name in places if _same_name(_norm(name).split()[-1],tail)]
    if len(inflected)==1:
        return ('known',inflected[0])
    # A shorter form the player typed is the same place, not a new one.
    # A shorter form the player typed is the same place only when exactly one
    # place answers to it: "башня" may name two different towers.
    partial=[name for name in places if value in _norm(name) or _norm(name) in value]
    if len(partial)==1:
        return ('known',partial[0])
    if len(partial)>1:
        return ('ask','Есть несколько мест, которые подходят: '+', '.join(partial)+'. Какое именно?')
    # A place invented out of nothing is an addition only when it is named as one.
    if not re.search(r'[а-яёa-z]',value):
        return ('ask','Назовите место словами, пожалуйста.')
    # A reply that names nothing is not a place: ask again instead of inventing.
    if _UNSURE.match(value.strip()):
        return ('ask','Не понял название. Назовите место — знакомое или новое, и я запишу ваш выбор.')
    # A sentence about places is not a place name: it asks a meta-question.
    if _META.search(value):
        return ('ask','Назовите место, где начинается история. Это может быть знакомое место или новое.')
    # The player's own spelling is kept: it becomes an addition, not a fact
    # from the source, and it is titled as the player wrote it.
    # Match on the normalized form, but keep the player's own letters: 'ё' is a
    # real letter of the name, and normalization exists only to compare.
    words=[part for part in str(text or '').split() if part]
    return ('add',' '.join([words[0][:1].upper()+words[0][1:]]+words[1:]))


def next_question(state):
    """The one question still waiting, or None."""
    preparation=state.get('preparation') or {}
    answered=preparation.get('answers') or {}
    for entry in preparation.get('open_questions') or []:
        if entry.get('field') not in answered:
            return entry
    return None


def question_text(entry):
    return (entry or {}).get('question') or ''


def answer(state, text, *, resolver=None):
    """Record the player's answer to the open question.

    Returns (field, value, entry) when the sentence answers the question the
    Master actually asked, and None when it does not: an unrelated remark must
    not be squeezed into a setting the player never chose.
    """
    entry=next_question(state)
    if entry is None:
        return None
    field=entry.get('field')
    value=None
    if field=='level':
        value=read_level(text)
    elif field=='party_size':
        value=read_party(text)
    elif field=='start' and resolver is not None:
        try:value=resolver(text)
        except ValueError:value=None
    if value in (None,''):
        return None
    preparation=state.setdefault('preparation',{})
    preparation.setdefault('answers',{})[field]=dict(
        value=value,text=str(text)[:500],provenance='player_answer')
    return field,value,entry


def pending_for_mechanics(state):
    """A level-dependent mechanical step waits until the level is answered."""
    preparation=state.get('preparation') or {}
    answered=preparation.get('answers') or {}
    for entry in preparation.get('open_questions') or []:
        if entry.get('field')=='level' and 'level' not in answered:
            return 'Уровень ещё не согласован в диалоге; шаг, зависящий от уровня, ждёт ответа.'
    return ''
