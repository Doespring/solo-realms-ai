"""Reviewed bridge from the SRD 5.2 starting-equipment prose to corpus items.

The pinned corpus publishes each class's and background's starting kit as a
sentence on the document ('Starting Equipment' then 'Choose A or B: (A) Chain
Mail, Shield, Mace, ... and 7GP; or (B) 110GP'), and the background documents
additionally carry a Foundry equipment table. Neither can be turned into corpus
items mechanically:

* the background table references Foundry compendium keys
  (Compendium.dnd5e.equipment24.Item.phbwepSpear00000) and the item documents
  carry no Foundry uuid, so no id map can resolve them;
* the class documents carry no equipment rows at all, only wealth.

So this module is the reviewed transcription: the kit composition below is
copied from the corpus prose, every item is a real corpus slug, and
tests/test_nat20_kit.py re-reads that prose and fails when the corpus and this
table drift apart. It adds no rule arithmetic and invents no item: entries the
corpus has no document for are reported as unresolved instead of being guessed
at.
"""
from __future__ import annotations

KIT_PROSE = 'Starting Equipment'
BACKGROUND_PROSE = 'Equipment:'

#: Prose item name -> corpus slug. ``None`` means the corpus has no such
#: document; the grant then reports the entry as unresolved rather than
#: substituting something that looks similar.
ITEM_BY_NAME = {
    "chain mail": "chain-mail",
    "chain shirt": "chain-shirt",
    "studded leather armor": "studded-leather-armor",
    "leather armor": "leather-armor",
    "shield": "shield",
    "greatsword": "greatsword",
    "longsword": "longsword",
    "shortsword": "shortsword",
    "scimitar": "scimitar",
    "flail": "flail",
    "greataxe": "greataxe",
    "handaxe": "handaxe",
    "mace": "mace",
    "sickle": "sickle",
    "spear": "spear",
    "dagger": "dagger",
    "javelin": "javelin",
    "quarterstaff": "quarterstaff",
    "longbow": "longbow",
    "shortbow": "shortbow",
    "arrows": "arrows",
    "quiver": "quiver",
    "dungeoneer's pack": "dungeoneers-pack",
    "explorer's pack": "explorers-pack",
    "priest's pack": "priests-pack",
    "scholar's pack": "scholars-pack",
    "burglar's pack": "burglars-pack",
    "entertainer's pack": "entertainers-pack",
    "herbalism kit": "herbalism-kit",
    "healer's kit": "healers-kit",
    "thieves' tools": "thieves-tools",
    "calligrapher's supplies": "calligraphers-supplies",
    "crowbar": "crowbar",
    "pouch": "pouch",
    "book": "book",
    "robe": "robe",
    "parchment": "parchment",
    "traveler's clothes": "clothes-travelers",
    # Focus and symbol variants the corpus does publish as concrete items.
    "crystal": "crystal",
    "orb": "orb",
    "rod": "rod",
    "staff": "staff",
    "wand": "wand",
    "amulet": "amulet",
    "emblem": "emblem",
    "reliquary": "reliquary",
    "sprig of mistletoe": "sprig-of-mistletoe",
    "wooden staff": "wooden-staff",
    # Named in the prose but absent from the corpus; reported, never invented.
    "spellbook": None,
    "musical instrument": None,
    "gaming set": None,
    "artisan's tools": None,
}

#: The variant groups the prose leaves open ("Arcane Focus (Quarterstaff)",
#: "Holy Symbol", "Gaming Set (same as above)"). An empty options tuple means the
#: corpus publishes no document for that group at all.
PICKS = {
    "arcane-focus": (
        "Магическая фокусировка",
        (("crystal", "Кристалл"), ("orb", "Сфера"), ("rod", "Жезл"),
         ("staff", "Посох"), ("wand", "Палочка")),
    ),
    "druidic-focus": (
        "Друидическая фокусировка",
        (("sprig-of-mistletoe", "Веточка омелы"), ("wooden-staff", "Деревянный посох")),
    ),
    "holy-symbol": (
        "Священный символ",
        (("amulet", "Амулет"), ("emblem", "Эмблема"), ("reliquary", "Реликварий")),
    ),
    "gaming-set": ("Набор для игр", ()),
    "musical-instrument": ("Музыкальный инструмент", ()),
    "artisan-tools": ("Ремесленные инструменты", ()),
}

#: (kind, source slug) -> kits, in the corpus's own letter order. Each row is
#: (key, label, items, picks, notes, gold_gp): items is ((slug, count), ...),
#: picks is ((group, default slug or None), ...) and notes names prose entries
#: with no corpus document.
KITS = {
    ("class", "barbarian"): (
        ("A", "Топоры и набор исследователя",
         (("greataxe", 1), ("handaxe", 4), ("explorers-pack", 1)), (), (), 15),
        ("B", "Только золото", (), (), (), 75),
    ),
    ("class", "bard"): (
        ("A", "Кожа, кинжалы и набор артиста",
         (("leather-armor", 1), ("dagger", 2), ("entertainers-pack", 1)),
         (("musical-instrument", None),),
         ("Музыкальный инструмент: в корпусе нет документа инструмента.",), 19),
        ("B", "Только золото", (), (), (), 90),
    ),
    ("class", "cleric"): (
        ("A", "Кольчужная рубаха, щит и булава",
         (("chain-shirt", 1), ("shield", 1), ("mace", 1), ("priests-pack", 1)),
         (("holy-symbol", "amulet"),), (), 7),
        ("B", "Только золото", (), (), (), 110),
    ),
    ("class", "druid"): (
        ("A", "Кожа, щит и серп",
         (("leather-armor", 1), ("shield", 1), ("sickle", 1), ("explorers-pack", 1),
          ("herbalism-kit", 1)),
         (("druidic-focus", "wooden-staff"),), (), 9),
        ("B", "Только золото", (), (), (), 50),
    ),
    ("class", "fighter"): (
        ("A", "Кольчуга и двуручный меч",
         (("chain-mail", 1), ("greatsword", 1), ("flail", 1), ("javelin", 8),
          ("dungeoneers-pack", 1)), (), (), 4),
        ("B", "Кожа с шипами и лук",
         (("studded-leather-armor", 1), ("scimitar", 1), ("shortsword", 1),
          ("longbow", 1), ("arrows", 20), ("quiver", 1), ("dungeoneers-pack", 1)),
         (), (), 11),
        ("C", "Только золото", (), (), (), 155),
    ),
    ("class", "monk"): (
        ("A", "Копьё и кинжалы",
         (("spear", 1), ("dagger", 5), ("explorers-pack", 1)),
         (("artisan-tools", None),),
         ("Ремесленные инструменты или музыкальный инструмент владения: документа в корпусе нет.",), 11),
        ("B", "Только золото", (), (), (), 50),
    ),
    ("class", "paladin"): (
        ("A", "Кольчуга, щит и длинный меч",
         (("chain-mail", 1), ("shield", 1), ("longsword", 1), ("javelin", 6),
          ("priests-pack", 1)),
         (("holy-symbol", "amulet"),), (), 9),
        ("B", "Только золото", (), (), (), 150),
    ),
    ("class", "ranger"): (
        ("A", "Кожа с шипами и длинный лук",
         (("studded-leather-armor", 1), ("scimitar", 1), ("shortsword", 1),
          ("longbow", 1), ("arrows", 20), ("quiver", 1), ("explorers-pack", 1)),
         (("druidic-focus", "sprig-of-mistletoe"),), (), 7),
        ("B", "Только золото", (), (), (), 150),
    ),
    ("class", "rogue"): (
        ("A", "Кожа, кинжалы и воровские инструменты",
         (("leather-armor", 1), ("dagger", 2), ("shortsword", 1), ("shortbow", 1),
          ("arrows", 20), ("quiver", 1), ("thieves-tools", 1), ("burglars-pack", 1)),
         (), (), 8),
        ("B", "Только золото", (), (), (), 100),
    ),
    ("class", "sorcerer"): (
        ("A", "Копьё, кинжалы и фокусировка",
         (("spear", 1), ("dagger", 2), ("dungeoneers-pack", 1)),
         (("arcane-focus", "crystal"),), (), 28),
        ("B", "Только золото", (), (), (), 50),
    ),
    ("class", "warlock"): (
        ("A", "Кожа, серп и фокусировка",
         (("leather-armor", 1), ("sickle", 1), ("dagger", 2), ("book", 1),
          ("scholars-pack", 1)),
         (("arcane-focus", "orb"),), (), 15),
        ("B", "Только золото", (), (), (), 100),
    ),
    ("class", "wizard"): (
        ("A", "Кинжалы, фокусировка и мантия",
         (("dagger", 2), ("robe", 1), ("scholars-pack", 1)),
         (("arcane-focus", "staff"),),
         ("Книга заклинаний: в корпусе нет документа предмета.",), 5),
        ("B", "Только золото", (), (), (), 55),
    ),
    ("background", "acolyte"): (
        ("A", "Принадлежности писца",
         (("calligraphers-supplies", 1), ("book", 1), ("parchment", 10), ("robe", 1)),
         (("holy-symbol", "amulet"),), (), 8),
        ("B", "Только золото", (), (), (), 50),
    ),
    ("background", "criminal"): (
        ("A", "Кинжалы и воровские инструменты",
         (("dagger", 2), ("thieves-tools", 1), ("crowbar", 1), ("pouch", 2),
          ("clothes-travelers", 1)), (), (), 16),
        ("B", "Только золото", (), (), (), 50),
    ),
    ("background", "sage"): (
        ("A", "Посох и книги",
         (("quarterstaff", 1), ("calligraphers-supplies", 1), ("book", 1),
          ("parchment", 8), ("robe", 1)), (), (), 8),
        ("B", "Только золото", (), (), (), 50),
    ),
    ("background", "soldier"): (
        ("A", "Копьё, лук и лечебный набор",
         (("spear", 1), ("shortbow", 1), ("arrows", 20), ("healers-kit", 1),
          ("quiver", 1), ("clothes-travelers", 1)),
         (("gaming-set", None),),
         ("Набор для игр: владение выбирается отдельно, документа предмета в корпусе нет.",), 14),
        ("B", "Только золото", (), (), (), 50),
    ),
}


def kits(kind: str, source: str) -> tuple[dict[str, object], ...]:
    """The reviewed kits for one class or background, letters in corpus order."""
    rows = KITS.get((kind, source))
    if rows is None:
        raise ValueError("нет записанного начального комплекта: " + kind + " " + source)
    out = []
    for key, label, items, picks, notes, gold in rows:
        out.append(
            {
                "key": key,
                "label": label,
                "gold_gp": gold,
                "items": [{"slug": slug, "count": count} for slug, count in items],
                "picks": [
                    {
                        "group": group,
                        "label": PICKS[group][0],
                        "options": [
                            {"slug": slug, "name": name} for slug, name in PICKS[group][1]
                        ],
                        "default": default,
                    }
                    for group, default in picks
                ],
                "notes": list(notes),
            }
        )
    return tuple(out)


def kit(kind: str, source: str, key: str) -> dict[str, object]:
    for row in kits(kind, source):
        if row["key"] == key:
            return row
    raise ValueError("неизвестный начальный комплект " + repr(key) + " для " + kind + " " + source)


def grant(
    kind: str, source: str, key: str, picks: dict[str, str] | None = None
) -> dict[str, object]:
    """Resolve one kit to its items, its gold and its unresolved entries.

    ``picks`` answers the kit's open variant groups (``{"arcane-focus":
    "crystal"}``); an unanswered group falls back to the default the prose
    implies. A pick the group does not offer is rejected, not silently replaced.
    """
    chosen = dict(picks or {})
    row = kit(kind, source, key)
    resolved: list[dict[str, object]] = [dict(item) for item in row["items"]]
    groups = []
    unresolved = [
        {"label": note, "reason": "в корпусе нет документа предмета"}
        for note in row["notes"]
    ]
    known = {group["group"] for group in row["picks"]}
    for extra in sorted(set(chosen) - known):
        raise ValueError("неизвестная группа выбора комплекта: " + repr(extra))
    for group in row["picks"]:
        name = group["group"]
        options = [option["slug"] for option in group["options"]]
        pick = chosen.get(name, group["default"])
        if not options:
            if pick is not None:
                raise ValueError(
                    "группа " + repr(group["label"]) + " не имеет вариантов в корпусе"
                )
            groups.append(dict(group, chosen=None))
            continue
        if pick not in options:
            raise ValueError(
                group["label"] + ": допустимые варианты " + repr(sorted(options))
                + ", получено " + repr(pick)
            )
        resolved.append({"slug": pick, "count": 1})
        groups.append(dict(group, chosen=pick))
    counts: dict[str, int] = {}
    for item in resolved:
        slug = str(item["slug"])
        counts[slug] = counts.get(slug, 0) + int(item["count"])
    return {
        "kind": kind,
        "source": source,
        "key": row["key"],
        "label": row["label"],
        "gold_gp": row["gold_gp"],
        "items": [{"slug": slug, "count": count} for slug, count in sorted(counts.items())],
        "picks": groups,
        "unresolved": unresolved,
    }


def equipment_slugs(grants) -> tuple[str, ...]:
    """The distinct item slugs a set of grants puts in the character's pack.

    ``CharacterBuildSpec.equipment`` is a slug tuple, so the kit's counts stay in
    the kit record (the sheet derives AC from the slugs) and an unresolved entry
    never becomes a slug.
    """
    out: list[str] = []
    for row in grants:
        for item in row["items"]:
            slug = str(item["slug"])
            if slug not in out:
                out.append(slug)
    return tuple(out)


def prose_line(kind: str, source: str, description: str) -> str:
    """The corpus's own starting-equipment sentence, for the drift test.

    A class document has a "Starting Equipment" heading; a background document
    has a one-line "Equipment:" field. Both end at the first newline, so the same
    reader serves the drift test for either kind.
    """
    for marker in (KIT_PROSE, BACKGROUND_PROSE):
        index = description.find(marker)
        if index >= 0:
            rest = description[index + len(marker):].lstrip()
            return rest.split(chr(10), 1)[0].strip()
    raise ValueError(kind + " " + source + " has no starting-equipment section")


def normalize(name: str) -> str:
    """Compare-friendly form of a prose item name."""
    cleaned = name.replace(chr(0xAD), "").replace(chr(0x2019), "'")
    return " ".join(cleaned.split()).strip(" .,").lower()


__all__ = [
    "ITEM_BY_NAME",
    "KITS",
    "KIT_PROSE",
    "PICKS",
    "equipment_slugs",
    "grant",
    "kit",
    "kits",
    "normalize",
    "prose_line",
]
