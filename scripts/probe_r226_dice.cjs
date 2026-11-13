'use strict';
// r226: приёмка знака кубика в живом приложении.
// Что меряем: размер знака в ленте (36 ± 1), число внутри грани, цвет исхода из
// токенов темы, экран броска 120 px, обе темы, 375 (DPR 2) и 1440.
// Скриншоты — в каталог OUT, замеры — в OUT/report.json.
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = process.argv[2] || 'http://127.0.0.1:19226/';
const CID = process.argv[3];
const OUT = process.argv[4] || 'reports/r226';
const CHROME = process.env.R226_CHROME;

fs.mkdirSync(OUT, { recursive: true });
const report = { base: BASE, themes: {}, errors: [], ok: false };

// Контраст исходов считаем по WCAG из живых computed-цветов.
const parseRgb = (s) => {
  const m = String(s || '').match(/(\d+(?:\.\d+)?)[,\s]+(\d+(?:\.\d+)?)[,\s]+(\d+(?:\.\d+)?)(?:[,\s/]+([\d.]+))?/);
  return m ? { rgb: [+m[1], +m[2], +m[3]], alpha: m[4] === undefined ? 1 : Number(m[4]) } : null;
};
const relLum = (rgb) => {
  const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
};
const contrast = (a, b) => {
  const la = relLum(a), lb = relLum(b);
  return +(((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)).toFixed(2));
};

const probe = () => {
  const roll = document.querySelector('#chat .roll, #play .roll');
  if (!roll) return { found: false };
  const svg = roll.querySelector('svg.die');
  const num = roll.querySelector('.die__num');
  const box = roll.querySelector('.roll__die');
  const use = svg && svg.querySelector('use');
  const rect = (n) => {
    if (!n) return null;
    const b = n.getBoundingClientRect();
    return { w: +b.width.toFixed(2), h: +b.height.toFixed(2), x: Math.round(b.x), y: Math.round(b.y) };
  };
  const rx = (n) => { // видимые чернила символа: bbox самого <use>
    if (!n) return null;
    try { const b = n.getBBox(); return { w: +b.width.toFixed(2), h: +b.height.toFixed(2) }; } catch (e) { return null; }
  };
  const cs = svg ? getComputedStyle(svg) : null;
  const numStyle = num ? getComputedStyle(num) : null;
  // Число меряем по отрисованному прямоугольнику (с учётом CSS-transform),
  // а не по getBBox: bbox текста — это em-квадрат со спуском, он шире чернил.
  const numRect = num ? num.getBoundingClientRect() : null;
  const svgRect = svg ? svg.getBoundingClientRect() : null;
  const numBox = numRect ? { w: +numRect.width.toFixed(2), h: +numRect.height.toFixed(2), x: +(numRect.x - svgRect.x).toFixed(2), y: +(numRect.y - svgRect.y).toFixed(2) } : null;
  const dieBox = svgRect ? { w: +svgRect.width.toFixed(2), h: +svgRect.height.toFixed(2), x: 0, y: 0 } : null;
  const verdict = roll.querySelector('.roll__verdict');
  return {
    found: true,
    href: use ? (use.getAttribute('href') || use.getAttribute('xlink:href')) : null,
    viewBox: svg ? svg.getAttribute('viewBox') : null,
    box: rect(box),
    svg: rect(svg),
    useInk: rx(use),
    color: cs ? cs.color : null,
    class: svg ? svg.getAttribute('class') : null,
    number: num ? num.textContent : null,
    numberFontSize: numStyle ? numStyle.fontSize : null,
    numberInk: numBox,
    numberInsideHull: !!(numBox && dieBox && numBox.x >= dieBox.x - 1 && numBox.x + numBox.w <= dieBox.x + dieBox.w + 1 && numBox.y >= dieBox.y - 1 && numBox.y + numBox.h <= dieBox.y + dieBox.h + 1),
    figures: roll.querySelector('.roll__need') ? roll.querySelector('.roll__need').innerText.replace(/\s+/g, ' ').trim() : null,
    total: roll.querySelector('.roll__sum') ? roll.querySelector('.roll__sum').innerText.replace(/\s+/g, ' ').trim() : null,
    verdict: verdict ? { text: verdict.textContent, color: getComputedStyle(verdict).color, out: verdict.dataset.out } : null,
    rowText: roll.innerText.replace(/\s+/g, ' ').trim(),
    horizontalScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };
};

const probeView = () => {
  const d = document.querySelector('dialog.roll-view');
  if (!d) return { found: false };
  const svg = d.querySelector('svg.die');
  const num = d.querySelector('.die__num');
  const b = svg ? svg.getBoundingClientRect() : null;
  const numRect = num ? num.getBoundingClientRect() : null;
  const svgRect = svg ? svg.getBoundingClientRect() : null;
  const nb = numRect ? { w: +numRect.width.toFixed(2), h: +numRect.height.toFixed(2), x: +(numRect.x - svgRect.x).toFixed(2), y: +(numRect.y - svgRect.y).toFixed(2) } : null;
  const hb = svgRect ? { w: +svgRect.width.toFixed(2), h: +svgRect.height.toFixed(2), x: 0, y: 0 } : null;
  const use = svg && svg.querySelector('use');
  return {
    found: true,
    open: d.open,
    href: use ? (use.getAttribute('href') || use.getAttribute('xlink:href')) : null,
    size: b ? { w: +b.width.toFixed(2), h: +b.height.toFixed(2) } : null,
    number: num ? num.textContent : null,
    numberFontSize: num ? getComputedStyle(num).fontSize : null,
    numberInk: nb,
    numberInsideHull: !!(nb && hb && nb.x >= hb.x - 1 && nb.x + nb.w <= hb.x + hb.w + 1 && nb.y >= hb.y - 1 && nb.y + nb.h <= hb.y + hb.h + 1),
    figures: d.querySelector('.roll-view__figures') ? d.querySelector('.roll-view__figures').innerText.replace(/\s+/g, ' ').trim() : null,
    sum: d.querySelector('.roll-view__sum') ? d.querySelector('.roll-view__sum').innerText.replace(/\s+/g, ' ').trim() : null,
    verdict: d.querySelector('.roll__verdict') ? { text: d.querySelector('.roll__verdict').textContent, color: getComputedStyle(d.querySelector('.roll__verdict')).color } : null,
    closeButton: !!d.querySelector('button'),
  };
};

async function themeRun(browser, theme, width, dsf) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 600 ? 812 : 900 }, deviceScaleFactor: dsf });
  await ctx.addInitScript(([id, th]) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', 'on');
    localStorage.setItem('solo-theme', th);
  }, [CID, theme]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push({ kind: 'console', text: m.text().slice(0, 200) }); });
  page.on('pageerror', (e) => errors.push({ kind: 'pageerror', text: String(e && e.message).slice(0, 200) }));
  page.on('response', (r) => { if (r.status() >= 400) errors.push({ kind: 'http' + r.status(), url: r.url().slice(0, 140) }); });

  await page.goto(BASE, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  const applied = await page.evaluate(() => document.documentElement.dataset.theme);
  if (applied !== theme) {
    await page.evaluate((th) => { document.documentElement.dataset.theme = th; }, theme);
  }
  if (!(await page.evaluate(() => !!document.querySelector('#play.active')))) {
    const nav = page.locator('[data-view="play"]').first();
    if (await nav.count()) { await nav.click({ timeout: 10000 }).catch(() => {}); }
  }
  await page.waitForSelector('#chat .roll, #play .roll', { timeout: 30000 });
  await page.waitForTimeout(900); // анимация броска r220 — 620 мс, ждём посадку

  const data = await page.evaluate(probe);
  data.theme = theme;
  data.requestedTheme = theme;
  data.renderedTheme = await page.evaluate(() => document.documentElement.dataset.theme);
  data.viewport = [width, dsf];
  data.devicePixelRatio = await page.evaluate(() => window.devicePixelRatio);

  // Три исхода — те же классы, что ставит рантайм (.o-hit/.o-miss/.o-crit).
  // Цвет читаем с живого знака в ленте: временно меняем класс и возвращаем.
  data.colors = await page.evaluate(() => {
    const svg = document.querySelector('#chat .roll svg.die, #play .roll svg.die');
    const plaque = document.querySelector('#chat .roll, #play .roll');
    const stamp = document.querySelector('#chat .roll .roll__verdict, #play .roll .roll__verdict');
    const out = {};
    if (svg) {
      const original = svg.getAttribute('class');
      for (const kind of ['hit', 'miss', 'crit']) {
        svg.setAttribute('class', 'die die--36 o-' + kind);
        out[kind] = getComputedStyle(svg).color;
      }
      svg.setAttribute('class', original);
    }
    out.plaqueBg = plaque ? getComputedStyle(plaque).backgroundColor : null;
    out.stampColor = stamp ? getComputedStyle(stamp).color : null;
    out.stampBg = stamp ? getComputedStyle(stamp).backgroundColor : null;
    out.pageBg = getComputedStyle(document.body).backgroundColor;
    return out;
  });

  await page.locator('#chat .roll, #play .roll').last().scrollIntoViewIfNeeded();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}/feed-${theme}-${width}.png` });
  await page.locator('#chat .roll, #play .roll').last().screenshot({ path: `${OUT}/plaque-${theme}-${width}.png` }).catch(() => {});

  // Экран броска: нажимаем знак — открывается крупный d20.
  await page.locator('#chat .roll .roll__die, #play .roll .roll__die').last().click();
  await page.waitForSelector('dialog.roll-view', { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(400);
  data.view = await page.evaluate(probeView);
  await page.screenshot({ path: `${OUT}/rollview-${theme}-${width}.png` });
  const dialog = page.locator('dialog.roll-view');
  if (await dialog.count()) await dialog.screenshot({ path: `${OUT}/rollview-only-${theme}-${width}.png` }).catch(() => {});
  await page.keyboard.press('Escape').catch(() => {});
  await page.waitForTimeout(200);

  data.errors = errors;
  await ctx.close();
  return data;
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  for (const theme of ['dark', 'light']) {
    report.themes[theme] = {};
    for (const [width, dsf] of [[1440, 1], [375, 2]]) {
      const key = `${width}`;
      report.themes[theme][key] = await themeRun(browser, theme, width, dsf);
      const d = report.themes[theme][key];
      console.log(`STEP ${theme}-${width}`, JSON.stringify({
        chip: d.svg, box: d.box, href: d.href, num: d.number, numFs: d.numberFontSize,
        inside: d.numberInsideHull, color: d.color, figures: d.figures, total: d.total,
        verdict: d.verdict, view: d.view && { size: d.view.size, num: d.view.number, href: d.view.href, inside: d.view.numberInsideHull },
        errors: d.errors.length,
      }));
    }
  }
  const preExisting = /favicon\.ico|Applying inline style violates|identity is not defined/;
  const flat = [];
  for (const theme of Object.keys(report.themes)) for (const w of Object.keys(report.themes[theme])) {
    flat.push(...report.themes[theme][w].errors.map((e) => ({ theme, w, ...e })));
  }
  report.errors = flat;
  report.freshErrors = flat.filter((e) => !preExisting.test((e.text || '') + ' ' + (e.url || '')));
  const sizes = [];
  for (const theme of Object.keys(report.themes)) for (const w of Object.keys(report.themes[theme])) {
    const d = report.themes[theme][w];
    if (d.svg) sizes.push([`${theme}-${w}`, d.svg.w, d.svg.h]);
  }
  report.chipSizes = sizes;
  // Контраст трёх исходов и вердикта к фону плашки (WCAG, порог 4.5:1 для текста).
  const over = (fg, bg) => {
    const a = parseRgb(fg), b = parseRgb(bg);
    if (!a || !b) return null;
    if (a.alpha >= 1 || !b) return contrast(a.rgb, b.rgb);
    return contrast(a.rgb.map((c, i) => c * a.alpha + b.rgb[i] * (1 - a.alpha)), b.rgb);
  };
  report.contrast = {};
  for (const theme of Object.keys(report.themes)) for (const w of Object.keys(report.themes[theme])) {
    const c = report.themes[theme][w].colors || {};
    const bg = c.plaqueBg;
    report.contrast[`${theme}-${w}`] = {
      plaqueBg: bg,
      hit: over(c.hit, bg),
      miss: over(c.miss, bg),
      crit: over(c.crit, bg),
      verdict: over(c.stampColor, c.stampBg && parseRgb(c.stampBg) && parseRgb(c.stampBg).alpha > 0 ? c.stampBg : bg),
    };
  }
  const chipOk = sizes.every(([, w, h]) => Math.abs(w - 36) <= 1 && Math.abs(h - 36) <= 1);
  const viewsOk = Object.values(report.themes).every((t) => Object.values(t).every((d) => d.view && d.view.found && d.view.size && Math.abs(d.view.size.w - 120) <= 1));
  const hrefOk = Object.values(report.themes).every((t) => Object.values(t).every((d) => d.href === '#sg-chip-36' && d.view && d.view.href === '#sg-d20-120'));
  const insideOk = Object.values(report.themes).every((t) => Object.values(t).every((d) => d.numberInsideHull && d.view && d.view.numberInsideHull));
  const scrollOk = Object.values(report.themes).every((t) => Object.values(t).every((d) => !d.horizontalScroll));
  report.summary = { chipOk, viewsOk, hrefOk, insideOk, scrollOk, freshErrors: report.freshErrors.length };
  report.ok = chipOk && viewsOk && hrefOk && insideOk && scrollOk && report.freshErrors.length === 0;
  fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
  console.log('SUMMARY', JSON.stringify(report.summary));
  console.log('RESULT', report.ok ? 'OK' : 'FAIL');
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((e) => { console.error('PROBE_FAILED', e && e.message); process.exit(2); });
