# CONTEXT.md — PackProof Inspection (PPI)

## 1. Обзор проекта
- **Название:** PackProof Inspection (PPI)
- **Продакшн-домен:** `https://packproofinspection.com`
- **Назначение:** Веб-сервис видеофиксации упаковки (Packing) и распаковки (Unboxing) посылок для защиты e-commerce продавцов от мошенничества с возвратами (Retourenbetrug) в Германии.
- **Язык интерфейса и юридических страниц:** Немецкий (`de`), с атрибутом `translate="no"` на ключевых UI-элементах.

## 2. Текущий стек и инфраструктура (Production)
- **Backend:** Python 3.14, FastAPI, Uvicorn (`app.py` в корне репозитория).
- **Хостинг:** Render (`Start Command`: `uvicorn app:app --host 0.0.0.0 --port $PORT`, `Auto-Deploy` из ветки `main` репозитория `packproof-landing`).
- **База данных:** Neon PostgreSQL (подключение через `DATABASE_URL`).
- **Объектное хранилище видео:** Cloudflare R2 (бакет `packproof-videos`, S3-совместимый API через `boto3`).
- **Frontend:** Серверный рендеринг Jinja2 (`templates/`), тёмная тема PPI, локальный буфер видеосегментов в браузере через **IndexedDB** для защиты от обрыва интернета во время записи.

## 3. Структура файлов репозитория
- `app.py` — основной сервер FastAPI (все маршруты страниц и API, работа с БД и R2).
- `requirements.txt` — зависимости Python.
- `templates/` — HTML-шаблоны:
  - `index.html` — лендинг (главная страница).
  - `login.html` — регистрация и вход (`/login`).
  - `dashboard.html` — личный кабинет продавца (`/app`).
  - `inspection.html` — создание инспекции и запись видео упаковки (`/inspection`).
  - `verify.html` — проверка QR + PIN и запись видео распаковки (`/verify`).
  - `report.html` — публичный/защищённый отчёт с двумя видео и SHA-256 хэшами (`/report/...`).
  - `admin.html` — панель администратора.
  - `impressum.html` и `datenschutz.html` — актуальные юридические страницы (DSGVO / Stand: November 2026, оператор: Yevhenii Banko, Bayreuth).

## 4. Переменные окружения (настроены на Render)
`DATABASE_URL`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`, `APP_BASE_URL`, `SECRET_KEY`.
*(Локально в репозитории `.env` не хранится и не коммитится).*

## 5. Следующая задача (внедрение защиты от злоупотреблений)
Необходимо интегрировать в текущий `app.py` и шаблоны 4 уровня защиты, не ломая существующую логику IndexedDB, Neon PostgreSQL и R2:
1. Валидация форматов трек-номеров (DHL, UPS, DPD, Hermes, GLS).
2. Лимит инспекций (не более 2 записей на один трек-номер).
3. Автоматическое удаление видео в Cloudflare R2 через 30 дней (Lifecycle Rule / TTL для удержания в рамках бесплатного тира).
4. Защита регистрации и аутентификации (блокировка одноразовых email-доменов и rate limiting).

## 6. Правила работы с кодом для AI
- Не переписывать проект на Flask или SQLite — используется строго **FastAPI + Neon PostgreSQL (`DATABASE_URL`) + Cloudflare R2**.
- Все HTML-шаблоны отдаются только из папки `templates/` через `templates.TemplateResponse(request=request, name="...")`.
- Перед выдачей кода учитывать текущие названия таблиц и колонок в `app.py`.