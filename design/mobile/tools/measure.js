/* r231 — замер одного кадра. Один и тот же файл подключают tools/shoot.py
   (Playwright) и CDP-прогон: числа в SPEC.md и tools/checks.json считаются
   ровно этим кодом, без второй реализации.

   Вызов: window.__measureFrame('.phone[data-screen="home"][data-theme="dark"][data-w="375"]')

   Что проверяется:
     • размер кадра совпадает с заявленным;
     • горизонтальной прокрутки нет, видимых элементов за краем нет;
     • зона нажатия ≥ 44 × 44;
     • ЛЮБОЙ видимый текст не мельче 14 px (без белых списков: владелец
       жаловался именно на 12–13 px, поэтому меряем все текстовые узлы кадра,
       а не заранее отобранные селекторы);
     • основной текст (рассказ, поле ввода, строки меню и списков) ≥ 16;
     • шапка ≤ 64;
     • главное действие [data-primary] выше сгиба;
     • шторка: высота, прокрутка внутри, вкладки, закреплённый выход.
*/
window.__measureFrame = function (sel) {
  const phone = document.querySelector(sel);
  if (!phone) return null;
  const pr = phone.getBoundingClientRect();
  const q = s => Array.from(phone.querySelectorAll(s));
  const vis = el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const rect = el => {
    const r = el.getBoundingClientRect();
    return {x: +(r.x - pr.x).toFixed(1), y: +(r.y - pr.y).toFixed(1),
            w: +r.width.toFixed(1), h: +r.height.toFixed(1), bottom: +(r.bottom - pr.y).toFixed(1)};
  };
  const px = el => parseFloat(getComputedStyle(el).fontSize);

  // ── Текст: все видимые текстовые узлы кадра ─────────────────────────────
  const texts = [];
  for (const el of phone.querySelectorAll('*')) {
    if (!vis(el)) continue;
    let own = '';
    for (const n of el.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    if (!own.trim()) continue;
    const cs = getComputedStyle(el);
    texts.push({px: px(el), text: own.trim().replace(/\s+/g, ' ').slice(0, 28),
                cls: (el.className || '').toString().slice(0, 40),
                tag: el.tagName.toLowerCase()});
  }
  const textMin = texts.length ? Math.min(...texts.map(t => t.px)) : null;
  const textBad = texts.filter(t => t.px < 13.5);

  // ── Основной текст: роли, для которых 16 — минимум ──────────────────────
  const MAIN = ['.prose', '.composer__input', '.field__input', '.sheet__list li',
                '.menu-row', '.menu-exit', '.row__title', '.btn'];
  const main = [];
  for (const s of MAIN) for (const el of q(s)) if (vis(el)) main.push({sel: s, px: px(el)});
  const mainMin = main.length ? Math.min(...main.map(m => m.px)) : null;
  const mainBad = main.filter(m => m.px < 15.5);

  // ── Выход за края кадра ─────────────────────────────────────────────────
  const outside = [];
  for (const el of phone.querySelectorAll('*')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > pr.right + 1.5 || r.left < pr.left - 1.5) {
      outside.push({tag: el.tagName.toLowerCase(), cls: el.className.toString().slice(0, 60),
                    left: +(r.left - pr.left).toFixed(1), right: +(r.right - pr.left).toFixed(1)});
    }
  }

  // ── Зоны нажатия ────────────────────────────────────────────────────────
  const tap = [];
  for (const el of phone.querySelectorAll('button, a, textarea, input, select, [role=tab], [role=button]')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    tap.push({what: (el.getAttribute('aria-label') || el.textContent.trim().slice(0, 28) || el.tagName),
              w: +r.width.toFixed(1), h: +r.height.toFixed(1),
              ok: r.width >= 43.5 && r.height >= 43.5});
  }

  // ── Шторка ──────────────────────────────────────────────────────────────
  const sheet = phone.querySelector('.sheetmodal');
  let sheetInfo = null;
  if (sheet) {
    const sb = sheet.querySelector('.sheetmodal__body') || sheet;
    const tabs = sheet.querySelector('.menu-tabs');
    const exit = sheet.querySelector('.menu-exit');
    sheetInfo = {
      h: +sheet.getBoundingClientRect().height.toFixed(1),
      top: +(sheet.getBoundingClientRect().top - pr.top).toFixed(1),
      bodyH: +sb.getBoundingClientRect().height.toFixed(1),
      bodyClient: sb.clientHeight,
      bodyScroll: sb.scrollHeight,
      scroll: sb.scrollHeight - sb.clientHeight,
      tabsH: tabs ? +tabs.getBoundingClientRect().height.toFixed(1) : null,
      exitH: exit ? +exit.getBoundingClientRect().height.toFixed(1) : null,
      rows: sheet.querySelectorAll('.menu-row').length,
      pane: sheet.dataset.pane || null
    };
  }


  // ── Ритм отступов (SPEC §2) ─────────────────────────────────────────────
  // Каждый gap/padding/margin-top/bottom в слое приложения обязан быть из
  // набора 0/2/4/6/8/12/16/24/32 (34 — safe area, 42 — 8 + 34 низ дока).
  // iOS-полосы и нарисованную клавиатуру не считаем: это не приложение.
  const RHYTHM = [0, 2, 4, 6, 8, 12, 16, 24, 32, 34, 42];
  const rhythm = {};
  for (const el of phone.querySelectorAll('*')) {
    if (!vis(el) || el.closest('.status, .homebar, .kb')) continue;
    const cs = getComputedStyle(el);
    for (const prop of ['gap', 'padding-top', 'padding-right', 'padding-bottom',
                        'padding-left', 'margin-top', 'margin-bottom']) {
      const v = cs[prop];
      if (!v || v === 'auto' || v === 'normal') continue;
      for (const n of v.split(' ').map(parseFloat)) if (!isNaN(n)) rhythm[n] = (rhythm[n] || 0) + 1;
    }
  }
  const rhythmBad = Object.keys(rhythm).map(Number)
    .filter(n => !RHYTHM.includes(n)).sort((a, b) => a - b);

  const bar = phone.querySelector('[data-zone=bar]');
  const body = phone.querySelector('[data-zone=body]');
  const dock = phone.querySelector('[data-zone=dock]');
  const primary = phone.querySelector('[data-primary]');

  return {
    frame: {w: pr.width, h: pr.height},
    hscroll: phone.scrollWidth - phone.clientWidth,
    bar: bar ? +bar.getBoundingClientRect().height.toFixed(1) : null,
    body: body ? {h: +body.getBoundingClientRect().height.toFixed(1),
                  client: body.clientHeight, scroll: body.scrollHeight} : null,
    dock: dock ? +dock.getBoundingClientRect().height.toFixed(1) : null,
    primary: primary && vis(primary) ? rect(primary) : null,
    textMin, textBad, mainMin, mainBad,
    outside, tapBad: tap.filter(t => !t.ok), tapN: tap.length,
    sheet: sheetInfo,
    scrollTop: body ? +body.scrollTop.toFixed(1) : null,
    rhythm, rhythmBad,
    kb: phone.hasAttribute('data-kb')
  };
};

/* Подготовка кадра к съёмке: кадры с data-scroll="bottom" (игра) показывают
   ленту на последнем сообщении — SPEC §8.3. Возвращает фактический scrollTop. */
window.__prepFrame = function (sel) {
  const phone = document.querySelector(sel);
  if (!phone) return null;
  const body = phone.querySelector('[data-zone=body]');
  if (!body) return 0;
  body.scrollTop = phone.dataset.scroll === 'bottom'
    ? body.scrollHeight - body.clientHeight
    : 0;
  return +body.scrollTop.toFixed(1);
};
