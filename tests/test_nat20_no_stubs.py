"""Критерий 14.4: нет известных обязательных механических заглушек.

Пункт 14 запрещает объявлять завершение при известных заглушках, а пункт 14.4
требует их отсутствия. Заглушка — это не только `pass`: это и `NotImplementedError`,
и функция, весь смысл которой — вернуть None.

Файл разбирает AST всех модулей движка и падает, если появится любой из этих
видов. Так «импортировано, реализация потом» не может вернуться незамеченным.
"""
import ast
from pathlib import Path

import dnd5e_engine


ENGINE = Path(dnd5e_engine.__file__).parent


def _modules():
    return sorted(ENGINE.rglob("*.py"))


def _functions():
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node


def _body(node):
    """Тело без строки документации."""
    return [
        s for s in node.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str))
    ]


def test_the_engine_has_no_placeholder_markers():
    markers = ("NotImplementedError", "TODO", "FIXME", "XXX")
    hits = []
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                hits.append(f"{path.relative_to(ENGINE)}: {marker}")
    assert not hits, f"в движке остались пометки заглушек: {hits[:5]}"


def test_no_function_body_is_empty():
    """Тело из одного докстринга, `pass`, или одиночного `return None` — заглушка."""
    empty = []
    for path, node in _functions():
        body = _body(node)
        where = f"{path.relative_to(ENGINE)}:{node.lineno} {node.name}"
        if not body:
            empty.append(where)
            continue
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            empty.append(where)
            continue
        value = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
        if isinstance(value, ast.Constant) and value.value is None:
            empty.append(where)
    assert not empty, f"пустые функции: {empty[:5]}"


def test_no_function_body_is_just_pass():
    empty = []
    for path, node in _functions():
        body = _body(node)
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            empty.append(f"{path.relative_to(ENGINE)}:{node.lineno} {node.name}")
    assert not empty, f"пустые функции: {empty[:5]}"


def test_no_function_only_returns_none():
    """Функция, чьё единственное действие — вернуть None, ничего не делает."""
    stubs = []
    for path, node in _functions():
        body = _body(node)
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and value.value is None:
            stubs.append(f"{path.relative_to(ENGINE)}:{node.lineno} {node.name}")
    assert not stubs, f"функции-заглушки на None: {stubs[:5]}"


def test_every_activity_kind_has_a_resolver():
    """Семейства механик заклинаний: у каждого вида активности есть обработчик."""
    from dnd5e_engine.activities import resolver

    for kind in ("attack", "cast", "check", "damage", "heal", "save", "activity"):
        assert hasattr(resolver, f"resolve_{kind}"), kind


def test_the_resolver_module_has_no_ellipsis_bodies():
    from dnd5e_engine.activities import resolver

    source = Path(resolver.__file__).read_text(encoding="utf-8")
    assert "    ..." not in source

