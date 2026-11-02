"""Read-only manifest/archive inventory. Never loads Foundry or Laaru JS."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_entries(archive, seven_zip):
    result = subprocess.run([str(seven_zip), "l", "-slt", "-sccUTF-8", str(archive)],
                            capture_output=True, encoding="utf-8", check=True)
    entries = []
    for block in result.stdout.split("----------", 1)[1].split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        name = fields["Path"].replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ":" in name:
            raise ValueError("unsafe archive entry")
        entries.append({"path": name, "bytes": int(fields.get("Size", 0)),
                        "directory": fields.get("Attributes", "").startswith("D")})
    return entries


def inventory(archive, foundry, laaru, seven_zip, imported=None):
    entries = archive_entries(archive, seven_zip)
    foundry, laaru = Path(foundry), Path(laaru)
    system = json.loads((foundry / "system.json").read_text(encoding="utf-8"))
    module = json.loads((laaru / "module.json").read_text(encoding="utf-8"))
    if system["id"] != "dnd5e" or system["version"] != "5.3.3" or module["version"] != "4.3.0":
        raise ValueError("source release changed; review inventory classification")
    imported_counts = {}
    archive_sha = sha256(archive)
    system_sha = sha256(foundry / "system.json")
    module_sha = sha256(laaru / "module.json")
    if imported:
        for document in imported["documents"]:
            pack = document["foundry_pack"]
            imported_counts[pack] = imported_counts.get(pack, 0) + 1
    packs = []
    for pack in system["packs"]:
        book = (pack.get("flags") or {}).get("dnd5e", {}).get("sourceBook")
        if book == "SRD 5.2":
            rules, basis = "dnd2024", "manifest sourceBook=SRD 5.2"
        elif pack["name"] in ("content24", "tables24"):
            rules, basis = "dnd2024", "explicit Foundry *24 rules/table pack; check document edition before import"
        elif book == "SRD 5.1":
            rules, basis = "dnd2014", "manifest sourceBook=SRD 5.1"
        else:
            rules, basis = "unknown", "manifest lacks an explicit source edition; no automatic import"
        prefix = "dnd5e/" + pack["path"].replace("\\", "/") + "/"
        files = [entry for entry in entries if entry["path"].startswith(prefix) and not entry["directory"]]
        packs.append({"id": "foundry.dnd5e." + pack["name"], "pack": pack["name"], "type": pack["type"],
                      "source": "Foundry dnd5e 5.3.3 / " + (book or pack["name"]),
                      "rules_version": rules, "version_basis": basis, "locale": "en",
                      "license": {"content": "CC-BY-4.0 (SRD material)", "code": "MIT",
                                  "evidence": "dnd5e/README.md:12-24; dnd5e/LICENSE.txt",
                                  "assets": "separate terms; no images imported"},
                      "provenance": {"archive": str(Path(archive).resolve()), "archive_sha256": archive_sha,
                                     "manifest_sha256": system_sha, "path": pack["path"]},
                      "storage": "LevelDB", "archive_files": len(files), "archive_bytes": sum(f["bytes"] for f in files),
                      "import_status": "partial_imported" if pack["name"] in imported_counts else "indexed_only",
                      "imported_documents": imported_counts.get(pack["name"], 0)})
    for pack in module["packs"]:
        folder = (laaru / pack["path"]).resolve()
        if not folder.is_relative_to(laaru.resolve()):
            raise ValueError("pack outside Laaru root")
        files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []
        packs.append({"id": "laaru." + pack["name"], "pack": pack["name"], "type": pack["type"],
                      "source": "Laaru 4.3.0 / DnD14 + HB/UA", "rules_version": "mixed_2014_homebrew",
                      "version_basis": "DnD14 title; sourceBooks PHB/DMG/MM explicitly say 2014; other sources are mixed",
                      "locale": "ru-RU", "license": {"content": "unverified", "code": "unverified",
                      "evidence": "module.json authors/sourceBooks; no grant of redistribution inferred"},
                      "provenance": {"manifest": str((laaru / "module.json").resolve()),
                                     "manifest_sha256": module_sha, "path": pack["path"]},
                      "storage": "LevelDB", "archive_files": len(files), "archive_bytes": sum(p.stat().st_size for p in files),
                      "import_status": "excluded_executable" if pack["type"] == "Macro" else "indexed_quarantined",
                      "imported_documents": 0})
    script = laaru / "scripts/spellGiver.mjs"
    return {"schema_version": "1.0.0", "checked_on": "2026-10-17", "packs": packs,
            "components": [{"source": "Laaru 4.3.0 spellGiver", "rules_version": "dnd2014",
                            "locale": "ru-RU", "license": "unverified", "provenance": {
                                "path": str(script.resolve()), "sha256": sha256(script),
                                "source_books": module["flags"]["dnd5e"]["sourceBooks"]},
                            "import_status": "excluded_executable", "executed": False}],
            "source_code_executed": False, "silent_edition_conversion": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--foundry", type=Path, required=True, help="extracted dnd5e directory")
    parser.add_argument("--laaru", type=Path, required=True)
    parser.add_argument("--seven-zip", type=Path, default=Path("C:/Program Files/7-Zip/7z.exe"))
    parser.add_argument("--import-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    imported = json.loads(args.import_manifest.read_text(encoding="utf-8")) if args.import_manifest else None
    result = inventory(args.archive, args.foundry, args.laaru, args.seven_zip, imported)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"packs": len(result["packs"]), "rules_versions": sorted({p["rules_version"] for p in result["packs"]})}))


if __name__ == "__main__":
    main()
