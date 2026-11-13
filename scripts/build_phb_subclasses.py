"""r147/r169: подклассы PHB 2024, которых нет в SRD-наборе — дословный перенос.

SRD 5.2 несёт ровно один подкласс на класс (`canonical/subclasses/` — 12
файлов), а книга даёт четыре на класс. r147 собрал так три коллегии Барда,
r169 расширяет тот же путь на остальные 11 классов: 48 подклассов книги, из
них 12 уже в пакете, 36 пишутся здесь (`phb2024.<класс>.<подкласс>`).

Источник — только уже извлечённый текст книги
(`rules_data/v2/evidence/phb-2024-ru/phb-pp050-177.txt`), без пересказа: скрипт
снимает переносы, служебные строки полосы и подписи иллюстраций, а остальное
раскладывает по умениям. Список подклассов класса и страница начала главы
берутся из корпуса (`rules_data/v2/chapters/phb-03-classes/*.json`,
`facts.subclasses` + `source.pdf_page`), уровни умений — из заголовков книги
(«N-й уровень: …»), страницы — из маркеров `=== PDF PAGE N ===`.

Что скрипт НЕ делает: не придумывает механику. У этих умений нет
типизированной активности, поэтому каждое пишется как `adjudication_required`
с дословным текстом; исполняемых полей (`activities`, `passive_effects`) в
записи нет намеренно.

Две таблицы исключений, обе проверяемые текстом:

* `FROZEN` — id бардовских коллегий из r147 заморожены (на них ссылаются
  тесты и пробы): английские слаги умений вместо транслита.
* `FEATURE_OWNER` — места, где вёрстка книги переплетает два подкласса и
  умение физически стоит под чужим заголовком (Следопыт: умения Странника фей
  набраны в правой колонке рядом с началом Сумрачного охотника). Сверено по
  тексту умения, не по порядку строк.

Проверка вместо доверия: для 12 подклассов, которые есть в SRD-пакете, число
разобранных умений обязано совпасть с числом `feature`-ссылок пакета — иначе
сборка падает, а не пишет тихо искорёженную запись.

Выход: integration/nat20/phb-2024/subclasses/<slug>.json — каталог в
.gitignore (текст книги приватный, маркер provenance — 'phb-2024-ru', license
'user_private').

    .venv/bin/python scripts/build_phb_subclasses.py
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "rules_data/v2/evidence/phb-2024-ru/phb-pp050-177.txt"
CHAPTERS = ROOT / "rules_data/v2/chapters/phb-03-classes"
OUT = ROOT / "integration/nat20/phb-2024/subclasses"
DOCUMENT_ID = "phb_2024_ru"
DOCUMENT_SHA256 = "40a8ea0225049902328fcc93aa79c14862a222148bb3bf76ce4ad5efbc8633a5"

#: Русское имя класса в корпусе -> идентификатор класса в движке.
CLASS_SLUG = {
    "Бард": "bard",
    "Варвар": "barbarian",
    "Воин": "fighter",
    "Волшебник": "wizard",
    "Друид": "druid",
    "Жрец": "cleric",
    "Колдун": "warlock",
    "Монах": "monk",
    "Паладин": "paladin",
    "Плут": "rogue",
    "Следопыт": "ranger",
    "Чародей": "sorcerer",
}

#: Подклассы книги, которые уже есть в SRD-пакете: их не пишем, пакет —
#: авторитет. Пары сверяются по числу умений с пакетом (см. check_srd_twin).
SRD = {
    ("bard", "Коллегия знаний"): "lore",
    ("barbarian", "Путь берсерка"): "berserker",
    ("fighter", "Чемпион"): "champion",
    ("wizard", "Воплотитель"): "evoker",
    ("druid", "Круг земли"): "land",
    ("cleric", "Домен жизни"): "life",
    ("warlock", "Покровительство Исчадия"): "fiend",
    ("monk", "Воин открытой ладони"): "hand",
    ("paladin", "Клятва преданности"): "devotion",
    ("rogue", "Вор"): "thief",
    ("ranger", "Охотник"): "hunter",
    ("sorcerer", "Драконье чародейство"): "draconic",
}

#: r147: id и слаги умений трёх коллегий Барда заморожены — на них ссылаются
#: тесты (`phb2024.bard.valor.combat-inspiration`) и прогон r147-live.
FROZEN = {
    ("bard", "Коллегия доблести"): dict(slug="phb2024.bard.valor", features={
        "Боевое вдохновение": "combat-inspiration",
        "Боевая подготовка": "martial-training",
        "Дополнительная атака": "extra-attack",
        "Боевая магия": "battle-magic"}),
    ("bard", "Коллегия очарования"): dict(slug="phb2024.bard.glamour", features={
        "Чарующая магия": "beguiling-magic",
        "Мантия вдохновения": "mantle-of-inspiration",
        "Мантия величия": "mantle-of-majesty",
        "Несокрушимое величие": "unbreakable-majesty"}),
    ("bard", "Коллегия танца"): dict(slug="phb2024.bard.dance", features={
        "Ослепительные фуэте": "dazzling-footwork",
        "Вдохновляющее перемещение": "inspiring-movement",
        "Совместный танец": "tandem-footwork",
        "Ведущее уклонение": "leading-evasion"}),
}

#: Умение, напечатанное под чужим заголовком: (класс, имя умения) -> подкласс.
#: Следопыт, полоса PDF 161: таблица и умения Странника фей набраны в правой
#: колонке рядом с началом Сумрачного охотника, и порядок строк врёт.
FEATURE_OWNER = {
    ("ranger", "Заклинания Странника фей"): "Странник фей",
    ("ranger", "Потустороннее очарование"): "Странник фей",
    ("ranger", "Заманивающий трюк"): "Странник фей",
}

HEADING = re.compile(r"^(\d+)-й уровень: (.+)$")
#: Вводный зачин абзаца внутри умения: «Защита.», «Виртуоз танца.».
LEAD_IN = re.compile(r"^[А-ЯЁ][а-яё]+(?: [а-яё]+)?\.\s")
PAGE_HEAD = re.compile(r"^=== PDF PAGE (\d+) ===$")
#: Служебные строки полосы: напечатанный номер страницы и колонтитул.
COLOPHON = re.compile(r"^(глава \d+ \| классы персонажей|\d{1,3})$")
#: Колонтитул главы: по нему видно, где кончается глава «Классы персонажей».
CHAPTER_HEAD = re.compile(r"^глава \d+ \| классы персонажей$")
#: Заголовок таблицы набран капителью: в извлечении это строчная, за которой
#: сразу идёт прописная внутри слова («волНа дикой магии», «приёМы Мастера боя»).
SMALL_CAPS = re.compile(r"[а-яё][А-ЯЁ]")

#: Транслитерация для id: русская механика не переводится, а переносится.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "iu", "я": "ia",
}


def slugify(text: str) -> str:
    """Русское имя -> ascii-слаг через нижнее подчёркивание."""
    out: list[str] = []
    gap = False
    for char in text.lower():
        if char in _TRANSLIT:
            out.append(_TRANSLIT[char])
            gap = False
        elif char.isalnum() and char.isascii():
            out.append(char)
            gap = False
        elif not gap and out:
            out.append("_")
            gap = True
    return "".join(out).strip("_")


def pages():
    """[(pdf_page, [line, ...]), ...] из evidence-файла."""
    out: list[tuple[int, list[str]]] = []
    current: tuple[int, list[str]] | None = None
    for line in EVIDENCE.read_text(encoding="utf-8").splitlines():
        head = PAGE_HEAD.match(line)
        if head:
            if current:
                out.append(current)
            current = (int(head.group(1)), [])
            continue
        if current is not None:
            current[1].append(line)
    if current:
        out.append(current)
    return out


def furniture(text: str) -> bool:
    """Служебная строка полосы: подпись иллюстрации или её обрывок.

    Подпись соседнего подкласса («Подкласс «Путь берсерка»») набрана мелким
    кеглем и в извлечении часто разорвана на строки: «Подкласс», ««Коллегия»,
    «знаний»». Признак подписи — вся строка целиком внутри кавычек, без текста
    снаружи; строка правила, где кавычки только часть фразы («Таблица «Кости
    энергии Пси-воина» показы…», ««Варианты приёмов» (далее…»), остаётся.
    """
    stripped = text.strip()
    if stripped == "Подкласс" or stripped.startswith("Подкласс «"):
        return True
    if len(stripped) > 70:
        return False
    quoted_open = re.fullmatch(r"«[^»]*»?", stripped)
    quoted_close = re.fullmatch(r"[^«]*»", stripped)
    return bool(quoted_open or quoted_close)


def clean(lines: list[tuple[int, str]], split_first: bool = False):
    """Снять переносы и служебные строки; вернуть [(pdf_page, paragraph)].

    Абзац рвётся только там, где книга сама его рвёт: по вводным зачинам
    («Защита.», «Виртуоз танца.», …) и, если ``split_first``, после слогана
    подкласса. Остальные строки полосы склеиваются: перенос внутри слова
    («рассказчи -» / «ки») — без пробела, обычный перенос строки — с пробелом.
    ``\ufffd`` — потерянный глиф ссылки в извлечении (не текст книги), снимается.
    """
    paragraphs: list[tuple[int, str]] = []
    pending_break = False
    for page, line in lines:
        stripped = line.strip()
        text = stripped.replace("\ufffd", "").strip()
        if not text or COLOPHON.match(text) or furniture(text):
            continue
        # Глиф ссылки в извлечении съел точку на границе фраз («Приказ �»):
        # там, где он стоял в конце строки, книга начинает новую фразу.
        lost_break = stripped.endswith("\ufffd")
        if paragraphs and paragraphs[-1][1].endswith(" -"):
            paragraphs[-1] = (paragraphs[-1][0], paragraphs[-1][1][:-2] + text)
        elif paragraphs and paragraphs[-1][1].endswith("-"):
            # Перенос по слогам без пробела перед дефисом: пробел после дефиса
            # в извлечении остаётся, и слово читается как два («модифи- катор»).
            # Склеиваем через дефис, а не выбрасываем его: у составных слов
            # («пси-воин», «кто-то») дефис — часть слова, и терять его нельзя.
            paragraphs[-1] = (paragraphs[-1][0], paragraphs[-1][1] + text)
        elif paragraphs and (pending_break or LEAD_IN.match(text) or (split_first and len(paragraphs) == 1)):
            paragraphs.append((page, text))
        elif paragraphs:
            paragraphs[-1] = (paragraphs[-1][0], paragraphs[-1][1] + " " + text)
        else:
            paragraphs.append((page, text))
        pending_break = lost_break
    return paragraphs


def printed_page(pdf_page: int) -> int:
    # Полоса PDF N несёт напечатанный номер N-1 (проверено по evidence: PDF 57 -> 56).
    return pdf_page - 1


@lru_cache(maxsize=1)
def chapters():
    """[(class_ru, class_slug, [subclass, ...], first_pdf_page), ...] из корпуса.

    Глава класса начинается с самой ранней страницы его правил; границы глав
    нужны, чтобы искать заголовки подклассов в своей полосе, а не по всей книге.
    """
    out = []
    for path in sorted(CHAPTERS.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        page = min(row["source"]["pdf_page"] for row in body["rules"])
        rule = next((row for row in body["rules"]
                     if ".podklass_" in row["id"] and row.get("facts", {}).get("subclasses")), None)
        if rule is None:
            raise SystemExit(f"{path.name}: нет правила подкласса с facts.subclasses")
        class_ru = rule["name"].split(":", 1)[0].strip()
        if class_ru not in CLASS_SLUG:
            raise SystemExit(f"{path.name}: неизвестный класс {class_ru!r}")
        out.append((class_ru, CLASS_SLUG[class_ru], tuple(rule["facts"]["subclasses"]), page))
    return out


def chapter_end(all_pages) -> int:
    """Страница после конца последней главы: последняя полоса с колонтитулом
    «глава 3 | классы персонажей» плюс один (дальше идёт глава 4)."""
    last = max(page for page, lines in all_pages
               if any(CHAPTER_HEAD.match(line.strip()) for line in lines))
    return last + 1


def sections():
    """Все подклассы книги: [{class_ru, class_slug, name, rows, page}, ...].

    Заголовок подкласса — отдельная строка с его именем; порядок берётся
    книжный (у Жреца он отличается от корпусного), состав сверяется с корпусом.
    Конец раздела — заголовок следующего подкласса, поэтому хвост главы класса
    в чужое умение не попадает.
    """
    all_pages = pages()
    table = chapters()
    found = []
    for index, (class_ru, class_slug, names, first) in enumerate(table):
        # Полоса перед началом следующей главы — рекламная страница класса
        # («ВарварВарвар»), а не текст правил: окно кончается перед ней.
        last = (table[index + 1][3] - 1) if index + 1 < len(table) else chapter_end(all_pages)
        rows = [(page, line.strip()) for page, lines in all_pages if first <= page < last
                for line in lines]
        heads = [(pos, rows[pos][1]) for pos in range(len(rows)) if rows[pos][1] in names]
        seen = [name for _, name in heads]
        if set(seen) != set(names):
            raise SystemExit(f"{class_ru}: не найдены разделы {sorted(set(names) - set(seen))}")
        if len(seen) != len(set(seen)):
            raise SystemExit(f"{class_ru}: раздел подкласса встретился дважды")
        for position, (pos, name) in enumerate(heads):
            stop = heads[position + 1][0] if position + 1 < len(heads) else len(rows)
            found.append(dict(class_ru=class_ru, class_slug=class_slug, name=name,
                              rows=rows[pos:stop], page=rows[pos][0]))
    return found


def parse(section):
    """Раздел подкласса -> (описание, [умение, ...]) по заголовкам уровней."""
    body = section["rows"][1:]
    first = next((pos for pos, (_, text) in enumerate(body) if HEADING.match(text)), len(body))
    intro = clean(body[:first], split_first=True)
    features: list[dict] = []
    current: dict | None = None
    for page, text in body[first:]:
        found = HEADING.match(text.strip())
        if found:
            if current:
                features.append(current)
            current = dict(level=int(found.group(1)), name=found.group(2).strip(), rows=[])
            continue
        if current is not None:
            current["rows"].append((page, text))
    if current:
        features.append(current)
    section["intro"] = intro
    section["features"] = features
    return section


def heading_like(text: str) -> bool:
    """Строка похожа на заголовок таблицы: короткая, с прописной, без точки."""
    stripped = text.strip()
    return (2 <= len(stripped) <= 60 and stripped[:1].isupper()
            and stripped[-1:] not in (".", ",", ":", ";", "!", "?", "…", "»"))


def split_tail(feature, others):
    """Отделить таблицу, набранную после последнего умения подкласса.

    Таблица стоит в конце раздела, хотя относится к одному из умений (приёмы
    Мастера боя — к «Боевому превосходству», волна дикой магии — к «Волне
    дикой магии»). Граница — заголовок капителью («волНа дикой магии») или
    обычный подзаголовок, на который ссылается другое умение этого подкласса
    («Варианты приёмов»). Возвращает (умение, таблица, владелец-по-имени|None).
    """
    rows = feature["rows"]
    for position, (_, text) in enumerate(rows):
        stripped = text.strip()
        if len(stripped) > 60:
            continue
        if SMALL_CAPS.search(stripped):
            return dict(feature, rows=rows[:position]), rows[position:], stripped
        if heading_like(stripped):
            key = normalize(stripped)
            for other in others:
                if other is feature:
                    continue
                said = normalize(" ".join(text for _, text in other["rows"]))
                if f"«{key}»" in said or normalize(other["name"]) == key:
                    return dict(feature, rows=rows[:position]), rows[position:], stripped
    return feature, [], None


def reassign(parsed):
    """Вернуть умения, напечатанные под чужим заголовком, их подклассу."""
    by_name = {(row["class_slug"], row["name"]): row for row in parsed}
    for row in parsed:
        for feature in list(row["features"]):
            owner = FEATURE_OWNER.get((row["class_slug"], feature["name"]))
            if owner and owner != row["name"]:
                by_name[(row["class_slug"], owner)]["features"].append(feature)
                row["features"].remove(feature)
    for row in parsed:
        # Уровни умений — из книги; группировка по уровню стабильная, чтобы
        # перенесённое умение встало на своё место, а не в конец.
        row["features"].sort(key=lambda feature: feature["level"])
        attach_tail(row)
    return parsed


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def attach_tail(section) -> None:
    """Таблицу в конце раздела отдать умению, к которому она относится."""
    if not section["features"]:
        return
    body, tail, title_line = split_tail(section["features"][-1], section["features"])
    if not tail:
        return
    title = normalize(title_line)
    section["features"][-1] = body
    same_name = [row for row in section["features"] if normalize(row["name"]) == title]
    mentioned = [row for row in section["features"]
                 if title and title in normalize(" ".join(text for _, text in row["rows"]))]
    owner = same_name[0] if same_name else (mentioned[0] if len(mentioned) == 1 else None)
    if owner is None:
        owner = section["features"][-1]
    print(f"~ {section['name']}: таблица «{title_line}» -> «{owner['name']}»")
    owner["rows"] = owner["rows"] + tail


def build(section):
    class_ru, class_slug, name = section["class_ru"], section["class_slug"], section["name"]
    intro, features = section["intro"], section["features"]
    if not intro:
        raise SystemExit(f"{name}: пустое описание")
    if len(features) < 3:
        raise SystemExit(f"{name}: разобрано {len(features)} умений — раздел не сошёлся")

    frozen = FROZEN.get((class_slug, name), {})
    slug = frozen.get("slug") or f"phb2024.{class_slug}.{slugify(name)}"
    out_features = []
    for feature in features:
        feature_slug = (frozen.get("features", {}).get(feature["name"])
                        or slugify(feature["name"]))
        paragraphs = clean(feature["rows"])
        if not paragraphs:
            raise SystemExit(f"{name} / {feature['name']}: пустой текст")
        pages_hit = sorted({printed_page(page) for page, _ in paragraphs})
        out_features.append(dict(
            slug=f"{slug}.{feature_slug}", name=feature["name"], level=feature["level"],
            printed_pages=pages_hit, text="\n\n".join(text for _, text in paragraphs),
            # Механики сверх текста в книге нет: типизированной активности у
            # этих умений не существует, поэтому честный статус — решение
            # Мастера, а не «исполняется».
            execution_status="adjudication_required",
            blocker=("типизированной активности нет: «%s» (PHB 2024, стр. %s) "
                     "исполняется решением Мастера, в движке только текст"
                     % (feature["name"], ", ".join(str(page) for page in pages_hit))),
        ))

    description = "\n\n".join(text for _, text in intro)
    pages_hit = sorted({printed_page(page) for page, _ in intro})
    return dict(
        slug=slug,
        name=name,
        description=description,
        identifier=slug,
        class_identifier=class_slug,
        spellcasting=dict(ability="", progression="none"),
        advancement=[],
        granted_features=[dict(ref_type="feature", slug=row["slug"], level=row["level"],
                               optional=False) for row in out_features],
        feature_choices=[],
        provenance=dict(kind="phb", source="phb-2024-ru", license_tag="user_private",
                        document_id=DOCUMENT_ID, document_sha256=DOCUMENT_SHA256,
                        printed_pages=pages_hit, pdf_pages=sorted({page for page, _ in intro}),
                        section=f"{class_ru}, глава 3 «Классы персонажей»: "
                                f"Подкласс «{name}»"),
        review=dict(known_divergence=None, requires_user_decision=False),
        features=out_features,
    )


def check_srd_twin(class_slug: str, name: str, record: dict) -> None:
    """Сверка с пакетом: у подкласса, который есть в SRD, должно быть столько же
    умений, сколько `feature`-ссылок пакета. Ловит разъезд разбора с книгой."""
    try:
        from dnd5e_srd_data.loader import BundledAssetLoader
    except ImportError:  # сборка вне .venv — сверка недоступна, но не молча
        print("! сверка с SRD пропущена: dnd5e_srd_data не импортируется", file=sys.stderr)
        return
    twin = BundledAssetLoader().get_subclass(SRD[(class_slug, name)])
    expected = len([ref for ref in twin.granted_features if ref.ref_type == "feature"])
    got = len(record["features"])
    if got != expected:
        raise SystemExit(f"{name}: разобрано {got} умений, в SRD {expected}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    parsed = reassign([parse(section) for section in sections()])
    records, srds = [], []
    for section in parsed:
        record = build(section)
        if (section["class_slug"], section["name"]) in SRD:
            check_srd_twin(section["class_slug"], section["name"], record)
            srds.append(record)
        else:
            records.append(record)

    seen: set[str] = set()
    for record in records:
        if record["slug"] in seen:
            raise SystemExit(f"повтор id: {record['slug']}")
        seen.add(record["slug"])
        for feature in record["features"]:
            if feature["slug"] in seen:
                raise SystemExit(f"повтор id умения: {feature['slug']}")
            seen.add(feature["slug"])

    keep = {record["slug"] + ".json" for record in records}
    for stale in OUT.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()

    for record in records:
        path = OUT / f"{record['slug']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.relative_to(ROOT)}: {len(record['features'])} умений, "
              f"стр. {record['provenance']['printed_pages']}")
        for feature in record["features"]:
            print(f"    {feature['level']} ур. {feature['name']} [{feature['slug']}] "
                  f"стр. {feature['printed_pages']}: {feature['text'][:70]}…")
    print(f"итого: подклассов книги {len(records) + len(srds)}, "
          f"в SRD {len(srds)} (сверено по числу умений), записано {len(records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
