> **Оборванный заход, не для приёмки.** Кадры `t11a-*` сняты до правки
> `web/prepare-progress.js` (у засечек был нулевой кегль, подписи «Сейчас» не было), и
> браузер отдавал старый скрипт из кэша профиля. Приёмка n11 — `04-report.md`,
> `04-measures.json`, кадры `04-*.png`.

# t11a — замер интерфейса приложения (10 состояний × 2 ширины × 2 темы)

Коммит: `64a57ad0` · кадров: **2** · приложение: `http://127.0.0.1:55179` · профиль: `/home/hermes/projects/SoloRealmsAI-Nat20/runtime/n11-audit4` · браузер: Chromium (ms-playwright 1248) по CDP

Замеры сняты с живого приложения (FastAPI + Chromium): тема — ключ `solo-theme`, кампании посеяны движком Nat20. Живой профиль `runtime/web-local/` и порт 19215 не использовались. «Видимых интерактивных» — элементы в кадре, не за его краем; обрезанные (`scrollWidth > clientWidth + 1`) ищутся по всему документу. Порог тап-таргета 44 px — телефонный (рубрика, критерий 5); на 1440 он приведён справочно, чтобы видеть разницу между полосой наведения и пальцем.

## Таблица 40 кадров

| состояние | тема | ширина | перелив по X | мин. кегль | тап-таргетов < 44 | контраст < 4.5 | видимых интерактивных | разных кеглей | обрезано |
|---|---|---|---|---|---|---|---|---|---|
| prep ⚠ | dark | 375 | 0 | 0 | 0 / 6 | 7 (min 1.49) | 6 | 3 | 0 |
| prep-log ⚠ | dark | 375 | 0 | 0 | 0 / 8 | 7 (min 1.49) | 8 | 4 | 0 |

⚠ `prep` / `prep-log` — снят экран создания приключения: панель подготовки книги (`#creation-progress-panel .prep-panel`) в изолированном профиле не появляется. Mock-Мастер книги не готовит (`prepare.disabled = true`), настоящая подготовка требует настроенного облачного провайдера — в тестовом профиле его нет и платных вызовов не делалось.

`game-kb` — клавиатура смоделирована так же, как её видит `web/mobile.js`: кадр сжат до 476 px (812 − 336) и выставлен класс `kb-open`. Системной клавиатуры в headless Chromium нет, поэтому полоса клавиатуры на снимке не рисуется — измеряется только сжатое состояние экрана.

Стенд: кампании сеет движок Nat20, и `create_campaign(name)` пишет одно имя и в кампанию, и в героя (`solo/nat20_api.py:454`). Поэтому стенд с n08 сеет кампанию именем героя («Мирон»), а название истории ставит отдельной строкой таблицы — как в продукте, где имя героя и название истории задаются отдельно. Состояние движка при этом не подменяется: в рантайме герой так и остаётся «Мироном».

## Что не открылось и почему

- нет: все 40 кадров сняты с живых экранов приложения.

## Где именно болит (по числам)

- горизонтальный перелив: **0 px во всех 40 кадрах** (`scrollWidth` равен ширине окна)
- контраст < 4.5:1: **7** текстов — `prep` 375 (в 1 темах); худшие: `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-active:nth-of-type(5)` 1.49:1; `div#creation-progress-panel > div.prep-panel.is-failed > p.prep-steps:nth-of-type(1) > span.prep-arrow:nth-of-type(2)` 1.67:1; `div#creation-progress-panel > div.prep-panel.is-failed > p.prep-steps:nth-of-type(1) > span.prep-arrow:nth-of-type(4)` 1.67:1
- контраст < 4.5:1: **7** текстов — `prep-log` 375 (в 1 темах); худшие: `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-active:nth-of-type(5)` 1.49:1; `div#creation-progress-panel > div.prep-panel.is-failed > p.prep-steps:nth-of-type(1) > span.prep-arrow:nth-of-type(2)` 1.67:1; `div#creation-progress-panel > div.prep-panel.is-failed > p.prep-steps:nth-of-type(1) > span.prep-arrow:nth-of-type(4)` 1.67:1
- минимальный кегль **0 px** — `prep` 375 (в 1 темах, кеглей на экране: 3); `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-done:nth-of-type(1)` 0 px «1»; `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-done:nth-of-type(2)` 0 px «2»; `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-done:nth-of-type(3)` 0 px «3»
- минимальный кегль **0 px** — `prep-log` 375 (в 1 темах, кеглей на экране: 4); `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-done:nth-of-type(1)` 0 px «1»; `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-done:nth-of-type(2)` 0 px «2»; `div#creation-progress-panel > div.prep-panel.is-failed > div.prep-ticks:nth-of-type(3) > span.prep-tick.is-done:nth-of-type(3)` 0 px «3»

## Цвета вне `web/design-tokens.css`

- все вычисленные цвета текста, фона и рамок входят в палитру `web/design-tokens.css` (белый и чёрный считаются допустимыми).

## Как повторить

```
.venv/bin/python tools/ui_audit.py            # 40 кадров + <tag>-measures.json + <tag>-report.md
.venv/bin/python tools/ui_audit.py --tag 02     # повторный замер в 02-report.md, кадры 01 не трогаются
```

