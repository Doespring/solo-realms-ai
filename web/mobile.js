/* r230 — телефонный слой: высота экрана, бургер, заголовок шапки, лента.

   Это не логика игры: файл трогает только представление — где стоит меню,
   какая высота у экрана, куда прокручена лента. Обработчики экранов не
   переопределяются, узлы меню не клонируются: бургер нажимает те же кнопки
   `[data-view]`, что и столбец. На столе (> 760 px) слой молчит. */
(() => {
  'use strict';
  const PHONE = '(max-width: 760px)';
  const phone = window.matchMedia(PHONE);
  const body = document.body;
  const root = document.documentElement;

  /* ---------- 1. Высота: visualViewport, а не 100vh ----------
     На iOS клавиатура сжимает только visualViewport, layout остаётся 812;
     поэтому высоту берём у него, иначе док уезжает под клавиатуру.

     Пишем только при изменении: `--vvh` меняет высоту body, а любая запись
     стиля в обработчике resize/scroll — это готовый цикл (замер: кадр
     крутился в 25 % CPU и не отдавал управление замеру). */
  let lastH = -1;
  let lastKb = null;
  function syncHeight() {
    const view = window.visualViewport;
    const height = Math.round(view ? view.height : window.innerHeight);
    if (height !== lastH) {
      lastH = height;
      root.style.setProperty('--vvh', height + 'px');
      /* Клавиатура открылась или закрылась — лента едет к последнему
         сообщению: иначе видно не то, что читаешь, а середину. */
      if (document.activeElement && document.activeElement.id === 'player-input') {
        requestAnimationFrame(() => toBottom(false));
      }
    }
    const gap = view ? window.innerHeight - view.height : 0;
    const kb = gap > 80;
    if (kb !== lastKb) {
      lastKb = kb;
      body.classList.toggle('kb-open', kb);
    }
  }
  syncHeight();
  window.addEventListener('resize', syncHeight);
  window.addEventListener('orientationchange', syncHeight);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', syncHeight);

  /* ---------- 2. Бургер и шторка ---------- */
  const aside = document.querySelector('body > aside.sidebar');
  const header = document.querySelector('main > header');
  let burger = null;
  let scrim = null;
  let paintTheme = null;

  function setMenu(open) {
    body.classList.toggle('mobile-menu-open', open);
    if (burger) burger.setAttribute('aria-expanded', String(open));
    if (open && aside) { aside.scrollTop = 0; if (paintTheme) paintTheme(); }
    else if (burger) burger.focus({ preventScroll: true });
  }

  function build() {
    if (!aside || !header || !phone.matches) return;
    if (!burger) {
      burger = document.createElement('button');
      burger.type = 'button';
      burger.id = 'mobile-burger';
      burger.className = 'm-burger';
      burger.setAttribute('aria-label', 'Меню');
      burger.setAttribute('aria-expanded', 'false');
      for (let i = 0; i < 3; i++) burger.append(document.createElement('span'));
      burger.addEventListener('click', () => setMenu(!body.classList.contains('mobile-menu-open')));
      header.prepend(burger);
      scrim = document.createElement('div');
      scrim.className = 'm-scrim';
      scrim.addEventListener('click', () => setMenu(false));
      body.append(scrim);
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && body.classList.contains('mobile-menu-open')) setMenu(false);
      });
      aside.addEventListener('click', event => {
        if (event.target.closest('button[data-view], a[href]')) setMenu(false);
      });
      themeRow();
    }
    syncTitle();
  }

  /* Тема. На столе её переключатель стоит в шапке, на телефоне в шапке ему
     места нет: над лентой остаются только меню и «Лист». Строка в шторке
     зовёт ту же applyTheme из play.js — своего состояния темы здесь нет,
     иначе выбор разошёлся бы с тем, что видят остальные экраны. */
  function themeRow() {
    if (!aside) return;
    const old = aside.querySelector('#mobile-theme');
    // Ушли на стол (или окно расширили) — строке темы в полосе меню не место:
    // там переключатель стоит в шапке, а этот занимал бы всю строку.
    if (!phone.matches) { if (old) old.remove(); return; }
    if (old) return;
    const row = document.createElement('button');
    row.type = 'button';
    row.id = 'mobile-theme';
    row.className = 'nav';
    const paint = () => {
      const light = root.dataset.theme === 'light' || window.soloTheme === 'light';
      row.textContent = light ? 'Оформление: светлое' : 'Оформление: тёмное';
      row.setAttribute('aria-pressed', String(light));
    };
    row.addEventListener('click', () => {
      const next = (root.dataset.theme === 'light') ? 'dark' : 'light';
      if (typeof window.applyTheme === 'function') window.applyTheme(next);
      else { root.dataset.theme = next; try { localStorage.setItem('solo-theme', next); } catch (e) {} }
      paint();
    });
    aside.append(row);
    paintTheme = paint;
    paint();
  }

  /* Заголовок шапки = имя открытого экрана. Крошки на телефоне не читаются:
     в шапке название и, на игровом экране, вторая строка «глава · место».
     Имя берём у того же пункта меню, что и шторка. */
  /* n17: вторая строка шапки игры. Глава — название главы книги: play.js
     пишет его в `#chapter-label` из preparation.library (у свободной игры
     главы нет), место — сцена, которую называет `#scene-title`. Пустые части
     не печатаем, поэтому в свободной игре строка — просто место. */
  function sceneLine() {
    const chapter = (document.querySelector('#chapter-label')?.textContent || '').trim();
    // Место игрок видит в сцене: `#place-label` пишет play.js из `state.location`
    // (прежний `#scene-title` из разметки удалён вместе с дублирующим заголовком).
    const place = (document.querySelector('#place-label')?.textContent
      || document.querySelector('#scene-title')?.textContent || '').trim();
    return [chapter, place].filter(Boolean).join(' · ');
  }

  function syncTitle() {
    if (!header || !phone.matches) return;
    let title = header.querySelector('.m-title');
    if (!title) {
      title = document.createElement('div');
      title.className = 'm-title';
      header.insertBefore(title, header.children[1] || null);
    }
    const view = document.querySelector('body > main > .view.active');
    const name = view ? view.id : '';
    const buttons = [...document.querySelectorAll(`[data-view="${name}"]`)];
    const named = buttons.find(b => (b.innerText || '').trim());
    const heading = view ? view.querySelector('h1, h2') : null;
    // n03: на игровом экране подпись — название истории, а не слово «Игра»:
    // по макету `game` шапка называет приключение, в котором вы находитесь.
    const story = name === 'play' ? (document.querySelector('#campaign-label')?.textContent || '') : '';
    // n04: на главной шапка называет приложение (макет `home`), а не повторяет
    // заголовок «Тихая история на целый вечер» второй раз.
    const home = name === 'welcome' ? 'Solo Realms AI' : '';
    // n08: у листа героя своего H1 больше нет (надзаголовок и H1 дублировали
    // вкладку «Лист героя»), поэтому подпись шапки берётся отсюда.
    const sheet = name === 'character' ? 'Лист героя' : '';
    // n17: две строки вместо одного узла с текстом — название не режется
    // многоточием там, где под ним есть ещё подзаголовок.
    let line = title.querySelector('.m-title__name');
    if (!line) {
      title.textContent = '';
      line = document.createElement('span');
      line.className = 'm-title__name';
      title.append(line);
    }
    line.textContent = (story || home || sheet || (named ? named.innerText : heading ? heading.innerText : name)).trim();
    const second = name === 'play' ? sceneLine() : '';
    let sub = title.querySelector('.m-sub');
    if (second) {
      if (!sub) {
        sub = document.createElement('span');
        sub.className = 'm-sub';
        title.append(sub);
      }
      sub.textContent = second;
    } else if (sub) {
      sub.remove();
    }
  }

  /* ---------- 3. Лента игры: к последнему сообщению ---------- */
  function chatBox() { return document.querySelector('body > main > .view#play #chat'); }
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
      const nudge = () => {
        syncHeight();
        requestAnimationFrame(() => requestAnimationFrame(() => toBottom(false)));
      };
      setTimeout(nudge, 60);
      setTimeout(nudge, 330);
    }
  });

  const chat = chatBox();
  if (chat) {
    let stick = true;
    chat.addEventListener('scroll', () => {
      stick = chat.scrollHeight - chat.clientHeight - chat.scrollTop < 120;
    }, { passive: true });
    if (window.MutationObserver) {
      new MutationObserver(() => { if (stick) toBottom(false); }).observe(chat, { childList: true, subtree: true });
    }
  }

  /* ---------- 4. Переключение экрана: заголовок, высота, лента ---------- */
  if (typeof window.view === 'function') {
    const original = window.view;
    window.view = function () {
      const result = original.apply(this, arguments);
      syncHeight();
      syncTitle();
      setTimeout(() => { if (document.querySelector('body > main > .view#play.active')) toBottom(false); }, 0);
      return result;
    };
  }
  document.addEventListener('click', event => {
    if (event.target.closest('[data-view], .start__btn, .start__continue')) {
      setTimeout(() => { syncTitle(); syncHeight(); }, 300);
    }
  });
  // Название истории приходит позже шапки: play.js пишет его в #campaign-label
  // после ответа /campaigns/<id>. Без этой подписки на холодной перезагрузке в
  // шапке стояло «Новая история» из разметки до первого клика.
  document.addEventListener('solo:render', () => { syncTitle(); });

  phone.addEventListener('change', () => {
    setMenu(false);
    if (phone.matches) build();
    // Ушли на стол — строку темы из шторки убираем: в горизонтальной полосе
    // меню она растянулась бы во всю ширину. Вернулись — build() создаст снова.
    themeRow();
    syncHeight();
  });

  /* n17: шапка доступна проверке и другим экранам: play.js зовёт syncTitle()
     после того, как записал название кампании, главу и место. */
  window.soloHeader = { syncTitle };

  function boot() {
    build();
    syncTitle();
    syncHeight();
    if (document.querySelector('body > main > .view#play.active')) toBottom(false);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
