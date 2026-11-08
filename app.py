from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import hashlib
import uuid

app = Flask(__name__)
app.secret_key = 'packproof_super_secret_key_security'  # Секретный ключ для сессий

DB_NAME = 'packproof.db'

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

@app.route('/')
def home():
    return redirect(url_for('login_page'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        password_hash = hash_password(password)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, role, is_vip FROM users WHERE email = ? AND password_hash = ?", (email, password_hash))
        user = cursor.fetchone()
        conn.close()
        
        if user:
            # Сохраняем email и роль в сессию пользователя
            session['user_email'] = email
            session['user_role'] = user[1]
            print(f"[ERFOLG] Benutzer {email} erfolgreich angemeldet.")
            return redirect(url_for('dashboard'))
        else:
            return "Fehler: Ungültige E-Mail oder falsches Passwort! <a href='/login'>Zurück</a>"
            
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register_action():
    email = request.form.get('email')
    password = request.form.get('password')
    password_hash = hash_password(password)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        user_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO users (id, email, password_hash, role) VALUES (?, ?, ?, ?)", (user_id, email, password_hash, 'buyer'))
        conn.commit()
        print(f"[ERFOLG] Neuer Benutzer registriert: {email}")
        
        # Сразу логиним пользователя после регистрации
        session['user_email'] = email
        session['user_role'] = 'buyer'
        return redirect(url_for('dashboard'))
    except sqlite3.IntegrityError:
        return "Fehler: Diese E-Mail ist bereits registriert! <a href='/login'>Zurück</a>"
    finally:
        conn.close()

@app.route('/dashboard')
def dashboard():
    # Проверяем, вошел ли пользователь в систему
    if 'user_email' not in session:
        return redirect(url_for('login_page'))
    
    # Рендерим личный кабинет из папки templates
    return render_template('app.html', email=session['user_email'], role=session['user_role'])

@app.route('/inspection')
def inspection():
    if 'user_email' not in session:
        return redirect(url_for('login_page'))
    return render_template('inspection.html', email=session['user_email'], role=session['user_role'])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# Маршрут для приема видео с камеры со страницы inspection.html
@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'user_email' not in session:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    
    if 'video' not in request.files:
        return jsonify({'status': 'error', 'message': 'Kein Video gefunden'}), 400
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'Kein Dateiname'}), 400
    
    if file:
        # Папка для сохранения видео
        videos_dir = 'videos'
        os.makedirs(videos_dir, exist_ok=True)
        
        safe_email = session.get('user_email', 'user').replace('@', '_at_').replace('.', '_')
        filename = f"inspection_{safe_email}.webm"
        filepath = os.path.join(videos_dir, filename)
        
        file.save(filepath)
        
        return jsonify({
            'status': 'success', 
            'message': 'Video erfolgreich hochgeladen und gespeichert!',
            'path': filepath
        }), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)