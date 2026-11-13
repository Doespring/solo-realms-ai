# r174 — текст готовит DeepSeek, картинки смотрит Gemini: роль зрения отдельно

## ЦЕЛЬ

Подготовка книги идёт дешёвой моделью (DeepSeek), а когда в запрос попадают
картинки (карты, раздатки, страницы), запрос уходит модели со зрением (Gemini).
Сейчас это одна роль, поэтому переключение на DeepSeek отключило зрение целиком.

## ЧТО ПРОИЗОШЛО (факт, проверено прорабом 02.11)

Владелец велел перевести подготовку на DeepSeek ради цены: `pdf_analyst` был
`google/gemini-3.8-flash` (39,27 ₽/млн вход, 196,36 ₽/млн выход), стал
`deepseek/deepseek-v4.1-flash` (4,03 / 57,60). Профиль
`runtime/web-local/cloud-profiles.json` уже изменён, приложение перезапущено,
`GET /api/ai/profiles` подтверждает: `pdf_analyst → deepseek`.
Копия прежнего профиля: `runtime/backups/cloud-profiles-before-deepseek-*.json`.

**Побочный эффект:** в `solo/cloud.py` (~строка 165) стоит

```python
if images:
    if mode!='pdf_analyst' or cfg.image_per_million is None:
        raise ValueError('Для изображений нужен Gemini PDF с отдельно подтверждённым тарифом изображений за токен')
```

то есть любая отправка картинки теперь падает: у `pdf_analyst` (DeepSeek) тариф
изображений пуст. Разбор карт моделью (r156: «модель видит карту») отключён.

## ЧТО СДЕЛАТЬ

1. Ввести отдельную роль зрения — `vision` — в тех же местах, где сейчас
   перечислены роли:
   - `solo/cloud_settings.py` (`CloudProfile`: поле `vision: ModelConfig | None`,
     проверить `secure()`/serialisation и валидацию `for model in (...)` ~строка 86);
   - `solo/cloud_control.py:6 ROLES` и `:7 LABELS` (например `vision='Зрение: карты'`);
   - `solo/ai_roles.py:17 routerai_preset()` — задать `vision=role(GEMINI,.0,1024,json=False,'low')`
     и `solo/ai_roles.py:45 select_role(profile,'vision')`;
   - `solo/cloud.py:125` (список ролей в `endpoints`) и `:165` — **гейт картинок
     переписать**: при наличии картинок брать `profile.vision`, а если он не
     настроен — падать с тем же внятным текстом, что и сейчас (не молча уходить
     в модель без зрения).
2. Тариф изображений (`image_per_million`) живёт теперь у роли `vision`;
   у `pdf_analyst` он остаётся пустым, и это нормально.
3. Миграция и совместимость: профиль без `vision` ведёт себя как раньше —
   картинки падают с прежним сообщением. Никакого «тихого» выбора модели.
4. Экран настроек ИИ: роль `vision` показывать рядом с остальными, с той же
   проверкой тарифа; цену брать из каталога RouterAI, а не выдумывать.
5. В нашем профиле (`runtime/web-local/cloud-profiles.json` — **не коммитить**)
   прописать `vision` = `google/gemini-3.8-flash`, endpoint `google-ai-studio/flex`,
   тарифы 39,27168375 / 196,35841875 / image 78,5433675 (те, что были у
   `pdf_analyst` до переключения; копия — в `runtime/backups/`).

## ФАЙЛЫ

- `solo/cloud.py`, `solo/cloud_settings.py`, `solo/cloud_control.py`,
  `solo/ai_roles.py`
- `solo/app.py` — если роль перечисляется в ответах API профилей
- тест: `tests/test_vision_role_for_images.py`

## НЕ ТРОГАТЬ

- `pdf_analyst` = `deepseek/deepseek-v4.1-flash` (решение владельца по цене).
- `vendor/nat20/**`, `PATCHES.json`, `rules_data/**`.
- `runtime/web-local/cloud-profiles.json` в коммит не включать (секреты/профиль).
- Платных вызовов не делать: проверка только на моке/сухим прогоном сборки запроса.

## ПРИЁМКА (вывод в отчёт)

1. `SOLO_TEST=1 .venv/bin/python -m pytest tests/test_vision_role_for_images.py -q`
   → N passed: запрос с картинкой уходит конфигу `vision` (по имени модели видно);
   запрос без картинки — в `pdf_analyst`; профиль без `vision` падает с внятным
   текстом, а не уходит в модель без зрения.
2. `GET /api/ai/profiles` (свой стенд) показывает три роли с моделями:
   `interpreter`, `narrator`/`pdf_analyst` = deepseek, `vision` = gemini.
3. Сухой прогон сборки запроса с картинкой (без отправки): печатает выбранную
   модель, число картинок и длину base64 — числа в отчёт.
4. Батч: `test_analysis_images*, test_cloud_accounting, test_ai_roles,
   test_ai_tuning1` → 0 failed.

## ОТЧЁТ (15 строк max)

Коммит `r174: <что>`. Какая модель обслуживает текст, какая картинки, что видит
экран настроек, чем доказано. Плохое — первой строкой.

## ПОТОЛОК

Два подхода. Не закрылось — отчёт фактами, карточку не закрывать.
