"""Fetch pinned public packages using Windows TLS; no pip network/proxy mutation.

This setup helper is separate from the offline game. HTTPS PyPI metadata and
SHA256 checks precede installation. The sole source build is locked lark-parser.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version


def main():
    root = Path(__file__).resolve().parents[1]
    cache = root / "integration/nat20/.build-cache"
    cache.mkdir(parents=True, exist_ok=True)
    manifest_path = cache.parent / "dependencies.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"packages": {}}
    tags = set(sys_tags())
    pinned = {"d20": "1.1.2", "lark-parser": "0.9.0", "hatchling": "1.27.0",
              "setuptools": "80.9.0", "wheel": "0.45.1", "dfindexeddb": "20260210",
              "python-snappy": "0.7.3", "zstd": "1.5.7.2", "pyyaml": "6.0.3"}
    pending = [Requirement(f"{name}=={version}") for name, version in pinned.items()]
    selected, constraints, installed = {}, {}, {}

    def curl(url, path):
        if not url.startswith(("https://pypi.org/", "https://files.pythonhosted.org/")):
            raise ValueError("dependency URL outside public PyPI")
        subprocess.run(["curl.exe", "-fsSL", "--retry", "2", "--retry-all-errors", "--connect-timeout", "8", "--max-time", "45",
                        url, "-o", str(path)], check=True)

    def metadata(name, version=None):
        path = cache / f"{name}-{version or 'index'}.metadata.json"
        if not path.exists():
            curl(f"https://pypi.org/pypi/{name}/{version + '/' if version else ''}json", path)
        return json.loads(path.read_text(encoding="utf-8"))

    while pending:
        requirement = pending.pop(0)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        constraints.setdefault(name, []).append(requirement.specifier)
        try:
            current = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            current = None
        if current and all(spec.contains(current) for spec in constraints[name]):
            installed[name] = current
            continue
        if name in selected:
            if not all(spec.contains(selected[name]["version"]) for spec in constraints[name]):
                raise ValueError("dependency conflict: " + name)
            continue
        if name in pinned:
            data = metadata(name, pinned[name])
        else:
            data = metadata(name)
            versions = sorted((Version(v) for v in data["releases"] if not Version(v).is_prerelease), reverse=True)
            version = next(str(v) for v in versions if all(spec.contains(v) for spec in constraints[name]))
            if version != data["info"]["version"]:
                data = metadata(name, version)
        candidates = [file for file in data["urls"] if file["filename"].endswith(".whl")
                      and parse_wheel_filename(file["filename"])[3] & tags]
        if not candidates and name == "lark-parser" and data["info"]["version"] == "0.9.0":
            candidates = [file for file in data["urls"] if file["packagetype"] == "sdist"]
        if not candidates:
            raise ValueError("no compatible prebuilt package: " + name)
        file = sorted(candidates, key=lambda item: item["filename"])[0]
        target = cache / file["filename"]
        if not target.exists():
            curl(file["url"], target)
        expected = file["digests"]["sha256"]
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("dependency checksum mismatch: " + name)
        selected[name] = {"version": data["info"]["version"], "file": target.name,
                          "sha256": expected, "source": file["url"]}
        print("fetched", name, data["info"]["version"], flush=True)
        # dfindexeddb pins a legacy snappy wrapper without a Windows wheel.
        # Use the explicitly recorded 0.7.3 compatible decompress API instead.
        if name != "dfindexeddb":
            pending.extend(Requirement(item) for item in data["info"].get("requires_dist") or [])
    manifest = {"schema_version": "1.0.0", "packages": {**previous["packages"], **selected}, "existing_packages": installed,
                "reader_dependency_override": "dfindexeddb snappy 0.6.1 -> 0.7.3, zstd 1.5.5.1 -> 1.5.7.2; compatible decompress APIs; no engine dependency override"}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    wheels = [str(cache / item["file"]) for item in selected.values() if item["file"].endswith(".whl")]
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", *wheels], check=True)
    if "lark-parser" in selected:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-build-isolation", "--no-deps",
                        str(cache / selected["lark-parser"]["file"])], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-build-isolation", "--no-deps",
                    str(root / "vendor/nat20/packages/dnd5e-srd-data"),
                    str(root / "vendor/nat20/packages/dnd5e-engine")], check=True)


if __name__ == "__main__":
    main()
