const newsList = document.querySelector('#news-list');
const newsQuery = document.querySelector('#news-query');
const escapeNews = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let publications = [];
function dateNews(value) { return value ? new Intl.DateTimeFormat('ru-RU',{dateStyle:'long'}).format(new Date(value)) : ''; }
function drawNews() {
  const q = newsQuery.value.trim().toLocaleLowerCase('ru-RU');
  const items = publications.filter(n => !q || `${n.title} ${n.summary} ${n.category}`.toLocaleLowerCase('ru-RU').includes(q));
  if (!items.length) { newsList.innerHTML = `<div class="committee-state">${q?'По этому запросу новостей нет. Попробуй другое слово.':'Пока нет опубликованных новостей. Загляни позже.'}</div>`; return; }
  newsList.innerHTML = items.map(n => `<article class="news-card">${n.cover_image?`<a href="/news/${encodeURIComponent(n.slug)}"><img src="${escapeNews(n.cover_image)}" alt="" loading="lazy"></a>`:''}<div class="news-card-content"><div class="article-meta"><span>${escapeNews(n.category)}</span><time>${dateNews(n.published_at)}</time>${n.is_pinned?'<span>Закреплено</span>':''}</div><h3><a href="/news/${encodeURIComponent(n.slug)}">${escapeNews(n.title)}</a></h3><p>${escapeNews(n.summary)}</p><a class="link-arrow" href="/news/${encodeURIComponent(n.slug)}">Читать статью →</a></div></article>`).join('');
}
async function loadNews() {
  try { const r=await fetch('/api/v1/news');if(!r.ok)throw new Error();publications=await r.json();drawNews(); }
  catch { newsList.innerHTML='<div class="committee-state">Не удалось загрузить публикации. Обнови страницу чуть позже.</div>'; }
}
newsQuery.addEventListener('input',drawNews);
loadNews();
