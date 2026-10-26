import os
import hashlib
import qrcode
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="PackProof Inspection API")

# Определяем базовую директорию (папку, где лежит main.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Папка static находится на уровень выше (в корне проекта)
STATIC_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "static"))
# Папка uploads
UPLOAD_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Если вдруг static лежит рядом с main.py в папке backend, используй:
if not os.path.exists(STATIC_DIR):
    STATIC_DIR = os.path.join(BASE_DIR, "static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

video_db = {}


# --- 1. Маршруты для страниц ---

@app.get("/")
async def read_root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/app")
async def read_app():
    return FileResponse(os.path.join(STATIC_DIR, "app.html"))

@app.get("/verify")
async def read_verify():
    return FileResponse(os.path.join(STATIC_DIR, "verify.html"))


# --- 2. Бэкенд API ---

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...), tracking_id: str = Form(...)):
    try:
        contents = await file.read()
        sha256_hash = hashlib.sha256(contents).hexdigest()
        file_extension = os.path.splitext(file.filename)[1] or ".webm"
        saved_filename = f"{sha256_hash}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, saved_filename)
        
        with open(file_path, "wb") as f:
            f.write(contents)
            
        video_info = {
            "tracking_id": tracking_id,
            "filename": saved_filename,
            "sha256": sha256_hash,
            "file_path": file_path
        }
        video_db[sha256_hash] = video_info
        video_db[tracking_id] = video_info
        
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
    video_info = video_db.get(identifier)
    if not video_info:
        raise HTTPException(status_code=404, detail="Proof record not found")
    
    return {
        "status": "verified",
        "tracking_id": video_info["tracking_id"],
        "sha256": video_info["sha256"],
        "video_url": f"/uploads/{video_info['filename']}"
    }


# --- 3. Подключение статических папок ---

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")