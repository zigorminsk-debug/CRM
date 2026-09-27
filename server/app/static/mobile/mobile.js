/* Приложение инженера (Android/WebView): маршрут на день, «Поехали», чекбоксы готовности. */
const S = { token: localStorage.getItem('crm_token') || '', user: null, day: new Date().toISOString().slice(0, 10), feedId: 0, plan: null };

const $ = (s) => document.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === 'class') n.className = v; else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
};
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function toast(msg, ms = 4500) {
  const t = el('div', { class: 'toast' }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}

async function api(path, opts = {}) {
  const o = { headers: { 'Content-Type': 'application/json' }, ...opts };
  if (S.token) o.headers.Authorization = 'Bearer ' + S.token;
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const txt = await r.text();
  let data; try { data = txt ? JSON.parse(txt) : {}; } catch { data = {}; }
  if (!r.ok) throw new Error(data.error || data.detail || 'HTTP ' + r.status);
  return data;
}

/* --------------------------------------------------------- вход/выход */
async function login() {
  try {
    const res = await api('/api/auth/login', { method: 'POST', body: { username: $('#lg-user').value, password: $('#lg-pass').value, device: 'android' } });
    if (res.role !== 'engineer' && !res.engineer_id) toast('Внимание: пользователь не привязан к инженеру — заявки не придут');
    S.token = res.token; localStorage.setItem('crm_token', S.token); S.user = res;
    await boot();
  } catch (e) { toast('Ошибка входа: ' + e.message); }
}
$('#lg-go').onclick = login;
$('#lg-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') login(); });
$('#btn-out').onclick = async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
  localStorage.removeItem('crm_token'); location.reload();
};
$('#btn-refresh').onclick = () => load();
$('#btn-day').onclick = () => {
  const d = prompt('Дата маршрута (ГГГГ-ММ-ДД):', S.day);
  if (d) { S.day = d; load(); }
};

/* ------------------------------------------------------------ маршрут */
function openNav(item) {
  const deep = item.nav_intent;
  if (deep) {
    // Яндекс.Навигатор установлен -> откроется он; иначе браузер откроет карты по https-ссылке
    location.href = deep;
    const fallback = item.nav_url || `https://yandex.ru/maps/?rtext=${item.lat},${item.lon}&rtt=auto`;
    setTimeout(() => { if (!document.hidden) window.open(fallback, '_blank'); }, 900);
  } else if (item.nav_url) {
    window.open(item.nav_url, '_blank');
  } else {
    toast('У заявки нет координат — уточните адрес в диспетчерской');
  }
}

function taskCard(t) {
  const card = el('div', { class: 'card' });
  card.append(el('div', { class: 'top' },
    el('div', {}, el('span', { class: 'order' }, t.order || '•'),
      el('span', { class: 'num' }, t.number), el('div', { class: 'work' }, t.work_name)),
    el('div', {}, t.priority_label ? el('span', { class: 'badge b-' + t.priority }, t.priority_label) : null,
      t.eta ? el('div', { class: 'eta' }, 'прибытие ~' + t.eta) : null)));
  card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Адрес'), el('span', { class: 'addr' }, t.address)));
  card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Контакт'), el('span', {}, t.contact_person || '—')));
  if (t.phone || t.phone_formatted) {
    card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Телефон'),
      el('a', { href: 'tel:' + (t.phone || t.phone_formatted), style: 'color:#7dd3fc' }, t.phone_formatted || t.phone)));
  }
  card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Контрагент'), el('span', {}, t.contractor + (t.zone_name ? ' · ' + t.zone_name : ''))));
  if (t.leg_km !== undefined) card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Дорога'), el('span', {}, `${t.leg_km} км / ~${t.drive_minutes} мин, работа ${t.work_minutes} мин`)));
  if (t.comment) card.append(el('div', { class: 'comment' }, t.comment));

  const actions = el('div', { class: 'actions' });
  actions.append(el('button', { class: 'primary', onclick: async () => {
    try {
      const res = await api(`/api/engineer/task/${t.id}/go`, { method: 'POST', body: { lat: null, lon: null } });
      openNav(res);
      await load();
    } catch (e) { toast(e.message); }
  } }, '🚗 Поехали (Яндекс.Навигатор)'));

  const c1 = el('label', { class: 'chk' });
  const i1 = el('input', { type: 'checkbox', onchange: (e) => finish(t, 'onsite', e.target) });
  c1.append(i1, el('span', {}, 'Готово на месте (выполнено)'));
  const c2 = el('label', { class: 'chk' });
  const i2 = el('input', { type: 'checkbox', onchange: (e) => finish(t, 'pickup_office', e.target) });
  c2.append(i2, el('span', {}, 'Забор в офис (картридж/оборудование)'));
  card.append(actions, c1, c2);

  card.append(el('div', { class: 'actions' },
    el('button', { onclick: () => details(t) }, 'Подробнее'),
    el('button', { class: 'warn', onclick: () => postpone(t) }, 'Перенести')));
  return card;
}

async function finish(t, result, checkbox) {
  const comment = result === 'pickup_office'
    ? (prompt('Забор в офис. Комментарий (что забираем, что не готово):', '') || '')
    : (prompt('Готово на месте. Что сделано:', 'Работы выполнены, заказчик принял') || '');
  try {
    const res = await api(`/api/engineer/task/${t.id}/done`, { method: 'POST', body: { result, comment } });
    if (result === 'pickup_office' && res.delivery) {
      toast(`Забор оформлен. Доставка заказчику: ${res.delivery.scheduled_date} (следующий рабочий день). При неготовности можно перенести.`, 9000);
    } else {
      toast('Отмечено: готово на месте');
    }
    await load();
  } catch (e) {
    toast('Не удалось сохранить: ' + e.message); checkbox.checked = false;
  }
}

async function postpone(t) {
  const d = prompt('На какую дату перенести заявку (ГГГГ-ММ-ДД)?', S.day);
  if (!d) return;
  const reason = prompt('Причина переноса:', 'Оборудование не готово') || '';
  try { await api(`/api/requests/${t.id}/postpone`, { method: 'POST', body: { new_day: d, reason } }); toast('Перенесено на ' + d); await load(); }
  catch (e) { toast(e.message); }
}

function deliveryCard(d) {
  const card = el('div', { class: 'card' });
  card.append(el('div', { class: 'top' },
    el('div', {}, el('div', { class: 'num' }, 'Доставка: ' + d.number), el('div', { class: 'work' }, d.work_name)),
    el('span', { class: 'badge b-pickup_office' }, 'выдача ' + d.scheduled_date)));
  card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Адрес'), el('span', { class: 'addr' }, d.address)));
  card.append(el('div', { class: 'row' }, el('span', { class: 'k' }, 'Контакт'), el('span', {}, `${d.contact_person || '—'} ${d.phone || ''}`)));
  if (d.comment) card.append(el('div', { class: 'comment' }, d.comment + (d.postpone_count ? ` (переносов: ${d.postpone_count})` : '')));
  card.append(el('div', { class: 'actions' },
    el('button', { class: 'ok', onclick: async () => { await api(`/api/engineer/delivery/${d.id}/done`, { method: 'POST' }); toast('Доставлено заказчику'); await load(); } }, '✓ Доставлено'),
    el('button', { class: 'warn', onclick: async () => {
      const reason = prompt('Причина переноса (что не готово)?', 'Оборудование не готово') || '';
      const res = await api(`/api/engineer/delivery/${d.id}/postpone`, { method: 'POST', body: { days: 1, comment: reason } });
      toast('Перенесено на ' + res.scheduled_date); await load();
    } }, 'Перенести +1 раб. день')));
  return card;
}

function details(t) {
  const box = $('#sheet-box'); box.innerHTML = '';
  box.append(el('h3', {}, t.number + ' · ' + (t.status_label || '')));
  const kv = el('div', { class: 'kv' });
  const row = (k, v) => { kv.append(el('div', { class: 'k' }, k), el('div', {}, v ?? '—')); };
  row('Контрагент', t.contractor); row('УНП', t.unp); row('р/с', t.bank_account);
  row('Контактное лицо', t.contact_person); row('Телефон', t.phone_formatted || t.phone);
  row('Работа', t.work_name); row('Срочность', t.priority_label); row('Статус', t.status_label);
  row('Адрес', t.address + (t.lat ? ` (${t.lat.toFixed(5)}, ${t.lon.toFixed(5)})` : ''));
  row('Оборудование', (t.equipment || '') + (t.serial ? ' / ' + t.serial : ''));
  row('Плановая дата', t.planned_date); row('Пояснение', t.comment);
  box.append(kv);
  box.append(el('div', { class: 'actions' },
    el('button', { class: 'primary', onclick: () => { openNav(t); } }, 'Открыть маршрут'),
    el('button', { onclick: () => { $('#sheet').hidden = true; } }, 'Закрыть')));
  $('#sheet').hidden = false;
}
$('#sheet').addEventListener('click', (e) => { if (e.target.id === 'sheet') $('#sheet').hidden = true; });

/* ------------------------------------------------------------ загрузка */
async function load() {
  const plan = await api('/api/engineer/route?day=' + S.day);
  S.plan = plan;
  $('#eng-name').textContent = plan.engineer.full_name;
  $('#route-meta').textContent = `${plan.day} · заявок ${plan.route.length} · ${plan.total_km} км · ~${plan.total_hours} ч`;
  const sum = $('#summary'); sum.innerHTML = '';
  sum.append(el('span', {}, 'Старт: ', el('b', {}, plan.start.name)));
  if (plan.overflow && plan.overflow.length) sum.append(el('span', {}, `⚠ не влезает в день: `, el('b', {}, plan.overflow.length)));
  if (plan.deliveries && plan.deliveries.length) sum.append(el('span', {}, 'Доставок: ', el('b', {}, plan.deliveries.length)));

  const box = $('#tasks'); box.innerHTML = '';
  const active = (plan.route || []).filter((t) => !['done_onsite', 'delivered', 'closed'].includes(t.status));
  const finished = (plan.route || []).filter((t) => ['done_onsite', 'delivered', 'closed'].includes(t.status));
  if (!active.length) box.append(el('div', { class: 'card muted' }, 'На этот день активных заявок нет. Ожидайте поступления новых.'));
  active.forEach((t) => box.append(taskCard(t)));
  finished.forEach((t) => { const c = taskCard(t); c.classList.add('done'); box.append(c); });

  const dbox = $('#deliveries'); dbox.innerHTML = '';
  $('#deliveries-sec').hidden = !(plan.deliveries || []).length;
  (plan.deliveries || []).forEach((d) => dbox.append(deliveryCard(d)));

  const other = $('#tasks-other'); other.innerHTML = '';
  const nodata = (plan.route || []).filter((t) => !t.lat || !t.lon);
  if (!nodata.length) other.append(el('div', { class: 'card muted' }, 'Все заявки на день имеют координаты.'));
  nodata.forEach((t) => other.append(taskCard(t)));

  if (plan.route && plan.route.length) {
    const pts = [plan.start.lat + ',' + plan.start.lon, ...plan.route.filter((t) => t.lat).map((t) => t.lat + ',' + t.lon)];
    $('#all-route').href = 'https://yandex.ru/maps/?rtext=' + pts.join('~') + '&rtt=auto';
  } else { $('#all-route').href = 'https://yandex.ru/maps/'; }
}

/* --------------------------------------------------- push-уведомления */
async function pollFeed() {
  if (!S.token) return;
  try {
    const res = await api('/api/engineer/feed?since=' + S.feedId + '&wait=25');
    if (res.last_id) S.feedId = res.last_id;
    for (const ev of res.events || []) {
      if (['task.new', 'task.postponed', 'task.pickup', 'zone.replacement'].includes(ev.kind)) {
        const msg = ({ 'task.new': 'Новая заявка', 'task.postponed': 'Заявка перенесена', 'task.pickup': 'Оформлен забор в офис', 'zone.replacement': 'Вам передана зона (замена)' })[ev.kind];
        toast(`${msg}: ${ev.payload.number || ''} ${ev.payload.address || ''}`);
        if (window.Notification && Notification.permission === 'granted') {
          new Notification(msg, { body: `${ev.payload.number || ''} ${ev.payload.address || ''}` });
        }
        await load();
      }
    }
  } catch (e) { /* сеть недоступна — повторим */ }
  setTimeout(pollFeed, 1500);
}
$('#btn-notify').onclick = async () => {
  if (window.Notification) { const p = await Notification.requestPermission(); toast('Уведомления: ' + p); }
  else toast('Браузер не поддерживает уведомления — работает всплывающий канал');
};

/* ---------------------------------------------------------------- запуск */
async function boot() {
  try { S.user = await api('/api/auth/me'); } catch { localStorage.removeItem('crm_token'); S.token = ''; return showLogin(); }
  $('#login').hidden = true; $('#app').hidden = false;
  await load(); pollFeed();
  setInterval(() => { if (document.visibilityState === 'visible') load(); }, 60000);
}
function showLogin() { $('#login').hidden = false; $('#app').hidden = true; }
if (S.token) boot(); else showLogin();
