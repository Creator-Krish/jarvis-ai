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
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))  # 10MB limit
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# CORS - Restrict in production
if os.environ.get("FLASK_ENV") == "production":
    CORS(app, origins=[os.environ.get("ALLOWED_ORIGIN", "https://jarvis-ai.onrender.com")], supports_credentials=True)
else:
    CORS(app, origins=["*"], supports_credentials=True)

# =============================
# UPLOAD FOLDER SETUP
# =============================
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/persistent/uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =============================
# ENVIRONMENT VARIABLES (SAFE)
# =============================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/login/callback")

GROQ_KEY = os.environ.get("GROQ_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")

# Validate critical keys
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    logger.warning("⚠️ Google OAuth keys not set - Login will fail")

# =============================
# DATABASE CONNECTION (WITH RETRY)
# =============================
def get_db():
    try:
        return mysql.connector.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            user=os.environ.get("DB_USER", "root"),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "jarvis_db"),
            connection_timeout=10,
            pool_name="jarvis_pool",
            pool_size=5
        )
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

# =============================
# RATE LIMIT (IP + USER COMBO)
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

# =============================
# LOGIN REQUIRED DECORATOR
# =============================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Not logged in'}), 401
        return f(*args, **kwargs)
    return decorated_function

# =============================
# SYSTEM PROMPT (PROFESSIONAL VERSION)
# =============================
SYSTEM_PROMPT = """You are JARVIS, an AI assistant developed by Krish Palival. 
You are designed to be helpful, intelligent, and professional. You provide accurate, thoughtful responses.
You have capabilities including PDF processing, voice recognition, image OCR, and NLP analysis."""

# =============================
# AI FUNCTIONS (WITH TIMEOUT & LOGGING)
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
        logger.warning(f"DeepSeek error: {res.status_code}")
        return None
    except Exception as e:
        logger.error(f"DeepSeek exception: {e}")
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
        logger.error(f"Groq exception: {e}")
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
        logger.error(f"Gemini exception: {e}")
        return None

def call_ai(prompt):
    # Try DeepSeek first
    response = call_deepseek(prompt)
    if response:
        return response, "deepseek"
    
    # Fallback to Groq
    response = call_groq(prompt)
    if response:
        return response, "groq"
    
    # Final fallback to Gemini
    response = call_gemini(prompt)
    if response:
        return response, "gemini"
    
    logger.error("All AI services failed")
    return "I'm currently experiencing high demand. Please try again in a moment.", "fallback"

# =============================
# INIT DATABASE WITH INDEXES
# =============================
def init_db():
    db = get_db()
    cursor = db.cursor()
    
    # Users table
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
    
    # Login logs
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
    
    # Chat sessions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            title VARCHAR(255) DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Messages
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT PRIMARY KEY AUTO_INCREMENT,
            session_id INT,
            role ENUM('user', 'assistant') NOT NULL,
            content TEXT NOT NULL,
            model_used VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        )
    """)
    
    # PDF documents table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdf_documents (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            filename VARCHAR(255),
            file_path TEXT,
            content TEXT,
            summary TEXT,
            keywords TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Voice notes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS voice_notes (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            filename VARCHAR(255),
            text_content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # Images table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            filename VARCHAR(255),
            extracted_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # CREATE INDEXES for performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions ON chat_sessions(user_id, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_messages ON messages(session_id, created_at)")
    
    db.commit()
    cursor.close()
    db.close()
    logger.info("✅ Database initialized with indexes")

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
            logger.error(f"Token exchange failed: {token_res.status_code}")
            return redirect(url_for('index', error='Token exchange failed'))
        
        token_json = token_res.json()
        access_token = token_json.get('access_token')
        
        user_res = requests.get('https://www.googleapis.com/oauth2/v1/userinfo', headers={'Authorization': f'Bearer {access_token}'}, timeout=10)
        if user_res.status_code != 200:
            return redirect(url_for('index', error='Failed to get user info'))
        
        user_info = user_res.json()
        
        # Get or create user
        db = get_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (user_info['id'], user_info['email']))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("""
                INSERT INTO users (google_id, email, username, display_name, avatar_url) 
                VALUES (%s, %s, %s, %s, %s)
            """, (user_info['id'], user_info['email'], user_info.get('name', user_info['email'].split('@')[0]).replace(' ', '_'), 
                  user_info.get('name', user_info['email'].split('@')[0]), user_info.get('picture', '')))
            db.commit()
            user_id = cursor.lastrowid
        else:
            user_id = user['id']
            cursor.execute("UPDATE users SET last_login = NOW(), total_logins = total_logins + 1 WHERE id = %s", (user_id,))
            db.commit()
        
        cursor.close()
        db.close()
        
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
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, title, created_at, updated_at FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC", (user_id,))
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

@app.route("/ai/session/<int:session_id>/message", methods=["POST"])
@login_required
def add_message(session_id):
    user_id = session['user_id']
    data = request.json
    prompt = data.get("prompt", "").strip()
    
    if not prompt:
        return jsonify({"success": False, "error": "Empty message"}), 400
    
    if is_rate_limited(user_id, request.remote_addr):
        return jsonify({"success": False, "error": "Too many requests. Please wait a moment."}), 429
    
    db = get_db()
    cursor = db.cursor()
    
    # Save user message
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (session_id, prompt))
    
    # Get AI response
    response, model_used = call_ai(prompt)
    
    # Save AI response
    cursor.execute("INSERT INTO messages (session_id, role, content, model_used) VALUES (%s, 'assistant', %s, %s)", 
                   (session_id, response, model_used))
    cursor.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
    
    # Update title if first message
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

# =============================
# PDF ROUTES (WITH SECURE FILENAME)
# =============================
@app.route("/ai/pdf/upload", methods=["POST"])
@login_required
def upload_pdf():
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400
    
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"success": False, "error": "Only PDF files allowed"}), 400
    
    # Secure filename
    filename = secure_filename(f"{session['user_id']}_{int(time.time())}_{file.filename}")
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    
    try:
        import PyPDF2
        text = ""
        with open(filepath, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
        
        # Simple summary
        summary = text[:500] + "..." if len(text) > 500 else text
        
        # Save to database
        db = get_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO pdf_documents (user_id, filename, file_path, content, summary) 
            VALUES (%s, %s, %s, %s, %s)
        """, (session['user_id'], file.filename, filepath, text[:10000], summary))
        db.commit()
        pdf_id = cursor.lastrowid
        cursor.close()
        db.close()
        
        return jsonify({
            "success": True,
            "pdf_id": pdf_id,
            "filename": file.filename,
            "text_length": len(text),
            "summary": summary
        })
        
    except Exception as e:
        logger.error(f"PDF processing error: {e}")
        return jsonify({"success": False, "error": "Failed to process PDF"}), 500

# =============================
# NLP ANALYSIS ROUTE
# =============================
@app.route("/ai/nlp/analyze", methods=["POST"])
@login_required
def analyze_text_api():
    data = request.json
    text = data.get("text", "").strip()
    
    if not text:
        return jsonify({"success": False, "error": "No text provided"}), 400
    
    # Simple analysis (no heavy NLP libraries)
    words = len(text.split())
    sentences = len(text.split('.'))
    
    # Simple sentiment (keyword-based)
    positive_words = ['good', 'great', 'awesome', 'amazing', 'wonderful', 'best', 'love']
    negative_words = ['bad', 'terrible', 'awful', 'hate', 'worst', 'sad']
    
    text_lower = text.lower()
    pos_score = sum(1 for w in positive_words if w in text_lower)
    neg_score = sum(1 for w in negative_words if w in text_lower)
    
    if pos_score > neg_score:
        sentiment = "positive"
    elif neg_score > pos_score:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    
    return jsonify({
        "success": True,
        "analysis": {
            "word_count": words,
            "sentence_count": sentences,
            "sentiment": sentiment,
            "length": len(text)
        }
    })

# =============================
# FRONTEND
# =============================
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# =============================
# ERROR HANDLERS
# =============================
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({"error": "Internal server error"}), 500

# =============================
# RUN
# =============================
if __name__ == "__main__":
    init_db()
    logger.info("="*60)
    logger.info("🚀 JARVIS ULTIMATE AI SUITE - PRODUCTION MODE")
    logger.info("📄 PDF Processor")
    logger.info("🎤 Voice to Text")
    logger.info("🖼️ Image OCR")
    logger.info("🧠 NLP Analysis")
    logger.info(f"📁 Upload folder: {UPLOAD_FOLDER}")
    logger.info("="*60)
    
    port = int(os.environ.get("PORT", 5000))
    
    if os.environ.get("FLASK_ENV") == "production":
        # Production: Use waitress or gunicorn
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)
