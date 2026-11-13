"""Разрешение стартового снаряжения предыстории в конкретные предметы корпуса.

SRD 5.2 §Starting Equipment: предыстория даёт «a set of starting equipment».
В корпусе это найдено в `canonical/backgrounds/*.json` → `starting_equipment`,
список записей Foundry. Записи ссылаются на предметы ключами вида

    Compendium.dnd5e.equipment24.Item.phbwepSpear00000
    Compendium.dnd5e.items.Item.SsAmWV6YBqeOFihT

**Формула ключа** (установлена измерением, не догадкой):
`phb` + группа (3 символа: `wep`, `amo`, `tul`, `bag`) + CamelCase имени,
обрезанный на 15 символах. У наборов группа вплотную: `phbag` (5 символов
префикса), у остальных `phbwep`/`phbamo`/`phbtul` (6). Хвостовые нули — добивка
до фиксированной длины.

**Восстановление необратимо неоднозначно**: обрезка теряет хвост имени, поэтому
`phbwepShor` подходит и к `shortbow`, и к `shortsword`. Порядок разрешения:

1. остаток РОВНО равен CamelCase-имени слага → однозначно;
2. иначе — префиксные кандидаты; берётся КРАТЧАЙШЕЕ имя (базовый предмет,
   а не `robe-of-eyes` или `quarterstaff-of-the-acrobat`);
3. несколько кандидатов равной длины → отказ (`UnresolvedStartingItem`),
   а не молчаливый выбор первого (п.2: не заменять догадкой).

Записи без разборчивого ключа (`tool: game`, `focus: holy`) не разрешаются в
предмет и перечисляются отдельно как выбор игрока.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dnd5e_engine.lib_loader import get_lib_loader


class UnresolvedStartingItem(ValueError):
    """Ключ предмета не восстановим однозначно по данным корпуса."""


@dataclass(frozen=True)
class StartingItem:
    """Один разрешённый предмет стартового набор."""

    slug: str
    name: str
    count: int


@dataclass(frozen=True)
class StartingLoadout:
    """Результат разрешения `starting_equipment` одной предыстории."""

    items: tuple[StartingItem, ...] = ()
    wealth_gp: int = 0
    unresolved: tuple[str, ...] = field(default_factory=tuple)


_GROUP_PREFIX = {"wep", "amo", "tul"}


def _camel(name: str) -> str:
    """``healers-kit`` -> ``HealersKit``; ``clothes-travelers`` -> ``ClothesTravelers``."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("-") if part)


def _key_core(key: str) -> str | None:
    """Отрезать ``phb``+группа и хвостовые нули. ``None`` — ключ не предмет."""
    # Полный вид: ``Compendium.dnd5e.equipment24.Item.phbwepSpear00000``.
    # Хвост после последней точки — собственно ключ Foundry.
    tail = key.rsplit(".", 1)[-1]
    if not tail.startswith("phb") or len(tail) < 7:
        return None
    # Ширина группы в ключе установлена фактом: `phbwep`/`phbamo`/`phbtul` —
    # 6 символов (3-буквенная группа), а наборы пишутся как `phbag` — то есть
    # 5 символов, группа `ag` (НЕ `bag`).
    if tail.startswith("phbag"):
        width = 5
    elif tail[3:6] in _GROUP_PREFIX:
        width = 6
    else:
        return None
    core = tail[width:].rstrip("0")
    return core or None


def resolve_starting_key(key: str, catalogue: dict[str, str]) -> StartingItem | None:
    """Разрешить один ключ Foundry в предмет корпуса.

    ``catalogue`` — ``{slug: name}`` всех предметов корпуса.
    ``None`` — ключ не является предметом (``game``, ``focus:holy``).
    ``UnresolvedStartingItem`` — ключ предметный, но неоднозначен.
    """
    core = _key_core(key)
    if core is None:
        return None
    by_camel = {_camel(slug): slug for slug in catalogue}
    exact = by_camel.get(core)
    if exact is not None:
        return StartingItem(slug=exact, name=catalogue[exact], count=1)
    prefix = [s for camel, s in by_camel.items() if camel.startswith(core)]
    if len(prefix) == 1:
        return StartingItem(slug=prefix[0], name=catalogue[prefix[0]], count=1)
    if not prefix:
        return None
    shortest = sorted(prefix, key=lambda s: (len(_camel(s)), s))
    if len(_camel(shortest[0])) == len(_camel(shortest[1])):
        raise UnresolvedStartingItem(
            f"key {key!r} is ambiguous between {shortest[0]!r} and {shortest[1]!r}; "
            "refusing to pick one rather than guessing"
        )
    return StartingItem(slug=shortest[0], name=catalogue[shortest[0]], count=1)


def resolve_background_equipment(background_slug: str) -> StartingLoadout:
    """Стартовое снаряжение предыстории, разрешённое в предметы корпуса.

    Классы здесь НЕ обрабатываются: у них в корпусе нет `starting_equipment`
    вовсе (только `wealth`) — это ограничение источника, а не движка.
    """
    loader = get_lib_loader()
    doc = loader.get_background(background_slug)
    if doc is None:
        raise ValueError(f"unknown background: {background_slug!r}")
    catalogue = {slug: loader.get_item(slug).name for slug in loader.list_slugs("items")}
    items: list[StartingItem] = []
    unresolved: list[str] = []
    for entry in getattr(doc, "starting_equipment", ()) or ():
        kind = entry.get("type")
        if kind == "AND":
            continue
        count = entry.get("count") or 1
        if kind == "linked":
            item = resolve_starting_key(str(entry.get("key") or ""), catalogue)
            if item is None:
                unresolved.append(str(entry.get("key")))
            else:
                items.append(StartingItem(slug=item.slug, name=item.name, count=int(count)))
        else:
            # `tool`, `focus` и подобные — выбор игрока, не конкретный предмет.
            unresolved.append(str(entry.get("key") or kind))
    wealth = getattr(doc, "wealth", None)
    try:
        gold = int(str(wealth).strip()) if wealth is not None else 0
    except (TypeError, ValueError):
        gold = 0
    return StartingLoadout(items=tuple(items), wealth_gp=gold, unresolved=tuple(unresolved))
