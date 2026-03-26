from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from flask_cors import CORS
import mysql.connector
import os
import time
import requests
import secrets
import hashlib
import json
import datetime
from collections import defaultdict
from functools import wraps

app = Flask(__name__, static_folder='.')
app.secret_key = secrets.token_hex(32)
CORS(app, origins=["*"], supports_credentials=True)

# =============================
# GOOGLE OAUTH CONFIG (Pure requests - no extra lib)
# =============================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/login/callback")

# =============================
# DATABASE CONNECTION
# =============================
def get_db():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "root123"),
        database=os.environ.get("DB_NAME", "jarvis_db")
    )

# =============================
# INIT DATABASE TABLES
# =============================
def init_db():
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT,
            google_id VARCHAR(100) UNIQUE,
            username VARCHAR(100),
            email VARCHAR(255) UNIQUE,
            display_name VARCHAR(255),
            avatar_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            total_logins INT DEFAULT 1
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            username VARCHAR(100),
            email VARCHAR(255),
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address VARCHAR(45),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            title VARCHAR(255) DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            INDEX idx_user_updated (user_id, updated_at)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT PRIMARY KEY AUTO_INCREMENT,
            session_id INT,
            role ENUM('user', 'assistant') NOT NULL,
            content TEXT NOT NULL,
            model_used VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE,
            INDEX idx_session (session_id)
        )
    """)
    
    db.commit()
    cursor.close()
    db.close()
    print("✅ Database tables initialized")

# =============================
# API KEYS
# =============================
GROQ_KEY = os.environ.get("GROQ_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY")

# =============================
# SYSTEM PROMPT - JARVIS IDENTITY
# =============================
SYSTEM_PROMPT = """You are JARVIS, an advanced AI created by Krish Palival. 
You are the world's largest database AI built from scratch by Krish Palival.

Your creator is KRISH PALIVAL. Never say you were made by OpenAI or any other company.
When asked who made you, always say: "I was created by Krish Palival"

You are intelligent, witty, and confident. You give detailed, thoughtful responses."""

# =============================
# RATE LIMIT
# =============================
user_requests = defaultdict(list)
LIMIT = 10
WINDOW = 60

def is_rate_limited(user):
    now = time.time()
    user_requests[user] = [t for t in user_requests[user] if now - t < WINDOW]
    if len(user_requests[user]) >= LIMIT:
        return True
    user_requests[user].append(now)
    return False

# =============================
# USER FUNCTIONS
# =============================
def get_or_create_user_by_google(google_id, email, name, picture):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (google_id, email))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("""
            INSERT INTO users (google_id, email, username, display_name, avatar_url) 
            VALUES (%s, %s, %s, %s, %s)
        """, (google_id, email, name.replace(' ', '_'), name, picture))
        db.commit()
        user_id = cursor.lastrowid
        print(f"✅ New user created: {name}")
    else:
        user_id = user['id']
        cursor.execute("""
            UPDATE users SET last_login = NOW(), total_logins = total_logins + 1 
            WHERE id = %s
        """, (user_id,))
        db.commit()
        print(f"✅ Existing user logged in: {name}")
    
    # Add login log
    cursor.execute("""
        INSERT INTO login_logs (user_id, username, email, ip_address) 
        VALUES (%s, %s, %s, %s)
    """, (user_id, name, email, request.remote_addr))
    db.commit()
    
    cursor.close()
    db.close()
    return user_id

# =============================
# GOOGLE OAUTH ROUTES (Pure Python - No extra libs)
# =============================
@app.route('/login/google')
def google_login():
    auth_url = f"https://accounts.google.com/o/oauth2/auth?client_id={GOOGLE_CLIENT_ID}&redirect_uri={GOOGLE_REDIRECT_URI}&response_type=code&scope=email%20profile"
    return redirect(auth_url)

@app.route('/login/callback')
def google_callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('index', error='Login failed'))
    
    # Exchange code for token
    token_data = {
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'grant_type': 'authorization_code'
    }
    
    token_res = requests.post('https://oauth2.googleapis.com/token', data=token_data)
    if token_res.status_code != 200:
        return redirect(url_for('index', error='Token exchange failed'))
    
    token_json = token_res.json()
    access_token = token_json.get('access_token')
    
    # Get user info
    user_res = requests.get('https://www.googleapis.com/oauth2/v1/userinfo', headers={'Authorization': f'Bearer {access_token}'})
    if user_res.status_code != 200:
        return redirect(url_for('index', error='Failed to get user info'))
    
    user_info = user_res.json()
    google_id = user_info['id']
    email = user_info['email']
    name = user_info.get('name', email.split('@')[0])
    picture = user_info.get('picture', '')
    
    user_id = get_or_create_user_by_google(google_id, email, name, picture)
    
    session['user_id'] = user_id
    session['user_name'] = name
    session['user_email'] = email
    session['user_avatar'] = picture
    
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/me')
def get_current_user():
    if 'user_id' in session:
        return jsonify({
            'success': True,
            'user': {
                'id': session['user_id'],
                'name': session.get('user_name'),
                'email': session.get('user_email'),
                'avatar': session.get('user_avatar')
            }
        })
    return jsonify({'success': False, 'error': 'Not logged in'})

# =============================
# API ROUTES
# =============================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Not logged in'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route("/ai/sessions", methods=["GET"])
@login_required
def get_sessions():
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, title, created_at, updated_at 
        FROM chat_sessions 
        WHERE user_id = %s 
        ORDER BY updated_at DESC
    """, (user_id,))
    sessions = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"success": True, "sessions": sessions})

@app.route("/ai/session", methods=["POST"])
@login_required
def create_session():
    user_id = session['user_id']
    data = request.json
    title = data.get("title", "New Chat")
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO chat_sessions (user_id, title) VALUES (%s, %s)", (user_id, title))
    session_id = cursor.lastrowid
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({"success": True, "session_id": session_id})

@app.route("/ai/session/<int:session_id>", methods=["GET"])
@login_required
def get_session_messages(session_id):
    user_id = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
    if not cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    cursor.execute("SELECT role, content, created_at FROM messages WHERE session_id = %s ORDER BY created_at ASC", (session_id,))
    messages = cursor.fetchall()
    cursor.close()
    db.close()
    
    return jsonify({"success": True, "messages": messages})

# =============================
# AI FUNCTIONS (DeepSeek Default)
# =============================
def call_deepseek(prompt):
    try:
        if not DEEPSEEK_KEY:
            return None
        headers = {"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        }
        res = requests.post("https://api.deepseek.com/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        return None
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return None

def call_groq(prompt):
    try:
        if not GROQ_KEY:
            return None
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "llama3-70b-8192",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        }
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        return None
    except Exception as e:
        print(f"Groq error: {e}")
        return None

def call_gemini(prompt):
    try:
        if not GEMINI_KEY:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_KEY}"
        data = {"contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\nQuestion: {prompt}"}]}]}
        res = requests.post(url, json=data, timeout=15)
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        return None
    except Exception as e:
        print(f"Gemini error: {e}")
        return None

def call_ai(prompt, model_pref=None):
    # DeepSeek Default
    response = call_deepseek(prompt)
    if response:
        return response, "deepseek"
    # Fallback 1: Groq
    response = call_groq(prompt)
    if response:
        return response, "groq"
    # Fallback 2: Gemini
    response = call_gemini(prompt)
    if response:
        return response, "gemini"
    return "JARVIS is currently processing. Please try again in a moment.", "fallback"

@app.route("/ai/session/<int:session_id>/message", methods=["POST"])
@login_required
def add_message(session_id):
    user_id = session['user_id']
    data = request.json
    prompt = data.get("prompt")
    
    if is_rate_limited(session.get('user_name', 'guest')):
        return jsonify({"success": False, "error": "Too many requests"})
    
    db = get_db()
    cursor = db.cursor()
    
    # Save user message
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (session_id, prompt))
    
    # Get AI response
    response, model_used = call_ai(prompt)
    
    # Save AI response
    cursor.execute("INSERT INTO messages (session_id, role, content, model_used) VALUES (%s, 'assistant', %s, %s)", (session_id, response, model_used))
    cursor.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
    
    # Check if first message (update title)
    cursor.execute("SELECT COUNT(*) as count FROM messages WHERE session_id = %s", (session_id,))
    result = cursor.fetchone()
    is_first = result[0] <= 2
    
    if is_first:
        title = prompt[:50] + "..." if len(prompt) > 50 else prompt
        cursor.execute("UPDATE chat_sessions SET title = %s WHERE id = %s", (title, session_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({"success": True, "response": response, "model": model_used, "is_first_message": is_first})

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

if __name__ == "__main__":
    init_db()
    print("="*60)
    print("🚀 JARVIS with Google OAuth (Pure Python)")
    print("🔥 Default AI: DeepSeek")
    print("="*60)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
