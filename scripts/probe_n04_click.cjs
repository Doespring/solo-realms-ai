// n04: живой клик по главной — «Продолжить», прошлая история, «Начать историю».
// node probe_n04_click.cjs <base-list> <base-empty>
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');

const [listBase, emptyBase] = process.argv.slice(2);

async function open(browser, base, width, height, ready = '#welcome .story') {
  const context = await browser.newContext({ viewport: { width, height }, locale: 'ru-RU' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 200)));
  page.on('response', (r) => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url().slice(0, 120)); });
  await context.addInitScript(() => {
    localStorage.setItem('solo-start-page', 'welcome');
    localStorage.setItem('solo-theme', 'dark');
    localStorage.removeItem('solo-campaign');
  });
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction((sel) => !!document.querySelector(sel), ready, { timeout: 30000 });
  await page.waitForTimeout(900);
  return { context, page, errors };
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.SOLO_UI_BROWSER,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  const out = {};

  // 1. «Продолжить» ведёт в игру последней кампании.
  {
    const { context, page, errors } = await open(browser, listBase, 375, 812);
    const name = await page.locator('#welcome .start__continue .start__continue-title').innerText();
    await page.locator('#welcome .start__continue').click();
    await page.waitForFunction(() => document.querySelector('#play.active'), null, { timeout: 20000 });
    await page.waitForTimeout(1200);
    out.resume = {
      name,
      view: await page.evaluate(() => document.querySelector('.view.active').id),
      campaign: await page.evaluate(() => localStorage.getItem('solo-campaign')),
      title: await page.evaluate(() => (document.querySelector('.m-title') || {}).textContent),
      errors,
    };
    await context.close();
  }

  // 2. Строка прошлой истории открывает СВОЮ кампанию, а не последнюю.
  {
    const { context, page, errors } = await open(browser, listBase, 375, 812);
    const target = await page.evaluate(() => {
      const row = document.querySelectorAll('#welcome .story')[0];
      return { title: row.querySelector('.story-title').textContent, meta: row.querySelector('.story-meta').innerText.replace(/\n/g, ' · ') };
    });
    await page.evaluate(() => document.querySelectorAll('#welcome .story')[0].click());
    await page.waitForFunction(() => document.querySelector('#play.active'), null, { timeout: 20000 });
    await page.waitForTimeout(1200);
    out.past = {
      ...target,
      view: await page.evaluate(() => document.querySelector('.view.active').id),
      campaign: await page.evaluate(() => localStorage.getItem('solo-campaign')),
      title: await page.evaluate(() => (document.querySelector('.m-title') || {}).textContent),
      errors,
    };
    await context.close();
  }

  // 3. Пустое состояние: фраза на месте, «Начать историю» открывает диалог.
  {
    const { context, page, errors } = await open(browser, emptyBase, 375, 812, '#welcome .start__empty:not([hidden])');
    const phrase = await page.locator('#welcome .start__empty').innerText();
    await page.locator('#welcome .start__btn--primary').click();
    await page.waitForTimeout(600);
    out.empty = {
      phrase,
      activeAfterClick: await page.evaluate(() => (document.querySelector('.view.active') || {}).id),
      heading: await page.evaluate(() => (document.querySelector('.view.active h1') || {}).textContent),
      resumeRows: await page.evaluate(() => document.querySelectorAll('#welcome .story, #welcome .start__continue:not([hidden])').length),
      errors,
    };
    await context.close();
  }

  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch((e) => { console.error('FAIL', (e && e.stack) || e); process.exit(1); });
