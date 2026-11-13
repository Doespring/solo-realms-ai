#!/usr/bin/env python3
"""r203 — замер: КС захвата и типизация спасброска захвата у монстров.

Читает канонический корпус SRD 5.2 и печатает три вещи:

1. Таблицу `КС спасброска`: что посчитает движок по данным (`8 + PB + мод
   характеристики` из `save.dc.calculation`, либо `flat`-формула) против того,
   что напечатано в SRD 5.2.1. Напечатанные числа — не память агента: у каждой
   строки указан адрес страницы, откуда они взяты (см. `PRINTED`).
2. Ссылки `activity.effects[].id` у сейв-активностей с захватом: разрешаются ли
   они в `Monster.passive_effects` (иначе движку нечего накладывать) и какие
   записи остаются без типа.
3. Классификацию нетипизированных: накладывает ли книга состояние на цель,
   относится ли эффект к механике «проглатывания» (которой в движке нет), или
   по книге у спасброска нет состояния вовсе.

Скрипт только читает. Правку данных делает `apply_r203_grapple_data.py`.

Запуск:
    .venv/bin/python scripts/probe_r203_grapple_typed.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONSTERS = (ROOT / "vendor/nat20/packages/dnd5e-srd-data/src/"
            "dnd5e_srd_data/canonical/monsters")

ABILITY = ("str", "dex", "con", "int", "wis", "cha")

#: Напечатанные в SRD 5.2.1 числа. Источник — страница бестиария SRD 5.2:
#: https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/<slug>/ (кроме
#: животных — раздел /animals/). Формат: (спасбросок, освобождение).
PRINTED: dict[tuple[str, str], tuple[int, int | None, str]] = {
    ("bugbear-stalker", "Quick Grapple"): (
        13, 13, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/bugbear-stalker/"),
    ("behir", "Constrict"): (
        18, 16, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/behir/"),
    ("couatl", "Constrict"): (
        15, 13, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/couatl/"),
    ("giant-constrictor-snake", "Constrict"): (
        14, 14, "https://rules.omnisgm.com/en/dnd/srd-5.2/animals/giant-constrictor-snake/"),
    ("marilith", "Constrict"): (
        17, 14, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/marilith/"),
    ("salamander", "Constrict"): (
        15, 14, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/salamander/"),
    ("invisible-stalker", "Vortex"): (
        14, 13, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/invisible-stalker/"),
    ("shambling-mound", "Engulf"): (
        15, 14, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/shambling-mound/"),
    ("water-elemental", "Whelm"): (
        15, 14, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/water-elemental/"),
    ("kraken", "Fling"): (
        25, None, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/kraken/"),
    ("chuul", "Paralyzing Tentacles"): (
        13, None, "https://rules.omnisgm.com/en/dnd/srd-5.2/monsters-a-z/chuul/"),
}

#: Нетипизированные ссылки, которые книга типизирует (состояние на цель).
#: (slug, action, effect_id) -> (статусы, КС освобождения или None)
TYPED_BY_BOOK: dict[tuple[str, str, str], tuple[list[str], int | None]] = {
    ("behir", "Constrict", "Hd0Gy3wJtPfObERq"): (["grappled", "restrained"], 16),
    ("couatl", "Constrict", "Hd0Gy3wJtPfObERq"): (["grappled", "restrained"], 13),
    ("giant-constrictor-snake", "Constrict", "Hd0Gy3wJtPfObERq"): (["grappled"], 14),
    ("marilith", "Constrict", "Hd0Gy3wJtPfObERq"): (["grappled", "restrained"], 14),
    ("salamander", "Constrict", "Hd0Gy3wJtPfObERq"): (["grappled", "restrained"], 14),
    ("invisible-stalker", "Vortex", "fWPQye4Mr7VNbX9J"): (["grappled"], 13),
    ("shambling-mound", "Engulf", "vsa7d6xWOAvWdOWM"): (
        ["grappled", "blinded", "restrained"], 14),
    ("water-elemental", "Whelm", "rN76liiHkUoMRMJN"): (["grappled", "restrained"], 14),
    ("kraken", "Fling", "UovyViBfR9aAlt0y"): (["prone"], None),
}

#: Сейв-активности с захватом в тексте, у которых по книге НЕТ состояния на
#: цель вовсе — только урон. Это не «нетипизированная ссылка», а спасбросок без
#: эффекта; строка нужна, чтобы в отчёте у каждой записи была причина.
DAMAGE_ONLY: dict[tuple[str, str], str] = {
    ("glabrezu", "Pummel"): (
        "Pummel: провал — 15 (3d6+5) дробящего, успех — половина; состояния "
        "по книге нет, ссылки на эффект в данных тоже нет"),
    ("vampire-spawn", "Bite"): (
        "Bite: провал — урон некротикой и уменьшение максимума хитов; "
        "состояния по книге нет, ссылки на эффект в данных тоже нет"),
}

#: Ссылки, которые книгой НЕ типизируются как состояние цели. Причина строкой.
ADJUDICATION: dict[str, str] = {
    "VOHv1LyO3hHolZXo": (
        "первый спасбросок Swallow: по книге провал сразу проглатывает цель, "
        "и Grappled тут же снимается — состояния на цель не остаётся, а "
        "механики проглатывания в движке нет"),
    "aS5FyMP72vn3OKf1": (
        "второй (Constitution) спасбросок Swallow бросает САМО существо, "
        "когда получает урон изнутри, — это не эффект на цель"),
    "5ReKSrBu11ume7GP": (
        "Paralyzing Tentacles: Poisoned+Paralyzed с повтором спасброска в конце "
        "каждого хода цели; повторный спасбросок из данных не выводится "
        "типизированной активностью, поэтому без новой механики запись "
        "осталась бы включённой навсегда"),
}


def _ability_mod(score: int) -> int:
    return (score - 10) // 2


def _engine_dc(monster: dict, activity: dict) -> int | None:
    """КС, который посчитает движок по `save.dc` (activities/save.py:_resolve_dc)."""
    dc = activity["save"]["dc"]
    calculation, formula = dc.get("calculation") or "", dc.get("formula") or ""
    pb = monster["proficiency_bonus"]
    if calculation in ABILITY:
        return 8 + pb + _ability_mod(monster["ability_scores"][calculation])
    if calculation == "flat" or (calculation == "" and formula):
        if formula.isdigit():
            return int(formula)
        return None  # формулы @mod/@prof без контекста здесь не считаем
    return None


def _walk(monster: dict):
    for action in monster.get("actions") or []:
        for activity in action.get("activities") or []:
            if activity.get("kind") == "save":
                yield action, activity


def main() -> int:
    rows, unresolved, missing_effect, dc_mismatch = [], [], [], []
    for path in sorted(MONSTERS.glob("*.json")):
        monster = json.loads(path.read_text(encoding="utf-8"))
        slug = monster.get("slug") or path.stem
        text = json.dumps(monster.get("actions"), ensure_ascii=False).lower()
        if "grapple" not in text and "grappled" not in text:
            continue
        effects = {e["id"]: e for e in monster.get("passive_effects") or []}
        for action, activity in _walk(monster):
            action_text = (action.get("name", "") + " " +
                           str(action.get("description", ""))).lower()
            if "rapple" not in action_text:
                continue
            refs = [e["id"] for e in activity.get("effects") or []]
            resolved = [r for r in refs if r in effects]
            rows.append((slug, action["name"], activity["save"]["ability"],
                         activity["save"]["dc"], _engine_dc(monster, activity),
                         refs, resolved))
            for ref in refs:
                if ref not in effects:
                    unresolved.append((slug, action["name"], ref))
            if refs and not resolved and (slug, action["name"], refs[0]) not in TYPED_BY_BOOK:
                key = refs[0]
                if key not in ADJUDICATION:
                    missing_effect.append((slug, action["name"], key))
            printed = PRINTED.get((slug, action["name"]))
            if printed and printed[0] != _engine_dc(monster, activity):
                dc_mismatch.append((slug, action["name"], _engine_dc(monster, activity),
                                    printed[0]))

    print("Спасброски захвата (save-активности в действиях с захватом):", len(rows))
    print()
    print(f"{'существо':24} {'действие':22} {'тип':4} {'движок':>6} {'книга':>5} ok")
    for slug, name, ability, dc, engine, refs, resolved in rows:
        printed = PRINTED.get((slug, name))
        verdict = "?"
        if printed:
            verdict = "ok" if engine == printed[0] else "РАСХОЖДЕНИЕ"
        print(f"{slug:24} {name[:22]:22} {'/'.join(ability):4} {str(engine):>6} "
              f"{str(printed[0]) if printed else '-':>5} {verdict}")
    print()
    print("Ссылок на эффект без определения:", len(unresolved),
          "| из них объяснены adjudication:", sum(1 for _, _, r in unresolved
                                                   if r in ADJUDICATION),
          "| типизируются книгой:", sum(1 for u in unresolved if u in TYPED_BY_BOOK))
    for slug, name, ref in unresolved:
        if (slug, name, ref) in TYPED_BY_BOOK:
            statuses, escape = TYPED_BY_BOOK[(slug, name, ref)]
            print(f"  ТИПИЗИРУЕТСЯ {slug:22} {name:20} {ref} -> {statuses} escape={escape}")
        else:
            print(f"  adjudication {slug:22} {name:20} {ref} — {ADJUDICATION.get(ref, 'ПРИЧИНА НЕ ЗАПИСАНА')}")
    print()
    print("Сейв-активности без ссылки на эффект (по книге состояния нет):")
    for (slug, name), why in DAMAGE_ONLY.items():
        print(f"  {slug:22} {name:20} — {why}")
    print()
    if missing_effect:
        print("БЕЗ ОБЪЯСНЕНИЯ:", missing_effect)
    if dc_mismatch:
        print("РАСХОЖДЕНИЯ КС (существо, действие, движок, книга):", dc_mismatch)
    return 1 if (missing_effect or dc_mismatch) else 0


if __name__ == "__main__":
    sys.exit(main())
