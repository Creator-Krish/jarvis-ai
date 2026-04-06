from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from flask_cors import CORS
import psycopg2
import psycopg2.extras
import os
import time
import requests
import secrets
import hashlib
import json
import datetime
import base64
import io
import re
import logging
import sys
from functools import wraps
from collections import defaultdict
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

# =============================
# LOGGING SETUP
# =============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('jarvis.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =============================
# APP CONFIGURATION
# =============================
app = Flask(__name__, static_folder='.')
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

if os.environ.get("FLASK_ENV") == "production":
    CORS(app, origins=[os.environ.get("ALLOWED_ORIGIN", "https://jarvis-ai.onrender.com")], supports_credentials=True)
else:
    CORS(app, origins=["*"], supports_credentials=True)

# =============================
# UPLOAD FOLDER SETUP
# =============================
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/tmp/uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =============================
# ENVIRONMENT VARIABLES
# =============================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/login/callback")

GROQ_KEY = os.environ.get("GROQ_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")

# =============================
# DATABASE CONNECTION (POSTGRESQL)
# =============================
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "jarvis_db"),
            connect_timeout=10
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

# =============================
# RATE LIMIT
# =============================
rate_limit_store = defaultdict(list)
LIMIT = 10
WINDOW = 60

def is_rate_limited(user_id, ip):
    key = f"{user_id}:{ip}"
    now = time.time()
    rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < WINDOW]
    if len(rate_limit_store[key]) >= LIMIT:
        return True
    rate_limit_store[key].append(now)
    return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Not logged in'}), 401
        return f(*args, **kwargs)
    return decorated_function

# =============================
# SYSTEM PROMPT
# =============================
SYSTEM_PROMPT = """You are JARVIS, an AI assistant developed by Krish Palival. 
You are designed to be helpful, intelligent, and professional. You provide accurate, thoughtful responses."""

# =============================
# AI FUNCTIONS
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
        logger.error(f"DeepSeek error: {e}")
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
        logger.error(f"Groq error: {e}")
        return None

def call_gemini(prompt):
    try:
        if not GEMINI_KEY:
            return None
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(f"{SYSTEM_PROMPT}\n\nUser: {prompt}")
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return None

def call_ai(prompt):
    response = call_deepseek(prompt)
    if response:
        return response, "deepseek"
    response = call_groq(prompt)
    if response:
        return response, "groq"
    response = call_gemini(prompt)
    if response:
        return response, "gemini"
    return "I'm currently experiencing high demand. Please try again in a moment.", "fallback"

# =============================
# DATABASE INIT (POSTGRESQL)
# =============================
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
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
    
    # Login logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            username VARCHAR(100),
            email VARCHAR(255),
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address VARCHAR(45)
        )
    """)
    
    # Chat sessions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Messages
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role VARCHAR(10) NOT NULL,
            content TEXT NOT NULL,
            model_used VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # PDF documents
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdf_documents (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            filename VARCHAR(255),
            file_path TEXT,
            content TEXT,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions ON chat_sessions(user_id, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_messages ON messages(session_id, created_at)")
    
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ PostgreSQL database initialized")

# =============================
# GOOGLE OAUTH ROUTES
# =============================
@app.route('/login/google')
def google_login():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google OAuth not configured"}), 500
    auth_url = f"https://accounts.google.com/o/oauth2/auth?client_id={GOOGLE_CLIENT_ID}&redirect_uri={GOOGLE_REDIRECT_URI}&response_type=code&scope=email%20profile"
    return redirect(auth_url)

@app.route('/login/callback')
def google_callback():
    code = request.args.get('code')
    if not code:
        return redirect(url_for('index', error='Login failed'))
    
    token_data = {
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'grant_type': 'authorization_code'
    }
    
    try:
        token_res = requests.post('https://oauth2.googleapis.com/token', data=token_data, timeout=10)
        if token_res.status_code != 200:
            return redirect(url_for('index', error='Token exchange failed'))
        
        token_json = token_res.json()
        access_token = token_json.get('access_token')
        
        user_res = requests.get('https://www.googleapis.com/oauth2/v1/userinfo', headers={'Authorization': f'Bearer {access_token}'}, timeout=10)
        if user_res.status_code != 200:
            return redirect(url_for('index', error='Failed to get user info'))
        
        user_info = user_res.json()
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (user_info['id'], user_info['email']))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("""
                INSERT INTO users (google_id, email, username, display_name, avatar_url) 
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (user_info['id'], user_info['email'], user_info.get('name', user_info['email'].split('@')[0]).replace(' ', '_'), 
                  user_info.get('name', user_info['email'].split('@')[0]), user_info.get('picture', '')))
            user_id = cursor.fetchone()['id']
            conn.commit()
        else:
            user_id = user['id']
            cursor.execute("UPDATE users SET last_login = NOW(), total_logins = total_logins + 1 WHERE id = %s", (user_id,))
            conn.commit()
        
        cursor.close()
        conn.close()
        
        session['user_id'] = user_id
        session['user_name'] = user_info.get('name', user_info['email'].split('@')[0])
        session['user_email'] = user_info['email']
        
        return redirect(url_for('index'))
        
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return redirect(url_for('index', error='Authentication failed'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/me')
def get_current_user():
    if 'user_id' in session:
        return jsonify({'success': True, 'user': {
            'id': session['user_id'],
            'name': session.get('user_name'),
            'email': session.get('user_email')
        }})
    return jsonify({'success': False, 'error': 'Not logged in'})

# =============================
# CHAT ROUTES
# =============================
@app.route("/ai/sessions", methods=["GET"])
@login_required
def get_sessions():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT id, title, created_at, updated_at FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC", (user_id,))
    sessions = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "sessions": sessions})

@app.route("/ai/session", methods=["POST"])
@login_required
def create_session():
    user_id = session['user_id']
    data = request.json
    title = data.get("title", "New Chat")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_sessions (user_id, title) VALUES (%s, %s) RETURNING id", (user_id, title))
    session_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({"success": True, "session_id": session_id})

@app.route("/ai/session/<int:session_id>", methods=["GET"])
@login_required
def get_session_messages(session_id):
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cursor.execute("SELECT * FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    cursor.execute("SELECT role, content, created_at FROM messages WHERE session_id = %s ORDER BY created_at ASC", (session_id,))
    messages = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify({"success": True, "messages": messages})

@app.route("/ai/session/<int:session_id>/message", methods=["POST"])
@login_required
def add_message(session_id):
    user_id = session['user_id']
    data = request.json
    prompt = data.get("prompt", "").strip()
    
    if not prompt:
        return jsonify({"success": False, "error": "Empty message"}), 400
    
    if is_rate_limited(user_id, request.remote_addr):
        return jsonify({"success": False, "error": "Too many requests"}), 429
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (session_id, prompt))
    
    response, model_used = call_ai(prompt)
    
    cursor.execute("INSERT INTO messages (session_id, role, content, model_used) VALUES (%s, 'assistant', %s, %s)", 
                   (session_id, response, model_used))
    cursor.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
    
    cursor.execute("SELECT COUNT(*) as count FROM messages WHERE session_id = %s", (session_id,))
    result = cursor.fetchone()
    is_first = result[0] <= 2
    
    if is_first:
        title = prompt[:50] + "..." if len(prompt) > 50 else prompt
        cursor.execute("UPDATE chat_sessions SET title = %s WHERE id = %s", (title, session_id))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({"success": True, "response": response, "model": model_used, "is_first_message": is_first})

# =============================
# FRONTEND
# =============================
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# =============================
# RUN
# =============================
if __name__ == "__main__":
    init_db()
    logger.info("="*60)
    logger.info("🚀 JARVIS with PostgreSQL")
    logger.info("="*60)
    
    port = int(os.environ.get("PORT", 5000))
    
    if os.environ.get("FLASK_ENV") == "production":
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)
