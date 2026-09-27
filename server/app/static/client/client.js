/* Кабинет клиента: подача заявок своей организацией и контроль статусов/доставок. */
const S = { token: localStorage.getItem('crm_token') || '', user: null, cfg: null, timer: null };

const $ = (s) => document.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === 'class') n.className = v; else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) n.append(k.nodeType ? k : String(k));
  return n;
};

function toast(msg, ms = 5000) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

const asText = (v) => (v === null || v === undefined) ? ''
  : (typeof v === 'object' ? JSON.stringify(v) : String(v));

async function api(path, opts = {}) {
  // заголовки объединяем, а не заменяем: кастомные headers не должны
  // выкидывать Content-Type и Authorization (иначе POST ломается с 422)
  const o = { ...opts, headers: {
    'Content-Type': 'application/json',
    ...(S.token ? { Authorization: 'Bearer ' + S.token } : {}),
    ...(opts.headers || {}),
  } };
  if (o.body && typeof o.body !== 'string' && !(o.body instanceof Blob) && !(o.body instanceof FormData)) o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const txt = await r.text();
  let data; try { data = txt ? JSON.parse(txt) : {}; } catch { data = { raw: txt }; }
  if (!r.ok) throw new Error(asText(data.error) || asText(data.detail) || ('HTTP ' + r.status));
  return data;
}

/* Телефон: любой ввод -> +375XXXXXXXXX (те же правила, что на сервере). */
function normalizePhone(raw) {
  const digits = (raw || '').replace(/[^0-9]/g, '');
  if (!digits) return '';
  let rest = digits;
  if (rest.startsWith('375')) rest = rest.slice(3);
  else if (rest.startsWith('80') && rest.length === 11) rest = rest.slice(2);
  else if (rest.startsWith('8') && rest.length === 10) rest = rest.slice(1);
  else if (rest.startsWith('0') && rest.length === 10) rest = rest.slice(1);
  if (rest.length === 9 && /^\d+$/.test(rest)) return '+375' + rest;
  if (rest.length === 7 && rest.startsWith('0')) return '+37517' + rest.slice(1);
  return '+' + digits;
}
function formatPhone(p) {
  const m = (p || '').match(/^\+375(\d{2})(\d{3})(\d{2})(\d{2})$/);
  return m ? ('+375 ' + m[1] + ' ' + m[2] + '-' + m[3] + '-' + m[4]) : p;
}
/* Поле телефона: при потере фокуса приводим к виду +375 XX XXX-XX-XX. */
function attachPhoneInput(inputSel, hintSel) {
  const inp = document.querySelector(inputSel);
  const hint = hintSel ? document.querySelector(hintSel) : null;
  if (!inp) return;
  inp.addEventListener('blur', () => {
    const raw = inp.value.trim();
    if (!raw) { if (hint) hint.textContent = ''; return; }
    const n = normalizePhone(raw);
    const ok = /^\+375(25|29|33|44|17)\d{7}$/.test(n);
    inp.value = ok ? formatPhone(n) : n;
    if (hint) {
      hint.textContent = ok ? ('Телефон: ' + formatPhone(n))
        : ('Не похож на белорусский номер (' + n + '). Примеры: 8029 1234567, +375 29 123-45-67, 291234567');
    }
  });
}

const fmtDT = (s) => s ? new Date(s).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '';
const badge = (kind, text) => el('span', { class: 'badge b-' + kind }, text);

/* ------------------------------------------------------------------ вход */
async function login() {
  try {
    const res = await api('/api/auth/login', { method: 'POST', body: { username: $('#lg-user').value, password: $('#lg-pass').value, device: 'web-client' } });
    if (res.role !== 'client') { toast('Этот вход — для клиентов. Диспетчерам: /dispatcher, инженерам: /m'); return; }
    S.token = res.token; localStorage.setItem('crm_token', S.token);
    await boot();
  } catch (e) { toast('Ошибка входа: ' + e.message); }
}
$('#lg-go').onclick = login;
$('#lg-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') login(); });
$('#logout').onclick = async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
  localStorage.removeItem('crm_token'); S.token = ''; location.reload();
};

$('#tabs').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-tab]'); if (!b) return;
  document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
  if (b.dataset.tab === 'my') loadMy();
  if (b.dataset.tab === 'deliveries') loadDeliveries();
});

/* --------------------------------------------------------- новая заявка */
async function fillSelects() {
  const works = await api('/api/works');
  const w = $('#k-work'); w.innerHTML = '';
  let cat = '';
  for (const item of works) {
    if (item.category !== cat) { cat = item.category; w.append(el('optgroup', { label: cat })); }
    w.lastChild.append(el('option', { value: item.code }, item.name));
  }
  const p = $('#k-priority'); p.innerHTML = '';
  for (const pr of S.cfg.priorities) p.append(el('option', { value: pr.code, selected: pr.code === 'normal' ? 'selected' : null }, pr.label));
}

$('#k-submit').onclick = async () => {
  const body = {
    contact_person: $('#k-contact').value.trim(), phone: normalizePhone($('#k-phone').value.trim()),
    work_code: $('#k-work').value, priority: $('#k-priority').value,
    address: $('#k-address').value.trim(), equipment: $('#k-equipment').value.trim() || null,
    serial: $('#k-serial').value.trim() || null, comment: $('#k-comment').value.trim(),
  };
  $('#k-submit').disabled = true;
  try {
    const r = await api('/api/client/requests', { method: 'POST', body });
    toast(`Заявка ${r.number} принята. ${r._assignment_message || ''}`, 8000);
    ['#k-contact', '#k-phone', '#k-address', '#k-equipment', '#k-serial', '#k-comment'].forEach((s) => { $(s).value = ''; });
    $('#k-priority').value = 'normal';
  } catch (e) { toast('Заявка не принята: ' + e.message, 8000); }
  $('#k-submit').disabled = false;
};

/* ------------------------------------------------------------- списки */
async function loadMy() {
  const data = await api('/api/client/requests');
  const tb = $('#my-table tbody'); tb.innerHTML = '';
  for (const r of data.items) {
    tb.append(el('tr', {},
      el('td', {}, el('b', {}, r.number)),
      el('td', {}, fmtDT(r.created_at)),
      el('td', {}, r.work_name),
      el('td', {}, badge(r.priority, r.priority_label)),
      el('td', {}, badge(r.status, r.status_label)),
      el('td', {}, r.engineer_name || '—'),
      el('td', {}, r.address),
      el('td', {}, el('button', { onclick: () => openMy(r.id) }, 'Подробнее')),
    ));
  }
  if (!tb.children.length) tb.append(el('tr', {}, el('td', { colspan: 8 }, 'Заявок пока нет — подайте первую на вкладке «Новая заявка»')));
  $('#my-summary').innerHTML = `Всего заявок: <b>${data.total}</b>`;
}

async function openMy(id) {
  const r = await api('/api/client/requests/' + id);
  const modal = document.createElement('div');
  modal.className = 'modal';
  const box = el('div', { class: 'modal-box' });
  box.append(el('h2', {}, r.number + ' — ' + r.status_label));
  box.append(el('div', { class: 'kv' },
    el('div', {}, 'Работа'), el('div', {}, r.work_name),
    el('div', {}, 'Срочность'), el('div', {}, badge(r.priority, r.priority_label)),
    el('div', {}, 'Статус'), el('div', {}, badge(r.status, r.status_label)),
    el('div', {}, 'Инженер'), el('div', {}, r.engineer_name || '— будет назначен —'),
    el('div', {}, 'Адрес'), el('div', {}, r.address),
    el('div', {}, 'Оборудование'), el('div', {}, (r.equipment || '—') + (r.serial ? ' / ' + r.serial : '')),
    el('div', {}, 'Пояснение'), el('div', {}, r.comment || '—'),
    el('div', {}, 'Плановая дата'), el('div', {}, r.planned_date || '—'),
    el('div', {}, 'Выполнено'), el('div', {}, fmtDT(r.done_at)),
  ));
  const ev = el('div', { class: 'events' });
  for (const e of r.events || []) ev.append(el('div', {}, `${fmtDT(e.at)} · ${e.comment || e.to_status || ''}`));
  box.append(el('h3', {}, 'История'), ev,
    el('div', { class: 'toolbar' }, el('button', { onclick: () => modal.remove() }, 'Закрыть')));
  modal.append(box);
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
  document.body.append(modal);
}

async function loadDeliveries() {
  const list = await api('/api/client/deliveries');
  const tb = $('#dlv-table tbody'); tb.innerHTML = '';
  const LABEL = { scheduled: 'запланирована', delivered: 'доставлено', postponed: 'перенесена' };
  for (const d of list) {
    tb.append(el('tr', {},
      el('td', {}, el('b', {}, d.number)),
      el('td', {}, d.scheduled_date),
      el('td', {}, badge(d.status === 'delivered' ? 'delivered' : 'pickup_office', LABEL[d.status] || d.status)),
      el('td', {}, d.postpone_count),
      el('td', {}, d.comment || '—'),
    ));
  }
  if (!tb.children.length) tb.append(el('tr', {}, el('td', { colspan: 5 }, 'Доставок нет')));
}

/* --------------------------------------------------------------- запуск */
async function boot() {
  attachPhoneInput('#k-phone', '#k-phone-hint');
  try { S.user = await api('/api/auth/me'); } catch { S.token = ''; localStorage.removeItem('crm_token'); return showLogin(); }
  if (S.user.role !== 'client') { toast('Этот вход — для клиентов. Диспетчерам: /dispatcher, инженерам: /m'); S.token = ''; localStorage.removeItem('crm_token'); return showLogin(); }
  S.cfg = await api('/api/config');
  $('#login').hidden = true; $('#app').hidden = false;
  $('#whoami').textContent = S.user.company || S.user.full_name || S.user.username;
  $('#company-name').textContent = S.user.company ? '· ' + S.user.company + ' (УНП ' + (S.user.unp || '—') + ')' : '';
  await fillSelects();
  loadMy();
}
function showLogin() { $('#login').hidden = false; $('#app').hidden = true; }

if (S.token) boot(); else showLogin();
