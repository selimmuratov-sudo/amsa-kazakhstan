from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.models import EventCertificate, Role, User, UserRoleAssignment
from app.rbac import require_member_admin
from app.routers import auth, committees, events, exchanges, members, news, notifications, reports, search
from app.security import current_user

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
settings = get_settings()

app = FastAPI(
    title="AMSA Kazakhstan Platform API",
    version="0.1.0",
    description="Member accounts and scoped administration API for the AMSA Kazakhstan platform.",
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.host_list)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")
    return response


app.include_router(auth.router)
app.include_router(members.router)
app.include_router(committees.router)
app.include_router(events.router)
app.include_router(exchanges.router)
app.include_router(reports.router)
app.include_router(notifications.router)
app.include_router(search.router)
app.include_router(news.router)

PUBLIC_PAGES = {"about.html", "opportunities.html", "values.html", "contacts.html", "local-committees.html", "events.html", "search.html", "news.html"}


@app.get("/healthz", include_in_schema=False)
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def homepage() -> FileResponse:
    return FileResponse(ROOT / "index.html", media_type="text/html")


@app.get("/styles.css", include_in_schema=False)
def public_styles() -> FileResponse:
    return FileResponse(ROOT / "styles.css", media_type="text/css")


@app.get("/portal.css", include_in_schema=False)
def portal_styles() -> FileResponse:
    return FileResponse(ROOT / "portal.css", media_type="text/css")


@app.get("/portal.js", include_in_schema=False)
def portal_script() -> FileResponse:
    return FileResponse(ROOT / "portal.js", media_type="application/javascript")


@app.get("/password-reset.js", include_in_schema=False)
def password_reset_script() -> FileResponse:
    return FileResponse(ROOT / "password-reset.js", media_type="application/javascript")


@app.get("/committee-admin.js", include_in_schema=False)
def committee_admin_script() -> FileResponse:
    return FileResponse(ROOT / "committee-admin.js", media_type="application/javascript")


@app.get("/event-admin.js", include_in_schema=False)
def event_admin_script() -> FileResponse:
    return FileResponse(ROOT / "event-admin.js", media_type="application/javascript")


@app.get("/exchange-admin.js", include_in_schema=False)
def exchange_admin_script() -> FileResponse:
    return FileResponse(ROOT / "exchange-admin.js", media_type="application/javascript")


@app.get("/exchanges.js", include_in_schema=False)
def exchanges_script() -> FileResponse:
    return FileResponse(ROOT / "exchanges.js", media_type="application/javascript")


@app.get("/reports.js", include_in_schema=False)
def reports_script() -> FileResponse:
    return FileResponse(ROOT / "reports.js", media_type="application/javascript")


@app.get("/news.js", include_in_schema=False)
def news_script() -> FileResponse:
    return FileResponse(ROOT / "news.js", media_type="application/javascript")


@app.get("/news-admin.js", include_in_schema=False)
def news_admin_script() -> FileResponse:
    return FileResponse(ROOT / "news-admin.js", media_type="application/javascript")


@app.get("/home-feed.js", include_in_schema=False)
def home_feed_script() -> FileResponse:
    return FileResponse(ROOT / "home-feed.js", media_type="application/javascript")


@app.get("/local-committees.js", include_in_schema=False)
def committees_script() -> FileResponse:
    return FileResponse(ROOT / "local-committees.js", media_type="application/javascript")


@app.get("/events.js", include_in_schema=False)
def events_script() -> FileResponse:
    return FileResponse(ROOT / "events.js", media_type="application/javascript")


@app.get("/search.js", include_in_schema=False)
def search_script() -> FileResponse:
    return FileResponse(ROOT / "search.js", media_type="application/javascript")


@app.get("/site-nav.js", include_in_schema=False)
def site_nav_script() -> FileResponse:
    return FileResponse(ROOT / "site-nav.js", media_type="application/javascript")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
@app.get("/register", response_class=HTMLResponse, include_in_schema=False)
@app.get("/cabinet", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def portal_page(request: Request) -> HTMLResponse:
    mode = request.url.path.strip("/") or "login"
    titles = {"login": "Вход в кабинет", "register": "Регистрация студента", "cabinet": "Мой кабинет", "admin": "Управление студентами"}
    return TEMPLATES.TemplateResponse(request, "portal.html", {"title": titles[mode], "mode": mode})


@app.get("/change-password", response_class=HTMLResponse, include_in_schema=False)
def password_reset_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "password-reset.html", {"title": "Смена временного пароля"})


@app.get("/admin/committees", response_class=HTMLResponse, include_in_schema=False)
def committee_admin_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    require_member_admin(db, current_user(request, db))
    return TEMPLATES.TemplateResponse(request, "committee-admin.html", {"title": "Локальные комитеты"})


@app.get("/admin/events", response_class=HTMLResponse, include_in_schema=False)
def event_admin_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    require_member_admin(db, current_user(request, db))
    return TEMPLATES.TemplateResponse(request, "event-admin.html", {"title": "Управление мероприятиями"})


@app.get("/admin/news", response_class=HTMLResponse, include_in_schema=False)
def news_admin_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    require_member_admin(db, current_user(request, db))
    return TEMPLATES.TemplateResponse(request, "news-admin.html", {"title": "Редактор новостей"})


@app.get("/admin/exchanges", response_class=HTMLResponse, include_in_schema=False)
def exchanges_admin_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    actor = current_user(request, db)
    role_codes = set(db.scalars(select(Role.code).join(UserRoleAssignment).where(UserRoleAssignment.user_id == actor.id)))
    if not role_codes.intersection({"super_admin", "national_board", "exchange_reviewer"}):
        raise HTTPException(status_code=403, detail="National exchange reviewer role required")
    return TEMPLATES.TemplateResponse(request, "exchange-admin.html", {"title": "Управление обменами"})


@app.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def reports_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    actor = current_user(request, db)
    roles = set(db.scalars(select(Role.code).join(UserRoleAssignment).where(UserRoleAssignment.user_id == actor.id)))
    if not roles.intersection({"super_admin", "national_board", "lc_director", "local_officer"}):
        raise HTTPException(status_code=403, detail="Committee reporting role required")
    return TEMPLATES.TemplateResponse(request, "reports.html", {
        "title": "Отчёты локальных комитетов",
        "google_form_url": get_settings().google_reports_embed_url,
    })


@app.get("/certificates/{certificate_id}/print", response_class=HTMLResponse, include_in_schema=False)
def print_certificate(certificate_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    actor = current_user(request, db)
    certificate = db.scalar(select(EventCertificate).options(joinedload(EventCertificate.event),
        joinedload(EventCertificate.user).joinedload(User.profile)).where(
            EventCertificate.id == certificate_id, EventCertificate.user_id == actor.id,
            EventCertificate.status == "issued"))
    if not certificate:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return TEMPLATES.TemplateResponse(request, "certificate.html", {
        "certificate": certificate,
        "recipient_name": certificate.user.profile.full_name if certificate.user.profile else certificate.user.username,
        "verification_url": request.url_for("verify_certificate_page", public_code=certificate.public_code),
    })


@app.get("/certificate/verify/{public_code}", response_class=HTMLResponse, include_in_schema=False)
def verify_certificate_page(public_code: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not 32 <= len(public_code) <= 64:
        raise HTTPException(status_code=404, detail="Certificate not found")
    certificate = db.scalar(select(EventCertificate).options(joinedload(EventCertificate.event),
        joinedload(EventCertificate.user).joinedload(User.profile)).where(EventCertificate.public_code == public_code))
    if not certificate:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return TEMPLATES.TemplateResponse(request, "certificate-verify.html", {
        "certificate": certificate,
        "recipient_name": certificate.user.profile.full_name if certificate.user.profile else certificate.user.username,
        "is_valid": certificate.status == "issued",
    })


@app.get("/{page_name}", response_class=HTMLResponse, include_in_schema=False)
def public_page(page_name: str) -> FileResponse:
    if page_name not in PUBLIC_PAGES:
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(ROOT / page_name, media_type="text/html")


