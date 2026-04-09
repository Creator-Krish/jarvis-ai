from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, g
from flask_cors import CORS
import psycopg2.extras
import psycopg2
from psycopg2 import pool
import os
import time
import requests
import secrets
import logging
import sys
from functools import wraps
from collections import defaultdict
from werkzeug.middleware.proxy_fix import ProxyFix
import jwt
from datetime import datetime, timedelta
import redis

# =============================
# LOGGING SETUP (Prevent spam)
# =============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('jarvis.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
# Suppress werkzeug logs
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.')

# =============================
# ENV VARIABLES (All required)
# =============================
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is required")

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET environment variable is required")

app.secret_key = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'  # Required for cross-site OAuth
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# =============================
# CORS - RESTRICTED
# =============================
PRODUCTION_DOMAIN = os.environ.get("FRONTEND_URL", "https://jarvis-e76i.onrender.com")
ALLOWED_ORIGINS = [PRODUCTION_DOMAIN]

if os.environ.get("FLASK_ENV") != "production":
    ALLOWED_ORIGINS.append("http://localhost:5000")

CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# =============================
# REDIS FOR RATE LIMITING
# =============================
redis_client = None
REDIS_URL = os.environ.get("REDIS_URL")
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        logger.info("✅ Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")

# Fallback rate limiting (in-memory)
rate_limit_store = defaultdict(list)

def is_rate_limited(user_id, ip, limit=5, window=60):
    """Rate limiting with Redis fallback - 5 requests per minute"""
    key = f"ratelimit:{user_id}:{ip}"
    
    if redis_client:
        current = redis_client.get(key)
        if current and int(current) >= limit:
            return True
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        pipe.execute()
        return False
    else:
        now = time.time()
        requests = rate_limit_store[key]
        requests = [t for t in requests if now - t < window]
        if len(requests) >= limit:
            return True
        requests.append(now)
        rate_limit_store[key] = requests
        return False

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr

# =============================
# JWT AUTH (Single auth method)
# =============================
JWT_EXPIRY_DAYS = 7

def generate_token(user_id, email):
    payload = {
        'user_id': user_id,
        'email': email,
        'exp': datetime.utcnow() + timedelta(days=JWT_EXPIRY_DAYS),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check Authorization header first
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            payload = verify_token(token)
            if payload:
                g.user_id = payload['user_id']
                g.user_email = payload['email']
                return f(*args, **kwargs)
        
        # Fallback to session (for backward compatibility)
        if 'user_id' in session:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute("SELECT session_token FROM users WHERE id = %s", (session['user_id'],))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result and result['session_token'] == session.get('session_token'):
                g.user_id = session['user_id']
                g.user_email = session.get('user_email')
                return f(*args, **kwargs)
        
        return jsonify({'success': False, 'error': 'Authentication required'}), 401
    return decorated_function

# =============================
# DATABASE CONNECTION POOL
# =============================
db_pool = None

def init_db_pool():
    global db_pool
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(
            1, 10,
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME", "jarvis_db"),
            connect_timeout=10
        )
        logger.info("✅ Database connection pool initialized")
    except Exception as e:
        logger.error(f"Failed to create connection pool: {e}")
        raise

def get_db_connection():
    if db_pool:
        return db_pool.getconn()
    # Fallback direct connection
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=os.environ.get("DB_NAME", "jarvis_db"),
        connect_timeout=10
    )

def return_db_connection(conn):
    if db_pool:
        db_pool.putconn(conn)
    else:
        conn.close()

# =============================
# SYSTEM PROMPT
# =============================
SYSTEM_PROMPT = """You are JARVIS, an AI assistant developed by Krish Palival. 
You are designed to be helpful, intelligent, and professional. You provide accurate, thoughtful responses."""

# =============================
# AI FUNCTIONS with retry
# =============================
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")
GROQ_KEY = os.environ.get("GROQ_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")

def call_deepseek(prompt, retry=True):
    try:
        if not DEEPSEEK_KEY:
            return None
        headers = {"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt[:4000]}],
            "max_tokens": 1024,
            "temperature": 0.7
        }
        res = requests.post("https://api.deepseek.com/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        if retry:
            time.sleep(1)
            return call_deepseek(prompt, retry=False)
        logger.warning(f"DeepSeek error: {res.status_code}")
        return None
    except Exception as e:
        logger.error(f"DeepSeek exception: {e}")
        return None

def call_groq(prompt, retry=True):
    try:
        if not GROQ_KEY:
            return None
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "llama3-70b-8192",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt[:4000]}],
            "max_tokens": 1024,
            "temperature": 0.7
        }
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        if retry:
            time.sleep(1)
            return call_groq(prompt, retry=False)
        return None
    except Exception as e:
        logger.error(f"Groq exception: {e}")
        return None

def call_gemini(prompt, retry=True):
    try:
        if not GEMINI_KEY:
            return None
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(f"{SYSTEM_PROMPT}\n\nUser: {prompt[:3000]}")
        return response.text
    except Exception as e:
        logger.error(f"Gemini exception: {e}")
        return None

def call_ai(prompt):
    # Sanitize input
    prompt = prompt.replace("<", "").replace(">", "").strip()
    
    if len(prompt) < 2:
        return "Please write a longer message. I'm here to help!", "fallback"
    
    logger.info(f"AI call started for prompt length: {len(prompt)}")
    
    # Try DeepSeek first
    response = call_deepseek(prompt)
    if response:
        logger.info("DeepSeek responded successfully")
        return response, "deepseek"
    
    # Fallback to Groq
    response = call_groq(prompt)
    if response:
        logger.info("Groq responded successfully")
        return response, "groq"
    
    # Final fallback to Gemini
    response = call_gemini(prompt)
    if response:
        logger.info("Gemini responded successfully")
        return response, "gemini"
    
    logger.error("All AI services failed")
    return "I'm currently experiencing high demand. Please try again in a moment.", "fallback"

# =============================
# HEALTH CHECK ENDPOINT
# =============================
@app.route('/health')
def health_check():
    status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "redis": redis_client is not None,
        "database": False,
        "version": "3.0.0"
    }
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return_db_connection(conn)
        status["database"] = True
    except Exception as e:
        status["status"] = "degraded"
        logger.error(f"Health check DB failed: {e}")
    
    return jsonify(status)

# =============================
# DATABASE INIT
# =============================
def init_db():
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
            total_logins INT DEFAULT 1,
            session_token TEXT
        )
    """)
    
    # 🔥 FIX: Add is_admin column if it doesn't exist
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE")
        logger.info("✅ Added is_admin column")
    except Exception as e:
        logger.warning(f"is_admin column may already exist: {e}")
    
    # Set admin users
    cursor.execute("UPDATE users SET is_admin = TRUE WHERE email IN ('krish@gmail.com', 'admin@jarvis.ai')")
    
    # Rest of your tables...
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            username VARCHAR(100),
            email VARCHAR(255),
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address VARCHAR(45),
            user_agent TEXT,
            success BOOLEAN DEFAULT TRUE,
            error_message TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) DEFAULT 'New Chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
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
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions ON chat_sessions(user_id, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_messages ON messages(session_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_login_logs ON login_logs(user_id, login_time)")
    
    conn.commit()
    cursor.close()
    return_db_connection(conn)
    logger.info("✅ PostgreSQL database initialized")
# =============================
# GOOGLE OAUTH ROUTES
# =============================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", f"{PRODUCTION_DOMAIN}/login/callback")

if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    logger.warning("⚠️ Google OAuth credentials missing")

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
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (user_info['id'], user_info['email']))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("""
                INSERT INTO users (google_id, email, username, display_name, avatar_url) 
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (user_info['id'], user_info['email'], 
                  user_info.get('name', user_info['email'].split('@')[0]).replace(' ', '_'), 
                  user_info.get('name', user_info['email'].split('@')[0]), 
                  user_info.get('picture', '')))
            user_id = cursor.fetchone()['id']
            conn.commit()
        else:
            user_id = user['id']
            cursor.execute("UPDATE users SET last_login = NOW(), total_logins = total_logins + 1 WHERE id = %s", (user_id,))
            conn.commit()
        
        # Generate JWT token
        jwt_token = generate_token(user_id, user_info['email'])
        cursor.execute("UPDATE users SET session_token = %s WHERE id = %s", (jwt_token, user_id))
        conn.commit()
        
        cursor.close()
        return_db_connection(conn)
        
        # Set session (for backward compatibility)
        session['user_id'] = user_id
        session['user_name'] = user_info.get('name', user_info['email'].split('@')[0])
        session['user_email'] = user_info['email']
        session['session_token'] = jwt_token
        
        # Log successful login
        log_conn = get_db_connection()
        log_cursor = log_conn.cursor()
        log_cursor.execute("""
            INSERT INTO login_logs (user_id, username, email, ip_address, user_agent, success) 
            VALUES (%s, %s, %s, %s, %s, TRUE)
        """, (user_id, session['user_name'], session['user_email'], get_client_ip(), request.headers.get('User-Agent', '')))
        log_conn.commit()
        log_cursor.close()
        return_db_connection(log_conn)
        
        return redirect(url_for('index'))
        
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return redirect(url_for('index', error='Authentication failed'))

@app.route('/logout')
def logout():
    if 'user_id' in session:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET session_token = NULL WHERE id = %s", (session['user_id'],))
        conn.commit()
        cursor.close()
        return_db_connection(conn)
    session.clear()
    return redirect(url_for('index'))

@app.route('/logout-all', methods=['POST'])
@login_required
def logout_all_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET session_token = NULL WHERE id = %s", (g.user_id,))
    conn.commit()
    cursor.close()
    return_db_connection(conn)
    session.clear()
    return jsonify({"success": True, "message": "Logged out from all devices"})

@app.route('/api/me')
def get_current_user():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        if payload:
            return jsonify({'success': True, 'user': {
                'id': payload['user_id'],
                'name': payload['email'].split('@')[0],
                'email': payload['email']
            }, 'token': token})
    
    if 'user_id' in session:
        return jsonify({'success': True, 'user': {
            'id': session['user_id'],
            'name': session.get('user_name'),
            'email': session.get('user_email')
        }})
    
    return jsonify({'success': False, 'error': 'Not logged in'}), 401

# =============================
# CHAT ROUTES
# =============================
@app.route("/ai/sessions", methods=["GET"])
@login_required
def get_sessions():
    user_id = g.user_id
    ip = get_client_ip()
    
    if is_rate_limited(user_id, ip, limit=30, window=60):
        logger.warning(f"Rate limit hit for sessions: {user_id}")
        return jsonify({"success": False, "error": "Too many requests"}), 429
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cursor.execute("SELECT COUNT(*) FROM chat_sessions WHERE user_id = %s", (user_id,))
    total = cursor.fetchone()['count']
    
    cursor.execute("""
        SELECT id, title, created_at, updated_at 
        FROM chat_sessions 
        WHERE user_id = %s 
        ORDER BY updated_at DESC 
        LIMIT %s OFFSET %s
    """, (user_id, per_page, offset))
    sessions = cursor.fetchall()
    
    cursor.close()
    return_db_connection(conn)
    
    return jsonify({
        "success": True, 
        "sessions": sessions,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page
        }
    })

@app.route("/ai/session", methods=["POST"])
@login_required
def create_session():
    user_id = g.user_id
    ip = get_client_ip()
    
    if is_rate_limited(user_id, ip, limit=20, window=60):
        return jsonify({"success": False, "error": "Too many requests"}), 429
    
    data = request.json
    title = data.get("title", "New Chat")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_sessions (user_id, title) VALUES (%s, %s) RETURNING id", (user_id, title[:100]))
    session_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    return_db_connection(conn)
    
    return jsonify({"success": True, "session_id": session_id})

@app.route("/ai/session/<int:session_id>", methods=["GET"])
@login_required
def get_session_messages(session_id):
    user_id = g.user_id
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cursor.execute("SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
    if not cursor.fetchone():
        cursor.close()
        return_db_connection(conn)
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    cursor.execute("""
        SELECT role, content, created_at, model_used 
        FROM messages 
        WHERE session_id = %s 
        ORDER BY created_at ASC 
        LIMIT %s OFFSET %s
    """, (session_id, per_page, offset))
    messages = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM messages WHERE session_id = %s", (session_id,))
    total = cursor.fetchone()['count']
    
    cursor.close()
    return_db_connection(conn)
    
    return jsonify({
        "success": True, 
        "messages": messages,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page
        }
    })

@app.route("/ai/session/<int:session_id>/message", methods=["POST"])
@login_required
def add_message(session_id):
    user_id = g.user_id
    ip = get_client_ip()
    data = request.json
    prompt = data.get("prompt", "").strip()
    
    # Input validation
    if not prompt:
        return jsonify({"success": False, "error": "Empty message"}), 400
    
    if len(prompt) > 5000:
        return jsonify({"success": False, "error": "Message too long (max 5000 characters)"}), 400
    
    # Spam detection
    words = prompt.split()
    if len(set(words)) < 3 and len(words) > 5:
        return jsonify({"success": False, "error": "Message looks like spam. Please write a meaningful message."}), 400
    
    # Rate limit: 5 messages per minute
    if is_rate_limited(user_id, ip, limit=5, window=60):
        logger.warning(f"Rate limit hit for messages: {user_id}")
        return jsonify({"success": False, "error": "Too many messages. Please wait a moment."}), 429
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verify ownership
    cursor.execute("SELECT user_id FROM chat_sessions WHERE id = %s", (session_id,))
    result = cursor.fetchone()
    if not result or result[0] != user_id:
        cursor.close()
        return_db_connection(conn)
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    # Save user message
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (session_id, prompt[:5000]))
    
    # Get AI response
    start_time = time.time()
    response, model_used = call_ai(prompt)
    response_time = time.time() - start_time
    
    logger.info(f"AI response time: {response_time:.2f}s, model: {model_used}")
    
    # Save AI response
    cursor.execute("INSERT INTO messages (session_id, role, content, model_used) VALUES (%s, 'assistant', %s, %s)", 
                   (session_id, response[:5000], model_used))
    cursor.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
    
    # Update title if first message
    cursor.execute("SELECT COUNT(*) as count FROM messages WHERE session_id = %s", (session_id,))
    result = cursor.fetchone()
    is_first = result[0] <= 2
    
    if is_first:
        title = prompt[:50] + "..." if len(prompt) > 50 else prompt
        cursor.execute("UPDATE chat_sessions SET title = %s WHERE id = %s", (title, session_id))
    
    conn.commit()
    cursor.close()
    return_db_connection(conn)
    
    return jsonify({
        "success": True, 
        "response": response, 
        "model": model_used, 
        "response_time": round(response_time, 2),
        "is_first_message": is_first
    })

# =============================
# ADMIN DASHBOARD (DB-based)
# =============================
@app.route("/admin/stats", methods=["GET"])
@login_required
def admin_stats():
    # Check admin status from database
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT is_admin FROM users WHERE id = %s", (g.user_id,))
    result = cursor.fetchone()
    
    if not result or not result['is_admin']:
        cursor.close()
        return_db_connection(conn)
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    cursor.execute("SELECT COUNT(*) as total_users FROM users")
    total_users = cursor.fetchone()['total_users']
    
    cursor.execute("SELECT COUNT(*) as total_sessions FROM chat_sessions")
    total_sessions = cursor.fetchone()['total_sessions']
    
    cursor.execute("SELECT COUNT(*) as total_messages FROM messages")
    total_messages = cursor.fetchone()['total_messages']
    
    cursor.execute("SELECT COUNT(*) as active_today FROM login_logs WHERE login_time > NOW() - INTERVAL '24 hours'")
    active_today = cursor.fetchone()['active_today']
    
    cursor.close()
    return_db_connection(conn)
    
    return jsonify({
        "success": True,
        "stats": {
            "total_users": total_users,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "active_today": active_today
        }
    })

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# =============================
# ERROR HANDLERS
# =============================
@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Resource not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({"success": False, "error": "Something went wrong"}), 500

@app.errorhandler(429)
def rate_limit_error(e):
    return jsonify({"success": False, "error": "Too many requests. Please try again later."}), 429

# =============================
# RUN
# =============================
if __name__ == "__main__":
    init_db_pool()
    init_db()
    logger.info("="*60)
    logger.info("🚀 JARVIS SECURE BACKEND - PRODUCTION READY")
    logger.info("✅ Rate limiting: 5 messages/minute")
    logger.info("✅ JWT authentication: Enabled")
    logger.info("✅ Database connection pool: Active")
    logger.info("✅ CORS: Restricted to production domain")
    logger.info("✅ Admin panel: DB-based authorization")
    logger.info("✅ Health check: /health endpoint")
    logger.info("="*60)
    
    port = int(os.environ.get("PORT", 5000))
    
    if os.environ.get("FLASK_ENV") == "production":
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)
