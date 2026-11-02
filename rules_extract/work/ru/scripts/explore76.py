
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
for kw in ['Надевание и снятие','Надевание','Владение доспехами','обученность','Обученность','Скрытность','Помеха» ','н/д','без мировоззрения']:
    print(kw, len(re.findall(re.escape(kw),P)))
print('=== armor table')
i=P.find('Тяжёлый доспех (10 минут')
print(P[i:i+900])

