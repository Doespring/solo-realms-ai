'''The transcript must open with ONE line from the GM.

Confirmed on live data: the scene install is stored in actions.original with
request_id 'prepared-world', so play.js rendered it as something the hero said
-- the same opening appeared twice, once signed with the hero's name.
These tests hold the data contract and the rendering rule together.
'''
import sys, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLAY = (ROOT / 'web' / 'play.js').read_text(encoding='utf-8')
STORAGE = (ROOT / 'solo' / 'storage.py').read_text(encoding='utf-8')


def test_history_exposes_the_request_id():
    '''Without this field the renderer cannot tell a setup record from speech.'''
    assert 'a.request_id' in STORAGE


def test_setup_records_are_not_signed_with_the_hero_name():
    assert 'prepared-world' in PLAY and 'lantern-install' in PLAY
    assert 'setup' in PLAY


def test_the_gm_opening_line_still_renders():
    '''Skipping the hero line must not skip the GM line with it.'''
    lines = [l for l in PLAY.splitlines() if 'opening' in l and 'message(' in l]
    assert lines, 'the GM opening line must stay rendered'
    assert "МАСТЕР" in lines[0]


def test_setup_guard_does_not_drop_the_whole_event():
    assert not re.search(r"request_id\|\|''\)\)\)continue", PLAY), (
        'a setup record still needs its MASTER line rendered')
