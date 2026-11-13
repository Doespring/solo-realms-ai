// N2: шапка игры и док — живые числа из DOM, без подменённых ответов.
// argv: <base> <campaignId> <tempDir>
const {chromium} = require(process.env.SOLO_UI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [base, cid, temp] = process.argv.slice(2);
const out = process.env.SOLO_UI_EVIDENCE || temp;
fs.mkdirSync(out, {recursive: true});

const NAMES = ['Тайна трёх сосен', 'Соляной тракт', 'Дом у маяка'];
const TITLE_MIN = 100;      // приёмка: название шире 100 px
const INPUT_GAP = 100;      // приёмка: зазор «текст → поле» ≤ 100 px
const SIDE = 16;            // приёмка: поля композера 16 px
const KB_GAP = 8;           // макет `game-kb`: 8 px до клавиатуры

async function openGame(browser, width, height, kb) {
  const context = await browser.newContext({viewport: {width, height}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await context.route('**/*', route =>
    new URL(route.request().url()).origin === base ? route.continue() : route.abort());
  await page.addInitScript(id => localStorage.setItem('solo-campaign', id), cid);
  // `load` на этой странице не наступает вовсе (висит один из ресурсов):
  // ждём `domcontentloaded` и дальше — по конкретной функции экрана.
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  await page.waitForFunction(() => Boolean(window.soloHeader), null, {timeout: 30000});
  await page.evaluate(() => { view('play'); });
  await page.waitForFunction(() => Boolean(document.querySelector('#play.view.active')), null,
    {timeout: 20000});
  if (kb) {
    await page.evaluate(() => document.body.classList.add('kb-open'));
    await page.waitForTimeout(300);
  }
  return {context, page, errors};
}

(async () => {
  const browser = await chromium.launch(process.env.SOLO_UI_BROWSER
    ? {executablePath: process.env.SOLO_UI_BROWSER, headless: true,
       args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']}
    : {channel: 'msedge', headless: true});
  const checks = {};
  try {
    // ── 375 без клавиатуры ──────────────────────────────────────────────────
    const small = await openGame(browser, 375, 812, false);
    const titles = await small.page.evaluate(names => {
      const label = document.querySelector('#campaign-label');
      return names.map(name => {
        if (label) label.textContent = name;
        window.soloHeader.syncTitle();
        const line = document.querySelector('body > main > header .m-title > span');
        const sub = document.querySelector('body > main > header .m-sub');
        return {
          name: name,
          shown: line ? line.textContent : null,
          client: line ? line.clientWidth : null,
          scroll: line ? line.scrollWidth : null,
          width: line ? Math.round(line.getBoundingClientRect().width) : null,
          sub: sub ? sub.textContent : null,
        };
      });
    }, NAMES);
    for (const row of titles) {
      assert.equal(row.shown, row.name, `в шапке не название кампании: ${row.shown}`);
      assert(row.scroll <= row.client + 1,
        `название обрезано: «${row.name}» ${row.scroll} > ${row.client}`);
      assert(row.width >= TITLE_MIN,
        `название уже ${TITLE_MIN} px: «${row.name}» ${row.width}`);
      assert(row.sub && row.sub.trim().length > 2,
        `под названием нет второй строки (глава · место): ${row.sub}`);
    }
    checks.titles = titles;

    const geometry = await small.page.evaluate(() => {
      const form = document.querySelector('#play #action-form');
      const send = document.querySelector('#play #send');
      const chat = document.querySelector('#play #chat');
      const messages = [...chat.querySelectorAll('.message')];
      const last = messages.length ? messages[messages.length - 1] : null;
      const r = node => node.getBoundingClientRect();
      return {
        viewport: innerWidth,
        form: {x: r(form).x, right: r(form).right, w: r(form).width},
        sendRight: r(send).right,
        textToInput: last ? Math.round(r(form).top - r(last).bottom) : null,
        sidePadding: [getComputedStyle(form).paddingLeft, getComputedStyle(form).paddingRight],
      };
    });
    assert.equal(Math.round(geometry.form.x), SIDE,
      `левая граница рамки не ${SIDE} px: ${geometry.form.x}`);
    assert.equal(Math.round(geometry.viewport - geometry.form.right), SIDE,
      `правая граница рамки не ${SIDE} px: ${geometry.viewport - geometry.form.right}`);
    assert(geometry.viewport - geometry.sendRight >= 20,
      `«Сказать» слишком близко к краю: ${geometry.viewport - geometry.sendRight} px`);
    assert(geometry.textToInput !== null,
      'в ленте нет ни одного сообщения — зазор текст→поле не измерить');
    assert(geometry.textToInput <= INPUT_GAP,
      `зазор текст→поле ${geometry.textToInput} px > ${INPUT_GAP}`);
    checks.phone = geometry;
    await small.page.screenshot({path: path.join(out, 'n2-game-375.png')});
    await small.context.close();

    // ── 375 с клавиатурой ───────────────────────────────────────────────────
    const kb = await openGame(browser, 375, 476, true);
    const keyboard = await kb.page.evaluate(() => {
      const form = document.querySelector('#play #action-form').getBoundingClientRect();
      return {bottom: Math.round(form.bottom), viewport: innerHeight,
              gap: Math.round(innerHeight - form.bottom)};
    });
    assert.equal(keyboard.gap, KB_GAP,
      `до клавиатуры ${keyboard.gap} px вместо ${KB_GAP}`);
    checks.keyboard = keyboard;
    await kb.page.screenshot({path: path.join(out, 'n2-game-kb-375.png')});
    await kb.context.close();

    // ── 1440: поле ввода и колонка ленты — одна ширина ──────────────────────
    const wide = await openGame(browser, 1440, 900, false);
    const desktop = await wide.page.evaluate(() => {
      const form = document.querySelector('#play #action-form').getBoundingClientRect();
      const column = document.querySelector('#play #chat .welcome')
        || document.querySelector('#play #chat .message');
      const col = column.getBoundingClientRect();
      const short = [...document.querySelectorAll(
        '#play button, #play summary, #play textarea, main > header button, aside.sidebar button, aside.sidebar a')]
        .filter(node => {
          const r = node.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        })
        .filter(node => {
          const r = node.getBoundingClientRect();
          return r.height < 44 || r.width < 44;
        })
        .map(node => `${(node.id || node.className || node.tagName)} ${Math.round(node.getBoundingClientRect().width)}x${Math.round(node.getBoundingClientRect().height)}`);
      return {form: {x: form.x, w: form.width, right: form.right},
              column: {x: col.x, w: col.width, right: col.right},
              short};
    });
    assert.equal(Math.round(desktop.form.w), Math.round(desktop.column.w),
      `поле ${desktop.form.w} px против колонки ленты ${desktop.column.w} px`);
    assert.equal(Math.round(desktop.form.right), Math.round(desktop.column.right),
      `правый край поля ${desktop.form.right} против ${desktop.column.right}`);
    assert.deepEqual(desktop.short, [], 'зоны нажатия меньше 44 px на столе');
    checks.desktop = desktop;
    await wide.page.screenshot({path: path.join(out, 'n2-game-1440.png')});
    await wide.context.close();

    // Ошибки скриптов экрана — тоже отказ: шапка не должна «чиниться» ценой
    // падения соседнего кода.
    const pageErrors = [...small.errors, ...kb.errors, ...wide.errors];
    assert.deepEqual(pageErrors, [], 'ошибки в скриптах экрана');
    fs.writeFileSync(path.join(out, 'n2-results.json'),
      JSON.stringify({checks, pageErrors}, null, 2), 'utf8');
    console.log(JSON.stringify(checks));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
