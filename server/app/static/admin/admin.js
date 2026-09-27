/* Админка диспетчера: заявки, инженеры, зоны, отпуска, справочники, отчёты. */
const S = { token: localStorage.getItem('crm_token') || '', user: null, page: 0, limit: 50, lastId: 0, cfg: null };

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

function toast(msg, ms = 3200) {
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
    const res = await api('/api/auth/login', { method: 'POST', body: { username: $('#lg-user').value, password: $('#lg-pass').value, device: 'web-admin' } });
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

/* ------------------------------------------------------------- навигация */
$('#tabs').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-tab]'); if (!b) return;
  document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('active', x === b));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
  loadTab(b.dataset.tab);
});

/* -------------------------------------------------------------- заявки */
function statusOptions() {
  const sel = $('#f-status');
  sel.innerHTML = '<option value="">Все статусы</option>';
  for (const s of S.cfg.statuses) sel.append(el('option', { value: s.code }, s.label));
  sel.append(el('option', { value: 'new,assigned,in_progress,pickup_office' }, 'Только активные'));
}
function filters() {
  return {
    q: $('#f-q').value.trim(), contractor: $('#f-contractor').value.trim(), unp: $('#f-unp').value.trim(),
    bank_account: $('#f-acc').value.trim(), phone: $('#f-phone').value.trim(), status: $('#f-status').value,
    engineer_id: $('#f-engineer').value, date_from: $('#f-date-from').value, date_to: $('#f-date-to').value,
  };
}
function qs(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v) p.set(k, v);
  return p.toString();
}
async function loadRequests() {
  const f = filters();
  const data = await api('/api/requests?' + qs({ ...f, limit: S.limit, offset: S.page * S.limit }));
  const tb = $('#req-table tbody'); tb.innerHTML = '';
  for (const r of data.items) {
    tb.append(el('tr', {},
      el('td', {}, el('b', {}, r.number)),
      el('td', {}, fmtDT(r.created_at)),
      el('td', {}, r.contractor),
      el('td', {}, r.unp),
      el('td', {}, el('code', {}, r.bank_account)),
      el('td', {}, r.work_name),
      el('td', {}, badge(r.priority, r.priority_label)),
      el('td', {}, badge(r.status, r.status_label)),
      el('td', {}, r.zone_name || '—'),
      el('td', {}, r.engineer_name || '—'),
      el('td', {}, r.address),
      el('td', {}, el('button', { onclick: () => openRequest(r.id) }, 'Открыть')),
    ));
  }
  $('#req-summary').innerHTML = `Найдено: <b>${data.total}</b> · страница ${S.page + 1} из ${Math.max(1, Math.ceil(data.total / S.limit))}` +
    (f.unp || f.bank_account || f.contractor ? ` · фильтр: ${esc(f.contractor || '')} ${esc(f.unp || '')} ${esc(f.bank_account || '')}` : '');
  const pg = $('#req-page'); pg.innerHTML = '';
  pg.append(el('button', { onclick: () => { if (S.page > 0) { S.page--; loadRequests(); } } }, '← Назад'));
  pg.append(el('button', { onclick: () => { if ((S.page + 1) * S.limit < data.total) { S.page++; loadRequests(); } } }, 'Вперёд →'));
  pg.append(el('button', { onclick: () => loadRequests() }, 'Обновить'));
  $('#f-export').href = '/api/requests/export.csv?' + qs(f);
}
$('#f-go').onclick = () => { S.page = 0; loadRequests(); };
$('#f-reset').onclick = () => {
  ['#f-q', '#f-contractor', '#f-unp', '#f-acc', '#f-phone', '#f-date-from', '#f-date-to'].forEach((s) => { $(s).value = ''; });
  $('#f-status').value = ''; $('#f-engineer').value = ''; S.page = 0; loadRequests();
};
document.querySelectorAll('#tab-requests input, #tab-requests select').forEach((i) =>
  i.addEventListener('keydown', (e) => { if (e.key === 'Enter') { S.page = 0; loadRequests(); } }));

async function openRequest(id) {
  const r = await api('/api/requests/' + id);
  const box = $('#modal-box'); box.innerHTML = '';
  box.append(el('h2', {}, r.number + ' — ' + r.status_label));
  box.append(el('div', { class: 'kv' },
    el('div', {}, 'Контрагент'), el('div', {}, r.contractor + (r.bank_name ? ' · ' + r.bank_name : '')),
    el('div', {}, 'УНП / р/с'), el('div', {}, r.unp + ' / ' + r.bank_account),
    el('div', {}, 'Контактное лицо'), el('div', {}, r.contact_person),
    el('div', {}, 'Телефон'), el('div', {}, el('a', { href: 'tel:' + r.phone }, r.phone_formatted)),
    el('div', {}, 'Работа'), el('div', {}, r.work_name + ' (' + (r.site_kind === 'office' ? 'в офисе' : r.site_kind === 'remote' ? 'удалённо' : 'на месте') + ')'),
    el('div', {}, 'Срочность'), el('div', {}, badge(r.priority, r.priority_label)),
    el('div', {}, 'Адрес'), el('div', {}, r.address + (r.lat ? ` (${r.lat.toFixed(5)}, ${r.lon.toFixed(5)})` : '')),
    el('div', {}, 'Район (зона)'), el('div', {}, r.zone_name || 'не определён'),
    el('div', {}, 'Инженер'), el('div', {}, (r.engineer_name || '— не назначен —') + (r.assigned_by ? ' · ' + r.assigned_by : '')),
    el('div', {}, 'Оборудование'), el('div', {}, (r.equipment || '—') + (r.serial ? ' / ' + r.serial : '')),
    el('div', {}, 'Пояснение'), el('div', {}, r.comment || '—'),
    el('div', {}, 'Плановая дата'), el('div', {}, r.planned_date || '—'),
    el('div', {}, 'Выполнено'), el('div', {}, fmtDT(r.done_at)),
  ));
  if (r.nav_url) box.append(el('p', {}, el('a', { href: r.nav_url, target: '_blank', class: 'btn' }, 'Открыть в Яндекс.Картах')));

  const deliveries = r.deliveries || [];
  if (deliveries.length) {
    box.append(el('h3', {}, 'Доставка заказчику'));
    const t = el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Дата'), el('th', {}, 'Статус'), el('th', {}, 'Переносов'), el('th', {}, 'Комментарий'), el('th', {}, ''))));
    const tb = el('tbody');
    for (const d of deliveries) tb.append(el('tr', {}, el('td', {}, d.scheduled_date), el('td', {}, d.status),
      el('td', {}, d.postpone_count), el('td', {}, d.comment || ''), el('td', {},
        el('button', { onclick: () => postponeDelivery(d.id) }, 'Перенести +1 раб. день'))));
    t.append(tb); box.append(t);
  }

  box.append(el('h3', {}, 'Действия'));
  const engSel = el('select');
  for (const e of await api('/api/admin/engineers', {}, )) engSel.append(el('option', { value: e.id, selected: e.id === r.engineer_id ? 'selected' : null }, e.full_name));
  const reason = el('input', { placeholder: 'причина переназначения' });
  box.append(el('div', { class: 'toolbar' }, engSel, reason,
    el('button', { class: 'primary', onclick: async () => { await api(`/api/requests/${id}/reassign`, { method: 'POST', body: { engineer_id: +engSel.value, reason: reason.value } }); toast('Заявка переназначена'); $('#modal').hidden = true; loadRequests(); } }, 'Переназначить')));
  const day2 = el('input', { type: 'date' });
  const why = el('input', { placeholder: 'причина переноса' });
  box.append(el('div', { class: 'toolbar' }, day2, why,
    el('button', { onclick: async () => { await api(`/api/requests/${id}/postpone`, { method: 'POST', body: { new_day: day2.value, reason: why.value } }); toast('Заявка перенесена'); $('#modal').hidden = true; loadRequests(); } }, 'Перенести заявку')));
  const cmt = el('input', { placeholder: 'комментарий к смене статуса' });
  const st = el('select');
  for (const s of S.cfg.statuses) st.append(el('option', { value: s.code, selected: s.code === r.status ? 'selected' : null }, s.label));
  box.append(el('div', { class: 'toolbar' }, st, cmt,
    el('button', { onclick: async () => { await api(`/api/requests/${id}/status`, { method: 'POST', body: { status: st.value, comment: cmt.value } }); toast('Статус изменён'); $('#modal').hidden = true; loadRequests(); } }, 'Сменить статус')));

  box.append(el('h3', {}, 'История по заявке'));
  const ev = el('div', { class: 'events' });
  for (const e of r.events || []) ev.append(el('div', {}, `${fmtDT(e.at)} · ${e.actor || '—'} · ${e.from_status ? e.from_status + ' → ' : ''}${e.to_status || ''} ${e.comment || ''}`));
  box.append(ev);
  box.append(el('div', { class: 'toolbar' }, el('button', { onclick: () => { $('#modal').hidden = true; } }, 'Закрыть')));
  $('#modal').hidden = false;
}
async function postponeDelivery(id) {
  const days = prompt('На сколько рабочих дней перенести доставку?', '1');
  if (!days) return;
  const comment = prompt('Комментарий (что не готово)?', 'Оборудование не готово') || '';
  await api(`/api/engineer/delivery/${id}/postpone`, { method: 'POST', body: { days: +days, comment } });
  toast('Доставка перенесена'); $('#modal').hidden = true; loadRequests();
}
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('#modal').hidden = true; });

/* ------------------------------------------------------------ инженеры */
async function loadEngineers() {
  const list = await api('/api/admin/engineers');
  const tb = $('#eng-table tbody'); tb.innerHTML = '';
  for (const e of list) {
    tb.append(el('tr', {},
      el('td', {}, e.id), el('td', {}, e.full_name), el('td', {}, e.phone || '—'),
      el('td', {}, e.zones_count), el('td', {}, e.open_tasks),
      el('td', {}, e.absence ? `${e.absence.kind} ${e.absence.date_from}—${e.absence.date_to}` : '—'),
      el('td', {}, e.active ? 'да' : 'нет'),
      el('td', {}, el('button', { onclick: async () => { const n = prompt('ФИО', e.full_name); if (!n) return; const p = prompt('Телефон', e.phone || ''); await api('/api/admin/engineers/' + e.id, { method: 'PUT', body: { full_name: n, phone: p, active: e.active, base_lat: e.base_lat, base_lon: e.base_lon } }); toast('Сохранено'); loadEngineers(); } }, 'Изменить'),
        ' ',
        el('button', { onclick: async () => { await api('/api/admin/engineers/' + e.id, { method: 'PUT', body: { full_name: e.full_name, phone: e.phone, active: e.active ? 0 : 1, base_lat: e.base_lat, base_lon: e.base_lon } }); loadEngineers(); } }, e.active ? 'Отключить' : 'Включить'),
        ' ',
        el('button', { class: 'danger', onclick: async () => {
          const msg = `Удалить инженера ${e.full_name}?\n\n` +
            `Зон в ведении: ${e.zones_count}. Они останутся без ответственного.\n` +
            `Открытых заявок: ${e.open_tasks} — станут нераспределёнными.\n` +
            `Закрытые заявки останутся в истории без имени инженера.`;
          if (!confirm(msg)) return;
          try {
            const r = await api('/api/admin/engineers/' + e.id, { method: 'DELETE' });
            toast(`Инженер ${r.name} удалён (откреплено заявок: ${r.requests_unlinked})`, 6000);
            loadEngineers(); loadSelects();
          } catch (err) { toast(err.message); }
        } }, 'Удалить')))
    );
  }
}
$('#e-add').onclick = async () => {
  try {
    await api('/api/admin/engineers', { method: 'POST', body: { full_name: $('#e-name').value, phone: $('#e-phone').value, base_lat: +$('#e-lat').value || null, base_lon: +$('#e-lon').value || null } });
    toast('Инженер добавлен'); $('#e-name').value = $('#e-phone').value = ''; loadEngineers(); loadSelects();
  } catch (e) { toast(e.message); }
};

/* ---------------------------------------------------------------- зоны */
async function loadZones() {
  const [cov, zones, engs] = await Promise.all([api('/api/admin/coverage' + (($('#z-day').value) ? '?day=' + $('#z-day').value : '')), api('/api/geo/zones'), api('/api/admin/engineers', {}, )]);
  const tb = $('#cov-table tbody'); tb.innerHTML = '';
  for (const c of cov.coverage) {
    tb.append(el('tr', {}, el('td', {}, c.name), el('td', {}, c.kind === 'city' ? 'Минск' : 'Область'),
      el('td', {}, c.primary ? c.primary.full_name : '— не закреплено —'),
      el('td', {}, c.engineer ? c.engineer.full_name + (c.engineer.via === 'replacement' ? ' (замена)' : c.engineer.via === 'duty' ? ' (дежурный)' : '') : '— нет доступных —')));
  }
  const lb = $('#load-table tbody'); lb.innerHTML = '';
  for (const w of cov.workload) lb.append(el('tr', {}, el('td', {}, w.full_name), el('td', {}, w.tasks), el('td', {}, w.minutes), el('td', {}, w.km)));
  const zsel = $('#as-zone'); if (zsel.options.length !== zones.length) {
    zsel.innerHTML = ''; for (const z of zones) zsel.append(el('option', { value: z.id }, z.name));
  }
}
$('#z-load').onclick = loadZones;
$('#as-go').onclick = async () => {
  try {
    const res = await api('/api/admin/assignments', { method: 'POST', body: { zone_id: +$('#as-zone').value, engineer_id: +$('#as-eng').value, date_from: $('#as-from').value || new Date().toISOString().slice(0, 10), date_to: $('#as-to').value || null, reason: $('#as-reason').value } });
    toast('Закрепление сохранено. Перераспределено заявок: ' + res.requests_reassigned); loadZones();
  } catch (e) { toast(e.message); }
};
$('#z-fix').onclick = async () => {
  const r = await api('/api/admin/reassign-absent', { method: 'POST' });
  toast('Перераспределено заявок: ' + r.requests_reassigned); loadZones(); loadRequests();
};

/* ------------------------------------------------------------- отпуска */
async function loadAbsences() {
  const list = await api('/api/admin/absences');
  const tb = $('#ab-table tbody'); tb.innerHTML = '';
  const KIND = { vacation: 'Отпуск', sick: 'Больничный', dayoff: 'Отгул', other: 'Прочее' };
  for (const a of list) tb.append(el('tr', {}, el('td', {}, a.engineer_name), el('td', {}, KIND[a.kind] || a.kind),
    el('td', {}, a.date_from), el('td', {}, a.date_to), el('td', {}, a.replacement_name || '—'), el('td', {}, a.comment || ''),
    el('td', {}, el('button', { class: 'danger', onclick: async () => { if (confirm('Удалить запись?')) { await api('/api/admin/absences/' + a.id, { method: 'DELETE' }); loadAbsences(); toast('Удалено'); } } }, 'Удалить'))));
}
$('#ab-add').onclick = async () => {
  try {
    const res = await api('/api/admin/absences', { method: 'POST', body: { engineer_id: +$('#ab-eng').value, date_from: $('#ab-from').value, date_to: $('#ab-to').value, kind: $('#ab-kind').value, replacement_engineer_id: $('#ab-repl').value ? +$('#ab-repl').value : null, comment: $('#ab-comment').value } });
    toast('Оформлено. Перераспределено заявок: ' + res.requests_reassigned); loadAbsences(); loadZones();
  } catch (e) { toast(e.message); }
};

/* ------------------------------------------------------- справочник работ */
async function loadWorks() {
  const list = await api('/api/admin/works');
  const tb = $('#work-table tbody'); tb.innerHTML = '';
  for (const w of list) tb.append(el('tr', {}, el('td', {}, w.code), el('td', {}, w.name), el('td', {}, w.category),
    el('td', {}, w.site_kind === 'office' ? 'в офисе' : w.site_kind === 'remote' ? 'удалённо' : 'на месте'),
    el('td', {}, w.default_minutes), el('td', {}, w.active ? 'да' : 'нет')));
}
$('#w-add').onclick = async () => {
  try {
    await api('/api/admin/works', { method: 'POST', body: { code: $('#w-code').value, name: $('#w-name').value, category: $('#w-cat').value || 'Прочее', site_kind: $('#w-site').value, default_minutes: +$('#w-min').value || 60 } });
    toast('Работа сохранена'); loadWorks();
  } catch (e) { toast(e.message); }
};

/* ------------------------------------------------------------ настройки */
async function loadSettings() {
  const list = await api('/api/admin/settings');
  const box = $('#settings-form'); box.innerHTML = '';
  const labels = {
    company_name: 'Название компании', office_address: 'Адрес офиса/склада', office_phone: 'Телефон офиса',
    office_lat: 'Широта офиса', office_lon: 'Долгота офиса (старт маршрута)', workday_start: 'Начало рабочего дня',
    workday_end: 'Окончание рабочего дня', yandex_geocoder_key: 'Ключ Яндекс.Геокодера (адрес → координаты)',
    delivery_lead_days: 'Доставка заказчику, рабочих дней', route_radius_warn_km: 'Предупреждение о радиусе, км',
    duty_engineer_id: 'Дежурный инженер (ID) при отсутствии ответственного',
  };
  for (const s of list) box.append(el('label', {}, labels[s.key] || s.key, el('input', { 'data-key': s.key, value: s.value || '' })));
}
$('#set-save').onclick = async () => {
  const values = {};
  document.querySelectorAll('#settings-form input[data-key]').forEach((i) => { values[i.dataset.key] = i.value; });
  await api('/api/admin/settings', { method: 'POST', body: { values } });
  toast('Настройки сохранены');
};

/* ------------------------------------------------- очистка демо-данных */
$('#purge-demo').onclick = async () => {
  if (!confirm('Удалить ВСЕ демо-записи?\n\nУдаляются: заявки, доставки, контрагенты, инженеры, закрепления зон, отпуска, лента событий.\nОстаются: зоны, справочник работ, настройки, пользователи.')) return;
  try {
    const r = await api('/api/admin/purge-demo', { method: 'POST' });
    toast(r.message || 'Демо-данные удалены', 6000);
    loadRequests();
  } catch (e) { toast(e.message); }
};

/* --------------------------------------------- пользователи (4 раздела) */
const ROLE_LABELS = { client: 'Клиент', engineer: 'Инженер', operator: 'Диспетчер', admin: 'Администратор' };
const NU = { role: 'client', users: [] };

async function loadUsers() {
  NU.users = await api('/api/admin/users');
  renderUsers();
  const engs = await api('/api/admin/engineers');
  const sel = $('#nu-eng'); const keep = sel.value;
  sel.innerHTML = '<option value="">— не привязан к инженеру —</option>';
  engs.filter((e) => e.active).forEach((e) => sel.append(el('option', { value: e.id }, e.full_name)));
  sel.value = keep;
}

function renderUsers() {
  const role = NU.role;
  document.querySelectorAll('#role-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.role === role));
  const isClient = role === 'client', isEngineer = role === 'engineer';
  $('#nu-client-fields').hidden = !isClient;
  $('#nu-engineer-fields').hidden = !isEngineer;
  $('#nu-role-hint').textContent = 'Новый пользователь попадёт в раздел «' + ROLE_LABELS[role] + '»';
  $('#nu-col-extra').textContent = isClient ? 'Организация / УНП' : isEngineer ? 'Привязка к инженеру' : 'Роль';
  const tb = $('#nu-table tbody'); tb.innerHTML = '';
  for (const u of NU.users.filter((x) => x.role === role)) {
    const extra = isClient ? (u.company || '—') + ' · ' + (u.unp || 'нет УНП')
      : isEngineer ? (u.engineer_id ? 'инженер #' + u.engineer_id : '— не привязан —')
      : ROLE_LABELS[u.role];
    tb.append(el('tr', {},
      el('td', {}, u.id),
      el('td', {}, el('b', {}, u.username)),
      el('td', {}, u.full_name || '—'),
      el('td', {}, extra),
      el('td', {}, u.active ? el('span', { class: 'badge b-planned' }, 'активен') : el('span', { class: 'badge b-cancelled' }, 'заблокирован')),
      el('td', {},
        el('button', { onclick: async () => {
          const p = prompt('Новый пароль для ' + u.username + ' (мин. 6 символов):');
          if (p === null) return;
          try { await api(`/api/admin/users/${u.id}/password`, { method: 'POST', body: { password: p } }); toast('Пароль обновлён'); } catch (e) { toast(e.message); }
        } }, 'Пароль'),
        ' ',
        el('button', { onclick: async () => {
          try { await api(`/api/admin/users/${u.id}/active`, { method: 'POST', body: { active: u.active ? 0 : 1 } }); loadUsers(); } catch (e) { toast(e.message); }
        } }, u.active ? 'Блокировать' : 'Разблокировать'),
        ' ',
        el('button', { class: 'danger', onclick: async () => {
          if (!confirm('Удалить пользователя ' + u.username + '?')) return;
          try { await api(`/api/admin/users/${u.id}`, { method: 'DELETE' }); loadUsers(); toast('Удалено'); } catch (e) { toast(e.message); }
        } }, 'Удалить'),
      )));
  }
  if (!tb.children.length) tb.append(el('tr', {}, el('td', { colspan: 6 }, 'В этом разделе пока нет пользователей')));
}

document.querySelectorAll('#role-tabs button').forEach((b) => b.onclick = () => { NU.role = b.dataset.role; renderUsers(); });
$('#nu-add').onclick = async () => {
  const body = {
    username: $('#nu-username').value.trim(), password: $('#nu-pass').value,
    full_name: $('#nu-full').value.trim(), role: NU.role,
  };
  if (NU.role === 'client') { body.company = $('#nu-company').value.trim(); body.unp = $('#nu-unp').value.trim(); }
  if (NU.role === 'engineer' && $('#nu-eng').value) body.engineer_id = +$('#nu-eng').value;
  try {
    await api('/api/admin/users', { method: 'POST', body });
    toast('Пользователь создан');
    ['#nu-username', '#nu-pass', '#nu-full', '#nu-company', '#nu-unp'].forEach((s) => { $(s).value = ''; });
    loadUsers();
  } catch (e) { toast(e.message); }
};

/* --------------------------------------------------------------- отчёты */
async function loadReports() {
  const f = $('#r-from').value, t = $('#r-to').value;
  const r = await api('/api/admin/reports/summary?' + qs({ day_from: f, day_to: t }));
  const bar = (label, val, max) => el('div', { class: 'bar' }, el('span', {}, label), el('span', {}, el('i', { style: `width:${max ? Math.round(val / max * 100) : 0}%` })), el('span', {}, val));
  const box = $('#report'); box.innerHTML = '';
  const mk = (title, rows, keyLabel, keyVal) => {
    const b = el('div', { class: 'report-block' }, el('h3', {}, title), el('div', { class: 'bars' }));
    const max = Math.max(1, ...rows.map((x) => keyVal(x)));
    for (const x of rows) b.lastChild.append(bar(x[keyLabel] + (x.label ? ' ' + x.label : ''), keyVal(x), max));
    return b;
  };
  box.append(el('div', { class: 'summary' }, `Период: <b>${r.period.from} — ${r.period.to}</b>`, `Нераспределённых заявок: <b>${r.unassigned}</b>`));
  box.append(mk('По статусам', r.by_status, 'label', (x) => x.c));
  box.append(mk('По срочности', r.by_priority, 'label', (x) => x.c));
  box.append(mk('По инженерам (всего)', r.by_engineer, 'engineer', (x) => x.total));
  box.append(mk('По районам', r.by_zone, 'zone', (x) => x.c));
  box.append(mk('Топ работ', r.by_work, 'work', (x) => x.c));
  box.append(el('div', { class: 'report-block' }, el('h3', {}, 'Загрузка на сегодня'), el('pre', { style: 'white-space:pre-wrap' }, r.workload.map((w) => `${w.full_name}: задач ${w.tasks}, ${w.minutes} мин, ${w.km} км${w.overload ? ' ⚠ перегруз' : ''}`).join('\n'))));
  box.append(el('div', { class: 'report-block' }, el('h3', {}, 'Доставки заказчикам (забор в офис)'), el('pre', {}, r.deliveries.map((d) => `${d.scheduled_date}: ${d.c}`).join('\n') || 'нет')));
}

/* --------------------------------------------------------------- журнал */
async function loadAudit() {
  const list = await api('/api/admin/audit?limit=300');
  const tb = $('#audit-table tbody'); tb.innerHTML = '';
  for (const a of list) tb.append(el('tr', {}, el('td', {}, fmtDT(a.at)), el('td', {}, a.actor || ''), el('td', {}, a.action), el('td', {}, el('code', {}, (a.payload || '').slice(0, 160)))));
}

/* -------------------------------------------------------- вспомогательное */
async function loadSelects() {
  const engs = await api('/api/admin/engineers');
  const opts = () => engs.map((e) => el('option', { value: e.id }, e.full_name));
  $('#f-engineer').innerHTML = '<option value="">Все инженеры</option>';
  engs.forEach((e) => $('#f-engineer').append(el('option', { value: e.id }, e.full_name)));
  for (const sel of ['#as-eng', '#ab-eng', '#u-eng']) {
    const keep = $(sel).value; $(sel).innerHTML = sel === '#u-eng' ? '<option value="">— не привязан —</option>' : '';
    opts().forEach((o) => $(sel).append(o)); $(sel).value = keep;
  }
  const repl = $('#ab-repl'); repl.innerHTML = '<option value="">— замена не указана —</option>';
  engs.forEach((e) => repl.append(el('option', { value: e.id }, e.full_name)));
}

function loadTab(name) {
  return ({
    requests: loadRequests, engineers: loadEngineers, zones: loadZones, absences: loadAbsences,
    works: loadWorks, users: loadUsers, reports: loadReports, settings: loadSettings, audit: loadAudit,
  }[name] || (() => {}))();
}

/* ------------------------------------------- живое обновление (SSE) */
function startLive() {
  if (!S.token) return;
  const es = new EventSource('/api/events/stream?token=' + encodeURIComponent(S.token));
  es.onmessage = () => { if ($('#live').checked && $('#tab-requests').classList.contains('active')) loadRequests(); };
  es.onerror = () => setTimeout(() => es.close(), 5000);
}

/* --------------------------------------------------------------- запуск */
async function boot() {
  try { S.user = await api('/api/auth/me'); } catch { S.token = ''; localStorage.removeItem('crm_token'); return showLogin(); }
  S.cfg = await api('/api/config');
  $('#login').hidden = true; $('#app').hidden = false;
  $('#whoami').textContent = `${S.user.full_name || S.user.username} (${S.user.role})`;
  $('#as-from').value = new Date().toISOString().slice(0, 10);
  $('#z-day').value = new Date().toISOString().slice(0, 10);
  $('#r-from').value = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  $('#r-to').value = new Date().toISOString().slice(0, 10);
  statusOptions(); await loadSelects(); await loadRequests(); startLive();
}
function showLogin() { $('#login').hidden = false; $('#app').hidden = true; }

if (S.token) boot(); else showLogin();
