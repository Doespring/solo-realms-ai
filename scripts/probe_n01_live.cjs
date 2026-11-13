'use strict';
// n01: приёмка на настоящем экране в Chromium, 375×812, без подмены ответов.
// Сервер поднимает scripts/n01_accept_server.py (своя база, свой журнал).
//
//   1. Открыть кампанию → нажать действие дважды быстро: в журнале ОДИН запрос
//      хода, второй не уходит ни сразу, ни через 400 мс.
//   2. Два таба: действие в свежем, потом в старом → 409, игрок видит русскую
//      строку «Сцена обновилась. Вот что можно сделать», сцена перезапрошена,
//      повтора того же тела нет.
//
//   node scripts/probe_n01_live.cjs <base> <campaign> <outdir> <actions.jsonl>

const path = require('path');
const fs = require('node:fs');
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || path.join('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules', 'playwright'));

const [BASE, CID, OUT = 'reports/n01/live', LOG] = process.argv.slice(2);
const WIDTH = 375, HEIGHT = 812;
const CHROME = process.env.SOLO_UI_BROWSER || '/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome';
fs.mkdirSync(OUT, { recursive: true });

const report = { base: BASE, campaign: CID, log: LOG, scenario1: {}, scenario2: {}, errors: [], ok: false };
const step = (name, value) => { report.steps = report.steps || []; report.steps.push({ name, ...value }); console.log('STEP', name, JSON.stringify(value).slice(0, 700)); };

function journal() {
  if (!LOG || !fs.existsSync(LOG)) return [];
  return fs.readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map((line) => { try { return JSON.parse(line); } catch (e) { return null; } }).filter(Boolean);
}
const turnRequests = (rows) => rows.filter((row) => row.src === 'http' && row.m === 'POST'
  && (row.p === '/api/campaigns/' + CID + '/screen/commands' || row.p === '/api/campaigns/' + CID + '/command-turn'));
const conflicts = (rows) => rows.filter((row) => row.src === 'conflict' && row.campaign === CID);

async function openGame(browser, context, tabLabel, init) {
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  page.on('pageerror', (e) => report.errors.push({ tab: tabLabel, kind: 'pageerror', text: String(e && e.message).slice(0, 240) }));
  page.on('console', (m) => { if (m.type() === 'error') report.errors.push({ tab: tabLabel, kind: 'console', text: m.text().slice(0, 240) }); });
  if (init) await page.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', 'on');
  }, CID);
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => typeof window.SoloGameScreen === 'object', { timeout: 30000 });
  if (!(await page.evaluate(() => !!document.querySelector('#play.active')))) {
    const start = page.locator('#welcome .start__btn--primary');
    if (await start.count()) { await start.first().click({ timeout: 5000 }).catch(() => {}); await page.waitForTimeout(400); }
  }
  if (!(await page.evaluate(() => !!document.querySelector('#play.active')))) {
    const nav = page.locator('.nav[data-view="play"], [data-view="play"]').first();
    if (await nav.count()) await nav.click({ timeout: 8000 }).catch(() => {});
  }
  await page.waitForSelector('#play.active', { timeout: 25000 });
  await page.waitForSelector('.game-actions:not([hidden]) button', { timeout: 40000 });
  await page.waitForTimeout(300);
  return page;
}

const actionButtons = (page) => page.evaluate(() => {
  const row = document.querySelector('.game-actions:not([hidden])');
  if (!row) return [];
  return [...row.querySelectorAll('button')].filter((b) => !b.disabled).map((b) => b.textContent.trim());
});

const feedback = (page) => page.evaluate(() => {
  const node = document.getElementById('game-feedback');
  if (!node) return null;
  const text = document.querySelector('#game-feedback [class*="feedback"], #game-feedback p')?.textContent || node.textContent;
  return { hidden: node.hidden, state: node.dataset.state || null, text: (text || '').replace(/\s+/g, ' ').trim() };
});

const screenVersion = (page) => page.evaluate(async (id) => {
  const response = await fetch('/api/campaigns/' + id + '/screen');
  const packet = await response.json();
  return { version: packet.snapshot && packet.snapshot.version, commands: (packet.commands || []).length };
}, CID);

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'], headless: true });
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, locale: 'ru-RU' });
  try {
    // ---------- Сценарий 1: одно нажатие — один запрос ----------
    const before = turnRequests(journal()).length;
    const page = await openGame(browser, context, 'A', true);
    const buttons = await actionButtons(page);
    if (!buttons.length) throw new Error('на экране нет доступных действий — кампания не годится для прогона');
    const versionBefore = await screenVersion(page);
    step('opened', { buttons, version: versionBefore.version, before });

    // Двойное нажатие: два клика в одном тике, как двойной клик игрока.
    const fired = await page.evaluate(() => {
      const row = document.querySelector('.game-actions:not([hidden])');
      const button = [...row.querySelectorAll('button')].find((b) => !b.disabled);
      let sent = 0;
      button.click(); sent += 1;
      button.click(); sent += 1;
      return { sent, label: button.textContent.trim(), disabledAfter: button.disabled };
    });
    await page.waitForTimeout(4500);
    const rows1 = journal();
    const turns1 = turnRequests(rows1).slice(before);
    report.scenario1 = { clicks: fired.sent, label: fired.label, disabledAfter: fired.disabledAfter,
      requests: turns1.length, statuses: turns1.map((row) => row.c), router: turns1.map((row) => row.p),
      via: turns1.map((row) => row.via || null), line: await feedback(page), screen: await screenVersion(page) };
    step('double-click', report.scenario1);
    await page.screenshot({ path: path.join(OUT, 'n01-double-click-375.png') });

    // Клик в 400-мс окне после ответа не должен рождать запрос: ждём окно и
    // проверяем, что повтор разрешён только после него, уже с новой версией.
    const beforeWindow = turnRequests(journal()).length;
    await page.evaluate(() => { const row = document.querySelector('.game-actions:not([hidden])'); const b = row && [...row.querySelectorAll('button')].find((n) => !n.disabled); if (b) b.click(); });
    await page.waitForTimeout(1200);
    report.scenario1.afterWindowClick = turnRequests(journal()).length - beforeWindow;
    step('window-click', { requests: report.scenario1.afterWindowClick });

    // ---------- Сценарий 2: два таба, действие в старом ----------
    const tabB = await openGame(browser, context, 'B');
    const versionB = await screenVersion(tabB);
    step('tab-b-opened', versionB);
    // Таб B уходит вперёд: его действие делает версию свежей, у таба A — старая сцена.
    const bFired = await tabB.evaluate(() => {
      const row = document.querySelector('.game-actions:not([hidden])');
      const b = row && [...row.querySelectorAll('button')].find((n) => !n.disabled);
      if (!b) return null;
      b.click(); return b.textContent.trim();
    });
    await tabB.waitForTimeout(4500);
    const versionAfterB = await screenVersion(tabB);
    step('tab-b-acted', { label: bFired, ...versionAfterB });

    // Таб A: ловим тела запросов, чтобы доказать отсутствие повтора.
    const posts = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/campaigns/' + CID)) posts.push({ url: request.url(), body: request.postData() || '' });
    });
    const conflictBefore = conflicts(journal()).length;
    const stale = await page.evaluate(() => {
      const row = document.querySelector('.game-actions:not([hidden])');
      const b = row && [...row.querySelectorAll('button')].find((n) => !n.disabled);
      if (!b) return null;
      b.click(); return b.textContent.trim();
    });
    await page.waitForTimeout(1500);
    const lineAfterConflict = await feedback(page);
    await page.waitForTimeout(4000);
    const rows2 = journal();
    const newConflicts = conflicts(rows2).slice(conflictBefore);
    report.scenario2 = { staleLabel: stale, tabAtoVersion: versionAfterB.version, line: lineAfterConflict,
      posts: posts.map((p) => ({ url: p.url.replace(BASE, ''), body: p.body.slice(0, 200) })),
      conflictRecords: newConflicts, screenAfter: await screenVersion(page) };
    step('conflict', report.scenario2);
    await page.screenshot({ path: path.join(OUT, 'n01-conflict-375.png') });
    await page.screenshot({ path: path.join(OUT, 'n01-conflict-375-full.png'), fullPage: true });

    report.ok = true;
    fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
    console.log('OK', path.join(OUT, 'report.json'));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error('FAIL', (error && error.stack) || error); process.exit(1); });
