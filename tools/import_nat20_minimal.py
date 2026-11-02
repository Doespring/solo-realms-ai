"""Import an explicit CC-BY SRD 2024 vertical using Nat20's own translator.

LevelDB is read as files, never opened for writing. JSON is safe YAML. The
upstream translator handles mechanics; no Foundry or Laaru JavaScript runs.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
import random
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/nat20/packages/dnd5e-srd-data"
# Trusted, fixed vendored code; not a path received from content or AI.
sys.path.insert(0, str(UPSTREAM))
from tools.translators import foundry as translator
from dfindexeddb.leveldb.record import FolderReader

SELECTION = {
    "classes24": {
        "phbftrFighter000": ("classes", "fighter", "class"),
        "phbftrFightingSt": ("features", "fighting-style", "feature"),
        "phbftrSecondWind": ("features", "second-wind", "feature"),
        "phbftrWeaponMast": ("features", "weapon-mastery", "feature"),
    },
    "origins24": {
        "phbspHuman000000": ("species", "human", "species"),
        "phbsptResourcefu": ("features", "resourceful", "feature"),
        "phbsptSkillful00": ("features", "skillful", "feature"),
        "phbsptVersatile0": ("features", "versatile", "feature"),
    },
    "equipment24": {
        "phbwepLongsword0": ("items", "longsword", "weapon"),
        "phbarmChainMail0": ("items", "chain-mail", "armor"),
        "phbarmShield0000": ("items", "shield", "armor"),
    },
    "actors24": {"mmGoblinWarrior0": ("monsters", "goblin-warrior", "monster")},
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def read_pack(folder):
    active = {}
    # Resolve current active MANIFEST, sequence numbers and tombstones.
    for wrapper in FolderReader(folder).GetRecords(use_manifest=True):
        if wrapper.recovered:
            continue
        record = wrapper.record
        old = active.get(record.key)
        if old is None or record.sequence_number > old.sequence_number:
            active[record.key] = record
    def hydrate(document, collection, identity):
        for field in ("items", "effects", "pages", "results"):
            children = document.get(field)
            if isinstance(children, list) and children and all(isinstance(child, str) for child in children):
                values = []
                for child in children:
                    child_id = identity + "." + child
                    child_collection = collection + "." + field
                    entry = active.get(("!" + child_collection + "!" + child_id).encode())
                    if entry is None or int(entry.record_type) != 1:
                        raise ValueError("missing embedded document: " + child_id)
                    values.append(hydrate(json.loads(entry.value), child_collection, child_id))
                document[field] = values
        return document
    documents = {}
    for key, record in active.items():
        if int(record.record_type) != 1 or not key.startswith((b"!items!", b"!actors!")):
            continue
        document = json.loads(record.value)
        if not isinstance(document, dict) or document.get("_id") != key.decode().split("!")[-1]:
            raise ValueError("unexpected Foundry document identity")
        documents[document["_id"]] = hydrate(document, key.decode().split("!")[1], document["_id"])
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundry", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "integration/nat20/imported")
    args = parser.parse_args()
    system = json.loads((args.foundry / "system.json").read_text(encoding="utf-8"))
    if (system["id"], system["version"]) != ("dnd5e", "5.3.3"):
        raise ValueError("unreviewed Foundry release")
    source_root = args.output / "raw/_source"
    selected = []
    for pack, selection in SELECTION.items():
        documents = read_pack(args.foundry / "packs" / pack)
        for id, (category, slug, kind) in selection.items():
            document = documents[id]
            source = document.get("system", {}).get("source", {})
            if source.get("rules") != "2024" or source.get("license") != "CC-BY-4.0":
                raise ValueError("edition/license gate failed: " + id)
            relative = Path(pack)
            if pack == "classes24":
                relative /= "fighter"
                if kind == "feature":
                    relative /= "class-features"
            elif pack == "origins24":
                relative /= "species"
                if kind == "feature":
                    relative /= "traits/human"
            path = source_root / relative / (slug + ".yml")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encode(document), encoding="utf-8")
            selected.append((path, document, pack, category, slug, kind))
    index = translator.build_feature_index(source_root)
    functions = {"class": translator.translate_class_yaml, "species": translator.translate_species_yaml,
                 "weapon": translator.translate_weapon_yaml, "armor": translator.translate_armor_yaml,
                 "monster": translator.translate_monster_yaml, "feature": translator.translate_feature_yaml}
    records = []
    for path, document, pack, category, slug, kind in selected:
        kwargs = {"ingest_date": date(2026, 10, 17), "ingest_version": "solo-foundry533-nat20-070-v1"}
        if kind in ("class", "species", "feature"):
            # Some translators take the feature index under an optional keyword.
            import inspect
            if "feature_index" in inspect.signature(functions[kind]).parameters:
                kwargs["feature_index"] = index
        entity = functions[kind](path, **kwargs)
        if entity.slug != slug:
            raise ValueError("unexpected translator identity: " + entity.slug)
        result = entity.model_dump(mode="json")
        deferred = []
        if kind in ("class", "species"):
            # Same UUID -> GrantRef mapping used by Nat20 tools/regen.py.
            # Only level-one fixed grants belong to this minimal import;
            # choices and higher-level refs are recorded, never guessed.
            grants = []
            for advancement in result.get("advancement", []):
                level = advancement.get("level", 0)
                if advancement["type"] == "ItemGrant":
                    for item in advancement["configuration"].get("items", []):
                        reference = index.get(item["uuid"])
                        if level <= 1 and reference is None:
                            raise ValueError("missing level-one grant: " + item["uuid"])
                        if level <= 1:
                            grants.append({**reference.model_dump(mode="json"), "level": level})
                        else:
                            deferred.append({"uuid": item["uuid"], "level": level})
                elif advancement["type"] == "ItemChoice":
                    deferred.append({"choice": advancement["id"], "level": level,
                                     "status": "player_choice_not_imported"})
            result["granted_features"] = grants
        # Replace the translator's upstream-commit URL with the actual local
        # release/UUID. No source-content or mechanics field is synthesized.
        result["provenance"].update(source_url=f"https://github.com/foundryvtt/dnd5e/releases/tag/release-5.3.3#Compendium.dnd5e.{pack}.{document['_id']}")
        target = args.output / "canonical" / category / (slug + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encode(result), encoding="utf-8")
        records.append({"category": category, "slug": slug, "file": target.relative_to(args.output).as_posix(),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "rules_version": "dnd2024",
                        "locale": "en", "license": "CC-BY-4.0", "foundry_pack": pack,
                        "foundry_id": document["_id"], "raw_file": path.relative_to(args.output).as_posix(),
                        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "import_status": "translated_typed_data",
                        "deferred_dependencies": deferred,
                        "mechanics": "Nat20 0.7.0, no JS execution; not certification of every feature"})
    manifest = {"schema_version": "1.0.0", "rules_version": "dnd2024", "foundry_version": "5.3.3",
                "nat20_version": "0.7.0", "documents": records, "laaru_imported": False,
                "source_code_executed": False, "translator": "vendored Nat20 tools/translators/foundry.py",
                "scope": "level-one human fighter, equipment and goblin-warrior; action capability allowlist remains narrow"}
    # Nat20's host seam requires a fixed monster to-hit. Its generic monster
    # translator also loses the embedded weapon's governing ability. Reuse
    # Nat20's OWN pinned weapon projection once at import, not a second resolver.
    from dnd5e_engine.activities.attack import _attack_bonus, _weapon_default_ability
    from dnd5e_engine.activities.context import ActivityResolutionContext
    from dnd5e_engine.types.combat import Combatant
    monster_path = args.output / "canonical/monsters/goblin-warrior.json"
    monster = json.loads(monster_path.read_text(encoding="utf-8"))
    raw_actor = next(doc for _, doc, pack, _, _, _ in selected if pack == "actors24")
    raw_weapon = next(item for item in raw_actor["items"] if item.get("name") == "Scimitar")
    with tempfile.TemporaryDirectory(prefix="nat20-weapon-project-", dir=args.output) as working:
        workspace = Path(working).resolve()
        if workspace.parent != args.output.resolve():
            raise ValueError("unexpected weapon projection workspace")
        path = workspace / "scimitar.yml"
        path.write_text(encode(raw_weapon), encoding="utf-8")
        weapon = translator.translate_weapon_yaml(path, ingest_date=date(2026, 10, 17), ingest_version="projection-only")
    ability_map = {"str": "strength", "dex": "dexterity", "con": "constitution", "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
    actor = Combatant(entity_id="goblin", entity_type="Monster", name=monster["name"], initiative=0,
        hp_current=monster["hp"], **{ability_map[key]: value for key, value in monster["ability_scores"].items()})
    ctx = ActivityResolutionContext(rng=random.Random(0), caster=actor, targets=[], event_emitter=lambda event: None,
        caster_abilities=monster["ability_scores"], caster_proficiency_bonus=monster["proficiency_bonus"])
    governing = _weapon_default_ability(weapon, ctx)
    scimitar = next(action for action in monster["actions"] if action["slug"] == "scimitar")
    normal = next(activity for activity in scimitar["activities"] if activity["name"] == "")
    activity = next(activity for action in translator.translate_monster_yaml(
        next(path for path, _, pack, _, _, _ in selected if pack == "actors24"),
        ingest_date=date(2026, 10, 17), ingest_version="projection-only").actions
        if action.slug == "scimitar" for activity in action.activities if activity.name == "")
    if normal["damage"]["parts"][0]["bonus"] or len(normal["damage"]["parts"]) != 1:
        raise ValueError("Unreviewed goblin base-damage shape")
    profile = {"schema_version": "1.0.0", "rules_version": "dnd2024", "monster": "goblin-warrior",
        "action": "scimitar", "activity_id": normal["id"], "governing_ability": governing,
        "attack_bonus": _attack_bonus(activity, ctx, weapon, governing),
        "damage_ability_bonus": ctx.ability_mod(governing),
        "basis": "Foundry embedded Scimitar -> pinned Nat20 weapon/default-ability/attack-bonus projection",
        "restriction": "normal Scimitar attacks only; advantage rider, Shortbow and Nimble Escape unbound"}
    profile_path = args.output / "encounter-profile.json"
    profile_path.write_text(encode(profile), encoding="utf-8")
    manifest["encounter_profile"] = {"file": profile_path.name, "sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest()}
    (args.output / "manifest.json").write_text(encode(manifest), encoding="utf-8")
    print(encode({"imported": len(records), "categories": sorted({r["category"] for r in records})}))


if __name__ == "__main__":
    main()
