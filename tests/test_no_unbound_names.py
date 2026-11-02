'''Scope-correct check for reads of names nothing binds.

A flat per-file set of names proves nothing: the defect we chase ('status'
used in a scope that never binds it) passes such a scan. Python itself is the
authority, so use symtable: it knows the scope of every name.'''
import builtins, symtable, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILTINS = set(dir(builtins))


def unbound_reads(source, filename='mod'):
    top = symtable.symtable(source, filename, 'exec')
    # An unbound name is reported as 'global', so compare against the module.
    at_module = {sym.get_name() for sym in top.get_symbols()
                 if sym.is_assigned() or sym.is_imported() or sym.is_parameter()}
    bad = []

    def walk(table, is_top):
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced() or name in ('self', 'cls'):
                continue
            if sym.is_assigned() or sym.is_parameter() or sym.is_imported() or sym.is_free():
                continue
            if not is_top and name in at_module:
                continue
            if name in BUILTINS or name in ('__file__', '__name__', '__doc__', '__package__'):
                continue
            bad.append((table.get_name(), name))
        for child in table.get_children():
            walk(child, False)

    walk(top, True)
    return bad


def test_no_module_reads_a_name_nothing_binds():
    problems = []
    for path in sorted((ROOT / 'solo').glob('*.py')):
        source = path.read_text(encoding='utf-8')
        for table_name, name in unbound_reads(source, path.name):
            if name == 'self' or name == 'cls':
                continue
            problems.append(path.name + ' in ' + table_name + ' reads ' + name)
    assert not problems, problems


def test_the_check_catches_the_observation_crash():
    broken = ('def a():\n    status = 1\n    return status\n'
              '\ndef b():\n    return status\n')
    assert ('b', 'status') in unbound_reads(broken)
