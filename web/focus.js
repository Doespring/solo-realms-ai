'use strict';
/* A07/F12 — фокус в модальных окнах.

   Аудит 08.11 (снимки S74–S75): Tab за последним элементом <dialog> уходил
   из окна, и `document.activeElement` становился BODY. Родное `showModal()`
   блокирует мышь и клики, но кольцо фокуса в этом браузере не замыкает.
   Здесь три вещи сразу для каждого окна:

   1) Tab/Shift+Tab ходят по кругу внутри верхнего окна;
   2) всё, что под окном, получает `inert` — ни мышью, ни Tab туда не попасть;
   3) закрытие возвращает фокус кнопке-инициатору.

   Модуль не знает про экраны: он надевает поведение на
   HTMLDialogElement.showModal/close, поэтому работает и для окон, которые
   создаются в рантайме (карточка предмета, просмотр броска, материал). */
(() => {
 const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"]),[contenteditable="true"]';
 const SKIP = new Set(['SCRIPT','STYLE','LINK','TEMPLATE','NOSCRIPT','META','TITLE']);
 const stack = [];
 const original = new Map();

 const seen = node => !!node && !node.disabled && node.getClientRects().length > 0
   && node.getAttribute('aria-hidden') !== 'true'
   && getComputedStyle(node).visibility !== 'hidden';
 function focusables(root){ return Array.from(root.querySelectorAll(FOCUSABLE)).filter(seen); }
 /* Порядок обхода как у браузера: сначала положительный tabindex, затем
    остальные в порядке разметки. */
 function order(nodes){
   const positive = nodes.filter(node => node.tabIndex > 0).sort((a, b) => a.tabIndex - b.tabIndex);
   const rest = nodes.filter(node => node.tabIndex <= 0);
   return positive.concat(rest);
 }
 function top(){ return stack.length ? stack[stack.length - 1] : null; }

 /* Окно, которое убрали из документа без close(), иначе осталось бы в стеке:
    подложка навсегда осталась бы inert. Наблюдатель живёт только пока открыто
    хотя бы одно окно. */
 let observer = null;
 function watch(){
   if (observer || !stack.length || typeof MutationObserver !== 'function') return;
   observer = new MutationObserver(() => prune());
   observer.observe(document.body, { childList: true, subtree: true });
 }
 function unwatch(){
   if (observer && !stack.length) { observer.disconnect(); observer = null; }
 }
 function prune(){
   let changed = false, topmost = null;
   for (let index = stack.length - 1; index >= 0; index -= 1) {
     const record = stack[index];
     if (record.dialog.isConnected) continue;
     if (index === stack.length - 1) topmost = record;
     stack.splice(index, 1);
     record.dialog.removeEventListener('close', record.onClose);
     restoreTabindex(record);
     changed = true;
   }
   if (!changed) return;
   // Сначала вернуть подложку, потом фокус: иначе инициатор ещё inert и focus()
   // молча не сработает.
   inertBackground();
   unwatch();
   if (topmost) returnFocus(topmost);
 }
 function restoreTabindex(record){
   if (record.tabindex === null) return;
   if (record.tabindex === undefined) record.dialog.removeAttribute('tabindex');
   else record.dialog.setAttribute('tabindex', record.tabindex);
   record.tabindex = null;
 }

 /* Подложка: всё, кроме верхнего окна, помечается inert. Прежнее значение
    запоминается, чтобы не снять чужой inert (панели ставят его сами). */
 function inertBackground(){
   const current = top();
   if (!current) {
     // Окон больше нет: возвращаем подложке ровно то, что было до первого окна.
     for (const [node, inert] of original) node.inert = inert;
     original.clear();
     return;
   }
   for (const node of document.body.children) {
     if (SKIP.has(node.tagName)) continue;
     if (!original.has(node)) original.set(node, node.inert === true);
     // Контейнер, внутри которого лежит окно, гасить нельзя: тогда окно тоже
     // станет inert и перестанет принимать фокус.
     const holds = node === current.dialog || node.contains(current.dialog);
     node.inert = !holds;
   }
   for (const node of original.keys()) if (!node.isConnected) original.delete(node);
 }

 function returnFocus(record){
   const target = record.initiator;
   const real = target && target !== document.body && target !== document.documentElement;
   if (real && target.isConnected && typeof target.focus === 'function' && !target.closest('[inert]')) {
     target.focus();
     if (document.activeElement === target) return;
   }
   // Инициатора перерисовали, пока окно было открыто: фокус — в первый
   // достижимый элемент активного экрана, иначе игрок остался бы с BODY.
   const view = document.querySelector('.view.active') || document.body;
   const candidate = view.querySelector('#player-input') || focusables(view)[0];
   if (candidate && seen(candidate)) candidate.focus();
 }

 function detach(record){
   const index = stack.indexOf(record);
   if (index < 0) return false;
   stack.splice(index, 1);
   record.dialog.removeEventListener('close', record.onClose);
   restoreTabindex(record);
   inertBackground();
   unwatch();
   return true;
 }

 function onKeydown(event){
   const current = top();
   if (!current) return;
   const dialog = current.dialog;
   if (event.key === 'Escape') {
     // Когда фокус внутри окна, закрытие (и событие cancel) делает сам браузер:
     // так диалог может отказаться закрываться через preventDefault. Вмешиваемся
     // только если фокус уехал из окна — тогда Escape до него не дойдёт.
     if (event.isComposing || event.defaultPrevented) return;
     if (dialog.contains(document.activeElement)) return;
     event.preventDefault();
     dialog.close();
     return;
   }
   if (event.key !== 'Tab') return;
   const nodes = order(focusables(dialog));
   if (!nodes.length) {
     event.preventDefault();
     if (current.tabindex === null) current.tabindex = dialog.getAttribute('tabindex');
     dialog.setAttribute('tabindex', '-1');
     dialog.focus();
     return;
   }
   const active = document.activeElement;
   if (!dialog.contains(active)) {
     event.preventDefault();
     (event.shiftKey ? nodes[nodes.length - 1] : nodes[0]).focus();
     return;
   }
   const first = nodes[0], last = nodes[nodes.length - 1];
   if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
   else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
 }
 document.addEventListener('keydown', onKeydown, true);

 const proto = window.HTMLDialogElement && window.HTMLDialogElement.prototype;
 if (!proto) return;
 const openModal = proto.showModal;
 const closeModal = proto.close;

 proto.showModal = function showModal(){
 // Инициатора запоминаем ДО открытия: showModal переносит фокус внутрь окна,
 // и после вызова activeElement уже не тот, кто окно вызвал. Повторный вызов
 // на уже открытом окне не должен затирать прежнего инициатора элементом
 // из самого окна.
 const previous = stack.find(record => record.dialog === this);
 const initiator = previous ? previous.initiator : document.activeElement;
 const result = openModal.apply(this, arguments);
 if (previous) detach(previous);
 const record = { dialog: this, initiator, onClose: null, tabindex: null };
 record.onClose = () => { if (detach(record)) returnFocus(record); };
 stack.push(record);
 this.addEventListener('close', record.onClose);
 inertBackground();
 watch();
 const target = this.querySelector('[autofocus]') || focusables(this)[0];
 if (target) target.focus();
 else {
   if (record.tabindex === null) record.tabindex = this.getAttribute('tabindex');
   this.setAttribute('tabindex', '-1');
   this.focus();
 }
 return result;
 };

 proto.close = function close(){
   const record = stack.find(row => row.dialog === this);
   if (!record) return closeModal.apply(this, arguments);
   // Сначала снимаем inert и только потом закрываем: иначе родной возврат
   // фокуса и следующий за close() focus() из обработчика кнопки попадут
   // в inert-подложку и молча не сработают.
   const wasTop = top() === record;
   detach(record);
   const result = closeModal.apply(this, arguments);
   if (wasTop) returnFocus(record);
   return result;
 };
})();
