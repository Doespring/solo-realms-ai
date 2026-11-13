'use strict';
// n02: профиль мастера, сохранённый прежней версией (значение поля вне
// допустимых), не ломает чтение библиотеки, открывается в редакторе и
// сохраняется после того, как игрок выбрал значение заново.
//
//   node scripts/probe_n02_master.cjs <base> <profile> <campaign> <outdir>

const path = require('path');
const fs = require('node:fs');
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || path.join('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules', 'playwright'));

const [BASE, PROFILE, CAMPAIGN, OUT = 'reports/n02/live'] = process.argv.slice(2);
const WIDTH = 375, HEIGHT = 812;
const CHROME = process.env.SOLO_UI_BROWSER || '/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome';
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const report = { profile: PROFILE, listStatus: null, repair: '', rhythmStored: null, toastEdit: '', saveStatus: null, toastSave: '', ok: false };
  try {
    const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT } });
    const listed = await context.request.get(BASE + '/api/master/profiles');
    report.listStatus = listed.status();
    const payload = await listed.json().catch(() => null);
    report.repair = (Array.isArray(payload) ? payload.find((row) => row.id === PROFILE)?.repair : '') || '';

    const page = await context.newPage();
    page.setDefaultTimeout(40000);
    page.on('pageerror', (e) => console.log('PAGEERROR', e.message));
    // Глобальный view() заменён роутером кампании: без открытой кампании раздел
    // «Мастер» не открывается вовсе (это и есть поведение игрока).
    await page.addInitScript((campaign) => {
      localStorage.setItem('solo-remember-campaign', 'on');
      localStorage.setItem('solo-campaign', campaign);
    }, CAMPAIGN);
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('#play.active', { timeout: 30000 }).catch(() => {});
    // На 375 меню свёрнуто: кнопка в DOM есть, но не видима — ждём появления, кликаем из JS.
    await page.waitForFunction(() => !!document.querySelector('.nav[data-view="master"]'), { timeout: 30000 });
    await page.evaluate(() => document.querySelector('.nav[data-view="master"]').click());
    await page.waitForFunction(() => document.querySelectorAll('#profile-select option').length > 1, { timeout: 30000 });
    await page.waitForFunction((id) => [...document.querySelectorAll('#profile-select option')].some((o) => o.value === id), PROFILE, { timeout: 20000 });
    await page.selectOption('#profile-select', PROFILE);
    await page.evaluate(() => document.querySelector('#profile-edit').click());
    await page.waitForTimeout(500);
    report.rhythmStored = await page.evaluate(() => document.querySelector('#literary-rhythm').value);
    report.toastEdit = (await page.textContent('#toast') || '').trim();
    // Скриншот именно с пояснением про значения прежней версии: он и есть
    // обещанный «экран с новым текстом отказа».
    await page.screenshot({ path: path.join(OUT, 'n02-05-master-repair-375.png'), fullPage: false });
    // Игрок выбирает допустимое значение и сохраняет профиль.
    // Панель «Литературность» свёрнута в <details>: у select нет видимости,
    // поэтому значение ставится так же, как его берёт readMaster.
    await page.evaluate(() => { const node = document.querySelector('#literary-rhythm'); node.value = 'сбалансированный'; node.dispatchEvent(new Event('change', { bubbles: true })); });
    const [saved] = await Promise.all([
      page.waitForResponse((r) => r.request().method() === 'PUT' && r.url().includes('/api/master/profiles/' + PROFILE), { timeout: 30000 }).catch(() => null),
      page.evaluate(() => document.querySelector('#profile-update').click()),
    ]);
    report.saveStatus = saved ? saved.status() : null;
    await page.waitForTimeout(700);
    report.toastSave = (await page.textContent('#toast') || '').trim();
    await page.screenshot({ path: path.join(OUT, 'n02-04-master-profile-375.png'), fullPage: false });
    await context.close();
    report.ok = report.listStatus === 200 && /темп повествования/.test(report.repair)
      && /прежней версии/.test(report.toastEdit) && report.saveStatus === 200;
    console.log('REPORT', JSON.stringify(report, null, 1));
    fs.writeFileSync(path.join(OUT, 'n02-master.json'), JSON.stringify(report, null, 1), 'utf8');
    process.exitCode = report.ok ? 0 : 1;
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error('PROBE FAILED', error && error.stack || error); process.exit(1); });
