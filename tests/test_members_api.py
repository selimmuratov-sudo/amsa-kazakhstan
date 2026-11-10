import pytest
import base64
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app import main as main_module
from app.config import Settings
from app import cli as platform_cli
from app import login_limiter
from app.main import app
from app.routers import news as news_router
from app.models import AuditEvent, City, Event, EventCertificate, EventRegistration, ExchangeApplicationHistory, LocalCommittee, MemberProfile, NewsArticle, Notification, Role, University, User, UserRoleAssignment, ExchangeCall, utcnow
from app.security import hash_password, verify_password


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(news_router, "MEDIA_DIR", tmp_path / "news-media")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with TestSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestSession.begin() as db:
        roles = [
            Role(code="super_admin", name="Super Admin"),
            Role(code="national_board", name="National Board"),
            Role(code="national_officer", name="National Officer"),
            Role(code="exchange_reviewer", name="Exchange Reviewer"),
            Role(code="lc_director", name="Local Committee Director"),
            Role(code="local_officer", name="Local Officer"),
            Role(code="member", name="Member"),
        ]
        db.add_all(roles)
    with TestClient(app) as test_client:
        yield test_client, TestSession
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def csrf(client: TestClient) -> str:
    return client.get("/api/v1/auth/csrf").json()["csrf_token"]


def test_registration_is_private_and_creates_pending_member(client):
    c, _ = client
    token = csrf(c)
    response = c.post("/api/v1/auth/register", headers={"X-CSRF-Token": token}, json={
        "full_name": "Test Member", "username": "test-member", "password": "correct horse battery",
        "university_name": "Test Medical University", "city_name": "Test City", "study_year": 3,
    })
    assert response.status_code == 201
    assert response.json()["membership_status"] == "pending"
    assert response.json()["username"] == "test-member"
    assert response.json()["email"] is None
    profile = c.get("/api/v1/members/me")
    assert profile.status_code == 200
    assert profile.json()["email"] is None
    assert profile.json()["username"] == "test-member"
    assert profile.json()["membership_status"] == "pending"
    assert c.get("/api/v1/admin/members").status_code == 403
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": response.json()["csrf_token"]})
    anonymous_token = csrf(c)
    login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": anonymous_token}, json={"username": "TEST-MEMBER", "password": "correct horse battery"})
    assert login.status_code == 200
    assert login.json()["password_reset_required"] is False
    assert login.json()["username"] == "test-member"
    csrf_token = login.json()["csrf_token"]
    bad_change = c.post("/api/v1/auth/password", headers={"X-CSRF-Token": csrf_token}, json={"current_password": "wrong password", "new_password": "new strong password 123"})
    assert bad_change.status_code == 400
    change = c.post("/api/v1/auth/password", headers={"X-CSRF-Token": csrf_token}, json={"current_password": "correct horse battery", "new_password": "new strong password 123"})
    assert change.status_code == 200
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})
    next_token = csrf(c)
    assert c.post("/api/v1/auth/login", headers={"X-CSRF-Token": next_token}, json={"username": "test-member", "password": "correct horse battery"}).status_code == 401
    next_token = csrf(c)
    assert c.post("/api/v1/auth/login", headers={"X-CSRF-Token": next_token}, json={"username": "test-member", "password": "new strong password 123"}).status_code == 200


def test_write_without_csrf_is_rejected(client):
    c, _ = client
    csrf(c)
    response = c.post("/api/v1/auth/register", json={
        "full_name": "Test Member", "username": "test-member", "password": "correct horse battery",
        "university_name": "Test Medical University",
    })
    assert response.status_code == 403


def test_registration_is_rate_limited_per_ip_without_blocking_login(client):
    c, _ = client
    token = csrf(c)
    first_password = "correct horse battery"
    for index in range(5):
        response = c.post("/api/v1/auth/register", headers={"X-CSRF-Token": token}, json={
            "full_name": f"Rate Test {index}", "username": f"rate-test-{index}", "password": first_password,
            "university_name": "Test Medical University",
        })
        assert response.status_code == 201
        c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": response.json()["csrf_token"]})
        token = csrf(c)

    blocked = c.post("/api/v1/auth/register", headers={"X-CSRF-Token": token}, json={
        "full_name": "Rate Test Blocked", "username": "rate-test-blocked", "password": first_password,
        "university_name": "Test Medical University",
    })
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert c.post("/api/v1/auth/login", headers={"X-CSRF-Token": token}, json={
        "username": "rate-test-0", "password": first_password,
    }).status_code == 200


def test_admin_password_reset_is_one_time_scoped_and_revokes_sessions(client):
    c, session_factory = client
    with session_factory.begin() as db:
        admin_role = db.scalar(select(Role).where(Role.code == "super_admin"))
        member_role = db.scalar(select(Role).where(Role.code == "member"))
        admin = User(username="reset-admin", email=None, password_hash=hash_password("admin current password"), account_status="active")
        admin.profile = MemberProfile(full_name="Reset Admin", university_name="AMSA Kazakhstan", membership_status="active")
        admin.role_assignments.append(UserRoleAssignment(role=admin_role, scope_type="national"))
        member = User(username="reset-member", email=None, password_hash=hash_password("member current password"), account_status="active")
        member.profile = MemberProfile(full_name="Reset Member", university_name="Medical University", membership_status="active")
        member.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        db.add_all([admin, member]); db.flush()
        member_id = member.id

    with TestClient(app) as member_client:
        token = csrf(member_client)
        member_login = member_client.post("/api/v1/auth/login", headers={"X-CSRF-Token": token}, json={"username":"reset-member", "password":"member current password"})
        assert member_login.status_code == 200
        member_csrf = member_login.json()["csrf_token"]
        assert member_client.post(f"/api/v1/admin/members/{member_id}/password-reset", headers={"X-CSRF-Token":member_csrf}, json={}).status_code == 403

        admin_token = csrf(c)
        admin_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token":admin_token}, json={"username":"reset-admin", "password":"admin current password"})
        assert admin_login.status_code == 200
        reset = c.post(f"/api/v1/admin/members/{member_id}/password-reset", headers={"X-CSRF-Token":admin_login.json()["csrf_token"]}, json={})
        assert reset.status_code == 200
        assert reset.headers["cache-control"] == "no-store, private"
        result = reset.json()
        temporary_password = result["temporary_password"]
        assert len(temporary_password) >= 20 and result["password_reset_required"] is True
        assert result["revoked_sessions"] == 1
        assert member_client.get("/api/v1/members/me").status_code == 401

        token = csrf(member_client)
        temporary_login = member_client.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"reset-member", "password":temporary_password})
        assert temporary_login.status_code == 200 and temporary_login.json()["password_reset_required"] is True
        assert member_client.get("/api/v1/members/me").status_code == 403
        assert member_client.get("/api/v1/members/me").json()["detail"] == "Password change required"
        changed = member_client.post("/api/v1/auth/password", headers={"X-CSRF-Token":temporary_login.json()["csrf_token"]}, json={
            "current_password":temporary_password, "new_password":"member permanent password 2026"})
        assert changed.status_code == 200
        assert member_client.get("/api/v1/members/me").status_code == 200
        member_client.post("/api/v1/auth/logout", headers={"X-CSRF-Token":temporary_login.json()["csrf_token"]})
        token = csrf(member_client)
        assert member_client.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"reset-member", "password":temporary_password}).status_code == 401
        token = csrf(member_client)
        assert member_client.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"reset-member", "password":"member permanent password 2026"}).status_code == 200


def test_bootstrap_super_admin_must_replace_one_time_password(client, monkeypatch):
    _, session_factory = client
    monkeypatch.setattr(platform_cli, "SessionLocal", session_factory)
    temporary_password = platform_cli.create_super_admin("bootstrap-admin", "Bootstrap Administrator")
    with session_factory() as db:
        user = db.scalar(select(User).where(User.username == "bootstrap-admin"))
        assert user.password_reset_required is True
        assert verify_password(temporary_password, user.password_hash)


def test_login_rate_limit_is_pseudonymous_and_expires(client, monkeypatch):
    c, session_factory = client
    with session_factory.begin() as db:
        role = db.scalar(select(Role).where(Role.code == "member"))
        user = User(username="throttle-user", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        user.profile = MemberProfile(full_name="Throttle User", university_name="Test University", membership_status="active")
        user.role_assignments.append(UserRoleAssignment(role=role, scope_type="national"))
        db.add(user)

    start = utcnow()
    monkeypatch.setattr(login_limiter, "utcnow", lambda: start)
    token = csrf(c)
    failures = [c.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"throttle-user", "password":"wrong password"}) for _ in range(10)]
    assert all(response.status_code == 401 for response in failures)
    unknown = c.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"no-such-user", "password":"wrong password"})
    assert unknown.status_code == 401 and unknown.json()["detail"] == failures[0].json()["detail"]
    locked = c.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"throttle-user", "password":"correct horse battery"})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    assert locked.json()["detail"] == "Too many failed login attempts. Try again later."
    with session_factory() as db:
        buckets = db.scalars(select(login_limiter.LoginAttemptBucket)).all()
        assert buckets and all(len(bucket.bucket_hash) == 64 for bucket in buckets)
        assert all("throttle-user" not in bucket.bucket_hash and "testclient" not in bucket.bucket_hash for bucket in buckets)

    monkeypatch.setattr(login_limiter, "utcnow", lambda: start + timedelta(minutes=16))
    allowed = c.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"throttle-user", "password":"correct horse battery"})
    assert allowed.status_code == 200


def test_local_officer_cannot_read_or_change_another_committee(client):
    c, session_factory = client
    with session_factory.begin() as db:
        city = City(name="Test City")
        db.add(city); db.flush()
        university = University(name="Test University", city_id=city.id)
        db.add(university); db.flush()
        first = LocalCommittee(name="Test LC One", university_id=university.id, city_id=city.id)
        second = LocalCommittee(name="Test LC Two", university_id=university.id, city_id=city.id)
        db.add_all([first, second]); db.flush()
        director_role = db.scalar(select(Role).where(Role.code == "lc_director"))
        member_role = db.scalar(select(Role).where(Role.code == "member"))
        director = User(username="director", email="director@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        director.profile = MemberProfile(full_name="Test Director", university_name=university.name, committee_id=first.id, membership_status="active")
        director.role_assignments.append(UserRoleAssignment(role=director_role, scope_type="local_committee", scope_id=first.id))
        m1 = User(username="one", email="one@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        m1.profile = MemberProfile(full_name="Member One", university_name=university.name, committee_id=first.id, membership_status="active")
        m1.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        m2 = User(username="two", email="two@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        m2.profile = MemberProfile(full_name="Member Two", university_name=university.name, committee_id=second.id, membership_status="active")
        m2.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        db.add_all([director, m1, m2])
        db.flush()
        director_id, outside_id = director.id, m2.id

    csrf_token = csrf(c)
    login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": csrf_token}, json={"username": "director", "password": "correct horse battery"})
    assert login.status_code == 200
    summary = c.get("/api/v1/admin/summary")
    assert summary.status_code == 200
    assert summary.json()["members"] == 1
    assert summary.json()["active_members"] == 1
    assert summary.json()["committees"] == 1
    roster = c.get("/api/v1/admin/members")
    assert roster.status_code == 200
    assert [row["full_name"] for row in roster.json()] == ["Member One"]
    next_csrf = login.json()["csrf_token"]
    blocked = c.patch(f"/api/v1/admin/members/{outside_id}/membership-status", headers={"X-CSRF-Token": next_csrf}, json={"status": "active"})
    assert blocked.status_code == 403
    blocked_reset = c.post(f"/api/v1/admin/members/{outside_id}/password-reset", headers={"X-CSRF-Token": next_csrf}, json={})
    assert blocked_reset.status_code == 404


def test_public_search_only_returns_published_content_and_no_member_data(client):
    c, session_factory = client
    with session_factory.begin() as db:
        db.add_all([
            Event(title="Published Cardiology Workshop", summary="Open workshop", starts_at=utcnow(), status="published"),
            Event(title="Private Draft Cardiology Workshop", summary="Secret draft", starts_at=utcnow(), status="draft"),
        ])
    result = c.get("/api/v1/search", params={"q": "Cardiology"})
    assert result.status_code == 200
    assert [x["title"] for x in result.json()["results"]] == ["Published Cardiology Workshop"]


def test_public_navigation_pages_and_browser_scripts_are_served(client):
    c, _ = client
    for path in ["/", "/about.html", "/opportunities.html", "/values.html", "/contacts.html",
                 "/local-committees.html", "/events.html", "/news.html", "/search.html", "/events.js", "/search.js", "/news.js", "/home-feed.js", "/news-admin.js",
                 "/local-committees.js", "/site-nav.js", "/committee-admin.js", "/event-admin.js"]:
        assert c.get(path).status_code == 200, path
    assert c.get("/admin/events").status_code == 401
    assert c.get("/admin/news").status_code == 401
    assert c.get("/admin/committees").status_code == 401


def test_security_headers_apply_to_public_pages_and_hsts_is_production_only(client, monkeypatch):
    c, _ = client
    response = c.get("/")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Strict-Transport-Security" not in response.headers
    monkeypatch.setattr(main_module.settings, "environment", "production")
    secure_response = c.get("/")
    assert secure_response.headers["Strict-Transport-Security"] == "max-age=15552000; includeSubDomains"


def test_news_cms_upload_publish_search_and_archive(client):
    c, session_factory = client
    assert c.get("/api/v1/admin/news/media").status_code == 401
    with session_factory.begin() as db:
        admin_role = db.scalar(select(Role).where(Role.code == "super_admin"))
        admin = User(username="editor", email="editor@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        admin.profile = MemberProfile(full_name="News Editor", university_name="AMSA Kazakhstan", membership_status="active")
        admin.role_assignments.append(UserRoleAssignment(role=admin_role, scope_type="national"))
        draft = NewsArticle(title="Hidden draft about exchanges", slug="hidden-draft", body="This unpublished story should not appear", status="draft")
        db.add_all([admin, draft])

    token = csrf(c)
    login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": token}, json={"username": "editor", "password": "correct horse battery"})
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/cWQAAAAASUVORK5CYII=")
    upload = c.post("/api/v1/admin/news/cover", headers={"X-CSRF-Token": csrf_token}, files={"file": ("cover.png", png, "image/png")})
    assert upload.status_code == 201
    media = c.get("/api/v1/admin/news/media")
    assert media.status_code == 200
    assert [item["url"] for item in media.json()] == [upload.json()["url"]]
    assert c.get(upload.json()["url"]).status_code == 200
    invalid_upload = c.post("/api/v1/admin/news/cover", headers={"X-CSRF-Token": csrf_token}, files={"file": ("bad.svg", b"<svg/>", "image/svg+xml")})
    assert invalid_upload.status_code == 415
    published = c.post("/api/v1/admin/news", headers={"X-CSRF-Token": csrf_token}, json={
        "title": "Встреча медицинских студентов", "summary": "Открытый разговор о развитии сообщества", "body": "Подробная история встречи и её результаты.",
        "category": "Сообщество", "status": "published", "is_pinned": True, "cover_image": upload.json()["url"],
    })
    assert published.status_code == 201
    article = published.json()
    assert article["slug"].startswith("vstrecha-meditsinskikh")
    assert c.get(f"/news/{article['slug']}").status_code == 200
    assert c.get("/api/v1/search", params={"q": "встреча"}).json()["results"][0]["type"] == "Новость"
    assert [item["slug"] for item in c.get("/api/v1/news").json()] == [article["slug"]]
    archived = c.post(f"/api/v1/admin/news/{article['id']}/archive", headers={"X-CSRF-Token": csrf_token})
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert c.get(f"/api/v1/news/{article['slug']}").status_code == 404


def test_public_stats_count_members_only_not_admin_profiles(client):
    c, session_factory = client
    with session_factory.begin() as db:
        role = db.scalar(select(Role).where(Role.code == "super_admin"))
        admin = User(username="stats-admin", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        admin.profile = MemberProfile(full_name="Operations Admin", university_name="AMSA Kazakhstan", membership_status="active")
        admin.role_assignments.append(UserRoleAssignment(role=role, scope_type="national"))
        db.add(admin)
    summary = c.get("/api/v1/public/summary")
    assert summary.status_code == 200
    assert summary.json()["active_members"] == 0


def test_event_registration_requires_active_membership_and_enforces_capacity(client):
    c, session_factory = client
    with session_factory.begin() as db:
        member_role = db.scalar(select(Role).where(Role.code == "member"))
        active = User(username="active", email="active@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        active.profile = MemberProfile(full_name="Active Student", university_name="Test University", membership_status="active")
        active.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        pending = User(username="pending", email="pending@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        pending.profile = MemberProfile(full_name="Pending Student", university_name="Test University", membership_status="pending")
        pending.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        second = User(username="second", email="second@example.test", password_hash=hash_password("correct horse battery"), account_status="active")
        second.profile = MemberProfile(full_name="Second Student", university_name="Test University", membership_status="active")
        second.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        event = Event(title="Limited Workshop", summary="One seat", starts_at=utcnow()+timedelta(days=1), status="published", capacity=1)
        db.add_all([active, pending, second, event]); db.flush()
        event_id = event.id

    csrf(c)
    denied_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": csrf(c)}, json={"username": "pending", "password": "correct horse battery"})
    assert denied_login.status_code == 200
    denied = c.post(f"/api/v1/events/{event_id}/register", headers={"X-CSRF-Token": denied_login.json()["csrf_token"]})
    assert denied.status_code == 403

    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": denied_login.json()["csrf_token"]})
    anon = csrf(c)
    login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": anon}, json={"username": "active", "password": "correct horse battery"})
    token = login.json()["csrf_token"]
    first = c.post(f"/api/v1/events/{event_id}/register", headers={"X-CSRF-Token": token})
    assert first.status_code == 201 and first.json()["status"] == "confirmed"
    assert c.get(f"/api/v1/members/me/events").json()[0]["my_status"] == "confirmed"
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token})
    second_token = csrf(c)
    second_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": second_token}, json={"username": "second", "password": "correct horse battery"})
    waitlisted = c.post(f"/api/v1/events/{event_id}/register", headers={"X-CSRF-Token": second_login.json()["csrf_token"]})
    assert waitlisted.status_code == 201 and waitlisted.json()["status"] == "waitlisted"
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": second_login.json()["csrf_token"]})
    active_anon = csrf(c)
    active_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": active_anon}, json={"username": "active", "password": "correct horse battery"})
    cancelled = c.delete(f"/api/v1/events/{event_id}/register", headers={"X-CSRF-Token": active_login.json()["csrf_token"]})
    assert cancelled.status_code == 204
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": active_login.json()["csrf_token"]})
    second_anon = csrf(c)
    second_relogin = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": second_anon}, json={"username": "second", "password": "correct horse battery"})
    assert c.get("/api/v1/members/me/events").json()[0]["my_status"] == "confirmed"
    assert c.get("/api/v1/members/me/notifications").json()["items"][0]["kind"] == "event_waitlist"


def test_event_attendance_certificate_issue_revoke_and_private_print(client):
    c, session_factory = client
    with session_factory.begin() as db:
        member_role = db.scalar(select(Role).where(Role.code == "member"))
        admin_role = db.scalar(select(Role).where(Role.code == "super_admin"))
        member = User(username="certificate-member", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        member.profile = MemberProfile(full_name="Certificate Member", university_name="Test University", membership_status="active")
        member.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        admin = User(username="certificate-admin", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        admin.profile = MemberProfile(full_name="Certificate Admin", university_name="AMSA Kazakhstan", membership_status="active")
        admin.role_assignments.append(UserRoleAssignment(role=admin_role, scope_type="national"))
        event = Event(title="Completed Workshop", summary="Certificate workflow", starts_at=utcnow()-timedelta(days=2),
            ends_at=utcnow()-timedelta(days=1), status="published")
        db.add_all([member, admin, event]); db.flush()
        registration = EventRegistration(event_id=event.id, user_id=member.id, status="confirmed")
        db.add(registration)
        event_id, member_id = event.id, member.id

    anon = csrf(c)
    member_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": anon}, json={"username": "certificate-member", "password": "correct horse battery"})
    assert member_login.status_code == 200
    member_token = member_login.json()["csrf_token"]
    assert c.get(f"/api/v1/admin/events/{event_id}/registrations").status_code == 403
    assert c.get("/api/v1/members/me/certificates").json() == []
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": member_token})

    admin_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": csrf(c)}, json={"username": "certificate-admin", "password": "correct horse battery"})
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["csrf_token"]
    roster = c.get(f"/api/v1/admin/events/{event_id}/registrations")
    assert roster.status_code == 200 and roster.json()[0]["status"] == "confirmed"
    marked = c.patch(f"/api/v1/admin/events/{event_id}/registrations/{member_id}/attendance", headers={"X-CSRF-Token": admin_token}, json={"attended": True})
    assert marked.status_code == 200 and marked.json()["attendance_status"] == "attended"
    issued = c.post(f"/api/v1/admin/events/{event_id}/certificates/{member_id}", headers={"X-CSRF-Token": admin_token})
    assert issued.status_code == 201
    certificate = issued.json()
    assert c.get(certificate["verification_url"]).status_code == 200
    assert c.get(f"/api/v1/certificates/verify/{certificate['public_code']}").json()["is_valid"] is True
    assert c.post(f"/api/v1/admin/events/{event_id}/certificates/{member_id}", headers={"X-CSRF-Token": admin_token}).status_code == 409
    assert c.patch(f"/api/v1/admin/events/{event_id}/registrations/{member_id}/attendance", headers={"X-CSRF-Token": admin_token}, json={"attended": False}).status_code == 409
    revoked = c.post(f"/api/v1/admin/events/{event_id}/certificates/{certificate['id']}/revoke", headers={"X-CSRF-Token": admin_token}, json={"reason":"Issued in error"})
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert c.get(f"/api/v1/certificates/verify/{certificate['public_code']}").json()["is_valid"] is False
    c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": admin_token})

    member_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": csrf(c)}, json={"username": "certificate-member", "password": "correct horse battery"})
    assert member_login.status_code == 200
    member_token = member_login.json()["csrf_token"]
    member_certificates = c.get("/api/v1/members/me/certificates").json()
    assert len(member_certificates) == 1 and member_certificates[0]["status"] == "revoked"
    assert member_certificates[0]["print_url"] is None
    assert c.get(f"/certificates/{certificate['id']}/print").status_code == 404
    assert c.post(f"/api/v1/admin/events/{event_id}/certificates/{member_id}", headers={"X-CSRF-Token": member_token}).status_code == 403


def test_exchange_application_privacy_review_workflow_and_history(client):
    c, session_factory = client
    with session_factory.begin() as db:
        member_role = db.scalar(select(Role).where(Role.code == "member"))
        reviewer_role = db.scalar(select(Role).where(Role.code == "exchange_reviewer"))
        super_role = db.scalar(select(Role).where(Role.code == "super_admin"))
        candidate = User(username="candidate", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        candidate.profile = MemberProfile(full_name="Exchange Candidate", university_name="Medical University", membership_status="active")
        candidate.role_assignments.append(UserRoleAssignment(role=member_role, scope_type="national"))
        reviewer = User(username="exchange-reviewer", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        reviewer.profile = MemberProfile(full_name="Exchange Reviewer", university_name="AMSA Kazakhstan", membership_status="active")
        reviewer.role_assignments.append(UserRoleAssignment(role=reviewer_role, scope_type="national"))
        root = User(username="root-admin", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        root.profile = MemberProfile(full_name="Root Admin", university_name="AMSA Kazakhstan", membership_status="active")
        root.role_assignments.append(UserRoleAssignment(role=super_role, scope_type="national"))
        call = ExchangeCall(title="AMSEP autumn intake", program_type="AMSEP", destination="Partner country",
            summary="Student exchange", description="Academic and cultural exchange", requirements="Active member",
            closes_at=utcnow() + timedelta(days=30), status="open")
        db.add_all([candidate, reviewer, root, call]); db.flush()
        call_id = call.id

    reviewer_csrf = csrf(c)
    reviewer_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token": reviewer_csrf}, json={"username": "exchange-reviewer", "password": "correct horse battery"})
    assert reviewer_login.status_code == 200
    assert c.get("/api/v1/admin/exchanges").status_code == 200
    assert c.get("/admin/exchanges").status_code == 200
    assert c.get("/api/v1/admin/exchange-reviewers").status_code == 403
    assert len(c.get("/api/v1/exchanges").json()) == 1

    with TestClient(app) as candidate_client:
        token = csrf(candidate_client)
        logged_in = candidate_client.post("/api/v1/auth/login", headers={"X-CSRF-Token": token}, json={"username": "candidate", "password": "correct horse battery"})
        assert logged_in.status_code == 200
        candidate_csrf = logged_in.json()["csrf_token"]
        submitted = candidate_client.post(f"/api/v1/exchanges/{call_id}/applications", headers={"X-CSRF-Token": candidate_csrf}, json={
            "motivation": "I want to learn from another health system and bring practical ideas back to my student community.",
            "relevant_experience": "I coordinate peer learning sessions.", "preferred_destination": "Partner country",
        })
        assert submitted.status_code == 201
        app_id = submitted.json()["id"]
        assert submitted.json()["motivation"] is None
        assert candidate_client.post(f"/api/v1/exchanges/{call_id}/applications", headers={"X-CSRF-Token": candidate_csrf}, json={"motivation": "A sufficiently long reason for applying to this exchange program today."}).status_code == 409
        assert candidate_client.get("/api/v1/admin/exchanges/applications").status_code == 403

        moved = c.patch(f"/api/v1/admin/exchanges/applications/{app_id}", headers={"X-CSRF-Token": reviewer_login.json()["csrf_token"]}, json={"status":"under_review", "reviewer_note":"Your application is now being reviewed."})
        assert moved.status_code == 200
        assert moved.json()["candidate_name"] == "Exchange Candidate"
        my_apps = candidate_client.get("/api/v1/members/me/exchange-applications").json()
        assert my_apps[0]["status"] == "under_review"
        assert my_apps[0]["reviewer_note"] == "Your application is now being reviewed."
        assert candidate_client.get("/api/v1/members/me/notifications").json()["items"][0]["kind"] == "exchange_status"
        history = candidate_client.get(f"/api/v1/members/me/exchange-applications/{app_id}/history").json()
        assert [row["to_status"] for row in history] == ["submitted", "under_review"]
        assert c.patch(f"/api/v1/admin/exchanges/applications/{app_id}", headers={"X-CSRF-Token": reviewer_login.json()["csrf_token"]}, json={"status":"rejected"}).status_code == 200
        assert c.patch(f"/api/v1/admin/exchanges/applications/{app_id}", headers={"X-CSRF-Token": reviewer_login.json()["csrf_token"]}, json={"status":"accepted"}).status_code == 409

    with TestClient(app) as root_client:
        root_token = csrf(root_client)
        root_login = root_client.post("/api/v1/auth/login", headers={"X-CSRF-Token": root_token}, json={"username":"root-admin", "password":"correct horse battery"})
        assert root_login.status_code == 200
        root_csrf = root_login.json()["csrf_token"]
        assert root_client.get("/admin/exchanges").status_code == 200
        assert [row["username"] for row in root_client.get("/api/v1/admin/exchange-reviewers").json()] == ["exchange-reviewer"]
        added = root_client.post("/api/v1/admin/exchange-reviewers", headers={"X-CSRF-Token":root_csrf}, json={"username":"candidate"})
        assert added.status_code == 201
        assert added.json()["username"] == "candidate"
        assert root_client.post("/api/v1/admin/exchange-reviewers", headers={"X-CSRF-Token":root_csrf}, json={"username":"candidate"}).status_code == 409
        assert root_client.delete(f"/api/v1/admin/exchange-reviewers/{added.json()['user_id']}", headers={"X-CSRF-Token":root_csrf}).status_code == 204


def test_exchange_management_ui_is_served(client):
    c, _ = client
    assert c.get("/admin/exchanges").status_code == 401
    assert c.get("/exchange-admin.js").status_code == 200
    assert c.get("/exchanges.js").status_code == 200
    assert c.get("/opportunities.html").status_code == 200


def test_quarterly_committee_reports_scope_review_comments_and_history(client):
    c, session_factory = client
    with session_factory.begin() as db:
        writer_role = db.scalar(select(Role).where(Role.code == "lc_director"))
        national_role = db.scalar(select(Role).where(Role.code == "national_board"))
        first = LocalCommittee(name="Report Committee One")
        second = LocalCommittee(name="Report Committee Two")
        db.add_all([first, second]); db.flush()
        writer = User(username="report-writer", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        writer.profile = MemberProfile(full_name="Report Writer", university_name="University One", membership_status="active")
        writer.role_assignments.append(UserRoleAssignment(role=writer_role, scope_type="local_committee", scope_id=first.id))
        outsider = User(username="other-writer", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        outsider.profile = MemberProfile(full_name="Other Writer", university_name="University Two", membership_status="active")
        outsider.role_assignments.append(UserRoleAssignment(role=writer_role, scope_type="local_committee", scope_id=second.id))
        reviewer = User(username="report-reviewer", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        reviewer.profile = MemberProfile(full_name="Report Reviewer", university_name="AMSA Kazakhstan", membership_status="active")
        reviewer.role_assignments.append(UserRoleAssignment(role=national_role, scope_type="national"))
        db.add_all([writer, outsider, reviewer]); db.flush()
        first_id, second_id = first.id, second.id

    report_data = {"committee_id": first_id, "period":"2026-Q4", "executive_summary":"We delivered student-led educational activities across the quarter.",
        "activities":"Hosted two peer-learning meetings and an introductory workshop.", "impact":"More students joined recurring learning sessions.",
        "challenges":"Scheduling rooms during exam season.", "next_steps":"Recruit volunteers for the next quarter.", "members_count":18, "events_count":3}
    with TestClient(app) as writer_client:
        token = csrf(writer_client)
        login = writer_client.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"report-writer","password":"correct horse battery"})
        assert login.status_code == 200
        assert writer_client.get("/reports").status_code == 200
        assert writer_client.get("/reports.js").status_code == 200
        writer_csrf = login.json()["csrf_token"]
        assert [x["id"] for x in writer_client.get("/api/v1/reports/committees").json()] == [first_id]
        created = writer_client.post("/api/v1/reports", headers={"X-CSRF-Token":writer_csrf}, json=report_data)
        assert created.status_code == 201
        report_id = created.json()["id"]
        assert writer_client.post("/api/v1/reports", headers={"X-CSRF-Token":writer_csrf}, json=report_data).status_code == 409
        assert writer_client.post("/api/v1/reports", headers={"X-CSRF-Token":writer_csrf}, json={**report_data,"committee_id":second_id,"period":"2027-Q1"}).status_code == 403
        assert writer_client.get("/api/v1/admin/reports").status_code == 403
        assert writer_client.get("/api/v1/admin/reports/google-sheets/status").status_code == 403
        assert writer_client.post(f"/api/v1/reports/{report_id}/submit", headers={"X-CSRF-Token":writer_csrf}, json={}).json()["status"] == "submitted"

        with TestClient(app) as outsider_client:
            outsider_token = csrf(outsider_client)
            outsider_login = outsider_client.post("/api/v1/auth/login", headers={"X-CSRF-Token":outsider_token}, json={"username":"other-writer","password":"correct horse battery"})
            assert outsider_login.status_code == 200
            assert outsider_client.get("/reports").status_code == 200
            assert outsider_client.get("/api/v1/reports").json() == []
            assert outsider_client.get(f"/api/v1/reports/{report_id}/comments").status_code == 404

        reviewer_token = csrf(c)
        reviewer_login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token":reviewer_token}, json={"username":"report-reviewer","password":"correct horse battery"})
        assert reviewer_login.status_code == 200
        assert c.get("/reports").status_code == 200
        reviewer_csrf = reviewer_login.json()["csrf_token"]
        assert len(c.get("/api/v1/admin/reports").json()) == 1
        assert c.get("/api/v1/members/me/notifications").json()["items"][0]["kind"] == "report_submitted"
        checking = c.patch(f"/api/v1/admin/reports/{report_id}", headers={"X-CSRF-Token":reviewer_csrf}, json={"status":"under_review","comment":"The report is in national review."})
        assert checking.status_code == 200
        returned = c.patch(f"/api/v1/admin/reports/{report_id}", headers={"X-CSRF-Token":reviewer_csrf}, json={"status":"changes_requested","comment":"Please add a short outcome for the workshop."})
        assert returned.status_code == 200
        assert writer_client.get("/api/v1/members/me/notifications").json()["items"][0]["kind"] == "report_status"
        changed_data = {**report_data, "activities":"Hosted two peer-learning meetings and an introductory workshop; both included learner feedback."}
        assert writer_client.patch(f"/api/v1/reports/{report_id}", headers={"X-CSRF-Token":writer_csrf}, json=changed_data).status_code == 200
        assert writer_client.post(f"/api/v1/reports/{report_id}/submit", headers={"X-CSRF-Token":writer_csrf}, json={}).status_code == 200
        approved = c.patch(f"/api/v1/admin/reports/{report_id}", headers={"X-CSRF-Token":reviewer_csrf}, json={"status":"approved","comment":"Approved for the national quarterly review."})
        assert approved.status_code == 200 and approved.json()["status"] == "approved"
        sync_status = c.get("/api/v1/admin/reports/google-sheets/status")
        assert sync_status.status_code == 200 and sync_status.json()["enabled"] is False
        assert sync_status.json()["runs"][0]["status"] == "not_configured"
        assert sync_status.json()["runs"][0]["error_code"] == "not_configured"
        retry = c.post(f"/api/v1/admin/reports/{report_id}/sync", headers={"X-CSRF-Token": reviewer_csrf}, json={})
        assert retry.status_code == 200 and retry.json()["status"] == "not_configured"
        assert writer_client.get(f"/api/v1/reports/{report_id}/comments").json()[-1]["body"] == "Approved for the national quarterly review."
        assert writer_client.get(f"/api/v1/reports/{report_id}/history").json()[-1]["to_status"] == "approved"
        assert writer_client.patch(f"/api/v1/reports/{report_id}", headers={"X-CSRF-Token":writer_csrf}, json=changed_data).status_code == 409


def test_google_forms_embed_accepts_only_google_forms_view_urls():
    valid = Settings(google_reports_form_url="https://docs.google.com/forms/d/e/abc-123/viewform?usp=sf_link")
    assert valid.google_reports_embed_url == "https://docs.google.com/forms/d/e/abc-123/viewform?usp=sf_link&embedded=true"
    invalid = Settings(google_reports_form_url="https://example.com/forms/d/e/abc/viewform")
    assert invalid.google_reports_embed_url is None


def test_notification_center_pagination_privacy_and_read_actions(client):
    c, session_factory = client
    with session_factory.begin() as db:
        role = db.scalar(select(Role).where(Role.code == "member"))
        first = User(username="notify-first", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        first.profile = MemberProfile(full_name="First Recipient", university_name="University", membership_status="active")
        first.role_assignments.append(UserRoleAssignment(role=role, scope_type="national"))
        second = User(username="notify-second", email=None, password_hash=hash_password("correct horse battery"), account_status="active")
        second.profile = MemberProfile(full_name="Second Recipient", university_name="University", membership_status="active")
        second.role_assignments.append(UserRoleAssignment(role=role, scope_type="national"))
        db.add_all([first, second]); db.flush()
        first_id, second_id = first.id, second.id
        db.add_all([
            Notification(user_id=first_id, kind="event", title="Первое уведомление", body="Событие", link="/events.html"),
            Notification(user_id=first_id, kind="report", title="Второе уведомление", body="Отчёт", link="/reports"),
            Notification(user_id=second_id, kind="private", title="Чужое уведомление", body="Приватное", link="/cabinet"),
        ])

    token = csrf(c)
    login = c.post("/api/v1/auth/login", headers={"X-CSRF-Token":token}, json={"username":"notify-first","password":"correct horse battery"})
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]
    first_page = c.get("/api/v1/members/me/notifications?limit=1").json()
    assert first_page["unread_count"] == 2 and first_page["has_more"] is True
    assert first_page["items"][0]["title"] == "Второе уведомление"
    second_page = c.get(f"/api/v1/members/me/notifications?limit=1&before_id={first_page['next_before_id']}").json()
    assert [item["title"] for item in second_page["items"]] == ["Первое уведомление"]
    first_notification_id = second_page["items"][0]["id"]
    assert c.patch(f"/api/v1/members/me/notifications/{first_notification_id}/read", headers={"X-CSRF-Token":csrf_token}).status_code == 200
    assert c.get(f"/api/v1/members/me/notifications/{first_page['items'][0]['id']}/read").status_code == 405
    assert c.post("/api/v1/members/me/notifications/read-all", headers={"X-CSRF-Token":csrf_token}, json={}).json() == {"marked_read":1}
    assert c.get("/api/v1/members/me/notifications").json()["unread_count"] == 0

    with TestClient(app) as other_client:
        other_token = csrf(other_client)
        other_login = other_client.post("/api/v1/auth/login", headers={"X-CSRF-Token":other_token}, json={"username":"notify-second","password":"correct horse battery"})
        assert other_login.status_code == 200
        other_items = other_client.get("/api/v1/members/me/notifications").json()["items"]
        assert [item["title"] for item in other_items] == ["Чужое уведомление"]

