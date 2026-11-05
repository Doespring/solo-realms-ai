"""The narrator may not answer in a foreign language the table never allowed.

solo/narrator_language.py:19 validate rejects any Latin word outside the set
derived from the packet. solo/model_protocol.py calls it twice - on every
streamed chunk (partial=True) and once on the finished text. Removing the final
call left the narrator tests green: they check grounding, not language.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.narrator_language import NarrativeLanguageError, allowed_foreign, validate


def test_a_foreign_word_is_refused():
    packet = {'confirmed': ['Зал пуст.']}
    allowed = allowed_foreign(packet)
    with pytest.raises(NarrativeLanguageError):
        validate('Зал пуст, sir.', allowed)


def test_a_word_from_the_packet_is_allowed():
    packet = {'confirmed': ['Dorn стоит у двери.']}
    allowed = allowed_foreign(packet)
    validate('Dorn стоит у двери.', allowed)


def test_the_finished_text_is_checked_not_only_the_chunks():
    """The protocol must validate the whole answer, not just streaming chunks."""
    source = open('solo/model_protocol.py', encoding='utf-8').read()
    assert source.count('validate(presentation.shown,allowed)') >= 1, (
        'the finished narrative is never checked for language')
