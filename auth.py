import os
import re
import time
from collections import defaultdict
import bcrypt
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import get_db, User

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@packproof.de").lower()

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


login_limiter = InMemoryRateLimiter(max_requests=10, window_seconds=600)     # 10 попыток за 10 мин
register_limiter = InMemoryRateLimiter(max_requests=3, window_seconds=3600)  # 3 регистрации в час с IP


def normalize_email(raw_email: str) -> tuple[str | None, str | None]:
    """Возвращает (нормализованный_email, текст_ошибки_на_немецком)."""
    clean = raw_email.strip().lower()
    if not EMAIL_REGEX.match(clean):
        return None, "Bitte geben Sie eine gültige E-Mail-Adresse ein."

    local_part, domain = clean.rsplit("@", 1)
    if domain in DISPOSABLE_DOMAINS:
        return None, "Wegwerf-E-Mail-Adressen sind für die Registrierung nicht zugelassen."

    # Защита от бесконечных клонов через "+alias" в Gmail
    if domain in {"gmail.com", "googlemail.com"} and "+" in local_part:
        local_part = local_part.split("+", 1)[0]

    return f"{local_part}@{domain}", None


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


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

    request.session["user_id"] = new_user.id
    request.session["user_email"] = new_user.email
    request.session["is_admin"] = new_user.is_admin

    if is_admin:
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/app", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)