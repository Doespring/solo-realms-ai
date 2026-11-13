// n04: замер главного экрана (home) — до и после.
// node probe_n04_home.cjs <base> <width> <height> <theme> <out.png> [--json out.json]
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');

const [base, width, height, theme, shot, jsonOut] = process.argv.slice(2);

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.SOLO_UI_BROWSER,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  const context = await browser.newContext({
    viewport: { width: Number(width), height: Number(height) },
    locale: 'ru-RU',
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 300)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE ' + m.text().slice(0, 300)); });
  page.on('response', (r) => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url().slice(0, 140)); });
  await context.addInitScript((t) => {
    localStorage.setItem('solo-start-page', 'welcome');
    localStorage.setItem('solo-theme', t);
    localStorage.removeItem('solo-campaign');
    localStorage.removeItem('solo-remember-campaign');
  }, theme);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('.view#welcome'), null, { timeout: 30000 });
  await page.waitForTimeout(1300);
  await page.evaluate(() => document.getAnimations().forEach((a) => a.finish()));
  await page.waitForTimeout(400);

  const info = await page.evaluate(() => {
    // «Видимый» = ненулевой прямоугольник, не скрыт стилем, не за `hidden`,
    // и не обрезан прокручиваемым/скрытым предком. Без последнего строка
    // списка, уехавшая под кромку своего скролла, считалась бы видимой.
    const shown = (n) => {
      const r = n.getBoundingClientRect();
      const s = getComputedStyle(n);
      if (!(r.width > 0 && r.height > 0) || s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) <= 0.05 || n.closest('[hidden]')) return false;
      let top = 0, left = 0, bottom = innerHeight, right = innerWidth;
      for (let p = n.parentElement; p && p !== document.documentElement; p = p.parentElement) {
        const ps = getComputedStyle(p);
        if (/auto|scroll|hidden/.test(ps.overflow + ' ' + ps.overflowY + ' ' + ps.overflowX)) {
          const pr = p.getBoundingClientRect();
          top = Math.max(top, pr.top); left = Math.max(left, pr.left);
          bottom = Math.min(bottom, pr.bottom); right = Math.min(right, pr.right);
        }
      }
      return r.bottom > top + 1 && r.top < bottom - 1 && r.right > left + 1 && r.left < right - 1;
    };
    const inView = (r) => r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
    const active = document.querySelector('.view.active');
    const interactive = [...document.querySelectorAll('button,a[href],select,textarea,input,[role=button],[data-view]')]
      .filter((n) => shown(n) && inView(n.getBoundingClientRect()));
    const accent = interactive.filter((n) => {
      const s = getComputedStyle(n);
      const bg = s.backgroundColor;
      return !/rgba\(0, 0, 0, 0\)|transparent/.test(bg);
    });
    const sizeMap = (root, into) => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const text = (node.textContent || '').trim();
        if (!text) continue;
        const parent = node.parentElement;
        if (!parent || !shown(parent) || !inView(parent.getBoundingClientRect())) continue;
        const size = getComputedStyle(parent).fontSize;
        into[size] = (into[size] || 0) + 1;
      }
      return into;
    };
    const sizes = sizeMap(active, {});
    const pageSizes = sizeMap(document.body, {});
    const rect = active.getBoundingClientRect();
    const box = (sel) => {
      const n = document.querySelector(sel);
      if (!n || !shown(n)) return null;
      const r = n.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.left), y: Math.round(r.top), font: getComputedStyle(n).fontSize, text: (n.innerText || '').trim().slice(0, 120) };
    };
    return {
      documentScrollWidth: document.documentElement.scrollWidth,
      innerWidth,
      innerHeight,
      documentScrollHeight: document.documentElement.scrollHeight,
      bodyScrollWidth: document.body.scrollWidth,
      activeView: active ? active.id : null,
      bodyClass: document.body.className,
      interactiveCount: interactive.length,
      interactive: interactive.map((n) => {
        const r = n.getBoundingClientRect();
        const s = getComputedStyle(n);
        return {
          tag: n.tagName.toLowerCase(),
          text: (n.innerText || n.getAttribute('aria-label') || n.id || '').trim().replace(/\s+/g, ' ').slice(0, 60),
          id: n.id || null,
          cls: n.className || null,
          w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.top),
          font: s.fontSize,
          bg: s.backgroundColor,
        };
      }),
      accentCount: accent.length,
      accent: accent.map((n) => (n.innerText || n.id || '').trim().slice(0, 40)),
      fontSizes: sizes,
      fontKinds: Object.keys(sizes).length,
      pageFontSizes: pageSizes,
      pageFontKinds: Object.keys(pageSizes).length,
      rect: { w: Math.round(rect.width), h: Math.round(rect.height) },
      parts: {
        header: box('body > main > header'),
        eyebrow: box('#welcome .eyebrow'),
        h1: box('#welcome h1'),
        lead: box('#welcome .start__lead'),
        primary: box('#welcome .start__btn--primary'),
        ghost: box('#welcome .start__btn--ghost'),
        continueRow: box('#welcome .start__continue'),
        list: box('#welcome .start__stories'),
        storyList: box('#welcome .start__list'),
        story: box('#welcome .story'),
        kicker: box('#welcome .start__stories-kicker'),
        foot: box('#welcome .start__foot'),
        art: box('#welcome .start__art'),
      },
      text: (active.innerText || '').replace(/\n{2,}/g, '\n').slice(0, 1500),
      offRight: [...document.querySelectorAll('body *')].filter((n) => {
        const r = n.getBoundingClientRect();
        return shown(n) && r.right > innerWidth + 1;
      }).length,
    };
  });
  await page.screenshot({ path: shot, fullPage: false });
  const result = { viewport: width + 'x' + height, theme, errors, ...info };
  if (jsonOut) require('fs').writeFileSync(jsonOut, JSON.stringify(result, null, 1));
  console.log(JSON.stringify(result, null, 1));
  await browser.close();
})().catch((e) => { console.error('FAIL', (e && e.stack) || e); process.exit(1); });
