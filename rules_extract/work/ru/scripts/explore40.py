
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
S = load('SRD521.full.txt')
print([re.sub(r'\s+',' ',m.group(0).replace('\n',' ')) for m in re.finditer(r'Subclass[: ]*[^\n]{0,60}', S)][:40])

