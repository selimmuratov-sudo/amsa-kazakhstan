const message = document.querySelector('#committee-admin-message');
const list = document.querySelector('#committee-admin-list');
const select = document.querySelector('#officer-committee');
let csrf = '';
let rows = [];

function flash(text, bad = false) { message.textContent = text; message.classList.toggle('error', bad); message.classList.add('visible'); }
async function token() { const r = await fetch('/api/v1/auth/csrf', {credentials:'same-origin'}); csrf = (await r.json()).csrf_token; }
async function api(url, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrf;
  if (options.body && typeof options.body !== 'string') { headers['Content-Type']='application/json'; options.body=JSON.stringify(options.body); }
  const r=await fetch(url,{...options,headers,credentials:'same-origin'}); const data=r.status===204?null:(r.headers.get('content-type')||'').includes('json')?await r.json():null;
  if(!r.ok)throw new Error(data?.detail||'Не удалось выполнить запрос.'); return data;
}
function addCell(row, value, small=false) { const td=document.createElement('td'); const el=document.createElement(small?'small':'span'); el.textContent=value||'—'; td.append(el); row.append(td); return td; }
function render() {
  list.replaceChildren(); select.replaceChildren();
  document.querySelector('#committee-admin-count').textContent=`Комитетов: ${rows.length}`;
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.className='empty';td.textContent='Комитеты пока не добавлены.';tr.append(td);list.append(tr);}
  for(const c of rows){
    const tr=document.createElement('tr');const name=document.createElement('td');name.append(Object.assign(document.createElement('strong'),{textContent:c.name}),Object.assign(document.createElement('small'),{textContent:c.public_email||'Публичный email не указан'}));
    const place=document.createElement('td');place.append(Object.assign(document.createElement('span'),{textContent:c.university||'—'}),Object.assign(document.createElement('small'),{textContent:c.city||'—'}));
    addCell(tr,c.member_count);addCell(tr,c.is_public?'Опубликован':'Скрыт');
    const actions=document.createElement('td');const edit=document.createElement('button');edit.type='button';edit.className='table-action';edit.textContent='Изменить';edit.addEventListener('click',()=>fill(c));actions.append(edit);
    tr.append(name,place);tr.insertBefore(name,tr.firstChild);tr.insertBefore(place,name.nextSibling);tr.append(actions);list.append(tr);
    const option=document.createElement('option');option.value=c.id;option.textContent=c.name;select.append(option);
  }
  loadOfficers();
}
function fill(c){const form=document.querySelector('#committee-form');for(const key of ['id','name','university_name','city_name','public_email','public_description'])form.elements.namedItem(key).value=key==='id'?c.id:c[key]||'';form.elements.is_public.checked=c.is_public;document.querySelector('#committee-form-heading').textContent=`Изменить: ${c.name}`;form.scrollIntoView({behavior:'smooth',block:'start'});}
async function loadOfficers(){const target=document.querySelector('#officer-list');if(!select.value){target.innerHTML='<p class="empty">Создай или выбери комитет.</p>';return;}try{const people=await api(`/api/v1/admin/committees/${select.value}/officers`);target.replaceChildren();if(!people.length){target.innerHTML='<p class="empty">Назначений пока нет.</p>';return;}for(const p of people){const row=document.createElement('div');row.className='officer-row';const label=document.createElement('div');label.append(Object.assign(document.createElement('strong'),{textContent:p.full_name||`@${p.username}`}),Object.assign(document.createElement('small'),{textContent:`@${p.username}${p.email?` · ${p.email}`:''} · ${p.role}`}));const remove=document.createElement('button');remove.type='button';remove.className='table-action danger-action';remove.textContent='Снять роль';remove.addEventListener('click',async()=>{try{await api(`/api/v1/admin/committees/${select.value}/officers/${p.user_id}/${p.role}`,{method:'DELETE'});flash('Роль снята.');loadOfficers();}catch(e){flash(e.message,true);}});row.append(label,remove);target.append(row);}}catch(e){target.textContent=e.message;}}
async function load(){await token();rows=await api('/api/v1/admin/committees');render();}
document.querySelector('#committee-form').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const body={name:form.elements.name.value,university_name:form.elements.university_name.value,city_name:form.elements.city_name.value,public_email:form.elements.public_email.value||null,public_description:form.elements.public_description.value||null,is_public:form.elements.is_public.checked};const id=form.elements.id.value;try{await api(id?`/api/v1/admin/committees/${id}`:'/api/v1/admin/committees',{method:id?'PATCH':'POST',body});flash(id?'Комитет обновлён.':'Комитет добавлен.');form.reset();form.elements.id.value='';document.querySelector('#committee-form-heading').textContent='Добавить комитет';await load();}catch(err){flash(err.message,true);}});
document.querySelector('#committee-reset').addEventListener('click',()=>{const f=document.querySelector('#committee-form');f.reset();f.elements.id.value='';document.querySelector('#committee-form-heading').textContent='Добавить комитет';});
document.querySelector('#officer-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;try{await api(`/api/v1/admin/committees/${f.elements.committee_id.value}/officers`,{method:'POST',body:{username:f.elements.username.value,role_code:f.elements.role_code.value}});flash('Роль назначена.');f.elements.username.value='';await loadOfficers();}catch(err){flash(err.message,true);}});
select.addEventListener('change',loadOfficers);
document.querySelector('.menu').addEventListener('click',()=>{const nav=document.querySelector('.navlinks');const open=nav.classList.toggle('open');document.querySelector('.menu').setAttribute('aria-expanded',open);});
load().catch(e=>{flash(e.message,true);if(![...rows].length)list.textContent=e.message;});
