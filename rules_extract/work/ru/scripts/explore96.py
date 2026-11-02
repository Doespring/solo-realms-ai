
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
def pgn(pos): return P[:pos].count('===== [стр.')
for kw in ['Сокрытие','Укрытие\nКогда','Сложная','Слабо заслонённая','Тяжёлый доспех','Медная монета','Оружие\nОружие','Оружие\nТаблица']:
    for m in list(re.finditer(kw,P))[:3]:
        print(kw,'p',pgn(m.start()),'|',P[max(0,m.start()-90):m.end()+150].replace('\n',' '))
print('### pgen check for known: Окровавленный')
for m in list(re.finditer('Окровавленный\nСущество',P))[:2]: print(pgn(m.start()))
for m in list(re.finditer('Оружие\n',P))[:8]: print('ORU', pgn(m.start()), repr(P[m.start():m.start()+80]))

