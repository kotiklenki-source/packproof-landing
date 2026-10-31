import os
import sqlite3
import json
import hashlib
import qrcode
import boto3
from io import BytesIO
from botocore.config import Config
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="PackProof Inspection API")

# --- НАСТРОЙКИ CLOUDFLARE R2 ---
R2_ACCESS_KEY_ID = "54340d792c9c2fccfa2128602ef9bd8e"
R2_SECRET_ACCESS_KEY = "46f9c14bc015a11189d2a342dna10299983b3e1076uf30c85c770dbffa340cf8"
R2_ENDPOINT_URL = "https://1ff6eaee49a620894ec41d70b1840a85.r2.cloudflarestorage.com"
R2_BUCKET_NAME = "packproof-videos"

# Клиент S3 / R2
s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version='s3v4'),
    region_name='auto'
)

#Пути к локальной БД (для индексации)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "packproof.db")
JSON_DB_PATH = os.path.join(PROJECT_ROOT, "db.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id TEXT NOT NULL,
            r2_key TEXT NOT NULL,
            sha256 TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

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


# --- Маршруты страниц ---

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
        file_ext = os.path.splitext(file.filename)[1] or ".webm"
        r2_key = f"{tracking_id}_{sha256_hash[:8]}{file_ext}"

        # Загрузка видео напрямую в бакет Cloudflare R2
        s3_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=r2_key,
            Body=contents,
            ContentType=file.content_type or "video/webm"
        )

        # Сохранение записи в SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO videos (tracking_id, r2_key, sha256) VALUES (?, ?, ?)",
            (tracking_id, r2_key, sha256_hash)
        )
        conn.commit()
        conn.close()

        # Дублирование в JSON
        video_info = {
            "tracking_id": tracking_id,
            "r2_key": r2_key,
            "sha256": sha256_hash
        }
        video_db[sha256_hash] = video_info
        video_db[tracking_id] = video_info
        save_json_db(video_db)

        return {
            "status": "success",
            "message": "Video uploaded to R2 successfully",
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
    r2_key = None
    tracking_id = identifier
    sha256_hash = ""

    # Поиск записи в БД
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tracking_id, r2_key, sha256 FROM videos WHERE tracking_id = ? OR sha256 = ?",
        (identifier, identifier)
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        tracking_id, r2_key, sha256_hash = row[0], row[1], row[2]
    else:
        video_info = video_db.get(identifier)
        if video_info:
            tracking_id = video_info["tracking_id"]
            r2_key = video_info["r2_key"]
            sha256_hash = video_info["sha256"]

    if not r2_key:
        raise HTTPException(status_code=404, detail="Proof record not found")

    # Генерация временной прямой ссылки для безопасного просмотра прямо из R2
    signed_url = s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': R2_BUCKET_NAME, 'Key': r2_key},
        ExpiresIn=3600  # Ссылка действительна 1 час
    )

    return {
        "status": "verified",
        "tracking_id": tracking_id,
        "sha256": sha256_hash,
        "video_url": signed_url
    }


app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")