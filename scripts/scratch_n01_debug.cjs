'use strict';
// Диагностика: что видно на экране игры в 375 после открытия кампании.
const path = require('path');
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || '/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const [BASE, CID] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SOLO_UI_BROWSER, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, locale: 'ru-RU' });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR', String(e).slice(0, 300)));
  page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)); });
  await page.addInitScript((id) => { localStorage.setItem('solo-campaign', id); localStorage.setItem('solo-remember-campaign', 'on'); }, CID);
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.SoloGameScreen === 'object', { timeout: 30000 });
  const start = page.locator('#welcome .start__btn--primary');
  if (await start.count()) { await start.first().click({ timeout: 5000 }).catch(() => {}); await page.waitForTimeout(600); }
  if (!(await page.evaluate(() => !!document.querySelector('#play.active')))) {
    const nav = page.locator('.nav[data-view="play"], [data-view="play"]').first();
    if (await nav.count()) await nav.click({ timeout: 8000 }).catch(() => {});
  }
  await page.waitForSelector('#play.active', { timeout: 25000 });
  await page.waitForTimeout(3000);
  const info = await page.evaluate(() => {
    const vis = (el) => !!(el && el.offsetParent !== null && !el.hidden);
    const buttons = [...document.querySelectorAll('#play button')].filter(vis).map((b) => (b.textContent || '').trim().slice(0, 60) + (b.disabled ? ' [off]' : ''));
    return { view: document.querySelector('.view.active')?.id, buttons: buttons.slice(0, 30),
      feedback: (document.getElementById('game-feedback')?.textContent || '').trim().slice(0, 300),
      actionsRow: !!document.querySelector('.game-actions'), actionsHidden: document.querySelector('.game-actions')?.hidden ?? null,
      snapshot: window.SoloGameScreen?.packet?.snapshot ? Object.keys(window.SoloGameScreen.packet.snapshot) : null };
  });
  console.log(JSON.stringify(info, null, 1));
  await page.screenshot({ path: path.resolve('reports/n01/debug2-375.png'), fullPage: true });
  await browser.close();
})().catch((e) => { console.error('FAIL', e && e.stack || e); process.exit(1); });
