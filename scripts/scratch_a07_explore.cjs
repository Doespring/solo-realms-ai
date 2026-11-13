// A07: разведка — какие Tab-остановки и селекты есть на живом экране.
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const base = process.argv[2];
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SOLO_UI_BROWSER, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'ru-RU' });
  const page = await context.newPage();
  await context.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', id);
    localStorage.setItem('solo-theme', 'dark');
    localStorage.removeItem('solo-start-page');
  }, process.env.SOLO_CAMPAIGN);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!document.querySelector('#play.active'), null, { timeout: 40000 });
  await page.waitForTimeout(1500);
  const d = (e) => `(() => { const a = document.activeElement; return a ? a.tagName + (a.id ? '#' + a.id : '') + (a.dataset?.view ? '[view=' + a.dataset.view + ']' : '') + (a.getAttribute?.('aria-label') ? '(' + a.getAttribute('aria-label') + ')' : '') + ' · ' + (a.textContent || '').trim().slice(0, 24) : 'null'; })()`;
  const stops = [];
  for (let i = 0; i < 90; i += 1) {
    await page.keyboard.press('Tab');
    await page.waitForTimeout(25);
    stops.push(await page.evaluate(d()));
  }
  const selects = await page.evaluate(() => [...document.querySelectorAll('select')].map((s) => ({ label: s.getAttribute('aria-label'), id: s.id, visible: s.getClientRects().length > 0, options: s.options.length, view: s.closest('.view')?.id || '' })));
  console.log(JSON.stringify({ stops, selects }, null, 1));
  await browser.close();
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
