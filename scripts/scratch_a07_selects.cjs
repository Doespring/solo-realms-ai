// A07: разведка — какой <select> теряет фокус после ArrowDown.
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
  const views = ['play', 'campaigns', 'character', 'master', 'journal', 'inventory', 'rulesets', 'settings', 'cloud', 'world', 'skills'];
  const report = [];
  for (const view of views) {
    const opened = await page.evaluate((id) => {
      const nav = document.querySelector('.nav[data-view="' + id + '"]') || document.querySelector('[data-view="' + id + '"]');
      if (!nav) return false;
      nav.click();
      return true;
    }, view);
    await page.waitForTimeout(700);
    await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true; }));
    await page.waitForTimeout(200);
    const count = await page.evaluate(() => {
      window.__a07 = [...document.querySelectorAll('select')].filter((s) => s.getClientRects().length && s.options.length > 1);
      return window.__a07.length;
    });
    for (let i = 0; i < count; i += 1) {
      const before = await page.evaluate((index) => {
        const s = window.__a07[index];
        if (!s || !s.isConnected) return null;
        s.focus();
        const a = document.activeElement;
        return { label: s.getAttribute('aria-label') || s.id || '(без подписи)', id: s.id, focused: a === s, value: s.value, view: document.querySelector('.view.active')?.id };
      }, i);
      if (!before || !before.focused) { report.push({ view, index: i, skipped: true, before }); continue; }
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(400);
      const after = await page.evaluate((index) => {
        const s = window.__a07[index], a = document.activeElement;
        return { same: a === s, connected: !!s?.isConnected, active: a?.tagName + (a?.id ? '#' + a.id : '') + (a?.getAttribute?.('aria-label') ? '(' + a.getAttribute('aria-label') + ')' : ''), body: a === document.body, valueNow: s?.value };
      }, i);
      report.push({ view, index: i, ...before, ...after });
    }
  }
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
