"""Local-first reading of a pasted character sheet.

A player often has the hero already written down - on paper, in another app,
in a text file. This module reads that text locally: it never calls a model,
never guesses a value the text does not contain, and never clamps one. Every
name it resolves is offered with the alternatives the corpus also matches, so
the player confirms the reading in the wizard instead of trusting it.
"""

import re
import unicodedata

from .nat20_wizard import ABILITY_ORDER, WizardDraft

# Words a sheet uses for each score, in English and Russian.
ABILITY_WORDS = {
    "strength": ("strength", "str", "сила", "сил"),
    "dexterity": ("dexterity", "dex", "ловкость", "ловк"),
    "constitution": ("constitution", "con", "телосложение", "телосл"),
    "intelligence": ("intelligence", "int", "интеллект", "интелл"),
    "wisdom": ("wisdom", "wis", "мудрость", "мудр"),
    "charisma": ("charisma", "cha", "харизма", "харизм"),
}

SCORE = re.compile(r"(?<![-\d])(\d{1,2})(?![-\d])")

# The corpus is English. A player who writes their sheet in Russian names the class
# and the species in Russian, so each catalogue slug also answers to those words.
RU_NAMES = {
    'fighter': ('воин', 'боец', 'воитель'),
    'wizard': ('волшебник', 'маг', 'чародей'),
    'cleric': ('жрец', 'клирик'),
    'rogue': ('плут', 'вор'),
    'barbarian': ('варвар',),
    'bard': ('бард',),
    'druid': ('друид',),
    'monk': ('монах',),
    'paladin': ('паладин',),
    'ranger': ('следопыт',),
    'sorcerer': ('колдун крови', 'соркерер'),
    'warlock': ('колдун',),
    'human': ('человек', 'люди'),
    'elf-high': ('эльф высший', 'высший эльф', 'эльф'),
    'elf-wood': ('лесной эльф', 'эльф лесной'),
    'elf-drow': ('дроу', 'тёмный эльф', 'темный эльф'),
    'dwarf': ('дварф', 'гном'),
    'halfling': ('полурослик', 'хоббит'),
    'gnome-forest': ('лесной гном',),
    'gnome-rock': ('скальный гном',),
    'dragonborn': ('драконорождённый', 'драконорожденный'),
    'goliath': ('голиаф',),
    'orc': ('орк',),
    'tiefling-infernal': ('тифлинг', 'дьявольский тифлинг'),
    'tiefling-abyssal': ('тифлинг бездны',),
    'tiefling-chthonic': ('тифлинг хтонический',),
    'acolyte': ('послушник',),
    'criminal': ('преступник',),
    'sage': ('мудрец',),
    'soldier': ('солдат', 'воин'),
}



def _fold(text):
    """Lowercase, accent-free text with collapsed spaces."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _lines(text):
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _after(line, words):
    """The value part of "Label: value" when the label matches one of words."""
    label, sep, rest = line.partition(":")
    if not sep:
        label, sep, rest = line.partition("=")
    if not sep:
        return None
    folded = _fold(label)
    return rest.strip() if any(word in folded for word in words) else None


def _scores(lines):
    """Every ability score the text states, with the line it came from.

    One line often carries several scores ("Сила 16, Ловкость 14"), so each
    ability takes the nearest number that follows its own name, never simply the
    last number on the line.
    """
    found = {}
    for line in lines:
        folded = _fold(line)
        for ability, words in ABILITY_WORDS.items():
            if ability in found:
                continue
            for word in words:
                at = folded.find(word)
                if at < 0:
                    continue
                tail = folded[at + len(word):]
                numbers = SCORE.findall(tail)
                if not numbers:
                    continue
                value = int(numbers[0])
                if 1 <= value <= 30:
                    found[ability] = dict(value=value, line=line)
                break
    return found

def _pick(line, words, catalogue, limit=6):
    """The catalogue entries a "Label: value" line matches, best first."""
    raw = _after(line, words)
    if not raw:
        return []
    wanted = _fold(raw)
    exact, partial = [], []
    for slug in catalogue:
        name = _fold(slug.replace("_", " ").replace("-", " "))
        # A Russian sheet says "Класс: воин" where the corpus says "fighter".
        aliases = [_fold(alias) for alias in RU_NAMES.get(slug, ())]
        if wanted == name or wanted == _fold(slug) or wanted in aliases:
            exact.append(slug)
        elif wanted and (wanted in name or name in wanted
                         or any(wanted in alias or alias in wanted for alias in aliases)):
            partial.append(slug)
    return (exact + partial)[:limit]


def _field(lines, words):
    for line in lines:
        value = _after(line, words)
        if value:
            return value
    return ""


def parse_sheet(text, loader=None):
    """Read a pasted sheet into a partial draft plus what needs confirming.

    The result names every fact it took, the line it took it from, and the
    alternatives the corpus also offers. Nothing is applied: the wizard decides
    whether the reading is right, and the original text is returned unchanged.
    """
    from .nat20_wizard import _frame

    if loader is None:
        loader = _frame()[0]
    original = str(text or "")
    lines = _lines(original)
    draft = {}
    fields = []
    ambiguous = []
    unknown = []

    def note(field, value, line, alternatives=()):
        fields.append(dict(field=field, value=value, line=line, alternatives=list(alternatives)))

    species_rows = list(loader.list_slugs("species"))
    class_rows = list(loader.list_slugs("classes"))
    background_rows = list(loader.list_slugs("backgrounds"))

    for line in lines:
        matches = _pick(line, ("species", "race", "вид", "раса"), species_rows)
        if matches:
            if len(matches) > 1:
                ambiguous.append(dict(field="species", value=matches[0], line=line, alternatives=matches))
            draft["species_slug"] = matches[0]
            note("species", matches[0], line, matches[1:])
            break
    for line in lines:
        matches = _pick(line, ("class", "класс"), class_rows)
        if matches:
            if len(matches) > 1:
                ambiguous.append(dict(field="class", value=matches[0], line=line, alternatives=matches))
            draft["class_slug"] = matches[0]
            note("class", matches[0], line, matches[1:])
            break
    for line in lines:
        matches = _pick(line, ("background", "предыстория", "origin"), background_rows)
        if matches:
            if len(matches) > 1:
                ambiguous.append(dict(field="background", value=matches[0], line=line, alternatives=matches))
            draft["background_slug"] = matches[0]
            note("background", matches[0], line, matches[1:])
            break

    raw_level = ""
    for line in lines:
        raw_level = _after(line, ("level", "уровень")) or ""
        if raw_level:
            break
    if raw_level:
        numbers = SCORE.findall(raw_level)
        if numbers and 1 <= int(numbers[0]) <= 20:
            draft["level"] = int(numbers[0])
            note("level", int(numbers[0]), raw_level)
        elif numbers:
            unknown.append(dict(field="level", value=numbers[0],
                reason="Уровень вне 1..20: значение не применено."))

    scores = _scores(lines)
    if scores:
        draft["ability_scores"] = {ability: row["value"] for ability, row in scores.items()}
        for ability, row in scores.items():
            note("ability." + ability, row["value"], row["line"])
        if len(scores) < len(ABILITY_ORDER):
            unknown.append(dict(field="ability_scores", value=len(scores),
                reason="В тексте нашлись не все шесть характеристик; остальные выберите в мастере."))

    name = ""
    for line in lines:
        if "name" in _fold(line) or "имя" in _fold(line):
            value = _after(line, ("name", "имя", "герой"))
            if value:
                name = value[:80]
                note("name", name, line)
                break
    if not name and lines:
        first = lines[0]
        if ":" not in first and len(first) <= 80:
            name = first
            note("name", name, first)
    if name:
        draft["name"] = name

    missing = [field for field in ("species_slug", "class_slug") if not draft.get(field)]
    return dict(
        source=original,
        draft=WizardDraft.model_validate(draft).model_dump(mode="json") if draft else None,
        fields=fields,
        ambiguous=ambiguous,
        unknown=unknown,
        missing=missing,
        ready=not missing and not ambiguous,
    )


__all__ = ["ABILITY_WORDS", "parse_sheet"]