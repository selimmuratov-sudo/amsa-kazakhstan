const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let exchangeAdminCsrf = '';
let exchangeCalls = [];
async function exchangeAdminApi(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = exchangeAdminCsrf;
  if (options.body && typeof options.body !== 'string') { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body); }
  const response = await fetch(path, { ...options, headers, credentials:'same-origin' });
  const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(data?.detail || 'Не удалось выполнить запрос.');
  return data;
}
const adminNotice = (message, error = false) => { const el = document.querySelector('#exchange-admin-message'); el.textContent = message; el.classList.toggle('error', error); el.classList.add('visible'); };
const asLocalInput = value => { if (!value) return ''; const date = new Date(value); date.setMinutes(date.getMinutes() - date.getTimezoneOffset()); return date.toISOString().slice(0,16); };
const exchangeStatusLabels = { submitted:'Получена', under_review:'На рассмотрении', shortlisted:'Шорт-лист', accepted:'Принята', rejected:'Отклонена', withdrawn:'Отозвана' };
async function loadExchangeCalls() {
  exchangeCalls = await exchangeAdminApi('/api/v1/admin/exchanges');
  const host = document.querySelector('#exchange-admin-call-list');
  document.querySelector('#exchange-admin-call-count').textContent = `Наборов: ${exchangeCalls.length}`;
  host.innerHTML = exchangeCalls.length ? exchangeCalls.map(call => `<article class="exchange-admin-call"><div><span class="status-badge">${esc(call.status)}</span><h3>${esc(call.title)}</h3><p>${esc(call.program_type)} · ${esc(call.destination)}</p><small>До ${new Date(call.closes_at).toLocaleDateString('ru-RU')} · заявок: ${call.applications_count}</small></div><button type="button" class="table-action edit-exchange" data-id="${call.id}">Редактировать</button></article>`).join('') : '<p class="empty">Создай первый набор — после публикации он появится на странице возможностей.</p>';
  const selector = document.querySelector('#exchange-filter-call');
  const current = selector.value;
  selector.innerHTML = '<option value="">Все наборы</option>' + exchangeCalls.map(call => `<option value="${call.id}">${esc(call.title)}</option>`).join(''); selector.value = current;
  host.querySelectorAll('.edit-exchange').forEach(button => button.addEventListener('click', () => {
    const call = exchangeCalls.find(item => item.id === Number(button.dataset.id)); const form = document.querySelector('#exchange-call-form');
    for (const [key, value] of Object.entries(call)) { const field = form.elements.namedItem(key); if (field) field.value = key.endsWith('_at') ? asLocalInput(value) : value ?? ''; }
    document.querySelector('#exchange-call-form-title').textContent = 'Редактировать набор'; form.scrollIntoView({ behavior:'smooth', block:'start' });
  }));
}
function allowedTransitions(status) { return ({ submitted:['under_review','shortlisted','accepted','rejected'], under_review:['shortlisted','accepted','rejected'], shortlisted:['under_review','accepted','rejected'] })[status] || []; }
async function loadExchangeApplications() {
  const params = new URLSearchParams(); const callId = document.querySelector('#exchange-filter-call').value; const status = document.querySelector('#exchange-filter-status').value;
  if (callId) params.set('call_id', callId); if (status) params.set('status_filter', status);
  const rows = await exchangeAdminApi(`/api/v1/admin/exchanges/applications${params.size ? `?${params}` : ''}`);
  const host = document.querySelector('#exchange-applications-list');
  if (!rows.length) { host.innerHTML = '<p class="empty">Заявок с такими фильтрами пока нет.</p>'; return; }
  host.innerHTML = rows.map(row => `<article class="exchange-application-card"><div class="exchange-application-head"><div><span class="status-badge">${esc(exchangeStatusLabels[row.status] || row.status)}</span><h3>${esc(row.candidate_name)} <small>@${esc(row.candidate_username)}</small></h3><p>${esc(row.university_name)} · ${esc(row.call_title)} · ${esc(row.destination)}</p></div><time>${new Date(row.submitted_at).toLocaleDateString('ru-RU')}</time></div><details><summary>Открыть мотивацию и опыт</summary><h4>Мотивация</h4><p class="application-copy">${esc(row.motivation)}</p><h4>Опыт</h4><p class="application-copy">${esc(row.relevant_experience || 'Не указан')}</p><p><strong>Предпочтение:</strong> ${esc(row.preferred_destination || 'Не указано')}</p></details>${allowedTransitions(row.status).length ? `<form class="review-form" data-id="${row.id}"><label class="field">Сообщение кандидату<textarea name="reviewer_note" rows="2" maxlength="5000" placeholder="Комментарий, видимый кандидату в личном кабинете"></textarea></label><div class="review-controls"><select name="status" aria-label="Решение по заявке">${allowedTransitions(row.status).map(value => `<option value="${value}">${esc(exchangeStatusLabels[value])}</option>`).join('')}</select><button class="table-action" type="submit">Сохранить решение</button></div></form>` : `<p class="reviewer-note"><strong>Комментарий кандидату:</strong> ${esc(row.reviewer_note || '—')}</p>`}</article>`).join('');
  host.querySelectorAll('.review-form').forEach(form => form.addEventListener('submit', async event => { event.preventDefault(); const data = Object.fromEntries(new FormData(form)); try { await exchangeAdminApi(`/api/v1/admin/exchanges/applications/${form.dataset.id}`, { method:'PATCH', body:data }); adminNotice('Решение сохранено, изменение записано в историю.'); await loadExchangeApplications(); } catch (error) { adminNotice(error.message, true); } }));
}
async function loadExchangeReviewers() {
  const host = document.querySelector('#reviewer-list');
  try {
    const reviewers = await exchangeAdminApi('/api/v1/admin/exchange-reviewers');
    host.innerHTML = reviewers.length ? reviewers.map(item => `<div class="officer-row"><div><strong>${esc(item.full_name)}</strong><small>@${esc(item.username)}</small></div><button type="button" class="table-action danger-action" data-remove-reviewer="${item.user_id}">Убрать доступ</button></div>`).join('') : '<p class="empty">Ревьюеры ещё не назначены.</p>';
    host.querySelectorAll('[data-remove-reviewer]').forEach(button => button.addEventListener('click', async () => { try { await exchangeAdminApi(`/api/v1/admin/exchange-reviewers/${button.dataset.removeReviewer}`, {method:'DELETE'}); adminNotice('Доступ ревьюера снят.'); await loadExchangeReviewers(); } catch (error) { adminNotice(error.message, true); } }));
  } catch (error) { document.querySelector('#reviewer-management').hidden = true; }
}
async function initExchangeAdmin() {
  const csrf = await fetch('/api/v1/auth/csrf', { credentials:'same-origin' }); exchangeAdminCsrf = (await csrf.json()).csrf_token;
  try { await loadExchangeCalls(); await loadExchangeApplications(); await loadExchangeReviewers(); }
  catch (error) { if (error.message.includes('role') || error.message.includes('authenticated') || error.message.includes('session')) { location.href='/login'; return; } adminNotice(error.message, true); }
  const form = document.querySelector('#exchange-call-form');
  form.addEventListener('submit', async event => { event.preventDefault(); const data = Object.fromEntries(new FormData(form)); const id = data.id; delete data.id; data.opens_at = data.opens_at ? new Date(data.opens_at).toISOString() : null; data.closes_at = new Date(data.closes_at).toISOString(); data.seats = data.seats ? Number(data.seats) : null; try { await exchangeAdminApi(id ? `/api/v1/admin/exchanges/${id}` : '/api/v1/admin/exchanges', { method:id ? 'PATCH':'POST', body:data }); form.reset(); form.elements.namedItem('program_type').value='AMSEP'; document.querySelector('#exchange-call-form-title').textContent='Создать набор'; adminNotice('Набор сохранён.'); await loadExchangeCalls(); await loadExchangeApplications(); } catch (error) { adminNotice(error.message, true); } });
  document.querySelector('#exchange-call-reset').addEventListener('click', () => { form.reset(); form.elements.namedItem('program_type').value='AMSEP'; document.querySelector('#exchange-call-form-title').textContent='Создать набор'; });
  document.querySelector('#exchange-filter-call').addEventListener('change', loadExchangeApplications); document.querySelector('#exchange-filter-status').addEventListener('change', loadExchangeApplications);
  document.querySelector('#reviewer-form').addEventListener('submit', async event => { event.preventDefault(); const username = new FormData(event.currentTarget).get('username').trim(); try { await exchangeAdminApi('/api/v1/admin/exchange-reviewers', {method:'POST',body:{username}}); event.currentTarget.reset(); adminNotice('Ревьюер назначен. Доступ записан в аудит.'); await loadExchangeReviewers(); } catch (error) { adminNotice(error.message, true); } });
}
document.addEventListener('DOMContentLoaded', initExchangeAdmin);
