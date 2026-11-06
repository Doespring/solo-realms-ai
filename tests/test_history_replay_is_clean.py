'''The history endpoint must clean rows written before the filter existed.

A campaign saved earlier replayed 'attacks_remaining: 0' into the chat on
every open, because storage trusted whatever was already in the table.'''
import inspect, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from solo.game import player_text  # noqa: E402
from solo.storage import Store  # noqa: E402

LEGACY = 'Враг атакует (attacks_remaining: 0).'


def test_history_cleans_what_it_reads():
    assert 'player_text' in inspect.getsource(Store.history)


def test_a_legacy_row_becomes_table_speech():
    assert player_text(LEGACY) == 'Враг атакует.'


def test_ordinary_history_line_survives():
    line = 'Нет активной боевой ситуации и видимой цели для атаки.'
    assert player_text(line) == line
