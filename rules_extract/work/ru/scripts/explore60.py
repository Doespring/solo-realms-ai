
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
def show(T,name,kws,rad=90,n=6):
    print('=========',name)
    for kw in kws:
        ms=list(re.finditer(kw,T))[:n]
        print('### ',kw,len(list(re.finditer(kw,T))))
        for m in ms[:3]:
            print('   ',T[max(0,m.start()-rad):m.end()+rad].replace('\n',' '))
show(G,'GM',[r'Мировоззрение',r'законодобр',r'нейтральн',r'хаотичн',r'законн',r'\bОпасност',r'Репутац',r'состояни[ея] Окровавл',r'Окровавл',r'Искусство',r'искусност',r'Тест к20',r'Проверка характеристики',r'Класс сложности',r'преимущество',r'Помеха|помеха'])

