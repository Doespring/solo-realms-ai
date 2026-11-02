
import sys, re
sys.stdout.reconfigure(encoding='utf-8')
base = r'C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\rules_extract\work\ru\text'
P = open(base+r'\PHB2024ru.full.txt', encoding='utf-8').read().replace('\r\n','\n')
P = re.sub(r'-\n','',P); P = re.sub(r'\n+','\n',P)
def pgn(pos): return P[:pos].count('===== [стр.')
for kw in ['Укрытие\n','Застигнутый врасплох','Помощь [Действие]','Засада [Действие]','Поиск [Действие]','Изучение [Действие]','Влияние [Действие]','Использование [Действие]','Рывок [Действие]','Отход [Действие]','Уклонение [Действие]','Подготовка [Действие]','Магия [Действие]','Атака [Действие]','Тест к20\n','Преимущество\n','Помеха\n','Спасбросок\n','Проверка характеристики\n','Инициатива\n','Бонус владения\n','Владение\n','Окровавленный\n','Порог урона\n','Сфера [Область','Эманация [Область','Цилиндр [Область','Линия [Область','Конус [Область']:
    ms=[pgn(m.start()) for m in re.finditer(kw,P)][:4]
    print(kw.replace('\n','~'), ms)

