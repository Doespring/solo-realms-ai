
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt'); S = load('SRD521.full.txt')
cond_en = ['Blinded','Charmed','Deafened','Exhaustion','Frightened','Grappled','Incapacitated','Invisible','Paralyzed','Petrified','Poisoned','Prone','Restrained','Stunned','Unconscious']
print('SRD conds:', {c: len(re.findall(r'\b'+c+r'\b',S)) for c in cond_en})
ru_gm = ['ослеплённый','очарованный','оглохший','истощение','испуганный','схваченный','недееспособный','невидимый','парализованный','окаменевший','отравленный','лежащий ничком','опутанный','оглушённый','безссознательный','бессознательный','сбитый с ног','окровавленный','Сбитый с ног']
print('GM conds:', {c: len(re.findall(c,G,re.I)) for c in ru_gm})
print('PHB conds:', {c: len(re.findall(c,P,re.I)) for c in ru_gm})
dm = ['кислот','дробящий','холод','огон','силовое поле','электрич','некротич','колющий','яд','психическ','излучени','рубящий','звук']
print('GM dmg:', {c: len(re.findall(c,G,re.I)) for c in dm})
print('PHB dmg:', {c: len(re.findall(c,P,re.I)) for c in dm})
i=G.find('состояние лежащий ничком'); print(G[i-300:i+200])

