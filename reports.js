let reportCsrf = '';
let reportRows = [];
let reportCanWrite = false;
let reportCanReview = false;
let googleSheetsEnabled = false;
let googleSheetRuns = new Map();
const reportEsc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const reportLabels = {draft:'Черновик',submitted:'Отправлен',under_review:'На проверке',changes_requested:'Нужны правки',approved:'Утверждён'};
function reportNotice(text, error=false) { const el=document.querySelector('#report-message'); el.textContent=text; el.classList.toggle('error',error); el.classList.add('visible'); }
async function reportApi(path, options={}) {
  const headers={...(options.headers||{})};
  if(options.method&&options.method!=='GET') headers['X-CSRF-Token']=reportCsrf;
  if(options.body&&typeof options.body!=='string'){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body)}
  const response=await fetch(path,{...options,headers,credentials:'same-origin'});
  const data=response.headers.get('content-type')?.includes('application/json')?await response.json():null;
  if(!response.ok) throw new Error(data?.detail||'Не удалось выполнить запрос.');
  return data;
}
function reportFormData(form) {
  const data=Object.fromEntries(new FormData(form));
  data.committee_id=Number(data.committee_id);
  for(const key of ['members_count','events_count']) data[key]=data[key]===''?null:Number(data[key]);
  return data;
}
async function loadGoogleSheetStatus(){
  const data=await reportApi('/api/v1/admin/reports/google-sheets/status');
  googleSheetsEnabled=data.enabled;
  googleSheetRuns=new Map();
  for(const run of data.runs) if(!googleSheetRuns.has(run.report_id)) googleSheetRuns.set(run.report_id,run);
  const panel=document.querySelector('#google-sheets-panel'),note=document.querySelector('#google-sheets-status');
  panel.hidden=false;
  note.textContent=data.enabled?`Синхронизация включена. Утверждённые отчёты отправляются на вкладку «${data.sheet_name}». Если отправка завершится ошибкой, её можно повторить из карточки отчёта.`:'Синхронизация не настроена. Администратору нужно задать ID таблицы, путь к JSON сервисного аккаунта и название вкладки в переменных AMSA_GOOGLE_REPORTS_*.';
}
function renderReports() {
  const host=document.querySelector('#report-list');
  document.querySelector('#report-count').textContent=`Отчётов: ${reportRows.length}`;
  if(!reportRows.length){host.innerHTML='<p class="empty">Отчётов пока нет. Создай первый квартальный отчёт или дождись подачи от комитетов.</p>';return;}
  host.innerHTML=reportRows.map(report=>{
    const edit=reportCanWrite&&['draft','changes_requested'].includes(report.status)?`<button class="table-action edit-report" data-id="${report.id}">Редактировать черновик</button><button class="table-action submit-report" data-id="${report.id}">Отправить на проверку</button>`:'';
    const review=reportCanReview&&['submitted','under_review'].includes(report.status)?`<form class="report-review-form" data-id="${report.id}"><label class="field">Комментарий комитета<textarea name="comment" rows="2" required minlength="3" maxlength="5000" placeholder="Обратная связь, видимая комитету"></textarea></label><div class="review-controls"><select name="status"><option value="under_review">Взять на проверку</option><option value="changes_requested">Вернуть на доработку</option><option value="approved">Утвердить</option></select><button class="table-action" type="submit">Сохранить решение</button></div></form>`:'';
    const syncRun=googleSheetRuns.get(report.id);
    const syncLabels={queued:'В очереди',succeeded:'Синхронизирован',failed:'Ошибка синхронизации',not_configured:'Не настроено'};
    const staleQueue=syncRun?.status==='queued'&&Date.now()-Date.parse(syncRun.created_at)>=120000;
    const sync=reportCanReview&&report.status==='approved'?`<div class="google-sync-row"><span class="field-hint">Google Sheets: ${staleQueue?'Попытка зависла в очереди':syncRun?syncLabels[syncRun.status]||reportEsc(syncRun.status):'Ещё не отправлен'}${syncRun?.error_code?` · ${reportEsc(syncRun.error_code)}`:''}${syncRun?.sheet_range?` · ${reportEsc(syncRun.sheet_range)}`:''}</span><button class="table-action sync-report" data-id="${report.id}" type="button" ${!googleSheetsEnabled||syncRun?.status==='queued'&&!staleQueue?'disabled':''}>${syncRun?.status==='succeeded'?'Синхронизировать заново':'Синхронизировать'}</button></div>`:'';
    return `<article class="report-card"><div class="report-card-head"><div><span class="status-badge report-status-${report.status}">${reportLabels[report.status]||report.status}</span><h3>${reportEsc(report.committee_name)}</h3><p>${reportEsc(report.period)} · ${report.members_count??'—'} участников · ${report.events_count??'—'} мероприятий</p></div><time>${new Date(report.updated_at).toLocaleDateString('ru-RU')}</time></div><details class="report-details" data-id="${report.id}"><summary>Содержание, комментарии и история</summary><div class="report-content"><h4>Резюме</h4><p>${reportEsc(report.executive_summary)}</p><h4>Активности</h4><p>${reportEsc(report.activities)}</p><h4>Результаты</h4><p>${reportEsc(report.impact||'Не заполнено')}</p><h4>Сложности</h4><p>${reportEsc(report.challenges||'Не заполнено')}</p><h4>Дальнейшие шаги</h4><p>${reportEsc(report.next_steps||'Не заполнено')}</p></div><div class="report-comments" data-loaded="false"><h4>Комментарии</h4><div class="report-comment-list">Открой, чтобы загрузить переписку…</div><form class="report-comment-form" data-id="${report.id}"><label class="field">Добавить комментарий<textarea name="body" rows="2" minlength="3" maxlength="5000" required></textarea></label><button type="submit" class="table-action">Отправить комментарий</button></form><h4>История статусов</h4><div class="report-history">Загрузка…</div></div></details><div class="report-actions">${edit}${review}${sync}</div></article>`;
  }).join('');
  host.querySelectorAll('.edit-report').forEach(button=>button.addEventListener('click',()=>fillReport(reportRows.find(row=>row.id===Number(button.dataset.id)))));
  host.querySelectorAll('.submit-report').forEach(button=>button.addEventListener('click',async()=>{try{await reportApi(`/api/v1/reports/${button.dataset.id}/submit`,{method:'POST',body:{}});reportNotice('Отчёт отправлен национальной команде.');await loadReports()}catch(error){reportNotice(error.message,true)}}));
  host.querySelectorAll('.report-review-form').forEach(form=>form.addEventListener('submit',async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(form));try{await reportApi(`/api/v1/admin/reports/${form.dataset.id}`,{method:'PATCH',body});reportNotice('Решение и комментарий сохранены.');await loadReports()}catch(error){reportNotice(error.message,true)}}));
  host.querySelectorAll('.sync-report').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{const result=await reportApi(`/api/v1/admin/reports/${button.dataset.id}/sync`,{method:'POST',body:{}});reportNotice(result.status==='not_configured'?'Google Sheets ещё не настроен.':'Синхронизация запущена.');await loadReports()}catch(error){reportNotice(error.message,true);button.disabled=false}}));
  host.querySelectorAll('.report-details').forEach(details=>details.addEventListener('toggle',async()=>{if(!details.open||details.dataset.loaded==='true')return;const id=details.dataset.id;const box=details.querySelector('.report-comment-list'),historyBox=details.querySelector('.report-history');try{const [comments,history]=await Promise.all([reportApi(`/api/v1/reports/${id}/comments`),reportApi(`/api/v1/reports/${id}/history`)]);box.innerHTML=comments.length?comments.map(item=>`<p class="report-comment"><strong>${reportEsc(item.author_name)}</strong><time>${new Date(item.created_at).toLocaleString('ru-RU')}</time><br>${reportEsc(item.body)}</p>`).join(''):'Комментариев пока нет.';historyBox.innerHTML=history.length?history.map(item=>`<p>${reportLabels[item.to_status]||reportEsc(item.to_status)} · ${new Date(item.created_at).toLocaleString('ru-RU')}${item.note?`<br><span>${reportEsc(item.note)}</span>`:''}</p>`).join(''):'История пуста.';details.dataset.loaded='true'}catch(error){box.textContent=error.message;historyBox.textContent=error.message}}));
  host.querySelectorAll('.report-comment-form').forEach(form=>form.addEventListener('submit',async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(form));try{await reportApi(`/api/v1/reports/${form.dataset.id}/comments`,{method:'POST',body});form.reset();const details=form.closest('.report-details');details.dataset.loaded='false';details.dispatchEvent(new Event('toggle'));reportNotice('Комментарий добавлен.')}catch(error){reportNotice(error.message,true)}}));
}
function fillReport(report){const form=document.querySelector('#report-form');for(const [key,value] of Object.entries(report)){const field=form.elements.namedItem(key);if(field)field.value=value??''}document.querySelector('#report-form-title').textContent=`Редактировать ${report.period}`;form.scrollIntoView({behavior:'smooth',block:'start'})}
async function loadReports(){const status=document.querySelector('#report-status-filter').value;const params=new URLSearchParams();if(status)params.set('status_filter',status);reportRows=await reportApi(reportCanReview?`/api/v1/admin/reports${params.size?`?${params}`:''}`:'/api/v1/reports');if(reportCanReview)await loadGoogleSheetStatus();renderReports()}
async function initReports(){
  const csrf=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});reportCsrf=(await csrf.json()).csrf_token;
  const profile=await reportApi('/api/v1/members/me');reportCanReview=profile.roles.some(role=>['super_admin','national_board'].includes(role));
  try{const committees=await reportApi('/api/v1/reports/committees');if(committees.length){reportCanWrite=true;const select=document.querySelector('#report-committee');select.innerHTML=committees.map(item=>`<option value="${item.id}">${reportEsc(item.name)}</option>`).join('');document.querySelector('#report-editor-panel').hidden=false}}catch{}
  if(reportCanReview){document.querySelector('#report-list-eyebrow').textContent='НАЦИОНАЛЬНАЯ ПРОВЕРКА';document.querySelector('#report-status-filter').hidden=false;document.querySelector('#report-status-filter').addEventListener('change',()=>loadReports().catch(error=>reportNotice(error.message,true)))}
  await loadReports();
  const form=document.querySelector('#report-form');
  form.addEventListener('submit',async event=>{event.preventDefault();const data=reportFormData(form);const id=data.id;delete data.id;try{await reportApi(id?`/api/v1/reports/${id}`:'/api/v1/reports',{method:id?'PATCH':'POST',body:data});form.reset();document.querySelector('#report-form-title').textContent='Создать квартальный отчёт';reportNotice('Черновик сохранён. Перед отправкой его можно открыть и отредактировать.');await loadReports()}catch(error){reportNotice(error.message,true)}});
  document.querySelector('#report-reset').addEventListener('click',()=>{form.reset();form.elements.namedItem('id').value='';document.querySelector('#report-form-title').textContent='Создать квартальный отчёт'});
}
document.addEventListener('DOMContentLoaded',()=>initReports().catch(error=>{reportNotice(error.message,true);if(error.message.includes('Authentication'))location.href='/login'}));
