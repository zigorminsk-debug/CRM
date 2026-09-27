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

/* Полный адрес из отдельных полей: НП, улица, дом, кв./офис. */
function buildAddress() {
  const st = $('#r-settlement').value.trim();
  let street = $('#r-street').value.trim();
  const house = $('#r-house').value.trim();
  const office = $('#r-office').value.trim();
  // «ул.» добавляем только если пользователь не написал сам (проспект, пер., шоссе…)
  if (street && !/^(ул|ул\.|улица|пр|пр-т|просп|проспект|пер|пер\.|переулок|б-р|бульвар|пл|площадь|шоссе|ш)\b/i.test(street)) street = 'ул. ' + street;
  const parts = [];
  if (st) parts.push(st);
  if (street) parts.push(street + (house ? ', д. ' + house : ''));
  else if (house) parts.push('д. ' + house);
  if (office) parts.push(/^\d/.test(office) ? 'оф. ' + office : office);
  return parts.join(', ');
}
function updateAddrPreview() { $('#r-addr-preview').textContent = buildAddress(); }
['#r-settlement', '#r-street', '#r-house', '#r-office'].forEach((s) => $(s).addEventListener('input', updateAddrPreview));

$('#r-check').onclick = async () => {
  const q = buildAddress();
  if (!q) { toast('Укажите населённый пункт и улицу'); return; }
  $('#r-geo-result').textContent = 'Определяю…';
  try {
    const g = await api('/api/geo/resolve?' + new URLSearchParams({ address: q }));
    $('#r-geo-result').textContent = g.ok
      ? `Адрес найден (${g.provider === 'local_index' ? 'справочник' : g.provider}): зона «${g.zone_name || 'не определена'}»`
        + (g.engineer_name ? `, инженер: ${g.engineer_name}` : '')
        + (g.message ? ` — ${g.message}` : '')
      : (g.message || 'Адрес не найден — уточните написание');
  } catch (e) { $('#r-geo-result').textContent = e.message; }
};

/* Контрагент: варианты из прошлых заявок; при выборе — подстановка реквизитов. */
let contractorCache = [];
let cTimer = null;
$('#r-contractor').addEventListener('input', () => {
  clearTimeout(cTimer);
  cTimer = setTimeout(async () => {
    const q = $('#r-contractor').value.trim();
    if (q.length < 2) { contractorCache = []; return; }
    try {
      contractorCache = await api('/api/contractors?q=' + encodeURIComponent(q));
      const dl = $('#dl-contractors'); dl.innerHTML = '';
      for (const c of contractorCache) dl.append(el('option', { value: c.name }, c.unp ? ('УНП ' + c.unp + (c.phone ? ' · ' + c.phone : '')) : ''));
    } catch (e) { /* подсказки не критичны */ }
  }, 200);
});
$('#r-contractor').addEventListener('change', () => {
  const name = $('#r-contractor').value.trim().toLowerCase();
  const c = contractorCache.find((x) => x.name.toLowerCase() === name);
  if (!c) return;
  if (c.unp) $('#r-unp').value = c.unp;
  if (c.bank_account) $('#r-account').value = c.bank_account;
  if (c.bank_name) $('#r-bank').value = c.bank_name;
  if (c.contact_person) $('#r-contact').value = c.contact_person;
  if (c.phone) { $('#r-phone').value = c.phone; $('#r-phone').dispatchEvent(new Event('blur')); }
  toast('Реквизиты подставлены из прошлой заявки: ' + c.name, 4000);
});

/* Населённый пункт: подсказки (Минск + Минская область). */
let stTimer = null;
$('#r-settlement').addEventListener('input', () => {
  clearTimeout(stTimer);
  stTimer = setTimeout(async () => {
    const q = $('#r-settlement').value.trim();
    if (q.length < 2) return;
    try {
      const list = await api('/api/geo/settlements?q=' + encodeURIComponent(q));
      const dl = $('#dl-settlements'); dl.innerHTML = '';
      for (const item of list) dl.append(el('option', { value: item.name }, item.district || ''));
    } catch (e) { /* не критично */ }
  }, 200);
});

/* Улица: подсказки по началу названия (Фабр… -> Фабрициуса). */
let strTimer = null;
$('#r-street').addEventListener('input', () => {
  clearTimeout(strTimer);
  strTimer = setTimeout(async () => {
    const q = $('#r-street').value.trim();
    if (q.length < 2) return;
    const settlement = $('#r-settlement').value.trim();
    try {
      const list = await api('/api/geo/streets?q=' + encodeURIComponent(q)
        + (settlement ? '&settlement=' + encodeURIComponent(settlement) : ''));
      const dl = $('#dl-streets'); dl.innerHTML = '';
      for (const item of list) dl.append(el('option', { value: item.name }, item.district || ''));
    } catch (e) { /* не критично */ }
  }, 200);
});

$('#r-clear').onclick = () => {
  ['#r-contractor', '#r-unp', '#r-account', '#r-bank', '#r-contact', '#r-phone',
   '#r-settlement', '#r-street', '#r-house', '#r-office',
   '#r-equipment', '#r-serial', '#r-comment'].forEach((s) => { $(s).value = ''; });
  $('#r-priority').value = 'normal';
  $('#r-date').value = new Date().toISOString().slice(0, 10);
  $('#r-geo-result').textContent = '';
};

$('#r-submit').onclick = async () => {
  const body = {
    contractor: $('#r-contractor').value.trim(), unp: $('#r-unp').value.trim(),
    bank_account: $('#r-account').value.trim(), bank_name: $('#r-bank').value.trim() || null,
    contact_person: $('#r-contact').value.trim(), phone: normalizePhone($('#r-phone').value.trim()),
    work_code: $('#r-work').value, priority: $('#r-priority').value,
    address: buildAddress(), equipment: $('#r-equipment').value.trim() || null,
    serial: $('#r-serial').value.trim() || null, comment: $('#r-comment').value.trim(),
    planned_date: $('#r-date').value || null,
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
      el('td', {}, r.planned_date || '—'),
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
    el('div', {}, 'Дата исполнения'), el('div', {}, r.planned_date || '—'),
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
  attachPhoneInput('#r-phone', '#r-phone-hint');
  $('#r-date').value = new Date().toISOString().slice(0, 10);
  loadRequests();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if ($('#live').checked && $('#tab-list').classList.contains('active')) loadRequests(); }, 30000);
}
function showLogin() { $('#login').hidden = false; $('#app').hidden = true; }

if (S.token) boot(); else showLogin();
