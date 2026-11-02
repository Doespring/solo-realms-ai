
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
def pgn(pos): return P[:pos].count('===== [стр.')
i=P.find('Принципиально-нейтральное (ПН)')
print('ALIGN PAGE', pgn(i))
print(P[i:i+1800])
print('### SAIL/EXPLORE')
for kw in ['Темп','Исследование\n','Скорость передвижения','путешестви']:
    print(kw, P.count(kw))
i=P.find('Ячейки заклинаний')
print('SLOT PAGE', pgn(i))

