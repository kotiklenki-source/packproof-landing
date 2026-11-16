import os
import bcrypt
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import get_db, User

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@packproof.de").lower()


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
    email_clean = email.strip().lower()
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
    email_clean = email.strip().lower()
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