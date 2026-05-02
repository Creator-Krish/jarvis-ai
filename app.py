"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    JARVIS ENTERPRISE AI BACKEND                             ║
║                    WORKING VERSION - NO DATABASE                            ║
║                    Version: 3.1.0 Enterprise                                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import time
import secrets
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from collections import defaultdict
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urlencode
import threading

from flask import (
    Flask, request, jsonify, send_from_directory, 
    session, redirect, g, make_response, Response
)
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import jwt
import requests

# Optional imports
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
    APP_NAME: str = "JARVIS Enterprise AI"
    VERSION: str = "3.1.0"
    ENVIRONMENT: str = os.environ.get("FLASK_ENV", "production")
    PORT: int = int(os.environ.get("PORT", 5000))
    
    PRODUCTION_DOMAIN: str = os.environ.get("FRONTEND_URL", "https://jarvis-e76i.onrender.com")
    FRONTEND_URL: str = PRODUCTION_DOMAIN
    
    SECRET_KEY: str = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    JWT_SECRET: str = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 168
    
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = 'Lax'
    
    RATE_LIMIT_MESSAGES: int = 10
    RATE_LIMIT_WINDOW: int = 60
    RATE_LIMIT_SESSIONS: int = 30
    
    # AI API Keys
    DEEPSEEK_KEY: Optional[str] = os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY: Optional[str] = os.environ.get("GROQ_KEY")
    GEMINI_KEY: Optional[str] = os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY: Optional[str] = os.environ.get("OPENROUTER_KEY")
    
    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = os.environ.get("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: str = os.environ.get(
        "GOOGLE_REDIRECT_URI",
        f"{PRODUCTION_DOMAIN}/login/callback"
    )
    
    MAX_MESSAGE_LENGTH: int = 5000
    MAX_SESSION_TITLE: int = 100
    
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
# DTOs (Complete with all fields)
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

@dataclass
class RateLimitDTO:
    is_allowed: bool
    remaining: int
    reset_at: float
    limit: int
    window: int

# =============================
# SECURITY VALIDATOR
# =============================
class SecurityValidator:
    @staticmethod
    def sanitize_input(text: str, max_length: int = 5000) -> str:
        if not text:
            return ""
        text = text.replace('\x00', '')
        text = ''.join(c for c in text if c == '\n' or c == '\t' or ord(c) >= 32)
        if len(text) > max_length:
            text = text[:max_length]
        text = re.sub(r'<[^>]*>', '', text)
        text = text.replace('javascript:', '').replace('onerror=', '').replace('onload=', '')
        return text.strip()
    
    @staticmethod
    def detect_spam(text: str) -> bool:
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
# RATE LIMITER
# =============================
class RateLimiter:
    def __init__(self):
        self.buckets: Dict[str, Dict] = defaultdict(lambda: {'tokens': 0, 'last_refill': time.time()})
        self._lock = threading.Lock()
    
    def check_rate_limit(self, key: str, limit: int = 10, window: int = 60) -> RateLimitDTO:
        with self._lock:
            now = time.time()
            bucket = self.buckets[key]
            time_passed = now - bucket['last_refill']
            refill = (time_passed / window) * limit
            bucket['tokens'] = min(limit, bucket['tokens'] + refill)
            bucket['last_refill'] = now
            
            if bucket['tokens'] >= 1:
                bucket['tokens'] -= 1
                return RateLimitDTO(True, int(bucket['tokens']), now + window, limit, window)
            
            wait = (1 - bucket['tokens']) * (window / limit)
            return RateLimitDTO(False, 0, now + wait, limit, window)

rate_limiter = RateLimiter()

# =============================
# JWT SERVICE
# =============================
class JWTService:
    @staticmethod
    def create_token(user_id: str, email: str, is_admin: bool = False) -> str:
        payload = {
            'user_id': user_id,
            'email': email,
            'is_admin': is_admin,
            'iat': datetime.now(timezone.utc),
            'exp': datetime.now(timezone.utc) + timedelta(hours=Config.JWT_EXPIRY_HOURS)
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)
    
    @staticmethod
    def verify_token(token: str) -> Optional[Dict]:
        if not token:
            return None
        try:
            return jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None
    
    @staticmethod
    def get_token_from_request() -> Optional[str]:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth.split(' ')[1]
        return request.args.get('token') or session.get('session_token')

# =============================
# IN-MEMORY STORAGE (COMPLETE)
# =============================
class InMemoryStorage:
    def __init__(self):
        self.users: Dict[str, UserDTO] = {}
        self.sessions: Dict[str, SessionDTO] = {}
        self.messages: Dict[str, List[MessageDTO]] = defaultdict(list)
        self.user_sessions: Dict[str, List[str]] = defaultdict(list)
        self._counter = 0
        self._lock = threading.Lock()
    
    def _next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return str(self._counter)
    
    def create_or_update_user(self, google_id: str, email: str, display_name: str, avatar_url: str = "") -> UserDTO:
        if not email:
            raise ValueError("Email is required")
        
        with self._lock:
            # Find existing user
            for user in self.users.values():
                if user.google_id == google_id or user.email.lower() == email.lower():
                    user.display_name = display_name or email.split('@')[0]
                    user.avatar_url = avatar_url
                    user.last_login = datetime.now(timezone.utc)
                    user.total_logins += 1
                    if email.lower() in ['krish@gmail.com', 'admin@jarvis.ai']:
                        user.is_admin = True
                    return user
            
            # Create new user
            user_id = self._next_id()
            user = UserDTO(
                id=user_id,
                google_id=google_id,
                email=email.lower(),
                display_name=display_name or email.split('@')[0],
                avatar_url=avatar_url,
                is_admin=email.lower() in ['krish@gmail.com', 'admin@jarvis.ai'],
                created_at=datetime.now(timezone.utc),
                last_login=datetime.now(timezone.utc),
                total_logins=1,
                session_token=None
            )
            self.users[user_id] = user
            logger.info(f"✅ New user created: {email}")
            return user
    
    def find_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        return self.users.get(user_id)
    
    def update_session_token(self, user_id: str, token: Optional[str]):
        user = self.users.get(user_id)
        if user:
            user.session_token = token
    
    def create_session(self, user_id: str, title: str = "New Chat") -> Optional[SessionDTO]:
        with self._lock:
            session_id = self._next_id()
            session_obj = SessionDTO(
                id=session_id,
                user_id=user_id,
                title=SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE)[:100],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            self.sessions[session_id] = session_obj
            self.user_sessions[user_id].append(session_id)
            logger.info(f"Session created: {session_id} for user {user_id}")
            return session_obj
    
    def get_user_sessions(self, user_id: str) -> List[SessionDTO]:
        session_ids = self.user_sessions.get(user_id, [])
        sessions_list = []
        for sid in session_ids:
            if sid in self.sessions:
                sessions_list.append(self.sessions[sid])
        return sorted(sessions_list, key=lambda x: x.updated_at or datetime.min, reverse=True)
    
    def get_session(self, session_id: str, user_id: str) -> Optional[SessionDTO]:
        session_obj = self.sessions.get(session_id)
        if session_obj and session_obj.user_id == user_id:
            return session_obj
        return None
    
    def delete_session(self, session_id: str, user_id: str) -> bool:
        with self._lock:
            session_obj = self.sessions.get(session_id)
            if not session_obj or session_obj.user_id != user_id:
                return False
            
            del self.sessions[session_id]
            if session_id in self.messages:
                del self.messages[session_id]
            
            user_sessions = self.user_sessions.get(user_id, [])
            if session_id in user_sessions:
                user_sessions.remove(session_id)
            
            logger.info(f"Session deleted: {session_id}")
            return True
    
    def update_session_title(self, session_id: str, title: str):
        session_obj = self.sessions.get(session_id)
        if session_obj:
            session_obj.title = SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE)[:100]
            session_obj.updated_at = datetime.now(timezone.utc)
    
    def add_message(self, session_id: str, role: str, content: str, model_used: str = None) -> Optional[MessageDTO]:
        session_obj = self.sessions.get(session_id)
        if not session_obj:
            return None
        
        with self._lock:
            msg_id = self._next_id()
            message = MessageDTO(
                id=msg_id,
                session_id=session_id,
                role=role,
                content=SecurityValidator.sanitize_input(content, Config.MAX_MESSAGE_LENGTH),
                model_used=model_used,
                created_at=datetime.now(timezone.utc)
            )
            self.messages[session_id].append(message)
            session_obj.updated_at = datetime.now(timezone.utc)
            return message
    
    def get_messages(self, session_id: str) -> List[MessageDTO]:
        return sorted(self.messages.get(session_id, []), key=lambda x: x.created_at or datetime.min)
    
    def get_message_count(self, session_id: str) -> int:
        return len(self.messages.get(session_id, []))

storage = InMemoryStorage()

# =============================
# AI SERVICE
# =============================
class AIService:
    def __init__(self):
        self.system_prompt = "You are JARVIS, an advanced AI assistant. Be helpful and professional."
    
    def call_deepseek(self, prompt: str) -> Optional[str]:
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
    
    def call_groq(self, prompt: str) -> Optional[str]:
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
    
    def call_gemini(self, prompt: str) -> Optional[str]:
        if not Config.GEMINI_KEY or not GEMINI_AVAILABLE:
            return None
        try:
            genai.configure(api_key=Config.GEMINI_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(prompt[:3000])
            return resp.text if resp else None
        except:
            return None
    
    def generate_response(self, prompt: str, preferred_model: str = None) -> Tuple[str, str, float]:
        start = time.time()
        prompt = SecurityValidator.sanitize_input(prompt, Config.MAX_MESSAGE_LENGTH)
        
        if SecurityValidator.detect_spam(prompt):
            return "Please rephrase your message.", "system", 0
        
        models = [
            ('deepseek', self.call_deepseek),
            ('groq', self.call_groq),
            ('gemini', self.call_gemini)
        ]
        
        if preferred_model:
            models = [(m, f) for m, f in models if m == preferred_model] + [(m, f) for m, f in models if m != preferred_model]
        
        for name, func in models:
            try:
                resp = func(prompt)
                if resp:
                    return resp, name, round(time.time() - start, 2)
            except:
                continue
        
        return "I'm having trouble connecting. Please try again.", "fallback", round(time.time() - start, 2)

ai_service = AIService()

# =============================
# FLASK APP
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

# CORS
CORS(app, origins=[Config.FRONTEND_URL, "https://jarvis-e76i.onrender.com"], supports_credentials=True)

# =============================
# HELPERS
# =============================
def get_client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For')
    return forwarded.split(',')[0].strip() if forwarded else request.remote_addr or '0.0.0.0'

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = JWTService.get_token_from_request()
        user = None
        if token:
            payload = JWTService.verify_token(token)
            if payload:
                user = storage.find_user_by_id(payload['user_id'])
        if not user and 'user_id' in session:
            user = storage.find_user_by_id(session['user_id'])
        if not user:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated

# =============================
# ROUTES
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
    
    state = secrets.token_hex(32)
    session['oauth_state'] = state
    
    params = {
        'client_id': Config.GOOGLE_CLIENT_ID,
        'redirect_uri': Config.GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'email profile',
        'state': state
    }
    auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
    return redirect(auth_url)

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
        # Exchange code for token
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
        
        user_info = user_res.json()
        logger.info(f"User authenticated: {user_info.get('email')}")
        
        # Create user
        user = storage.create_or_update_user(
            google_id=user_info['id'],
            email=user_info['email'],
            display_name=user_info.get('name', user_info['email'].split('@')[0]),
            avatar_url=user_info.get('picture', '')
        )
        
        # Create JWT
        jwt_token = JWTService.create_token(user.id, user.email, user.is_admin)
        storage.update_session_token(user.id, jwt_token)
        session['user_id'] = user.id
        session['session_token'] = jwt_token
        
        return redirect(f"{Config.FRONTEND_URL}?token={jwt_token}")
        
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return redirect(f"{Config.FRONTEND_URL}?error=Auth+failed")

@app.route('/logout')
def logout():
    if 'user_id' in session:
        storage.update_session_token(session['user_id'], None)
    session.clear()
    return redirect(Config.FRONTEND_URL)

@app.route('/api/me')
def get_current_user():
    token = JWTService.get_token_from_request()
    if token:
        payload = JWTService.verify_token(token)
        if payload:
            user = storage.find_user_by_id(payload['user_id'])
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
        user = storage.find_user_by_id(session['user_id'])
        if user:
            return jsonify({
                'success': True,
                'user': {
                    'id': user.id,
                    'name': user.display_name,
                    'email': user.email
                }
            })
    
    return jsonify({'success': False, 'error': 'Not authenticated'}), 401

@app.route('/ai/sessions', methods=['GET'])
@login_required
def get_sessions():
    sessions = storage.get_user_sessions(g.current_user.id)
    return jsonify({'success': True, 'sessions': [asdict(s) for s in sessions]})

@app.route('/ai/session', methods=['POST'])
@login_required
def create_session():
    data = request.json or {}
    session_obj = storage.create_session(g.current_user.id, data.get('title', 'New Chat'))
    return jsonify({'success': True, 'session_id': session_obj.id, 'session': asdict(session_obj)})

@app.route('/ai/session/<session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    session_obj = storage.get_session(session_id, g.current_user.id)
    if not session_obj:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    messages = storage.get_messages(session_id)
    return jsonify({'success': True, 'session': asdict(session_obj), 'messages': [asdict(m) for m in messages]})

@app.route('/ai/session/<session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id):
    if storage.delete_session(session_id, g.current_user.id):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.route('/ai/session/<session_id>/message', methods=['POST'])
@login_required
def send_message(session_id):
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    
    if not prompt:
        return jsonify({'success': False, 'error': 'Empty message'}), 400
    
    if not storage.get_session(session_id, g.current_user.id):
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    # Save user message
    storage.add_message(session_id, 'user', prompt)
    
    # Check if first message
    is_first = storage.get_message_count(session_id) <= 2
    
    # Generate AI response
    response, model, resp_time = ai_service.generate_response(prompt, data.get('model'))
    
    # Save AI response
    storage.add_message(session_id, 'assistant', response, model)
    
    # Update title if first message
    if is_first:
        title = prompt[:50] + ('...' if len(prompt) > 50 else '')
        storage.update_session_title(session_id, title)
    
    return jsonify({
        'success': True,
        'response': response,
        'model': model,
        'response_time': resp_time,
        'is_first_message': is_first
    })

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return send_from_directory('.', 'index.html')

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}")
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

# =============================
# STARTUP
# =============================
if __name__ == "__main__":
    Config.display()
    logger.info("🚀 JARVIS starting - NO DATABASE MODE")
    
    port = Config.PORT
    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=port, threads=4)
        except ImportError:
            app.run(host="0.0.0.0", port=port, debug=False)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)
