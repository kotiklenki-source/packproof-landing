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
R2_ACCESS_KEY_ID = "2c1fab3667f17f8104af65c23398f493"
R2_SECRET_ACCESS_KEY = "e72c3c21c30ab05321d10600aa90898f4168483a3037a01eedf282ac81af817c"
R2_ENDPOINT_URL = "https://1ff6eaee49a620894ec41d70b1840a85.r2.cloudflarestorage.com"
R2_BUCKET_NAME = "packproof-videos"

s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name='auto',
    config=Config(signature_version='s3v4', s3={'addressing_style': 'path'})
)

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
            sha256 TEXT NOT NULL,
            secret_pin TEXT DEFAULT '1234',
            unboxing_r2_key TEXT DEFAULT NULL
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

@app.get("/")
async def read_root():
    return FileResponse(os.path.join(PROJECT_ROOT, "index.html"))

@app.get("/app")
async def read_app():
    return FileResponse(os.path.join(PROJECT_ROOT, "app.html"))

@app.get("/verify")
async def read_verify():
    return FileResponse(os.path.join(PROJECT_ROOT, "verify.html"))

@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...), 
    tracking_id: str = Form(None),
    secret_pin: str = Form("1234")
):
    try:
        final_id = tracking_id or "ID_" + str(int(time.time()))
        contents = await file.read()
        sha256_hash = hashlib.sha256(contents).hexdigest()
        file_ext = os.path.splitext(file.filename)[1] or ".webm"
        r2_key = f"pack_{final_id}_{sha256_hash[:8]}{file_ext}"

        s3_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=r2_key,
            Body=contents,
            ContentType=file.content_type or "video/webm"
        )

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO videos (tracking_id, r2_key, sha256, secret_pin) VALUES (?, ?, ?, ?)",
            (final_id, r2_key, sha256_hash, secret_pin)
        )
        conn.commit()
        conn.close()

        video_info = {
            "tracking_id": final_id,
            "r2_key": r2_key,
            "sha256": sha256_hash,
            "secret_pin": secret_pin,
            "unboxing_r2_key": None
        }
        video_db[final_id] = video_info
        save_json_db(video_db)

        return {"status": "success", "tracking_id": final_id, "secret_pin": secret_pin}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload-unboxing")
async def upload_unboxing(
    file: UploadFile = File(...),
    tracking_id: str = Form(...),
    pin: str = Form(...)
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT secret_pin FROM videos WHERE tracking_id = ?", (tracking_id,))
    row = cursor.fetchone()
    
    saved_pin = row[0] if row else video_db.get(tracking_id, {}).get("secret_pin", "1234")
    
    if str(saved_pin) != str(pin):
        conn.close()
        raise HTTPException(status_code=403, detail="Falscher PIN / Неверный PIN-код")

    contents = await file.read()
    sha256_hash = hashlib.sha256(contents).hexdigest()
    file_ext = os.path.splitext(file.filename)[1] or ".webm"
    unboxing_key = f"unbox_{tracking_id}_{sha256_hash[:8]}{file_ext}"

    s3_client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=unboxing_key,
        Body=contents,
        ContentType=file.content_type or "video/webm"
    )

    cursor.execute("UPDATE videos SET unboxing_r2_key = ? WHERE tracking_id = ?", (unboxing_key, tracking_id))
    conn.commit()
    conn.close()

    if tracking_id in video_db:
        video_db[tracking_id]["unboxing_r2_key"] = unboxing_key
        save_json_db(video_db)

    return {"status": "success", "message": "Unboxing video uploaded successfully"}

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tracking_id, r2_key, sha256, unboxing_r2_key FROM videos WHERE tracking_id = ?",
        (identifier,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row and identifier not in video_db:
        raise HTTPException(status_code=404, detail="Proof record not found")

    if row:
        t_id, r2_key, sha256_hash, unboxing_key = row[0], row[1], row[2], row[3]
    else:
        info = video_db[identifier]
        t_id, r2_key, sha256_hash, unboxing_key = info["tracking_id"], info["r2_key"], info["sha256"], info.get("unboxing_r2_key")

    pack_url = s3_client.generate_presigned_url('get_object', Params={'Bucket': R2_BUCKET_NAME, 'Key': r2_key}, ExpiresIn=3600)
    
    unbox_url = None
    if unboxing_key:
        unbox_url = s3_client.generate_presigned_url('get_object', Params={'Bucket': R2_BUCKET_NAME, 'Key': unboxing_key}, ExpiresIn=3600)

    return {
        "status": "verified",
        "tracking_id": t_id,
        "sha256": sha256_hash,
        "video_url": pack_url,
        "unboxing_video_url": unbox_url
    }

app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")