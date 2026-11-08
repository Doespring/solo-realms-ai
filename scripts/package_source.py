"""Archive the committed source, never the working tree or private profiles."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from solo.version import VERSION


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT)
    if git('status', '--porcelain', '--untracked-files=no').strip():
        raise RuntimeError('Commit the final tracked source before packaging')
    commit = git('rev-parse', 'HEAD').decode().strip()
    names = git('ls-tree', '-r', '--name-only', 'HEAD').decode('utf8').splitlines()
    public_databases = {'visual_catalog/assets.sqlite3', 'visual_catalog/source/SoloRealms_AssetCatalog.sqlite3'}
    for name in names:
        path = Path(name)
        if (path.parts[0] in {'data', 'models', 'runtime', '.venv', 'dist', 'build', 'build-tools'}
                or path.suffix.lower() in {'.gguf', '.ggml', '.dpapi', '.safetensors'}
                or (path.suffix.lower() in {'.sqlite3', '.db'} and name not in public_databases)
                or path.name == 'cloud-profiles.json'):
            raise ValueError('Private data or model in committed source: ' + name)
    archive = ROOT / 'dist' / ('SoloRealmsAI-Source-' + VERSION + '.zip')
    subprocess.run(['git', 'archive', '--format=zip', '--prefix=SoloRealmsAI/', '--output=' + str(archive), 'HEAD'], cwd=ROOT, check=True)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip():
            raise RuntimeError('Source archive CRC failed')
        file_count = sum(not item.is_dir() for item in zipped.infolist())
        for name in public_databases:
            if hashlib.sha256(zipped.read('SoloRealmsAI/' + name)).hexdigest() != checksum(ROOT / name):
                raise RuntimeError('Original public catalog changed in archive')
    result = dict(version=VERSION, git_commit=commit, name=archive.name, bytes=archive.stat().st_size,
                  sha256=checksum(archive), crc='ok', files=file_count,
                  user_profiles_keys_models_and_python_environment_excluded=True)
    (ROOT / 'dist' / ('Source-manifest-' + VERSION + '.json')).write_text(json.dumps(result, indent=2), encoding='utf8')
    products = [ROOT / 'dist' / ('SoloRealmsAI-Windows-' + VERSION + '.zip'),
                ROOT / 'dist' / ('SoloRealmsAI-Setup-' + VERSION + '.exe'), archive]
    (ROOT / 'dist' / ('SHA256SUMS-' + VERSION + '.txt')).write_text(
        ''.join(checksum(path) + '  ' + path.name + '\n' for path in products), encoding='ascii')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
