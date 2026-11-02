
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); G = load('GM2024ru.full.txt')
i=S.find('Cleave.')
print(S[i-600:i+900])
print('##### GM danger types')
for kw in ['Неприятная опасность','Смертельная опасность','уровни 1–4','Запутанность','Заражённая','Падение','Удушение','Огонь','Тонкий лёд','Зыбучий']:
    ms=list(re.finditer(kw,G))
    print(kw,len(ms))
i=G.find('Опасности\nКнига игрока')
print(G[i:i+2600])

