import sqlite3
import hashlib
import uuid

DB_NAME = 'packproof.db'

def hash_password(password):
    """Secure password hashing"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def register_user(email, password, role='buyer'):
    """Register a new user in the system"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    user_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    
    try:
        cursor.execute(
            "INSERT INTO users (id, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (user_id, email, password_hash, role)
        )
        conn.commit()
        print(f"[ERFOLG] Benutzer {email} wurde erfolgreich mit der Rolle '{role}' registriert.")
    except sqlite3.IntegrityError:
        print(f"[FEHLER] Ein Benutzer mit der E-Mail-Adresse {email} existiert bereits!")
    finally:
        conn.close()

def login_user(email, password):
    """User authentication handler"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    password_hash = hash_password(password)
    
    cursor.execute(
        "SELECT id, role, is_vip FROM users WHERE email = ? AND password_hash = ?",
        (email, password_hash)
    )
    user = cursor.fetchone()
    conn.close()
    
    if user:
        print(f"[ERFOLG] Willkommen zurück! ID: {user[0]}, Rolle: {user[1]}, VIP: {user[2]}")
        return user
    else:
        print("[FEHLER] Ungültige E-Mail-Adresse oder falsches Passwort!")
        return None

if __name__ == "__main__":
    print("PackProof Auth-Modul wird getestet...")
    register_user("test@packproof.de", "mypass123", "buyer")
    login_user("test@packproof.de", "mypass123")