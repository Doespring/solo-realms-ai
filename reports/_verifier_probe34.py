import json, urllib.request, pathlib
def h(tag):
    with urllib.request.urlopen('http://127.0.0.1:19219/api/health', timeout=20) as r:
        d = json.load(r)
    return tag, {k: d.get(k) for k in ('build','disk','drift','commit','contract')}
target = pathlib.Path(r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')/'solo'/'chronicles.py'
orig = target.read_bytes()
try:
    print(h('before edit'))
    target.write_bytes(orig + b'\n# drift probe by verifier\n')
    print(h('after edit of solo/chronicles.py'))
finally:
    target.write_bytes(orig)
print(h('after restore'))
web = pathlib.Path(r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')/'web'/'play.js'
worig = web.read_bytes()
try:
    web.write_bytes(worig + b'\n// drift probe web\n')
    print(h('after edit of web/play.js (UI, not backend)'))
finally:
    web.write_bytes(worig)
print(h('after restore web'))
