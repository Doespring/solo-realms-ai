
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
C = load('CONV.full.txt'); G = load('GM2024ru.full.txt')
for m in re.finditer(r'[^\n.]{0,80}\[New Name\][^\n]{0,40}', C):
    print('NN:', re.sub(r'\s+',' ',m.group(0)))
print('---- GM terms')
for kw in ['Окровавлен','Bloodied','искусн','Школа','Уровень','Предел свежести','Стойкость','Смерть','Нокаут','Застигнут']:
    print(kw, len(re.findall(kw,G)))

