/* Консоль диспетчера: приём заявок (замена формы Windows-клиента) и работа со списком. */
const S = { token: localStorage.getItem('crm_token') || '', user: null, page: 0, limit: 50, cfg: null, timer: null };

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
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function toast(msg, ms = 4200) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}

async function api(path, opts = {}) {
  const o = { headers: { 'Content-Type': 'application/json' }, ...opts };
  if (S.token) o.headers.Authorization = 'Bearer ' + S.token;
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const txt = await r.text();
  let data; try { data = txt ? JSON.parse(txt) : {}; } catch { data = { raw: txt }; }
  if (!r.ok) throw new Error(data.error || data.detail || ('HTTP ' + r.status));
  return data;
}

const fmtDT = (s) => s ? new Date(s).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '';
const badge = (kind, text) => el('span', { class: 'badge b-' + kind }, text);

/* ------------------------------------------------------------------ вход */
async function login() {
  try {
    const res = await api('/api/auth/login', { method: 'POST', body: { username: $('#lg-user').value, password: $('#lg-pass').value, device: 'web-dispatcher' } });
    if (!['admin', 'operator'].includes(res.role)) { toast('Этот вход — для диспетчера. Клиентам: /client, инженерам: /m'); return; }
    S.token = res.token; localStorage.setItem('crm_token', S.token); S.user = res;
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
  if (b.dataset.tab === 'list') loadRequests();
});

/* --------------------------------------------------------- новая заявка */
async function fillSelects() {
  const works = await api('/api/works');
  const w = $('#r-work'); w.innerHTML = '';
  let cat = '';
  for (const item of works) {
    if (item.category !== cat) { cat = item.category; w.append(el('optgroup', { label: cat })); }
    w.lastChild.append(el('option', { value: item.code }, item.name));
  }
  const p = $('#r-priority'); p.innerHTML = '';
  for (const pr of S.cfg.priorities) p.append(el('option', { value: pr.code, selected: pr.code === 'normal' ? 'selected' : null }, pr.label));
}

$('#r-check').onclick = async () => {
  const q = $('#r-address').value.trim();
  if (!q) { toast('Введите адрес'); return; }
  $('#r-geo-result').textContent = 'Определяю…';
  try {
    const g = await api('/api/geo/resolve?' + new URLSearchParams({ q }));
    $('#r-geo-result').textContent = g.ok
      ? `Адрес найден: ${g.lat.toFixed(5)}, ${g.lon.toFixed(5)} — ${g.message || 'зона определена'}`
      : (g.message || 'Адрес не найден — уточните написание');
  } catch (e) { $('#r-geo-result').textContent = e.message; }
};

$('#r-clear').onclick = () => {
  ['#r-contractor', '#r-unp', '#r-account', '#r-bank', '#r-contact', '#r-phone', '#r-address',
   '#r-equipment', '#r-serial', '#r-comment'].forEach((s) => { $(s).value = ''; });
  $('#r-priority').value = 'normal';
  $('#r-geo-result').textContent = '';
};

$('#r-submit').onclick = async () => {
  const body = {
    contractor: $('#r-contractor').value.trim(), unp: $('#r-unp').value.trim(),
    bank_account: $('#r-account').value.trim(), bank_name: $('#r-bank').value.trim() || null,
    contact_person: $('#r-contact').value.trim(), phone: $('#r-phone').value.trim(),
    work_code: $('#r-work').value, priority: $('#r-priority').value,
    address: $('#r-address').value.trim(), equipment: $('#r-equipment').value.trim() || null,
    serial: $('#r-serial').value.trim() || null, comment: $('#r-comment').value.trim(),
  };
  $('#r-submit').disabled = true;
  try {
    const r = await api('/api/requests', { method: 'POST', body, headers: { 'X-Client-Name': 'dispatcher-web' } });
    toast(`Заявка ${r.number} принята. ${r._assignment_message || ''}`, 7000);
    $('#r-clear').click();
  } catch (e) { toast('Заявка не принята: ' + e.message, 8000); }
  $('#r-submit').disabled = false;
};

/* ------------------------------------------------------------- список */
function filters() {
  return {
    q: $('#f-q').value.trim(), contractor: $('#f-contractor').value.trim(), unp: $('#f-unp').value.trim(),
    status: $('#f-status').value, date_from: $('#f-date-from').value, date_to: $('#f-date-to').value,
  };
}
const qs = (o) => { const p = new URLSearchParams(); for (const [k, v] of Object.entries(o)) if (v) p.set(k, v); return p.toString(); };

async function loadRequests() {
  const f = filters();
  const data = await api('/api/requests?' + qs({ ...f, limit: S.limit, offset: S.page * S.limit }));
  const tb = $('#req-table tbody'); tb.innerHTML = '';
  for (const r of data.items) {
    tb.append(el('tr', {},
      el('td', {}, el('b', {}, r.number)),
      el('td', {}, fmtDT(r.created_at)),
      el('td', {}, r.contractor),
      el('td', {}, r.work_name),
      el('td', {}, badge(r.priority, r.priority_label)),
      el('td', {}, badge(r.status, r.status_label)),
      el('td', {}, r.zone_name || '—'),
      el('td', {}, r.engineer_name || '—'),
      el('td', {}, r.address),
      el('td', {}, el('button', { onclick: () => openRequest(r.id) }, 'Открыть')),
    ));
  }
  $('#req-summary').innerHTML = `Найдено: <b>${data.total}</b> · страница ${S.page + 1} из ${Math.max(1, Math.ceil(data.total / S.limit))}`;
  const pg = $('#req-page'); pg.innerHTML = '';
  pg.append(el('button', { onclick: () => { if (S.page > 0) { S.page--; loadRequests(); } } }, '← Назад'));
  pg.append(el('button', { onclick: () => { if ((S.page + 1) * S.limit < data.total) { S.page++; loadRequests(); } } }, 'Вперёд →'));
  pg.append(el('button', { onclick: () => loadRequests() }, 'Обновить'));
}

$('#f-go').onclick = () => { S.page = 0; loadRequests(); };
$('#f-reset').onclick = () => {
  ['#f-q', '#f-contractor', '#f-unp', '#f-date-from', '#f-date-to'].forEach((s) => { $(s).value = ''; });
  $('#f-status').value = ''; S.page = 0; loadRequests();
};
document.querySelectorAll('#tab-list input, #tab-list select').forEach((i) =>
  i.addEventListener('keydown', (e) => { if (e.key === 'Enter') { S.page = 0; loadRequests(); } }));

async function openRequest(id) {
  const r = await api('/api/requests/' + id);
  const box = $('#modal-box'); box.innerHTML = '';
  box.append(el('h2', {}, r.number + ' — ' + r.status_label));
  box.append(el('div', { class: 'kv' },
    el('div', {}, 'Контрагент'), el('div', {}, r.contractor + (r.bank_name ? ' · ' + r.bank_name : '')),
    el('div', {}, 'УНП / р/с'), el('div', {}, r.unp + ' / ' + r.bank_account),
    el('div', {}, 'Контактное лицо'), el('div', {}, r.contact_person),
    el('div', {}, 'Телефон'), el('div', {}, el('a', { href: 'tel:' + r.phone }, r.phone_formatted || r.phone)),
    el('div', {}, 'Работа'), el('div', {}, r.work_name),
    el('div', {}, 'Срочность'), el('div', {}, badge(r.priority, r.priority_label)),
    el('div', {}, 'Адрес'), el('div', {}, r.address + (r.lat ? ` (${r.lat.toFixed(5)}, ${r.lon.toFixed(5)})` : '')),
    el('div', {}, 'Район (зона)'), el('div', {}, r.zone_name || 'не определён'),
    el('div', {}, 'Инженер'), el('div', {}, r.engineer_name || '— не назначен —'),
    el('div', {}, 'Оборудование'), el('div', {}, (r.equipment || '—') + (r.serial ? ' / ' + r.serial : '')),
    el('div', {}, 'Пояснение'), el('div', {}, r.comment || '—'),
  ));

  box.append(el('h3', {}, 'Действия'));
  const engSel = el('select');
  for (const e of await api('/api/admin/engineers')) engSel.append(el('option', { value: e.id, selected: e.id === r.engineer_id ? 'selected' : null }, e.full_name));
  const reason = el('input', { placeholder: 'причина переназначения' });
  box.append(el('div', { class: 'toolbar' }, engSel, reason,
    el('button', { class: 'primary', onclick: async () => {
      try {
        await api(`/api/requests/${id}/reassign`, { method: 'POST', body: { engineer_id: +engSel.value, reason: reason.value } });
        toast('Заявка переназначена'); $('#modal').hidden = true; loadRequests();
      } catch (e) { toast(e.message); }
    } }, 'Переназначить')));
  const day2 = el('input', { type: 'date' });
  const why = el('input', { placeholder: 'причина переноса' });
  box.append(el('div', { class: 'toolbar' }, day2, why,
    el('button', { onclick: async () => {
      try {
        await api(`/api/requests/${id}/postpone`, { method: 'POST', body: { new_day: day2.value, reason: why.value } });
        toast('Заявка перенесена'); $('#modal').hidden = true; loadRequests();
      } catch (e) { toast(e.message); }
    } }, 'Перенести заявку')));
  const cmt = el('input', { placeholder: 'комментарий к смене статуса' });
  const st = el('select');
  for (const s of S.cfg.statuses) st.append(el('option', { value: s.code, selected: s.code === r.status ? 'selected' : null }, s.label));
  box.append(el('div', { class: 'toolbar' }, st, cmt,
    el('button', { onclick: async () => {
      try {
        await api(`/api/requests/${id}/status`, { method: 'POST', body: { status: st.value, comment: cmt.value } });
        toast('Статус изменён'); $('#modal').hidden = true; loadRequests();
      } catch (e) { toast(e.message); }
    } }, 'Сменить статус')));

  box.append(el('div', { class: 'toolbar' }, el('button', { onclick: () => { $('#modal').hidden = true; } }, 'Закрыть')));
  $('#modal').hidden = false;
}
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('#modal').hidden = true; });

/* --------------------------------------------------------------- запуск */
async function boot() {
  try { S.user = await api('/api/auth/me'); } catch { S.token = ''; localStorage.removeItem('crm_token'); return showLogin(); }
  if (!['admin', 'operator'].includes(S.user.role)) { toast('Этот вход — для диспетчера. Клиентам: /client, инженерам: /m'); S.token = ''; localStorage.removeItem('crm_token'); return showLogin(); }
  S.cfg = await api('/api/config');
  $('#login').hidden = true; $('#app').hidden = false;
  $('#whoami').textContent = `${S.user.full_name || S.user.username} (диспетчер)`;
  const sel = $('#f-status'); sel.innerHTML = '<option value="">Все статусы</option>';
  for (const s of S.cfg.statuses) sel.append(el('option', { value: s.code }, s.label));
  await fillSelects();
  loadRequests();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if ($('#live').checked && $('#tab-list').classList.contains('active')) loadRequests(); }, 30000);
}
function showLogin() { $('#login').hidden = false; $('#app').hidden = true; }

if (S.token) boot(); else showLogin();
