"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    JARVIS ENTERPRISE AI BACKEND v3.1                        ║
║                    PRODUCTION - NO DATABASE                                 ║
║                    FULLY FIXED & OPTIMIZED                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import time
import secrets
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from collections import defaultdict
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urlencode

from flask import (
    Flask, request, jsonify, send_from_directory,
    session, redirect, g, make_response
)
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import jwt
import requests

# Optional AI
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# =============================
# LOGGING
# =============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('jarvis')
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# =============================
# CONFIGURATION
# =============================
class Config:
    APP_NAME = "JARVIS Enterprise AI"
    VERSION = "3.1.0"
    ENVIRONMENT = os.environ.get("FLASK_ENV", "production")
    PORT = int(os.environ.get("PORT", 5000))
    
    PRODUCTION_DOMAIN = os.environ.get("FRONTEND_URL", "https://jarvis-e76i.onrender.com")
    FRONTEND_URL = PRODUCTION_DOMAIN
    
    SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRY_HOURS = 168
    
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    RATE_LIMIT_MESSAGES = 10
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_SESSIONS = 30
    
    # AI Keys
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY")
    
    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.environ.get(
        "GOOGLE_REDIRECT_URI",
        f"{PRODUCTION_DOMAIN}/login/callback"
    )
    
    MAX_MESSAGE_LENGTH = 5000
    MAX_SESSION_TITLE = 100

    @classmethod
    def display(cls):
        logger.info("╔════════════════════════════════════════════════════════╗")
        logger.info(f"║  {cls.APP_NAME} v{cls.VERSION}                         ║")
        logger.info("╠════════════════════════════════════════════════════════╣")
        logger.info(f"║  Environment:  {cls.ENVIRONMENT}                       ║")
        logger.info(f"║  Port:         {cls.PORT}                              ║")
        logger.info(f"║  Domain:       {cls.PRODUCTION_DOMAIN}                 ║")
        logger.info(f"║  OAuth:        {'✅' if cls.GOOGLE_CLIENT_ID else '❌'}               ║")
        logger.info(f"║  DeepSeek:     {'✅' if cls.DEEPSEEK_KEY else '❌'}               ║")
        logger.info(f"║  Groq:         {'✅' if cls.GROQ_KEY else '❌'}               ║")
        logger.info("╚════════════════════════════════════════════════════════╝")

# =============================
# DTOs (Complete)
# =============================
@dataclass
class UserDTO:
    id: str
    google_id: Optional[str]
    email: str
    display_name: str
    avatar_url: Optional[str]
    is_admin: bool = False
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    total_logins: int = 1
    session_token: Optional[str] = None

@dataclass
class SessionDTO:
    id: str
    user_id: str
    title: str = "New Chat"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class MessageDTO:
    id: str
    session_id: str
    role: str
    content: str
    model_used: Optional[str] = None
    created_at: Optional[datetime] = None

# =============================
# Security
# =============================
class Security:
    @staticmethod
    def sanitize(text: str, max_len: int = 5000) -> str:
        if not text:
            return ""
        text = text.replace('\x00', '')
        text = ''.join(c for c in text if c == '\n' or c == '\t' or ord(c) >= 32)
        if len(text) > max_len:
            text = text[:max_len]
        text = re.sub(r'<[^>]*>', '', text)
        text = text.replace('javascript:', '').replace('onerror=', '')
        return text.strip()
    
    @staticmethod
    def is_spam(text: str) -> bool:
        if not text or len(text) < 2:
            return True
        words = text.split()
        if len(words) > 5 and len(set(words)) < 3:
            return True
        if len(text) > 20 and text.isupper():
            return True
        return False
    
    @staticmethod
    def generate_token(length: int = 32) -> str:
        return secrets.token_hex(length)

# =============================
# Rate Limiter (Thread-safe)
# =============================
class RateLimiter:
    def __init__(self):
        self.buckets = defaultdict(lambda: {'tokens': 0, 'last_refill': time.time()})
        self.lock = threading.Lock()
    
    def check(self, key: str, limit: int, window: int) -> Tuple[bool, int, float]:
        with self.lock:
            now = time.time()
            bucket = self.buckets[key]
            elapsed = now - bucket['last_refill']
            bucket['tokens'] = min(limit, bucket['tokens'] + elapsed * (limit / window))
            bucket['last_refill'] = now
            
            if bucket['tokens'] >= 1:
                bucket['tokens'] -= 1
                return True, int(bucket['tokens']), now + window
            else:
                wait = (1 - bucket['tokens']) * (window / limit)
                return False, 0, now + wait

rate_limiter = RateLimiter()

# =============================
# JWT Service
# =============================
class JWTService:
    @staticmethod
    def create(user_id: str, email: str, is_admin: bool = False) -> str:
        payload = {
            'user_id': user_id,
            'email': email,
            'is_admin': is_admin,
            'iat': datetime.now(timezone.utc),
            'exp': datetime.now(timezone.utc) + timedelta(hours=Config.JWT_EXPIRY_HOURS)
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)
    
    @staticmethod
    def verify(token: str) -> Optional[Dict]:
        if not token:
            return None
        try:
            return jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
        except:
            return None
    
    @staticmethod
    def from_request() -> Optional[str]:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth.split(' ')[1]
        return request.args.get('token') or session.get('session_token')

# =============================
# In-Memory Storage (Thread-safe)
# =============================
class Storage:
    def __init__(self):
        self.users: Dict[str, UserDTO] = {}
        self.sessions: Dict[str, SessionDTO] = {}
        self.messages: Dict[str, List[MessageDTO]] = defaultdict(list)
        self.user_sessions: Dict[str, List[str]] = defaultdict(list)
        self.counter = 0
        self.lock = threading.Lock()
    
    def _next_id(self) -> str:
        with self.lock:
            self.counter += 1
            return str(self.counter)
    
    def create_or_update_user(self, google_id: str, email: str, name: str, avatar: str = "") -> UserDTO:
        if not email:
            raise ValueError("Email required")
        email = email.lower().strip()
        with self.lock:
            for user in self.users.values():
                if user.google_id == google_id or user.email == email:
                    user.display_name = name or email.split('@')[0]
                    user.avatar_url = avatar
                    user.last_login = datetime.now(timezone.utc)
                    user.total_logins += 1
                    if email in ('krish@gmail.com', 'admin@jarvis.ai'):
                        user.is_admin = True
                    return user
            uid = self._next_id()
            user = UserDTO(
                id=uid, google_id=google_id, email=email,
                display_name=name or email.split('@')[0], avatar_url=avatar,
                is_admin=email in ('krish@gmail.com', 'admin@jarvis.ai'),
                created_at=datetime.now(timezone.utc),
                last_login=datetime.now(timezone.utc),
                total_logins=1
            )
            self.users[uid] = user
            logger.info(f"New user: {email}")
            return user
    
    def get_user(self, uid: str) -> Optional[UserDTO]:
        return self.users.get(uid)
    
    def set_session_token(self, uid: str, token: Optional[str]):
        if u := self.users.get(uid):
            u.session_token = token
    
    def create_session(self, uid: str, title: str = "New Chat") -> SessionDTO:
        with self.lock:
            sid = self._next_id()
            sess = SessionDTO(
                id=sid, user_id=uid,
                title=Security.sanitize(title, Config.MAX_SESSION_TITLE)[:100],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            self.sessions[sid] = sess
            self.user_sessions[uid].append(sid)
            return sess
    
    def get_user_sessions(self, uid: str) -> List[SessionDTO]:
        sids = self.user_sessions.get(uid, [])
        return sorted([self.sessions[sid] for sid in sids if sid in self.sessions],
                      key=lambda x: x.updated_at or datetime.min, reverse=True)
    
    def get_session(self, sid: str, uid: str) -> Optional[SessionDTO]:
        sess = self.sessions.get(sid)
        if sess and sess.user_id == uid:
            return sess
        return None
    
    def delete_session(self, sid: str, uid: str) -> bool:
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess or sess.user_id != uid:
                return False
            del self.sessions[sid]
            if sid in self.messages:
                del self.messages[sid]
            if sid in self.user_sessions[uid]:
                self.user_sessions[uid].remove(sid)
            return True
    
    def update_session_title(self, sid: str, title: str):
        if sess := self.sessions.get(sid):
            sess.title = Security.sanitize(title, Config.MAX_SESSION_TITLE)[:100]
            sess.updated_at = datetime.now(timezone.utc)
    
    def add_message(self, sid: str, role: str, content: str, model: str = None) -> Optional[MessageDTO]:
        if sid not in self.sessions:
            return None
        with self.lock:
            mid = self._next_id()
            msg = MessageDTO(
                id=mid, session_id=sid, role=role,
                content=Security.sanitize(content, Config.MAX_MESSAGE_LENGTH),
                model_used=model, created_at=datetime.now(timezone.utc)
            )
            self.messages[sid].append(msg)
            if sess := self.sessions.get(sid):
                sess.updated_at = datetime.now(timezone.utc)
            return msg
    
    def get_messages(self, sid: str) -> List[MessageDTO]:
        return sorted(self.messages.get(sid, []), key=lambda x: x.created_at or datetime.min)
    
    def message_count(self, sid: str) -> int:
        return len(self.messages.get(sid, []))

storage = Storage()

# =============================
# AI Service (Multi-fallback)
# =============================
class AIService:
    def __init__(self):
        self.system_prompt = "You are JARVIS, a helpful AI assistant."
    
    def _call_deepseek(self, prompt: str) -> Optional[str]:
        if not Config.DEEPSEEK_KEY:
            return None
        try:
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt[:3000]}]},
                headers={"Authorization": f"Bearer {Config.DEEPSEEK_KEY}", "Content-Type": "application/json"},
                timeout=25
            )
            return resp.json()['choices'][0]['message']['content'] if resp.status_code == 200 else None
        except:
            return None
    
    def _call_groq(self, prompt: str) -> Optional[str]:
        if not Config.GROQ_KEY:
            return None
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={"model": "llama3-70b-8192", "messages": [{"role": "user", "content": prompt[:3000]}]},
                headers={"Authorization": f"Bearer {Config.GROQ_KEY}", "Content-Type": "application/json"},
                timeout=25
            )
            return resp.json()['choices'][0]['message']['content'] if resp.status_code == 200 else None
        except:
            return None
    
    def _call_gemini(self, prompt: str) -> Optional[str]:
        if not Config.GEMINI_KEY or not GEMINI_AVAILABLE:
            return None
        try:
            genai.configure(api_key=Config.GEMINI_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt[:3000])
            return resp.text if resp else None
        except:
            return None
    
    def generate(self, prompt: str, preferred: str = None) -> Tuple[str, str, float]:
        start = time.time()
        prompt = Security.sanitize(prompt, Config.MAX_MESSAGE_LENGTH)
        if Security.is_spam(prompt):
            return "Please rephrase your message.", "system", 0
        
        models = [
            ('deepseek', self._call_deepseek),
            ('groq', self._call_groq),
            ('gemini', self._call_gemini)
        ]
        if preferred:
            models = [(m, f) for m, f in models if m == preferred] + [(m, f) for m, f in models if m != preferred]
        
        for name, func in models:
            try:
                resp = func(prompt)
                if resp:
                    return resp, name, round(time.time() - start, 2)
            except:
                continue
        return "AI service unavailable. Please try again.", "fallback", round(time.time() - start, 2)

ai = AIService()

# =============================
# Flask App
# =============================
app = Flask(__name__, static_folder='.')
app.secret_key = Config.SECRET_KEY
app.config.update(
    SECRET_KEY=Config.SECRET_KEY,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CORS(app, origins=[Config.FRONTEND_URL], supports_credentials=True)

# =============================
# Helpers
# =============================
def get_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For')
    return forwarded.split(',')[0].strip() if forwarded else request.remote_addr or '0.0.0.0'

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = JWTService.from_request()
        user = None
        if token:
            payload = JWTService.verify(token)
            if payload:
                user = storage.get_user(payload['user_id'])
        if not user and 'user_id' in session:
            user = storage.get_user(session['user_id'])
        if not user:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        g.user = user
        return f(*args, **kwargs)
    return decorated

# =============================
# Routes
# =============================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'version': Config.VERSION})

@app.route('/login/google')
def google_login():
    if not Config.GOOGLE_CLIENT_ID:
        return redirect(f"{Config.FRONTEND_URL}?error=OAuth+not+configured")
    state = Security.generate_token(32)
    session['oauth_state'] = state
    params = {
        'client_id': Config.GOOGLE_CLIENT_ID,
        'redirect_uri': Config.GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'email profile',
        'state': state
    }
    return redirect(f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}")

@app.route('/login/callback')
def google_callback():
    error = request.args.get('error')
    if error:
        return redirect(f"{Config.FRONTEND_URL}?error={error}")
    code = request.args.get('code')
    if not code:
        return redirect(f"{Config.FRONTEND_URL}?error=No+code")
    state = request.args.get('state')
    saved_state = session.pop('oauth_state', None)
    if saved_state and state != saved_state:
        return redirect(f"{Config.FRONTEND_URL}?error=Invalid+state")
    try:
        # Exchange code
        token_res = requests.post('https://oauth2.googleapis.com/token', data={
            'code': code,
            'client_id': Config.GOOGLE_CLIENT_ID,
            'client_secret': Config.GOOGLE_CLIENT_SECRET,
            'redirect_uri': Config.GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code'
        }, timeout=15)
        if token_res.status_code != 200:
            return redirect(f"{Config.FRONTEND_URL}?error=Token+failed")
        access_token = token_res.json().get('access_token')
        # Get user info
        user_res = requests.get(
            'https://www.googleapis.com/oauth2/v1/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=15
        )
        if user_res.status_code != 200:
            return redirect(f"{Config.FRONTEND_URL}?error=User+info+failed")
        info = user_res.json()
        user = storage.create_or_update_user(
            google_id=info['id'],
            email=info['email'],
            name=info.get('name', info['email'].split('@')[0]),
            avatar=info.get('picture', '')
        )
        token = JWTService.create(user.id, user.email, user.is_admin)
        storage.set_session_token(user.id, token)
        session['user_id'] = user.id
        session['session_token'] = token
        return redirect(f"{Config.FRONTEND_URL}?token={token}")
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return redirect(f"{Config.FRONTEND_URL}?error=Auth+failed")

@app.route('/logout')
def logout():
    if 'user_id' in session:
        storage.set_session_token(session['user_id'], None)
    session.clear()
    return redirect(Config.FRONTEND_URL)

@app.route('/api/me')
def me():
    token = JWTService.from_request()
    if token:
        payload = JWTService.verify(token)
        if payload:
            user = storage.get_user(payload['user_id'])
            if user:
                return jsonify({
                    'success': True,
                    'user': {
                        'id': user.id,
                        'name': user.display_name,
                        'email': user.email,
                        'avatar': user.avatar_url,
                        'is_admin': user.is_admin
                    },
                    'token': token
                })
    if 'user_id' in session:
        user = storage.get_user(session['user_id'])
        if user:
            return jsonify({'success': True, 'user': {
                'id': user.id, 'name': user.display_name, 'email': user.email
            }})
    return jsonify({'success': False, 'error': 'Not authenticated'}), 401

@app.route('/ai/sessions', methods=['GET'])
@login_required
def list_sessions():
    allowed, remaining, reset = rate_limiter.check(f"sessions:{g.user.id}", Config.RATE_LIMIT_SESSIONS, 60)
    if not allowed:
        return jsonify({'success': False, 'error': 'Too many requests'}), 429
    sessions = storage.get_user_sessions(g.user.id)
    return jsonify({'success': True, 'sessions': [asdict(s) for s in sessions]})

@app.route('/ai/session', methods=['POST'])
@login_required
def new_session():
    data = request.json or {}
    sess = storage.create_session(g.user.id, data.get('title', 'New Chat'))
    return jsonify({'success': True, 'session_id': sess.id, 'session': asdict(sess)})

@app.route('/ai/session/<sid>', methods=['GET'])
@login_required
def get_session(sid):
    sess = storage.get_session(sid, g.user.id)
    if not sess:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    msgs = storage.get_messages(sid)
    return jsonify({'success': True, 'session': asdict(sess), 'messages': [asdict(m) for m in msgs]})

@app.route('/ai/session/<sid>', methods=['DELETE'])
@login_required
def del_session(sid):
    if storage.delete_session(sid, g.user.id):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.route('/ai/session/<sid>/message', methods=['POST'])
@login_required
def send_msg(sid):
    allowed, remaining, reset = rate_limiter.check(f"msg:{g.user.id}", Config.RATE_LIMIT_MESSAGES, Config.RATE_LIMIT_WINDOW)
    if not allowed:
        return jsonify({'success': False, 'error': 'Rate limited', 'retry_after': reset - time.time()}), 429
    
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    if not prompt:
        return jsonify({'success': False, 'error': 'Empty message'}), 400
    
    if not storage.get_session(sid, g.user.id):
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    # Save user message
    storage.add_message(sid, 'user', prompt)
    is_first = storage.message_count(sid) <= 2
    
    # AI response
    response, model, elapsed = ai.generate(prompt, data.get('model'))
    storage.add_message(sid, 'assistant', response, model)
    
    if is_first:
        title = prompt[:50] + ('...' if len(prompt) > 50 else '')
        storage.update_session_title(sid, title)
    
    return jsonify({
        'success': True,
        'response': response,
        'model': model,
        'response_time': elapsed,
        'is_first_message': is_first,
        'remaining_requests': remaining
    })

@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return send_from_directory('.', 'index.html')

@app.errorhandler(500)
def handle_500(e):
    logger.error(f"Server error: {e}")
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

# =============================
# Main
# =============================
if __name__ == "__main__":
    Config.display()
    logger.info("🚀 JARVIS starting (In-Memory, No DB)")
    port = Config.PORT
    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port, threads=4)
        except ImportError:
            app.run(host="0.0.0.0", port=port, debug=False)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)
