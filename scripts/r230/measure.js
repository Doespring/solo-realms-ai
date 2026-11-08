(() => {
  const RES = {width: innerWidth, height: innerHeight, dpr: devicePixelRatio,
    screen: null, theme: null, counts: {}, fonts: [], targets: [], inlineLinks: 0,
    overflow: [], overflowCount: 0, gaps: [], contrast: [], composer: null,
    density: null, dice: null, docOverflow: null};

  const act = document.querySelector('.view.active');
  RES.screen = act ? act.id : null;
  RES.theme = document.documentElement.dataset.theme || 'base';

  const csCache = new Map();
  const CS = el => { let c = csCache.get(el); if (!c) { c = getComputedStyle(el); csCache.set(el, c); } return c; };
  const bgCache = new Map();

  const path = el => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else {
      const cls = (el.className && typeof el.className === 'string') ? el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
      if (cls) s += '.' + cls;
    }
    const host = el.closest('section.view');
    if (host && host.id) s = '#' + host.id + ' ' + s;
    return s;
  };
  const ownText = el => {
    let t = '';
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.textContent;
    return t.replace(/\s+/g, ' ').trim();
  };

  /* Visible = painted now. Hidden screens are display:none, so this is also
     "what the owner sees on this screen". */
  const visible = [];
  const boxes = new Map();
  for (const el of document.querySelectorAll('body *')) {
    const cs = CS(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) continue;
    /* Меню, уехавшее за край transform'ом, имеет размер, но его не видно:
       правый край левее нуля (или левый правее окна) — это не экран. */
    if (r.right <= 0 || r.left >= innerWidth) continue;
    /* Содержимое закрытого <details> браузер держит в дереве без отрисовки:
       размер есть, фона нет — замер контраста на нём врёт. Меряем только
       открытые группы (и их summary). */
    const closed = el.closest('details:not([open])');
    if (closed && !(el.tagName === 'SUMMARY' && el.parentElement === closed)) continue;
    visible.push(el);
    boxes.set(el, r);
  }

  /* ---------- 1. font-size < 14 ---------- */
  const smallFonts = {};
  for (const el of visible) {
    const text = ownText(el);
    if (!text) continue;
    const px = parseFloat(CS(el).fontSize);
    if (px < 13.95) {
      const key = path(el) + ' @' + px;
      if (!smallFonts[key]) smallFonts[key] = {sel: path(el), px: +px.toFixed(1), n: 0, sample: text.slice(0, 60)};
      smallFonts[key].n++;
    }
  }
  RES.fonts = Object.values(smallFonts).sort((a, b) => b.n - a.n);
  RES.counts.smallFonts = RES.fonts.reduce((s, f) => s + f.n, 0);
  RES.counts.smallFontKinds = RES.fonts.length;

  /* ---------- 2. hit targets ---------- */
  const TARGETS = 'button, a[href], summary, select, input:not([type=hidden]), textarea, [role="button"], [onclick], label[for]';
  const targetSet = new Set(Array.from(document.querySelectorAll(TARGETS)));
  const rows = [];
  RES.targets = [];
  for (const el of targetSet) {
    if (!boxes.has(el)) continue;
    const r = boxes.get(el);
    const cs = CS(el);
    const text = ((el.innerText || el.value || el.getAttribute('aria-label') || el.title || '') + '').replace(/\s+/g, ' ').trim().slice(0, 40);
    const parentText = el.parentElement ? (el.parentElement.textContent || '').replace(/\s+/g, ' ').trim() : '';
    const inline = cs.display === 'inline' && parentText.length > (el.textContent || '').length + 12;
    const row = {sel: path(el), w: +r.width.toFixed(1), h: +r.height.toFixed(1), text, inline};
    rows.push(row);
    if (inline) { RES.inlineLinks++; continue; }
    if (r.width < 43.5 || r.height < 43.5) RES.targets.push(row);
  }
  RES.counts.smallTargets = RES.targets.length;

  /* neighbour gap < 8 px between controls that share a parent */
  const seenGap = new Set();
  for (const el of targetSet) {
    if (!boxes.has(el) || !el.parentElement) continue;
    const r = boxes.get(el);
    if (r.width < 44 || r.height < 44) continue;
    for (const sib of el.parentElement.children) {
      if (sib === el || !targetSet.has(sib) || !boxes.has(sib)) continue;
      const q = boxes.get(sib);
      if (q.width < 44 || q.height < 44) continue;
      const dx = Math.max(0, Math.max(r.left, q.left) - Math.min(r.right, q.right));
      const dy = Math.max(0, Math.max(r.top, q.top) - Math.min(r.bottom, q.bottom));
      const horizontal = dy === 0 || (r.top < q.bottom && q.top < r.bottom);
      const gap = horizontal ? dx : dy;
      if (gap >= 0 && gap < 8) {
        const key = path(el) + '|' + path(sib);
        if (!seenGap.has(key)) { seenGap.add(key); RES.gaps.push({a: path(el), b: path(sib), gap: +gap.toFixed(1)}); }
      }
    }
  }
  RES.counts.tightGaps = RES.gaps.length;

  /* ---------- 3. overflow ---------- */
  /* Переполнение внутри собственного скролл-контейнера (широкая таблица) —
     это не горизонтальная прокрутка страницы, а штатный приём: такой элемент
     считаем отдельно (`contained`). Нарушение — только `bleed`. */
  for (const el of visible) {
    if (el === document.documentElement) continue;
    const d = el.scrollWidth - el.clientWidth;
    if (d >= 1 && el.clientWidth > 0) {
      const cs = CS(el);
      const contained = ['auto', 'scroll', 'hidden', 'clip'].includes(cs.overflowX);
      RES.overflow.push({sel: path(el), delta: d, client: el.clientWidth, scroll: el.scrollWidth,
        overflowX: cs.overflowX, contained, tag: el.tagName.toLowerCase()});
    }
  }
  RES.overflowCount = RES.overflow.filter(o => !o.contained).length;
  RES.counts.contained = RES.overflow.length - RES.overflowCount;
  RES.docOverflow = {scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth};
  RES.docOverflow.delta = RES.docOverflow.scroll - RES.docOverflow.client;

  /* ---------- contrast (text vs its painted background) ---------- */
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const parse = s => {
    const m = /rgba?\(([^)]+)\)/.exec(s);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const bodyBg = parse(CS(document.body).backgroundColor) || [255, 255, 255, 1];
  const bgOf = el => {
    const cached = bgCache.get(el);
    if (cached) return cached;
    let n = el, found = null;
    while (n) {
      const c = parse(CS(n).backgroundColor);
      if (c && c[3] >= 0.999) { found = c; break; }
      if (c && c[3] > 0) { if (!found) found = c; }
      n = n.parentElement;
    }
    const out = found || bodyBg;
    bgCache.set(el, out);
    return out;
  };
  const contrastSeen = {};
  for (const el of visible) {
    const text = ownText(el);
    if (!text) continue;
    const cs = CS(el);
    const fg = parse(cs.color);
    if (!fg) continue;
    const bg = bgOf(el);
    const L1 = lum(fg), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight, 10) >= 700;
    const need = (px >= 24 || (bold && px >= 18.66)) ? 3 : 4.5;
    if (ratio < need - 0.01) {
      const key = path(el) + '|' + cs.color + '|' + ratio.toFixed(2);
      if (!contrastSeen[key]) contrastSeen[key] = {sel: path(el), ratio: +ratio.toFixed(2), px: +px.toFixed(1), text: text.slice(0, 40)};
    }
  }
  RES.contrast = Object.values(contrastSeen);
  RES.counts.lowContrast = RES.contrast.length;

  /* ---------- 4. composer vs visual viewport ---------- */
  const field = act ? act.querySelector('#action-form textarea, #action-form input, form.composer textarea, form.composer input, textarea') : null;
  if (field && boxes.has(field)) {
    const r = boxes.get(field), cs = CS(field);
    const vv = window.visualViewport;
    RES.composer = {sel: path(field), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      top: +r.top.toFixed(1), bottom: +r.bottom.toFixed(1), fontSize: +parseFloat(cs.fontSize).toFixed(1),
      vvHeight: vv ? +vv.height.toFixed(1) : null, vvTop: vv ? +vv.offsetTop.toFixed(1) : null,
      innerHeight: innerHeight, docH: document.documentElement.scrollHeight,
      underKeyboard: vv ? (+r.bottom > vv.height + vv.offsetTop + 0.5) : null};
  }

  /* ---------- 5. density of the feed ---------- */
  if (act) {
    let best = null;
    for (const el of act.querySelectorAll('*')) {
      if (!boxes.has(el)) continue;
      const sh = el.scrollHeight, ch = el.clientHeight;
      if (ch > 60 && sh > ch + 20 && el.children.length >= 2) {
        if (!best || ch > best.clientHeight) best = el;
      }
    }
    if (best) {
      const kids = Array.from(best.children).filter(k => boxes.has(k));
      const heights = kids.map(k => boxes.get(k).height).filter(h => h > 0);
      RES.density = {container: path(best), clientH: best.clientHeight, scrollH: best.scrollHeight,
        screens: +(best.scrollHeight / best.clientHeight).toFixed(2), items: kids.length,
        avgItem: heights.length ? +(heights.reduce((a, b) => a + b, 0) / heights.length).toFixed(1) : null,
        docScreens: +(document.documentElement.scrollHeight / innerHeight).toFixed(2)};
    } else {
      RES.density = {container: null, docScreens: +(document.documentElement.scrollHeight / innerHeight).toFixed(2)};
    }
  }

  /* ---------- 6. dice face numbers ---------- */
  const faces = [];
  for (const el of document.querySelectorAll('[class*="die"],[class*="dice"],#tabletop-dice *')) {
    if (!boxes.has(el)) continue;
    const t = ownText(el);
    if (!/^\d{1,2}$/.test(t)) continue;
    const cs = CS(el), r = boxes.get(el);
    faces.push({sel: path(el), value: t, px: +parseFloat(cs.fontSize).toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1)});
  }
  RES.dice = {faces: faces.slice(0, 12), count: faces.length,
    minPx: faces.length ? Math.min(...faces.map(f => f.px)) : null};

  return JSON.stringify(RES);
})()
