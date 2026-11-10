#!/usr/bin/env python3
"""Small local AMSA Kazakhstan membership portal (Python standard library only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import html
import io
import os
import secrets
import sqlite3
import sys
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import tempfile

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(tempfile.gettempdir()) / "data"
DB_PATH = DATA_DIR / "amsa.sqlite3"
COOKIE = "amsa_session"
SESSION_AGE = 7 * 24 * 60 * 60
ITERATIONS = 600_000
LOGIN_FAILURES: dict[str, list[float]] = {}
STATUSES = {"pending": "Ожидает подтверждения", "active": "Подтверждён", "suspended": "Приостановлен"}


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        university TEXT NOT NULL,
        course TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','admin')),
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','suspended')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        csrf TEXT NOT NULL,
        expires INTEGER NOT NULL
    )""")
    con.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
    con.commit()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass

    # Автоматическое создание админа
    admin_exists = con.execute("SELECT 1 FROM users WHERE email = ?", ("amsa-admin",)).fetchone()
    if not admin_exists:
        admin_pass_hash = password_record("BadSanta1234")
        con.execute(
            """INSERT INTO users (full_name, email, university, course, password_hash, role, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Администратор", "amsa-admin", "AMSA", "1", admin_pass_hash, "admin", "active")
        )
        con.commit()

    return con


def password_record(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def password_matches(password: str, record: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = record.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def password_strength(password: str) -> bool:
    return len(password) >= 12 and len(password) <= 200


class Handler(BaseHTTPRequestHandler):
    server_version = "AMSA-Portal"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_html(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def read_form(self) -> dict[str, str]:
        length = min(int(self.headers.get("Content-Length", "0")), 16_384)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def cookies(self) -> SimpleCookie:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        return jar

    def session(self, create: bool = False) -> tuple[sqlite3.Row | None, str | None, str | None]:
        con = db()
        token = self.cookies().get(COOKIE)
        if token:
            raw = token.value
            row = con.execute("SELECT s.csrf,s.expires,u.* FROM sessions s LEFT JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>?", (hashlib.sha256(raw.encode()).hexdigest(), int(time.time()))).fetchone()
            if row:
                if row["id"] and row["status"] == "suspended":
                    con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))
                    con.commit()
                    con.close()
                    return None, None, None
                con.close()
                return row, None, row["csrf"]
        con.close()
        if not create:
            return None, None, None
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        con = db()
        con.execute("INSERT INTO sessions(token_hash,csrf,expires) VALUES(?,?,?)", (hashlib.sha256(raw.encode()).hexdigest(), csrf, int(time.time()) + SESSION_AGE))
        con.commit()
        con.close()
        return None, raw, csrf

    @staticmethod
    def cookie_header(token: str, age: int = SESSION_AGE) -> str:
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}"

    def csrf_ok(self, form: dict[str, str], csrf: str | None) -> bool:
        supplied = form.get("csrf", "")
        return bool(csrf and supplied and hmac.compare_digest(supplied, csrf))

    def require_user(self) -> tuple[sqlite3.Row | None, str | None, str | None]:
        user, token, csrf = self.session()
        if not user or not user["id"]:
            self.redirect("/login")
            return None, token, csrf
        return user, token, csrf

    def page(self, title: str, content: str, csrf: str, user: sqlite3.Row | None = None, message: str = "", headers: dict[str, str] | None = None) -> None:
        if user and user["id"]:
            account = f'<a class="account-link" href="/cabinet">Мой кабинет</a>'
        else:
            account = '<a class="account-link" href="/login">Войти</a>'
        note = f'<div class="notice">{esc(message)}</div>' if message else ""
        nav = f'''<div class="top"><div class="wrap"><span>Asian Medical Students’ Association</span><span>Казахстан · <a href="mailto:rckazakhstan@amsa-international.org">написать нам ↗</a></span></div></div>
<header><nav class="nav wrap"><a class="brand" href="/"><span class="brandmark">AM</span><span>AMSA<small>KAZAKHSTAN</small></span></a><button class="menu" aria-label="Открыть меню" aria-expanded="false">☰</button><div class="navlinks"><a href="/">Главная</a><a href="/about.html">О сообществе</a><a href="/opportunities.html">Возможности</a><a href="/values.html">Наши ценности</a><a href="/contacts.html">Контакты</a></div>{account}</nav></header>'''
        script = """<script>const menu=document.querySelector('.menu'),navlinks=document.querySelector('.navlinks');menu.addEventListener('click',()=>{const open=navlinks.classList.toggle('open');menu.setAttribute('aria-expanded',open);menu.textContent=open?'×':'☰'});navlinks.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>{navlinks.classList.remove('open');menu.setAttribute('aria-expanded','false');menu.textContent='☰'}));</script>"""
        doc = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#10192b"><title>{esc(title)} — AMSA Kazakhstan</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet"><link rel="stylesheet" href="/styles.css"><style>{AUTH_CSS}</style></head><body>{nav}<main class="auth-main"><div class="auth-wrap"><a class="backlink" href="/">← На главную</a><h1>{esc(title)}</h1>{note}{content}</div></main><footer class="footer"><div class="wrap"><span>© 2026 AMSA Kazakhstan</span><a href="https://amsa-international.org/our-members/" target="_blank" rel="noreferrer">AMSA International ↗</a><a href="/">На главную ↑</a></div></footer>{script}</body></html>'''
        self.send_html(doc, headers=headers)

    def form(self, action: str, csrf: str, fields: str, submit: str) -> str:
        return f'<form method="post" action="{action}" class="auth-form"><input type="hidden" name="csrf" value="{esc(csrf)}">{fields}<button class="btn" type="submit">{submit} <span>↗</span></button></form>'

    def input(self, label: str, name: str, kind: str = "text", required: bool = True, attrs: str = "") -> str:
        req = "required" if required else ""
        return f'<label class="field">{label}<input type="{kind}" name="{name}" {req} {attrs}></label>'

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/about.html", "/opportunities.html", "/values.html", "/contacts.html", "/styles.css"):
            name = "index.html" if path == "/" else path.lstrip("/")
            file = ROOT / name
            if not file.is_file():
                self.send_error(404)
                return
            data = file.read_bytes()
            content_type = "text/css; charset=utf-8" if name.endswith(".css") else "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/login", "/register"):
            user, new_token, csrf = self.session(create=True)
            headers = {"Set-Cookie": self.cookie_header(new_token)} if new_token else None
            if user and user["id"]:
                self.redirect("/cabinet", headers)
                return
            if path == "/login":
                fields = self.input("Email", "email", "email", attrs='autocomplete="email"') + self.input("Пароль", "password", "password", attrs='autocomplete="current-password"')
                content = '<p class="auth-sub">Войди, чтобы открыть личный кабинет.</p>' + self.form("/login", csrf or "", fields, "Войти") + '<p class="auth-switch">Ещё нет аккаунта? <a href="/register">Зарегистрироваться</a></p>'
                self.page("Вход в кабинет", content, csrf or "", message=self.query_message(), user=user, headers=headers)
            else:
                fields = self.input("Имя и фамилия", "full_name", attrs='autocomplete="name" maxlength="100"') + self.input("Email", "email", "email", attrs='autocomplete="email" maxlength="254"') + self.input("Университет", "university", attrs='maxlength="120"') + self.input("Курс", "course", attrs='maxlength="30"') + self.input("Пароль (не менее 12 символов)", "password", "password", attrs='autocomplete="new-password" minlength="12" maxlength="200"')
                content = '<p class="auth-sub">Создай аккаунт студента. После регистрации он появится в списке на подтверждение.</p>' + self.form("/register", csrf or "", fields, "Создать аккаунт") + '<p class="auth-switch">Уже зарегистрирован? <a href="/login">Войти</a></p>'
                self.page("Регистрация студента", content, csrf or "", message=self.query_message(), user=user, headers=headers)
            return
        if path == "/logout":
            user, _, _ = self.session()
            token = self.cookies().get(COOKIE)
            if token:
                con = db()
                con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.value.encode()).hexdigest(),))
                con.commit()
                con.close()
            self.redirect("/login", {"Set-Cookie": self.cookie_header("", 0)})
            return
        if path == "/cabinet":
            user, token, csrf = self.require_user()
            if not user:
                return
            status = STATUSES.get(user["status"], user["status"])
            fields = self.input("Имя и фамилия", "full_name", attrs=f'value="{esc(user["full_name"])}" maxlength="100"') + self.input("Университет", "university", attrs=f'value="{esc(user["university"])}" maxlength="120"') + self.input("Курс", "course", attrs=f'value="{esc(user["course"])}" maxlength="30"')
            content = f'<div class="profile-head"><div class="profile-mark">{esc(user["full_name"][:1].upper())}</div><div><p class="auth-sub">Личный кабинет студента</p><div class="status-badge status-{esc(user["status"])}">{esc(status)}</div></div></div><div class="profile-email">{esc(user["email"])}</div><h2 class="form-title">Данные профиля</h2>{self.form("/cabinet", csrf or "", fields, "Сохранить изменения")}<div class="auth-bottom"><a href="/logout">Выйти из аккаунта</a>' + (' · <a href="/admin">Открыть админ-панель</a>' if user["role"] == "admin" else "") + '</div>'
            self.page("Мой кабинет", content, csrf or "", user=user)
            return
        if path == "/admin":
            user, token, csrf = self.require_user()
            if not user:
                return
            if user["role"] != "admin":
                self.send_error(403, "Доступ только для администратора")
                return
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0].strip()[:100]
            con = db()
            if query:
                like = f"%{query}%"
                rows = con.execute("SELECT id,full_name,email,university,course,status,created_at FROM users WHERE role='student' AND (full_name LIKE ? OR email LIKE ? OR university LIKE ?) ORDER BY created_at DESC", (like, like, like)).fetchall()
            else:
                rows = con.execute("SELECT id,full_name,email,university,course,status,created_at FROM users WHERE role='student' ORDER BY created_at DESC").fetchall()
            con.close()
            parts = []
            for row in rows:
                options = ''.join(f'<option value="{s}"{" selected" if s == row["status"] else ""}>{esc(label)}</option>' for s,label in STATUSES.items())
                parts.append(f'''<tr><td><strong>{esc(row['full_name'])}</strong><small>{esc(row['email'])}</small></td><td>{esc(row['university'])}<small>{esc(row['course'])}</small></td><td>{esc(row['created_at'])}</td><td><form method="post" action="/admin/status" class="status-form"><input type="hidden" name="csrf" value="{esc(csrf)}"><input type="hidden" name="user_id" value="{row['id']}"><select name="status">{options}</select><button type="submit">Сохранить</button></form></td></tr>''')
            table = ''.join(parts) or '<tr><td colspan="4" class="empty">Студенты пока не зарегистрированы.</td></tr>'
            content = f'''<div class="admin-top"><p class="auth-sub">Реестр студентов и подтверждение участия.</p><span class="admin-count">Аккаунтов: {len(rows)}</span></div><form class="search-form" method="get" action="/admin"><input name="q" value="{esc(query)}" placeholder="Имя, email или университет"><button type="submit">Найти</button></form><div class="table-scroll"><table class="roster"><thead><tr><th>Студент</th><th>Университет / курс</th><th>Дата регистрации</th><th>Статус</th></tr></thead><tbody>{table}</tbody></table></div><p class="auth-bottom"><a href="/cabinet">← Назад в кабинет</a></p>'''
            self.page("Управление студентами", content, csrf or "", user=user, message=self.query_message())
            return
        self.send_error(404)

    def query_message(self) -> str:
        return parse_qs(urlparse(self.path).query).get("msg", [""])[0][:200]

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        form = self.read_form()
        user, _, csrf = self.session()
        if not self.csrf_ok(form, csrf):
            self.send_error(403, "Форма устарела. Обнови страницу и попробуй снова.")
            return
        if path == "/register":
            full_name = form.get("full_name", "").strip()[:100]
            email = form.get("email", "").strip().lower()[:254]
            university = form.get("university", "").strip()[:120]
            course = form.get("course", "").strip()[:30]
            password = form.get("password", "")
            if not full_name or "@" not in email or not university or not course or not password_strength(password):
                self.redirect("/register?msg=" + quote("Проверь поля и пароль: не менее 12 символов"))
                return
            con = db()
            try:
                con.execute("INSERT INTO users(full_name,email,university,course,password_hash) VALUES(?,?,?,?,?)", (full_name,email,university,course,password_record(password)))
                con.commit()
            except sqlite3.IntegrityError:
                self.redirect("/register?msg=" + quote("Этот email уже зарегистрирован"))
                return
            finally:
                con.close()
            self.redirect("/login?msg=" + quote("Аккаунт создан. Теперь можно войти."))
            return
        if path == "/login":
            email = form.get("email", "").strip().lower()[:254]
            key = f"{self.client_address[0]}:{email}"
            now = time.time()
            recent = [t for t in LOGIN_FAILURES.get(key, []) if now - t < 600]
            if len(recent) >= 8:
                self.redirect("/login?msg=" + quote("Слишком много попыток. Подожди 10 минут."))
                return
            con = db()
            record = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            con.close()
            if not record or not password_matches(form.get("password", ""), record["password_hash"]):
                recent.append(now)
                LOGIN_FAILURES[key] = recent
                self.redirect("/login?msg=" + quote("Не удалось войти. Проверь email и пароль."))
                return
            if record["status"] == "suspended":
                self.redirect("/login?msg=" + quote("Вход временно недоступен. Свяжись с AMSA Kazakhstan."))
                return
            LOGIN_FAILURES.pop(key, None)
            old = self.cookies().get(COOKIE)
            con = db()
            if old:
                con.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(old.value.encode()).hexdigest(),))
            token, new_csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            con.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires) VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), record["id"], new_csrf, int(time.time()) + SESSION_AGE))
            con.commit()
            con.close()
            self.redirect("/cabinet", {"Set-Cookie": self.cookie_header(token)})
            return
        if path == "/cabinet":
            if not user or not user["id"]:
                self.redirect("/login")
                return
            full_name = form.get("full_name", "").strip()[:100]
            university = form.get("university", "").strip()[:120]
            course = form.get("course", "").strip()[:30]
            if not full_name or not university or not course:
                self.redirect("/cabinet?msg=" + quote("Заполни все поля"))
                return
            con = db()
            con.execute("UPDATE users SET full_name=?,university=?,course=? WHERE id=? AND role='student'", (full_name,university,course,user["id"]))
            con.commit()
            con.close()
            self.redirect("/cabinet?msg=" + quote("Профиль обновлён"))
            return
        if path == "/admin/status":
            if not user or user["role"] != "admin":
                self.send_error(403)
                return
            status = form.get("status", "")
            try:
                user_id = int(form.get("user_id", ""))
            except ValueError:
                self.send_error(400)
                return
            if status not in STATUSES:
                self.send_error(400)
                return
            con = db()
            con.execute("UPDATE users SET status=? WHERE id=? AND role='student'", (status,user_id))
            if status == "suspended":
                con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            con.commit()
            con.close()
            self.redirect("/admin?msg=" + quote("Статус обновлён"))
            return
        self.send_error(404)


AUTH_CSS = """
.account-link{display:inline-flex;padding:11px 16px;background:#435dff;color:white;font-size:11px;font-weight:700;border-radius:5px}.account-link:hover{background:#3048e4;color:white}.auth-main{min-height:70vh;padding:55px 18px 90px;background:#e9edf8}.auth-wrap{width:min(560px,100%);margin:auto;background:white;padding:36px;border:1px solid #e1e5ef;box-shadow:0 20px 60px #1d2f5b0a}.auth-wrap h1{font:500 clamp(35px,6vw,48px)/1.05 Georgia,serif;letter-spacing:-.05em;margin:12px 0}.backlink{color:#435dff;font-size:11px;font-weight:700}.auth-sub{font-size:13px;color:#687282;line-height:1.7;margin:0 0 19px}.auth-form{display:grid;gap:15px}.field{display:grid;gap:7px;font-size:11px;font-weight:700;color:#303a4d}.field input{width:100%;border:1px solid #dfe3e8;padding:12px 13px;border-radius:4px;font:400 14px 'DM Sans',sans-serif;color:#10192b;background:white}.field input:focus{outline:2px solid #435dff44;border-color:#435dff}.auth-form .btn{margin-top:4px;cursor:pointer}.auth-switch,.auth-bottom{margin:18px 0 0;font-size:11px;color:#687282}.auth-switch a,.auth-bottom a{color:#435dff;font-weight:700}.notice{margin:15px 0;padding:12px;background:#e9edff;color:#25335a;font-size:12px;line-height:1.5}.profile-head{display:flex;align-items:center;gap:14px;margin:23px 0}.profile-head .auth-sub{margin:0 0 6px}.profile-mark{width:44px;height:44px;border-radius:50%;background:#d7f36a;display:grid;place-items:center;font:700 19px Manrope,sans-serif}.status-badge{display:inline-block;font-size:10px;font-weight:700;padding:5px 8px;border-radius:20px;background:#f2edce;color:#655618}.status-active{background:#e0f2e8;color:#176b4b}.status-suspended{background:#fae4e4;color:#9b3232}.profile-email{font-size:12px;color:#687282;border-bottom:1px solid #dfe3e8;padding-bottom:20px;margin-bottom:23px}.form-title{font:500 21px Georgia,serif;margin:0 0 14px}.auth-bottom{padding-top:16px;border-top:1px solid #dfe3e8}.admin-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.admin-count{font-size:10px;font-weight:700;color:#435dff}.search-form{display:flex;gap:8px;margin:20px 0}.search-form input{min-width:0;flex:1;padding:11px;border:1px solid #dfe3e8;font:12px 'DM Sans',sans-serif}.search-form button,.status-form button{border:0;background:#10192b;color:white;padding:0 14px;font-size:10px;font-weight:700;cursor:pointer}.table-scroll{overflow:auto}.roster{border-collapse:collapse;width:100%;font-size:11px}.roster th{text-align:left;color:#687282;font-size:9px;text-transform:uppercase;letter-spacing:.07em;padding:11px 8px;border-bottom:1px solid #dfe3e8}.roster td{padding:13px 8px;border-bottom:1px solid #edf0f3;vertical-align:top}.roster td strong,.roster td small{display:block}.roster td small{font-size:10px;color:#687282;margin-top:4px}.status-form{display:flex;gap:5px}.status-form select{max-width:130px;font-size:10px;padding:6px;border:1px solid #dfe3e8}.status-form button{padding:7px}.empty{text-align:center;color:#687282;padding:28px!important}
@media(max-width:650px){.account-link{font-size:10px;padding:9px 11px}.auth-main{padding:30px 14px 65px}.auth-wrap{padding:24px 20px}.admin-top{align-items:flex-start;flex-direction:column}.roster{min-width:600px}}
"""


def create_admin(email: str, name: str) -> None:
    password = __import__("getpass").getpass("Admin password (12+ characters): ")
    if not password_strength(password):
        raise SystemExit("Password must be 12-200 characters.")
    con = db()
    try:
        con.execute("INSERT INTO users(full_name,email,university,course,password_hash,role,status) VALUES(?,?,?,?,?,'admin','active')", (name.strip(), email.strip().lower(), "AMSA Kazakhstan", "Администратор", password_record(password)))
        con.commit()
    except sqlite3.IntegrityError:
        raise SystemExit("That email already exists.")
    finally:
        con.close()
    print(f"Admin account created for {email.strip().lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local AMSA Kazakhstan portal")
    sub = parser.add_subparsers(dest="command")
    admin = sub.add_parser("create-admin", help="create the first administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.email, args.name)
        return
    host = os.environ.get("AMSA_HOST", "127.0.0.1")
    port = int(os.environ.get("AMSA_PORT", "8000"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
