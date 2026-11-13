'use strict';
// r220: приёмка игрового экрана в настоящем Chromium.
// Один живой ход, плашка броска с SVG-кубиком, замеры 1440 и 375, стек каждой ошибки.
// Отличие от черновика: если на полной странице сначала открыта оболочка «стол»,
// жмём вкладку «Игра» — иначе #play.active не появляется и проба падает не по делу.
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = process.argv[2];
const CID = process.argv[3];
const OUT = process.argv[4] || '/tmp/r220';
const PHRASE = process.argv[5] || 'проверяю внимательность';
const CHROME = process.env.R220_CHROME;

fs.mkdirSync(OUT, { recursive: true });
const report = { base: BASE, steps: [], errors: [], layouts: {}, dive: {}, ok: false };

const layoutProbe = () => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const doc = document.documentElement;
  const header = document.querySelector('main>header');
  const chat = document.getElementById('chat');
  const view = document.getElementById('play');
  const rail = document.querySelector('.scene-rail');
  const toggle = document.getElementById('sheet-toggle');
  const overlaps = (a, b) => a && b && !(a.bottom <= b.top || b.bottom <= a.top || a.right <= b.left || b.right <= a.left);
  const overflowing = [];
  for (const node of document.querySelectorAll('#play *, main>header *')) {
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
    const r = node.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right > vw + 1 || r.left < -1) overflowing.push({ tag: node.tagName, cls: String(node.className || '').slice(0, 40), left: Math.round(r.left), right: Math.round(r.right) });
  }
  const feed = chat ? getComputedStyle(chat) : null;
  const msg = document.querySelector('#chat .message, #chat article');
  const msgStyle = msg ? getComputedStyle(msg) : null;
  const sendBtn = document.getElementById('send');
  const tokens = view ? getComputedStyle(view) : null;
  const cs = chat ? getComputedStyle(chat) : null;
  return {
    viewport: [vw, vh],
    scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth,
    horizontalScroll: doc.scrollWidth > doc.clientWidth + 1,
    headerOverlapsFeed: overlaps(header && header.getBoundingClientRect(), chat && chat.getBoundingClientRect()),
    headerHeight: header ? Math.round(header.getBoundingClientRect().height) : null,
    chatWidth: chat ? Math.round(chat.getBoundingClientRect().width) : null,
    chatPadX: cs ? [cs.paddingLeft, cs.paddingRight] : null,
    chatFontSize: feed ? feed.fontSize : null,
    chatLineHeight: feed ? feed.lineHeight : null,
    messageFontSize: msgStyle ? msgStyle.fontSize : null,
    messageLineHeight: msgStyle ? msgStyle.lineHeight : null,
    sentButton: sendBtn ? sendBtn.textContent.trim() : null,
    railVisible: rail ? getComputedStyle(rail).display !== 'none' && rail.getBoundingClientRect().width > 0 : false,
    railWidth: rail ? Math.round(rail.getBoundingClientRect().width) : 0,
    sheetToggleShown: toggle ? !!toggle.offsetParent : false,
    radiusToken: tokens ? tokens.getPropertyValue('--radius').trim() : '',
    inkToken: tokens ? tokens.getPropertyValue('--ink-900').trim() : '',
    vitals: document.querySelector('main>header .vitals')?.textContent.replace(/\s+/g, ' ').trim() || null,
    overflowing: overflowing.slice(0, 12),
  };
};

async function activatePlay(page) {
  const active = await page.evaluate(() => !!document.querySelector('#play.active'));
  if (active) return 'already';
  const nav = page.locator('.nav[data-view="play"], [data-view="play"]').first();
  await nav.click({ timeout: 10000 });
  await page.waitForSelector('#play.active', { timeout: 20000 });
  return 'by-tab';
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await ctx.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', 'on');
  }, CID);
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') report.errors.push({ kind: 'console', text: m.text().slice(0, 220), url: (m.location() || {}).url }); });
  page.on('pageerror', (e) => report.errors.push({ kind: 'pageerror', text: String(e && e.message).slice(0, 220), stack: String((e && e.stack) || '').split('\n').slice(0, 2).join(' | ') }));
  page.on('requestfailed', (r) => report.errors.push({ kind: 'requestfailed', url: r.url().slice(0, 160) }));
  page.on('response', (r) => { if (r.status() >= 400) report.errors.push({ kind: 'http' + r.status(), url: r.url().slice(0, 160) }); });

  const step = (name, value) => { report.steps.push({ name, ...value }); console.log('STEP', name, JSON.stringify(value).slice(0, 420)); };

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  step('play-opened', { how: await activatePlay(page) });
  await page.waitForSelector('#chat article, #chat .message', { timeout: 25000 });
  await page.waitForTimeout(500);
  step('feed-loaded', { articles: await page.locator('#chat article, #chat .message').count(), errorsAfterLoad: report.errors.length });

  report.layouts['1440'] = await page.evaluate(layoutProbe);
  step('layout-1440', report.layouts['1440']);
  await page.screenshot({ path: OUT + '/after-1440.png' });
  await page.screenshot({ path: OUT + '/after-1440-full.png', fullPage: true });

  const before = await page.locator('#chat article, #chat .message').count();
  await page.fill('#player-input', PHRASE);
  await page.click('#send');
  await page.waitForFunction((n) => document.querySelectorAll('#chat article, #chat .message').length > n, before, { timeout: 40000 });
  let rollFound = null;
  try { rollFound = await page.waitForSelector('#chat .roll', { timeout: 30000 }); } catch (e) { rollFound = null; }
  step('turn-sent', { before, after: await page.locator('#chat article, #chat .message').count(), roll: !!rollFound });

  if (rollFound) {
    report.dive = await page.evaluate(() => {
      const roll = document.querySelector('#chat .roll');
      const svg = roll && roll.querySelector('svg.die');
      const tag = roll && roll.querySelector('.roll__tag');
      const anims = tag ? tag.getAnimations().map((a) => ({ name: a.animationName, dur: a.effect && a.effect.getTiming().duration })) : [];
      const zone = (c) => { const n = roll && roll.querySelector('.' + c); return { zone: c, present: !!n, width: n ? Math.round(n.getBoundingClientRect().width) : 0 }; };
      const nums = roll && roll.querySelector('.roll__nums');
      const numStyle = nums ? getComputedStyle(nums) : null;
      return {
        rolling: roll ? roll.classList.contains('is-rolling') : false,
        animations: anims,
        zones: ['roll__die', 'roll__name', 'roll__nums', 'roll__tag'].map(zone),
        svgFaces: svg ? svg.querySelectorAll('polygon.die__face').length : 0,
        svgEdges: svg ? svg.querySelectorAll('path.die__edge').length : 0,
        dieNumber: roll && roll.querySelector('.die__num') ? roll.querySelector('.die__num').textContent : null,
        verdict: roll && roll.querySelector('.roll__verdict') ? roll.querySelector('.roll__verdict').textContent : null,
        numsWhiteSpace: numStyle ? numStyle.whiteSpace : null,
        ariaLabel: roll ? roll.getAttribute('aria-label') : null,
      };
    });
    step('roll-plaque', report.dive);
    await page.locator('#chat .roll').last().scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    await page.screenshot({ path: OUT + '/after-1440-roll.png' });
    await page.locator('#chat .roll').last().screenshot({ path: OUT + '/roll-plaque.png' }).catch(() => {});
  }

  await page.setViewportSize({ width: 375, height: 812 });
  await page.waitForTimeout(500);
  report.layouts['375'] = await page.evaluate(layoutProbe);
  step('layout-375', report.layouts['375']);
  await page.screenshot({ path: OUT + '/after-375.png' });
  await page.screenshot({ path: OUT + '/after-375-full.png', fullPage: true });
  const toggle = page.locator('#sheet-toggle');
  if (await toggle.isVisible()) {
    await toggle.click();
    await page.waitForTimeout(300);
    step('sheet-by-button', await page.evaluate(() => ({
      open: document.body.classList.contains('sheet-open'),
      railWidth: Math.round((document.querySelector('.scene-rail') || { getBoundingClientRect: () => ({ width: 0 }) }).getBoundingClientRect().width),
      aria: document.getElementById('sheet-toggle').getAttribute('aria-expanded'),
    })));
    await page.screenshot({ path: OUT + '/after-375-sheet.png' });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(400);
  step('wide-sheet', await page.evaluate(() => {
    const rail = document.querySelector('.scene-rail');
    return { railVisible: rail ? getComputedStyle(rail).display !== 'none' : false, railWidth: rail ? Math.round(rail.getBoundingClientRect().width) : 0, vitals: document.querySelector('main>header .vitals')?.textContent.replace(/\s+/g, ' ').trim() || null };
  }));
  step('errors-total', { count: report.errors.length });

  // Что считаем чужим шумом: он есть и на коммите-родителе (проверено отдельным прогоном).
  const preExisting = /favicon\.ico|Applying inline style violates|identity is not defined/;
  const fresh = report.errors.filter((e) => !preExisting.test((e.text || '') + ' ' + (e.url || '')));
  report.freshErrors = fresh.length;
  report.ok = fresh.length === 0 && !report.layouts['375'].horizontalScroll && !report.layouts['1440'].horizontalScroll;
  fs.writeFileSync(OUT + '/report.json', JSON.stringify(report, null, 2));
  console.log('FRESH_ERRORS', fresh.length, JSON.stringify(fresh).slice(0, 600));
  console.log('RESULT', JSON.stringify({ ok: report.ok, errorsTotal: report.errors.length, fresh: fresh.length }));
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((e) => { console.error('PROBE_FAILED', e && e.message); process.exit(2); });
