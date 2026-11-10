from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Event, LocalCommittee, NewsArticle, utcnow

router = APIRouter(tags=["search"])

PUBLIC_PAGES = [
    ("Главная", "/", "AMSA Kazakhstan — сообщество студентов-медиков"),
    ("О сообществе", "/about.html", "Asian Medical Students Association, AMSA Kazakhstan"),
    ("Возможности", "/opportunities.html", "AMSEP, обмены и международные конференции"),
    ("Наши ценности", "/values.html", "Knowledge, Action, Friendship"),
    ("Контакты", "/contacts.html", "Связаться с AMSA Kazakhstan"),
    ("Новости", "/news.html", "Новости и объявления AMSA Kazakhstan"),
]


@router.get("/api/v1/search")
def search_public(q: str = Query(min_length=2, max_length=100), db: Session = Depends(get_db)) -> dict:
    term = q.strip()
    if len(term) < 2:
        return {"query": term, "results": []}
    results = [{"title": title, "url": url, "snippet": snippet, "type": "Страница"}
               for title, url, snippet in PUBLIC_PAGES if term.casefold() in f"{title} {snippet}".casefold()]
    folded = term.casefold()
    committees = db.scalars(select(LocalCommittee).options(joinedload(LocalCommittee.university), joinedload(LocalCommittee.city))
        .where(LocalCommittee.is_public.is_(True)).order_by(LocalCommittee.name).limit(500)).unique()
    for c in committees:
        searchable = " ".join(x for x in [c.name, c.public_description, c.university.name if c.university else None, c.city.name if c.city else None] if x).casefold()
        if folded in searchable:
            results.append({"title": c.name, "url": "/local-committees.html", "snippet": " · ".join(x for x in [c.university.name if c.university else None, c.city.name if c.city else None, c.public_description] if x), "type": "Комитет"})
    events = db.scalars(select(Event).where(Event.status == "published").order_by(Event.starts_at).limit(500))
    for e in events:
        if folded in f"{e.title} {e.summary} {e.description} {e.category} {e.location}".casefold():
            results.append({"title": e.title, "url": "/events.html", "snippet": e.summary or e.description[:180], "type": "Мероприятие"})
    articles = db.scalars(select(NewsArticle).where(
        or_(NewsArticle.status == "published", (NewsArticle.status == "scheduled") & (NewsArticle.published_at.is_not(None)) & (NewsArticle.published_at <= utcnow()))
    ).order_by(NewsArticle.is_pinned.desc(), NewsArticle.published_at.desc()).limit(500))
    for article in articles:
        if folded in f"{article.title} {article.summary} {article.body} {article.category}".casefold():
            results.append({"title": article.title, "url": f"/news/{article.slug}", "snippet": article.summary, "type": "Новость"})
    return {"query": term, "results": results[:40]}
