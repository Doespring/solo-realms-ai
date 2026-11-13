"""Read-only source/protected-data evidence for the Windows preview acceptance."""
import hashlib,json,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];OUT=ROOT/'reports/preview7'
def sha(p):
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
if sys.argv[1]=='snapshot':
    files=[]
    for name in ('solo','web','packaging','scripts','prompts','examples/lantern-post','integration/nat20/imported','integration/nat20/playable'):
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    files += [ROOT/'desktop_main.py',ROOT/'vendor/nat20/UPSTREAM.json']
    manifest={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(set(files))}
    encoded=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    (OUT/'build-source.json').write_text(json.dumps(dict(source_sha256=hashlib.sha256(encoded).hexdigest(),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),files=manifest),indent=2),encoding='utf-8')
    print('Build source SHA256:',hashlib.sha256(encoded).hexdigest())
elif sys.argv[1]=='preservation':
    baseline=json.loads((OUT/'protected-before.json').read_text(encoding='utf-8'))
    changed=[p for p,expected in baseline.items() if not pathlib.Path(p).is_file() or sha(pathlib.Path(p))!=expected]
    result=dict(checked_files=len(baseline),unchanged=not changed,changed=changed)
    (OUT/'preservation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
else:raise SystemExit('snapshot or preservation required')
