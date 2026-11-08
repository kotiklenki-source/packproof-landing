-- 1. Таблица пользователей (Покупатели, Продавцы, Админы)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,                 -- Уникальный ID (например, UUID)
    email TEXT UNIQUE NOT NULL,          -- Email для входа
    password_hash TEXT NOT NULL,         -- Захэшированный пароль
    role TEXT NOT NULL DEFAULT 'buyer',  -- Роль: 'buyer', 'seller', 'admin'
    is_vip INTEGER DEFAULT 0,            -- Флаг VIP: 0 (нет), 1 (активен)
    vip_expires_at DATETIME,             -- Дата окончания VIP-статуса
    videos_left_this_month INTEGER DEFAULT 10, -- Остаток бесплатных видео (лимит 10)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Таблица промокодов и бонусов
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,               -- Сам код (например, 'PACKPROOF_VIP_6M')
    reward_type TEXT NOT NULL,           -- Тип бонуса: 'vip_6_months', 'extra_videos'
    reward_value INTEGER DEFAULT 1,      -- Значение (например, кол-во месяцев или видео)
    max_uses INTEGER DEFAULT 100,        -- Сколько раз можно использовать всего
    times_used INTEGER DEFAULT 0,        -- Сколько раз уже активировали
    expires_at DATETIME                  -- Срок действия промокода
);

-- 3. Таблица споров и арбитража (для трек-номеров и видео)
CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,                 -- ID спора
    tracking_number TEXT NOT NULL,       -- Трек-номер посылки
    buyer_id TEXT NOT NULL,              -- Кто открыл спор
    video_url TEXT,                      -- Ссылка на видео распаковки (Cloudflare R2)
    status TEXT DEFAULT 'pending',       -- Статус: 'pending', 'resolved_buyer', 'resolved_seller', 'resolved_delivery'
    verdict_reason TEXT,                 -- Выбранная причина/комментарий арбитра
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (buyer_id) REFERENCES users(id)
);

-- Индексы для быстрого поиска по трек-номеру и email
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_disputes_tracking ON disputes(tracking_number);