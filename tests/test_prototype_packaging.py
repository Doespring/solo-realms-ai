"""A verified SRD kobold document is data, not an obsolete KoboldCpp runtime."""
from pathlib import Path
import pytest
from scripts.package_windows import ROOT,pinned_canonical_document,artifact_path

def test_canonical_runtime_name_exception_requires_pinned_bytes_and_path(tmp_path):
    relative=Path('_internal/vendor/nat20/packages/dnd5e-srd-data/src/dnd5e_srd_data/canonical/monsters/kobold-warrior.json')
    original=ROOT/Path(*relative.parts[1:])
    copied=tmp_path/'kobold-warrior.json';copied.write_bytes(original.read_bytes())
    assert pinned_canonical_document(copied,relative,True)
    assert not pinned_canonical_document(copied,relative,False)
    assert not pinned_canonical_document(copied,Path('_internal/kobold-warrior.json'),True)
    copied.write_bytes(copied.read_bytes()+b' ')
    assert not pinned_canonical_document(copied,relative,True)

def test_hotfix_artifact_paths_cannot_replace_profiles_or_escape_output():
    assert artifact_path('dist/pdf-hotfix/SoloRealmsAI-Prototype','unused','dist')==ROOT/'dist/pdf-hotfix/SoloRealmsAI-Prototype'
    assert artifact_path('reports/pdf-hotfix/release-manifest.json','unused','reports')==ROOT/'reports/pdf-hotfix/release-manifest.json'
    for path in ('data','dist/../data','dist'):
        with pytest.raises(ValueError):artifact_path(path,'unused','dist')
