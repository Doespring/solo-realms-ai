// A07/F12 — фокус в диалогах: приёмка Q19 на живом экране.
//
//   node scripts/probe_a07_focus.cjs <base> [width] [height]
//
// 1. Карточка предмета: Tab ×20 внутри диалога — activeElement ни разу не BODY,
//    подложка под окном в inert.
// 2. Escape закрывает верхний диалог и возвращает фокус кнопке-инициатору.
// 3. Путь «карточка предмета → выбрать для реплики» только с клавиатуры.
// 4. Селектор действия: ArrowDown не уводит фокус (панель снята с боевой
//    страницы решением владельца 08.11 — модуль грузим на стенд сами).
// 5. Кольцо фокуса 2 px у Tab-остановок.
const { chromium } = require('/home/hermes/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');

const base = process.argv[2];
const width = Number(process.argv[3] || 1280);
const height = Number(process.argv[4] || 800);

const ACTIVE = `(() => {
  const a = document.activeElement;
  if (!a) return { name: 'null', label: '', body: false };
  const cls = typeof a.className === 'string' && a.className.trim() ? '.' + a.className.trim().split(/\\s+/).join('.') : '';
  return {
    name: a.tagName + (a.id ? '#' + a.id : '') + cls,
    label: (a.getAttribute?.('aria-label') || a.textContent || '').trim().slice(0, 40),
    body: a === document.body || a === document.documentElement,
  };
})()`;

async function tabTo(page, expression, max = 200) {
  for (let i = 0; i < max; i += 1) {
    if (await page.evaluate(expression)) return i;
    await page.keyboard.press('Tab');
    await page.waitForTimeout(60);
  }
  return -1;
}

async function ring(page) {
  return page.evaluate(`(() => {
    const a = document.activeElement;
    if (!a || a === document.body) return null;
    const cs = getComputedStyle(a);
    const host = a.closest('#action-form') || a;
    const hs = getComputedStyle(host);
    const pick = (s) => [s.outlineWidth, s.outlineStyle, s.outlineColor].join(' ');
    return { node: a.tagName + (a.id ? '#' + a.id : '') + (a.getAttribute?.('aria-label') ? '[' + a.getAttribute('aria-label') + ']' : ''), own: pick(cs), host: host === a ? null : pick(hs) };
  })()`);
}

async function openGame(browser, campaign) {
  const context = await browser.newContext({ viewport: { width, height }, locale: 'ru-RU' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 300)));
  page.on('response', (r) => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url().slice(0, 140)); });
  await context.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', id);
    localStorage.setItem('solo-theme', 'dark');
    localStorage.removeItem('solo-start-page');
  }, campaign);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window.SoloGameScreen || document.querySelector('#play.active')), null, { timeout: 40000 });
  await page.waitForTimeout(1500);
  return { context, page, errors };
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.SOLO_UI_BROWSER,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  const campaign = process.env.SOLO_CAMPAIGN;
  const out = { viewport: { width, height } };

  // ── 1+2. Карточка предмета: Tab ×20, затем Escape. ──────────────────────
  {
    const { context, page, errors } = await openGame(browser, campaign);
    await page.evaluate(() => document.querySelector('.nav[data-view="inventory"]').click());
    await page.waitForTimeout(1200);
    out.inventory = await page.evaluate(() => ({
      active: (document.querySelector('.view.active') || {}).id,
      cards: document.querySelectorAll('#inventory .item-card').length,
      first: document.querySelector('#inventory .item-card')?.textContent?.trim().slice(0, 60),
    }));
    await page.click('#inventory .item-card');
    await page.waitForTimeout(400);
    out.open = await page.evaluate(() => {
      const d = document.querySelector('dialog.quickview');
      const a = document.activeElement;
      return {
        open: !!d?.open,
        modal: !!d?.matches(':modal'),
        focusInside: !!d && d.contains(a),
        active: a?.tagName + (a ? '.' + String(a.className).trim().split(/\s+/).join('.') : ''),
        buttons: d ? [...d.querySelectorAll('button')].map((b) => b.textContent.trim()) : [],
      };
    });
    out.background = await page.evaluate(() => [...document.body.children]
      .filter((n) => !['SCRIPT', 'STYLE', 'LINK', 'TEMPLATE', 'NOSCRIPT', 'META'].includes(n.tagName))
      .map((n) => ({
        node: n.tagName + (n.id ? '#' + n.id : '') + (typeof n.className === 'string' && n.className ? '.' + n.className.trim().split(/\s+/).join('.') : ''),
        inert: n.inert === true,
      })));

    const tabs = [], rings = [];
    for (let i = 0; i < 20; i += 1) {
      await page.keyboard.press('Tab');
      await page.waitForTimeout(60);
      tabs.push(await page.evaluate(ACTIVE));
      if (i < 4) rings.push(await ring(page));
    }
    out.tabs = tabs;
    out.tabBodies = tabs.filter((t) => t.body).length;
    out.tabRings = rings;

    const wasOpen = await page.evaluate(() => !!document.querySelector('dialog.quickview')?.open);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    out.escape = await page.evaluate(`(() => {
      const d = document.querySelector('dialog.quickview');
      const a = document.activeElement;
      return {
        wasOpen: ${wasOpen},
        open: !!d?.open,
        active: a ? a.tagName + (a.className ? '.' + String(a.className).trim().split(/\\s+/).join('.') : '') : 'null',
        label: (a?.textContent || '').trim().slice(0, 50),
        isItemCard: !!a && a.classList?.contains('item-card'),
        inInventory: !!a?.closest('#inventory'),
        backgroundStillInert: [...document.body.children].some((n) => n.inert === true),
      };
    })()`);
    out.errors1 = errors;
    await context.close();
  }

  console.error('SECTION1', JSON.stringify({open: out.open, tabBodies: out.tabBodies, escape: out.escape}));
  // ── 3. Весь путь без мыши: меню → инвентарь → карточка → выбор. ─────────
  {
    const { context, page, errors } = await openGame(browser, campaign);
    const steps = {};
    steps.toMore = await tabTo(page, `(() => document.activeElement?.id === 'nav-more')()`);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    steps.moreOpen = await page.evaluate(() => document.querySelector('aside.sidebar').classList.contains('show-more'));
    steps.toNav = await tabTo(page, `(() => document.activeElement?.matches('.nav[data-view="inventory"]'))()`);
    steps.navActive = await page.evaluate(ACTIVE);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1300);
    steps.view = await page.evaluate(() => (document.querySelector('.view.active') || {}).id);
    steps.toCard = await tabTo(page, `(() => document.activeElement?.matches('#inventory .item-card'))()`);
    steps.cardActive = await page.evaluate(ACTIVE);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(400);
    steps.dialogOpen = await page.evaluate(() => !!document.querySelector('dialog.quickview')?.open);
    steps.toSelect = await tabTo(page, `(() => document.activeElement?.matches('dialog.quickview .primary'))()`);
    steps.selectActive = await page.evaluate(ACTIVE);
    steps.selectRing = await ring(page);
    await page.keyboard.press('Enter');
    await page.waitForTimeout(900);
    steps.after = await page.evaluate(() => {
      const chip = document.querySelector('.selection-chip');
      const input = document.querySelector('#play #player-input');
      const form = document.querySelector('#play #action-form');
      const fs = form ? getComputedStyle(form) : null;
      return {
        dialogOpen: !!document.querySelector('dialog.quickview')?.open,
        chipHidden: chip ? chip.hidden : null,
        chipText: chip ? chip.querySelector('span')?.textContent : null,
        view: (document.querySelector('.view.active') || {}).id,
        focusComposer: document.activeElement === input,
        active: document.activeElement?.tagName + (document.activeElement?.id ? '#' + document.activeElement.id : ''),
        composerRing: fs ? [fs.outlineWidth, fs.outlineStyle, fs.outlineColor].join(' ') : null,
      };
    });
    out.keyboardPath = steps;
    out.keyboardResult = steps.after;
    out.errors2 = errors;
    await context.close();
  }

  console.error('SECTION3', JSON.stringify(out.keyboardResult));
  // ── 4. Селектор действия: ArrowDown не уводит фокус. ────────────────────
  {
    const { context, page, errors } = await openGame(browser, campaign);
    await page.addScriptTag({ url: '/static/prototype-actions.js' });
    await page.waitForTimeout(800);
    const panel = await page.evaluate(() => {
      const details = document.querySelector('.prototype-action-panel');
      if (!details) return { found: false };
      details.open = true;
      return {
        found: true,
        hidden: details.hidden,
        options: document.querySelectorAll('.prototype-action-panel select[aria-label="Действие героя"] option').length,
      };
    });
    out.actionPanel = panel;
    if (panel.found && !panel.hidden) {
      const before = await page.evaluate(() => {
        const select = document.querySelector('.prototype-action-panel select[aria-label="Действие героя"]');
        select.focus();
        window.__a07select = select;
        return { value: select.value, focused: document.activeElement === select, ring: getComputedStyle(select).outlineWidth };
      });
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(700);
      const after = await page.evaluate(() => {
        const select = document.querySelector('.prototype-action-panel select[aria-label="Действие героя"]');
        const a = document.activeElement;
        return {
          sameNode: a === window.__a07select,
          body: a === document.body,
          active: a ? a.tagName + (a.getAttribute?.('aria-label') ? '[' + a.getAttribute('aria-label') + ']' : '') : 'null',
          value: select?.value,
          fields: document.querySelectorAll('.prototype-action-fields > *').length,
        };
      });
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(700);
      const again = await page.evaluate(() => ({
        body: document.activeElement === document.body,
        sameNode: document.activeElement === window.__a07select,
        value: document.querySelector('.prototype-action-panel select[aria-label="Действие героя"]')?.value,
        fields: document.querySelectorAll('.prototype-action-fields > *').length,
      }));
      // Поле внутри перерисованных параметров тоже не должно терять фокус.
      const inner = await page.evaluate(() => {
        const label = [...document.querySelectorAll('.prototype-action-fields label')].find((l) => l.querySelector('select,input'));
        const control = label?.querySelector('select,input');
        if (!control) return null;
        control.focus();
        window.__a07inner = control;
        return { label: label.textContent.trim().slice(0, 30), focused: document.activeElement === control };
      });
      let innerAfter = null;
      if (inner) {
        await page.keyboard.press('ArrowDown');
        await page.waitForTimeout(400);
        innerAfter = await page.evaluate(() => ({ same: document.activeElement === window.__a07inner, body: document.activeElement === document.body }));
      }
      out.actionDown = { before, after, again, inner, innerAfter };
    }
    out.errors3 = errors;
    await context.close();
  }

  console.error('SECTION4', JSON.stringify(out.actionDown || out.actionPanel));
  // ── 5. Кольцо фокуса: Tab по игровому экрану. ───────────────────────────
  {
    const { context, page, errors } = await openGame(browser, campaign);
    const rings = [];
    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press('Tab');
      await page.waitForTimeout(80);
      const r = await ring(page);
      if (r) rings.push({ i, ...r });
    }
    out.rings = rings;
    out.ringsBad = rings.filter((r) => !(r.own.startsWith('2px solid') || (r.host || '').startsWith('2px solid')));
    out.errors4 = errors;
    await context.close();
  }

  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch((e) => { console.error('PROBE FAILED', e); process.exit(1); });
