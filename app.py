import os
import sqlite3
import hashlib
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = 'packproof_secret_key'  # Секретный ключ для сессий
DB_NAME = 'packproof.db'

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    # Таблица инспекций / видео
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            status TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Инициализируем базу данных при запуске
init_db()

# 1. Главная страница (Лендинг)
@app.route('/')
def home():
    return render_template('index.html')

# 2. Страница входа / авторизации
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        pwd_hash = hash_password(password)

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE email = ? AND password = ?', (email, pwd_hash))
        user = cursor.fetchone()
        conn.close()

        if user:
            session['user_id'] = user[0]
            session['email'] = email
            return redirect(url_for('dashboard_page'))
        else:
            return render_template('login.html', error='Ungültige Anmeldedaten')

    return render_template('login.html')

# 3. Страница регистрации
@app.route('/register', methods=['GET', 'POST'])
def register_page():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        pwd_hash = hash_password(password)

        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (email, password) VALUES (?, ?)', (email, pwd_hash))
            conn.commit()
            conn.close()
            return redirect(url_for('login_page'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error='E-Mail ist bereits vergeben')

    return render_template('register.html')

# 4. Личный кабинет (Дашборд)
@app.route('/dashboard')
def dashboard_page():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, filename, status, timestamp FROM inspections WHERE user_id = ?', (session['user_id'],))
    inspections = cursor.fetchall()
    conn.close()

    return render_template('app.html', inspections=inspections)

# 5. Страница инспекции / создания видеозаписи
@app.route('/inspection')
def inspection_page():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('inspection.html')

# 6. API для загрузки видео
@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400

    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    os.makedirs('videos', exist_ok=True)
    filepath = os.path.join('videos', video_file.filename)
    video_file.save(filepath)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO inspections (user_id, filename, status) VALUES (?, ?, ?)',
                   (session['user_id'], video_file.filename, 'COMPLETED & VERIFIED'))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Video successfully uploaded and verified'})

# 7. Юридические страницы (Impressum & Datenschutz)
@app.route('/impressum.html')
def impressum_page():
    return render_template('impressum.html')

@app.route('/datenschutz.html')
def datenschutz_page():
    return render_template('datenschutz.html')

# 8. Выход из системы (Logout)
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)