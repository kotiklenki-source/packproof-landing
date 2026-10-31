import os
import sqlite3
import json
import hashlib
import qrcode
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="PackProof Inspection API")

# 1. Определение путей к папкам
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))

# Если файлы видео сохраняются в uploaded_videos
UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploaded_videos")
if not os.path.exists(UPLOAD_DIR):
    UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DB_PATH = os.path.join(PROJECT_ROOT, "packproof.db")
JSON_DB_PATH = os.path.join(PROJECT_ROOT, "db.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация SQLite базы данных
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Вспомогательное хранилище в памяти
video_db = {}

def load_json_db():
    if os.path.exists(JSON_DB_PATH):
        try:
            with open(JSON_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json_db(data):
    with open(JSON_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

video_db = load_json_db()


# --- Маршруты для страниц ---

@app.get("/")
async def read_root():
    return FileResponse(os.path.join(PROJECT_ROOT, "index.html"))

@app.get("/app")
async def read_app():
    return FileResponse(os.path.join(PROJECT_ROOT, "app.html"))

@app.get("/verify")
async def read_verify():
    return FileResponse(os.path.join(PROJECT_ROOT, "verify.html"))


# --- Бэкенд API ---

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...), tracking_id: str = Form(...)):
    try:
        contents = await file.read()
        sha256_hash = hashlib.sha256(contents).hexdigest()
        file_extension = os.path.splitext(file.filename)[1] or ".webm"
        saved_filename = f"{tracking_id}_{sha256_hash[:8]}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, saved_filename)
        
        with open(file_path, "wb") as f:
            f.write(contents)
            
        # Сохранение в SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO videos (tracking_id, filename, sha256) VALUES (?, ?, ?)",
            (tracking_id, saved_filename, sha256_hash)
        )
        conn.commit()
        conn.close()

        # Дублирование в JSON
        video_info = {
            "tracking_id": tracking_id,
            "filename": saved_filename,
            "sha256": sha256_hash
        }
        video_db[sha256_hash] = video_info
        video_db[tracking_id] = video_info
        save_json_db(video_db)
        
        return {
            "status": "success",
            "message": "Video uploaded and verified successfully",
            "tracking_id": tracking_id,
            "sha256": sha256_hash
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/qr/{tracking_id}")
async def generate_qr(tracking_id: str):
    verify_url = f"https://packproofinspection.com/verify?id={tracking_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(verify_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    img_byte_arr = BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return StreamingResponse(img_byte_arr, media_type="image/png")


@app.get("/api/verify/{identifier}")
async def verify_video(identifier: str):
    # 1. Поиск в SQLite
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tracking_id, filename, sha256 FROM videos WHERE tracking_id = ? OR sha256 = ?",
        (identifier, identifier)
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            "status": "verified",
            "tracking_id": row[0],
            "sha256": row[2],
            "video_url": f"/uploads/{row[1]}"
        }

    # 2. Фолбэк на JSON/память
    video_info = video_db.get(identifier)
    if video_info:
        return {
            "status": "verified",
            "tracking_id": video_info["tracking_id"],
            "sha256": video_info["sha256"],
            "video_url": f"/uploads/{video_info['filename']}"
        }

    raise HTTPException(status_code=404, detail="Proof record not found")


# --- Монтирование папок со статикой ---

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")