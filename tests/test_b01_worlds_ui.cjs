// B01/F06+F05: имя файла — подсказка, сравнение правил — чтение (приёмка Q13).
//
//   node tests/test_b01_worlds_ui.cjs <base> <temp>
//
// 1. Файл `test-tiamat-08-11.soloworld (1).json` (то же содержимое, что у
//    принятого) импортируется 1 из 1: до импорта экран показывает имя кампании
//    и совместимость, ошибки «не пакет мира» нет.
// 2. Чужое содержимое по-прежнему отвергается — со своей причиной, и кампанию
//    не создаёт.
// 3. «Сравнить с правилами кампании» в кампании Nat20 отдаёт две версии рядом
//    и список различий, а не «Прежний обработчик отключён».
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const [base, temp] = process.argv.slice(2);
const out = process.env.SOLO_UI_EVIDENCE || temp;
fs.mkdirSync(out, { recursive: true });
const PACKAGE = process.env.SOLO_WORLD_PACKAGE;
const FOREIGN = process.env.SOLO_WORLD_FOREIGN;
const WORLD_NAME = process.env.SOLO_WORLD_NAME;
const CAMPAIGN = process.env.SOLO_RULES_CAMPAIGN;

(async () => {
  const browser = await chromium.launch(process.env.SOLO_UI_BROWSER
    ? { executablePath: process.env.SOLO_UI_BROWSER, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] }
    : { channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(45000);
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
  const status = () => page.evaluate(() => document.getElementById('worlds-status').textContent);
  const campaigns = () => page.evaluate(async () => (await (await fetch('/api/campaigns')).json()).length);
  try {
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.SoloWorlds);
    await page.locator('#worlds-upload').waitFor({ state: 'attached' });
    await page.evaluate(() => window.SoloWorlds.open());
    await page.locator('#worlds-status').waitFor({ state: 'attached' });
    const before = await campaigns();

    // 1. Тот же файл под браузерным именем «(1)» — импортируется.
    // Задержка импорта даёт увидеть состояние ДО него: имя и совместимость.
    await page.route('**/api/worlds/import', async route => {
      await new Promise(done => setTimeout(done, 900));
      await route.continue();
    });
    await page.setInputFiles('#worlds-upload', PACKAGE);
    await page.waitForFunction(name => {
      const text = document.getElementById('worlds-status').textContent;
      return text.includes('совместим') && text.includes(name);
    }, WORLD_NAME);
    const preview = await status();
    assert(!preview.toLowerCase().includes('не пакет мира'), 'вернулась ошибка по имени файла: ' + preview);
    assert(preview.includes(WORLD_NAME), 'до импорта нет имени кампании: ' + preview);
    assert(preview.includes('D&D 2024'), 'до импорта нет системы: ' + preview);
    await page.screenshot({ path: path.join(out, 'b01-world-preview-1280.png') });
    await page.waitForFunction(() => document.getElementById('worlds-status').textContent.includes('Кампания:'));
    const loaded = await status();
    assert(loaded.includes(WORLD_NAME), 'после импорта не названа кампания: ' + loaded);
    assert.equal(await campaigns(), before + 1, 'файл с «(1)» не создал кампанию');
    assert(await page.locator('#world-play-imported').count() === 1, 'нет кнопки «Играть в загруженный мир»');
    await page.unroute('**/api/worlds/import');

    // 2. Чужое содержимое отвергается со своей причиной и не создаёт кампанию.
    await page.setInputFiles('#worlds-upload', FOREIGN);
    await page.waitForFunction(() => document.getElementById('worlds-status').className.includes('is-bad'));
    const refusal = await status();
    assert(refusal.includes('soloworld'), 'причина отказа не про формат пакета: ' + refusal);
    assert(!refusal.includes('нужен файл'), 'отказ снова по имени файла: ' + refusal);
    assert.equal(await campaigns(), before + 1, 'чужой файл создал кампанию');
    await page.screenshot({ path: path.join(out, 'b01-world-foreign-1280.png') });

    // 3. Сравнение правил в кампании Nat20: две версии рядом и список различий.
    await page.evaluate(id => {
      localStorage.setItem('solo-campaign', id);
      localStorage.setItem('solo-remember-campaign', '1');
    }, CAMPAIGN);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelector('.nav[data-view="rulesets"]'));
    await page.evaluate(() => document.querySelector('.nav[data-view="rulesets"]').click());
    await page.locator('#rule-preview').waitFor();
    await page.waitForFunction(() => document.querySelectorAll('#ruleset-select option').length > 1);
    await page.evaluate(() => {
      const select = document.getElementById('ruleset-select');
      select.value = 'dnd2014';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await page.evaluate(() => document.getElementById('rule-preview').click());
    await page.waitForFunction(() => {
      const panel = document.getElementById('rulesets');
      return panel && panel.textContent.includes('Что изменится');
    });
    const compare = await page.evaluate(() => document.getElementById('rulesets').textContent);
    assert(!compare.includes('Прежний обработчик отключён'), 'сравнение ушло в отключённый обработчик');
    assert(compare.includes('dnd2024') && compare.includes('dnd2014'), 'нет двух наборов рядом');
    assert(compare.includes('кампании и выбранный набор'), 'нет таблицы «две версии рядом»');
    for (const field of ['Истощение', 'Внезапность', 'Кости после длительного отдыха']) {
      assert(compare.includes(field), 'в списке различий нет поля: ' + field);
    }
    assert(compare.includes('→'), 'различия не показаны парами «было → стало»');
    await page.screenshot({ path: path.join(out, 'b01-rules-compare-1280.png') });

    assert.equal(errors.length, 0, errors.join('|'));
    console.log(JSON.stringify({ checks: 3, preview: preview.slice(0, 120), refusal: refusal.slice(0, 120) }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack || error); process.exit(1); });
