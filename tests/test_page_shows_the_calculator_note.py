"""The page must show the calculator warning, not only carry it in JSON.

Section 7.1: a campaign without the engine is resolved by the plain Python
service. solo/app.py now answers with rules_backend_note; this checks the page
actually renders it, otherwise the honesty lives in a field nobody reads.
"""
import os
os.environ['SOLO_TEST']='1'

from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / 'web' / 'play.js'


def test_the_page_renders_the_calculator_note():
    source = PAGE.read_text(encoding='utf-8')
    assert 'calculator-status' in source, 'no element for the notice'
    assert 'rules_backend_note' in source, 'the notice is never filled'


def test_the_notice_is_hidden_when_the_engine_answers():
    source = PAGE.read_text(encoding='utf-8')
    assert 'calc.hidden=!note' in source.replace(' ', ''), (
        'an engine campaign must not show a warning')
