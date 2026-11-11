/* r230 — телефонный слой: высота экрана, меню без каркаса, лента и кубик.

   Это не логика игры: файл трогает только представление — какой высоты экран,
   где стоит поле ввода, куда прокручена лента и каким кеглем напечатано число
   на кубике. Обработчики экранов не переопределяются, узлы не клонируются:
   бургер нажимает те же пункты меню, что и рабочий стол.

   Слой включается только на узком экране; на рабочем столе он молчит. */
(() => {
  'use strict';
  const PHONE = '(max-width: 760px)';
  const phone = window.matchMedia(PHONE);
  const root = document.documentElement;
  const body = document.body;
  const notice = document.querySelector('body > .notice');

  /* ---------- 1. Высота экрана: visualViewport, а не 100vh ----------
     На Android клавиатура сжимает layout viewport, на iOS — только
     visualViewport. Высота берётся у visualViewport, поэтому поле ввода
     остаётся над клавиатурой на обоих. */
  function syncHeight() {
    const view = window.visualViewport;
    const height = Math.round(view ? view.height : window.innerHeight);
    root.style.setProperty('--vvh', height + 'px');
    root.style.setProperty('--notice-h', (notice ? notice.offsetHeight : 0) + 'px');
    if (view && window.innerHeight - view.height > 60) body.classList.add('kb-open');
    else body.classList.remove('kb-open');
  }
  syncHeight();
  window.addEventListener('resize', syncHeight);
  window.addEventListener('orientationchange', syncHeight);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', syncHeight);
    window.visualViewport.addEventListener('scroll', syncHeight);
  }
  if (notice && window.ResizeObserver) new ResizeObserver(syncHeight).observe(notice);

  /* ---------- 2. Экран игры занимает ровно один экран ---------- */
  function syncPlay() {
    const play = document.getElementById('play');
    const on = Boolean(play && play.classList.contains('active') && phone.matches);
    body.classList.toggle('play-fixed', on);
    syncHeight();
    if (on) requestAnimationFrame(() => toBottom(false));
  }
  phone.addEventListener('change', () => {
    body.classList.remove('menu-open');
    syncPlay();
  });

  /* ---------- 3. Лента: фокус и новый ход прокручивают к последнему ---------- */
  function chatBox() {
    return document.getElementById('chat');
  }
  function nearBottom(box) {
    return box.scrollHeight - box.clientHeight - box.scrollTop < 120;
  }
  function toBottom(smooth) {
    const box = chatBox();
    if (!box) return;
    box.style.scrollBehavior = smooth ? 'smooth' : 'auto';
    box.scrollTop = box.scrollHeight;
    if (smooth) setTimeout(() => { box.style.scrollBehavior = ''; }, 350);
  }
  document.addEventListener('focusin', event => {
    if (event.target && event.target.id === 'player-input') {
      /* Клавиатура открывается после фокуса: ждём её, потом прокручиваем. */
      setTimeout(() => { syncHeight(); toBottom(false); }, 60);
      setTimeout(() => { syncHeight(); toBottom(false); }, 320);
    }
  });
  const chat = chatBox();
  if (chat && window.MutationObserver) {
    let stick = true;
    chat.addEventListener('scroll', () => { stick = nearBottom(chat); }, { passive: true });
    new MutationObserver(() => { if (stick) toBottom(false); }).observe(chat, { childList: true, subtree: true });
  }

  /* ---------- 4. Кубик: число крупно, формула рядом ---------- */
  function bigDice() {
    for (const row of document.querySelectorAll('#tabletop-dice .dice-entry')) {
      if (row.dataset.r230) continue;
      const text = row.textContent || '';
      const at = text.indexOf('=');
      if (at < 0) continue;
      const bracket = text.indexOf('[', at);
      const total = (bracket < 0 ? text.slice(at + 1) : text.slice(at + 1, bracket)).trim();
      const rest = bracket < 0 ? '' : text.slice(bracket);
      const formula = document.createElement('span');
      formula.className = 'dice-formula';
      formula.textContent = text.slice(0, at).trim();
      const value = document.createElement('span');
      value.className = 'dice-total';
      value.textContent = total;
      const dice = document.createElement('span');
      dice.className = 'dice-formula';
      dice.textContent = rest;
      row.replaceChildren(formula, value, dice);
      row.dataset.r230 = '1';
      row.classList.add('dice-row');
    }
  }

  /* ---------- 5. Меню без каркаса r235: бургер и шторка ----------
     На дереве с каркасом (body.solo-shell) этой ветки не существует: там свой
     бургер и своя шторка. Здесь — прежний столбец-меню уезжает за край, а в
     шапке появляется кнопка 44×44. Пункты меню — те же узлы, обработчики целы. */
  function buildMenu() {
    if (body.classList.contains('solo-shell')) return;
    const aside = document.querySelector('aside.sidebar');
    const header = document.querySelector('main > header');
    if (!aside || !header || document.getElementById('mobile-burger')) return;
    const burger = document.createElement('button');
    burger.type = 'button';
    burger.id = 'mobile-burger';
    burger.className = 'mobile-burger';
    burger.setAttribute('aria-label', 'Меню');
    burger.setAttribute('aria-expanded', 'false');
    for (let i = 0; i < 3; i++) burger.append(document.createElement('span'));
    header.prepend(burger);

    const scrim = document.createElement('div');
    scrim.className = 'mobile-scrim';
    document.body.append(scrim);

    const set = open => {
      body.classList.toggle('menu-open', open);
      burger.setAttribute('aria-expanded', String(open));
      if (open) aside.scrollTop = 0;
      else burger.focus({ preventScroll: true });
    };
    burger.onclick = () => set(!body.classList.contains('menu-open'));
    scrim.onclick = () => set(false);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && body.classList.contains('menu-open')) set(false);
    });
    /* Пункт меню — дверь: сначала закрываем, потом экран меняется сам. */
    aside.addEventListener('click', event => {
      if (event.target.closest('button[data-view], a.nav, button.quiet')) set(false);
    });
  }

  /* ---------- 6. Один наблюдатель на всё, что перерисовывается ---------- */
  const originalView = window.view;
  window.view = function (name) {
    const result = originalView.apply(this, arguments);
    syncPlay();
    setTimeout(bigDice, 0);
    return result;
  };
  document.addEventListener('solo:render', () => { syncPlay(); bigDice(); });

  function boot() {
    buildMenu();
    syncPlay();
    bigDice();
    if (window.MutationObserver) {
      new MutationObserver(() => bigDice()).observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
