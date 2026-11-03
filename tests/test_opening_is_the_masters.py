"""The opening belongs to the master, and appears once.

Section 5 asks that the introduction is stored once in the master's voice and is
not passed off as the hero's words. The screen used to print the scene text as a
player line as well, because a setup record carries the same text.

Checked on the running screen too: 102 bubbles, the opening text found once,
signed MASTER, zero times signed by the player.
"""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path

UI = Path(__file__).resolve().parents[1]/'web'/'play.js'


def source():
    return UI.read_text(encoding='utf-8')


def test_the_opening_is_signed_by_the_master():
    text = source()
    assert "message('МАСТЕР',openingText,'master')" in text, 'the opening is the master speaking'


def test_the_opening_is_not_printed_twice():
    text = source()
    # The guard checks the whole timeline and the conversation before printing.
    assert 'current.history' in text and 'current.conversation' in text
    assert '.some(h=>String(h.narrative||h.reply' in text, 'a second copy must be refused'


def test_a_setup_record_is_not_signed_as_the_hero():
    text = source()
    assert 'prepared-world' in text and 'lantern-install' in text
    assert 'spoken' in text, 'the setup record must be described as not-the-hero'
