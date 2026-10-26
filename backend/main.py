import os
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="PackProof Inspection API", version="1.0.0")

# --- Определяем пути к директориям ---
# BASE_DIR указывает на корень проекта (папку packproof-landing)
BASE_DIR = Path(__file__).resolve().parent.parent
VIDEOS_DIR = BASE_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "packproof.db"

# --- Инициализация базы данных SQLite ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            video_filename TEXT NOT NULL,
            sha256_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 1. МАРШРУТЫ ДЛЯ ИКОНОК И WEB MANIFEST (FAVICON)
# ==========================================

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    file_path = BASE_DIR / "favicon.ico"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon():
    file_path = BASE_DIR / "apple-touch-icon.png"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/favicon-32x32.png", include_in_schema=False)
async def favicon_32():
    file_path = BASE_DIR / "favicon-32x32.png"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/favicon-16x16.png", include_in_schema=False)
async def favicon_16():
    file_path = BASE_DIR / "favicon-16x16.png"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/site.webmanifest", include_in_schema=False)
async def webmanifest():
    file_path = BASE_DIR / "site.webmanifest"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Manifest not found")

@app.get("/android-chrome-192x192.png", include_in_schema=False)
async def android_192():
    file_path = BASE_DIR / "android-chrome-192x192.png"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/android-chrome-512x512.png", include_in_schema=False)
async def android_512():
    file_path = BASE_DIR / "android-chrome-512x512.png"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Icon not found")

# ==========================================
# 2. МАРШРУТЫ HTML-СТРАНИЦ (FRONTEND)
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def get_landing():
    index_path = BASE_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(content="<h1>PackProof Landing Page</h1>", status_code=200)

@app.get("/app", response_class=HTMLResponse)
async def get_app():
    app_path = BASE_DIR / "app.html"
    if app_path.exists():
        return FileResponse(app_path)
    return HTMLResponse(content="<h1>PackProof Recorder App</h1>", status_code=200)

@app.get("/verify", response_class=HTMLResponse)
async def get_verify_page():
    verify_path = BASE_DIR / "verify.html"
    if verify_path.exists():
        return FileResponse(verify_path)
    return HTMLResponse(content="<h1>PackProof Verification Portal</h1>", status_code=200)

@app.get("/impressum", response_class=HTMLResponse)
async def get_impressum():
    path = BASE_DIR / "impressum.html"
    if path.exists():
        return FileResponse(path)
    return HTMLResponse(content="<h1>Impressum</h1>", status_code=200)

@app.get("/datenschutz", response_class=HTMLResponse)
async def get_datenschutz():
    path = BASE_DIR / "datenschutz.html"
    if path.exists():
        return FileResponse(path)
    return HTMLResponse(content="<h1>Datenschutz</h1>", status_code=200)

# ==========================================
# 3. API ЭНДПОИНТЫ (BACKEND)
# ==========================================

@app.post("/api/upload")
async def upload_video(
    order_id: str = Form(...),
    video: UploadFile = File(...)
):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_extension = Path(video.filename).suffix or ".webm"
    safe_order_id = "".join(c for c in order_id if c.isalnum() or c in ("-", "_"))
    filename = f"{safe_order_id}_{timestamp}{file_extension}"
    file_path = VIDEOS_DIR / filename

    content = await video.read()
    
    # Расчет SHA-256 хеша на бэкенде для двойной сверки
    sha256_hash = hashlib.sha256(content).hexdigest()

    with open(file_path, "wb") as f:
        f.write(content)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO packages (order_id, video_filename, sha256_hash) VALUES (?, ?, ?)",
        (safe_order_id, filename, sha256_hash)
    )
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "success",
        "order_id": safe_order_id,
        "filename": filename,
        "sha256_hash": sha256_hash,
        "verify_url": f"/verify?order={safe_order_id}"
    })

@app.get("/api/verify/{order_id}")
async def verify_order(order_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT order_id, video_filename, sha256_hash, created_at FROM packages WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Order ID not found")

    return JSONResponse({
        "status": "verified",
        "order_id": row[0],
        "video_url": f"/videos/{row[1]}",
        "sha256_hash": row[2],
        "created_at": row[3]
    })

@app.get("/videos/{filename}")
async def get_video(filename: str):
    video_path = VIDEOS_DIR / filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(video_path, media_type="video/webm")