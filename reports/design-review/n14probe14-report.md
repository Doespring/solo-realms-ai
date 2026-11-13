# n14probe14 — замер интерфейса приложения (5 кадров: 5 состояний × 1 ширины × 1 темы)

Коммит: `09a67561` · кадров: **5** · приложение: `http://127.0.0.1:48091` · профиль: `/home/hermes/projects/SoloRealmsAI-Nat20/runtime/n07-audit` · браузер: Chromium (ms-playwright 1248) по CDP

Замеры сняты с живого приложения (FastAPI + Chromium): тема — ключ `solo-theme`, кампании посеяны движком Nat20. Живой профиль `runtime/web-local/` и порт 19215 не использовались. «Видимых интерактивных» — элементы в кадре, не за его краем; обрезанные (`scrollWidth > clientWidth + 1`) ищутся по всему документу. Порог тап-таргета 44 px — телефонный (рубрика, критерий 5); на 1440 он приведён справочно, чтобы видеть разницу между полосой наведения и пальцем.

## Таблица кадров

| состояние | тема | ширина | перелив по X | мин. кегль | тап-таргетов < 44 | контраст < 4.5 | видимых интерактивных | разных кеглей | обрезано |
|---|---|---|---|---|---|---|---|---|---|
| menu | dark | 1440 | 0 | 14 | 16 / 34 | 0 | 34 | 3 | 0 |
| menu-app | dark | 1440 | 0 | 14 | 19 / 37 | 0 | 37 | 3 | 0 |
| home | dark | 1440 | 0 | 14 | 7 / 25 | 0 | 25 | 3 | 0 |
| notes | dark | 1440 | 0 | 12 | 13 / 15 | 0 | 15 | 5 | 0 |
| game | dark | 1440 | 0 | 14 | 9 / 12 | 0 | 12 | 4 | 0 |

`prep` / `prep-log` — настоящий экран подготовки книги. Стенд кладёт свой короткий текст (пять глав, ≤ 5 страниц, ни одного книжного фрагмента) в `runtime/<замер>/books/` и проводит его тем же путём, что у игрока: `receive_source` (извлечение локальное, модель не нужна), привязка документа к черновику, `source_batches` и `ensure_part` из `preparation_pipeline`. Строка `creation_runs` получает те же поля, что пишет `run()`: три части готовы, одна показана отказавшей (журнал не пустой), остальные в работе. Модель не зовётся ни разу — вызовов 0, расход 0. Кадр снимается по появлению панели `#creation-progress-panel .prep-panel`, а не по таймауту; `prep-log` раскрывает журнал (сохранённая история ошибок + список отказавших блоков).

`game-kb` — клавиатура смоделирована так же, как её видит `web/mobile.js`: кадр сжат до 476 px (812 − 336) и выставлен класс `kb-open`. Системной клавиатуры в headless Chromium нет, поэтому полоса клавиатуры на снимке не рисуется — измеряется только сжатое состояние экрана.

Стенд: кампании сеет движок Nat20, и `create_campaign(name)` пишет одно имя и в кампанию, и в героя (`solo/nat20_api.py:454`). Поэтому стенд с n08 сеет кампанию именем героя («Мирон»), а название истории ставит отдельной строкой таблицы — как в продукте, где имя героя и название истории задаются отдельно. Состояние движка при этом не подменяется: в рантайме герой так и остаётся «Мироном».

## Приёмка n13 (точечные проверки кадра)

Числа сняты одним и тем же JS-замером (`probe` в `MEASURE_JS`): `y`/`bottom` — координаты в кадре, `строк` — сколько строк текста в поле ввода (высота содержимого / line-height), переносы подписей меню считаются по границам символов (`Range`), а не по высоте кнопки.

| кадр | поле ввода: y / bottom / строк | «Подготовить» в кадре | шапка статов | характеристик: колонок / низ последней / в окне | меню: подписей / переносов / разорванных | «компьютере» | внутренние слова |
|---|---|---|---|---|---|---|---|
| `n14probe14-menu-dark-1440.png` | 0 / 0 / -1 | НЕТ в кадре | — | 1 / 0 / да | 7 / 0 / 0 | ЕСТЬ | 0 |
| `n14probe14-menu-app-dark-1440.png` | 0 / 0 / -1 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | 10 / 0 / 0 | ЕСТЬ | 0 |
| `n14probe14-home-dark-1440.png` | 0 / 0 / -1 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | — / 0 / 0 | ЕСТЬ | 0 |
| `n14probe14-notes-dark-1440.png` | 0 / 0 / -1 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | — / 0 / 0 | 0 | 0 |
| `n14probe14-game-dark-1440.png` | 0 / 0 / -1 | НЕТ в кадре | 12/12 хиты · 18 защита | 1 / 0 / да | — / 0 / 0 | 0 | 0 |

## Что не открылось и почему

- нет: все 5 кадров сняты с живых экранов приложения.

## Где именно болит (по числам)

- горизонтальный перелив: **0 px во всех 5 кадрах** (`scrollWidth` равен ширине окна)
- тап-таргеты < 44 px: **19** из 37 — `menu-app` 1440 (в 1 темах); худшие: `html > body.effects.campaign-open > aside.sidebar.show-more > a.brand` 115.6×21; `body.effects.campaign-open > aside.sidebar.show-more > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.campaign-open > aside.sidebar.show-more > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **16** из 34 — `menu` 1440 (в 1 темах); худшие: `html > body.effects.tabletop > aside.sidebar.show-more > a.brand` 115.6×21; `body.effects.tabletop > aside.sidebar.show-more > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.tabletop > aside.sidebar.show-more > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **13** из 15 — `notes` 1440 (в 1 темах); худшие: `html > body.effects.campaign-open > aside.sidebar > a.brand` 115.6×21; `body.effects.campaign-open > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.campaign-open > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **9** из 12 — `game` 1440 (в 1 темах); худшие: `html > body.effects.tabletop > aside.sidebar > a.brand` 115.6×21; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav.active:nth-of-type(1)` 68×38; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **7** из 25 — `home` 1440 (в 1 темах); худшие: `html > body.effects.tabletop > aside.sidebar > a.brand` 115.6×21; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- на 1440 порог 44 px — справочный (это мышь, а не палец): промахи там дают боковое меню, знак и переключатель темы (`a.brand` 21 px, `button.nav` 38 px), тот же набор, что на `new`; к панели подготовки претензий нет.
- минимальный кегль **12 px** — `notes` 1440 (в 1 темах, кеглей на экране: 5); мельче 14 px: `details#tabletop-dice > form > label:nth-of-type(1)` 12 px «Кости»; `details#tabletop-dice > form > label:nth-of-type(2)` 12 px «Грани»
- минимальный кегль **14 px** — `menu` 1440 (в 1 темах, кеглей на экране: 3)
- минимальный кегль **14 px** — `menu-app` 1440 (в 1 темах, кеглей на экране: 3)
- минимальный кегль **14 px** — `home` 1440 (в 1 темах, кеглей на экране: 3)
- минимальный кегль **14 px** — `game` 1440 (в 1 темах, кеглей на экране: 4)
- шторка `menu` 1440: высота 401 при окне 900, строк 17, групп 2 (Кампания, Приложение), прокрутка 0 px (scrollTop 0)
- шторка `menu-app` 1440: высота 521 при окне 900, строк 17, групп 2 (Кампания, Приложение), прокрутка 0 px (scrollTop 0)

## Цвета вне `web/design-tokens.css`

- все вычисленные цвета текста, фона и рамок входят в палитру `web/design-tokens.css` (белый и чёрный считаются допустимыми).

## Как повторить

```
.venv/bin/python tools/ui_audit.py            # 40 кадров + <tag>-measures.json + <tag>-report.md
.venv/bin/python tools/ui_audit.py --tag 02     # повторный замер в 02-report.md, кадры 01 не трогаются
.venv/bin/python tools/ui_audit.py --only new,prep,prep-log   # экран подготовки против «Новое приключение»
```

