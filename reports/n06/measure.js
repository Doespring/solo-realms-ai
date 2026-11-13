(() => {
  const aside = document.querySelector('body > aside.sidebar');
  const nav = aside.querySelector('nav');
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    if (!(rect.width > 1 && rect.height > 1)) return false;
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) <= 0.05) return false;
    const cx = Math.min(Math.max(rect.left + rect.width / 2, 0), innerWidth - 1);
    const cy = Math.min(Math.max(rect.top + rect.height / 2, 0), innerHeight - 1);
    if (cx < rect.left || cx > rect.right || cy < rect.top || cy > rect.bottom) return false;
    const hit = document.elementFromPoint(cx, cy);
    return !!(hit && (hit === node || node.contains(hit) || hit.contains(node)));
  };
  const text = (node) => (node.innerText || '').trim().replace(/\s+/g, ' ');
  const rows = [...aside.querySelectorAll('button,a,summary')];
  const inAside = rows.filter(visible);
  const inNav = [...nav.querySelectorAll('button,a,summary')].filter(visible);
  const rectOf = (node) => { const r = node.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const buttonLike = (node) => ['BUTTON', 'A', 'SUMMARY'].includes(node.tagName);
  const emptyAll = [...document.querySelectorAll('button,a,summary')].filter((x) => !x.innerText.trim());
  const emptyVisible = emptyAll.filter(visible);
  const emptyAside = rows.filter((x) => !x.innerText.trim());
  return {
    viewport: innerWidth + 'x' + innerHeight,
    showMore: aside.classList.contains('show-more'),
    mobileMenuOpen: document.body.classList.contains('mobile-menu-open'),
    strip: inAside.map((n) => ({ text: text(n) || '⟨пусто⟩', view: n.dataset ? (n.dataset.view || '') : '', rect: rectOf(n), tag: n.tagName.toLowerCase() })),
    navVisible: inNav.length,
    empty: { docAll: emptyAll.length, docVisible: emptyVisible.length, aside: emptyAside.length },
    emptyVisibleText: emptyVisible.map((n) => n.outerHTML.slice(0, 80)),
    asideEmptyHTML: emptyAside.map((n) => n.outerHTML.slice(0, 80)),
    drawerRows: [...aside.querySelectorAll('.nav-drawer button, .nav-drawer a')].length,
    menuSigns: {
      groups: [...aside.querySelectorAll('.nav-group-label')].map((n) => n.textContent),
      lastRow: (() => { const d = aside.querySelector('.nav-drawer'); const last = d && d.lastElementChild; return last ? (last.textContent || '').trim() || (last.querySelector('.nav') ? '' : '') : 'нет шторки'; })(),
      allViews: [...aside.querySelectorAll('[data-view]')].map((n) => n.dataset.view + ':' + (n.textContent || '').trim()),
      icons: aside.querySelectorAll('img, .solo-icon, .sigil').length,
    },
    scrollWidth: document.documentElement.scrollWidth,
  };
})()
