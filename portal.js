const messageBox = document.querySelector('#message');
const mode = document.body.dataset.mode;
let csrfToken = '';
let notificationCursor = null;
let notificationItems = [];
let notificationUnreadCount = 0;

async function getCsrf() {
  const response = await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' });
  const data = await response.json();
  csrfToken = data.csrf_token;
}

function notify(message, error = false) {
  if (!messageBox) return;
  messageBox.textContent = message;
  messageBox.classList.toggle('error', error);
  messageBox.classList.add('visible');
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') {
    if (!csrfToken) await getCsrf();
    headers['X-CSRF-Token'] = csrfToken;
  }
  if (options.body && typeof options.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(data?.detail || 'Не удалось выполнить запрос. Попробуй ещё раз.');
  return data;
}

function formObject(form) {
  const data = new FormData(form);
  const result = Object.fromEntries(data.entries());
  if ('study_year' in result) result.study_year = result.study_year === '' ? null : Number(result.study_year);
  for (const key of Object.keys(result)) if (result[key] === '') result[key] = null;
  return result;
}

async function initAuth() {
  await getCsrf();
  const login = document.querySelector('#login-form');
  const register = document.querySelector('#register-form');
  if (login) login.addEventListener('submit', async event => {
    event.preventDefault();
    try { const result = await api('/api/v1/auth/login', { method: 'POST', body: formObject(login) }); location.href = result.password_reset_required ? '/change-password' : '/cabinet'; }
    catch (error) { notify(error.message, true); }
  });
  if (register) register.addEventListener('submit', async event => {
    event.preventDefault();
    try { await api('/api/v1/auth/register', { method: 'POST', body: formObject(register) }); location.href = '/cabinet'; }
    catch (error) { notify(error.message, true); }
  });
}

const statusLabels = { pending: 'Ожидает подтверждения', active: 'Подтверждён', suspended: 'Приостановлен', left: 'Не состоит' };

function renderNotifications(append = false) {
  const host = document.querySelector('#notification-list');
  if (!append) host.replaceChildren();
  if (!notificationItems.length && !append) {
    const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Пока уведомлений нет.'; host.append(empty);
  }
  const count = document.querySelector('#notification-unread-count');
  count.textContent = notificationUnreadCount ? String(notificationUnreadCount) : ''; count.hidden = !notificationUnreadCount;
  document.querySelector('#notifications-read-all').hidden = !notificationUnreadCount;
  for (const item of notificationItems.slice(append ? -30 : 0)) {
    const card = document.createElement('article'); card.className = `notification-card${item.read_at ? '' : ' is-unread'}`;
    const content = document.createElement('div');
    const heading = document.createElement('strong'); heading.textContent = item.title;
    const body = document.createElement('p'); body.textContent = item.body;
    const time = document.createElement('time'); time.dateTime = item.created_at; time.textContent = new Date(item.created_at).toLocaleString('ru-RU');
    content.append(heading, body, time); card.append(content);
    const actions = document.createElement('div'); actions.className = 'notification-actions';
    if (item.link && item.link.startsWith('/') && !item.link.startsWith('//')) {
      const link = document.createElement('a'); link.href = item.link; link.className = 'table-action'; link.textContent = 'Открыть'; actions.append(link);
    }
    if (!item.read_at) {
      const read = document.createElement('button'); read.type = 'button'; read.className = 'plain-button'; read.textContent = 'Прочитано';
      read.addEventListener('click', async () => { try { await api(`/api/v1/members/me/notifications/${item.id}/read`, { method: 'PATCH' }); item.read_at = new Date().toISOString(); notificationUnreadCount = Math.max(0, notificationUnreadCount - 1); renderNotifications(); } catch (error) { notify(error.message, true); } });
      actions.append(read);
    }
    card.append(actions); host.append(card);
  }
  const more = document.querySelector('#notifications-load-more');
  more.hidden = !notificationCursor;
}

async function loadNotifications(append = false) {
  const params = new URLSearchParams({ limit: '30' });
  if (append && notificationCursor) params.set('before_id', String(notificationCursor));
  const page = await api(`/api/v1/members/me/notifications?${params}`);
  notificationItems = append ? notificationItems.concat(page.items) : page.items;
  notificationUnreadCount = page.unread_count;
  notificationCursor = page.has_more ? page.next_before_id : null;
  renderNotifications(append);
}

async function initCabinet() {
  await getCsrf();
  const profile = await api('/api/v1/members/me');
  document.querySelector('#cabinet-view').classList.remove('hidden');
  document.querySelector('#profile-email').textContent = `@${profile.username}${profile.email ? ` · ${profile.email}` : ''}`;
  document.querySelector('#profile-mark').textContent = profile.full_name.trim().slice(0, 1).toUpperCase();
  const badge = document.querySelector('#membership-status');
  badge.textContent = statusLabels[profile.membership_status] || profile.membership_status;
  badge.classList.add(`status-${profile.membership_status}`);
  const form = document.querySelector('#profile-form');
  for (const [key, value] of Object.entries(profile)) {
    const field = form.elements.namedItem(key);
    if (field) field.value = value ?? '';
  }
  if (profile.roles.some(role => ['super_admin', 'national_board', 'national_officer', 'lc_director', 'local_officer', 'exchange_reviewer'].includes(role))) {
    const adminLink = document.querySelector('#admin-link');
    adminLink.href = profile.roles.includes('exchange_reviewer') && !profile.roles.some(role => ['super_admin', 'national_board', 'national_officer', 'lc_director', 'local_officer'].includes(role)) ? '/admin/exchanges' : '/admin';
    adminLink.classList.remove('hidden');
  }
  if (profile.roles.some(role => ['super_admin', 'national_board', 'lc_director', 'local_officer'].includes(role))) {
    document.querySelector('#reports-link').classList.remove('hidden');
  }
  await loadNotifications();
  document.querySelector('#notifications-load-more').addEventListener('click', async () => { try { await loadNotifications(true); } catch (error) { notify(error.message, true); } });
  document.querySelector('#notifications-read-all').addEventListener('click', async () => { try { await api('/api/v1/members/me/notifications/read-all', { method:'POST', body:{} }); notificationItems.forEach(item => { item.read_at ||= new Date().toISOString(); }); notificationUnreadCount = 0; renderNotifications(); } catch (error) { notify(error.message, true); } });
  const registrations = await api('/api/v1/members/me/events');
  const eventList = document.querySelector('#my-events-list');
  if (!registrations.length) eventList.innerHTML = '<p class="empty">Ты пока не записан(а) на мероприятия. <a href="/events.html">Посмотреть календарь →</a></p>';
  else eventList.innerHTML = registrations.map(event => {
    const title = String(event.title).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const location = String(event.location || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const state = event.my_status === 'waitlisted' ? 'Лист ожидания' : event.my_status === 'attended' ? 'Посещение отмечено' : event.my_status === 'no_show' ? 'Отмечено отсутствие' : 'Место подтверждено';
    const canCancel = ['confirmed', 'waitlisted'].includes(event.my_status);
    return `<article class="committee-card"><span class="committee-index">${state}</span><h3>${title}</h3><p class="committee-university">${new Date(event.starts_at).toLocaleString('ru-RU',{dateStyle:'long',timeStyle:'short'})}</p><p class="committee-description">${location}</p>${canCancel ? `<button class="plain-button cancel-event" data-event-id="${event.id}">Отменить запись</button>` : ''}</article>`;
  }).join('');
  eventList.querySelectorAll('.cancel-event').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Отменить запись на мероприятие?')) return;
    try { await api(`/api/v1/events/${button.dataset.eventId}/register`, { method: 'DELETE' }); location.reload(); }
    catch (error) { notify(error.message, true); }
  }));
  const certificates = await api('/api/v1/members/me/certificates');
  const certificateList = document.querySelector('#my-certificates-list');
  if (!certificates.length) certificateList.innerHTML = '<p class="empty">Сертификаты появятся здесь после участия в мероприятиях.</p>';
  else certificateList.innerHTML = certificates.map(certificate => {
    const title = String(certificate.event_title).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const issued = new Date(certificate.issued_at).toLocaleDateString('ru-RU', {dateStyle:'long'});
    const status = certificate.status === 'issued' ? '<span class="status-badge status-active">Действителен</span>' : '<span class="status-badge status-suspended">Отозван</span>';
    const actions = certificate.status === 'issued' ? `<a class="table-action" href="${certificate.print_url}">Скачать / печать</a> <a class="table-action" href="${certificate.verification_url}" target="_blank" rel="noreferrer">Проверить подлинность ↗</a>` : `<a class="table-action" href="${certificate.verification_url}" target="_blank" rel="noreferrer">Посмотреть статус ↗</a>`;
    return `<article class="certificate-card"><div><span class="eyebrow">${status} · ${issued}</span><h3>${title}</h3></div><div class="certificate-actions">${actions}</div></article>`;
  }).join('');
  const exchangeStatusLabels = { submitted: 'Получена', under_review: 'На рассмотрении', shortlisted: 'Шорт-лист', accepted: 'Принята', rejected: 'Не прошла отбор', withdrawn: 'Отозвана' };
  const exchangeApps = await api('/api/v1/members/me/exchange-applications');
  const exchangeList = document.querySelector('#my-exchange-applications');
  if (!exchangeApps.length) exchangeList.innerHTML = '<p class="empty">Заявок пока нет. <a href="/opportunities.html#exchange-calls">Посмотреть открытые наборы →</a></p>';
  else exchangeList.innerHTML = exchangeApps.map(application => {
    const title = String(application.call_title).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const note = application.reviewer_note ? `<p class="reviewer-note"><strong>Сообщение от команды:</strong> ${String(application.reviewer_note).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</p>` : '';
    const withdraw = ['submitted', 'under_review'].includes(application.status) ? `<button class="plain-button withdraw-exchange" data-id="${application.id}" type="button">Отозвать заявку</button>` : '';
    const destination = String(application.destination || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    return `<article class="exchange-my-card"><div><span class="status-badge">${exchangeStatusLabels[application.status] || application.status}</span><h3>${title}</h3><p>${destination} · отправлена ${new Date(application.submitted_at).toLocaleDateString('ru-RU')}</p>${note}<details class="exchange-history" data-id="${application.id}"><summary>История рассмотрения</summary><div>Загрузка истории…</div></details>${withdraw}</div></article>`;
  }).join('');
  exchangeList.querySelectorAll('.withdraw-exchange').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Отозвать заявку на обмен?')) return;
    try { await api(`/api/v1/members/me/exchange-applications/${button.dataset.id}`, { method: 'DELETE' }); location.reload(); }
    catch (error) { notify(error.message, true); }
  }));
  exchangeList.querySelectorAll('.exchange-history').forEach(details => details.addEventListener('toggle', async () => {
    if (!details.open || details.dataset.loaded) return;
    try {
      const history = await api(`/api/v1/members/me/exchange-applications/${details.dataset.id}/history`);
      details.querySelector('div').innerHTML = history.length ? history.map(item => `<p>${exchangeStatusLabels[item.to_status] || item.to_status} · ${new Date(item.created_at).toLocaleString('ru-RU')}${item.note ? `<br><span>${String(item.note).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}</span>` : ''}</p>`).join('') : 'История появится здесь.';
      details.dataset.loaded = 'true';
    } catch (error) { details.querySelector('div').textContent = error.message; }
  }));
  const passwordForm = document.querySelector('#password-form');
  passwordForm.addEventListener('submit', async event => {
    event.preventDefault();
    const data = formObject(passwordForm);
    if (data.new_password !== data.confirm_password) { notify('Новые пароли не совпадают.', true); return; }
    delete data.confirm_password;
    try { await api('/api/v1/auth/password', { method: 'POST', body: data }); passwordForm.reset(); notify('Пароль обновлён. Другие активные сессии завершены.'); }
    catch (error) { notify(error.message, true); }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    try { await api('/api/v1/members/me', { method: 'PATCH', body: formObject(form) }); notify('Профиль сохранён.'); }
    catch (error) { notify(error.message, true); }
  });
  document.querySelector('#logout-link').addEventListener('click', async event => {
    event.preventDefault();
    try { await api('/api/v1/auth/logout', { method: 'POST', body: {} }); location.href = '/login'; }
    catch (error) { notify(error.message, true); }
  });
}

function cell(text, tag = 'span') {
  const element = document.createElement(tag);
  element.textContent = text || '—';
  return element;
}

async function loadMembers(query = '') {
  const response = await api(`/api/v1/admin/members${query ? `?${query}` : ''}`);
  const tbody = document.querySelector('#members-list');
  tbody.replaceChildren();
  document.querySelector('#member-count').textContent = `Участников: ${response.length}`;
  if (!response.length) {
    const tr = document.createElement('tr');
    const td = cell('Совпадений не найдено.'); td.colSpan = 4; td.className = 'empty'; tr.append(td); tbody.append(tr); return;
  }
  for (const member of response) {
    const tr = document.createElement('tr');
    const identity = document.createElement('td');
    identity.append(cell(member.full_name, 'strong'), cell(`@${member.username}`, 'small'), cell(member.email || 'Почта не добавлена', 'small'));
    const education = document.createElement('td');
    education.append(cell(member.university_name), cell([member.city_name, member.study_year ? `${member.study_year} курс` : ''].filter(Boolean).join(' · '), 'small'));
    const position = document.createElement('td');
    position.append(cell(member.committee_name), cell(member.position_title || member.activity_area, 'small'));
    const state = document.createElement('td');
    const form = document.createElement('form'); form.className = 'status-form';
    const select = document.createElement('select'); select.setAttribute('aria-label', `Статус ${member.full_name}`);
    for (const value of ['pending', 'active', 'suspended', 'left']) {
      const option = document.createElement('option'); option.value = value; option.textContent = statusLabels[value]; option.selected = value === member.membership_status; select.append(option);
    }
    const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Сохранить';
    form.append(select, save);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      try { await api(`/api/v1/admin/members/${member.id}/membership-status`, { method: 'PATCH', body: { status: select.value } }); notify('Статус обновлён.'); }
      catch (error) { notify(error.message, true); }
    });
    const resetButton = document.createElement('button'); resetButton.type = 'button'; resetButton.className = 'table-action reset-member-password'; resetButton.textContent = 'Сбросить пароль';
    resetButton.addEventListener('click', async () => {
      if (!confirm(`Создать новый временный пароль для @${member.username}? Все активные сессии пользователя будут завершены.`)) return;
      resetButton.disabled = true;
      try {
        const result = await api(`/api/v1/admin/members/${member.id}/password-reset`, { method: 'POST', body: {} });
        document.querySelector('#temporary-password-owner').textContent = `Временный пароль для @${result.username}. Покажи его пользователю один раз и передай безопасным способом.`;
        document.querySelector('#temporary-password-value').value = result.temporary_password;
        document.querySelector('#temporary-password-panel').hidden = false;
        document.querySelector('#temporary-password-panel').scrollIntoView({ behavior: 'smooth', block: 'center' });
      } catch (error) { notify(error.message, true); }
      finally { resetButton.disabled = false; }
    });
    state.append(form, resetButton);
    if (member.password_reset_required) state.append(cell('Ожидается смена временного пароля', 'small'));
    tr.append(identity, education, position, state); tbody.append(tr);
  }
}

async function initAdmin() {
  await getCsrf();
  loadAdminSummary();
  await loadMembers();
  document.querySelector('#admin-view').classList.remove('hidden');
  document.querySelector('#search-form').addEventListener('submit', async event => {
    event.preventDefault();
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(event.currentTarget)) if (value) params.set(key, value);
    try { await loadMembers(params.toString()); }
    catch (error) { notify(error.message, true); }
  });
  const exportLink = document.querySelector('.auth-bottom a[href="/api/v1/admin/members.csv"]');
  exportLink.addEventListener('click', async event => {
    event.preventDefault();
    try {
      const response = await fetch(event.currentTarget.href, { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Экспорт недоступен.');
      const blob = await response.blob(); const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = 'amsa-members.csv'; link.click(); URL.revokeObjectURL(url);
    } catch (error) { notify(error.message, true); }
  });
  document.querySelector('#temporary-password-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(document.querySelector('#temporary-password-value').value); notify('Временный пароль скопирован. Передай его пользователю безопасно.'); }
    catch { notify('Не удалось скопировать пароль. Выдели и скопируй его вручную.', true); }
  });
  document.querySelector('#temporary-password-close').addEventListener('click', () => {
    document.querySelector('#temporary-password-value').value = '';
    document.querySelector('#temporary-password-owner').textContent = '';
    document.querySelector('#temporary-password-panel').hidden = true;
  });
}

async function loadAdminSummary() {
  try {
    const summary = await api('/api/v1/admin/summary');
    const values = [summary.members, summary.active_members, summary.pending_members, summary.upcoming_events, summary.committees, summary.news_needing_attention];
    document.querySelectorAll('#admin-summary strong').forEach((node, i) => { node.textContent = Number(values[i]).toLocaleString('ru-RU'); });
  } catch { document.querySelector('#admin-summary').hidden = true; }
}

async function start() {
  const menu = document.querySelector('.menu');
  const nav = document.querySelector('.navlinks');
  menu.addEventListener('click', () => { const open = nav.classList.toggle('open'); menu.setAttribute('aria-expanded', open); menu.textContent = open ? '×' : '☰'; });
  nav.querySelectorAll('a').forEach(link => link.addEventListener('click', () => { nav.classList.remove('open'); menu.setAttribute('aria-expanded', 'false'); menu.textContent = '☰'; }));
  try {
    if (mode === 'login' || mode === 'register') await initAuth();
    if (mode === 'cabinet') await initCabinet();
    if (mode === 'admin') await initAdmin();
  } catch (error) {
    if (error.message === 'Password change required') location.href = '/change-password';
    else if (mode === 'cabinet' || mode === 'admin') location.href = '/login';
    else notify(error.message, true);
  }
}

document.addEventListener('DOMContentLoaded', start);
