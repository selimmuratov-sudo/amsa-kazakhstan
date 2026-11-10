const list = document.querySelector('#committee-list');
const query = document.querySelector('#committee-query');
let committees = [];

function renderCommittees() {
  const term = query.value.trim().toLocaleLowerCase('ru');
  const visible = committees.filter(item => [item.name, item.university, item.city].filter(Boolean).join(' ').toLocaleLowerCase('ru').includes(term));
  list.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement('div'); empty.className = 'committee-state';
    empty.textContent = committees.length ? 'По этому запросу комитетов не найдено.' : 'Подтверждённые локальные комитеты пока не опубликованы. Свяжись с национальной командой, чтобы узнать о запуске комитета в своём университете.';
    list.append(empty); return;
  }
  for (const item of visible) {
    const card = document.createElement('article'); card.className = 'committee-card';
    const index = document.createElement('span'); index.className = 'committee-index'; index.textContent = `LC / ${String(item.id).padStart(2, '0')}`;
    const mark = document.createElement('div'); mark.className = 'committee-mark'; mark.textContent = 'AM';
    const title = document.createElement('h3'); title.textContent = item.name;
    const university = document.createElement('p'); university.className = 'committee-university'; university.textContent = item.university || 'Университет уточняется';
    const city = document.createElement('p'); city.className = 'committee-city'; city.textContent = item.city || '';
    card.append(index, mark, title, university, city);
    if (item.description) { const desc = document.createElement('p'); desc.className = 'committee-description'; desc.textContent = item.description; card.append(desc); }
    if (item.public_email) { const email = document.createElement('a'); email.className = 'link-arrow'; email.href = `mailto:${encodeURIComponent(item.public_email)}`; email.textContent = 'Связаться '; const arrow = document.createElement('span'); arrow.textContent = '↗'; email.append(arrow); card.append(email); }
    list.append(card);
  }
}

async function loadCommittees() {
  try {
    const response = await fetch('/api/v1/committees');
    if (!response.ok) throw new Error('Не удалось загрузить каталог. Попробуй обновить страницу.');
    committees = await response.json(); renderCommittees();
  } catch (error) { list.textContent = error.message; }
}
query.addEventListener('input', renderCommittees);
loadCommittees();
