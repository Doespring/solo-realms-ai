// A07/F12: фокус не теряется в диалогах (приёмка Q19 на живом экране).
//
//   node tests/test_a07_focus.cjs <base>
//
// 1. Карточка предмета: 20 нажатий Tab внутри диалога — activeElement ни разу
//    не BODY и всегда внутри окна; подложка под окном в inert.
// 2. Escape закрывает верхний диалог и возвращает фокус кнопке-инициатору.
// 3. Селектор действия: ArrowDown не уводит фокус (узел списка постоянный).
// 4. Путь «карточка предмета → выбрать для реплики» проходится без мыши.
// 5. Кольцо фокуса: у каждой Tab-остановки 2 px (своё или у обёртки).
const { chromium } = require(process.env.SOLO_UI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const [base, temp] = process.argv.slice(2);
const out = process.env.SOLO_UI_EVIDENCE || temp;
fs.mkdirSync(out, { recursive: true });

const ACTIVE = `(() => {
  const a = document.activeElement;
  if (!a) return { name: 'null', label: '', body: true, inDialog: false };
  const cls = typeof a.className === 'string' && a.className.trim() ? '.' + a.className.trim().split(/\\s+/).join('.') : '';
  const open = [...document.querySelectorAll('dialog[open]')];
  const dialog = open.length ? open[open.length - 1] : null;
  return {
    name: a.tagName + (a.id ? '#' + a.id : '') + cls,
    label: (a.getAttribute?.('aria-label') || a.textContent || '').trim().slice(0, 40),
    body: a === document.body || a === document.documentElement,
    inDialog: !!dialog && dialog.contains(a),
  };
})()`;

const RING = `(() => {
  const a = document.activeElement;
  if (!a || a === document.body || a === document.documentElement) return { node: a ? a.tagName : 'null', body: true, own: '', host: null };
  const host = a.closest('#action-form') || a;
  const own = getComputedStyle(a);
  const box = getComputedStyle(host);
  const pick = (s) => [s.outlineWidth, s.outlineStyle, s.outlineColor].join(' ');
  return {
    node: a.tagName + (a.id ? '#' + a.id : '') + (a.getAttribute?.('aria-label') ? '[' + a.getAttribute('aria-label') + ']' : ''),
    body: false,
    own: pick(own),
    host: host === a ? null : pick(box),
  };
})()`;

const ringOk = (row) => Boolean(row) && (row.own.startsWith('2px solid') || (row.host || '').startsWith('2px solid'));

async function tabTo(page, expression, max = 200) {
  for (let index = 0; index < max; index += 1) {
    if (await page.evaluate(expression)) return index;
    await page.keyboard.press('Tab');
    await page.waitForTimeout(60);
  }
  return -1;
}

async function openGame(browser, campaign) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'ru-RU' });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await context.addInitScript((id) => {
    localStorage.setItem('solo-campaign', id);
    localStorage.setItem('solo-remember-campaign', id);
    localStorage.setItem('solo-theme', 'dark');
    localStorage.removeItem('solo-start-page');
    // Настоящий снимок кампании, который экран рассылает в solo:render:
    // перерисовку в тесте повторяем тем же событием, а не пустой заглушкой.
    document.addEventListener('solo:render', (event) => { window.__soloRenderDetail = event.detail; });
  }, campaign);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => !!(window.SoloGameScreen || document.querySelector('#play.active')), null, { timeout: 40000 });
  await page.waitForTimeout(1200);
  return { context, page, errors };
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.SOLO_UI_BROWSER || undefined,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });
  const plain = process.env.SOLO_CAMPAIGN_PLAIN;
  const experimental = process.env.SOLO_CAMPAIGN_EXPERIMENTAL;
  const result = { plain, experimental, checks: [] };

  try {
    // ── 1+2. Карточка предмета: Tab ×20, затем Escape. ─────────────────────
    {
      const { context, page, errors } = await openGame(browser, plain);
      await page.evaluate(() => document.querySelector('.nav[data-view="inventory"]').click());
      await page.waitForTimeout(1200);
      const cards = await page.evaluate(() => document.querySelectorAll('#inventory .item-card').length);
      assert(cards > 0, 'в инвентаре нет карточек предметов');
      await page.click('#inventory .item-card');
      await page.waitForTimeout(400);
      const opened = await page.evaluate(() => {
        const dialog = document.querySelector('dialog.quickview');
        return { open: !!dialog?.open, modal: !!dialog?.matches(':modal'), buttons: [...dialog.querySelectorAll('button')].map((b) => b.textContent.trim()) };
      });
      assert.equal(opened.open, true, 'карточка не открылась');
      assert.equal(opened.modal, true, 'окно открылось не как модальное');
      assert.deepEqual(opened.buttons, ['Выбрать для реплики', 'Закрыть']);
      const focusInside = await page.evaluate(() => !!document.querySelector('dialog.quickview')?.contains(document.activeElement));
      assert.equal(focusInside, true, 'фокус не вошёл в окно');

      const inert = await page.evaluate(() => {
        const dialog = document.querySelector('dialog.quickview');
        return [...document.body.children]
          .filter((node) => !['SCRIPT', 'STYLE', 'LINK', 'TEMPLATE', 'NOSCRIPT', 'META', 'TITLE'].includes(node.tagName))
          .map((node) => ({ node: node.tagName + (node.id ? '#' + node.id : ''), inert: node.inert === true, isDialog: node === dialog }));
      });
      const outside = inert.filter((row) => !row.isDialog);
      assert(outside.every((row) => row.inert), 'под окном остались достижимые элементы: ' + JSON.stringify(outside.filter((row) => !row.inert)));
      result.inert = inert;

      const tabs = [];
      for (let index = 0; index < 20; index += 1) {
        await page.keyboard.press('Tab');
        await page.waitForTimeout(60);
        tabs.push(await page.evaluate(ACTIVE));
      }
      const bodies = tabs.filter((row) => row.body).length;
      const escaped = tabs.filter((row) => !row.inDialog).length;
      assert.equal(bodies, 0, `фокус ушёл в BODY: ${bodies} из ${tabs.length}`);
      assert.equal(escaped, 0, `фокус вышел из окна: ${escaped} из ${tabs.length}`);
      result.tabs = tabs;

      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
      const escapedClose = await page.evaluate(() => {
        const dialog = document.querySelector('dialog.quickview');
        const active = document.activeElement;
        return {
          open: !!dialog?.open,
          isItemCard: !!active?.classList?.contains('item-card'),
          inInventory: !!active?.closest('#inventory'),
          label: (active?.textContent || '').trim(),
          backgroundInert: [...document.body.children].some((node) => node.inert === true),
        };
      });
      assert.equal(escapedClose.open, false, 'Escape не закрыл окно');
      assert.equal(escapedClose.isItemCard, true, 'фокус не вернулся кнопке-инициатору: ' + escapedClose.label);
      assert.equal(escapedClose.inInventory, true, 'инициатор не в том экране');
      assert.equal(escapedClose.backgroundInert, false, 'inert остался после закрытия');
      result.escape = escapedClose;
      assert.deepEqual(errors, [], 'ошибки страницы: ' + errors.join(' | '));
      result.checks.push('tab-20-inside-dialog', 'underline-inert', 'escape-returns-to-initiator');
      await page.screenshot({ path: path.join(out, 'a07-dialog.png') });
      await context.close();
    }

    // ── 4. Весь путь без мыши. ─────────────────────────────────────────────
    {
      const { context, page, errors } = await openGame(browser, plain);
      const steps = {};
      steps.toMore = await tabTo(page, `(() => document.activeElement?.id === 'nav-more')()`);
      assert(steps.toMore >= 0, 'не дошли Tab-ом до кнопки «ещё»');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(500);
      steps.toNav = await tabTo(page, `(() => document.activeElement?.matches('.nav[data-view="inventory"]'))()`);
      assert(steps.toNav >= 0, 'не дошли Tab-ом до «Инвентаря»');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(1300);
      assert.equal(await page.evaluate(() => (document.querySelector('.view.active') || {}).id), 'inventory', 'инвентарь не открылся с клавиатуры');
      steps.toCard = await tabTo(page, `(() => document.activeElement?.matches('#inventory .item-card'))()`);
      assert(steps.toCard >= 0, 'не дошли Tab-ом до карточки предмета');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(400);
      assert.equal(await page.evaluate(() => !!document.querySelector('dialog.quickview')?.open), true, 'карточка не открылась с клавиатуры');
      steps.toSelect = await tabTo(page, `(() => document.activeElement?.matches('dialog.quickview .primary'))()`);
      assert(steps.toSelect >= 0, 'не дошли Tab-ом до «Выбрать для реплики»');
      steps.ring = await page.evaluate(RING);
      assert(ringOk(steps.ring), 'у кнопки выбора нет рамки 2 px: ' + JSON.stringify(steps.ring));
      await page.keyboard.press('Enter');
      await page.waitForTimeout(900);
      steps.after = await page.evaluate(() => {
        const chip = document.querySelector('.selection-chip');
        return {
          dialogOpen: !!document.querySelector('dialog.quickview')?.open,
          chipHidden: chip ? chip.hidden : null,
          chipText: chip ? chip.querySelector('span')?.textContent : null,
          view: (document.querySelector('.view.active') || {}).id,
          focusComposer: document.activeElement === document.querySelector('#play #player-input'),
        };
      });
      assert.equal(steps.after.dialogOpen, false, 'окно не закрылось');
      assert.equal(steps.after.chipHidden, false, 'выбор не лёг в контекст реплики');
      assert.match(String(steps.after.chipText), /Longsword|предмет/i, 'в контексте нет названия предмета: ' + steps.after.chipText);
      assert.equal(steps.after.view, 'play', 'после выбора не вернулись на игровой экран');
      assert.equal(steps.after.focusComposer, true, 'фокус не в поле реплики');
      result.keyboard = steps;
      result.checks.push('keyboard-only-path');
      assert.deepEqual(errors, [], 'ошибки страницы: ' + errors.join(' | '));
      await context.close();
    }

    // ── 3. Селектор действия: ArrowDown не уводит фокус. ───────────────────
    {
      const { context, page, errors } = await openGame(browser, experimental);
      await page.addScriptTag({ url: '/static/prototype-actions.js' });
      await page.waitForTimeout(800);
      const panel = await page.evaluate(() => {
        const details = document.querySelector('.prototype-action-panel');
        if (!details) return { found: false };
        details.open = true;
        return { found: true, hidden: details.hidden, options: document.querySelectorAll('.prototype-action-panel select[aria-label="Действие героя"] option').length };
      });
      assert.equal(panel.found, true, 'панель действий не собралась');
      assert.equal(panel.hidden, false, 'панель скрыта: кампания не экспериментальная');
      assert(panel.options > 2, 'в списке действий меньше трёх пунктов');
      const before = await page.evaluate(() => {
        const select = document.querySelector('.prototype-action-panel select[aria-label="Действие героя"]');
        select.focus();
        window.__a07select = select;
        return { value: select.value, ring: getComputedStyle(select).outlineWidth };
      });
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(700);
      const after = await page.evaluate(() => {
        const active = document.activeElement;
        return {
          sameNode: active === window.__a07select,
          body: active === document.body,
          value: document.querySelector('.prototype-action-panel select[aria-label="Действие героя"]')?.value,
          fields: document.querySelectorAll('.prototype-action-fields > *').length,
        };
      });
      assert.equal(after.body, false, 'ArrowDown увёл фокус в BODY');
      assert.equal(after.sameNode, true, 'список действия заменён новым узлом');
      assert.notEqual(after.value, before.value, 'ArrowDown не сменил действие');
      assert(after.fields > 0, 'параметры действия не построились');
      await page.keyboard.press('ArrowDown');
      await page.waitForTimeout(700);
      const again = await page.evaluate(() => ({
        body: document.activeElement === document.body,
        sameNode: document.activeElement === window.__a07select,
        value: document.querySelector('.prototype-action-panel select[aria-label="Действие героя"]')?.value,
        fields: document.querySelectorAll('.prototype-action-fields > *').length,
      }));
      assert.equal(again.body, false, 'второй ArrowDown увёл фокус в BODY');
      assert.equal(again.sameNode, true, 'список действия заменён на втором шаге');
      // Настоящая перерисовка экрана (solo:render → load() → draw()): поле
      // параметров пересоздаётся, но фокус и выбранное значение обязаны остаться.
      const inner = await page.evaluate(() => {
        const control = [...document.querySelectorAll('.prototype-action-fields select,.prototype-action-fields input')][0];
        if (!control) return null;
        control.focus();
        if (control.tagName === 'SELECT' && control.options.length > 1) control.selectedIndex = 1;
        if (control.tagName === 'INPUT') control.value = '2';
        control.dispatchEvent(new Event('change', { bubbles: true }));
        return { label: control.getAttribute('aria-label'), value: control.value, focused: document.activeElement === control };
      });
      assert(inner && inner.label, 'в параметрах действия нет полей');
      assert.equal(inner.focused, true, 'поле параметров не получило фокус');
      const detail = await page.evaluate(() => { const value = window.__soloRenderDetail; if (!value) return null; document.dispatchEvent(new CustomEvent('solo:render', { detail: value })); return true; });
      assert.equal(detail, true, 'снимок кампании для solo:render не получен');
      await page.waitForTimeout(900);
      const innerAfter = await page.evaluate((label) => {
        const node = [...document.querySelectorAll('.prototype-action-fields [aria-label]')].find((item) => item.getAttribute('aria-label') === label);
        return {
          found: !!node,
          focused: !!node && document.activeElement === node,
          value: node ? node.value : null,
          body: document.activeElement === document.body,
        };
      }, inner.label);
      assert.equal(innerAfter.body, false, 'поле параметров потеряло фокус в BODY после перерисовки');
      assert.equal(innerAfter.found, true, 'после перерисовки поле «' + inner.label + '» пропало');
      assert.equal(innerAfter.focused, true, 'после перерисовки фокус не вернулся в поле «' + inner.label + '»');
      assert.equal(innerAfter.value, inner.value, 'выбранное значение поля потеряно при перерисовке');
      result.actions = { before, after, again, inner, innerAfter };
      result.checks.push('action-select-keeps-focus');
      assert.deepEqual(errors, [], 'ошибки страницы: ' + errors.join(' | '));
      await context.close();
    }

    // ── 5. Кольцо фокуса: Tab по игровому экрану. ──────────────────────────
    {
      const { context, page, errors } = await openGame(browser, plain);
      const rings = [];
      for (let index = 0; index < 14; index += 1) {
        await page.keyboard.press('Tab');
        await page.waitForTimeout(80);
        rings.push({ step: index, ...(await page.evaluate(RING)) });
      }
      // Остановки с BODY не выбрасываем: это граница страницы, её надо видеть
      // в доказательстве, а не молча выкидывать из выборки.
      const bad = rings.filter((row) => !row.body && !ringOk(row));
      const bodies = rings.filter((row) => row.body).length;
      assert.equal(bad.length, 0, 'нет рамки 2 px: ' + JSON.stringify(bad));
      result.rings = rings;
      result.ringBodies = bodies;
      result.checks.push('focus-ring-2px');
      assert.deepEqual(errors, [], 'ошибки страницы: ' + errors.join(' | '));
      await context.close();
    }

    // ── 6. Края контракта окна: повторный showModal, удаление без close(), cancel.
    {
      const { context, page, errors } = await openGame(browser, plain);
      const repeat = await page.evaluate(async () => {
        const initiator = document.createElement('button'); initiator.textContent = 'Инициатор';
        document.body.append(initiator); initiator.focus();
        const dialog = document.createElement('dialog'); const inside = document.createElement('button'); inside.textContent = 'Внутри';
        dialog.append(inside); document.body.append(dialog);
        dialog.showModal(); dialog.showModal();
        const focusInside = document.activeElement === inside;
        dialog.close(); await new Promise((done) => setTimeout(done, 40));
        const backToInitiator = document.activeElement === initiator;
        dialog.remove(); initiator.remove();
        return { focusInside, backToInitiator };
      });
      assert.equal(repeat.focusInside, true, 'повторный showModal не поставил фокус внутрь окна');
      assert.equal(repeat.backToInitiator, true, 'повторный showModal затёр инициатора');

      const removed = await page.evaluate(async () => {
        const initiator = document.createElement('button'); initiator.textContent = 'Инициатор';
        document.body.append(initiator); initiator.focus();
        const dialog = document.createElement('dialog'); dialog.append(document.createElement('button'));
        document.body.append(dialog); dialog.showModal(); dialog.remove();
        await new Promise((done) => setTimeout(done, 80));
        const skip = ['SCRIPT', 'STYLE', 'LINK', 'TEMPLATE', 'NOSCRIPT', 'META', 'TITLE'];
        const inert = [...document.body.children].filter((node) => !skip.includes(node.tagName) && node.inert === true).map((node) => node.tagName);
        const focused = document.activeElement === initiator;
        initiator.remove();
        return { inert, focused };
      });
      assert.deepEqual(removed.inert, [], 'окно убрали без close(), подложка осталась inert: ' + JSON.stringify(removed.inert));
      assert.equal(removed.focused, true, 'после удаления окна фокус не вернулся инициатору');

      // Окно вправе отказаться закрываться: Escape не должен обходить cancel.
      await page.evaluate(() => {
        const initiator = document.createElement('button'); initiator.textContent = 'Инициатор';
        document.body.append(initiator); initiator.focus();
        const dialog = document.createElement('dialog'); const inside = document.createElement('button'); inside.textContent = 'Внутри';
        dialog.append(inside);
        const refuse = (event) => event.preventDefault();
        dialog.addEventListener('cancel', refuse);
        document.body.append(dialog); dialog.showModal();
        window.__a07cancel = { dialog, refuse, initiator };
      });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      const stillOpen = await page.evaluate(() => window.__a07cancel.dialog.open);
      assert.equal(stillOpen, true, 'Escape закрыл окно, хотя cancel отменён');
      await page.evaluate(() => {
        const { dialog, refuse, initiator } = window.__a07cancel;
        dialog.removeEventListener('cancel', refuse); dialog.close(); dialog.remove(); initiator.remove();
      });
      result.guards = { repeat, removed, cancelRefused: stillOpen };
      result.checks.push('repeat-showmodal-keeps-initiator', 'removed-dialog-restores-background', 'escape-honours-cancel');
      // Фокус в BODY после закрытия окна без инициатора — не дефект, но виден в отчёте.
      result.focusAfterCancel = await page.evaluate(() => document.activeElement?.tagName || 'null');
      assert.deepEqual(errors, [], 'ошибки страницы: ' + errors.join(' | '));
      await context.close();
    }

    fs.writeFileSync(path.join(out, 'a07.json'), JSON.stringify({ server: 'local-uvicorn', model: 'CommandMockLLM', provider_calls: 0, real_nat20: true, ...result }, null, 2));
    console.log(JSON.stringify({ checks: result.checks, tabs: result.tabs?.length, escape: result.escape, actions: result.actions?.after, rings: result.rings?.length, ringBodies: result.ringBodies }));
  } catch (error) {
    console.error('A07 FAILED', error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
