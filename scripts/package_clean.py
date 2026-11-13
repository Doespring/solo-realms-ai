"""Current source + runnable Windows tree; no profiles or duplicate release ZIPs."""
import hashlib,json,zipfile,sqlite3
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parents[1]
VERSION='2.1.4-preview.6'
DIRECTORIES={'solo','web','assets','visual_catalog','prompts','docs','tests','examples','packaging','scripts','build-tools'}
ROOT_FILES={'.gitignore','.gitattributes','README.md','ARCHITECTURE.md','FIRST-PLAYABLE-PREVIEW6.md','CLEANUP-REPORT.md','THIRD_PARTY.md','desktop_main.py','Desktop.cmd','Start.cmd','Stop.cmd','requirements.txt','requirements-desktop.txt','pytest.ini'}
PUBLIC_DATABASES={'visual_catalog/assets.sqlite3','visual_catalog/source/SoloRealms_AssetCatalog.sqlite3'}

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    destination=ROOT.parent/('SoloRealmsAI-clean-'+VERSION+'.zip')
    if destination.exists():raise ValueError('Clean archive already exists; no overwrite')
    files=[]
    for path in ROOT.rglob('*'):
        relative=path.relative_to(ROOT);parts=relative.parts
        if parts[0] in {'.git','.venv','data','runtime','models','build'} or any(p in {'__pycache__','.pytest_cache'} for p in parts):continue
        if not path.is_file():continue
        selected=(len(parts)==1 and path.name in ROOT_FILES or parts[0] in DIRECTORIES or parts[:2]==('reports','cleanup') or parts[:2]==('dist','SoloRealmsAI') or relative.as_posix()=='dist/SoloRealmsAI-Setup-'+VERSION+'.exe')
        if not selected:continue
        if path.is_symlink():raise ValueError('Linked archive source: '+str(relative))
        # Reject profiles or private data even if accidentally placed in source.
        allowed_db=relative.as_posix() in PUBLIC_DATABASES or relative.as_posix() in {'dist/SoloRealmsAI/_internal/'+n for n in PUBLIC_DATABASES}
        runtime_zip=relative.as_posix()=='dist/SoloRealmsAI/_internal/base_library.zip'
        if path.name in {'cloud-profiles.json','cloud-usage.sqlite3','cleanup-state.tmp.json'} or path.suffix.lower() in {'.dpapi','.gguf','.ggml','.safetensors','.pyc','.pdf','.part'} or path.suffix.lower()=='.zip' and not runtime_zip or path.suffix.lower() in {'.sqlite3','.db'} and not allowed_db:
            raise ValueError('Private, duplicate or obsolete archive file: '+str(relative))
        if allowed_db:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
                assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                assert not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('campaigns','drafts','calls','cloud_keys')").fetchone()
        files.append(path)
    manifest=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(files)]
    names={f['path'] for f in manifest}
    assert {'README.md','Desktop.cmd','solo/first_playable.py','dist/SoloRealmsAI/SoloRealmsAI.exe','dist/SoloRealmsAI-Setup-'+VERSION+'.exe'}<=names
    temporary=destination.with_suffix('.zip.part')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for p in sorted(files):archive.write(p,'SoloRealmsAI/'+p.relative_to(ROOT).as_posix())
        archive.writestr('SoloRealmsAI/ARCHIVE-MANIFEST.json',json.dumps(dict(version=VERSION,files=manifest,no_campaigns_or_keys=True,virtual_environment_excluded=True),ensure_ascii=False,indent=2))
    with zipfile.ZipFile(temporary) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(set(archive.namelist()))
        for item in manifest:
            assert hashlib.sha256(archive.read('SoloRealmsAI/'+item['path'])).hexdigest()==item['sha256']
    temporary.replace(destination)
    result=dict(version=VERSION,archive=str(destination),bytes=destination.stat().st_size,sha256=sha(destination),files=len(files)+1,crc='ok',all_file_hashes_verified=True,no_campaigns_or_keys=True,contains_current_source_and_windows_exe=True)
    destination.with_suffix('.manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
