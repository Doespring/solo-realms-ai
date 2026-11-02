
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
txt = open(base+'\\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
txt = re.sub(r'-\n','',txt); txt = re.sub(r'\n+','\n',txt)
# find class names as standalone headings with "Основные черты"
for m in re.finditer(r'\n([А-ЯЁ][а-яё]+)\n\nОсновные черты', txt):
    print(m.group(1), m.start())
print('=== subclasses')
for m in re.finditer(r'\nПодкласс: ([^\n]{3,60})', txt):
    print(m.group(1), m.start())

