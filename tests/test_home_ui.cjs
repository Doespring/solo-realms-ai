// n04: главная — один призыв, одна строка состояния, список историй.
// Запуск: node tests/test_home_ui.cjs http://127.0.0.1:<порт> [http://127.0.0.1:<порт-пустой>]
// Браузер: SOLO_UI_BROWSER (готовый Chrome) или канал msedge, как в остальных тестах.
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');

const [base, emptyBase] = process.argv.slice(2);

async function inspect(browser, url, width, height, ready) {
  const context = await browser.newContext({ viewport: { width, height }, locale: 'ru-RU' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error).slice(0, 200)));
  page.on('response', (response) => {
    if (response.status() >= 400) errors.push(response.status() + ' ' + response.url().slice(0, 120));
  });
  await context.addInitScript(() => {
    localStorage.setItem('solo-start-page', 'welcome');
    localStorage.setItem('solo-theme', 'dark');
    localStorage.removeItem('solo-campaign');
  });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction((selector) => !!document.querySelector(selector), ready, { timeout: 30000 });
  await page.waitForTimeout(900);
  const state = await page.evaluate(() => {
    // Видимое = есть прямоугольник, не скрыто стилем и не обрезано своим скроллом.
    const shown = (node) => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      if (!(rect.width > 1 && rect.height > 1)) return false;
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) <= 0.05) return false;
      if (node.closest('[hidden]')) return false;
      let top = 0, left = 0, bottom = innerHeight, right = innerWidth;
      for (let parent = node.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
        const box = getComputedStyle(parent);
        if (/auto|scroll|hidden/.test(box.overflow + ' ' + box.overflowY + ' ' + box.overflowX)) {
          const own = parent.getBoundingClientRect();
          top = Math.max(top, own.top); left = Math.max(left, own.left);
          bottom = Math.min(bottom, own.bottom); right = Math.min(right, own.right);
        }
      }
      return rect.bottom > top + 1 && rect.top < bottom - 1 && rect.right > left + 1 && rect.left < right - 1;
    };
    const interactive = [...document.querySelectorAll('button,a[href],select,textarea,input,[role=button],[data-view]')].filter(shown);
    const accent = interactive.filter((node) => !/rgba\(0, 0, 0, 0\)|transparent/.test(getComputedStyle(node).backgroundColor));
    const sizes = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if ((node.textContent || '').trim() && parent && shown(parent)) sizes.add(getComputedStyle(parent).fontSize);
    }
    const content = document.querySelector('#welcome .start__feed') || document.querySelector('#welcome');
    const rect = content.getBoundingClientRect();
    return {
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth,
      view: (document.querySelector('.view.active') || {}).id,
      interactive: interactive.length,
      accent: accent.length,
      accentText: accent.map((node) => (node.innerText || '').trim().slice(0, 30)),
      sizes: [...sizes].sort(),
      contentWidth: Math.round(rect.width),
      contentCenter: Math.round(rect.left + rect.width / 2),
      viewportCenter: Math.round(innerWidth / 2),
      title: (document.querySelector('#welcome h1') || {}).innerText,
      lead: (document.querySelector('#welcome .start__lead') || {}).innerText,
      resume: (document.querySelector('#welcome .start__continue:not([hidden])') || {}).innerText || null,
      stories: document.querySelectorAll('#welcome .story').length,
      empty: (document.querySelector('#welcome .start__empty:not([hidden])') || {}).innerText || null,
      foot: (document.querySelector('#welcome .start__local') || {}).innerText || null,
    };
  });
  await context.close();
  return { ...state, errors };
}

(async () => {
  const browser = await chromium.launch(process.env.SOLO_UI_BROWSER
    ? { executablePath: process.env.SOLO_UI_BROWSER, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] }
    : { channel: 'msedge', headless: true });
  const checks = [];
  const ok = (name, passed, detail) => checks.push({ name, passed, detail });

  try {
    const phone = await inspect(browser, base, 375, 812, '#welcome .story');
    ok('375: нет горизонтальной прокрутки (scrollWidth == 375)', phone.scrollWidth === 375, phone.scrollWidth);
    ok('375: ровно один призыв с акцентом', phone.accent === 1, phone.accentText);
    ok('375: кеглей на экране не больше трёх', phone.sizes.length <= 3, phone.sizes);
    ok('375: видимых интерактивных не больше пяти', phone.interactive <= 5, phone.interactive);
    ok('375: есть заголовок, вводная, «Продолжить», строка состояния', !!phone.title && !!phone.lead && !!phone.resume && !!phone.foot,
      { title: phone.title, resume: phone.resume, foot: phone.foot });
    ok('375: истории списком, без ошибок страницы', phone.stories >= 1 && phone.errors.length === 0, phone.errors);

    const wide = await inspect(browser, base, 1440, 900, '#welcome .story');
    ok('1440: контент не шире 960', wide.contentWidth <= 960, wide.contentWidth);
    ok('1440: контент по центру', Math.abs(wide.contentCenter - wide.viewportCenter) <= 2, [wide.contentCenter, wide.viewportCenter]);
    ok('1440: ровно один призыв с акцентом', wide.accent === 1, wide.accentText);
    ok('1440: без ошибок страницы', wide.errors.length === 0, wide.errors);

    if (emptyBase) {
      const empty = await inspect(browser, emptyBase, 375, 812, '#welcome .start__empty:not([hidden])');
      ok('пусто: одна фраза и тот же один призыв',
        empty.empty === 'У вас пока нет историй. Начните с книги или загрузите свою' && empty.accent === 1 && empty.stories === 0,
        { empty: empty.empty, accent: empty.accentText, stories: empty.stories });
    }
  } finally {
    await browser.close();
  }

  for (const check of checks) console.log((check.passed ? 'ok   ' : 'FAIL ') + check.name + ' — ' + JSON.stringify(check.detail));
  const failed = checks.filter((check) => !check.passed);
  console.log((checks.length - failed.length) + ' passed, ' + failed.length + ' failed');
  if (failed.length) process.exit(1);
})().catch((error) => { console.error('FAIL', (error && error.stack) || error); process.exit(1); });
