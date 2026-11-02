
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    txt = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt); return txt
P = load('PHB2024ru.full.txt')
names = re.findall(r'Подкласс\s*\n?«([^»]+)»', P)
clean_names = [re.sub(r'\s+',' ',n) for n in names if len(n)<60]
print(len(clean_names))
for n in clean_names: print(n)

