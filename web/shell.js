/* r235 — the shell. One bar, one rail, one drawer.
   Every existing menu button keeps its own node (and therefore its own
   handler); the shell only moves it. Nothing is cloned, nothing is dropped:
   19 menu buttons + «Выход из игры» are the same 20 the old sidebar had. */
(() => {
  'use strict';
  const aside = document.querySelector('aside.sidebar');
  if (!aside) return;
  const main = document.querySelector('main');
  const header = main && main.querySelector('header');
  const nav = aside.querySelector('nav');
  if (!main || !header || !nav) return;

  document.body.classList.add('solo-shell');

  const TITLES = {
    welcome: 'Solo Realms AI', play: 'Игра', character: 'Лист героя', skills: 'Навыки и способности',
    inventory: 'Инвентарь', world: 'Карта мира', journal: 'Блокнот', saves: 'Сохранения',
    master: 'Параметры кампании', rulesets: 'Правила', campaigns: 'Мои приключения',
    settings: 'Настройки', cloud: 'Нейросети и API', costs: 'Расходы API',
    performance: 'Производительность', 'adventure-library': 'Библиотека приключений',
    'master-library': 'Профили Мастера', 'adventure-handouts': 'Раздатки и карты',
    creation: 'Новое приключение', preparation: 'Подготовка книги', 'prototype-creation': 'Герой и приключение',
  };
  const BACK_VIEWS = new Set(['creation', 'preparation', 'prototype-creation', 'adventure-library', 'master-library']);
  const ICONS = {
    play: 'combat', character: 'person', skills: 'skills', inventory: 'inventory', world: 'map',
    journal: 'journal', saves: 'save', master: 'feather', rulesets: 'book', campaigns: 'chronicle',
    settings: 'settings', cloud: 'wizard', costs: 'coins', performance: 'heart',
    'adventure-library': 'book', 'master-library': 'feather', 'adventure-handouts': 'chest',
    exit: 'exit',
  };
  const icon = name => {
    if (window.soloIcon) return window.soloIcon(name);
    const img = el('img', undefined, 'obsidian-icon solo-icon');
    img.src = '/static/visual/icons/' + name + '.svg';
    img.alt = '';
    img.setAttribute('aria-hidden', 'true');
    return img;
  };

  /* ---------- bar: title, back, burger ---------- */
  const title = el('h1', 'Solo Realms AI', 'shell-title');
  const back = el('button', undefined, 'shell-icon-btn shell-back');
  back.type = 'button';
  back.setAttribute('aria-label', 'Назад');
  back.append(icon('arrows'));
  back.onclick = () => view('campaigns');
  const burger = el('button', undefined, 'shell-icon-btn shell-burger');
  burger.type = 'button';
  burger.id = 'solo-burger';
  burger.setAttribute('aria-label', 'Меню');
  burger.setAttribute('aria-expanded', 'false');
  for (let i = 0; i < 3; i++) burger.append(el('span', undefined, 'burger-line'));
  const crumbs = header.querySelector('.breadcrumb');
  const runtime = header.querySelector('.runtime');
  if (crumbs) crumbs.hidden = true;
  if (runtime) runtime.hidden = true;
  header.prepend(burger, back, title);

  /* ---------- rail ---------- */
  const rail = el('nav', undefined, undefined);
  rail.id = 'solo-rail';
  rail.setAttribute('aria-label', 'Основные разделы');
  document.body.prepend(rail);

  /* ---------- drawer ---------- */
  const drawer = el('div');
  drawer.id = 'solo-drawer';
  drawer.setAttribute('role', 'dialog');
  drawer.setAttribute('aria-modal', 'true');
  drawer.setAttribute('aria-label', 'Меню приложения');
  const scrim = el('div', undefined, 'drawer-scrim');
  const panel = el('div', undefined, 'drawer-panel');
  const head = el('div', undefined, 'drawer-head');
  head.append(el('h2', 'Меню'));
  const close = el('button', undefined, 'shell-icon-btn');
  close.type = 'button';
  close.setAttribute('aria-label', 'Закрыть меню');
  /* One stroke, one 24 px size — the same weight as every icon in the frame. */
  close.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  head.append(close);
  const tabs = el('div', undefined, 'drawer-tabs');
  tabs.setAttribute('role', 'tablist');
  const tabCampaign = el('button', 'Кампания', 'drawer-tab');
  tabCampaign.type = 'button';
  tabCampaign.setAttribute('role', 'tab');
  tabCampaign.dataset.pane = 'campaign';
  const tabApp = el('button', 'Приложение', 'drawer-tab');
  tabApp.type = 'button';
  tabApp.setAttribute('role', 'tab');
  tabApp.dataset.pane = 'app';
  tabs.append(tabCampaign, tabApp);
  const body = el('div', undefined, 'drawer-body');
  const paneCampaign = el('div', undefined, 'drawer-pane');
  paneCampaign.dataset.pane = 'campaign';
  const paneApp = el('div', undefined, 'drawer-pane');
  paneApp.dataset.pane = 'app';
  body.append(paneCampaign, paneApp);
  panel.append(head, tabs, body);
  drawer.append(scrim, panel);
  document.body.append(drawer);

  /* Every button the old sidebar had, by data-view, in its original node. */
  const originals = [...nav.querySelectorAll('button[data-view]')];
  /* A button can only be placed once (it is moved, not copied), but the rail
     must still be able to press it afterwards — hence the registry. */
  const placed = new Map();
  const take = name => {
    const found = originals.find(b => b.dataset.view === name && !b.dataset.shellPlaced);
    if (found) { found.dataset.shellPlaced = 'yes'; placed.set(name, found); }
    return found;
  };
  function row(button, iconName) {
    if (!button) return null;
    button.classList.add('drawer-row');
    if (!button.querySelector('img') && !button.querySelector('svg')) button.prepend(icon(iconName));
    const text = button.querySelector('.nav-text');
    if (!text && button.textContent.trim()) {
      const label = button.textContent.trim();
      button.textContent = '';
      button.append(el('span', label, 'nav-text'));
    }
    return button;
  }

  const CAMPAIGN = [['play', 'Игра'], ['character', 'Лист героя'], ['skills', 'Навыки'],
    ['inventory', 'Инвентарь и экипировка'], ['world', 'Карта мира'], ['journal', 'Блокнот'],
    ['adventure-handouts', 'Раздатки и карты'], ['saves', 'Сохранения'],
    ['rulesets', 'Правила кампании'], ['master', 'Параметры кампании']];
  for (const [name, label] of CAMPAIGN) {
    const button = take(name);
    if (!button) continue;
    const span = button.querySelector('.nav-text');
    if (span) span.textContent = label;
    paneCampaign.append(row(button, ICONS[name]));
  }
  /* The dice are one control, not a menu row plus a button: the rail enters
     the same block that lives here. */
  const dice = nav.querySelector('#tabletop-dice');
  if (dice) {
    dice.classList.add('drawer-group');
    const summary = dice.querySelector('summary');
    if (summary) {
      summary.textContent = '';
      summary.append(icon('perception'), el('span', 'Кубики', 'nav-text'));
    }
    paneCampaign.append(dice);
  }

  const APP = [['campaigns', 'Мои приключения'], ['settings', 'Настройки приложения'],
    ['cloud', 'Нейросети и API'], ['costs', 'Расходы API'], ['performance', 'Производительность'],
    ['adventure-library', 'Библиотека приключений'], ['rulesets', 'Библиотека правил'],
    ['master-library', 'Профили Мастера']];
  for (const [name, label] of APP) {
    const button = take(name);
    if (!button) continue;
    const span = button.querySelector('.nav-text');
    if (span) span.textContent = label;
    paneApp.append(row(button, ICONS[name]));
  }

  /* The brand link was the only way home and it lived in the old sidebar. */
  const home = el('button', undefined, 'drawer-row');
  home.type = 'button';
  home.id = 'solo-home';
  home.append(icon('compass'), el('span', 'Главная', 'nav-text'));
  home.onclick = () => view('welcome');
  paneApp.append(home);

  /* «О проекте» was both a menu line and a footer line. One entry now, and it
     is the colophon pane the settings screen already owns. */
  const about = el('button', undefined, 'drawer-row');
  about.type = 'button';
  about.id = 'solo-about';
  about.append(icon('book'), el('span', 'О проекте', 'nav-text'));
  about.onclick = () => {
    const settings = originals.find(b => b.dataset.view === 'settings');
    settings?.click();
    const pane = [...document.querySelectorAll('.settings-tabs button')].find(b => /О программе/.test(b.textContent));
    pane?.click();
    closeDrawer();
  };
  paneApp.append(about);

  /* Advanced tools stay reachable, but behind one collapsed row instead of a
     second «Advanced / Developer» heading next to «О проекте». */
  const advanced = nav.querySelector('#tabletop-advanced');
  if (advanced) {
    advanced.classList.add('drawer-group');
    const summary = advanced.querySelector('summary');
    if (summary) {
      summary.textContent = 'Дополнительно';
      summary.prepend(icon('feather'));
    }
    paneApp.append(advanced);
  }

  /* «Оформление» lives in the drawer's «Приложение» tab (SPEC §1). The real
     control is the one in «Настройки приложения» → «Внешний вид»; this row
     drives that same select, so there is still exactly one setting. */
  const themeSource = document.getElementById('theme');
  if (themeSource) {
    const look = el('div', undefined, 'drawer-section');
    const label = el('label', 'Оформление');
    const select = el('select');
    select.id = 'solo-drawer-theme';
    for (const option of themeSource.options) {
      const copy = el('option', option.textContent);
      copy.value = option.value;
      select.append(copy);
    }
    select.value = themeSource.value;
    select.onchange = () => {
      themeSource.value = select.value;
      themeSource.dispatchEvent(new Event('change', { bubbles: true }));
    };
    label.append(select);
    look.append(label);
    paneApp.append(look);
  }

  const exit = take('exit');
  if (exit) {
    exit.classList.add('drawer-exit');
    panel.append(exit);
  }

  /* ---------- rail entries: the same four screens + «Ещё» ---------- */
  function railButton(iconName, label, onClick) {
    const b = el('button', undefined, 'rail-btn');
    b.type = 'button';
    b.title = label;
    b.setAttribute('aria-label', label);
    b.append(icon(iconName));
    /* A rail press is the opener, so closing the drawer can hand the focus back
       to the same rail entry (a programmatic press leaves `body` focused). */
    b.onclick = () => { b.focus(); onClick(); };
    b.dataset.rail = label;
    rail.append(b);
    return b;
  }
  /* The rail presses the very same node the drawer holds. `take` hands a node
     out once only, so the registry is the way back to it. */
  const railFor = (name, iconName, label) => railButton(iconName, label, () => (placed.get(name) || take(name))?.click());
  railFor('play', 'combat', 'Игра');
  railFor('character', 'person', 'Персонаж');
  railFor('journal', 'journal', 'Блокнот');
  railButton('perception', 'Кубики', () => {
    openDrawer('campaign', dice);
  });
  railButton('stairs', 'Ещё', () => openDrawer('app', home));
  rail.append(el('div', undefined, 'rail-sep'));

  /* ---------- drawer behaviour ---------- */
  let opener = null;
  function setPane(name) {
    for (const tab of [tabCampaign, tabApp]) {
      const on = tab.dataset.pane === name;
      tab.setAttribute('aria-selected', String(on));
      tab.tabIndex = on ? 0 : -1;
    }
    paneCampaign.classList.toggle('active', name === 'campaign');
    paneApp.classList.toggle('active', name === 'app');
  }
  tabCampaign.onclick = () => setPane('campaign');
  tabApp.onclick = () => setPane('app');
  function openDrawer(pane = 'campaign', focus) {
    opener = document.activeElement;
    drawer.classList.add('open');
    burger.setAttribute('aria-expanded', 'true');
    setPane(pane);
    const target = focus || (pane === 'campaign' ? paneCampaign.firstElementChild : paneApp.firstElementChild);
    if (focus && focus.tagName === 'DETAILS') focus.open = true;
    requestAnimationFrame(() => {
      if (focus) {
        focus.scrollIntoView({ block: 'center' });
        const first = focus.querySelector('summary, button');
        first?.focus();
      } else {
        target?.focus();
      }
    });
  }
  function closeDrawer() {
    if (!drawer.classList.contains('open')) return;
    drawer.classList.remove('open');
    burger.setAttribute('aria-expanded', 'false');
    /* Keyboard and pointer both open from a focused control. If the drawer was
       opened programmatically (no focused opener, `body`), the visible rail
       «Ещё» or the burger is the place the keyboard returns to. */
    const fallback = [...rail.querySelectorAll('.rail-btn')].find(b => b.dataset.rail === 'Ещё' && b.offsetParent !== null) || burger;
    const back = opener && opener !== document.body && document.contains(opener) ? opener : fallback;
    opener = null;
    back?.focus?.();
  }
  burger.onclick = () => openDrawer('campaign');
  close.onclick = closeDrawer;
  scrim.onclick = closeDrawer;
  drawer.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.stopPropagation(); closeDrawer(); }
    if (event.key !== 'Tab') return;
    const focusable = [...drawer.querySelectorAll('button:not([disabled]), select, summary')].filter(n => n.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
    else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && drawer.classList.contains('open')) closeDrawer();
  });
  /* A menu row is a door: it closes the drawer before the screen changes.
     The tabs are not doors — they only switch the pane. «Выход из игры» is a
     door too, but it lives in the fixed foot outside the pane. */
  drawer.addEventListener('click', event => {
    if (event.target.closest('.drawer-row, .drawer-exit')) closeDrawer();
  });

  /* ---------- the bar follows the screen ---------- */
  const baseView = view;
  window.view = view = function (name) {
    baseView(name);
    /* The old layer refuses some screens when no campaign is open. The bar must
       name the screen that actually stands, not the one that was asked for. */
    const shown = document.querySelector('.view.active')?.id || name;
    title.textContent = TITLES[shown] || 'Solo Realms AI';
    document.body.classList.toggle('has-back', BACK_VIEWS.has(shown));
    for (const b of rail.querySelectorAll('.rail-btn')) {
      const target = b.dataset.rail;
      b.classList.toggle('active', (target === 'Игра' && shown === 'play') ||
        (target === 'Персонаж' && shown === 'character') || (target === 'Блокнот' && shown === 'journal'));
    }
    if (shown === 'welcome') loadContinue();
  };
  view(document.querySelector('.view.active')?.id || 'welcome');

  /* ---------- «Продолжить» row on the main screen ---------- */
  let continueButton = null;
  async function loadContinue() {
    const welcome = document.getElementById('welcome');
    if (!welcome || !welcome.classList.contains('active')) return;
    let data;
    try { data = await api('/chronicles'); } catch { return; }
    const last = (data.campaigns || [])[0];
    continueButton?.remove();
    continueButton = null;
    if (!last) return;
    const button = el('button', undefined, 'home-continue');
    button.type = 'button';
    const text = el('span', undefined, 'continue-text');
    const meta = [last.hero, last.class_name, last.scene].filter(Boolean).join(' · ') || 'Ход сохранён';
    text.append(el('span', 'Продолжить', 'continue-kicker'), el('span', last.name, 'continue-name'), el('span', meta, 'continue-meta'));
    button.append(text, el('span', '→', 'continue-go'));
    button.onclick = protect(async () => {
      campaignId = last.id;
      localStorage.setItem('solo-campaign', last.id);
      await refresh();
      view('play');
    });
    continueButton = button;
    const start = welcome.querySelector('.home-start');
    (start || welcome).after(button);
  }

  /* ---------- the main screen: one action, one secondary link ---------- */
  const start = document.querySelector('#welcome .chronicle-start');
  if (start) {
    const description = start.querySelector('p');
    if (description) {
      description.textContent = 'Выберите сохранённое приключение или начните новое. Мир, решения и история героя остаются в ваших руках.';
    }
    const primary = start.querySelector('button.primary');
    if (primary) {
      primary.textContent = 'Начать историю';
      primary.classList.add('home-primary');
      const book = el('button', 'Загрузить книгу', 'home-link');
      book.type = 'button';
      book.onclick = () => {
        const open = window.soloOpenPreparation;
        const go = () => document.getElementById('creation-files')?.click();
        if (open) Promise.resolve(open()).then(() => setTimeout(go, 150));
      };
      const wrap = el('div', undefined, 'home-start');
      primary.before(wrap);
      wrap.append(primary, book);
      /* «Мои приключения» is a menu row now, not a second button here. */
      for (const b of [...start.querySelectorAll('button')]) if (b !== primary && !b.classList.contains('home-link')) b.remove();
    }
    const heading = start.querySelector('h2');
    if (heading) heading.textContent = 'Новое приключение';
  }

  /* One footer line, no repetition of menu entries. */
  const footer = main.querySelector('footer');
  if (footer) footer.textContent = 'Истории и книги остаются у вас · Прототип 2.2.0';

  /* ---------- colophon: one icon set, licence named ---------- */
  const aboutPane = document.querySelector('[data-settings-pane="about"]');
  if (aboutPane && !aboutPane.querySelector('.icon-licence')) {
    const link = el('a', 'game-icons.net — Lorc, Delapouite (CC BY 3.0)');
    link.href = '/static/visual/ATTRIBUTION.md';
    link.className = 'icon-licence';
    const line = el('p', 'Иконки: ');
    line.className = 'hint icon-licence';
    line.append(link);
    aboutPane.append(line);
  }

  /* A campaign that is not open cannot own campaign rows: the tabletop layer
     already disables them, and the drawer shows the same state. */
  document.addEventListener('solo:render', () => {
    const open = Boolean(document.body.classList.contains('campaign-open'));
    tabCampaign.hidden = false;
    tabCampaign.title = open ? '' : 'Кампания ещё не открыта';
  });

  loadContinue();
})();
