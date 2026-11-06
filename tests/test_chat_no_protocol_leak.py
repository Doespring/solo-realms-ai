"""The player must never see the interpreter's own protocol vocabulary.

A stored reply was found in conversations.reply that read
"...осмотреться (status=observe), попытаться скрыться (status=check, stealth)...".
That is machine vocabulary, not an in-game question.
"""
import re, pytest

PATTERN = r'\s*\(?\bstatus\s*=\s*[a-z_]+\b[^)]*\)?'

def clean(message):
    message = re.sub(PATTERN, '', message)
    return re.sub(r'\s{2,}', ' ', message).strip()

def test_status_tokens_are_removed_from_question():
    raw = ('Уточните: вы хотите просто осмотреться (status=observe), '
           'попытаться скрыться (status=check, stealth) или начать бой (status=start_combat)?')
    assert clean(raw) == 'Уточните: вы хотите просто осмотреться, попытаться скрыться или начать бой?'

def test_bare_status_is_removed():
    assert clean('Начинается бой. (status=start_combat)') == 'Начинается бой.'

def test_ordinary_russian_is_untouched():
    text = 'Вы идёте к воротам и видите стражу. Ничего подозрительного нет.'
    assert clean(text) == text

def test_no_double_spaces_after_removal():
    assert '  ' not in clean('Осмотреться (status=observe) или уйти?')
