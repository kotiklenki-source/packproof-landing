import os
import re
import json
import time
import urllib.request
import urllib.error
from collections import defaultdict

import bcrypt
from fastapi import APIRouter, Request, Form, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app import get_db, User, SECRET_KEY, APP_BASE_URL

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@packproof.de").lower()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
MAIL_FROM = os.getenv("MAIL_FROM", "PackProof Inspection <noreply@packproofinspection.com>")

reset_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="packproof-password-reset")

DISPOSABLE_DOMAINS = {
    "tempmail.com", "temp-mail.org", "10minutemail.com", "guerrillamail.com",
    "mailinator.com", "yopmail.com", "trashmail.com", "getnada.com",
    "dispostable.com", "mohmal.com", "fakeinbox.com", "maildrop.cc",
    "tempail.com", "emailondeck.com", "sharklasers.com", "trash-mail.com",
    "byom.de", "wegwerfemail.de", "muellmail.com", "spoofmail.de",
}

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, request: Request, scope: str) -> bool:
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )
        key = f"{scope}:{client_ip}"
        now = time.monotonic()

        self.hits[key] = [t for t in self.hits[key] if now - t < self.window_seconds]
        if len(self.hits[key]) >= self.max_requests:
            return False

        self.hits[key].append(now)
        return True


login_limiter = InMemoryRateLimiter(max_requests=10, window_seconds=600)      # 10 попыток за 10 мин
register_limiter = InMemoryRateLimiter(max_requests=3, window_seconds=3600)   # 3 регистрации в час с IP
forgot_limiter = InMemoryRateLimiter(max_requests=3, window_seconds=900)      # 3 запроса сброса за 15 мин


def normalize_email(raw_email: str) -> tuple[str | None, str | None]:
    clean = raw_email.strip().lower()
    if not EMAIL_REGEX.match(clean):
        return None, "Bitte geben Sie eine gültige E-Mail-Adresse ein."

    local_part, domain = clean.rsplit("@", 1)
    if domain in DISPOSABLE_DOMAINS:
        return None, "Wegwerf-E-Mail-Adressen sind für die Registrierung nicht zugelassen."

    if domain in {"gmail.com", "googlemail.com"} and "+" in local_part:
        local_part = local_part.split("+", 1)[0]

    return f"{local_part}@{domain}", None


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ==========================================
# ОТПРАВКА ПИСЕМ (RESEND HTTPS API)
# ==========================================
def send_email_via_resend(to_email: str, subject: str, html_body: str) -> None:
    if not RESEND_API_KEY:
        print(f"[MAIL SKIPPED] RESEND_API_KEY fehlt. Empfänger: {to_email} | Betreff: {subject}")
        return

    payload = json.dumps({
        "from": MAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "PackProofInspection/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[MAIL ERROR] HTTP {e.code} an {to_email}: {err_body}")
    except Exception as e:
        print(f"[MAIL ERROR] Fehler an {to_email}: {e}")


def send_welcome_email(to_email: str) -> None:
    dashboard_url = f"{APP_BASE_URL.rstrip('/')}/app"
    html = f"""
    <div style="font-family: Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 32px; border-radius: 12px; max-width: 520px; margin: 0 auto;">
        <h2 style="color: #38bdf8; margin-top: 0;">Willkommen bei PackProof Inspection!</h2>
        <p style="color: #cbd5e1; line-height: 1.5;">
            Ihr Konto (<b>{to_email}</b>) wurde erfolgreich erstellt. Sie können ab sofort rechtssichere Verpackungs- und Auspacknachweise mit SHA-256-Versiegelung erstellen.
        </p>
        <div style="background-color: #1e293b; border: 1px solid #334155; padding: 14px; border-radius: 8px; margin: 20px 0; font-size: 14px; color: #cbd5e1;">
            <b>Ihr aktueller Tarif:</b> Free (bis zu 10 Prüfungen/Monat, 30 Tage Speicherdauer)
        </div>
        <a href="{dashboard_url}" style="display: inline-block; background-color: #22c55e; color: #0f172a; font-weight: bold; text-decoration: none; padding: 12px 22px; border-radius: 8px;">
            Zum Dashboard öffnen →
        </a>
        <p style="font-size: 12px; color: #64748b; margin-top: 28px;">
            PackProof Inspection · Visuelle Verpackungssicherung
        </p>
    </div>
    """
    send_email_via_resend(to_email, "Willkommen bei PackProof Inspection", html)


def send_reset_email(to_email: str, reset_url: str) -> None:
    html = f"""
    <div style="font-family: Arial, sans-serif; background-color: #0f172a; color: #f8fafc; padding: 32px; border-radius: 12px; max-width: 520px; margin: 0 auto;">
        <h2 style="color: #38bdf8; margin-top: 0;">Passwort zurücksetzen</h2>
        <p style="color: #cbd5e1; line-height: 1.5;">
            Wir haben eine Anfrage zum Zurücksetzen des Passworts für Ihr Konto (<b>{to_email}</b>) erhalten.
        </p>
        <a href="{reset_url}" style="display: inline-block; background-color: #6366f1; color: #ffffff; font-weight: bold; text-decoration: none; padding: 12px 22px; border-radius: 8px; margin: 16px 0;">
            Neues Passwort festlegen
        </a>
        <p style="font-size: 13px; color: #94a3b8; line-height: 1.5;">
            Dieser Link ist <b>30 Minuten</b> gültig und kann nur einmal verwendet werden. Falls Sie diese Anfrage nicht gestellt haben, können Sie diese E-Mail ignorieren.
        </p>
    </div>
    """
    send_email_via_resend(to_email, "PackProof — Passwort zurücksetzen", html)


def create_reset_token(user: User) -> str:
    return reset_serializer.dumps({"uid": user.id, "ph": user.password_hash[:12]})


def verify_reset_token(token: str, db: Session) -> User | None:
    try:
        data = reset_serializer.loads(token, max_age=1800)
    except (BadSignature, SignatureExpired):
        return None
    user = db.query(User).filter(User.id == data.get("uid")).first()
    if not user or user.password_hash[:12] != data.get("ph"):
        return None
    return user


# ==========================================
# ANMELDUNG & REGISTRIERUNG (/login)
# ==========================================
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None, success: str = None):
    if request.session.get("user_id"):
        return RedirectResponse(url="/app", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "success": success},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not login_limiter.is_allowed(request, "login"):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Zu viele Anmeldeversuche. Bitte warten Sie einige Minuten."},
            status_code=429,
        )

    email_clean, err = normalize_email(email)
    if err:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": err},
            status_code=400,
        )

    user = db.query(User).filter(User.email == email_clean).first()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Ungültige E-Mail-Adresse oder falsches Passwort."},
            status_code=400,
        )

    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["is_admin"] = user.is_admin

    if user.is_admin:
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/app", status_code=303)


@router.post("/register")
async def register_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not register_limiter.is_allowed(request, "register"):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Zu viele Registrierungen von dieser IP-Adresse. Bitte versuchen Sie es später erneut."},
            status_code=429,
        )

    email_clean, err = normalize_email(email)
    if err:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": err},
            status_code=400,
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Das Passwort muss mindestens 6 Zeichen lang sein."},
            status_code=400,
        )

    existing = db.query(User).filter(User.email == email_clean).first()
    if existing:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Ein Benutzer mit dieser E-Mail-Adresse existiert bereits."},
            status_code=400,
        )

    is_first_user = db.query(User).count() == 0
    is_admin = (email_clean == ADMIN_EMAIL) or is_first_user

    new_user = User(
        email=email_clean,
        password_hash=hash_password(password),
        is_admin=is_admin,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    background_tasks.add_task(send_welcome_email, new_user.email)

    request.session["user_id"] = new_user.id
    request.session["user_email"] = new_user.email
    request.session["is_admin"] = new_user.is_admin

    if is_admin:
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/app", status_code=303)


# ==========================================
# PASSWORT ZURÜCKSETZEN (/forgot-password)
# ==========================================
@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"mode": "request"},
    )


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    if not forgot_limiter.is_allowed(request, "forgot"):
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "mode": "request",
                "error": "Zu viele Anfragen. Bitte warten Sie 15 Minuten und versuchen Sie es erneut.",
            },
            status_code=429,
        )

    email_clean, err = normalize_email(email)
    if err:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"mode": "request", "error": err},
            status_code=400,
        )

    user = db.query(User).filter(User.email == email_clean).first()
    if user:
        token = create_reset_token(user)
        reset_url = f"{APP_BASE_URL.rstrip('/')}/reset-password?token={token}"
        background_tasks.add_task(send_reset_email, user.email, reset_url)

    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={
            "mode": "request",
            "success": "Falls ein Konto mit dieser E-Mail-Adresse existiert, wurde ein Link zum Zurücksetzen gesendet.",
        },
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    user = verify_reset_token(token, db)
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "mode": "request",
                "error": "Dieser Link ist ungültig oder abgelaufen. Bitte fordern Sie einen neuen Link an.",
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"mode": "reset", "token": token, "email": user.email},
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = verify_reset_token(token, db)
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "mode": "request",
                "error": "Dieser Link ist ungültig oder abgelaufen. Bitte fordern Sie einen neuen Link an.",
            },
            status_code=400,
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "mode": "reset",
                "token": token,
                "email": user.email,
                "error": "Das neue Passwort muss mindestens 6 Zeichen lang sein.",
            },
            status_code=400,
        )

    user.password_hash = hash_password(password)
    db.commit()

    return RedirectResponse(
        url="/login?success=Ihr+Passwort+wurde+erfolgreich+ge%C3%A4ndert.+Bitte+melden+Sie+sich+an.",
        status_code=303,
    )


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)