# Замечание интегратору: переводы строк и SHA

При исходной настройке обе копии rules/advancement.py имели CRLF и SHA c61e8b8c4c04077b0eb634e18b54818a1ac34f9795a4820fb4eeb2d878a6ae1e. PATCHES.json ожидает 97e112d9a6499b7738365dea2979d6b4e9a0c3646dbaec57394d1220d0d1584a — тот же текст с LF. Это вызывает отказ release() до выполнения механик.

В engine checkout восстановлены точные LF-байты и добавлено vendor/nat20/** -text в .gitattributes. Все SHA UPSTREAM/PATCHES совпадают. Основная папка не исправлялась: общий поток должен отдельно проверить актуальный файл и применить защиту от преобразования Git. Не менять pin для оправдания случайного изменения строк. Не переносить весь setup commit: выбрать нужное изменение атрибутов и при необходимости восстановить известные байты.
## Проверка за минуту

```python
import hashlib
p = 'vendor/nat20/packages/dnd5e-engine/src/dnd5e_engine/rules/advancement.py'
b = open(p, 'rb').read()
assert b'\r\n' not in b, 'файл испорчен CRLF'
assert hashlib.sha256(b).hexdigest() == '97e112d9a6499b7738365dea2979d6b4e9a0c3646dbaec57394d1220d0d1584a', 'SHA не совпадает'
print('ok')
```

Если проверка падает — восстановить байты из этой поставки, а не править `PATCHES.json`.

## Что уже сделано в этой папке

В `work/nat20-engine` проблема устранена: CRLF отсутствует, SHA совпадает с `PATCHES.json`
(`kind: added`), `release()` проходит целиком (14.127, 14.128). Защита `.gitattributes`
(`vendor/nat20/** -text`) действует. При переносе её нужно нести вместе с файлами.
