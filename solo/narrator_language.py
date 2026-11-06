"""Cheap streaming language check; no semantic claims and no extra model call."""
import re,unicodedata

WORDS=re.compile(r'[^\W\d_]+',re.UNICODE)
class NarrativeLanguageError(ValueError):pass

def cyrillic(word):return all('CYRILLIC' in unicodedata.name(c,'') for c in word)

def allowed_foreign(data):
    words=set()
    def walk(value):
        if isinstance(value,dict):
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
        elif isinstance(value,str):words.update(m[0].casefold() for m in WORDS.finditer(value) if not cyrillic(m[0]))
    walk(data);return words

def validate(text,allowed,*,partial=False):
    for match in WORDS.finditer(text):
        word=match[0]
        if cyrillic(word) or word.casefold() in allowed:continue
        if partial and match.end()==len(text) and any(w.startswith(word.casefold()) for w in allowed):continue
        raise NarrativeLanguageError('Рассказчик выдал непредусмотренный нерусский фрагмент. Механика сохранена; автоматического повтора нет.')
