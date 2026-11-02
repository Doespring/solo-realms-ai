
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
# class sections start with "Класс: " patterns; find "Барвард" no. Find "Глава 3: Классы"
for kw in ['Глава 3: Классы','Подкласс','Предыстори','Черты','Вид']:
    ms=[m.start() for m in re.finditer(kw, txt)][:4]
    print(kw, len(re.findall(kw,txt)), ms)
i = txt.find('Описания классов')
print(txt[i:i+500] if i>0 else '')

