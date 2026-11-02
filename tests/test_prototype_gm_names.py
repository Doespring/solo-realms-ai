'''Every observation of a scene answered HTTP 500: prototype_gm.py named a
variable 'status' that never existed in that scope (three separate lines).
The path had never been exercised end to end, so the typo survived.
'''
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def test_prototype_gm_has_no_unbound_name():
    """Not 'status' only: any name read in a scope that never binds it.

    A single-name check was tied to the one bug that happened to be found; the
    generic scan lives in tests/test_no_unbound_names.py and covers every module.
    """
    from test_no_unbound_names import unbound_reads
    source = (ROOT / 'solo' / 'prototype_gm.py').read_text(encoding='utf-8')
    assert unbound_reads(source, 'prototype_gm.py') == []


def test_narrative_kind_comes_from_the_intent():
    source = (ROOT / 'solo' / 'prototype_gm.py').read_text(encoding='utf-8')
    # The reply contract says 'observation'; the interpreter says 'observe'.
    assert "status = 'observation' if intent.status == 'observe' else intent.status" in source
    assert "kind=status" in source
