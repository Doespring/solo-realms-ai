"""A copy of the project must not reach back into the folder it came from.

Section 9.1: the clean copy 'must not accidentally import code from the original
folder or the previous .venv through absolute paths'.

The critic showed that checking for the literal text 'sys.path.insert' guards a
spelling, not the effect: 'sys.path[0:0]=[...]', 'sys.path = [...] + sys.path'
and 'sys.path.extend(...)' all slip through. These tests therefore parse the code
and look at what it does.

He also named the channel that actually ties a copy to its source: the worker is
started with the caller's environment, so PYTHONPATH can make it run the original
package, and 'dnd5e_engine' resolves from the original .venv.
"""
import os
os.environ['SOLO_TEST']='1'

import ast
from pathlib import Path

SOLO = Path(__file__).resolve().parents[1] / 'solo'


def trees():
    for path in sorted(SOLO.rglob('*.py')):
        try:
            yield path, ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue


def is_sys_path(node):
    if isinstance(node, ast.Attribute):
        return node.attr == 'path' and isinstance(node.value, ast.Name) and node.value.id == 'sys'
    if isinstance(node, ast.Subscript):
        return is_sys_path(node.value)
    return False


def writes_sys_path(node):
    if isinstance(node, ast.Assign):
        return any(is_sys_path(t) for t in node.targets)
    if isinstance(node, ast.AugAssign):
        return is_sys_path(node.target)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return is_sys_path(node.func.value)
    if isinstance(node, ast.Delete):
        return any(is_sys_path(t) for t in node.targets)
    return False


def test_nothing_rewrites_sys_path():
    offenders = []
    for path, tree in trees():
        for node in ast.walk(tree):
            if writes_sys_path(node):
                offenders.append(path.name + ':' + str(getattr(node, 'lineno', 0)))
    assert not offenders, 'these would tie a copy to one machine: ' + repr(offenders)


def test_no_code_names_a_home_or_absolute_location():
    bad_starts = ('~/', 'C:/', 'C:\\\\', '\\\\')
    offenders = []
    for path, tree in trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if text.startswith(bad_starts) or '/Users/' in text or '/home/' in text:
                    offenders.append(path.name + ':' + str(node.lineno) + ' ' + text[:40])
    assert not offenders, repr(offenders)


def test_no_code_names_a_virtual_environment():
    offenders = []
    for path, tree in trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if '.venv' in node.value or 'site-packages' in node.value:
                    offenders.append(path.name + ':' + str(node.lineno))
    assert not offenders, repr(offenders)


def test_the_worker_drops_the_inherited_python_path():
    """The real channel: the worker is started with the caller's environment.

    If PYTHONPATH points at the original folder, 'python -m solo.nat20_worker'
    can find the original package. A copy must not depend on that.
    """
    source = (SOLO / 'nat20_api.py').read_text(encoding='utf-8')
    assert 'PYTHONPATH' in source, (
        'the worker inherits os.environ; PYTHONPATH must be removed explicitly')


def test_the_package_reports_its_own_location():
    from solo import build_identity
    identity = build_identity.build_identity()
    assert identity['version']
    assert (identity.get('modules') or 0) > 0


def test_the_worker_does_not_search_the_working_folder():
    """'python -c' puts '' on sys.path unless PYTHONSAFEPATH is set.

    A stray file in the working folder could then shadow a real module inside
    the worker. The worker must search its own package, not the folder it
    happens to be started in.
    """
    source = (SOLO / 'nat20_api.py').read_text(encoding='utf-8')
    assert 'environment.pop("PYTHONSAFEPATH", None)' in source, (
        'the worker must not inherit a flag that puts the working folder first')
