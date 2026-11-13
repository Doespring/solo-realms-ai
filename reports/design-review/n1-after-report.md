# n1-after — замер интерфейса приложения (8 кадров: 4 состояний × 2 ширины × 1 темы)

Коммит: `367eeff1` · кадров: **8** · приложение: `http://127.0.0.1:34023` · профиль: `/home/hermes/projects/SoloRealmsAI-Nat20/runtime/n1-after` · браузер: Chromium (ms-playwright 1248) по CDP

Замеры сняты с живого приложения (FastAPI + Chromium): тема — ключ `solo-theme`, кампании посеяны движком Nat20. Живой профиль `runtime/web-local/` и порт 19215 не использовались. «Видимых интерактивных» — элементы в кадре, не за его краем; обрезанные (`scrollWidth > clientWidth + 1`) ищутся по всему документу. Порог тап-таргета 44 px — телефонный (рубрика, критерий 5); на 1440 он приведён справочно, чтобы видеть разницу между полосой наведения и пальцем.

## Таблица кадров

| состояние | тема | ширина | перелив по X | мин. кегль | тап-таргетов < 44 | контраст < 4.5 | границ < 3:1 | видимых интерактивных | разных кеглей | обрезано |
|---|---|---|---|---|---|---|---|---|---|---|
| home | dark | 375 | 0 | 14 | 0 / 5 | 0 | 2 (min 1.35) | 5 | 3 | 0 |
| new | dark | 375 | 0 | 16 | 0 / 6 | 1 (min 3.96) | 4 (min 1.01) | 6 | 3 | 0 |
| prep | dark | 375 | 0 | 16 | 0 / 7 | 0 | 6 (min 1.14) | 7 | 3 | 0 |
| prep-log | dark | 375 | 0 | 16 | 0 / 5 | 0 | 4 (min 1.24) | 5 | 3 | 0 |
| home | dark | 1440 | 0 | 14 | 7 / 10 | 0 | 2 (min 1.35) | 10 | 3 | 0 |
| new | dark | 1440 | 0 | 14 | 9 / 14 | 1 (min 3.96) | 7 (min 1.01) | 14 | 4 | 0 |
| prep | dark | 1440 | 0 | 14 | 8 / 13 | 0 | 7 (min 1.14) | 13 | 5 | 0 |
| prep-log | dark | 1440 | 0 | 14 | 9 / 13 | 0 | 7 (min 1.14) | 13 | 6 | 0 |

Контраст: проверено 180 текстов и 2 плейсхолдеров; ниже 4.5:1 — **2**. Границы контролов (WCAG 1.4.11): границ < 3:1: **39** из 45 проверенных (контролов 45, ячеек листа 0), минимум 1.01:1. Не посчитано: **33**; причины — видимой-границы-нет 29, неактивный-элемент 4. Поштучно в `contrastSkipped` кадра.

`prep` / `prep-log` — настоящий экран подготовки книги. Стенд кладёт свой короткий текст (пять глав, ≤ 5 страниц, ни одного книжного фрагмента) в `runtime/<замер>/books/` и проводит его тем же путём, что у игрока: `receive_source` (извлечение локальное, модель не нужна), привязка документа к черновику, `source_batches` и `ensure_part` из `preparation_pipeline`. Строка `creation_runs` получает те же поля, что пишет `run()`: три части готовы, одна показана отказавшей (журнал не пустой), остальные в работе. Модель не зовётся ни разу — вызовов 0, расход 0. Кадр снимается по появлению панели `#creation-progress-panel .prep-panel`, а не по таймауту; `prep-log` раскрывает журнал (сохранённая история ошибок + список отказавших блоков).

`game-kb` — клавиатура смоделирована так же, как её видит `web/mobile.js`: кадр сжат до 476 px (812 − 336) и выставлен класс `kb-open`. Системной клавиатуры в headless Chromium нет, поэтому полоса клавиатуры на снимке не рисуется — измеряется только сжатое состояние экрана.

Стенд: кампании сеет движок Nat20, и `create_campaign(name)` пишет одно имя и в кампанию, и в героя (`solo/nat20_api.py:454`). Поэтому стенд с n08 сеет кампанию именем героя («Мирон»), а название истории ставит отдельной строкой таблицы — как в продукте, где имя героя и название истории задаются отдельно. Состояние движка при этом не подменяется: в рантайме герой так и остаётся «Мироном».

## Приёмка экрана подготовки (n11)

| кадр | md5 | md5 `new` | отличается | полоса | «Сейчас» | журнал отказов | история ошибок |
|---|---|---|---|---|---|---|---|
| `n1-after-prep-dark-375.png` | `181d823f15133833264c25aedf5d6a33` | `384a749add1bd9f7f987aab544984f17` | да | 3 из 5 готово · 190.188px | Сейчас | 1 строк | свёрнута |
| `n1-after-prep-log-dark-375.png` | `828ed9d0da84b148e2c651e752693aa0` | `384a749add1bd9f7f987aab544984f17` | да | 3 из 5 готово · 190.188px | Сейчас | 1 строк | раскрыта |
| `n1-after-prep-dark-1440.png` | `8cce6be68e0a6461faabea67da69d9e4` | `1ac2919e1fc93eab1f08e4defa97db76` | да | 3 из 5 готово · 375.594px | Сейчас | 1 строк | свёрнута |
| `n1-after-prep-log-dark-1440.png` | `f0012b09cac5e266c457d3b59292edd4` | `1ac2919e1fc93eab1f08e4defa97db76` | да | 3 из 5 готово · 375.594px | Сейчас | 1 строк | раскрыта |

Кадров подготовки, отличных от `new` той же ширины и темы: **4 из 4**.

## Приёмка n13 (точечные проверки кадра)

Числа сняты одним и тем же JS-замером (`probe` в `MEASURE_JS`): `y`/`bottom` — координаты в кадре, `строк` — сколько строк текста в поле ввода (высота содержимого / line-height), переносы подписей меню считаются по границам символов (`Range`), а не по высоте кнопки.

| кадр | поле ввода: y / bottom / строк | «Подготовить» в кадре | шапка статов | характеристик: колонок / низ последней / в окне | меню: подписей / переносов / разорванных | «компьютере» | внутренние слова |
|---|---|---|---|---|---|---|---|
| `n1-after-home-dark-375.png` | 0 / 0 / -0.8 | НЕТ в кадре | — | 1 / 0 / да | 7 / 0 / 0 | 0 | 0 |
| `n1-after-new-dark-375.png` | 164.4 / 337.4 / 6 | есть, y 355 | 12/12 хиты·18 защита | 1 / 0 / да | 7 / 0 / 0 | 0 | 0 |
| `n1-after-prep-dark-375.png` | -848.6 / -675.6 / 6 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | 7 / 0 / 0 | 0 | 0 |
| `n1-after-prep-log-dark-375.png` | -1004.6 / -831.6 / 6 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | 7 / 0 / 0 | 0 | 0 |
| `n1-after-home-dark-1440.png` | 0 / 0 / -1 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | — / 0 / 0 | ЕСТЬ | 0 |
| `n1-after-new-dark-1440.png` | 247 / 393 / 6 | есть, y 411 | 12/12 хиты·18 защита | 1 / 0 / да | — / 0 / 0 | ЕСТЬ | 0 |
| `n1-after-prep-dark-1440.png` | -490 / -344 / 6 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | — / 0 / 0 | ЕСТЬ | 0 |
| `n1-after-prep-log-dark-1440.png` | -640 / -494 / 6 | НЕТ в кадре | 12/12 хиты·18 защита | 1 / 0 / да | — / 0 / 0 | ЕСТЬ | 0 |

## Приёмка n14 (меню на вкладки, хиты, главная, блокнот)

Числа сняты тем же JS-замером (`probe`): `вкладка` — открытая вкладка шторки и сколько строк реально видно в её группе (скрытая группа не считается); `HP` — сколько раз подпись «HP» с числом встречается в панели героя и во всём видимом тексте документа; `главная` — секции-списки истории и кнопка «Все истории»; `блокнот` — пустые секции, кнопки сохранения и свободная ширина справа.

| кадр | вкладка шторки | строк в группе | строк в шторке | перелив шторки | «HP» в панели / в документе | главная: секций / «Все истории» | блокнот: пустых секций / сохранений / справа пусто |
|---|---|---|---|---|---|---|---|
| `n1-after-home-dark-375.png` | campaign (Кампания: 6) | 6 | 7 | 0 | 0 / 0 | 1 / 1 | — |
| `n1-after-new-dark-375.png` | campaign (Кампания: 6) | 6 | 7 | 0 | 0 / 0 | — | — |
| `n1-after-prep-dark-375.png` | campaign (Кампания: 6) | 6 | 7 | 0 | 0 / 0 | — | — |
| `n1-after-prep-log-dark-375.png` | campaign (Кампания: 6) | 6 | 7 | 0 | 0 / 0 | — | — |
| `n1-after-home-dark-1440.png` | campaign (нет строк) | 0 | 0 | 0 | 0 / 0 | 1 / 1 | — |
| `n1-after-new-dark-1440.png` | campaign (нет строк) | 0 | 0 | 0 | 0 / 0 | — | — |
| `n1-after-prep-dark-1440.png` | campaign (нет строк) | 0 | 0 | 0 | 0 / 0 | — | — |
| `n1-after-prep-log-dark-1440.png` | campaign (нет строк) | 0 | 0 | 0 | 0 / 0 | — | — |

## Что не открылось и почему

- нет: все 8 кадров сняты с живых экранов приложения.

## Где именно болит (по числам)

- горизонтальный перелив: **0 px во всех 8 снятых кадрах** (`scrollWidth` равен ширине окна)
- тап-таргеты < 44 px: **9** из 14 — `new` 1440 (в 1 темах); худшие: `html > body.effects.campaign-open > aside.sidebar > a.brand` 115.6×21; `body.effects.campaign-open > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.campaign-open > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **9** из 13 — `prep-log` 1440 (в 1 темах); худшие: `html > body.effects.campaign-open > aside.sidebar > a.brand` 115.6×21; `body.effects.campaign-open > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.campaign-open > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **8** из 13 — `prep` 1440 (в 1 темах); худшие: `html > body.effects.tabletop > aside.sidebar > a.brand` 115.6×21; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- тап-таргеты < 44 px: **7** из 10 — `home` 1440 (в 1 темах); худшие: `html > body.effects.tabletop > aside.sidebar > a.brand` 115.6×21; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(1)` 68×38; `body.effects.tabletop > aside.sidebar > nav > div.nav-bar:nth-of-type(1) > button.nav:nth-of-type(2)` 111×38
- на 1440 порог 44 px — справочный (это мышь, а не палец): промахи там дают боковое меню, знак и переключатель темы (`a.brand` 21 px, `button.nav` 38 px), тот же набор, что на `new`; к панели подготовки претензий нет.
- контраст < 4.5:1: **1** текстов — `new` 375 (в 1 темах); худшие: `textarea#creation-description` 3.96:1
- контраст < 4.5:1: **1** текстов — `new` 1440 (в 1 темах); худшие: `textarea#creation-description` 3.96:1
- границы контролов < 3:1: **7** элементов из 8 — `new` 1440 (в 1 темах); худшие: `section#creation > div.creation-drop:nth-of-type(2) > button` (рамка, top, 1 px) 1.01:1; `section#creation-mock-notice > div.button-row > button` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(1)` (рамка, top, 1 px) 1.14:1
- границы контролов < 3:1: **7** элементов из 8 — `prep` 1440 (в 1 темах); худшие: `section#creation > div.creation-files:nth-of-type(3) > article.creation-file > button` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(1)` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(2)` (рамка, top, 1 px) 1.14:1
- границы контролов < 3:1: **7** элементов из 7 — `prep-log` 1440 (в 1 темах); худшие: `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(1)` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(2)` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(3)` (рамка, top, 1 px) 1.14:1
- границы контролов < 3:1: **6** элементов из 6 — `prep` 375 (в 1 темах); худшие: `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(1)` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(2)` (рамка, top, 1 px) 1.14:1; `section#creation > div.creation-controls:nth-of-type(4) > button:nth-of-type(3)` (рамка, top, 1 px) 1.14:1
- границы контролов < 3:1: **4** элементов из 5 — `new` 375 (в 1 темах); худшие: `section#creation > div.creation-drop:nth-of-type(2) > button` (рамка, top, 1 px) 1.01:1; `section#creation-mock-notice > div.button-row > button` (рамка, top, 1 px) 1.14:1; `textarea#creation-description` (рамка, top, 1 px) 1.31:1
- границы контролов < 3:1: **4** элементов из 4 — `prep-log` 375 (в 1 темах); худшие: `div#creation-handoff > details.disclosure` (рамка, top, 1 px) 1.24:1; `html > body.effects.campaign-open > main > header > button.themetoggle:nth-of-type(2)` (рамка, top, 1 px) 1.35:1; `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-head:nth-of-type(1) > button.prep-cancel` (рамка, top, 1 px) 1.67:1
- минимальный кегль **14 px** — `home` 375 (в 1 темах, кеглей на экране: 3)
- минимальный кегль **14 px** — `home` 1440 (в 1 темах, кеглей на экране: 3)
- минимальный кегль **14 px** — `new` 1440 (в 1 темах, кеглей на экране: 4)
- минимальный кегль **14 px** — `prep` 1440 (в 1 темах, кеглей на экране: 5)
- минимальный кегль **14 px** — `prep-log` 1440 (в 1 темах, кеглей на экране: 6)
- минимальный кегль **16 px** — `new` 375 (в 1 темах, кеглей на экране: 3)

## Цвета вне `web/design-tokens.css`

- все вычисленные цвета текста, фона и рамок входят в палитру `web/design-tokens.css` (белый и чёрный считаются допустимыми).

## Как повторить

```
.venv/bin/python tools/ui_audit.py            # 40 кадров + <tag>-measures.json + <tag>-report.md
.venv/bin/python tools/ui_audit.py --tag 02     # повторный замер в 02-report.md, кадры 01 не трогаются
.venv/bin/python tools/ui_audit.py --only new,prep,prep-log   # экран подготовки против «Новое приключение»
```

