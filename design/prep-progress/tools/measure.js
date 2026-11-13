/* r234 — замер полосы подготовки в готовом кадре.
   Один файл на оба пути: одиночный вид mock.html?w=…&theme=… .
   Возвращает по объекту на каждую сцену: геометрию, кегли, контраст, засечки.

   Проверки (печатает tools/shoot.py):
     • текст не мельче 14 px — все видимые текстовые узлы, без белых списков;
     • контраст текста к его фактическому фону не ниже 4.5:1 (кроме
       декоративных узлов с aria-hidden — стрелки шагов);
     • ничего не выходит за правый край панели и за кадр;
     • заливка занимает ровно done/total от ширины линейки;
     • число засечек = min(total, 48);
     • штамп сбоя виден тогда и только тогда, когда в сцене есть failed.

   Вызов: window.__measurePrep() → массив по числу сцен. */
window.__measurePrep = function () {
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const rgb = s => {
    const m = s.match(/[\d.]+/g);
    if (!m) return null;
    return { c: m.slice(0, 3).map(Number), a: m.length > 3 ? Number(m[3]) : 1 };
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return +((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)).toFixed(2);
  };
  const hex = c => '#' + c.map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  const backdrop = el => {
    let node = el;
    while (node) {
      const cs = getComputedStyle(node);
      const bg = rgb(cs.backgroundColor);
      if (bg && bg.a >= 0.99) return bg.c;
      node = node.parentElement;
    }
    return [255, 255, 255];
  };
  const vis = el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  };

  const out = [];
  for (const scene of document.querySelectorAll('.scene')) {
    const panel = scene.querySelector('.prep-panel');
    const pr = panel.getBoundingClientRect();
    const pcs = getComputedStyle(panel);
    const rect = el => {
      const r = el.getBoundingClientRect();
      return { w: +r.width.toFixed(1), h: +r.height.toFixed(1),
               x: +(r.left - pr.left).toFixed(1), y: +(r.top - pr.top).toFixed(1) };
    };

    /* ── Текст: кегль и контраст ─────────────────────────────────────────── */
    const texts = [], low = [];
    for (const el of panel.querySelectorAll('*')) {
      if (!vis(el)) continue;
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.nodeValue;
      own = own.trim().replace(/\s+/g, ' ');
      if (!own) continue;
      const cs = getComputedStyle(el);
      const px = parseFloat(cs.fontSize);
      const fg = rgb(cs.color).c;
      const bg = backdrop(el);
      const cr = ratio(fg, bg);
      const item = { cls: String(el.className).slice(0, 40), text: own.slice(0, 34),
                     px: +px.toFixed(1), cr, fg: hex(fg), bg: hex(bg),
                     deco: el.getAttribute('aria-hidden') === 'true' || !!el.closest('[aria-hidden="true"]') };
      texts.push(item);
      if (!item.deco && (item.cr < 4.5 || item.px < 13.5)) low.push(item);
    }

    /* ── Заливка и засечки ──────────────────────────────────────────────── */
    const bar = panel.querySelector('.prep-bar');
    const fill = panel.querySelector('.prep-fill');
    const want = Number(bar.getAttribute('aria-valuenow'));
    const got = +(fill.getBoundingClientRect().width / bar.clientWidth * 100).toFixed(1);
    const ticks = panel.querySelectorAll('.prep-tick');
    const tickStates = { done: 0, failed: 0, active: 0, pending: 0 };
    for (const t of ticks) {
      const c = t.classList;
      if (c.contains('is-failed')) tickStates.failed++;
      else if (c.contains('is-active')) tickStates.active++;
      else if (c.contains('is-done')) tickStates.done++;
      else tickStates.pending++;
    }
    const alert = panel.querySelector('.prep-alert');
    const boxes = {};
    for (const [k, sel] of Object.entries({
      eyebrow: '.prep-eyebrow', clock: '.prep-clock', title: '.prep-title',
      steps: '.prep-steps', bar: '.prep-bar', ticks: '.prep-ticks',
      caption: '.prep-caption', meta: '.prep-meta', alert: '.prep-alert'
    })) {
      const n = panel.querySelector(sel);
      if (n) boxes[k] = rect(n);
    }
    const firstTick = ticks[0] ? ticks[0].getBoundingClientRect() : null;
    const lastTick = ticks.length ? ticks[ticks.length - 1].getBoundingClientRect() : null;

    /* ── Выход за края ──────────────────────────────────────────────────── */
    const outside = [];
    for (const el of panel.querySelectorAll('*')) {
      if (!vis(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right > pr.right + 1.5 || r.left < pr.left - 1.5) {
        outside.push({ cls: String(el.className).slice(0, 40), right: +(r.right - pr.left).toFixed(1) });
      }
    }

    out.push({
      scene: scene.dataset.scene,
      panel: { w: +pr.width.toFixed(1), h: +pr.height.toFixed(1),
               pad: [pcs.paddingTop, pcs.paddingRight, pcs.paddingBottom, pcs.paddingLeft].join(' '),
               gap: pcs.rowGap, spine: pcs.borderLeftWidth + ' ' + pcs.borderLeftColor },
      head: rect(panel.querySelector('.prep-head')),
      steps: rect(panel.querySelector('.prep-steps')),
      bar: { w: +bar.clientWidth.toFixed(1), h: +rect(bar).h.toFixed(1), want, got,
             valuetext: bar.getAttribute('aria-valuetext') },
      ticks: { n: ticks.length, ...tickStates },
      caption: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? null : rect(panel.querySelector('.prep-caption')),
      meta: rect(panel.querySelector('.prep-meta')),
      alert: { hidden: alert.hasAttribute('hidden'), h: +rect(alert).h.toFixed(1),
               text: alert.textContent.trim(), cr: alert.hasAttribute('hidden') ? null
                   : ratio(rgb(getComputedStyle(alert).color).c, rgb(getComputedStyle(alert).backgroundColor).c) },
      failed: Number(panel.dataset.failed || 0),
      running: panel.dataset.running === '1',
      boxes,
      ticksGeom: firstTick && lastTick ? {
        first: +(firstTick.left - pr.left).toFixed(1),
        lastRight: +(lastTick.right - pr.left).toFixed(1),
        w: +firstTick.width.toFixed(1)
      } : null,
      content: { w: +(pr.width - parseFloat(pcs.paddingLeft) - parseFloat(pcs.paddingRight)
                  - parseFloat(pcs.borderLeftWidth) - parseFloat(pcs.borderRightWidth)).toFixed(1) },
      textMin: texts.length ? Math.min(...texts.map(t => t.px)) : null,
      crMin: texts.filter(t => !t.deco).length ? Math.min(...texts.filter(t => !t.deco).map(t => t.cr)) : null,
      low,
      outside,
      docH: document.documentElement.scrollHeight,
      hscroll: document.documentElement.scrollWidth - window.innerWidth
    });
  }
  return out;
};
