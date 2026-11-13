#!/usr/bin/env python3
"""r203 — правка данных: КС Quick Grapple = напечатанному, спасбросок захвата типизирован.

Меняет ТОЛЬКО канонические данные SRD-корпуса и манифест `CORPUS.json`
(отпечатки файлов, сводный digest, записи `corrections` с причиной). Код
движка и хоста не трогается.

Что делает:

1. `bugbear-stalker` / Quick Grapple: `save.dc.calculation` был `"dex"`
   (8 + PB 2 + мод Ловкости 2 = **12**), а SRD 5.2.1 печатает **13**
   (8 + PB 2 + мод Силы 3). Число живёт в данных — там и правится.
2. Девять ссылок `activity.effects[].id` получают определения в
   `Monster.passive_effects` (статусы + напечатанный КС освобождения) — ровно
   так, как r199 сделал для bugbear-stalker/constrictor-snake. Пока ссылка не
   разрешается, движку нечего накладывать: существо «хватает» только в тексте.

Скрипт идемпотентен: повторный запуск ничего не меняет и печатает «без
изменений». Форматирование файлов сохраняется байт-в-байт по стилю исходника
(CRLF + 2 пробела; `ensure_ascii` тот, которым файл был записан).

Запуск:
    .venv/bin/python scripts/apply_r203_grapple_data.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/nat20"
CORPUS = VENDOR / "CORPUS.json"
CORPUS_ROOT = "packages/dnd5e-srd-data/src/dnd5e_srd_data"

SRD = "https://rules.omnisgm.com/en/dnd/srd-5.2"
ANIMALS = "https://rules.omnisgm.com/en/dnd/srd-5.2/animals"

#: (slug, action, effect_id) -> (имя эффекта, статусы, КС освобождения, источник)
TYPED: dict[tuple[str, str, str], tuple[str, list[str], int | None, str]] = {
    ("behir", "Constrict", "Hd0Gy3wJtPfObERq"): (
        "Grappled / Restrained", ["grappled", "restrained"], 16, f"{SRD}/monsters-a-z/behir/"),
    ("couatl", "Constrict", "Hd0Gy3wJtPfObERq"): (
        "Grappled / Restrained", ["grappled", "restrained"], 13, f"{SRD}/monsters-a-z/couatl/"),
    ("giant-constrictor-snake", "Constrict", "Hd0Gy3wJtPfObERq"): (
        "Grappled", ["grappled"], 14, f"{ANIMALS}/giant-constrictor-snake/"),
    ("marilith", "Constrict", "Hd0Gy3wJtPfObERq"): (
        "Grappled / Restrained", ["grappled", "restrained"], 14, f"{SRD}/monsters-a-z/marilith/"),
    ("salamander", "Constrict", "Hd0Gy3wJtPfObERq"): (
        "Grappled / Restrained", ["grappled", "restrained"], 14, f"{SRD}/monsters-a-z/salamander/"),
    ("invisible-stalker", "Vortex", "fWPQye4Mr7VNbX9J"): (
        "Grappled", ["grappled"], 13, f"{SRD}/monsters-a-z/invisible-stalker/"),
    ("shambling-mound", "Engulf", "vsa7d6xWOAvWdOWM"): (
        "Engulfed", ["grappled", "blinded", "restrained"], 14,
        f"{SRD}/monsters-a-z/shambling-mound/"),
    ("water-elemental", "Whelm", "rN76liiHkUoMRMJN"): (
        "Grappled / Restrained", ["grappled", "restrained"], 14,
        f"{SRD}/monsters-a-z/water-elemental/"),
    ("kraken", "Fling", "UovyViBfR9aAlt0y"): (
        "Prone", ["prone"], None, f"{SRD}/monsters-a-z/kraken/"),
}

#: Quick Grapple: напечатанный КС 13 = 8 + PB 2 + мод Силы 3 (не Ловкости 2).
QUICK_GRAPPLE_DC_FIX = ("bugbear-stalker", "Quick Grapple", "str",
           f"{SRD}/monsters-a-z/bugbear-stalker/")


def _detect_style(raw: str, obj: dict) -> tuple[bool, bool]:
    """Стиль файла: `(ensure_ascii, завершающий перевод строки)` — по исходнику."""
    for ensure_ascii in (False, True):
        text = json.dumps(obj, indent=2, ensure_ascii=ensure_ascii,
                          sort_keys=True).replace("\n", "\r\n")
        if text == raw:
            return ensure_ascii, False
        if text + "\r\n" == raw:
            return ensure_ascii, True
    raise SystemExit("не удалось воспроизвести форматирование файла — правка отменена")


def _dump_like(obj: dict, style: tuple[bool, bool]) -> str:
    ensure_ascii, trailing = style
    text = json.dumps(obj, indent=2, ensure_ascii=ensure_ascii,
                      sort_keys=True).replace("\n", "\r\n")
    return text + ("\r\n" if trailing else "")


def _load(slug: str) -> tuple[Path, str, dict, tuple[bool, bool]]:
    path = VENDOR / CORPUS_ROOT / "canonical/monsters" / f"{slug}.json"
    raw = path.read_bytes().decode("utf-8")
    obj = json.loads(raw)
    return path, raw, obj, _detect_style(raw, obj)


def _save(path: Path, raw: str, obj: dict, style: tuple[bool, bool], dry: bool) -> bool:
    new = _dump_like(obj, style)
    if new == raw:
        return False
    if not dry:
        path.write_bytes(new.encode("utf-8"))
    return True


def fix_bugbear_dc(dry: bool) -> bool:
    slug, action_name, ability, source = QUICK_GRAPPLE_DC_FIX
    path, raw, monster, style = _load(slug)
    changed = False
    for action in monster.get("actions") or []:
        if action.get("name") != action_name:
            continue
        for activity in action.get("activities") or []:
            if activity.get("kind") != "save":
                continue
            dc = activity["save"]["dc"]
            if dc.get("calculation") == ability:
                continue
            if dc.get("calculation") != "dex":
                raise SystemExit(f"неожиданное calculation у {slug}/{action_name}: {dc}")
            dc["calculation"] = ability
            changed = True
    if changed:
        _save(path, raw, monster, style, dry)
        print(f"КС: {slug}/{action_name} calculation dex -> {ability} "
              f"(8+PB {monster['proficiency_bonus']}+мод Силы "
              f"{(monster['ability_scores'][ability] - 10) // 2} = 13)")
    return changed


def type_effects(dry: bool) -> list[tuple[str, str]]:
    """Добавляет определения эффектов; возвращает список (slug, причина)."""
    touched: list[tuple[str, str]] = []
    by_slug: dict[str, list[tuple[str, list[str], int | None, str, str]]] = {}
    for (slug, action, effect_id), (name, statuses, escape, source) in TYPED.items():
        by_slug.setdefault(slug, []).append((effect_id, statuses, escape, name, action))

    for slug, entries in sorted(by_slug.items()):
        path, raw, monster, style = _load(slug)
        existing = {e.get("id"): e for e in monster.get("passive_effects") or []}
        reasons = []
        for effect_id, statuses, escape, name, action in entries:
            entry = existing.get(effect_id)
            if entry is None:
                entry = {
                    "changes": [],
                    "description": "",
                    "disabled": False,
                    "duration": None,
                    "escape_dc": escape,
                    "id": effect_id,
                    "name": name,
                    "statuses": list(statuses),
                    "transfer": False,
                }
                monster.setdefault("passive_effects", []).append(entry)
                existing[effect_id] = entry
            if entry.get("statuses") != statuses or entry.get("escape_dc") != escape:
                entry["statuses"] = list(statuses)
                entry["escape_dc"] = escape
            source = TYPED[(slug, action, effect_id)][3]
            reasons.append(
                f"{action}: статусы {statuses}, КС освобождения {escape} ({source})")
        changed = _save(path, raw, monster, style, dry)
        touched.append((slug, "; ".join(reasons)))
        if changed:
            print(f"эффект: {slug} -> {monster['passive_effects'][-1]['id']} "
                  f"{monster['passive_effects'][-1]['statuses']} "
                  f"escape={monster['passive_effects'][-1]['escape_dc']}")
    return touched


def repin_corpus(typed: list[tuple[str, str]], bugbear_changed: bool, dry: bool) -> None:
    raw = CORPUS.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    changed = [slug for slug, _ in typed]
    if "bugbear-stalker" not in changed:
        changed.append("bugbear-stalker")

    for slug in changed:
        rel = f"canonical/monsters/{slug}.json"
        digest = hashlib.sha256((VENDOR / CORPUS_ROOT / rel).read_bytes()).hexdigest()
        manifest["files"][rel] = digest

    hasher = hashlib.sha256()
    for rel in sorted(manifest["files"]):
        hasher.update(rel.encode())
        hasher.update(b"\0")
        hasher.update(bytes.fromhex(manifest["files"][rel]))
    manifest["digest"] = hasher.hexdigest()

    corrections = manifest.setdefault("corrections", {})
    for slug, reason in typed:
        rel = f"canonical/monsters/{slug}.json"
        corrections[f"r203-{slug}.json"] = {
            "path": f"{CORPUS_ROOT}/{rel}",
            "sha256": manifest["files"][rel],
            "reason": (
                "r203: у действия-захвата спасбросок ссылается на эффект из "
                f"общего пака monsterfeatures24, а канонический срез массив "
                "passive_effects актора потерял — ссылка висела, и движку было "
                f"нечего накладывать. Добавлено определение по книге: {reason}. "
                "КС освобождения — напечатанное число, совпадающее с формулой "
                "@skills.ath.passive типизированной активности «Escape Check» "
                "этого же действия."),
        }
    rel = "canonical/monsters/bugbear-stalker.json"
    key = "r199-bugbear-stalker.json"
    record = corrections.setdefault(key, {"path": f"{CORPUS_ROOT}/{rel}"})
    record["sha256"] = manifest["files"][rel]
    if "r203:" not in record.get("reason", ""):
        record["reason"] = record.get("reason", "") + (
            " | r203: save.dc.calculation был 'dex' и давал 8 + PB 2 + мод "
            "Ловкости 2 = 12, а SRD 5.2.1 печатает DC 13 (8 + PB 2 + мод Силы 3) "
            f"({QUICK_GRAPPLE_DC_FIX[3]}). Число живёт в данных — правка здесь, "
            "не в движке.")

    if dry:
        print(f"CORPUS.json (dry-run): обновилось бы файлов {len(changed)}, "
              f"digest {manifest['digest'][:12]}")
        return
    out = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if out == raw:
        print("CORPUS.json без изменений")
        return
    CORPUS.write_text(out, encoding="utf-8")
    print(f"CORPUS.json: файлов {len(changed)}, digest {manifest['digest'][:12]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    bugbear = fix_bugbear_dc(args.dry_run)
    typed = type_effects(args.dry_run)
    repin_corpus(typed, bugbear, args.dry_run)
    if not bugbear and not typed:
        print("без изменений: данные уже приведены к книге")
    return 0


if __name__ == "__main__":
    sys.exit(main())
