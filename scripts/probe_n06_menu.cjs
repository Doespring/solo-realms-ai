// n06 приёмка меню: полоса на 1440, шапка и шторка на 375.
// node scripts/probe_n06_menu.cjs <base> <out-dir>
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const [base, out] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });

const visible = `(n) => { const s = getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
  const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight; }`;

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const result = {};

  for (const [name, width, height, screenshot] of [['desktop', 1440, 900, true], ['phone', 375, 812, true]]) {
    const context = await browser.newContext({ viewport: { width, height }, locale: 'ru-RU', deviceScaleFactor: 1 });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 300)));
    page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE ' + m.text().slice(0, 200)); });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.soloMenu && document.querySelector('#nav-drawer'));
    await page.waitForTimeout(1200); // поздние скрипты: worlds-panel, mobile, wizard
    await page.evaluate((v) => { window.__vis = eval(v); }, visible);

    const strip = await page.evaluate(() => {
      const side = document.querySelector('body > aside.sidebar');
      const brand = side.querySelector('.brand');
      const bar = [...side.querySelectorAll('nav .nav-bar > *')];
      const shown = [];
      if (brand && window.__vis(brand)) shown.push({ text: brand.innerText.trim().split('\n')[0], kind: 'brand' });
      for (const n of bar) if (window.__vis(n)) shown.push({ text: n.innerText.trim(), kind: n.id || n.dataset.view || n.className });
      return { visible: shown.length, items: shown.map((x) => x.text), brand: brand ? brand.innerText.trim() : null,
        barChildren: bar.map((n) => (n.dataset.view || n.id) + ':' + n.innerText.trim()) };
    });

    const empty = await page.evaluate(() => [...document.querySelectorAll('button,a,summary')].filter(x => !x.innerText.trim()).length);
    /* Счётчик пересборок меню: rebuild() вставляет новый .nav-bar в nav.
       В покое должно быть 0, на смене порога — ровно 1. */
    const countRebuilds = () => page.evaluate(() => {
      window.__rb = 0;
      if (window.__rbObs) window.__rbObs.disconnect();
      window.__rbObs = new MutationObserver((list) => {
        for (const rec of list) {
          if (rec.type !== 'childList' || !rec.target || rec.target.tagName !== 'NAV') continue;
          for (const node of rec.addedNodes) if (node.classList && node.classList.contains('nav-bar')) window.__rb += 1;
        }
      });
      window.__rbObs.observe(document.querySelector('body > aside.sidebar'), { childList: true, subtree: true });
    });
    await countRebuilds();
    await page.waitForTimeout(1000);
    const idleRebuilds = await page.evaluate(() => window.__rb);

    let drawer = null, clicks = null, openShot = null;
    if (name === 'desktop') {
      await page.screenshot({ path: path.join(out, 'n06-1440-strip.png') });
      await page.click('#nav-more');
      await page.waitForTimeout(250);
      drawer = await page.evaluate(() => {
        const d = document.querySelector('#nav-drawer');
        const rows = [...d.querySelectorAll('button.nav, a.nav')].filter(window.__vis);
        return {
          rows: rows.length,
          labels: rows.map((n) => n.innerText.trim()),
          groups: [...d.querySelectorAll('.nav-group-label')].map((n) => n.innerText.trim()),
          last: d.lastElementChild ? d.lastElementChild.innerText.trim().slice(0, 40) : null,
          drawerHeight: Math.round(d.getBoundingClientRect().height),
          maxRowRight: Math.max(...rows.map((n) => Math.round(n.getBoundingClientRect().right))),
          viewport: innerWidth,
        };
      });
      openShot = path.join(out, 'n06-1440-drawer.png');
      await page.screenshot({ path: openShot });
      clicks = [];
      const labels = drawer.labels.filter((l) => l !== 'Выход из игры');
      for (const label of labels) {
        await page.click('#nav-more');
        await page.waitForTimeout(120);
        const btn = page.locator('#nav-drawer button.nav', { hasText: label }).first();
        if (!(await btn.count())) { clicks.push({ label, screen: 'НЕТ КНОПКИ' }); continue; }
        await btn.click();
        await page.waitForTimeout(350);
        const state = await page.evaluate(() => ({
          active: (document.querySelector('body > main > .view.active') || {}).id || null,
          drawerOpen: document.querySelector('body > aside.sidebar').classList.contains('show-more'),
        }));
        clicks.push({ label, screen: state.active, drawerClosed: !state.drawerOpen });
      }
    } else {
      await page.screenshot({ path: path.join(out, 'n06-375-header.png') });
      await page.click('#mobile-burger');
      await page.waitForTimeout(350);
      drawer = await page.evaluate(() => {
        const side = document.querySelector('body > aside.sidebar');
        const rows = [...side.querySelectorAll('button.nav, a.nav')].filter(window.__vis);
        const rects = rows.map((n) => n.getBoundingClientRect());
        const lefts = [...new Set(rects.map((r) => Math.round(r.left)))];
        const widths = [...new Set(rects.map((r) => Math.round(r.width)))];
        return {
          rows: rows.length,
          labels: rows.map((n) => n.innerText.trim()),
          groups: [...side.querySelectorAll('.nav-group-label')].filter(window.__vis).map((n) => n.innerText.trim()),
          last: rows.length ? rows[rows.length - 1].innerText.trim() : null,
          columns: lefts.length, lefts, widths,
          minHeight: rects.length ? Math.min(...rects.map((r) => Math.round(r.height))) : 0,
          sheetLeft: Math.round(side.getBoundingClientRect().left),
          sheetWidth: Math.round(side.getBoundingClientRect().width),
          scrimShown: getComputedStyle(document.querySelector('.m-scrim')).opacity,
        };
      });
      openShot = path.join(out, 'n06-375-drawer.png');
      await page.screenshot({ path: openShot });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(250);
      const escClosed = await page.evaluate(() => !document.body.classList.contains('mobile-menu-open'));
      /* Шторка занимает весь экран, затемнение под ней недостижимо — закрытие
         строкой «Закрыть», а не тапом мимо. */
      await page.click('#mobile-burger');
      await page.waitForTimeout(250);
      const closeRow = await page.evaluate(() => {
        const b = document.querySelector('aside.sidebar .nav-close');
        if (!b) return { present: false };
        const r = b.getBoundingClientRect();
        return { present: true, text: b.innerText.trim(), height: Math.round(r.height), display: getComputedStyle(b).display };
      });
      await page.click('aside.sidebar .nav-close');
      await page.waitForTimeout(250);
      const closeRowClosed = await page.evaluate(() => ({
        closed: !document.body.classList.contains('mobile-menu-open'),
        burgerFocused: document.activeElement && document.activeElement.id === 'mobile-burger',
        burgerAria: document.querySelector('#mobile-burger').getAttribute('aria-expanded'),
      }));
      drawer.escClosed = escClosed;
      drawer.closeRow = closeRow;
      drawer.closeRowClosed = closeRowClosed;
      drawer.idleRebuilds = idleRebuilds;
    }

    result[name] = { viewport: `${width}x${height}`, strip, emptyButtons: empty, idleRebuilds, drawer, clicks, openShot, errors };
    await context.close();
  }
  await browser.close();
  console.log(JSON.stringify(result, null, 1));
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
