
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
for kw in ['урона силовым','урона звуком','урона излучением','урона электричеством','получило урон огнём','урон Огнём','Чистая сила','Бессильный','Больше не бессилен']:
    print('##',kw,'GM',len(re.findall(kw,G,re.I)),'PHB',len(re.findall(kw,P,re.I)))
for kw in ['Окровавлен','урон силовым','урон огнём','урон Огнём']:
    for m in list(re.finditer(kw,G,re.I))[:2]:
        print(kw,'|',G[max(0,m.start()-90):m.end()+90].replace('\n',' '))
# languages in GM / species
for kw in ['Общий жестовый','Язык полуросликов','Великаний','Сильван','Глубинная речь','Воровской жаргон','Друидический','Небесный язык','Язык Бездны','Инфернальный','Подземный язык','Первичный']:
    print('LANG',kw,'GM',len(re.findall(kw,G)),'PHB',len(re.findall(kw,P)))

