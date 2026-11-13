/* n06 — меню: полоса на ПК (бренд + четыре экрана + «Ещё») и шторка «Ещё»
 * со всем остальным.
 *
 * Почему меню собирается здесь, а не правится в пяти местах. Состав меню
 * достраивают пять скриптов (obsidian.js, tabletop.js, adventure.js,
 * chronicle.js, worlds-panel.js): они раскладывают пункты по вложенным
 * details/div, переименовывают их по-своему и оставляют один экран под двумя
 * подписями. Из такой раскладки правилами CSS не получить ни ровной полосы,
 * ни столбца: у пункта в закрытом details подпись не рендерится, и кнопка
 * выглядит пустой. Поэтому здесь один список источников — атрибут data-view —
 * и одна раскладка: полоса (4 пункта + «Ещё») и две группы шторки.
 *
 * Узлы не клонируются: кнопка переносится вместе со своим onclick, поэтому
 * обработчики экранов остаются теми, что собрали скрипты.
 *
 * Переставлять пункты из MutationObserver нельзя без предохранителя: любая
 * вставка узла сама по себе childList, наблюдатель сработает снова. Поэтому
 * сравнивается отпечаток собранного меню (состав, подписи, положение группы
 * темы), а не «правильный образец»: после сборки он запоминается, и пока меню
 * ему соответствует, пересборки нет. Так сходятся и частый случай «чужой
 * скрипт переставил пункты» (отпечаток разошёлся — пересобрали), и случай
 * «пункта нет вовсе» (после сборки отпечаток стабилен — цикла нет).
 */
(() => {
  'use strict';
  const aside = document.querySelector('body > aside.sidebar');
  if (!aside) return;
  const nav = aside.querySelector('nav');
  if (!nav) return;

  /* Полоса на ПК: бренд + эти четыре кнопки + «Ещё» = шесть зон в полосе. */
  const BAR = [
    ['play', 'Игра'],
    ['campaigns', 'Кампании'],
    ['character', 'Персонаж'],
    ['master', 'Мастер'],
  ];

  /* Шторка: только экраны, подписи — те, что уже были в приложении.
     n13: подписи — словами игрока и одним словом там, где слово длинное:
     строка шторки на 375 — колонка 175 px, и «Производительность» рвалась
     посередине («Производительно/сть»), а «Настройки приложения» уходили во
     вторую строку (замечания №4 и №5 критика). «Нейросети и API», «Расходы
     API» — внутренние слова: их место заняли «Подключение Мастера» и
     «Расходы» (замечание №7). Короткие пары — те же, что в макете:
     «Навыки», «Инвентарь», «Правила», «Книги». */
  const GROUPS = [
    ['Кампания', [
      ['skills', 'Навыки'],
      ['inventory', 'Инвентарь'],
      ['world', 'Карта мира'],
      ['journal', 'Блокнот'],
      ['adventure-handouts', 'Раздатки и карты'],
      ['saves', 'Сохранения'],
    ]],
    ['Приложение', [
      /* n13: «Подключение Мастера» — первой строкой (chrome.css): в колонке
         подпись в 192 px рвалась на две строки (замечание №5 критика). */
      ['cloud', 'Подключение Мастера'],
      ['settings', 'Настройки'],
      /* n18: «Расходы» и «Скорость» — слова без предмета (замечание №5):
         в шторке читалось «Расходы» чего и «Скорость» чего. Названы по
         предмету: расходы на Мастера (платные ответы модели) и скорость его
         ответа (время хода, экран производительности). */
      ['costs', 'Расходы на Мастера'],
      ['performance', 'Скорость ответа'],
      ['about', 'О проекте'],
      ['worlds', 'Мои миры'],
      ['rulesets', 'Правила'],
      ['adventure-library', 'Книги'],
      ['master-library', 'Профили Мастера'],
    ]],
  ];
  const EXIT = ['exit', 'Выход из игры'];

  /* n18: состав шторки на телефоне — по SPEC §1.1: все 19 пунктов строками в
     двух вкладках, полосы четырёх кнопок нет. Прежде «Игра», «Кампании»,
     «Персонаж» и «Мастер» стояли в полосе внутри шторки (n13), а в «Кампании»
     оставалось 6 строк: половина листа была пустой, и «Выход» висел в воздухе.
     Теперь «Кампания» — 10 строк, «Приложение» — 9 + тема, как в макете.
     На ПК полоса прежняя: там она и есть главное меню (r224). */
  const PHONE_GROUPS = [
    ['Кампания', [
      ['play', 'Игра'],
      ['character', 'Персонаж'],
      ['skills', 'Навыки'],
      ['inventory', 'Инвентарь'],
      ['world', 'Карта мира'],
      ['journal', 'Блокнот'],
      ['adventure-handouts', 'Раздатки и карты'],
      ['saves', 'Сохранения'],
      ['rulesets', 'Правила'],
      ['master', 'Мастер'],
    ]],
    ['Приложение', [
      ['campaigns', 'Кампании'],
      ['settings', 'Настройки'],
      ['cloud', 'Подключение Мастера'],
      /* n18: «Расходы» и «Скорость» — слова без предмета (замечание №5 с
         кадра n16). Названы по предмету. */
      ['costs', 'Расходы на Мастера'],
      ['performance', 'Скорость ответа'],
      ['about', 'О проекте'],
      ['worlds', 'Мои миры'],
      ['adventure-library', 'Книги'],
      ['master-library', 'Профили Мастера'],
    ]],
  ];

  /* 760 — тот же порог, на котором включается телефонный слой (mobile.css). */
  const PHONE = window.matchMedia('(max-width: 760px)');

  /* Один экран — одна подпись: берём первый узел, дубли убираем из разметки. */
  function row(view, text) {
    const nodes = [...aside.querySelectorAll('[data-view]')].filter((node) => node.dataset.view === view);
    if (!nodes.length) return null;
    const node = nodes.shift();
    node.remove();
    for (const extra of nodes) extra.remove();
    node.classList.remove('more-item');
    node.classList.add('nav');
    /* Подпись ставится в существующий .nav-text: его ищут чужие скрипты
       (tabletop.js:8) и по нему же считается отпечаток. Иконки-картинки и
       символы-значки в подписи не нужны: на 13–14 px они читались как точки,
       а <img> в кнопке оставлял пустую зону слева. */
    const label = node.querySelector('.nav-text');
    if (label) {
      label.textContent = text;
      /* Убираем иконки-обёртки, но не предка самой подписи: если .nav-text
         когда-нибудь окажется вложенным, строка не должна опустеть. */
      for (const child of [...node.childNodes]) {
        if (child !== label && !(child.contains && child.contains(label))) child.remove();
      }
    } else {
      node.textContent = text;
    }
    return node;
  }

  function block(title, entries) {
    const box = document.createElement('div');
    box.className = 'nav-block';
    const label = document.createElement('p');
    label.className = 'nav-group-label';
    label.textContent = title;
    box.append(label);
    for (const [view, text] of entries) {
      const node = row(view, text);
      if (node) box.append(node);
    }
    return box;
  }

  /* Инструменты — не экраны, и в шторке им не место: «Кубики» и служебный
     блок разработчика раскрываются как <details>, а у пункта в закрытом
     <details> подпись не рендерится — ровно та пустая кнопка, из-за которой
     r251 и пришлось переделывать. Терять инструменты нельзя, поэтому бросок
     переезжает в «Блокнот» текущей кампании, технический блок — в «Настройки
     приложения». Оба обработчика остаются своими: узлы переносятся, не
     копируются. */
  function relocateTools() {
    const dice = nav.querySelector('#tabletop-dice') || aside.querySelector('#tabletop-dice');
    if (dice) {
      const journal = document.getElementById('journal');
      if (journal && !journal.contains(dice)) journal.append(dice);
    }
    const developer = nav.querySelector('#tabletop-advanced') || aside.querySelector('#tabletop-advanced');
    if (developer) {
      const settings = document.getElementById('settings');
      if (settings && !settings.contains(developer)) settings.append(developer);
    }
  }

  function rebuild() {
    /* n18: на телефоне полосы нет — все пункты стоят строками в двух
       вкладках (SPEC §1.1); на ПК — прежние четыре кнопки и «Ещё». */
    const phone = PHONE.matches;
    const groups = phone ? PHONE_GROUPS : GROUPS;
    const bar = document.createElement('div');
    bar.className = 'nav-bar';
    if (!phone) {
      for (const [view, text] of BAR) {
        const node = row(view, text);
        if (node) bar.append(node);
      }
    }

    const drawer = document.createElement('div');
    drawer.className = 'nav-drawer';
    drawer.id = 'nav-drawer';

    /* Телефонная шторка занимает весь экран, и «тап мимо» в ней невозможен:
       затемнение под шторкой не видно. Поэтому в шторке на телефоне есть
       явное закрытие (на ПК кнопка скрыта: там шторка — панель под полосой,
       и закрывает её клик в любом месте вне меню).
       n14: «Закрыть» — крестик в строке вкладок, а не отдельная строка. В
       «Приложении» на 375 было 12 строк, и «Выход» уезжал за низ окна
       (замечание №3 критика: 18 строк в шторке). Крестик занимает ту же
       площадь 44 × 44 и остаётся под пальцем в верхнем углу. */
    const head = document.createElement('div');
    head.className = 'nav-head';
    head.id = 'nav-head';
    const closeRow = document.createElement('button');
    closeRow.type = 'button';
    closeRow.className = 'nav-close';
    closeRow.textContent = '×';
    closeRow.setAttribute('aria-label', 'Закрыть меню');
    closeRow.title = 'Закрыть';
    closeRow.addEventListener('click', close);

    /* n14: шторка разведена на две вкладки — «Кампания» и «Приложение»
       (замечание №3 критика: 18 строк в две колонки, «Приложение» видно
       одновременно с «Кампанией», кадр menu-app был равен menu). Вкладка
       показывает одну группу в один столбец, в каждой ≤ 11 строк.
       Кнопки вкладок — НЕ `button.nav`: клик по .nav закрывает шторку
       (обработчик ниже), а переключение вкладки закрывать не должно. */
    const tabs = document.createElement('div');
    tabs.className = 'nav-tabs';
    tabs.id = 'nav-tabs';
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Разделы меню');

    const campaign = block(groups[0][0], groups[0][1]);
    campaign.classList.add('nav-block--campaign');
    const app = block(groups[1][0], groups[1][1]);
    app.classList.add('nav-block--app');

    function pick(tab) {
      drawer.dataset.tab = tab;
      for (const button of tabs.querySelectorAll('button.nav-tab')) {
        button.setAttribute('aria-selected', String(button.dataset.navTab === tab));
      }
      drawer.scrollTop = 0;
      aside.scrollTop = 0;
    }

    for (const [tab, text] of [['campaign', 'Кампания'], ['app', 'Приложение']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'nav-tab';
      button.dataset.navTab = tab;
      button.setAttribute('role', 'tab');
      button.textContent = text;
      button.addEventListener('click', () => pick(tab));
      tabs.append(button);
    }

    head.append(tabs, closeRow);
    drawer.append(head, campaign, app);

    /* Тема — контрол, а не экран: последняя строка группы, перед «Выходом». */
    const theme = aside.querySelector('#mobile-theme');
    if (theme) app.append(theme);
    /* «Выход» — последняя строка шторки. */
    const exit = row(EXIT[0], EXIT[1]);
    if (exit) {
      exit.classList.add('nav-exit');
      drawer.append(exit);
    }

    /* Открытая вкладка: всегда «Кампания». Шторку собирают заново после
       каждого изменения меню, и запомненная вкладка тут всё равно бы
       потерялась — а «Приложение» открывают вручную, когда оно нужно. */
    pick('campaign');

    /* Инструменты уводятся в экраны до того, как старая разметка меню будет
       выброшена: внутри nav их держат campaign-nav/tabletop-advanced. */
    relocateTools();

    nav.replaceChildren(bar, drawer);

    if (!phone) {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.id = 'nav-more';
      toggle.className = 'nav more-toggle';
      toggle.textContent = 'Ещё';
      toggle.setAttribute('aria-expanded', String(aside.classList.contains('show-more')));
      toggle.setAttribute('aria-controls', drawer.id);
      bar.append(toggle);
    }

    /* Отпечаток запоминаем здесь, а не в sync(): rebuild() зовут и снаружи
       (window.soloMenu.rebuild), и после ручного вызова «собрано» должно
       означать «собрано», иначе следующий тик наблюдателя соберёт ещё раз. */
    built = signature();
  }

  /* Опознание узла для отпечатка: экран — по data-view и подписи, прочее
     (группа темы, инструменты) — по id, без текста: текст темы меняется при
     переключении оформления, и пересобирать из-за этого меню нельзя. Класс в
     отпечаток не идёт: `.active` на пунктах переключает view() на каждый клик,
     и «навигация по классу» давала бы пересборку на ровном месте. */
  function mark(node) {
    if (node.dataset && node.dataset.view) {
      const text = node.querySelector('.nav-text') || node;
      return node.dataset.view + '=' + text.textContent.trim();
    }
    if (node.id) return node.id;
    if (node.classList.contains('nav-close')) return 'close';
    return node.tagName;
  }

  function signature() {
    if (nav.children.length !== 2) return '';
    const bar = nav.children[0];
    const drawer = nav.children[1];
    if (!bar.classList.contains('nav-bar') || !drawer.classList.contains('nav-drawer')) return '';
    const barSig = [...bar.children].map(mark).join('|');
    const drawerSig = [...drawer.children].map((child) => {
      if (!child.classList.contains('nav-block')) return mark(child);
      const title = child.querySelector('.nav-group-label');
      return '[' + (title ? title.textContent.trim() : '') + ']' +
        [...child.children].filter((item) => item !== title).map(mark).join(',');
    }).join('||');
    /* Место строки темы в отпечатке, без текста: текст меняется при
       переключении оформления, и пересобирать из-за него меню нельзя. Строку
       создаёт mobile.js на телефоне (он подключён в head с defer и успевает
       раньше chrome.js) и убирает при возврате на ширину стола. */
    const theme = aside.querySelector('#mobile-theme');
    const themeSig = theme ? (theme.closest('.nav-block') ? 'theme:block' : 'theme:outside') : 'theme:none';
    return barSig + '#' + drawerSig + '#' + themeSig;
  }

  /* Отпечаток последней собранной раскладки. Пустая строка — «ещё не собрано». */
  let built = '';

  function sync() {
    const now = signature();
    if (now && now === built) return;
    rebuild();
  }

  function close() {
    aside.classList.remove('show-more');
    /* На телефоне шторку открывает не «Ещё», а бургер из mobile.js — он
       помечает открытие классом на body и держит свой aria-expanded. */
    const sheet = document.body.classList.contains('mobile-menu-open');
    document.body.classList.remove('mobile-menu-open');
    const burger = document.getElementById('mobile-burger');
    if (burger) {
      burger.setAttribute('aria-expanded', 'false');
      /* Фокус не должен оставаться на кнопке, уехавшей за край экрана. */
      if (sheet) burger.focus({ preventScroll: true });
    }
    const toggle = nav.querySelector('#nav-more');
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  }

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target.closest && target.closest('#nav-more')) {
      /* sync() мог пересобрать меню: кнопку берём заново, иначе aria-expanded
         ушёл бы отсоединённому узлу. */
      sync();
      const toggle = nav.querySelector('#nav-more');
      if (!toggle) return;
      const open = aside.classList.toggle('show-more');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      return;
    }
    if (!aside.classList.contains('show-more')) return;
    const item = target.closest && target.closest('button.nav, a.nav');
    if (item) { close(); return; }
    if (!(target.closest && target.closest('aside.sidebar'))) close();
  }, true);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });

  /* Меню достраивается скриптами позже — сверяем отпечаток после каждого
     изменения. Через setTimeout, а не requestAnimationFrame: в headless-браузере
     кадры не тикают, и разметка оставалась несобранной. */
  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    setTimeout(() => {
      queued = false;
      sync();
    }, 0);
  }

  new MutationObserver(schedule).observe(aside, { childList: true, subtree: true });
  sync();
  window.addEventListener('load', schedule);
  /* Смена ширины меняет состав шторки (полоса ↔ строки). Отпечаток после
     изменения другой, поэтому sync() пересоберёт меню сам. */
  window.addEventListener('resize', schedule);
  window.soloMenu = { rebuild, close, signature: () => ({ built, now: signature() }) };
})();
