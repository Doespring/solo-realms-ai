# Ассеты

Что лежит в `design/assets/`, что нужно сгенерировать и куда что вставляется.
Формат путей — относительно `design/`.

## Уже есть: шрифты

Скопированы из `web/visual/fonts/` без изменений, лицензии рядом.

| Файл | Что это | Размер | Где работает |
|---|---|---|---|
| `assets/fonts/book-regular.ttf` | EB Garamond Regular, SIL OFL 1.1 | 851 КБ | рассказ, заголовки, имена, числа на бумаге |
| `assets/fonts/book-italic.ttf` | EB Garamond Italic, SIL OFL 1.1 | 754 КБ | голос игрока, вступление, курсив на листе |
| `assets/fonts/ui-regular.ttf` | PT Sans Regular, SIL OFL 1.1 | 443 КБ | весь интерфейс, подписи, пометки |
| `assets/fonts/ui-bold.ttf` | PT Sans Bold, SIL OFL 1.1 | 470 КБ | числа механики, акценты, кнопки |

Обе гарнитуры проверены на кириллицу (225 и 210 знаков, включая `ё`, `—`, `«»`,
`№`). Отсутствуют `✓ ✧ ★ ◆` — в макетах они не используются, вместо них
иконки и CSS-фигуры. Подключаются через `@font-face` в `screens/core.css`.

## Уже есть: иконки

`game-icons.net`, авторы **Lorc** и **Delapouite**, лицензия **CC BY 3.0**.
Скопированы из `web/visual/icons/`, цвет заменён на `currentColor`, размер
`viewBox 0 0 512 512`. В CSS каждая превращена в маску (класс `.ic-<имя>`),
поэтому красится `color` и не делает сетевых запросов.

| Файл | Автор | Где используется |
|---|---|---|
| `icons/compass.svg` | Lorc | 01 — кнопка «к списку историй», 02 — мини-лист (инициатива) |
| `icons/book.svg` | Lorc | 01 — «Загрузить книгу», 05 — файл-чип |
| `icons/save.svg` | Delapouite | 01 — сохранения, подвал «остаётся на компьютере» |
| `icons/settings.svg` | Delapouite | 01 — настройки |
| `icons/exit.svg` | Delapouite | 02, 04, 05 — «назад» |
| `icons/person.svg` | Delapouite | 02, 03, 04 — лист героя, «Ты» |
| `icons/heart.svg` | Delapouite | 02, 03, 04 — хиты |
| `icons/shield.svg` | Delapouite | 02, 03, 04 — защита |
| `icons/longsword.svg` | Lorc | 02 — короткий меч в списке «с собой» |
| `icons/leather_armor.svg` | Delapouite | 02 — кожаный доспех |
| `icons/rope.svg` | Delapouite | 02 — верёвка |
| `icons/coins.svg` | Delapouite | 02 — монеты |
| `icons/feather.svg` | Lorc | 03, 04 — «Мастер» и кнопка «Сказать» |
| `icons/journal.svg` | Lorc | 03, 04 — журнал |
| `icons/map.svg` | Lorc | 03 — карта, 05 — отсутствующая карта подземелья |
| `icons/search.svg` | Lorc | 05 — неразобранные сканы |

Полный набор (65 ресурсов) остаётся в `web/visual/icons/`; новый файл кладётся
в `assets/icons/` и подключается командой `python3 tools/build-icons.py`.

**Атрибуция обязательна** и уже стоит в подвале каждого макета:
«Иконки: Lorc, Delapouite — game-icons.net, CC BY 3.0».

## Нужно сгенерировать владельцу

Промпты — в `PROMPTS.md`. Пока этих файлов нет, макеты работают на CSS:
фоны — градиенты, бумага — градиент с зерном (SVG-шум в data-URI), рамка листа —
тонкая линия. Подстановка ничего не ломает.

| Файл | Размер | Формат | Где | Статус |
|---|---|---|---|---|
| `assets/img/start-bg.webp` | 2880 × 1920 | WebP q85 | 01 — фон страницы, 18–24 % | нужно |
| `assets/img/paper.webp` | 2048 × 2048, бесшовно | WebP q85 | 02 и 05 — фон `.sheet`, multiply 45 % | нужно |
| `assets/img/sheet-frame.png` | 2480 × 3508 (A4, 300 ppi) | PNG с альфой | 02 и 05 — рамка поверх листа | нужно |
| `assets/img/icons-style.png` | 1024 × 1024 | PNG | образец для добора иконок | по желанию |
| `assets/img/token-miron.png` | 1024 × 1024 | PNG с альфой | 04 — фишка игрока вместо буквы «М» | по желанию |
| `assets/img/token-foe.png` | 1024 × 1024 | PNG с альфой | 04 — фишки противников | по желанию |

Требования к качеству: без букв и цифр в кадре, без чужих логотипов и
узнаваемых персонажей, без рамок и подписей. Для печатных версий плакатов
(если понадобятся) — те же сюжеты в 300 ppi.

## Как подставить, когда файлы появятся

В `screens/01-start.css`:

```css
body.start{
  background-image:url("../assets/img/start-bg.webp");
  background-size:cover; background-position:center 40%; background-attachment:fixed;
}
```

В `screens/core.css`, в блок `.sheet`:

```css
.sheet::before{ opacity:.45; mix-blend-mode:multiply;
  background-image:url("../assets/img/paper.webp"); background-size:420px; }
```

Рамку — на `.sheet::after` вместо линии: `background:url(...) center/100% 100% no-repeat`.
Токены — `background-image` у `.token--hero` / `.token--foe`, буквы внутри скрыть.

## Чего в макетах нет и почему

- **Портретов героя** — их негде показывать: в игре нет постоянной панели,
  а лист героя обходится типографикой. Когда появится экран «кто я», портрет
  пойдёт туда.
- **Иллюстраций сцен** — мастер пишет словами; картинка на каждый ход замедлила
  бы чтение и стоила бы денег. Фон первого экрана — единственная «атмосфера».
- **PNG-рамок Kenney** — они есть в проекте, но я их не использую: рамка
  из готового набора тянет за собой чужую пластику (пиксельные уголки),
  а листу нужна типографская линия.
