// n05: прогон создания героя на телефоне. Один вопрос — один экран.
// node scripts/probe_n05_flow.cjs <base> <out-dir> <class-id> <tag>
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const [base, out, classId, tag, mode, theme] = process.argv.slice(2);
const clean = mode === 'clean';
const skin = theme || 'dark';
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.SOLO_UI_BROWSER, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, locale: 'ru-RU', deviceScaleFactor: 1 });
  await context.addInitScript((value) => { localStorage.setItem('solo-theme', value); }, skin);
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE ' + m.text().slice(0, 200)); });

  const waitIdle = () => page.waitForFunction(() => {
    const root = document.getElementById('wizard');
    return root && root.getAttribute('aria-busy') === 'false';
  });
  const state = () => page.evaluate(() => window.soloWizard.state());
  const current = async () => { const live = await state(); return live.screens[live.screen]; };
  const measure = () => page.evaluate(() => {
    const stage = document.querySelector('#wizard .wizard-stage');
    const foot = document.querySelector('#wizard .wizard-foot');
    const need = document.querySelector('#wizard .wizard-need');
    const next = document.querySelector('#wizard .wizard-foot button.primary');
    const rect = foot.getBoundingClientRect();
    const options = [...document.querySelectorAll('#wizard .wizard-stage .wizard-option')];
    const heights = options.map((n) => Math.round(n.getBoundingClientRect().height));
    const chosen = document.querySelector('#wizard .wizard-stage .wizard-option.is-chosen');
    const chosenStyle = chosen ? getComputedStyle(chosen) : null;
    const title = document.querySelector('#wizard .wizard-question');
    const dots = [...document.querySelectorAll('#wizard .wizard-list li')];
    return {
      counter: (document.querySelector('#wizard .wizard-progress') || {}).textContent || '',
      question: title ? title.textContent : '',
      docScrollWidth: document.documentElement.scrollWidth,
      stageScrollWidth: stage.scrollWidth,
      stageScrollHeight: stage.scrollHeight,
      stageClientHeight: stage.clientHeight,
      footerBottom: Math.round(rect.bottom),
      viewport: innerHeight,
      nextLabel: next ? next.textContent.trim() : null,
      nextInView: next ? next.getBoundingClientRect().bottom <= innerHeight + 0.5 && next.getBoundingClientRect().top >= 0 : false,
      nextEnabled: next ? !next.disabled : null,
      need: need && !need.hidden ? need.textContent.trim() : '',
      options: options.length,
      minOptionHeight: heights.length ? Math.min(...heights) : null,
      dots: dots.length,
      chosenBorder: chosenStyle ? chosenStyle.borderTopColor : null,
      chosenBackground: chosenStyle ? chosenStyle.backgroundColor : null,
      detailFont: (() => { const d = document.querySelector('#wizard .wizard-stage .wizard-option-detail'); return d ? getComputedStyle(d).fontSize : null; })(),
    };
  });
  const shot = async (name) => page.screenshot({ path: path.join(out, `${tag}-${name}.png`) });
  const goto = async (target) => { console.error('[goto]', target); await page.evaluate((id) => window.soloWizard.goto(id), target); await waitIdle(); };
  const choose = async (id) => {
    const node = page.locator(`#wizard .wizard-stage .wizard-option[data-id="${id}"]`).first();
    if (!(await node.count())) return false;
    if ((await node.getAttribute('aria-checked')) === 'true') return false;
    await node.click();
    await waitIdle();
    return true;
  };
  // Добить обязательные выборы, которые видит текущий экран (навыки, черты, ячейки).
  async function completeScreen() {
    for (let guard = 0; guard < 40; guard++) {
      const info = await page.evaluate(() => {
        const live = window.soloWizard.state();
        const parts = [...document.querySelectorAll('#wizard .wizard-part[data-choice]')];
        for (const part of parts) {
          const key = part.dataset.choice;
          const step = (live.contract.steps || []).find((row) => row.choice_key === key && row.status !== 'complete');
          if (!step) continue;
          if (step.kind === 'increase' || step.kind === 'asi') {
            if (part.querySelectorAll('.wizard-score button:not([disabled])').length) return { key, kind: 'increase' };
            continue;
          }
          const free = part.querySelectorAll('.wizard-option:not(.is-chosen):not([disabled])');
          if (free.length) return { key, kind: step.kind || '' };
        }
        return null;
      });
      console.error('[fill]', JSON.stringify(info));
      if (!info) return;
      const part = `#wizard .wizard-part[data-choice="${info.key}"]`;
      if (info.kind === 'increase' || info.kind === 'asi') {
        const plus = page.locator(`${part} .wizard-score button:text-is("+"):not([disabled])`).first();
        if (!(await plus.count())) return;
        await plus.click();
        await waitIdle();
        continue;
      }
      await page.locator(`${part} .wizard-option:not(.is-chosen)`).first().click();
      await waitIdle();
    }
  }
  async function fillScreen(id) {
    if (id === 'origin') await choose('Human');
    else if (id === 'class') await choose(classId);
    else if (id === 'background') await choose('soldier');
    else if (id === 'magic') {
      for (let guard = 0; guard < 40; guard++) {
        const missing = await page.evaluate(() => {
          const step = (window.soloWizard.state().contract.steps || []).find((row) => row.id === 'magic');
          return step ? Number(step.missing || 0) : 0;
        });
        if (!missing) break;
        const free = page.locator('#wizard .wizard-part[data-step="magic"] .wizard-option:not(.is-chosen):not([disabled])').first();
        if (!(await free.count())) break;
        await free.click();
        await waitIdle();
      }
    }
    else if (id === 'scores') {
      await choose('standard_array');
      const filled = await page.evaluate(() => Object.keys(window.soloWizard.state().draft.ability_scores || {}).length);
      if (!filled) {
        for (const value of [15, 14, 13, 12, 10, 8]) {
          const chip = page.locator('#wizard .wizard-stage .wizard-option').filter({ hasText: new RegExp('^' + value + '$') }).first();
          if (await chip.count()) { await chip.click(); await waitIdle(); }
        }
      }
    }
    await completeScreen();
  }

  const started = Date.now();
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.soloWizard);
  await page.evaluate(() => { localStorage.removeItem('solo-wizard-draft'); localStorage.removeItem('solo-wizard-pending'); localStorage.removeItem('solo-campaign'); });
  await page.evaluate(() => window.soloWizard.open());
  await page.locator('#wizard .wizard-shell').waitFor();
  await waitIdle();

  const report = { tag, moves: 0, screens: [] };
  report.plan = (await state()).screens;
  const visited = new Map();

  const walk = async (passes) => {
    for (let pass = 0; pass < passes; pass++) {
      const plan = (await state()).screens.map((row) => row.id);
      for (const id of plan) {
        await goto(id);
        if (!visited.has(id)) {
          visited.set(id, true);
          report.screens.push(Object.assign({ at: id }, await measure()));
          if (!clean) await shot('0' + report.screens.length + '-' + id);
        }
        await fillScreen(id);
        if (id === 'scores' && !clean && !visited.has('scores-filled')) {
          visited.set('scores-filled', true);
          await page.evaluate(() => { document.querySelector('#wizard .wizard-stage').scrollTop = 0; });
          report.screens.push(Object.assign({ at: 'scores-filled' }, await measure()));
          await shot('04b-scores-filled');
        }
      }
      await goto('gear');
      const button = page.locator('#wizard .wizard-foot button.primary');
      if (await button.isEnabled()) break;
    }
  };
  await walk(3);

  // Готово: снимок, имя, создание
  await goto('gear');
  report.screens.push(Object.assign({ at: 'gear-final' }, await measure()));
  if (!clean) await shot('05-gear');
  await page.locator('#wizard .wizard-field input[type=text]').fill('Мирон');
  await page.locator('#wizard .wizard-field input[type=text]').dispatchEvent('change');
  await waitIdle();
  report.readyNamed = await measure();
  if (!clean) { await page.evaluate(() => { document.querySelector('#wizard .wizard-stage').scrollTop = 0; }); await shot('05c-gear-named'); }

  await page.locator('#wizard .wizard-foot button.primary').click();
  await page.waitForFunction(() => document.getElementById('play') && document.getElementById('play').classList.contains('active'), null, { timeout: 60000 });
  report.elapsedSeconds = Math.round((Date.now() - started) / 100) / 10;
  await page.waitForTimeout(4000);
  if (!clean) await shot('06-play');
  report.play = await page.evaluate(() => ({
    active: document.getElementById('play').classList.contains('active'),
    wizardHidden: document.getElementById('wizard') ? document.getElementById('wizard').hidden : true,
    textLength: (document.getElementById('play') || {}).innerText ? document.getElementById('play').innerText.trim().length : 0,
    firstLines: ((document.getElementById('play') || {}).innerText || '').trim().split('\n').slice(0, 5),
    campaign: localStorage.getItem('solo-campaign'),
    inputVisible: !!document.querySelector('#play textarea, #play input[type=text]'),
  }));
  report.errors = errors;
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
})();
