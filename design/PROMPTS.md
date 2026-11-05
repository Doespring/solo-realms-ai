# Промпты для иллюстраций

Всё, что ниже, генерирует владелец по подписке (Google Flow / Gemini / GPT).
Я картинки не генерирую и в макеты не вставляю: макеты работают на CSS
и на готовых иконках, ассеты подставляются поверх.

Общие правила для всех промптов:

1. **Текст не просим никогда.** Любая кириллица от генератора — брак, который
   не исправить. В каждом промпте стоит `no text, no letters, no words, no
   typography`, а русский текст потом ставится набором в макете.
2. **Оставляем воздух** там, где по композиции будет текст (указано в каждом блоке).
3. **Ничего из чужой вселенной:** ни логотипов, ни дракона-амперсанда, ни
   узнаваемых персонажей, ни фирменных монстров. Стиль — «настольное фэнтези
   вообще», а не конкретная игра.
4. Разрешение берём с запасом ×2 от показа на экране, соотношение сторон — как
   в блоке. Формат: PNG для всего, что с прозрачностью/рамкой, WebP q85 — для фонов.

---

## 1. Фон первого экрана

**Файл:** `assets/img/start-bg.webp` · **2880 × 1920 (3:2)** · минимум 2× от показа
**Где:** `01-start.html`, подложка всей страницы, непрозрачность 18–24 %,
поверх — градиентная виньетка и роза ветров.

```
Moody still life of a solitary tabletop session at night, seen from slightly above.
A dark oak table fills the frame; at the right edge a candle burns low, its warm
amber light raking across a half-unrolled hand-drawn map and a scattering of
pencil marks. Deep umber and near-black shadows, muted olive and dusty parchment
tones, one warm amber light source, heavy soft shadows, fine film grain, painterly
digital illustration in the manner of a 19th-century etching tinted by hand.
No people, no faces, no creatures. The left half of the image stays dark, quiet and
almost empty — it is a background for a title. Main motif in the right third.
Shallow depth of field, nothing in sharp focus.
no text, no letters, no words, no typography, no logos, no symbols, no dice,
no game pieces, no UI, no frames, no watermark, no signature
```

Негатив отдельно, если генератор умеет: `text, letters, watermark, logo, dice, d20,
dragon, character art, cartoon, neon, saturated colours, sharp focus`.

## 2. Текстура пергамента (лист героя и разбор книги)

**Файл:** `assets/img/paper.webp` · **2048 × 2048**, бесшовная по краям
**Где:** `02-character.html` (лист героя), `05-book.html` (лист разбора) —
фон `.sheet` вместо текущего градиента, режим `multiply`, непрозрачность 45 %.

```
Seamless texture of aged unbleached paper, warm ivory base colour close to
#EFE2C6, visible cotton fibres, a few faint foxing spots and one soft water stain
near the edge, extremely subtle horizontal laid lines, no folds, no tears,
no burnt edges, flat even lighting, scanned at high resolution, perfectly
tileable, no vignette.
no text, no letters, no handwriting, no ink marks, no drawings, no borders,
no logos, no watermark
```

Проверка: размножить 2×2 и посмотреть стыки. Если видна сетка — перегенерировать,
а не размывать.

## 3. Рамка листа

**Файл:** `assets/img/sheet-frame.png` · **2480 × 3508 (A4, 300 ppi)**
**Где:** поверх `.sheet` в 02 и 05 (`.sheet::after`), тонкая линия цвета
`#8A6A3E` с непрозрачностью 25 %, отступ 14 px от края.

```
A single thin ornamental border for a printed page, drawn in sepia ink
(#6B4A2A) on plain warm ivory paper (#EFE2C6). The border sits 3 cm from the page
edge, 2–3 pt thick, with small restrained corner ornaments made of two laurel
leaves and a dot — botanical geometry, no heraldry, no shields, no crowns, no
animals. Straight even rules on all four sides, identical corners, generous empty
margin outside the border, nothing inside the border at all.
no text, no letters, no numbers, no monograms, no crests, no logos, no illustrations,
no drop shadows, no paper texture, no vignette
```

Резать по внешнему краю в PNG с прозрачностью; если генератор не даёт альфу —
вырезать фон в редакторе по цвету `#EFE2C6`.

## 4. Стилистика иконок (якорь для новых)

**Файл:** `assets/img/icons-style.png` · **1024 × 1024**, одна иконка на кадр
**Где:** новые иконки, которых нет в наборе game-icons (сейчас используются
31 готовая, см. `ASSETS.md`). Стиль должен совпасть с ними: силуэт, без заливки.

```
One single flat pictogram, centred on a square ivory background (#EFE2C6),
drawn in solid near-black ink (#2A231A) with an even 2 px stroke at 512 px,
rounded joins, slightly irregular hand-cut silhouette in the manner of old
woodcut icons, no shading, no gradient, no perspective, no outline glow,
generous padding, icon occupies 70 % of the canvas. Subject: [ПРЕДМЕТ].
no text, no letters, no numbers, no colour, no background detail, no frame,
no shadow, no logo
```

Подставлять вместо `[ПРЕДМЕТ]` по одному: `a coil of rope`, `a cracked clay jug`,
`a hunting knife`, `a lantern`, `a folded cloak`, `a raven feather`.
Готовые SVG из game-icons остаются основными — этот промпт только для добора.

## 5. Медальоны-фишки для боя (по желанию)

**Файл:** `assets/img/token-<имя>.png` · **1024 × 1024**, круг в центре
**Где:** `04-combat.html`, вместо букв «М / Р / С» на клетках. Пока макет
работает на буквах — это честный запасной вариант.

```
A circular portrait medallion for a tabletop token: shoulders-and-head bust of an
unnamed medieval traveller in a hood, seen in three-quarter view, drawn as a sepia
ink sketch with hatching on flat ivory paper, warm limited palette (umber, ochre,
muted olive), no frame, the subject cropped by an invisible circle, background
flat ivory. Neutral expression, no weapons raised, nothing heroic.
no text, no letters, no names, no logos, no armour emblems, no fantasy trademarks,
no bright colours, no 3D render
```

## 6. Что делать, если генератор всё-таки нарисовал буквы

Не замазывать и не «дочищать»: перегенерировать с тем же промптом и другим seed.
Буквы в кадре — это всегда заметно. Текст в этом проекте ставится только набором.
