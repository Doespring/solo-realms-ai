// A01: живая кнопка «Создать резервную копию» на экране «Настройки → Данные и копии».
// node scripts/probe_a01_ui.cjs <base-url> <out-dir>
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const [base, out] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SOLO_UI_BROWSER, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'ru-RU' });
  await context.addInitScript(() => localStorage.setItem('solo-theme', 'dark'));
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE ' + m.text().slice(0, 200)); });

  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('.nav[data-view="settings"]'));
  await page.evaluate(() => document.querySelector('.nav[data-view="settings"]').click());
  await page.waitForSelector('#settings .settings-tabs button');
  const tab = await page.evaluate(() => {
    const found = [...document.querySelectorAll('#settings .settings-tabs button')].find((b) => b.textContent.trim() === 'Данные и копии');
    if (found) found.click();
    return found ? found.textContent.trim() : null;
  });
  await page.waitForSelector('#settings .settings-page[data-settings-pane="data"]:not([hidden]) button');
  const before = await page.evaluate(() => document.querySelector('#settings .settings-page[data-settings-pane="data"]').innerText);

  await page.evaluate(() => {
    const pane = document.querySelector('#settings .settings-page[data-settings-pane="data"]');
    [...pane.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Создать резервную копию').click();
  });
  await page.waitForFunction(() => {
    const t = document.getElementById('toast');
    return t && !t.hidden && /Копия создана/.test(t.textContent);
  });
  const toast = await page.evaluate(() => document.getElementById('toast').textContent);
  const after = await page.evaluate(() => document.querySelector('#settings .settings-page[data-settings-pane="data"]').innerText);
  await page.screenshot({ path: path.join(out, 'a01-backup-button-1280.png'), fullPage: false });

  console.log(JSON.stringify({ tab, before, toast, after, errors }, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
