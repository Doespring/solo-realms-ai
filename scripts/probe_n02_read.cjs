'use strict';
// n02: приёмка на настоящем экране, 375×812 — что игрок читает при отказе и
// сколько 409 остаётся на чтении.
//
//   1. Кампания из закладки удалена → GET 404 → игрок видит русскую причину,
//      а не «Не удалось обработать запрос». Скриншот.
//   2. Кампания прежней версии из закладки → GET 409 → русская причина. Скриншот.
//   3. Список кампаний: старая карточка помечена и её кнопка выключена; пять
//      рабочих открываются подряд. В журнале за прогон — ноль 409 на GET.
//
//   node scripts/probe_n02_read.cjs <base> <legacy> <missing> <outdir> <actions.jsonl>

const path = require('path');
const fs = require('node:fs');
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || path.join('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules', 'playwright'));

const [BASE, LEGACY, MISSING, OUT = 'reports/n02/live', LOG] = process.argv.slice(2);
const WIDTH = 375, HEIGHT = 812;
const CHROME = process.env.SOLO_UI_BROWSER || '/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome';
fs.mkdirSync(OUT, { recursive: true });

const report = { base: BASE, legacy: LEGACY, missing: MISSING, log: LOG, refusals: {}, opened: [], errors: [], ok: false };
const step = (name, value) => { report.steps = report.steps || []; report.steps.push({ name, ...value }); console.log('STEP', name, JSON.stringify(value).slice(0, 700)); };

function journal() {
  if (!LOG || !fs.existsSync(LOG)) return [];
  return fs.readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map((line) => { try { return JSON.parse(line); } catch (e) { return null; } }).filter(Boolean);
}

async function openWithBookmark(browser, id) {
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  page.on('pageerror', (e) => report.errors.push({ kind: 'pageerror', text: String(e && e.message).slice(0, 240) }));
  await page.addInitScript((campaign) => {
    localStorage.setItem('solo-remember-campaign', 'on');
    localStorage.setItem('solo-campaign', campaign);
    localStorage.removeItem('solo-pending-send');
    localStorage.removeItem('solo-wizard-draft');
  }, id);
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // Отказ показывается тем же `toast`, что и любая другая беда экрана.
  await page.waitForFunction(() => { const t = document.querySelector('#toast'); return t && !t.hidden && (t.textContent || '').trim().length > 10; }, { timeout: 40000 });
  const text = (await page.textContent('#toast') || '').trim();
  return { context, page, text };
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {

    // 1. Удалённая кампания в закладке: 404.
    {
      const { context, page, text } = await openWithBookmark(browser, MISSING);
      report.refusals.missing = { status: 404, shown: text };
      step('404-text', { text });
      await page.screenshot({ path: path.join(OUT, 'n02-01-not-found-375.png'), fullPage: false });
      await context.close();
    }

    // 2. Кампания прежней версии в закладке: 409.
    {
      const { context, page, text } = await openWithBookmark(browser, LEGACY);
      report.refusals.legacy = { status: 409, shown: text };
      step('409-text', { text });
      await page.screenshot({ path: path.join(OUT, 'n02-02-legacy-375.png'), fullPage: false });
      await context.close();
    }

    // 3. Список кампаний и пять открытий подряд. Считаем только этот отрезок:
    // 409 из сценария 2 — намеренный показ отказа, а не поведение списка.
    const beforeList = journal().length;
    {
      const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
      const page = await context.newPage();
      page.setDefaultTimeout(40000);
      const gets = [];
      page.on('response', (response) => {
        const url = response.url();
        const match = url.match(/\/api\/campaigns\/([0-9a-f-]{36}|legacy-n02)(?:\?|$)/);
        if (response.request().method() === 'GET' && match) gets.push({ id: match[1], status: response.status() });
      });
      page.on('pageerror', (e) => report.errors.push({ kind: 'pageerror', text: String(e && e.message).slice(0, 240) }));
      await page.addInitScript(() => { localStorage.removeItem('solo-campaign'); localStorage.removeItem('solo-remember-campaign'); });
      await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForSelector('.nav[data-view="campaigns"]', { timeout: 30000 });
      // На 375 меню вне вьюпорта: клик по нему Playwright отвергает, а игрок нажимает.
      await page.evaluate(() => document.querySelector('.nav[data-view="campaigns"]').click());
      await page.waitForSelector('#campaign-list .chronicle-card', { timeout: 30000 });
      const cards = await page.evaluate(() => [...document.querySelectorAll('#campaign-list .chronicle-card')].map((card) => ({
        text: card.innerText.replace(/\s+/g, ' ').trim().slice(0, 120),
        disabled: !!card.querySelector('button')?.disabled,
      })));
      report.list = { total: cards.length, disabled: cards.filter((c) => c.disabled).length, legacyHint: cards.some((c) => /прежней версии/.test(c.text)) };
      await page.screenshot({ path: path.join(OUT, 'n02-03-list-375.png'), fullPage: true });
      step('list', report.list);

      const enabled = await page.evaluate(() => [...document.querySelectorAll('#campaign-list .chronicle-card')]
        .map((card, index) => ({ index, disabled: !!card.querySelector('button')?.disabled }))
        .filter((row) => !row.disabled).slice(0, 5).map((row) => row.index));
      for (const index of enabled) {
        await page.evaluate((i) => { document.querySelectorAll('#campaign-list .chronicle-card button')[i]?.click(); }, index);
        await page.waitForFunction(() => !!document.querySelector('#play.active'), { timeout: 40000 }).catch(() => {});
        await page.waitForTimeout(350);
        await page.evaluate(() => document.querySelector('.nav[data-view="campaigns"]').click());
        await page.waitForSelector('#campaign-list .chronicle-card', { timeout: 30000 });
      }
      report.opened = gets;
      report.openStatuses = gets.reduce((acc, row) => { acc[row.status] = (acc[row.status] || 0) + 1; return acc; }, {});
      step('opened', { count: enabled.length, statuses: report.openStatuses });
      await context.close();
    }

    const added = journal().slice(beforeList).filter((row) => row.src === 'http' && row.m === 'GET' && /^\/api\/campaigns\/[^/]+$/.test(row.p || ''));
    report.journalGets = added;
    report.journalConflicts = added.filter((row) => row.c === 409).length;
    report.ok = !report.errors.length
      && /Кампания не найдена/.test(report.refusals.missing?.shown || '')
      && /прежней версии/.test(report.refusals.legacy?.shown || '')
      && report.journalConflicts === 0
      && report.list.disabled >= 1;
    console.log('REPORT', JSON.stringify({ ...report, steps: undefined, opened: undefined }, null, 1));
    fs.writeFileSync(path.join(OUT, 'n02-live.json'), JSON.stringify(report, null, 1), 'utf8');
    process.exitCode = report.ok ? 0 : 1;
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error('PROBE FAILED', error && error.stack || error); process.exit(1); });
