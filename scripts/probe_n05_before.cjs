// n05 baseline: как редактор героя выглядел на 375 до правки.
// node scripts/probe_n05_before.cjs <base> <out-dir>
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const [base, out] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SOLO_UI_BROWSER, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, locale: 'ru-RU', deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 200)));
  const waitIdle = () => page.waitForFunction(() => {
    const root = document.getElementById('wizard');
    return root && root.getAttribute('aria-busy') === 'false';
  });
  const measure = () => page.evaluate(() => {
    const stage = document.querySelector('#wizard .wizard-stage');
    const foot = document.querySelector('#wizard .wizard-foot');
    const nav = document.querySelector('#wizard .wizard-steps');
    const options = [...document.querySelectorAll('#wizard .wizard-stage .wizard-option')];
    const rect = foot.getBoundingClientRect();
    const heights = options.map((n) => Math.round(n.getBoundingClientRect().height));
    const chosen = document.querySelector('#wizard .wizard-stage .wizard-option.is-chosen');
    const chosenStyle = chosen ? getComputedStyle(chosen) : null;
    return {
      docScrollWidth: document.documentElement.scrollWidth,
      stageScrollWidth: stage.scrollWidth,
      stageClientHeight: stage.clientHeight,
      stageScrollHeight: stage.scrollHeight,
      needsScrollToNext: rect.bottom > innerHeight + 0.5 || rect.top < 0,
      footerBottom: Math.round(rect.bottom),
      viewport: innerHeight,
      navHeight: nav ? Math.round(nav.getBoundingClientRect().height) : 0,
      options: options.length,
      optionHeights: heights.slice(0, 4),
      minOptionHeight: heights.length ? Math.min(...heights) : null,
      title: (document.querySelector('#wizard .wizard-stage-head h2, #wizard .wizard-stage h2') || {}).textContent || '',
      chosenOutline: chosenStyle ? chosenStyle.outlineStyle + ' ' + chosenStyle.outlineColor + ' ' + chosenStyle.borderColor : null,
      chosenBackground: chosenStyle ? chosenStyle.backgroundColor : null,
      footerButtons: [...document.querySelectorAll('#wizard .wizard-foot button')].filter((n) => n.offsetParent !== null).map((n) => n.textContent.trim() + (n.disabled ? '(off)' : '')),
    };
  });

  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.soloWizard);
  await page.evaluate(() => { localStorage.clear(); });
  await page.evaluate(() => window.soloWizard.open());
  await page.locator('#wizard .wizard-shell').waitFor();
  await waitIdle();
  const contract = await page.evaluate(() => window.soloWizard.state().contract);
  const report = { screensInContract: contract.steps.length, ids: contract.steps.map((s) => s.id), steps: [] };
  report.steps.push(Object.assign({ at: 'first' }, await measure()));
  await page.screenshot({ path: path.join(out, 'before-375-step1.png') });
  // Вид → Эльф (появится наследие) — снимок второго экрана
  await page.locator('#wizard .wizard-stage .wizard-option[data-id="Elf"]').click();
  await waitIdle();
  report.steps.push(Object.assign({ at: 'species-chosen' }, await measure()));
  await page.screenshot({ path: path.join(out, 'before-375-step2.png') });
  // Характеристики: до выбора способа
  const idx = contract.steps.filter((s) => s.id !== 'equipment').findIndex((s) => s.id === 'scores');
  await page.locator('#wizard .wizard-list button').nth(idx).click();
  await waitIdle();
  report.steps.push(Object.assign({ at: 'scores' }, await measure()));
  await page.screenshot({ path: path.join(out, 'before-375-scores.png') });
  report.errors = errors;
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
})();
