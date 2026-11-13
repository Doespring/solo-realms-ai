# r223 — карта перевода: старые цвета → токены

Снято с живого дерева 04.11 автоматически (не на глаз): пройдены все `web/*.css`, посчитаны
каждое использование `#hex` и расстояние до ближайшего токена в CIELAB. ΔE < 6 — замена
незаметна, 6–12 — надо посмотреть глазами, > 12 — цвет в палитре отсутствует.

## 1. Что выяснилось (это меняет постановку задачи)

- `web/index.html` грузит **17 файлов CSS сразу**, в порядке: `style → workspace → combat →
  inventory → world → obsidian → tabletop → creation → character-creation → chronicles →
  prototype → wizard → themes (дважды!) → design-tokens → game-screen → design-screens →
  design-table`. То есть старый слой не отключён — он лежит под новым, и всё, что новый не
  перекрыл, работает по-старому.
- В CSS живут **четыре** темы: `dark`, `obsidian`, `parchment`, `minimal` — все на жёстко
  прописанных цветах.
  `themes.css`: obsidian ×2, dark ×3, parchment ×13, minimal ×6;
  `obsidian.css`: obsidian ×29, dark ×2; `style.css`: parchment ×2, minimal ×2.
- В `index.html` стоит `data-theme="dark"`, переключатель — в `web/obsidian.js`:
  `select#theme` + `localStorage['solo-theme']`, и он же добавляет пункт
  «Obsidian Chronicle — пергамент и обсидиан». Новый язык ждёт только `dark` и `light`.
- Значит переводить надо не 205 цветов поштучно, а **снять легаси-палитры** и перевести
  остатки. Из 118 уникальных цветов 44 — из палитр obsidian/parchment, которые просто
  удаляются.

## 2. Инвентарь файлов

| файл | строк | правил | использований `#hex` | уникальных |
|---|---:|---:|---:|---:|
| `themes.css` | 204 | 78 | 72 | 40 |
| `obsidian.css` | 48 | 37 | 41 | 31 |
| `style.css` | 7 | 6 | 39 | 37 |
| `game-screen.css` | 439 | 204 | 16 | 12 |
| `wizard.css` | 210 | 128 | 11 | 5 |
| `creation.css` | 8 | 7 | 7 | 7 |
| `tabletop.css` | 5 | 3 | 7 | 7 |
| `design-table.css` | 246 | 95 | 5 | 4 |
| `chronicles.css` | 1 | 1 | 4 | 4 |
| `prototype.css` | 6 | 6 | 2 | 1 |
| `workspace.css` | 13 | 6 | 1 | 1 |
| `combat.css` | 9 | 9 | 0 | 0 |
| `inventory.css` | 8 | 7 | 0 | 0 |
| `world.css` | 4 | 4 | 0 | 0 |
| `design-screens.css` | 96 | 69 | 0 | 0 |

`combat.css`, `inventory.css`, `world.css` — цвета уже на токенах (0 hex), но правил мало
(9/7/4): проверить, что эти экраны вообще оформлены, а не пустые.

## 3. Вердикт по каждому файлу

| файл | что делать |
|---|---|
| `themes.css` | **свести к нулю.** 78 правил и 72 цвета — палитры obsidian/parchment/minimal плюс сайдбар. Тёмная палитра уже есть в токенах, остальное не нужно. Не переводить hex в hex. |
| `obsidian.css` | **удалить.** Весь файл — под `html[data-theme=obsidian]`, а такой темы в новом языке нет. |
| `style.css` | оставить `:root`-переменные, убрать блоки parchment/minimal. |
| `prototype.css` | 2 цвета `#8c261b` → `var(--sheet-bad)`. |
| `wizard.css` | 210 строк, 128 правил — экран создания героя, уже переведён r221: заменить оставшиеся 5 цветов токенами, дубли правил убрать. |
| `game-screen.css` | 439 строк, 204 правила, 16 использований / 4 уникальных цвета — игровой экран, переведён r220: заменить, дубли снять. |
| `chronicles.css`, `creation.css`, `tabletop.css`, `workspace.css` | 1–8 строк: заменить точечно. |
| `combat.css`, `inventory.css`, `world.css` | уже на токенах; проверить полноту оформления. |
| `design-tokens.css` | **единственный файл, где `#hex` остаётся.** Сюда же — светлая тема. |

## 4. Автозамена: ΔE ≤ 6 (45 цветов) — менять не глядя

| файл | цвет | раз | ближайший токен | ΔE |
|---|---|---:|---|---:|
| `themes.css` | `#1d1f1c` | 4 | `--sheet-bg` `#1e1a14` | 4.3 |
| `themes.css` | `#f6edda` | 3 | `--text` `#ede3ce` | 3.7 |
| `themes.css` | `#efe1c0` | 2 | `--sheet-ink` `#e8ddc7` | 5.9 |
| `themes.css` | `#1a1c19` | 2 | `--sheet-bg` `#1e1a14` | 3.9 |
| `themes.css` | `#262a23` | 2 | `--ink-700` `#2e2720` | 5.6 |
| `themes.css` | `#101210` | 1 | `--ink-900` `#14110d` | 2.3 |
| `themes.css` | `#181c19` | 1 | `--sheet-bg` `#1e1a14` | 4.7 |
| `themes.css` | `#2e3024` | 1 | `--line` `#332b22` | 5.6 |
| `themes.css` | `#121613` | 1 | `--ink-900` `#14110d` | 3.4 |
| `themes.css` | `#0005` | 1 | `--ink-950` `#0d0b08` | 3.4 |
| `themes.css` | `#17190f` | 1 | `--out-hit-ink` `#0f1408` | 2.9 |
| `themes.css` | `#2a2d26` | 1 | `--ink-700` `#2e2720` | 5.4 |
| `obsidian.css` | `#302a26` | 4 | `--ink-700` `#2e2720` | 2.6 |
| `obsidian.css` | `#f2e5c8` | 2 | `--text` `#ede3ce` | 4.3 |
| `obsidian.css` | `#efe1c0` | 2 | `--sheet-ink` `#e8ddc7` | 5.9 |
| `obsidian.css` | `#faf2dd` | 1 | `--text` `#ede3ce` | 5.2 |
| `obsidian.css` | `#302a2620` | 1 | `--ink-700` `#2e2720` | 2.6 |
| `obsidian.css` | `#f2e5c870` | 1 | `--text` `#ede3ce` | 4.3 |
| `obsidian.css` | `#f6edda` | 1 | `--text` `#ede3ce` | 3.7 |
| `obsidian.css` | `#c7b796` | 1 | `--roll-num` `#c6b89c` | 2.9 |
| `obsidian.css` | `#e9dfc8` | 1 | `--sheet-ink` `#e8ddc7` | 0.9 |
| `style.css` | `#101210` | 1 | `--ink-900` `#14110d` | 2.3 |
| `style.css` | `#181c19` | 1 | `--sheet-bg` `#1e1a14` | 4.7 |
| `style.css` | `#2e3024` | 1 | `--line` `#332b22` | 5.6 |
| `style.css` | `#121613` | 1 | `--ink-900` `#14110d` | 3.4 |
| `style.css` | `#0005` | 1 | `--ink-950` `#0d0b08` | 3.4 |
| `style.css` | `#080e09b8` | 1 | `--ink-950` `#0d0b08` | 2.6 |
| `style.css` | `#eee8db` | 1 | `--text` `#ede3ce` | 4.7 |
| `style.css` | `#e8e0cf` | 1 | `--text` `#ede3ce` | 2.5 |
| `style.css` | `#e4dbc7` | 1 | `--sheet-ink` `#e8ddc7` | 1.5 |
| `wizard.css` | `#14110d` | 3 | `--ink-900` `#14110d` | 0.0 |
| `wizard.css` | `#1d1812` | 3 | `--roll-bg` `#1b1711` | 0.9 |
| `wizard.css` | `#1b1711` | 2 | `--roll-bg` `#1b1711` | 0.0 |
| `wizard.css` | `#2a231b` | 2 | `--ink-750` `#262019` | 1.8 |
| `wizard.css` | `#241e17` | 1 | `--sheet-bg-2` `#221d16` | 0.8 |
| `game-screen.css` | `#3a3128` | 2 | `--ink-600` `#3a3128` | 0.0 |
| `game-screen.css` | `#191207` | 2 | `--out-crit-ink` `#1b1207` | 1.2 |
| `game-screen.css` | `#f4ead6` | 2 | `--text` `#ede3ce` | 2.5 |
| `game-screen.css` | `#2e2720` | 1 | `--ink-700` `#2e2720` | 0.0 |
| `game-screen.css` | `#dcd0b8` | 1 | `--sheet-ink` `#e8ddc7` | 4.8 |
| `game-screen.css` | `#221c15` | 1 | `--sheet-bg-2` `#221d16` | 0.7 |
| `game-screen.css` | `#c4af8c` | 1 | `--roll-num` `#c6b89c` | 5.8 |
| `game-screen.css` | `#f0e5cf` | 1 | `--text` `#ede3ce` | 1.0 |
| `game-screen.css` | `#14110de8` | 1 | `--ink-900` `#14110d` | 0.0 |
| `game-screen.css` | `#131009` | 1 | `--ink-900` `#14110d` | 1.2 |

## 5. Посмотреть глазами: 6 < ΔE ≤ 12 (37)

| файл | цвет | раз | ближайший токен | ΔE |
|---|---|---:|---|---:|
| `themes.css` | `#d9c9a6` | 6 | `--roll-num` `#c6b89c` | 7.1 |
| `themes.css` | `#e2e6dc` | 3 | `--text` `#ede3ce` | 7.7 |
| `themes.css` | `#202126` | 3 | `--sheet-bg` `#1e1a14` | 8.9 |
| `themes.css` | `#e5d2ad` | 2 | `--sheet-ink` `#e8ddc7` | 9.4 |
| `themes.css` | `#3a3f36` | 2 | `--ink-600` `#3a3128` | 8.5 |
| `themes.css` | `#1e2420` | 1 | `--sheet-bg-2` `#221d16` | 6.6 |
| `themes.css` | `#303b31` | 1 | `--ink-600` `#3a3128` | 10.0 |
| `themes.css` | `#9baa88` | 1 | `--moss` `#8fa97a` | 8.5 |
| `themes.css` | `#a3a094` | 1 | `--text-3` `#a2957b` | 9.7 |
| `themes.css` | `#a9a491` | 1 | `--sheet-note` `#b0a186` | 6.2 |
| `themes.css` | `#fff9ee` | 1 | `--text` `#ede3ce` | 9.4 |
| `themes.css` | `#fff3d22b` | 1 | `--text` `#ede3ce` | 8.1 |
| `themes.css` | `#fff3d226` | 1 | `--text` `#ede3ce` | 8.1 |
| `themes.css` | `#8d8676` | 1 | `--text-3` `#a2957b` | 8.5 |
| `obsidian.css` | `#bda57e` | 2 | `--sheet-note` `#b0a186` | 7.9 |
| `obsidian.css` | `#202126` | 2 | `--sheet-bg` `#1e1a14` | 8.9 |
| `obsidian.css` | `#e8d8b7` | 1 | `--sheet-ink` `#e8ddc7` | 6.3 |
| `obsidian.css` | `#e5d2ad` | 1 | `--sheet-ink` `#e8ddc7` | 9.4 |
| `obsidian.css` | `#665341` | 1 | `--line-strong` `#4a3e30` | 10.4 |
| `obsidian.css` | `#d5be98` | 1 | `--roll-num` `#c6b89c` | 7.0 |
| `obsidian.css` | `#503b2110` | 1 | `--line-strong` `#4a3e30` | 9.5 |
| `obsidian.css` | `#d9c9a6` | 1 | `--roll-num` `#c6b89c` | 7.1 |
| `obsidian.css` | `#20212612` | 1 | `--sheet-bg` `#1e1a14` | 8.9 |
| `obsidian.css` | `#202126aa` | 1 | `--sheet-bg` `#1e1a14` | 8.9 |
| `obsidian.css` | `#20212618` | 1 | `--sheet-bg` `#1e1a14` | 8.9 |
| `obsidian.css` | `#d7c9a6` | 1 | `--roll-num` `#c6b89c` | 6.9 |
| `style.css` | `#fff9ee` | 2 | `--text` `#ede3ce` | 9.4 |
| `style.css` | `#1e2420` | 1 | `--sheet-bg-2` `#221d16` | 6.6 |
| `style.css` | `#e2e6dc` | 1 | `--text` `#ede3ce` | 7.7 |
| `style.css` | `#303b31` | 1 | `--ink-600` `#3a3128` | 10.0 |
| `style.css` | `#9baa88` | 1 | `--moss` `#8fa97a` | 8.5 |
| `style.css` | `#232619` | 1 | `--ink-750` `#262019` | 6.4 |
| `style.css` | `#91b88a` | 1 | `--moss` `#8fa97a` | 6.7 |
| `style.css` | `#f6f0e5` | 1 | `--text` `#ede3ce` | 7.1 |
| `style.css` | `#3f4032` | 1 | `--line-strong` `#4a3e30` | 6.4 |
| `style.css` | `#d4cbbb` | 1 | `--sheet-ink` `#e8ddc7` | 7.1 |
| `game-screen.css` | `#5c4c39` | 2 | `--line-strong` `#4a3e30` | 7.2 |

## 6. Нужен новый токен или другое решение: ΔE > 12 (44)

Большинство — палитры obsidian/parchment (уходят вместе с файлами). Живые случаи:
цвета-чернила `#806845`, `#856a3c`, `#785d33` (старый текст-3 и линии) — им соответствуют
`--text-3` и `--line-strong`, но затемнение сильнее, чем в токенах: брать токен, а не новый
цвет. Если после удаления легаси-палитр такой цвет всё ещё нужен — это признак, что макет
не покрыт токенами: тогда новый токен добавляется **в `design-tokens.css` с комментарием,
откуда он и что означает**.

| файл | цвет | раз | ближайший токен | ΔE |
|---|---|---:|---|---:|
| `themes.css` | `#d1ba82` | 6 | `--candle-hi` `#edbb79` | 13.4 |
| `themes.css` | `#a3b0a6` | 4 | `--text-2` `#beb097` | 13.4 |
| `themes.css` | `#806845` | 3 | `--text-3` `#a2957b` | 18.8 |
| `themes.css` | `#856a3c` | 3 | `--candle-deep` `#b98a4e` | 18.1 |
| `themes.css` | `#785d33` | 2 | `--line-strong` `#4a3e30` | 22.8 |
| `themes.css` | `#8e9b91` | 1 | `--text-3` `#a2957b` | 13.8 |
| `themes.css` | `#a6b0b5` | 1 | `--text-2` `#beb097` | 18.7 |
| `themes.css` | `#ffffff0c` | 1 | `--text` `#ede3ce` | 14.9 |
| `themes.css` | `#7d7b68` | 1 | `--text-3` `#a2957b` | 12.3 |
| `themes.css` | `#7b878c` | 1 | `--text-3` `#a2957b` | 21.1 |
| `themes.css` | `#67624f` | 1 | `--line-strong` `#4a3e30` | 15.1 |
| `themes.css` | `#647176` | 1 | `--text-3` `#a2957b` | 25.5 |
| `themes.css` | `#8a7238` | 1 | `--candle-deep` `#b98a4e` | 15.2 |
| `themes.css` | `#7a6330` | 1 | `--candle-deep` `#b98a4e` | 20.7 |
| `obsidian.css` | `#92734a` | 3 | `--candle-deep` `#b98a4e` | 16.0 |
| `obsidian.css` | `#773e3c` | 2 | `--line-strong` `#4a3e30` | 22.7 |
| `obsidian.css` | `#70512f` | 1 | `--line-strong` `#4a3e30` | 18.4 |
| `obsidian.css` | `#806845` | 1 | `--text-3` `#a2957b` | 18.8 |
| `obsidian.css` | `#ffffff0c` | 1 | `--text` `#ede3ce` | 14.9 |
| `obsidian.css` | `#ffffff20` | 1 | `--text` `#ede3ce` | 14.9 |
| `obsidian.css` | `#ffffff25` | 1 | `--text` `#ede3ce` | 14.9 |
| `obsidian.css` | `#5c452010` | 1 | `--line-strong` `#4a3e30` | 15.9 |
| `obsidian.css` | `#b49d69` | 1 | `--candle-deep` `#b98a4e` | 13.9 |
| `obsidian.css` | `#9b8258` | 1 | `--text-3` `#a2957b` | 12.9 |
| `style.css` | `#f6f7f8` | 2 | `--text` `#ede3ce` | 13.8 |
| `style.css` | `#8e9b91` | 1 | `--text-3` `#a2957b` | 13.8 |
| `style.css` | `#d1ba82` | 1 | `--candle-hi` `#edbb79` | 13.4 |
| `style.css` | `#ba846f` | 1 | `--text-3` `#a2957b` | 18.2 |
| `style.css` | `#cb9b83` | 1 | `--sheet-note` `#b0a186` | 14.0 |
| `style.css` | `#7d7b68` | 1 | `--text-3` `#a2957b` | 12.3 |
| `style.css` | `#917242` | 1 | `--candle-deep` `#b98a4e` | 14.2 |
| `style.css` | `#6b7754` | 1 | `--text-3` `#a2957b` | 18.3 |
| `style.css` | `#57452330` | 1 | `--line-strong` `#4a3e30` | 13.1 |
| `style.css` | `#fff` | 1 | `--text` `#ede3ce` | 14.9 |
| `style.css` | `#eef0f2` | 1 | `--text` `#ede3ce` | 13.3 |
| `style.css` | `#243136` | 1 | `--ink-700` `#2e2720` | 12.7 |
| `style.css` | `#7b878c` | 1 | `--text-3` `#a2957b` | 21.1 |
| `style.css` | `#dde3e6` | 1 | `--text` `#ede3ce` | 13.7 |
| `style.css` | `#4a6b70` | 1 | `--line-strong` `#4a3e30` | 26.9 |
| `style.css` | `#e8eff0` | 1 | `--text` `#ede3ce` | 13.4 |
| `style.css` | `#577877` | 1 | `--text-3` `#a2957b` | 26.6 |
| `style.css` | `#20383f22` | 1 | `--ink-700` `#2e2720` | 16.9 |
| `prototype.css` | `#8c261b` | 2 | `--ember` `#d9704f` | 27.7 |
| `game-screen.css` | `#6b573c` | 1 | `--line-strong` `#4a3e30` | 14.0 |

## 7. Светлая тема

1. Значения брать из `design/screens/theme-light.css` (дизайнер сверил контрасты: 13.2 / 7.5 /
   5.9 / 5.2:1). Переписать их в `web/design-tokens.css` блоком `[data-theme="light"]`.
2. `:root` и `[data-theme="dark"]` — тёмная, остаётся как есть; светлая — только переопределение,
   ни одного нового имени токена.
3. Кнопка солнце/луна в шапке; состояние в `localStorage['solo-theme']` (тот же ключ, что у
   старого переключателя — иначе выбор потеряется у тех, кто уже играл); при первом заходе —
   `prefers-color-scheme`.
4. В `web/obsidian.js` убрать добавление пункта «Obsidian Chronicle» и миграцию старых тем —
   это единственное место в JS, которое карточке разрешено править.
5. Проверить, что переключение не сбрасывает состояние: кампания, ход, черновик в поле ввода.

## 8. Приёмка (числа, не слова)

```bash
# до и после, по каждому файлу
for f in web/*.css; do printf '%-24s %s\n' "$f" "$(grep -oE '#[0-9a-fA-F]{3,8}\b' $f | wc -l)"; done
# цель: 0 везде, кроме design-tokens.css
```

Ещё три проверки:

- `grep -c 'data-theme' web/*.css` — значения только `dark` и `light`.
- в `index.html` `themes.css` подключён один раз, а не дважды.
- браузер 375/1440: все экраны в обеих темах, прокрутки по горизонтали нет, консоль пустая.
