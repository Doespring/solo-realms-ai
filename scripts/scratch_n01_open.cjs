'use strict';
// Диагностика n01: почему экран игры не открывается в 375.
const path = require('path');
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || '/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const [BASE, CID] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SOLO_UI_BROWSER, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, locale: 'ru-RU' });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR', String(e).slice(0, 400)));
  page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 400)); });
  page.on('response', (r) => { const s = r.status(); if (s >= 400) console.log('HTTP', s, r.request().method(), r.url().slice(0, 140)); });
  await page.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', 'on');
    localStorage.setItem('solo-start-page', 'campaign');
  }, CID);
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.SoloGameScreen === 'object', { timeout: 30000 });
  await page.waitForTimeout(3000);
  const info = await page.evaluate(() => {
    const active = [...document.querySelectorAll('.view')].filter((v) => v.classList.contains('active')).map((v) => v.id);
    const vis = (el) => !!(el && el.offsetParent !== null && !el.hidden);
    return {
      title: document.title,
      active,
      bodyClass: document.body.className,
      welcomeButtons: [...document.querySelectorAll('#welcome button')].filter(vis).map((b) => (b.textContent || '').trim().slice(0, 50)),
      playVisible: vis(document.getElementById('play')),
      feedback: (document.getElementById('game-feedback') || {}).textContent,
      playerInput: !!document.getElementById('player-input'),
      nav: [...document.querySelectorAll('.nav')].map((b) => b.dataset.view + ':' + b.className),
    };
  });
  console.log(JSON.stringify(info, null, 1));
  await page.screenshot({ path: path.join('reports/n01/evidence', 'debug-open-375.png') });
  await browser.close();
})().catch((e) => { console.error('FAIL', e && e.stack || e); process.exit(1); });
