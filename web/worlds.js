/* r236 — готовые миры: положил файл — играешь через десять секунд.
   Экран кампании отдаёт собранный мир файлом и кладёт его в библиотеку
   runtime/worlds; экран «Новое приключение» показывает всё, что там лежит, и
   поднимает мир из файла новой кампанией. Ни один из путей не зовёт модель и
   не запускает разбор книги. */
(() => {
  'use strict';

  const SUFFIX = '.soloworld.json';
  const library = { rows: [], storage: 'runtime/worlds' };

  const bytes = size => size >= 1048576 ? (size / 1048576).toFixed(1) + ' МиБ' : Math.max(1, Math.round(size / 1024)) + ' КиБ';
  const stamp = value => value ? new Date(Number(value) * 1000).toLocaleString('ru-RU') : '';

  function packError(error) {
    return new Error(error && error.message ? error.message : 'Файл мира не принят.');
  }

  /* Файл уходит на сервер как есть: разбирает его тот же код, что и «Играть»
     из библиотеки, поэтому битый пакет объясняет причину одинаково. */
  async function upload(file) {
    let text;
    try { text = await file.text(); } catch { throw new Error('Файл мира не читается. Выберите файл ' + SUFFIX + '.'); }
    let response;
    try {
      response = await fetch('/api/worlds/import', {
        method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: text,
      });
    } catch { throw new Error('Связь с приложением прервалась. Файл мира не загружен.'); }
    let data = {};
    try { data = await response.json(); } catch { /* empty body */ }
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'string' ? detail : (detail && detail.message) || 'Файл мира не принят.');
    }
    return data;
  }

  async function openCampaign(created) {
    if (!created || !created.id) throw new Error('Сервер не вернул кампанию мира.');
    campaignId = created.id;
    try { localStorage.setItem('solo-campaign', created.id); } catch { /* private mode */ }
    if (typeof refresh === 'function') await refresh();
    view('play');
    if (typeof toast === 'function') toast(created.notice || ('Мир «' + created.name + '» открыт.'));
  }

  function playFile(file) {
    return protect(async () => {
      if (typeof busy !== 'undefined' && busy) throw new Error('Дождитесь завершения текущего ответа.');
      const created = await upload(file);
      await openCampaign(created);
      await loadLibrary();
    })();
  }

  function importPicker(label) {
    const input = el('input');
    input.type = 'file';
    input.accept = SUFFIX + ',application/json,.json';
    input.hidden = true;
    input.id = 'world-package-file-' + Math.random().toString(36).slice(2, 8);
    const button = el('button', label);
    button.type = 'button';
    button.addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      input.value = '';
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(SUFFIX)) {
        if (typeof toast === 'function') toast('Это не пакет мира: нужен файл ' + SUFFIX + '.');
        return;
      }
      if (file.size > 24 * 1048576) {
        if (typeof toast === 'function') toast('Файл больше 24 МиБ — это не пакет мира.');
        return;
      }
      playFile(file);
    });
    const holder = el('span', undefined, 'worlds-import');
    holder.append(button, input);
    return holder;
  }

  /* ---------- экран кампании: отдать мир ---------- */
  const campaignPanel = $('world');
  if (campaignPanel) {
    const box = el('section', undefined, 'worlds-pack');
    box.id = 'world-pack';
    box.append(el('h2', 'Готовый мир'));
    const hint = el('p', 'Собранный мир уезжает файлом ' + SUFFIX + ': на другой машине он поднимется сразу, без разбора книги и без вызовов модели.', 'hint');
    hint.id = 'world-pack-hint';
    const row = el('div', undefined, 'button-row');
    const download = el('button', 'Сохранить мир в файл');
    download.id = 'world-save-file';
    download.type = 'button';
    download.addEventListener('click', () => {
      if (!campaignId) { if (typeof toast === 'function') toast('Кампания ещё не открыта.'); return; }
      window.location.assign('/api/campaigns/' + campaignId + '/world/export');
    });
    const save = el('button', 'Сохранить сюда');
    save.type = 'button';
    save.id = 'world-save-library';
    save.addEventListener('click', protect(async () => {
      if (!campaignId) throw new Error('Кампания ещё не открыта.');
      const saved = await api('/campaigns/' + campaignId + '/world/save', {});
      if (typeof toast === 'function') toast('Мир сохранён: ' + (saved.folder || 'runtime/worlds') + '/' + saved.file);
      await loadLibrary();
    }));
    row.append(download, save, importPicker('Загрузить готовый мир'));
    box.append(hint, row);
    const h1 = campaignPanel.querySelector('h1');
    if (h1) h1.after(box); else campaignPanel.prepend(box);
  }

  /* ---------- «Новое приключение»: библиотека миров ---------- */
  const creationPanel = $('creation');
  const libraryBox = el('section', undefined, 'worlds-library');
  libraryBox.id = 'worlds-library';
  const head = el('div', undefined, 'worlds-library-head');
  head.append(el('h2', 'Готовые миры'), importPicker('Загрузить готовый мир'));
  const note = el('p', 'Мир, разобранный один раз, лежит файлом в папке ' + library.storage + '. Такой мир начинается сразу: книга не разбирается повторно.', 'hint');
  const rows = el('div', undefined, 'worlds-rows');
  rows.id = 'worlds-rows';
  libraryBox.append(head, note, rows);

  function row(entry) {
    const card = el('article', undefined, 'page-card worlds-card');
    card.id = 'world-card-' + entry.file;
    const title = el('h3', entry.name || entry.short || entry.file);
    const meta = el('p', [entry.system, stamp(entry.saved || entry.created_at), bytes(entry.size || 0)].filter(Boolean).join(' · '), 'hint');
    card.append(title, el('small', entry.file), meta);
    const actions = el('div', undefined, 'button-row');
    if (entry.broken) {
      card.append(el('p', 'Файл не читается: ' + (entry.problem || 'причина не указана'), 'worlds-problem'));
      actions.append(el('span', 'Проверьте файл или удалите его из папки.', 'hint'));
    } else {
      const play = el('button', 'Играть', 'primary');
      play.type = 'button';
      play.addEventListener('click', protect(async () => {
        if (typeof busy !== 'undefined' && busy) throw new Error('Дождитесь завершения текущего ответа.');
        const created = await api('/worlds/' + encodeURIComponent(entry.file) + '/play', {});
        await openCampaign(created);
        await loadLibrary();
      }));
      const download = el('button', 'Скачать');
      download.type = 'button';
      download.addEventListener('click', () => window.location.assign('/api/worlds/' + encodeURIComponent(entry.file) + '/download'));
      actions.append(play, download);
    }
    card.append(actions);
    return card;
  }

  async function loadLibrary() {
    let data;
    try { data = await api('/worlds'); } catch (error) { rows.replaceChildren(el('p', 'Библиотека миров недоступна: ' + error.message, 'hint')); return; }
    library.rows = data.worlds || [];
    library.storage = data.storage || library.storage;
    note.textContent = 'Мир, разобранный один раз, лежит файлом в папке ' + library.storage + '. Такой мир начинается сразу: книга не разбирается повторно.';
    rows.replaceChildren();
    if (!library.rows.length) { rows.append(el('p', 'Пока пусто. Разберите книгу и нажмите «Сохранить сюда» на экране мира — или загрузите готовый файл ' + SUFFIX + '.', 'hint')); return; }
    for (const entry of library.rows) rows.append(row(entry));
  }

  if (creationPanel) creationPanel.insertBefore(libraryBox, creationPanel.children[3] || null);

  window.soloWorldsReady = loadLibrary;
  const baseView = view;
  view = function (name) { baseView(name); if (name === 'creation' || name === 'campaigns') loadLibrary().catch(() => {}); };
  loadLibrary().catch(() => {});
})();
