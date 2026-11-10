const escHome = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const homeDate = value => new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short'}).format(new Date(value));
async function loadHomeFeed() {
  const [newsResponse,eventResponse,statsResponse] = await Promise.allSettled([
    fetch('/api/v1/news'), fetch('/api/v1/events'), fetch('/api/v1/public/summary')
  ]);
  let stories=[];
  if(newsResponse.status==='fulfilled'&&newsResponse.value.ok) stories=await newsResponse.value.json();
  const ticker=document.querySelector('#news-ticker'),tickerTrack=document.querySelector('#news-ticker-track');
  if(stories.length){ticker.hidden=false;tickerTrack.innerHTML=[...stories.slice(0,5),...stories.slice(0,5)].map(n=>`<a href="/news/${encodeURIComponent(n.slug)}">${escHome(n.category)} · ${escHome(n.title)} <span>↗</span></a>`).join('');}
  renderFeaturedNews(stories);
  const newsHost=document.querySelector('#home-news');
  if(stories.length) newsHost.innerHTML=stories.slice(0,3).map(n=>`<a class="home-feed-item" href="/news/${encodeURIComponent(n.slug)}"><time>${homeDate(n.published_at)}</time><strong>${escHome(n.title)}</strong><span>${escHome(n.summary)}</span></a>`).join('');
  else newsHost.innerHTML='<p class="home-feed-note">Новые публикации появятся здесь после выхода от редакции AMSA.</p>';
  let events=[];
  if(eventResponse.status==='fulfilled'&&eventResponse.value.ok)events=await eventResponse.value.json();
  events=events.filter(e=>new Date(e.starts_at)>=new Date()).slice(0,3);
  const eventHost=document.querySelector('#home-events');
  if(events.length)eventHost.innerHTML=events.map(e=>`<a class="home-feed-item" href="/events.html"><time>${homeDate(e.starts_at)} · ${escHome(e.location)}</time><strong>${escHome(e.title)}</strong><span>${escHome(e.summary||e.category)}</span></a>`).join('');
  else eventHost.innerHTML='<p class="home-feed-note">Ближайшие события появятся здесь после публикации в календаре.</p>';
  if(statsResponse.status==='fulfilled'&&statsResponse.value.ok){const s=await statsResponse.value.json();document.querySelector('#home-stats').innerHTML=[[s.active_members,'активных участников'],[s.public_committees,'локальных комитетов'],[s.universities,'университетов'],[s.published_events,'опубликованных мероприятий']].map(([n,label])=>`<div><strong>${Number(n).toLocaleString('ru-RU')}</strong><span>${label}</span></div>`).join('');}
}
function renderFeaturedNews(stories){
  const panel=document.querySelector('#featured-carousel');if(!stories.length)return;panel.hidden=false;stories=stories.slice(0,6);
  const dots=document.querySelector('#carousel-dots');let current=0,timer;
  dots.innerHTML=stories.slice(0,6).map((_,i)=>`<button type="button" aria-label="Новость ${i+1}" aria-current="${i===0?'true':'false'}"></button>`).join('');
  function show(index){current=(index+stories.length)%stories.length;const item=stories[current],image=document.querySelector('#carousel-image');image.style.backgroundImage=item.cover_image?`linear-gradient(90deg,#10192b22,#10192b22),url("${item.cover_image.replace(/["\\]/g,'')}")`:'';image.classList.toggle('no-image',!item.cover_image);document.querySelector('#carousel-category').textContent=item.category;document.querySelector('#carousel-position').textContent=`${String(current+1).padStart(2,'0')} / ${String(stories.length).padStart(2,'0')}`;document.querySelector('#carousel-title').textContent=item.title;document.querySelector('#carousel-summary').textContent=item.summary;const link=document.querySelector('#carousel-link');link.href=`/news/${encodeURIComponent(item.slug)}`;dots.querySelectorAll('button').forEach((b,i)=>b.setAttribute('aria-current',String(i===current)));}
  function restart(){clearInterval(timer);if(stories.length>1&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches)timer=setInterval(()=>show(current+1),6500)}
  document.querySelector('#carousel-prev').onclick=()=>{show(current-1);restart()};document.querySelector('#carousel-next').onclick=()=>{show(current+1);restart()};dots.querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{show(i);restart()});panel.addEventListener('mouseenter',()=>clearInterval(timer));panel.addEventListener('mouseleave',restart);panel.addEventListener('focusin',()=>clearInterval(timer));panel.addEventListener('focusout',restart);show(0);restart();
}
loadHomeFeed().catch(()=>{});
