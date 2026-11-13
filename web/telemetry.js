/* r250 — след действий владельца.
 *
 * Что пишет: нажатия, шаги редактора, ответы API, ошибки JS. Чего не пишет:
 * значения полей ввода (там бывают имена, пароли, описания) — только тип поля.
 *
 * Куда: POST /api/telemetry, пачкой. Тот же sid уходит заголовком X-Solo-Sid в
 * каждый запрос, поэтому на сервере клик и его запрос склеиваются в одну цепочку.
 */
(() => {
  if (window.__soloTrace) return;
  window.__soloTrace = true;

  const KEY = 'solo:sid';
  let sid = '';
  try {
    sid = sessionStorage.getItem(KEY) || '';
    if (!sid) {
      sid = Math.random().toString(36).slice(2, 10);
      sessionStorage.setItem(KEY, sid);
    }
  } catch (e) { sid = 'anon'; }

  const queue = [];
  let flushing = false;

  function where() {
    try { return location.pathname + location.hash; } catch (e) { return ''; }
  }

  function stepName() {
    const box = document.querySelector('[data-step],.wizard-step,[aria-current="step"]');
    if (!box) return '';
    return (box.getAttribute('data-step') || box.getAttribute('aria-label') || box.innerText || '')
      .replace(/\s+/g, ' ').trim().slice(0, 40);
  }

  function push(event) {
    event.path = where();
    const step = stepName();
    if (step && !event.step) event.step = step;
    queue.push(event);
    if (queue.length >= 25) flush();
  }

  function flush(useBeacon) {
    if (flushing || !queue.length) return;
    flushing = true;
    const batch = queue.splice(0, queue.length);
    const body = JSON.stringify({ events: batch });
    const done = () => { flushing = false; };
    try {
      if (useBeacon && navigator.sendBeacon) {
        navigator.sendBeacon('/api/telemetry', new Blob([body], { type: 'application/json' }));
        done();
        return;
      }
      fetch('/api/telemetry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Solo-Sid': sid },
        body,
        keepalive: true,
      }).then(done, done);
    } catch (e) { done(); }
  }

  setInterval(() => flush(false), 2000);
  window.addEventListener('pagehide', () => flush(true));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush(true);
  });

  /* ——— нажатия ——— */
  document.addEventListener('click', (event) => {
    const node = event.target && event.target.closest
      ? event.target.closest('button,a[href],[role="button"],input[type="checkbox"],input[type="radio"],label')
      : null;
    if (!node) return;
    const text = (node.getAttribute('aria-label') || node.getAttribute('title') ||
      node.innerText || node.value || '').replace(/\s+/g, ' ').trim();
    push({ type: 'click', label: text.slice(0, 60) || node.tagName.toLowerCase() });
  }, true);

  /* ——— отправка форм: только факт, без содержимого ——— */
  document.addEventListener('submit', (event) => {
    const form = event.target;
    push({ type: 'submit', label: (form.getAttribute('action') || form.id || 'форма').slice(0, 60) });
  }, true);

  /* ——— ответы API: код и текст ошибки ——— */
  function wrapFetch(original) {
    return function (...args) {
      const started = performance.now();
      let url = '';
      try { url = (args[0] && args[0].url) ? args[0].url : String(args[0]); } catch (e) { url = ''; }
      const init = args[1] || {};
      const headers = init.headers instanceof Headers ? init.headers : new Headers(init.headers || {});
      try { headers.set('X-Solo-Sid', sid); } catch (e) { /* noop */ }
      args[1] = Object.assign({}, init, { headers });
      return original.apply(this, args).then((response) => {
        const ms = Math.round(performance.now() - started);
        if (response.status >= 400) {
          response.clone().text().then((text) => {
            push({ type: 'api', url, status: response.status, ms, msg: text.slice(0, 300) });
          }, () => push({ type: 'api', url, status: response.status, ms }));
        } else if (url.indexOf('/api/telemetry') < 0) {
          push({ type: 'api', url, status: response.status, ms });
        }
        return response;
      }, (error) => {
        push({ type: 'api', url, msg: 'сеть: ' + String(error && error.message || error) });
        throw error;
      });
    };
  }
  if (window.fetch) window.fetch = wrapFetch(window.fetch);

  if (window.XMLHttpRequest) {
    const open = XMLHttpRequest.prototype.open;
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__solo = { method, url, started: performance.now() };
      return open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      const info = this.__solo;
      if (info) {
        try { this.setRequestHeader('X-Solo-Sid', sid); } catch (e) { /* noop */ }
        this.addEventListener('loadend', () => {
          const ms = Math.round(performance.now() - info.started);
          const status = this.status;
          if (status >= 400 || status === 0) {
            push({
              type: 'api', url: info.url, status, ms,
              msg: String(this.responseText || '').slice(0, 300) || 'нет ответа',
            });
          } else {
            push({ type: 'api', url: info.url, status, ms });
          }
        });
      }
      return send.apply(this, arguments);
    };
  }

  /* ——— ошибки скриптов ——— */
  window.addEventListener('error', (event) => {
    const message = event.message || (event.error && event.error.message) || '';
    if (!message) return;
    push({
      type: 'js', msg: message.slice(0, 250),
      nav: event.lineno ? Number(event.lineno) : undefined,
    });
  }, true);

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    push({ type: 'js', msg: ('promise: ' + String(reason && reason.message || reason)).slice(0, 250) });
  });

  push({ type: 'open' });
})();
