
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
S = load('SRD521.full.txt'); P = load('PHB2024ru.full.txt')
for kw in ['Aasimar','Charlatan','Farmer','Hermit','Merchant','Sailor','Wayfarer','Simple weapons','Martial weapons','Spell Slot','Travel Pace','Savant','Ascendant','Hedonist','Sybarite','Dilettante','Haunted','Raised','Guild','Outlander','Urchin','Noble','Scribe','Artisan','Guard']:
    print(kw, S.count(kw))
print('----PHB')
for kw in ['Хаотично-','Хаотичн','Нейтрально-зл','Нейтрально-добр','Ячейки заклинаний','Ячейка заклинаний','Темп путешеств','скорость путешеств']:
    print(kw, P.count(kw))
i=P.find('Хаотично'); print(P[i-400:i+400])

