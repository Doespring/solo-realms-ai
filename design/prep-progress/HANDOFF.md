# r234 — HANDOFF: что поменять в `web/`, чтобы панель стала такой

Задача этого файла — чтобы кодер внедрил панель, **не разгадывая замысел**.
Здесь нет правок приложения: только перечень изменений, готовые фрагменты CSS
и JS и проверки, по которым видно, что всё сошлось.

Читать вместе с `SPEC.md` (числа, состояния, тексты) и `mock.html` (как это
выглядит живьём). Если фрагмент ниже и макет спорят — прав макет, а расхождение
считать ошибкой этого файла, а не поводом решать на месте.

**Трогаем четыре файла (плюс готовый образец):**

| Файл | Действие |
|---|---|
| `web/prepare-progress.css` | заменить целиком (42 строки → ~160) |
| `web/prepare-progress.js` | заменить целиком: готовый файл — `design/prep-progress/tools/app-panel.js` (сверен с макетом, см. §3) |
| `web/index.html` | поднять метку ассетов: `sims-4` → `sims-5` (две штуки, строки 108) |
| `web/creation.js` | одна строка в `drawProgress()` (строка 39): передавать `run` и на терминальном статусе — иначе сцены «готово» не существует (§3.7) |

`web/creation.css`, `#creation-progress` и разметку экрана создания **не
трогаем**: панель живёт в `#creation-progress-panel` и остаётся соседом строки
«Подготовка: 7 / 12 блоков».

---

## 1. `web/index.html` — одна строка

Сейчас (строка 108):

```html
<link rel="stylesheet" href="/static/creation.css?v=r223"><link rel="stylesheet" href="/static/prepare-progress.css?v=sims-4"><script src="/static/prepare-progress.js?v=sims-4" defer></script>
```

Стало:

```html
<link rel="stylesheet" href="/static/creation.css?v=r223"><link rel="stylesheet" href="/static/prepare-progress.css?v=sims-5"><script src="/static/prepare-progress.js?v=sims-5" defer></script>
```

Метка обязательна: без неё у владельца останется старый CSS из кэша и он
увидит новую разметку со старыми стилями (серая колодка вместо линейки).

`design-tokens.css` подключается ниже по документу (строка 119). Для
переменных это не важно — они разрешаются в момент отрисовки, — но **не
добавляй в `prepare-progress.css` запасных хексов** (`var(--text, #2a231a)`):
в тёмной теме такой fallback даст светлые чернила на тёмном листе. Сейчас в
файле такие fallback есть — при замене они уходят.

---

## 2. `web/prepare-progress.css` — заменить целиком

### 2.1 Что удаляется

| Селектор / правило | Почему |
|---|---|
| `.prep-shine` | бегущий блик — главный признак «прогресс-бара из библиотеки» |
| `@keyframes prep-shine` | вместе с бликом |
| `.prep-tick.is-done{background:var(--moss,#6f8f5a)}` | зелёная заливка в середине работы: успех объявляется только в конце |
| `.prep-bar{...border-radius:999px;...}` | линейка, а не «колодка»: радиус 0, кромка волосяная |
| `.prep-panel{...border:1px solid var(--line)...}` | кромка листа — `--sheet-line`, слева корешок 3 px |
| `@media (max-width:420px){...}` | заменяется мобильным набором по умолчанию + `min-width:720px` |
| все `var(--x, #hex)` | токены в приложении всегда определены; fallback врёт в тёмной теме |

Старые `.prep-steps`, `.prep-step`, `.prep-arrow` не удаляются — они
переезжают в новый блок с другими цветами (см. ниже).

### 2.2 Что вставляется

Ниже — готовый текст файла. Он совпадает с блоком «ПАНЕЛЬ» в
`design/prep-progress/mock.css` один к одному, кроме двух намеренных отличий:

1. `.prep-panel` получает внешний отступ сверху `var(--s4)`: в приложении
   панель стоит под строкой «Подготовка: 7 / 12 блоков», в макете — сама по
   себе;
2. нет копии токенов и `@font-face` — их даёт `design-tokens.css`.

```css
/* r234 — полоса подготовки книги в языке «Стол и лист».
   Лист на столе: кромка 1 px, слева корешок 3 px. Корешок — единственный
   цветовой сигнал состояния: свеча (идёт), зелень (можно играть/готово),
   медь (готово со сбоем), линия (остановлено).
   Ноль градиентов, теней, размытий и полупрозрачных подложек; один радиус
   2 px; шкала отступов 4 px (2 и 6 px — только внутри пары). */

.prep-panel{
  display:grid;grid-template-columns:minmax(0,1fr);gap:0;
  width:100%;max-width:var(--measure);
  margin:var(--s4) 0 0;
  padding:var(--s4) var(--s4) var(--s4) calc(var(--s4) - 2px);
  background:var(--sheet-bg);
  border:1px solid var(--sheet-line);
  border-left:3px solid var(--candle);
  border-radius:var(--radius);
  color:var(--text);
  font-family:var(--font-ui);
}

/* ── Шапка: надзаголовок и часы, под ними — что делаю сейчас ───────────── */
.prep-head{
  display:grid;grid-template-columns:minmax(0,1fr) auto;
  align-items:baseline;column-gap:var(--s3);
}
.prep-eyebrow{
  margin:0;
  font:500 14px/1.3 var(--font-ui);
  letter-spacing:.12em;text-transform:uppercase;
  color:var(--text-3);
}
.prep-clock{
  margin:0;justify-self:end;
  font:500 14px/1.2 var(--font-ui);
  color:var(--text-3);font-variant-numeric:tabular-nums;
  white-space:nowrap;
}
.prep-title{
  grid-column:1 / -1;margin:6px 0 0;
  font:400 20px/1.2 var(--font-book);
  color:var(--text);
  letter-spacing:.002em;
  overflow-wrap:anywhere;
}

/* ── Путь: чтение → разбор → сборка ────────────────────────────────────── */
.prep-steps{
  display:flex;align-items:baseline;flex-wrap:wrap;
  gap:var(--s1) var(--s2);
  margin:var(--s2) 0 0;padding:0;
  font:500 14px/1.35 var(--font-ui);
  color:var(--text-3);
}
.prep-step{padding-bottom:2px;border-bottom:1px solid transparent}
.prep-step.is-done{color:var(--text-2)}
.prep-step.is-current{color:var(--candle);border-bottom-color:var(--candle)}
.prep-arrow{
  color:var(--line-strong);
  font:400 14px/1 var(--font-ui);
  transform:translateY(-1px);
}

/* ── Линейка: 1 + 10 + 1 px между двумя волосяными линиями. Пустая часть —
   это лист, а не серая колодка. На переднем крае — закладка 2×16 px. ──── */
.prep-bar{
  position:relative;height:12px;margin:var(--s3) 0 0;
  border-top:1px solid var(--line-strong);
  border-bottom:1px solid var(--line-strong);
  overflow:visible;
}
.prep-fill{
  position:absolute;left:0;top:0;bottom:0;width:0;
  background:var(--candle);border-radius:0;
  transition:width .4s ease-out;
}
.prep-fill::after{
  content:"";position:absolute;right:-1px;top:-3px;
  width:2px;height:16px;background:var(--candle);
}
.prep-panel[data-empty] .prep-fill::after{content:none}

/* ── Засечки блоков: стежки 4 px по нижнему краю листа ─────────────────── */
.prep-ticks{display:flex;gap:2px;margin:var(--s2) 0 0;height:4px}
.prep-tick{
  flex:1 1 0;min-width:2px;height:4px;box-sizing:border-box;
  background:var(--line-strong);border-radius:0;
  transition:background-color .3s ease;
}
.prep-tick.is-done{background:var(--candle)}
/* «Сейчас» — не залитый стежок, а намеченный: контур 1 px. Так готовое и
   текущее различаются в статике и при reduced-motion, а не только дыханием. */
.prep-tick.is-active{background:transparent;border:1px solid var(--candle);animation:prep-flame 2.4s ease-in-out infinite}
.prep-tick.is-failed{background:var(--ember)}
@keyframes prep-flame{50%{opacity:.45}}

/* ── Фраза: заметка на полях, курсив книги. Поле на две строки зарезервировано
   заранее — смена фразы не двигает панель. ─────────────────────────────── */
.prep-caption{
  margin:var(--s4) 0 0;padding:0;
  min-height:calc(2 * 1.45em);
  font:italic 400 16px/1.45 var(--font-book);
  color:var(--text-2);
  overflow-wrap:anywhere;
  opacity:0;transform:translateY(2px);
  transition:opacity .35s ease,transform .35s ease;
}
.prep-caption.is-in{opacity:1;transform:none}

/* ── Числа: часть, остаток. Каждый кусок не рвётся внутри себя. ─────────── */
.prep-meta{
  display:flex;flex-wrap:wrap;gap:2px var(--s2);
  margin:6px 0 0;padding:0;
  font:400 14px/1.45 var(--font-ui);
  color:var(--text-3);font-variant-numeric:tabular-nums;
}
.prep-meta span{white-space:nowrap}
.prep-meta .sep{color:var(--line-strong)}

/* ── Сбой: медная плашка с тёмными чернилами — как вердикт промаха броска. ─ */
.prep-alert{
  justify-self:start;max-width:100%;
  margin:var(--s3) 0 0;padding:var(--s1) var(--s2);
  background:var(--out-miss);color:var(--out-miss-ink);
  border-radius:var(--radius);
  font:700 14px/1.35 var(--font-ui);
  text-wrap:balance;   /* на 375 две строки делятся пополам, а не «…идут / дальше.» */
}
.prep-alert[hidden]{display:none}

/* ── Состояния ─────────────────────────────────────────────────────────── */
.prep-panel.is-stopped{border-left-color:var(--line-strong)}
.prep-panel.is-stopped .prep-tick.is-active{animation:none;opacity:1}

/* Глава 1 собрана — играть можно, книга ещё читается. */
.prep-panel.is-playable{border-left-color:var(--moss)}
.prep-panel.is-playable .prep-meta{color:var(--text-2)}

/* Готово: панель один раз меняет высоту — убирает строку фразы. */
.prep-panel.is-finished{border-left-color:var(--moss)}
.prep-panel.is-finished .prep-title{color:var(--moss)}
.prep-panel.is-finished .prep-caption{display:none}
.prep-panel.is-finished .prep-meta{color:var(--text-2)}

/* Медь корешка, надзаголовка и заголовка — вердикт КОНЦА. Поэтому оба класса:
   is-failed можно ставить всегда, ничего не покраснеет, пока прогон жив. */
.prep-panel.is-finished.is-failed{border-left-color:var(--ember)}
.prep-panel.is-finished.is-failed .prep-title{color:var(--ember)}
.prep-panel.is-finished.is-failed .prep-eyebrow{color:var(--ember)}

/* ── Стол шире: тот же лист, крупнее кегли, воздух 24 ──────────────────── */
@media (min-width:720px){
  .prep-panel{padding:var(--s5) var(--s5) var(--s5) calc(var(--s5) - 2px)}
  .prep-title{font-size:22px}
  .prep-caption{font-size:17px;min-height:1.45em}
}

/* ── Сдержанное движение: анимируются только заливка, засечки и приход
   фразы. При reduce всё стоит, геометрия не меняется. ─────────────────── */
@media (prefers-reduced-motion:reduce){
  .prep-fill{transition:none}
  .prep-tick{transition:none}
  .prep-tick.is-active{animation:none;opacity:1}
  .prep-caption{transition:none;opacity:1;transform:none}
}
```

Пояснения к спорным местам:

- **`max-width:var(--measure)`** (660 px) — панель не растягивается на всю
  ширину стола. Строка чисел и фраза должны оставаться в читаемой длине.
- **`padding-left` на 2 px меньше правого**: 3 px корешка + 14 = 1 px кромки +
  16, поэтому текст стоит на одном расстоянии от обеих кромок.
- **`overflow:visible` у `.prep-bar`**: закладка выступает на 3 px выше и ниже
  линейки. Если поставить `hidden` (как было), закладка обрежется и останется
  просто полоса.
- **`min-height:calc(2 * 1.45em)`** у фразы: данные приходят раз в 2–4 с, и
  без резерва панель дёргалась бы на каждой смене фразы. На широком столе
  поле в одну строку — и это единственная разница в геометрии между 375 и 1440.

---

## 3. `web/prepare-progress.js` — заменить целиком

Готовая новая версия файла лежит рядом: **`design/prep-progress/tools/app-panel.js`**.
Это не набросок, а тот самый файл, который прогнан через `tools/parity.py`
против макета: 9 сцен × 2 ширины × 2 темы, 36 сверок — высота листа, классы
состояний, заголовок, надзаголовок, часы, шаги, заливка, число и состояние
засечек, текст и видимость плашки, цвет корешка. Если правишь по месту —
ниже те же куски дословно; если копируешь файл — сверься с §5.

Что меняется по смыслу:

| Было | Стало |
|---|---|
| `head` = заголовок + часы | `head` = надзаголовок «Подготовка книги» + часы, под ними заголовок фазы |
| у заливки ребёнок `.prep-shine` | блика нет |
| засечка = блок с цифрой, `font-size:0` | засечка без текста, до 48 штук, дальше — группа блоков |
| `is-done` зелёный сразу | `is-done` свечой; зелень — только вердикт конца |
| `meta` — один текстовый узел | `meta` — куски с разделителями, узел не переписывается без нужды |
| плашки сбоя нет | `.prep-alert` — медная плашка, появляется один раз |
| `aria-live` у фразы `polite` | у фразы `off`, у чисел `polite` |

### 3.1 Новые константы и помощники (рядом с `FINISH_MS` / `fmt`)

```js
/* Засечек не больше 48: при 120 блоках одна засечка — три блока. Иначе ряд
   становится сплошной линией и перестаёт что-либо значить. */
const MAX_TICKS=48;
const plural=(n,one,few,many)=>{const d=n%10,h=n%100;if(d===1&&h!==11)return one;if(d>=2&&d<=4&&(h<10||h>=20))return few;return many};
/* Числа в панели везде цифрами: «2 блока не разобрались», «12 частей из 12».
   Слово согласуется с числом, чтобы строка не звучала как машинный перевод. */
const alertLine=(failed,over)=>{
 const verb=failed%10===1&&failed%100!==11?'не разобрался':'не разобрались';
 const head=failed+' '+plural(failed,'блок','блока','блоков')+' '+verb;
 return head+(over?'. Остальные части собраны.':' — остальные идут дальше.');
};
/* Числа собираются узлами: каждый кусок не рвётся внутри себя, перенос —
   только между кусками. Одинаковый текст не переписываем: иначе aria-live
   читает одно и то же на каждом опросе. */
function setMeta(node,parts){
 const text=parts.join(' · ');
 if(node.dataset.text===text)return;
 node.dataset.text=text;
 node.replaceChildren(...parts.flatMap((part,index)=>index?[el('span','·','sep'),el('span',part)]:[el('span',part)]));
}
```

### 3.2 `build()` — новая разметка

```js
function build(host){
 const root=el('div',undefined,'prep-panel');root.setAttribute('role','group');root.setAttribute('aria-label','Ход подготовки приключения');
 const head=el('div',undefined,'prep-head');
 const eyebrow=el('p','Подготовка книги','prep-eyebrow');
 const clock=el('p','0:00','prep-clock');
 const title=el('p','Подготовка','prep-title');
 head.append(eyebrow,clock,title);
 const steps=el('p',undefined,'prep-steps');steps.setAttribute('aria-label','Шаги подготовки');
 PHASE_ORDER.forEach((phase,index)=>{
  if(index)steps.append(Object.assign(el('span','→','prep-arrow'),{'aria-hidden':'true'}));
  const step=el('span',PHASE_STEP[phase],'prep-step');step.dataset.phase=phase;steps.append(step);
 });
 const bar=el('div',undefined,'prep-bar');bar.setAttribute('role','progressbar');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');
 const fill=el('div',undefined,'prep-fill');bar.append(fill);
 const ticks=el('div',undefined,'prep-ticks');ticks.setAttribute('aria-hidden','true');
 /* Фраза меняется каждые 2,6 с — в очередь чтеца она не идёт. */
 const caption=el('p','','prep-caption');caption.setAttribute('aria-live','off');
 const meta=el('p','','prep-meta');meta.setAttribute('aria-live','polite');
 const alert=el('p','','prep-alert');alert.hidden=true;
 root.append(head,steps,bar,ticks,caption,meta,alert);host.append(root);
 const state={host,root,title,clock,steps,bar,fill,ticks,caption,meta,alert,started:Date.now(),phase:'',tick:0,timer:null,rotor:null,exit:null,finished:false,lastDone:-1};
 return state;
}
```

Три вещи, которые легко пропустить:

- **`.prep-shine` удаляется вместе с узлом.** Не «оставить скрытым»: любой
  оставшийся блик вернёт ощущение библиотечного прогресс-бара.
- **В засечках больше нет цифр.** Группировка (см. 3.3) делает их
  безымянными; текст внутри ячейки не нужен, `font-size:0` тоже.
- **`alert` — новый узел**, в старом JS его не было. Он живёт в конце листа и
  появляется один раз.

### 3.3 `paint()` — состояния и засечки

```js
function paint(state,run){
 const progress=run.progress||{},phase=PHASE_ORDER.includes(progress.phase)?progress.phase:'extracting';
 const done=Math.max(0,Number(progress.done??run.done??0)),total=Math.max(0,Number(progress.total??run.total??0));
 const failed=Math.max(0,Number(progress.failed||0));
 /* Готовые и упавшие блоки — разные засечки. Упавшая засечка красится
    красным сразу, даже когда готовых ещё нет: раньше она была невидима, пока
    `done` не перевалит за неё, а потом красной становилась ГОТОВАЯ. */
 const processed=Math.min(total,done+failed);
 const running=run.status==='running'||run.status==='queued';
 state.phase=phase;
 state.title.textContent=PHASE_TITLE[phase]||'Разбираю книгу';
 const percent=total?Math.min(100,Math.round(done/total*100)):0;
 state.fill.style.width=percent+'%';
 state.root.toggleAttribute('data-empty',percent===0);
 state.bar.setAttribute('aria-valuenow',String(percent));
 state.bar.setAttribute('aria-valuetext',done+' из '+total+' частей');
 const current=PHASE_ORDER.indexOf(phase);
 Array.from(state.steps.querySelectorAll('.prep-step')).forEach((step,index)=>{
  step.classList.toggle('is-done',index<current||(index===current&&!running));
  step.classList.toggle('is-current',index===current&&running);
  if(index===current)step.setAttribute('aria-current','step');else step.removeAttribute('aria-current');
 });
 const unit=total>MAX_TICKS?Math.ceil(total/MAX_TICKS):1,cells=total?Math.ceil(total/unit):0;
 if(state.ticks.childElementCount!==cells){
  state.ticks.replaceChildren(...Array.from({length:cells},()=>el('span',undefined,'prep-tick')));
 }
 Array.from(state.ticks.children).forEach((cell,i)=>{
  const from=i*unit,to=Math.min(total,from+unit);
  /* Порядок: сбой → готово → сейчас. Упавшая засечка не бывает готовой. */
  const broken=Math.min(to,processed)>Math.max(from,done);
  cell.classList.toggle('is-failed',broken);
  cell.classList.toggle('is-done',!broken&&to<=done);
  cell.classList.toggle('is-active',!broken&&running&&from<=processed&&processed<to);
 });
 const bits=[];
 if(total)bits.push('Часть '+Math.min(processed+1,total)+' из '+total);
 if(progress.eta_s!==null&&progress.eta_s!==undefined&&done>0&&processed<total)bits.push('осталось ≈'+fmt(progress.eta_s));
 if(failed)bits.push('не вышло: '+failed);
 setMeta(state.meta,bits);
 state.alert.textContent=failed?alertLine(failed,false):'';
 state.alert.hidden=!failed;
 /* r237: глава 1 собрана — играть уже можно, а книга ещё читается. Пишем это
    словами и не прячем полосу: владелец видит и вход, и остаток работы. */
 if(Boolean(run.playable)&&running){
  state.title.textContent='Можно играть';
  setMeta(state.meta,['готово '+done+' из '+total+', остальное готовится']);
  state.root.classList.add('is-playable');
 }else state.root.classList.remove('is-playable');
 /* is-failed ставится всегда, когда есть сбои: CSS окрашивает медь только у
    `is-finished.is-failed`, поэтому во время работы корешок остаётся свечным. */
 state.root.classList.toggle('is-failed',Boolean(failed));
 state.root.classList.toggle('is-stopped',!running);
 if(!state.rotor&&running)startRotor(state);
 if(!running)stopRotor(state);
}
```

**Порядок вычисления засечки — часть исправления, а не стиль.** Сначала
`failed`, потом `done`, потом `active`. Если считать `done` первым, упавшая
засечка сначала красится свечой (как готовая), а красной становится соседняя —
именно этот баг был в болванке.

**`is-failed` ставится всегда, когда есть `failed`.** В CSS он окрашивает
только `is-finished.is-failed`, поэтому во время работы корешок остаётся
свечным: медь — это вердикт конца, а не «ой, что-то пошло не так» на каждом
опросе.

**«Сейчас» — контур, а не заливка.** Готовая засечка залита свечой, текущая
обведена 1 px (`border:1px solid var(--candle)` при `box-sizing:border-box`).
Это не украшение: данные приходят раз в 2–4 с, а при `prefers-reduced-motion`
дыхание выключено — без контура готовое и текущее выглядели бы одинаково.

### 3.4 `finish()` — правда в числах и словах

```js
function finish(state,run){
 state.finished=true;
 paint(state,run);
 const progress=run.progress||{};
 const done=Math.max(0,Number(progress.done??run.done??0)),total=Math.max(0,Number(progress.total??run.total??0));
 const failed=Math.max(0,Number(progress.failed||0));
 state.title.textContent=failed?'Часть блоков не вышла':(total&&done>=total?'Части собраны':'Подготовка остановлена');
 const parts=[];
 if(total)parts.push(done+' '+plural(done,'часть','части','частей')+' из '+total);
 if(failed)parts.push('не вышло: '+failed);
 setMeta(state.meta,parts);
 state.alert.textContent=failed?alertLine(failed,true):'';
 state.alert.hidden=!failed;
 state.root.classList.toggle('is-failed',Boolean(failed));
 state.caption.textContent='';state.caption.classList.remove('is-in');
 state.root.classList.add('is-finished');state.root.classList.remove('is-stopped');
 if(state.timer)clearInterval(state.timer);state.timer=null;
 state.exit=setTimeout(()=>dispose(state),FINISH_MS);
}
```

Что изменилось против текущего кода: после `paint()` числа и плашка
переписываются на «итоговые» (`12 частей из 12`, а не `Часть 13 из 12`),
время в строке чисел не дублируется — оно уже в часах шапки, — а высоту на
«готово» панель меняет **один раз**: строку фразы убирает CSS
(`.is-finished .prep-caption{display:none}`), а не JS.

### 3.5 `soloPrepPanel()` — одна строка, без которой панель опаздывает

В текущем коде:

```js
if(!state){panels.set(host,build(host));startClock(state,run)}
```

`panels.set()` возвращает сам `Map`, а не новое состояние, поэтому `state`
здесь всё ещё `undefined` — первый же `startClock(state,run)` падает
(`state.timer`), и панель появляется только со второго опроса. В новой версии:

```js
 let state=panels.get(host);const running=Boolean(run&&['queued','running'].includes(run.status));
 ...
 /* r234: panels.set() возвращает Map, а не состояние — раньше первый вызов
    терял state и падал в startClock(); панель появлялась только со второго
    опроса. Состояние строим в переменную. */
 if(!state){state=build(host);panels.set(host,state);startClock(state,run)}
 else if(state.finished){state.finished=false;state.root.classList.remove('is-finished','is-failed');if(state.exit){clearTimeout(state.exit);state.exit=null}}
 paint(state,run);
```

Снятие `is-failed` при повторном запуске обязательно: иначе после прогона со
сбоем новая панель сразу получила бы медный корешок.

### 3.6 Что осталось нетронутым и почему

| Узел / поведение | Решение |
|---|---|
| `PHASE_TITLE`, `PHASE_STEP`, `FLAVOR`, `AMBIENT`, `PHASE_ORDER` | тексты не меняются: панель говорит те же слова, другим голосом набранные |
| `startRotor` / `stopRotor` | фраза меняется раз в 2,6 с; при `reduce` стоит первая строка фазы |
| `startClock` | часы идут сами, `paint` их не переписывает |
| `FINISH_MS = 6000` | панель исчезает через 6 с после конца — «3 из 3» успевают увидеть |
| `dispose` / `panels` (WeakMap) | без изменений |
| `aria-live` у фразы | было `polite` → стало `off`: чтец не должен читать «Перелистываю страницы…» каждые 2,6 с. Сообщение об изменении — строка чисел (`polite`) |
| `run.playable` (r237) | сохраняется: заголовок «Можно играть», мета «готово 8 из 12, остальное готовится», корешок зеленеет |

### 3.7 «Готово» сейчас недостижимо — одна строка в `creation.js`

Это единственное место, где задача выходит за три файла, и без него сцена
«готово» останется только в макете.

`web/creation.js:39` зовёт панель так:

```js
window.soloPrepPanel(progressPanel,['queued','running'].includes(run?.status)?run:null);
```

Как только прогон получает терминальный статус (`ready`, `blocked`,
`cancelled`), в панель приходит `null` → `dispose()` — лист исчезает сразу,
`finish()` не вызывается никогда. Чтобы владелец увидел «Части собраны /
12 частей из 12» и панель ушла через 6 с, нужно передавать `run` и на
терминальном статусе:

```js
/* Конец подготовки: отдаём последний ответ, чтобы панель показала итог и
   ушла сама через FINISH_MS. Прогона нет вовсе — run пустой, панель уберётся. */
window.soloPrepPanel(progressPanel,run);
```

`soloPrepPanel` к этому готов: при не-running прогоне он вызывает `finish()`
один раз, ставит `state.finished` и больше не перерисовывает панель; при
`run === null` (прогона нет) по-прежнему просто убирает её. Ничего другого в
`creation.js` менять не нужно.

Если решено оставить `creation.js` как есть — вычеркните сцены `done` и
`done-failed` из приёмки: в живом приложении их не будет, панель будет
исчезать на последнем опросе. Тогда и `finish()` в `prepare-progress.js`
можно не переносить (но макет и SPEC его описывают — расхождение придётся
принять сознательно).

## 4. Дословные тексты (сверять с `SPEC.md` §6)

| Где | Текст |
|---|---|
| надзаголовок | `Подготовка книги` |
| фазы | `Читаю книгу` · `Разбираю книгу на части` · `Свожу части вместе` |
| конец | `Части собраны` · `Часть блоков не вышла` · `Подготовка остановлена` |
| играть | `Можно играть` |
| числа при работе | `Часть N из M` · `осталось ≈M:SS` · `не вышло: K` |
| числа в конце | `N частей из M` и, если были сбои, `не вышло: K` |
| плашка при работе | `K блок(а/ов) не разобрал(ся/ись) — остальные идут дальше.` |
| плашка в конце | `K блок(а/ов) не разобрал(ся/ись). Остальные части собраны.` |

Числа — цифрами. Склонения даёт `plural()`, руками формы не писать.

---

## 5. Проверка после внедрения

Порядок такой, чтобы не ловить по одному:

1. **Разметка.** В DevTools на экране создания во время подготовки:
   `.prep-panel > .prep-head(error/clock/title) → .prep-steps → .prep-bar >
   .prep-fill → .prep-ticks → .prep-caption → .prep-meta → .prep-alert`.
   В `.prep-fill` **нет** `.prep-shine`; в `.prep-tick` нет текста.
2. **Ноль.** Сцена «только начали»: заливка 0 %, у заливки нет закладки
   (`[data-empty]`), первая засечка обведена контуром (и дышит, если движение
   не выключено), строки «осталось» нет вовсе (не «≈0:00»).
3. **Сбой.** Прокрутить книгу с заведомо битым блоком: медные засечки на
   месте упавших, плашка видна, корешок **свечной** (медь только в конце).
4. **Середина.** Готовые засечки залиты, текущая — контуром: видно и без
   движения, что позади, а что сейчас.
5. **Готово.** `is-finished`: заголовок и корешок зелёные (или медные, если
   были сбои), строка фразы убрана, числа «12 частей из 12», время не
   продублировано, панель уходит через 6 с. Для этого — правка §3.7 в
   `creation.js`; без неё шаг не проверить.
6. **Кегли и контраст.** Всё видимое — не мельче 14 px. Быстрая проверка в
   консоли:
   ```js
   [...document.querySelectorAll('.prep-panel *')].filter(n=>n.offsetParent)
     .map(n=>[n.className,parseFloat(getComputedStyle(n).fontSize)])
     .filter(([,s])=>s<14)
   ```
   Пустой массив — норма. Контраст: `--text-3` на `--sheet-bg` 5.87 / 6.34,
   плашка 5.84 / 5.11 — ниже 4.5 не опускается ничего.
7. **375 px.** Ни одного переноса «по буквам», ни горизонтальной прокрутки:
   `document.documentElement.scrollWidth === innerWidth`. Строка чисел может
   переноситься **между** кусками (`Часть 8 из 12` / `осталось ≈2:10`), внутри
   куска — нет (`white-space:nowrap`).
8. **Уменьшенное движение.** DevTools → Rendering → `prefers-reduced-motion:
   reduce`: заливка и засечки без переходов, фраза видна сразу, высота панели
   та же, что без reduce.
9. **Кэш.** Метка `sims-5` в `index.html`; после перезагрузки в Sources
   видна новая версия `prepare-progress.css`.

Сверка с макетом: `design/prep-progress/mock.html` (витрина 2×2) и
`shots/prep-*.png`. Если в приложении что-то выглядит иначе — сравнивать
числа из `SPEC.md` §3.1, а не «на глаз».

Если сомневаешься, что перенёс JS один в один — прогони сверку из папки
макета:

```
/home/hermes/.venvs/design/bin/python tools/parity.py
```

Она поднимает `tools/parity.html` (там крутится `tools/app-panel.js`) и
сравнивает с макетом по 9 сценам × 2 ширинам × 2 темам: высота листа, классы,
заголовок, шаги, заливка, засечки, числа, плашка, цвет корешка. Вывод
«сверка: OK» — значит код и макет — одна и та же панель. Если правил файл
руками, скопируй его в `tools/app-panel.js` и прогони снова: расхождение
означает, что ты внедрил не то, что нарисовано.

---

## 6. Чего не делать

- Не переносить в приложение копию токенов и `@font-face` из `mock.css` —
  они там только чтобы макет открывался с диска.
- Не добавлять картинки, SVG, иконки, тени, градиенты и полупрозрачные
  подложки: панель должна весить копейки и работать офлайн.
- Не менять `#creation-progress` («Подготовка: 7 / 12 блоков») и не убирать
  `#creation-progress-panel`: эта задача — только вид панели.
- Не «улучшать» вёрстку экрана создания по ходу: правка панели не разрешение
  на редизайн соседей.
- Не переносить числа из макета в код: единственный источник правды —
  `run.progress`.
