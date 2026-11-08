"""Pinned Nat20 runtime + minimal typed Foundry 2024 corpus. No fallback packs."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from .content_verification_cache import verify_tree, read_json

from dnd5e_srd_data.loader import MemoryAssetLoader
from dnd5e_srd_data.schema.class_ import Class
from dnd5e_srd_data.schema.feature import Feature
from dnd5e_srd_data.schema.item import Armor, Weapon
from dnd5e_srd_data.schema.monster import Monster
from dnd5e_srd_data.schema.species import Species

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "integration/nat20/imported"
VENDOR = ROOT / "vendor/nat20"

# One trusted registry for both the worker and the master's available check IDs.
# (kind, skill, ability, dc). Every id the engine publishes in 'available_checks'
# must be reachable from chat, otherwise the table has a sheet with eighteen
# skills and only three of them can be used. The DC is the standard difficulty
# for a routine attempt at the named task; a scene may override it through
# definition.checks.
_SKILLS = {"acrobatics": "dex", "animal_handling": "wis", "arcana": "int", "athletics": "str",
           "deception": "cha", "history": "int", "insight": "wis", "intimidation": "cha",
           "investigation": "int", "medicine": "wis", "nature": "int", "perception": "wis",
           "performance": "cha", "persuasion": "cha", "religion": "int", "sleight_of_hand": "dex",
           "stealth": "dex", "survival": "wis"}
_ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}
CHECKS = {name: ("skill", name, None, 15) for name in _SKILLS}
CHECKS.update({name: ("ability", None, name, 15) for name in _ABILITIES})
CHECKS.update({"save-" + name: ("saving_throw", None, name.upper(), 15) for name in _ABILITIES})
# Perception is called for constantly at the table, so its default is lower.
CHECKS["perception"] = ("skill", "perception", None, 12)


def weapons():
    """Every weapon the D&D 2024 dataset knows, as the engine names it.

    The chat used to offer four hard-coded weapons (club, longsword, shortbow,
    unarmed) while the engine resolved attacks against all 86. The table could
    not say 'I draw a dagger' because nothing told it the word existed.
    """
    from dnd5e_srd_data.loader import BundledAssetLoader
    loader = BundledAssetLoader()
    found = {}
    for slug in loader.list_slugs('items'):
        try:
            item = loader.get_weapon(slug)
        except Exception:
            continue  # not a weapon: armour, gear, magic item
        if item is None:
            continue
        found[slug] = dict(name=item.name, category=str(item.weapon_category),
                           damage=[(part.dice, part.damage_type) for part in item.damage_parts],
                           mastery=str(item.mastery) if item.mastery else '',
                           weight=item.weight)
    return found


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


# One reviewed patch on top of the pinned commit. Every file it touches is listed
# here with the SHA of the reviewed content, so the pin stays an exact inventory:
# an edit that is not in this table is still an unreviewed revision. UPSTREAM.json
# keeps the upstream SHA of the patched files; the reviewed SHAs live beside it in
# vendor/nat20/PATCHES.json and both sides are checked.
PATCHES = VENDOR / 'PATCHES.json'


def _reviewed(lock):
    """The pin's file map with every reviewed patch applied to it."""
    if not PATCHES.is_file():
        return dict(lock["files"])
    patches = read_json(PATCHES)
    if patches.get("commit") != lock["commit"]:
        raise ValueError("Reviewed Nat20 patch was made against another upstream commit")
    files = dict(lock["files"])
    for entry in patches.get("files") or ():
        name, sha, kind = entry.get("path"), entry.get("sha256"), entry.get("kind")
        inside = name in files if kind == "modified" else kind == "added"
        if (not inside or not isinstance(name, str) or not name.endswith(".py")
                or not isinstance(sha, str) or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)):
            raise ValueError("Reviewed Nat20 patch names a file outside the pin")
        files[name] = sha
    return files


def release(selector=None):
    import dnd5e_engine
    import dnd5e_srd_data
    lock = read_json(VENDOR / 'UPSTREAM.json')
    if lock["commit"] != "e187c813bb4e43c4e4e62942ecd83831f1cdbcaa":
        raise ValueError("Unreviewed Nat20 upstream revision")
    reviewed = _reviewed(lock)
    installed = {"dnd5e-engine": Path(dnd5e_engine.__file__).parent,
                 "dnd5e-srd-data": Path(dnd5e_srd_data.__file__).parent}
    runtime_hashes = {}
    for package, package_root in installed.items():
        if version(package) != "0.7.0":
            raise ValueError("Nat20 package version mismatch")
        prefix = f"packages/{package}/src/{package.replace('-', '_')}/"
        pinned = {name.removeprefix(prefix): sha for name, sha in lock["files"].items()
                  if name.startswith(prefix) and name.endswith(".py")}
        expected = {name.removeprefix(prefix): sha for name, sha in reviewed.items()
                    if name.startswith(prefix) and name.endswith(".py")}
        # The installed tree must match the pin plus every reviewed patch, file
        # for file; an unlisted edit is still an unreviewed revision.
        verify_tree(package_root, expected, suffix='.py')
        # The fingerprint keeps identifying the *upstream* pin, so every
        # historical release digest still resolves to these same bytes. The
        # reviewed patch is identified by PATCHES.json instead: it adds a
        # module and changes no existing code path, so replay is identical.
        runtime_hashes[package] = digest(pinned)
    if version("d20") != "1.1.2" or version("lark-parser") != "0.9.0":
        raise ValueError("Nat20 dice dependency changed")
    manifest = read_json(CONTENT / 'manifest.json')
    if (manifest["rules_version"], manifest["foundry_version"], manifest["nat20_version"]) != ("dnd2024", "5.3.3", "0.7.0"):
        raise ValueError("Content rules edition/release mismatch")
    documents = {"classes": [], "species": [], "features": [], "items": [], "monsters": []}
    models = {"classes": Class, "species": Species, "features": Feature, "monsters": Monster}
    identities = set()
    for record in manifest["documents"]:
        path = (CONTENT / record["file"]).resolve()
        if not path.is_relative_to(CONTENT.resolve()) or record["rules_version"] != "dnd2024" or record["license"] != "CC-BY-4.0":
            raise ValueError("Unreviewed content provenance")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Imported content changed")
        category = record["category"]
        body = json.loads(raw)
        if body["slug"] != record["slug"] or (category, body["slug"]) in identities:
            raise ValueError("Content identity mismatch")
        identities.add((category, body["slug"]))
        model = (Weapon if body.get("slug") == "longsword" else Armor) if category == "items" else models[category]
        documents[category].append(model.model_validate(body))
    profile_record = manifest["encounter_profile"]
    profile_path = (CONTENT / profile_record["file"]).resolve()
    if not profile_path.is_relative_to(CONTENT.resolve()) or hashlib.sha256(profile_path.read_bytes()).hexdigest() != profile_record["sha256"]:
        raise ValueError("Encounter profile changed")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile["rules_version"] != "dnd2024" or profile["monster"] != "goblin-warrior":
        raise ValueError("Encounter profile edition mismatch")
    monster = documents["monsters"][0].model_dump(mode="json")
    action = next(row for row in monster["actions"] if row["slug"] == profile["action"])
    activity = next(row for row in action["activities"] if row["id"] == profile["activity_id"])
    if len(activity["damage"]["parts"]) != 1 or activity["damage"]["parts"][0]["bonus"]:
        raise ValueError("Unreviewed encounter damage shape")
    activity["attack"]["ability"] = profile["governing_ability"]
    activity["damage"]["parts"][0]["bonus"] = str(profile["damage_ability_bonus"])
    action["activities"] = [activity]
    monster["actions"] = [action]
    documents["monsters"] = [Monster.model_validate(monster)]
    fingerprint = digest({"runtime": runtime_hashes, "content": manifest,
                          "dice_versions": {name: version(name) for name in ("d20", "lark-parser", "cachetools")}})
    # Omitted selector and every historical fighter digest retain the exact
    # Preview7 corpus/fingerprint. An extension never mutates that manifest.
    if selector not in (None, 'fighter-2024-v1', fingerprint):
        from .nat20_prototype import RELEASE as PROTOTYPE, MANIFEST, bundled_release
        if selector == PROTOTYPE or (MANIFEST.is_file() and selector == digest({'runtime_parent':fingerprint,'bundled_data':json.loads(MANIFEST.read_text(encoding='utf-8'))})):
            return bundled_release(fingerprint, selector)
        from .nat20_bless import RELEASE, ROOT as EXTENSION_ROOT, extension, require_stand
        if selector != RELEASE:
            path = EXTENSION_ROOT / 'manifest.json'
            known = digest({'parent_release_digest': fingerprint,
                            'extension': json.loads(path.read_text(encoding='utf-8'))}) if path.is_file() else None
            if selector != known:
                raise ValueError('Pinned engine/content release changed; unknown release, explicit migration required')
        require_stand()
        documents, extended_fingerprint = extension(documents, fingerprint)
        if selector not in (RELEASE, extended_fingerprint):
            raise ValueError('Pinned engine/content release changed; unknown release, explicit migration required')
        fingerprint = extended_fingerprint
    loader = MemoryAssetLoader(**documents)
    return loader, fingerprint, profile
