
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
def load(fn):
    t = open(base+'\\'+fn, encoding='utf-8').read().replace('\r\n','\n')
    t = re.sub(r'-\n','',t); return re.sub(r'\n+','\n',t)
G = load('GM2024ru.full.txt'); P = load('PHB2024ru.full.txt')
i=P.find('Чистая сила'); print(P[i-700:i+300])
print('=========== GM species?')
print('Аасимар',len(re.findall('Аасимар',G)),'Тифлинг',len(re.findall('Тифлинг',G)),'Дварф',len(re.findall('Дварф',G)),'Дварф',len(re.findall('Гном',G)))
i=G.find('Аасимар'); print(G[i-400:i+400])

