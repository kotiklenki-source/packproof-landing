import os
import re
import io
import uuid
import hashlib
from datetime import datetime
from typing import Optional

import boto3
import qrcode
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# 1. НАСТРОЙКИ И ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (.env)
# ==========================================
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-packproof-key-change-in-prod")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./temp_local.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "packproof-videos")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://packproofinspection.com")

# ==========================================
# 2. БАЗА ДАННЫХ (SQLAlchemy)
# ==========================================
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=280)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(150), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    plan = Column(String(50), default="Free")
    created_at = Column(DateTime, default=datetime.utcnow)


class Inspection(Base):
    __tablename__ = "inspections"
    id = Column(Integer, primary_key=True, index=True)
    tracking_id = Column(String(100), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    secret_pin = Column(String(20), nullable=False)
    packing_video_url = Column(Text, nullable=True)
    packing_sha256 = Column(String(64), nullable=True)
    unboxing_video_url = Column(Text, nullable=True)
    unboxing_sha256 = Column(String(64), nullable=True)
    status = Column(String(50), default="verified")  # verified, unboxing_started, unboxed, disputed
    dispute_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# 3. ИНИЦИАЛИЗАЦИЯ FASTAPI
# ==========================================
app = FastAPI(title="PackProof Inspection", version="2.2.0")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

os.makedirs("videos", exist_ok=True)
os.makedirs("videos/tmp", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/videos", StaticFiles(directory="videos"), name="videos")
templates = Jinja2Templates(directory="templates")

try:
    import auth
    if hasattr(auth, "router"):
        app.include_router(auth.router)
except ImportError:
    pass


# ==========================================
# 4. ХЕЛПЕРЫ (ТАРИФЫ + ТРЕКИ + R2 + SHA-256)
# ==========================================
PLANS_CONFIG = {
    "free": {"label": "Free", "monthly_limit": 10, "r2_prefix": "free"},
    "pro": {"label": "Pro", "monthly_limit": 150, "r2_prefix": "pro"},
    "enterprise": {"label": "Enterprise", "monthly_limit": 2000, "r2_prefix": "enterprise"},
}

CARRIER_PATTERNS = [
    ("DHL", re.compile(r"^(\d{12}|\d{20}|[A-Z]{2}\d{9}[A-Z]{2})$")),
    ("UPS", re.compile(r"^1Z[A-Z0-9]{16}$")),
    ("DPD", re.compile(r"^\d{14}$")),
    ("Hermes", re.compile(r"^\d{11,14}$")),
    ("GLS", re.compile(r"^\d{11,12}$")),
]
ORDER_ID_PATTERN = re.compile(r"^[A-Z0-9\-_]{5,32}$")


def get_user_plan_config(user: Optional[User]) -> dict:
    if user and user.is_admin:
        return {"label": "Administrator", "monthly_limit": 999999, "r2_prefix": "admin"}
    plan_key = (user.plan if user and user.plan else "free").strip().lower()
    return PLANS_CONFIG.get(plan_key, PLANS_CONFIG["free"])


def validate_tracking_id(raw_code: str, is_order_id: bool = False) -> str:
    if not raw_code or not raw_code.strip():
        raise HTTPException(status_code=400, detail="Sendungsnummer erforderlich.")

    cleaned = re.sub(r"[\s\-]+", "", raw_code.strip().upper())
    if len(set(cleaned)) < 3:
        raise HTTPException(status_code=400, detail="Ungültige Nummer: Bitte geben Sie eine echte Sendungsnummer ein.")

    if is_order_id:
        order_cleaned = raw_code.strip().upper()
        if not ORDER_ID_PATTERN.match(order_cleaned):
            raise HTTPException(
                status_code=400,
                detail="Auftragsnummer muss 5–32 Zeichen lang sein (Buchstaben, Zahlen, Bindestrich).",
            )
        return order_cleaned

    for _, pattern in CARRIER_PATTERNS:
        if pattern.match(cleaned):
            return cleaned

    raise HTTPException(
        status_code=400,
        detail="Unbekanntes Format (erlaubt: DHL, Hermes, DPD, GLS, UPS) oder 'Auftragsnummer' wählen.",
    )


def upload_bytes_to_storage(file_bytes: bytes, filename: str, content_type: str) -> str:
    if R2_ACCOUNT_ID and R2_ACCESS_KEY and R2_SECRET_KEY and R2_PUBLIC_URL:
        s3 = boto3.client(
            service_name="s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
        )
        s3.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=filename,
            Body=file_bytes,
            ContentType=content_type,
        )
        return f"{R2_PUBLIC_URL.rstrip('/')}/{filename}"
    else:
        local_path = os.path.join("videos", filename)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(file_bytes)
        return f"/videos/{filename}"


def safe_tmp_path(tracking_id: str) -> str:
    safe_name = hashlib.sha256(tracking_id.strip().encode("utf-8")).hexdigest()[:24]
    return os.path.join("videos", "tmp", f"unbox_{safe_name}.webm")


def get_uploaded_chunk_count(tracking_id: str) -> int:
    meta_path = safe_tmp_path(tracking_id) + ".meta"
    if not os.path.exists(meta_path):
        return 0
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def set_uploaded_chunk_count(tracking_id: str, count: int) -> None:
    meta_path = safe_tmp_path(tracking_id) + ".meta"
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(str(count))


# ==========================================
# 5. МАРШРУТЫ СТРАНИЦ И API
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/app", response_class=HTMLResponse)
async def user_dashboard(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    inspections = (
        db.query(Inspection)
        .filter(Inspection.user_id == user.id)
        .order_by(Inspection.created_at.desc())
        .all()
    )
    role_label = "Administrator" if user.is_admin else user.plan
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "email": user.email,
            "role": role_label,
            "is_admin": user.is_admin,
            "inspections": inspections,
        },
    )


@app.get("/inspection", response_class=HTMLResponse)
async def inspection_page(request: Request):
    return templates.TemplateResponse(request=request, name="inspection.html")


@app.post("/api/upload")
async def upload_packing_video(
    request: Request,
    file: UploadFile = File(...),
    tracking_id: str = Form(...),
    secret_pin: str = Form(...),
    is_order_id: bool = Form(False),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Bitte melden Sie sich an, um eine Prüfung zu erstellen.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Benutzer nicht gefunden.")

    clean_id = validate_tracking_id(tracking_id, is_order_id=is_order_id)

    existing = db.query(Inspection).filter(Inspection.tracking_id == clean_id).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Für diese Sendungs-ID existiert bereits ein versiegelter Verpackungsnachweis.",
        )

    plan_cfg = get_user_plan_config(user)
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_used = (
        db.query(Inspection)
        .filter(Inspection.user_id == user.id, Inspection.created_at >= month_start)
        .count()
    )
    if monthly_used >= plan_cfg["monthly_limit"]:
        raise HTTPException(
            status_code=403,
            detail=f"Monatslimit erreicht ({plan_cfg['monthly_limit']} Prüfungen im Tarif {plan_cfg['label']}).",
        )

    video_bytes = await file.read()
    sha256_hash = hashlib.sha256(video_bytes).hexdigest()
    filename = f"{plan_cfg['r2_prefix']}/pack_{clean_id}_{uuid.uuid4().hex[:6]}.webm"
    video_url = upload_bytes_to_storage(video_bytes, filename, file.content_type or "video/webm")

    new_item = Inspection(
        tracking_id=clean_id,
        user_id=user.id,
        secret_pin=secret_pin.strip(),
        packing_video_url=video_url,
        packing_sha256=sha256_hash,
        status="verified",
    )
    db.add(new_item)
    db.commit()

    return JSONResponse({"success": True, "tracking_id": clean_id, "sha256": sha256_hash})

@app.get("/api/qr/{tracking_id}")
async def get_qr_image(tracking_id: str):
    verify_url = f"{APP_BASE_URL.rstrip('/')}/verify?id={tracking_id}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=2)
    qr.add_data(verify_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/api/verify/{tracking_id}")
async def get_verify_data(tracking_id: str, db: Session = Depends(get_db)):
    clean_id = tracking_id.strip()
    inspection = db.query(Inspection).filter(Inspection.tracking_id == clean_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Sendung nicht gefunden")

    tmp_file = safe_tmp_path(clean_id)
    partial_url = None
    if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 0 and not inspection.unboxing_video_url:
        partial_url = f"/videos/tmp/{os.path.basename(tmp_file)}"

    return {
        "tracking_id": inspection.tracking_id,
        "video_url": inspection.packing_video_url,
        "sha256": inspection.packing_sha256,
        "unboxing_video_url": inspection.unboxing_video_url,
        "unboxing_sha256": inspection.unboxing_sha256,
        "status": inspection.status,
        "uploaded_chunks": get_uploaded_chunk_count(clean_id),
        "partial_video_url": partial_url,
    }


@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, id: Optional[str] = None, db: Session = Depends(get_db)):
    inspection = None
    if id:
        inspection = db.query(Inspection).filter(Inspection.tracking_id == id.strip()).first()
    return templates.TemplateResponse(
        request=request,
        name="verify.html",
        context={"tracking_id": id or "", "inspection": inspection},
    )


@app.post("/api/start-unboxing")
async def start_unboxing_attempt(
    tracking_id: str = Form(...),
    secret_pin: str = Form(...),
    db: Session = Depends(get_db),
):
    clean_id = tracking_id.strip()
    inspection = db.query(Inspection).filter(Inspection.tracking_id == clean_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Sendung nicht gefunden")
    if inspection.secret_pin != secret_pin.strip():
        raise HTTPException(status_code=403, detail="Falscher Sicherheits-PIN")
    if inspection.unboxing_video_url or inspection.status in ("unboxing_started", "unboxed", "disputed"):
        raise HTTPException(
            status_code=409,
            detail="Ihre einzige Aufnahme-Berechtigung für diese Sendung wurde bereits verbraucht.",
        )

    inspection.status = "unboxing_started"
    db.commit()
    set_uploaded_chunk_count(clean_id, 0)
    return JSONResponse({"success": True})


@app.post("/api/upload-unboxing-chunk")
async def upload_unboxing_chunk(
    tracking_id: str = Form(...),
    secret_pin: str = Form(...),
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    clean_id = tracking_id.strip()
    inspection = db.query(Inspection).filter(Inspection.tracking_id == clean_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Sendung nicht gefunden")
    if inspection.secret_pin != secret_pin.strip():
        raise HTTPException(status_code=403, detail="Falscher Sicherheits-PIN")
    if inspection.unboxing_video_url or inspection.status in ("unboxed", "disputed"):
        raise HTTPException(
            status_code=409,
            detail="Das Auspack-Video wurde bereits versiegelt.",
        )

    current_count = get_uploaded_chunk_count(clean_id)
    if chunk_index < current_count:
        return JSONResponse({"success": True, "uploaded_chunks": current_count, "duplicate": True})

    if chunk_index > current_count:
        raise HTTPException(
            status_code=409,
            detail=f"Erwarteter Chunk {current_count}, aber {chunk_index} erhalten.",
        )

    chunk_bytes = await file.read()
    tmp_file = safe_tmp_path(clean_id)
    with open(tmp_file, "ab") as f:
        f.write(chunk_bytes)

    new_count = current_count + 1
    set_uploaded_chunk_count(clean_id, new_count)
    return JSONResponse({"success": True, "uploaded_chunks": new_count})


@app.post("/api/finalize-unboxing")
async def finalize_unboxing_video(
    tracking_id: str = Form(...),
    secret_pin: str = Form(...),
    outcome: str = Form("accepted"),  # "accepted" oder "disputed"
    dispute_note: str = Form(""),
    db: Session = Depends(get_db),
):
    clean_id = tracking_id.strip()
    inspection = db.query(Inspection).filter(Inspection.tracking_id == clean_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Sendung nicht gefunden")
    if inspection.secret_pin != secret_pin.strip():
        raise HTTPException(status_code=403, detail="Falscher Sicherheits-PIN")
    if inspection.unboxing_video_url or inspection.status in ("unboxed", "disputed"):
        raise HTTPException(
            status_code=409,
            detail="Das Auspack-Video wurde bereits versiegelt.",
        )

    if outcome == "disputed" and not dispute_note.strip():
        raise HTTPException(
            status_code=400,
            detail="Bitte geben Sie eine Begründung für die Reklamation an.",
        )

    tmp_file = safe_tmp_path(clean_id)
    if not os.path.exists(tmp_file) or os.path.getsize(tmp_file) == 0:
        raise HTTPException(
            status_code=400,
            detail="Keine aufgezeichneten Videodaten auf dem Server gefunden.",
        )

    with open(tmp_file, "rb") as f:
        full_video_bytes = f.read()

    sha256_hash = hashlib.sha256(full_video_bytes).hexdigest()
    owner = db.query(User).filter(User.id == inspection.user_id).first() if inspection.user_id else None
    plan_cfg = get_user_plan_config(owner)
    filename = f"{plan_cfg['r2_prefix']}/unbox_{clean_id}_{uuid.uuid4().hex[:6]}.webm"
    video_url = upload_bytes_to_storage(full_video_bytes, filename, "video/webm")

    inspection.unboxing_video_url = video_url
    inspection.unboxing_sha256 = sha256_hash

    if outcome == "disputed":
        inspection.status = "disputed"
        inspection.dispute_note = dispute_note.strip()
    else:
        inspection.status = "unboxed"

    db.commit()

    # Удаляем временные файлы после успешной герметизации
    try:
        os.remove(tmp_file)
    except OSError:
        pass
    try:
        os.remove(tmp_file + ".meta")
    except OSError:
        pass

    return JSONResponse({
        "success": True,
        "status": inspection.status,
        "report_url": f"/report/{inspection.tracking_id}",
    })


@app.post("/api/dispute")
async def open_dispute(
    request: Request,
    tracking_id: str = Form(...),
    description: str = Form(...),
    db: Session = Depends(get_db),
):
    inspection = db.query(Inspection).filter(Inspection.tracking_id == tracking_id.strip()).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Sendung nicht gefunden")
    inspection.status = "disputed"
    inspection.dispute_note = description.strip()
    db.commit()
    return RedirectResponse(url="/app", status_code=303)


@app.get("/report/{tracking_id}", response_class=HTMLResponse)
async def report_page(request: Request, tracking_id: str, db: Session = Depends(get_db)):
    inspection = db.query(Inspection).filter(Inspection.tracking_id == tracking_id.strip()).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Bericht nicht gefunden")
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={"inspection": inspection},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/login", status_code=303)
    all_inspections = db.query(Inspection).order_by(Inspection.created_at.desc()).all()
    all_users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"inspections": all_inspections, "users": all_users},
    )


@app.post("/admin/status/{inspection_id}")
async def admin_change_status(
    request: Request,
    inspection_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Zugriff verweigert")
    insp = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if insp:
        insp.status = status
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/impressum.html", response_class=HTMLResponse)
@app.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request):
    return templates.TemplateResponse(request=request, name="impressum.html")


@app.get("/datenschutz.html", response_class=HTMLResponse)
@app.get("/datenschutz", response_class=HTMLResponse)
async def datenschutz(request: Request):
    return templates.TemplateResponse(request=request, name="datenschutz.html")