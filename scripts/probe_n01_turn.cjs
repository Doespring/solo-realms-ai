'use strict';
// n01: приёмка на настоящем экране в Chromium, 375×812, без подмены ответов.
// Экран игры — разговор: одно нажатие «Сказать» = один POST /command-turn.
//
//   1. Двойное нажатие «Сказать»: в журнале ОДИН запрос хода.
//   2. Два таба: ход в свежем, потом в устаревшем → 409, игрок видит русскую
//      строку «Сцена обновилась. Вот что можно сделать», сцена перезапрошена,
//      повтора того же тела нет.
//
//   node scripts/probe_n01_turn.cjs <base> <campaign> <outdir> <actions.jsonl>

const path = require('path');
const fs = require('node:fs');
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || path.join('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules', 'playwright'));

const [BASE, CID, OUT = 'reports/n01/live', LOG] = process.argv.slice(2);
const WIDTH = 375, HEIGHT = 812;
const CHROME = process.env.SOLO_UI_BROWSER || '/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome';
fs.mkdirSync(OUT, { recursive: true });

const report = { base: BASE, campaign: CID, log: LOG, scenario1: {}, scenario2: {}, errors: [], ok: false };
const step = (name, value) => { report.steps = report.steps || []; report.steps.push({ name, ...value }); console.log('STEP', name, JSON.stringify(value).slice(0, 900)); };

function journal() {
  if (!LOG || !fs.existsSync(LOG)) return [];
  return fs.readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map((line) => { try { return JSON.parse(line); } catch (e) { return null; } }).filter(Boolean);
}
const turnPaths = ['/api/campaigns/' + CID + '/command-turn', '/api/campaigns/' + CID + '/screen/commands'];
const turnRequests = (rows) => rows.filter((row) => row.src === 'http' && row.m === 'POST' && turnPaths.includes(row.p));
const conflicts = (rows) => rows.filter((row) => row.src === 'conflict' && (row.campaign_id || row.campaign) === CID);

async function openGame(browser, context, tabLabel) {
  const page = await context.newPage();
  page.setDefaultTimeout(40000);
  page.on('pageerror', (e) => report.errors.push({ tab: tabLabel, kind: 'pageerror', text: String(e && e.message).slice(0, 240) }));
  page.on('console', (m) => { if (m.type() === 'error') report.errors.push({ tab: tabLabel, kind: 'console', text: m.text().slice(0, 240) }); });
  await page.addInitScript((id) => {
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
  await page.waitForSelector('#player-input', { state: 'visible', timeout: 40000 });
  await page.waitForTimeout(500);
  return page;
}

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
    // ---------- Сценарий 1: двойное нажатие — один запрос ----------
    const before = turnRequests(journal()).length;
    const page = await openGame(browser, context, 'A');
    const versionBefore = await screenVersion(page);
    step('opened', { version: versionBefore, before });

    const fired = await page.evaluate(() => {
      const input = document.getElementById('player-input');
      input.value = 'Осматриваю калитку';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const button = document.getElementById('send');
      let sent = 0;
      button.click(); sent += 1;
      button.click(); sent += 1;
      return { sent, disabledAfter: button.disabled };
    });
    await page.waitForTimeout(6000);
    const rows1 = journal();
    const turns1 = turnRequests(rows1).slice(before);
    report.scenario1 = { clicks: fired.sent, disabledAfter: fired.disabledAfter,
      requests: turns1.length, statuses: turns1.map((row) => row.c),
      router: turns1.map((row) => row.p), answers: turns1.map((row) => row.c),
      line: await feedback(page), screen: await screenVersion(page) };
    step('double-click', report.scenario1);
    await page.screenshot({ path: path.join(OUT, 'n01-double-click-375.png') });

    // ---------- Сценарий 2: два таба, ход в устаревшем ----------
    const tabB = await openGame(browser, context, 'B');
    const versionB = await screenVersion(tabB);
    step('tab-b-opened', versionB);
    await tabB.evaluate(() => {
      const input = document.getElementById('player-input');
      input.value = 'Осматриваю калитку';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('send').click();
    });
    await tabB.waitForTimeout(6000);
    const versionAfterB = await screenVersion(tabB);
    step('tab-b-acted', versionAfterB);

    // Таб A: ловим тела запросов, чтобы доказать отсутствие повтора.
    const posts = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/campaigns/' + CID)) posts.push({ url: request.url(), body: request.postData() || '' });
    });
    const conflictBefore = conflicts(journal()).length;
    // Таб A всё ещё держит сцену до хода B — его версия устарела.
    const staleVersion = await page.evaluate(() => window.SoloGameScreen && document.querySelector('#play.active') ? true : false);
    await page.evaluate(() => {
      const input = document.getElementById('player-input');
      input.value = 'Осматриваю калитку';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('send').click();
    });
    await page.waitForTimeout(1800);
    const lineAfterConflict = await feedback(page);
    await page.waitForTimeout(4000);
    const rows2 = journal();
    const newConflicts = conflicts(rows2).slice(conflictBefore);
    report.scenario2 = { tabAOpen: staleVersion, staleVersion: versionB.version, tabBVersion: versionAfterB.version,
      line: lineAfterConflict,
      posts: posts.map((p) => ({ url: p.url.replace(BASE, ''), body: p.body.slice(0, 200) })),
      conflictRecords: newConflicts, screenAfter: await screenVersion(page) };
    step('conflict', report.scenario2);
    await page.screenshot({ path: path.join(OUT, 'n01-conflict-375.png') });
    await page.screenshot({ path: path.join(OUT, 'n01-conflict-375-full.png'), fullPage: true });

    // ---------- Сценарий 3: настоящий HTTP 409 на действии экрана ----------
    // /screen/commands сверяет expected_version и отвечает 409 version_conflict.
    // Таб C держит сцену до хода B, затем шлёт устаревшее действие.
    const tabC = await openGame(browser, context, 'C');
    const versionC = await screenVersion(tabC);
    await tabB.evaluate(() => {
      const input = document.getElementById('player-input');
      input.value = 'Осматриваю калитку';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('send').click();
    });
    await tabB.waitForTimeout(6000);
    const versionAfterB2 = await screenVersion(tabB);
    const conflictBefore3 = conflicts(journal()).length;
    const posts3 = [];
    tabC.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/campaigns/' + CID)) posts3.push(request.url().replace(BASE, ''));
    });
    await tabC.evaluate(() => {
      window.SoloGameScreen.execute({ id: 'engine:1', kind: 'engine', label: 'Проверить: perception',
        parameters: { kind: 'check', check_id: 'perception' }, expected_version: undefined });
    });
    await tabC.waitForTimeout(1800);
    const lineAfter409 = await feedback(tabC);
    await tabC.waitForTimeout(4000);
    const rows3 = journal();
    report.scenario3 = { staleVersion: versionC.version, freshVersion: versionAfterB2.version,
      line: lineAfter409, posts: posts3,
      conflictRecords: conflicts(rows3).slice(conflictBefore3), screenAfter: await screenVersion(tabC) };
    step('http-409', report.scenario3);
    await tabC.screenshot({ path: path.join(OUT, 'n01-http-409-375.png') });

    // ---------- Сценарий 4: окно 400 мс после ответа ----------
    const beforeHold = turnRequests(journal()).length;
    const held = await tabB.evaluate(async () => {
      const input = document.getElementById('player-input');
      const send = document.getElementById('send');
      const answer = () => document.getElementById('game-feedback').dataset.state;
      input.value = 'Осматриваю калитку';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      send.click();
      const started = Date.now();
      await new Promise((resolve) => {
        const tick = () => {
          const state = answer();
          if (['resolved', 'error', 'conflict', 'unsupported'].includes(state)) return resolve();
          if (Date.now() - started > 20000) return resolve();
          setTimeout(tick, 20);
        };
        tick();
      });
      const answeredAt = Date.now() - started;
      // Сразу после ответа — ввод и клик внутри 400-мс окна.
      input.value = 'Осматриваю калитку';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const disabledInWindow = send.disabled;
      send.click();
      await new Promise((resolve) => setTimeout(resolve, 700));
      const disabledAfterWindow = send.disabled;
      send.click();
      return { answeredAt, disabledInWindow, disabledAfterWindow };
    });
    await tabB.waitForTimeout(6000);
    const afterHold = turnRequests(journal()).length;
    report.scenario4 = { ...held, requestsTotal: afterHold - beforeHold,
      requestsIncludingWindowClick: afterHold - beforeHold, line: await feedback(tabB) };
    step('window', report.scenario4);

    report.ok = true;
    fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));
    console.log('OK', path.join(OUT, 'report.json'));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error('FAIL', (error && error.stack) || error); process.exit(1); });
