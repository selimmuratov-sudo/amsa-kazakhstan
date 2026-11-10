const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let exchangeCsrf = '';
async function exchangeApi(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = exchangeCsrf;
  if (options.body && typeof options.body !== 'string') { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body); }
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(data?.detail || 'Не удалось выполнить запрос.');
  return data;
}
function formatDeadline(value) { return new Date(value).toLocaleDateString('ru-RU', { day:'numeric', month:'long', year:'numeric' }); }
async function initExchanges() {
  const list = document.querySelector('#exchange-call-list');
  const dialog = document.querySelector('#exchange-dialog');
  if (!list || !dialog) return;
  const tokenResponse = await fetch('/api/v1/auth/csrf', { credentials:'same-origin' });
  exchangeCsrf = (await tokenResponse.json()).csrf_token;
  let signedIn = false;
  try { await exchangeApi('/api/v1/members/me'); signedIn = true; } catch {}
  try {
    const calls = await exchangeApi('/api/v1/exchanges');
    if (!calls.length) { list.innerHTML = '<p class="empty">Сейчас нет открытых наборов. Следи за новостями AMSA Kazakhstan.</p>'; return; }
    list.innerHTML = calls.map(call => `<article class="exchange-call-card"><div class="exchange-call-top"><span>${escapeHtml(call.program_type)}</span><span>до ${formatDeadline(call.closes_at)}</span></div><h3>${escapeHtml(call.title)}</h3><p class="exchange-destination">${escapeHtml(call.destination)}</p><p>${escapeHtml(call.summary)}</p><div class="exchange-call-meta">${call.seats ? `Мест: ${call.seats}` : 'Количество мест уточняется'} · Активный набор</div><button class="btn exchange-apply" type="button" data-call="${call.id}" data-title="${escapeHtml(call.title)}" data-program="${escapeHtml(call.program_type)}">${signedIn ? 'Подать заявку' : 'Войти и подать заявку'} ↗</button><details><summary>Описание и требования</summary><p>${escapeHtml(call.description || 'Подробности программы уточняются.')}</p><h4>Требования</h4><p>${escapeHtml(call.requirements || 'Проверь требования у координатора обмена.')}</p></details></article>`).join('');
    list.querySelectorAll('.exchange-apply').forEach(button => button.addEventListener('click', () => {
      if (!signedIn) { location.href = `/login?next=${encodeURIComponent('/opportunities.html#exchange-calls')}`; return; }
      document.querySelector('#exchange-form-title').textContent = button.dataset.title;
      document.querySelector('#exchange-form-program').textContent = button.dataset.program;
      dialog.querySelector('[name="call_id"]').value = button.dataset.call;
      dialog.showModal();
    }));
  } catch (error) { list.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`; }
  dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.querySelector('form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    const callId = body.call_id; delete body.call_id;
    try {
      await exchangeApi(`/api/v1/exchanges/${callId}/applications`, { method:'POST', body });
      const note = document.querySelector('#exchange-form-message'); note.textContent = 'Заявка отправлена. Проверяй её статус в личном кабинете.'; note.classList.add('visible');
      setTimeout(() => { dialog.close(); form.reset(); note.classList.remove('visible'); }, 1600);
    } catch (error) {
      const note = document.querySelector('#exchange-form-message'); note.textContent = error.message; note.classList.add('visible','error');
    }
  });
}
document.addEventListener('DOMContentLoaded', initExchanges);
