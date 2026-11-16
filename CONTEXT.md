# CONTEXT.md — Паспорт проекта PackProof Inspection

## 1. Суть проекта
Веб-сервис видеофиксации состояния посылок при упаковке и распаковке для защиты продавцов и покупателей от споров/мошенничества.

## 2. Жестко зафиксированный стек (НЕ менять на Flask или SQLite!)
- **Backend:** Python 3.11+, FastAPI, Uvicorn, Jinja2.
- **Database:** Neon PostgreSQL (облачная БД, подключается через `DATABASE_URL` из `.env`).
- **Storage:** Cloudflare R2 (через `boto3` S3-совместимый API — видео и QR-коды не хранятся локально на диске Render!).
- **Hosting:** Render.com.
- **Frontend:** HTML5, Tailwind CSS, Vanilla JS (`MediaRecorder API` для записи видео с камеры).

## 3. Текущая структура файлов (в корне `packproof-landing/`)
- `CONTEXT.md` — этот файл.
- `app.py` — главный входной файл FastAPI (роуты страниц, загрузка видео в R2, генерация QR, отчёты, админка).
- `auth.py` — роутер авторизации, регистрации, сессий/токенов и личного кабинета (`/login`, `/register`, `/dashboard`).
- `database.py` — подключение к Neon PostgreSQL и создание таблиц.
- `requirements.txt` — зависимости (`fastapi`, `uvicorn`, `python-multipart`, `qrcode`, `pillow`, `boto3`, `requests`, `sqlalchemy`, `psycopg2-binary`, `passlib[bcrypt]`, `itsdangerous`).
- `templates/` — HTML-шаблоны (`index.html`, `inspection.html`, `dashboard.html`, `report.html`, `admin.html`, `login.html`, `impressum.html`, `datenschutz.html`).
- `en/` — англоязычные версии статических страниц (если используются).
- Иконки и манифест в корне (`favicon.ico`, `site.webmanifest` и т.д.).

## 4. Бизнес-логика и поток данных
1. Пользователь (или гость по трек-номеру) открывает `/inspect?tracking=...` (`inspection.html`).
2. Записывает видео распаковки/упаковки с камеры (лимит 3 минуты).
3. Фронтенд отправляет видео на `/api/upload-video`.
4. Бэкенд загружает видео напрямую в бакет **Cloudflare R2**, генерирует QR-код со ссылкой на публичный отчёт `/report/{tracking_number}`, сохраняет QR в R2 и записывает метаданные в **Neon PostgreSQL**.
5. Пользователь перенаправляется на `/report/{tracking_number}`, где видит видео, статус, дату и QR-код.
6. Авторизованные пользователи видят все свои инспекции в `/dashboard` (Личный кабинет). Админ видит всё в `/admin`.