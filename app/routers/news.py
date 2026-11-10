import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditEvent, Event, LocalCommittee, MemberProfile, NewsArticle, Role, User, UserRoleAssignment, utcnow
from app.rbac import committee_scope, require_member_admin
from app.schemas import NewsAdminOut, NewsOut, NewsWrite
from app.security import current_user, require_csrf

router = APIRouter(tags=["news"])
MEDIA_DIR = Path("data/news_media")
LIVE_STATUSES = {"published", "scheduled"}
TRANSLITERATION = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})


def make_slug(title: str) -> str:
    normalized = title.lower().translate(TRANSLITERATION)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:200].strip("-")
    return slug or f"news-{secrets.token_hex(5)}"


def is_live_query():
    now = utcnow()
    return or_(NewsArticle.status == "published",
               (NewsArticle.status == "scheduled") & (NewsArticle.published_at.is_not(None)) & (NewsArticle.published_at <= now))


def as_public(article: NewsArticle) -> NewsOut:
    return NewsOut(id=article.id, slug=article.slug, title=article.title, summary=article.summary,
                   body=article.body, category=article.category, cover_image=article.cover_image,
                   published_at=article.published_at, is_pinned=article.is_pinned, committee_id=article.committee_id)


def as_admin(article: NewsArticle) -> NewsAdminOut:
    return NewsAdminOut(**as_public(article).model_dump(), status=article.status,
                        created_at=article.created_at, updated_at=article.updated_at)


def prepare_content(db: Session, payload: NewsWrite, *, scope: list[int] | None, current: NewsArticle | None = None) -> dict:
    data = payload.model_dump()
    slug = (payload.slug or "").strip() or (current.slug if current else "") or make_slug(payload.title)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(status_code=422, detail="Slug must use lowercase Latin letters, numbers and hyphens")
    duplicate = db.scalar(select(NewsArticle.id).where(NewsArticle.slug == slug, NewsArticle.id != (current.id if current else -1)))
    if duplicate:
        raise HTTPException(status_code=409, detail="A publication with this URL already exists")
    if payload.status == "scheduled" and not payload.published_at:
        raise HTTPException(status_code=422, detail="Choose a scheduled publication time")
    if payload.cover_image and not re.fullmatch(r"/media/news/[a-f0-9]{32}\.(?:png|jpg|webp)", payload.cover_image):
        raise HTTPException(status_code=422, detail="Upload a cover image using the media upload control")
    if payload.status == "published" and not payload.published_at:
        data["published_at"] = utcnow()
    committee_id = payload.committee_id
    if scope is not None:
        if current and current.committee_id not in scope:
            raise HTTPException(status_code=404, detail="Publication not found")
        if current and committee_id not in {None, current.committee_id}:
            raise HTTPException(status_code=403, detail="You cannot move content outside your committee")
        if not current:
            if len(scope) != 1:
                raise HTTPException(status_code=403, detail="A committee scope is required to publish local content")
            committee_id = scope[0]
        elif committee_id is None:
            committee_id = current.committee_id
    if committee_id is not None and not db.get(LocalCommittee, committee_id):
        raise HTTPException(status_code=422, detail="Committee not found")
    data["slug"] = slug
    data["committee_id"] = committee_id
    return data


@router.get("/api/v1/news", response_model=list[NewsOut])
def list_news(limit: int = 30, db: Session = Depends(get_db)) -> list[NewsOut]:
    limit = max(1, min(limit, 100))
    rows = db.scalars(select(NewsArticle).where(is_live_query()).order_by(NewsArticle.is_pinned.desc(), NewsArticle.published_at.desc(), NewsArticle.id.desc()).limit(limit))
    return [as_public(row) for row in rows]


@router.get("/api/v1/public/summary")
def public_summary(db: Session = Depends(get_db)) -> dict[str, int]:
    verified_members = (select(func.count(func.distinct(MemberProfile.id))).join(User, User.id == MemberProfile.user_id)
                        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
                        .join(Role, Role.id == UserRoleAssignment.role_id).where(Role.code == "member"))
    return {
        "active_members": db.scalar(verified_members.where(MemberProfile.membership_status == "active")) or 0,
        "public_committees": db.scalar(select(func.count(LocalCommittee.id)).where(LocalCommittee.is_public.is_(True))) or 0,
        "universities": db.scalar(select(func.count(func.distinct(LocalCommittee.university_id))).where(LocalCommittee.is_public.is_(True), LocalCommittee.university_id.is_not(None))) or 0,
        "published_events": db.scalar(select(func.count(Event.id)).where(Event.status == "published")) or 0,
    }


@router.get("/api/v1/news/{slug}", response_model=NewsOut)
def get_news(slug: str, db: Session = Depends(get_db)) -> NewsOut:
    article = db.scalar(select(NewsArticle).where(NewsArticle.slug == slug, is_live_query()))
    if not article:
        raise HTTPException(status_code=404, detail="Publication not found")
    return as_public(article)


@router.get("/api/v1/admin/news", response_model=list[NewsAdminOut])
def list_admin_news(request: Request, db: Session = Depends(get_db)) -> list[NewsAdminOut]:
    actor = current_user(request, db)
    assignments = require_member_admin(db, actor)
    scope = committee_scope(assignments)
    stmt = select(NewsArticle).order_by(NewsArticle.updated_at.desc())
    if scope is not None:
        if not scope:
            return []
        stmt = stmt.where(NewsArticle.committee_id.in_(scope))
    return [as_admin(row) for row in db.scalars(stmt)]


@router.post("/api/v1/admin/news", response_model=NewsAdminOut, status_code=status.HTTP_201_CREATED)
def create_news(payload: NewsWrite, request: Request, db: Session = Depends(get_db)) -> NewsAdminOut:
    actor = current_user(request, db)
    require_csrf(request, db)
    scope = committee_scope(require_member_admin(db, actor))
    article = NewsArticle(**prepare_content(db, payload, scope=scope), created_by=actor.id)
    db.add(article)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A publication with this URL already exists") from exc
    db.add(AuditEvent(actor_user_id=actor.id, action="news.created", entity_type="news_article", entity_id=str(article.id), details={"status": article.status, "slug": article.slug}))
    db.commit()
    db.refresh(article)
    return as_admin(article)


@router.patch("/api/v1/admin/news/{article_id}", response_model=NewsAdminOut)
def update_news(article_id: int, payload: NewsWrite, request: Request, db: Session = Depends(get_db)) -> NewsAdminOut:
    actor = current_user(request, db)
    require_csrf(request, db)
    scope = committee_scope(require_member_admin(db, actor))
    article = db.get(NewsArticle, article_id)
    if not article or (scope is not None and article.committee_id not in scope):
        raise HTTPException(status_code=404, detail="Publication not found")
    changes = prepare_content(db, payload, scope=scope, current=article)
    for field, value in changes.items():
        setattr(article, field, value)
    db.add(AuditEvent(actor_user_id=actor.id, action="news.updated", entity_type="news_article", entity_id=str(article.id), details={"status": article.status, "slug": article.slug}))
    db.commit()
    db.refresh(article)
    return as_admin(article)


@router.post("/api/v1/admin/news/{article_id}/archive", response_model=NewsAdminOut)
def archive_news(article_id: int, request: Request, db: Session = Depends(get_db)) -> NewsAdminOut:
    actor = current_user(request, db)
    require_csrf(request, db)
    scope = committee_scope(require_member_admin(db, actor))
    article = db.get(NewsArticle, article_id)
    if not article or (scope is not None and article.committee_id not in scope):
        raise HTTPException(status_code=404, detail="Publication not found")
    article.status = "archived"
    article.updated_at = utcnow()
    db.add(AuditEvent(actor_user_id=actor.id, action="news.archived", entity_type="news_article", entity_id=str(article.id), details={"slug": article.slug}))
    db.commit()
    db.refresh(article)
    return as_admin(article)


@router.post("/api/v1/admin/news/cover", status_code=status.HTTP_201_CREATED)
async def upload_news_cover(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, str]:
    actor = current_user(request, db)
    require_csrf(request, db)
    require_member_admin(db, actor)
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Cover image must be 5 MB or smaller")
    detected = None
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = ("image/png", "png")
    elif content.startswith(b"\xff\xd8\xff"):
        detected = ("image/jpeg", "jpg")
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        detected = ("image/webp", "webp")
    if not detected:
        raise HTTPException(status_code=415, detail="Use a PNG, JPEG or WebP image")
    MEDIA_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(16)}.{detected[1]}"
    target = MEDIA_DIR / filename
    target.write_bytes(content)
    db.add(AuditEvent(actor_user_id=actor.id, action="news.cover_uploaded", entity_type="news_media", entity_id=filename, details={"content_type": detected[0], "bytes": len(content)}))
    db.commit()
    return {"url": f"/media/news/{filename}"}


@router.get("/api/v1/admin/news/media")
def list_news_media(request: Request, db: Session = Depends(get_db)) -> list[dict[str, str | int]]:
    """List uploaded editorial images for authenticated content admins."""
    require_member_admin(db, current_user(request, db))
    if not MEDIA_DIR.is_dir():
        return []
    items = []
    for path in MEDIA_DIR.iterdir():
        if path.is_file() and re.fullmatch(r"[a-f0-9]{32}\.(?:png|jpg|webp)", path.name):
            items.append({"url": f"/media/news/{path.name}", "bytes": path.stat().st_size, "name": path.name})
    return sorted(items, key=lambda item: item["name"], reverse=True)[:200]


@router.get("/media/news/{filename}", include_in_schema=False)
def get_news_cover(filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:png|jpg|webp)", filename):
        raise HTTPException(status_code=404, detail="Image not found")
    path = MEDIA_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    media_type = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[path.suffix[1:]]
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/news/{slug}", include_in_schema=False)
def news_article_page(slug: str, request: Request, db: Session = Depends(get_db)):
    article = db.scalar(select(NewsArticle).where(NewsArticle.slug == slug, is_live_query()))
    if not article:
        raise HTTPException(status_code=404, detail="Publication not found")
    from app.main import TEMPLATES
    return TEMPLATES.TemplateResponse(request, "news-article.html", {"title": article.title, "article": as_public(article)})
