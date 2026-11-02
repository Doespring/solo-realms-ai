'''Engine refusals must reach the table as Russian, not as log lines.

A live run typed 'Хочу повысить уровень' and the player got
'advancement choices still owed: expertise x2 at class:bard level 2' --
engine vocabulary that the identifier filter cannot see, because it contains
no snake_case and no key:value pair. Only a translated refusal is honest.
'''
import sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solo.game import refusal_text  # noqa: E402


def test_advancement_refusal_is_explained_in_russian():
    raw = 'advancement choices still owed: expertise x2 at class:bard level 2'
    text = refusal_text(ValueError(raw))
    assert 'нельзя' in text
    assert 'owed' not in text and 'expertise' not in text


def test_unknown_english_refusal_never_reaches_the_player():
    text = refusal_text(ValueError('some engine condition failed'))
    assert not any('a' <= ch <= 'z' for ch in text.casefold()) or 'Мастер' in text
    assert 'engine' not in text


def test_russian_refusal_is_kept_as_written():
    assert refusal_text(ValueError('Герой уже мёртв.')) == 'Герой уже мёртв.'
