'use strict';
// r222: приёмка входа и боя в настоящем Chromium.
// Вход (без кампании), затем кампания Nat20 → бой → один ход на карте,
// замеры 1440 и 375, стек каждой ошибки консоли.
//
//   node probe_r222_browser.cjs <base> <campaign> <outdir> [phrase]
//
// Playwright лежит в npm-кэше (npx): путь берётся из PLAYWRIGHT_DIR,
// chromium — из R222_CHROME.
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.PLAYWRIGHT_DIR || '/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules', 'playwright'));

const BASE = process.argv[2];
const CID = process.argv[3] || '';
const OUT = process.argv[4] || '/tmp/r222';
const PHRASE = process.argv[5] || 'начинаю бой';
const CHROME = process.env.R222_CHROME;

fs.mkdirSync(OUT, { recursive: true });
const report = { base: BASE, campaign: CID, phrase: PHRASE, steps: [], errors: [], layouts: {}, start: {}, ok: false };

const layoutProbe = () => {
  const vw = window.innerWidth;
  const doc = document.documentElement;
  const rect = (sel) => {
    const node = document.querySelector(sel);
    if (!node) return null;
    const r = node.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), shown: getComputedStyle(node).display !== 'none' && r.width > 0 && r.height > 0 };
  };
  const feed = document.querySelector('#chat');
  const board = document.querySelector('.board');
  const cells = board ? [...board.querySelectorAll('.board__cell')] : [];
  const cellBox = cells.length ? cells[0].getBoundingClientRect() : null;
  const overflowing = [];
  for (const node of document.querySelectorAll('#play *, .board *')) {
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
    const r = node.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right > vw + 1 || r.left < -1) overflowing.push({ tag: node.tagName, cls: String(node.className || '').slice(0, 40), left: Math.round(r.left), right: Math.round(r.right) });
  }
  return {
    viewport: [vw, window.innerHeight],
    scrollWidth: doc.scrollWidth,
    clientWidth: doc.clientWidth,
    horizontalScroll: doc.scrollWidth > doc.clientWidth + 1,
    play: rect('#play'),
    feed: rect('#chat'),
    board: rect('.board'),
    boardMap: rect('.board__map'),
    boardCells: cells.length,
    cell: cellBox ? { w: Math.round(cellBox.width), h: Math.round(cellBox.height) } : null,
    gridColumns: board ? getComputedStyle(board.querySelector('.board__map') || board).gridTemplateColumns.split(' ').length : null,
    turns: board ? [...board.querySelectorAll('.board__turn')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()) : [],
    legend: board ? [...board.querySelectorAll('.board__legend li')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()) : [],
    boardMeta: board ? (board.querySelector('.board__meta') || {}).textContent : null,
    boardNote: board ? (board.querySelector('.board__note') || {}).textContent : null,
    startShown: !!document.querySelector('#welcome.active'),
    startButtons: [...document.querySelectorAll('#welcome .start__btn')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()),
    overflowing: overflowing.slice(0, 10),
  };
};

const startProbe = () => {
  const view = document.querySelector('#welcome');
  if (!view) return { present: false };
  const r = view.getBoundingClientRect();
  const style = getComputedStyle(view);
  const button = view.querySelector('.start__btn--primary');
  const box = button ? button.getBoundingClientRect() : null;
  return {
    present: true, active: view.classList.contains('active'),
    width: Math.round(r.width), height: Math.round(r.height),
    background: style.backgroundColor,
    font: style.fontFamily,
    buttons: [...view.querySelectorAll('.start__btn')].map((n) => ({ text: n.textContent.replace(/\s+/g, ' ').trim(), w: Math.round(n.getBoundingClientRect().width), h: Math.round(n.getBoundingClientRect().height) })),
    primaryRadius: button ? getComputedStyle(button).borderRadius : null,
    primaryColor: button ? getComputedStyle(button).backgroundColor : null,
    heading: (view.querySelector('.start__title') || {}).textContent || null,
    lead: (view.querySelector('.start__lead') || {}).textContent || null,
    continueShown: !!(view.querySelector('.start__continue') && !view.querySelector('.start__continue').hidden),
    background: getComputedStyle(view).backgroundColor,
    overflow: { scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth },
    images: view.querySelectorAll('img').length,
    box,
  };
};

async function activatePlay(page) {
  if (await page.evaluate(() => !!document.querySelector('#play.active'))) return 'already';
  const startButton = page.locator('#welcome .start__btn--primary');
  if (await startButton.count()) { await startButton.first().click(); await page.waitForTimeout(400); }
  if (await page.evaluate(() => !!document.querySelector('#play.active'))) return 'by-start';
  const nav = page.locator('.nav[data-view="play"], [data-view="play"]').first();
  if (await nav.count()) { await nav.click({ timeout: 10000 }).catch(() => {}); }
  await page.waitForSelector('#play.active', { timeout: 20000 });
  return 'by-tab';
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') report.errors.push({ kind: 'console', text: m.text().slice(0, 240) }); });
  page.on('pageerror', (e) => report.errors.push({ kind: 'pageerror', text: String(e && e.message).slice(0, 240) }));
  page.on('requestfailed', (r) => report.errors.push({ kind: 'requestfailed', url: r.url().slice(0, 160) }));
  page.on('response', (r) => { if (r.status() >= 400) report.errors.push({ kind: 'http' + r.status(), url: r.url().slice(0, 160) }); });
  const step = (name, value) => { report.steps.push({ name, ...value }); console.log('STEP', name, JSON.stringify(value).slice(0, 600)); };

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  report.start['1440'] = await page.evaluate(startProbe);
  step('start-1440', report.start['1440']);
  await page.screenshot({ path: OUT + '/start-1440.png' });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(400);
  report.start['375'] = await page.evaluate(startProbe);
  step('start-375', report.start['375']);
  await page.screenshot({ path: OUT + '/start-375.png' });

  if (!CID) { report.ok = true; await browser.close(); fs.writeFileSync(OUT + '/report.json', JSON.stringify(report, null, 1)); console.log('OK', OUT); return; }

  await page.setViewportSize({ width: 1440, height: 900 });
  await ctx.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', 'on');
  }, CID);
  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(4000);
  step('play-opened', { how: await activatePlay(page) });
  await page.waitForSelector('#chat article, #chat .message', { timeout: 40000 });
  await page.waitForTimeout(400);
  report.layouts['1440-before-turn'] = await page.evaluate(layoutProbe);
  step('layout-1440-before', report.layouts['1440-before-turn']);

  const before = await page.locator('#chat article, #chat .message').count();
  await page.fill('#player-input', PHRASE);
  await page.click('#send');
  await page.waitForFunction((n) => document.querySelectorAll('#chat article, #chat .message').length > n, before, { timeout: 60000 });
  // Поле боя рисуется из пакета экрана; ждём именно его, иначе замер снимет
  // ещё не отрисованную плашку.
  const boardShown = await page.waitForSelector('#board:not([hidden])', { timeout: 20000 }).then(() => true).catch(() => false);
  await page.waitForTimeout(600);
  step('turn-sent', { before, after: await page.locator('#chat article, #chat .message').count(), boardShown });
  report.layouts['1440'] = await page.evaluate(layoutProbe);
  step('layout-1440', report.layouts['1440']);
  await page.screenshot({ path: OUT + '/combat-1440.png' });
  await page.screenshot({ path: OUT + '/combat-1440-full.png', fullPage: true });

  // Один ход на карте: клетку называет движок в списке действий; клик по
  // подсвеченной клетке, если она нарисована, иначе реплика координатами.
  const cell = page.locator('.board__cell--move').first();
  const marked = await cell.count();
  const beforeMove = await page.locator('#chat article, #chat .message').count();
  if (marked) {
    await cell.click({ timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(300);
    step('map-cell-click', { value: await page.evaluate(() => (document.getElementById('player-input') || {}).value || null) });
  }
  // Клетку называет движок: координаты берём из пакета экрана, а не из головы.
  // Если сейчас ход противника, сначала «заканчиваю ход» — иначе движок
  // законно отказывает («Hero is not the current actor»).
  const screenCommands = () => page.evaluate(async (id) => {
    const response = await fetch('/api/campaigns/' + id + '/screen');
    const packet = await response.json();
    return (packet.commands || []).map((command) => ({ label: command.label, parameters: command.parameters }));
  }, CID);
  const sendPhrase = async (phrase) => {
    const count = await page.locator('#chat article, #chat .message').count();
    await page.fill('#player-input', phrase);
    await page.click('#send');
    await page.waitForFunction((n) => document.querySelectorAll('#chat article, #chat .message').length > n, count, { timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(1200);
    return page.locator('#chat article, #chat .message').count();
  };
  let moved = null, passes = 0;
  for (let attempt = 0; attempt < 4; attempt++) {
    const commands = await screenCommands();
    const move = commands.find((command) => command.parameters && command.parameters.kind === 'move');
    if (move) {
      const target = move.parameters.position;
      const before = await page.locator('#chat article, #chat .message').count();
      const after = await sendPhrase('иду на клетку ' + target.x + ' ' + target.y);
      moved = { target, label: move.label, before, after };
      break;
    }
    const endTurn = commands.find((command) => command.parameters && command.parameters.kind === 'end_turn');
    if (!endTurn) break;
    passes += 1;
    await sendPhrase('заканчиваю ход');
  }
  step('map-move', { moved, passes, marked: await page.locator('.board__cell--move').count() });
  report.layouts['1440-move'] = await page.evaluate(layoutProbe);

  // 375 на настоящем бое — ДО синтетического пакета, иначе скриншот покажет
  // нарисованную сетку и будет выдавать её за данные сервера.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(500);
  report.layouts['375'] = await page.evaluate(layoutProbe);
  step('layout-375', report.layouts['375']);
  await page.screenshot({ path: OUT + '/combat-375.png' });
  await page.screenshot({ path: OUT + '/combat-375-full.png', fullPage: true });

  // Проверка самой рисовалки на синтетическом пакете: сегодняшний экран боя
  // геометрию не получает (reports/r222/SCREEN-PROJECTION-PATCH.md), поэтому
  // клиент проверяется на пакете той формы, которую описывает спецификация.
  // Это проверка кода отрисовки, а не утверждение о данных сервера.
  // Пакет отправляется заново перед каждым замером: любая перерисовка экрана
  // возвращает настоящий пакет, и прошлый синтетический молча исчезает.
  const SYNTHETIC = "(() => { const actors = ["
    + "{ id: 'hero', name: 'Мирон', team: 'hero', hp: 12, max_hp: 12, conditions: [], initiative: 22, position: { x: 1, y: 1, z: 0 } },"
    + "{ id: 'foe', name: 'Разбойник', team: 'enemy', hp: 4, max_hp: 9, conditions: ['grappled'], initiative: 11, position: { x: 3, y: 2, z: 0 } }];"
    + " const grid = { width: 7, height: 5, cell_size_ft: 5, blocked_cells: ['5,0', '5,1'], cover_cells: { '2,3': 'half', '4,4': 'total' } };"
    + " document.dispatchEvent(new CustomEvent('solo:packet', { detail: { snapshot: {"
    + " combat: { active: true, round: 2, current_actor: 'hero', actors, grid, terrain: 'укрытие: половинное' },"
    + " scene: { title: 'Трактирный зал' } }, commands: [] } })); })()";
  const drawSynthetic = async () => { await page.evaluate(SYNTHETIC); await page.waitForTimeout(150); };

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(400);
  await drawSynthetic();
  report.layouts['1440-grid'] = await page.evaluate(layoutProbe);
  step('synthetic-grid-1440', report.layouts['1440-grid']);
  await drawSynthetic();
  await page.screenshot({ path: OUT + '/grid-synthetic-1440.png' });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(500);
  await drawSynthetic();
  report.layouts['375-grid'] = await page.evaluate(layoutProbe);
  step('synthetic-grid-375', report.layouts['375-grid']);
  await drawSynthetic();
  await page.screenshot({ path: OUT + '/grid-synthetic-375.png' });

  report.ok = report.errors.filter((e) => e.kind !== 'console' || !/Content Security Policy/.test(e.text)).length === 0;
  await browser.close();
  fs.writeFileSync(OUT + '/report.json', JSON.stringify(report, null, 1));
  console.log('ERRORS', JSON.stringify(report.errors.slice(0, 8)));
  console.log(report.ok ? 'OK ' + OUT : 'FAILED ' + OUT);
})().catch((error) => { console.error('PROBE FAILED', error && error.message); process.exit(1); });
