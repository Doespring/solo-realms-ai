"""Release ZIP/manifest from the curated PyInstaller tree, never the project or user data."""
import argparse,hashlib,json,zipfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from solo.version import VERSION
def checksum(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def pinned_canonical_document(path,relative,prototype):
    prefix=('_internal','vendor','nat20','packages','dnd5e-srd-data','src','dnd5e_srd_data','canonical')
    if not prototype or relative.parts[:len(prefix)]!=prefix or path.suffix.lower()!='.json':
        return False
    lock=json.loads((ROOT/'vendor/nat20/UPSTREAM.json').read_text(encoding='utf-8'))
    expected=lock['files'].get(Path(*relative.parts[3:]).as_posix())
    return expected is not None and checksum(path)==expected

def artifact_path(value,default,base):
    path=(ROOT/(value or default)).resolve()
    if not path.is_relative_to((ROOT/base).resolve()) or path==(ROOT/base).resolve():
        raise ValueError('Artifact path must stay inside '+base)
    return path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prototype',action='store_true')
    parser.add_argument('--portable-only',action='store_true')
    parser.add_argument('--bundle')
    parser.add_argument('--report')
    args=parser.parse_args();prototype=args.prototype
    directory=artifact_path(args.bundle,'dist/SoloRealmsAI-Prototype' if prototype else 'dist/SoloRealmsAI','dist')
    target=artifact_path(args.report,'reports/prototype-5/release-manifest.json' if prototype else 'reports/preview10/release-manifest.json','reports')
    files=sorted(p for p in directory.rglob('*') if p.is_file())
    if not (directory/'SoloRealmsAI.exe').is_file():raise RuntimeError('Build the executable first')
    forbidden={'.gguf','.ggml','.dpapi'}
    catalogs={'_internal/visual_catalog/assets.sqlite3','_internal/visual_catalog/source/SoloRealms_AssetCatalog.sqlite3'}
    for p in files:
        relative=p.relative_to(directory)
        if relative.parts[:2]==('_internal','web'):
            if checksum(p)!=checksum(ROOT/Path(*relative.parts[1:])):raise ValueError('UI source changed during build; rebuild before packaging')
        old_runtime='kobold' in p.name.lower() and not pinned_canonical_document(p,relative,prototype)
        if p.is_symlink() or p.suffix.lower() in forbidden or (p.suffix.lower()=='.sqlite3' and relative.as_posix() not in catalogs) or ('data' in relative.parts and relative.parts[:6]!=('_internal','assets','licenses','pypdfium2','licenses','data')) or p.name=='cloud-profiles.json' or old_runtime:raise ValueError('Unexpected user data/AI runtime in release: '+str(relative))
        if relative.as_posix() in catalogs:
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute("SELECT 1 FROM sqlite_master WHERE name IN ('campaigns','calls','drafts','cloud_keys')").fetchone():raise ValueError('Invalid public asset catalog')
            original=ROOT/Path(*relative.parts[1:])
            if checksum(original)!=checksum(p):raise ValueError('Catalog bytes differ from preserved source')
    archive=ROOT/('dist/SoloRealmsAI-Windows-'+VERSION+'.zip')
    if archive.exists():raise FileExistsError('Versioned release ZIP already exists; preserve it')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for p in files:zipped.write(p,(Path(directory.name)/p.relative_to(directory)).as_posix())
    with zipfile.ZipFile(archive) as zipped:
        bad=zipped.testzip()
        if bad:raise RuntimeError('Archive CRC failed: '+bad)
    setup=ROOT/('dist/SoloRealmsAI-Setup-'+VERSION+'.exe')
    result=dict(created=datetime.now(timezone.utc).isoformat(),version=VERSION,experimental=prototype,mock_only=(directory/'_internal/preview-mock.json').exists(),bundle=directory.relative_to(ROOT).as_posix(),files=len(files),unpacked_bytes=sum(p.stat().st_size for p in files),archive=dict(name=archive.name,bytes=archive.stat().st_size,sha256=checksum(archive),crc='ok'),installer=None if args.portable_only else dict(name=setup.name,bytes=setup.stat().st_size,sha256=checksum(setup)),no_user_databases_keys_or_models=True,public_asset_catalogs=sorted(catalogs),files_manifest=[dict(path=p.relative_to(directory).as_posix(),bytes=p.stat().st_size,sha256=checksum(p)) for p in files])
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='files_manifest'}))
if __name__=='__main__':main()
