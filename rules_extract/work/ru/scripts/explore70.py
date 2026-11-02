
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
for f,name in ((G,'GM'),(P,'PHB')):
    pages = re.split(r'=====\s*\[стр\.(\d+)\]\s*=====', f)
    print('##',name)
    for k in range(1,len(pages),2):
        t=pages[k+1]
        for m in re.finditer(r'Подкласс[^\n]{0,80}', t):
            s=re.sub(r'\s+',' ',m.group(0))
            if '«' in s or ':' in s: print(' ',pages[k], s[:90])

