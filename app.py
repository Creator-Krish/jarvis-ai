import os
import secrets
import logging
import re
import time
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
import jwt
import requests

# =============================
# LOGGING
# =============================
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger('jarvis')

# =============================
# CONFIG
# =============================
class Config:
    PORT = int(os.environ.get("PORT", 5000))
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://jarvis-e76i.onrender.com")
    SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    
    # Google OAuth - IMPORTANT: These MUST be exact
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = f"{FRONTEND_URL}/login/callback"
    
    # AI Keys
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_KEY")
    
    MAX_MESSAGE_LENGTH = 5000

# =============================
# DATA CLASSES
# =============================
@dataclass
class UserDTO:
    id: str
    email: str
    name: str
    avatar: str = ""
    is_admin: bool = False

@dataclass
class SessionDTO:
    id: str
    user_id: str
    title: str = "New Chat"
    created_at: float = 0
    updated_at: float = 0

@dataclass
class MessageDTO:
    id: str
    session_id: str
    role: str
    content: str
    model: str = ""
    created_at: float = 0

# =============================
# IN-MEMORY STORAGE
# =============================
class Storage:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.messages = {}
        self.user_sessions = {}
        self.counter = 0
    
    def next_id(self):
        self.counter += 1
        return str(self.counter)
    
    def get_or_create_user(self, email, name, google_id, avatar=""):
        email = email.lower().strip()
        for uid, user in self.users.items():
            if user.email == email or (google_id and user.id == google_id):
                user.name = name
                user.avatar = avatar
                return user
        
        uid = self.next_id()
        user = UserDTO(
            id=uid, email=email, name=name, avatar=avatar,
            is_admin=email in ['krish@gmail.com', 'admin@jarvis.ai']
        )
        self.users[uid] = user
        logger.info(f"New user: {email}")
        return user
    
    def get_user(self, uid):
        return self.users.get(uid)
    
    def create_session(self, user_id, title="New Chat"):
        sid = self.next_id()
        now = time.time()
        sess = SessionDTO(id=sid, user_id=user_id, title=title[:100], created_at=now, updated_at=now)
        self.sessions[sid] = sess
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = []
        self.user_sessions[user_id].append(sid)
        return sess
    
    def get_user_sessions(self, user_id):
        sids = self.user_sessions.get(user_id, [])
        sessions = [self.sessions[sid] for sid in sids if sid in self.sessions]
        return sorted(sessions, key=lambda x: x.updated_at, reverse=True)
    
    def get_session(self, sid, user_id):
        sess = self.sessions.get(sid)
        if sess and sess.user_id == user_id:
            return sess
        return None
    
    def delete_session(self, sid, user_id):
        sess = self.sessions.get(sid)
        if not sess or sess.user_id != user_id:
            return False
        del self.sessions[sid]
        if sid in self.messages:
            del self.messages[sid]
        if user_id in self.user_sessions and sid in self.user_sessions[user_id]:
            self.user_sessions[user_id].remove(sid)
        return True
    
    def update_session_title(self, sid, title):
        if sid in self.sessions:
            self.sessions[sid].title = title[:100]
            self.sessions[sid].updated_at = time.time()
    
    def add_message(self, sid, role, content, model=""):
        if sid not in self.sessions:
            return None
        mid = self.next_id()
        msg = MessageDTO(
            id=mid, session_id=sid, role=role, content=content[:5000],
            model=model, created_at=time.time()
        )
        if sid not in self.messages:
            self.messages[sid] = []
        self.messages[sid].append(msg)
        if sid in self.sessions:
            self.sessions[sid].updated_at = time.time()
        return msg
    
    def get_messages(self, sid):
        return sorted(self.messages.get(sid, []), key=lambda x: x.created_at)
    
    def message_count(self, sid):
        return len(self.messages.get(sid, []))

storage = Storage()

# =============================
# JWT SERVICE
# =============================
class JWTService:
    @staticmethod
    def create(user_id, email, is_admin=False):
        payload = {
            'user_id': user_id,
            'email': email,
            'is_admin': is_admin,
            'exp': datetime.now(timezone.utc) + timedelta(days=7)
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm='HS256')
    
    @staticmethod
    def verify(token):
        try:
            return jwt.decode(token, Config.JWT_SECRET, algorithms=['HS256'])
        except:
            return None

# =============================
# AI SERVICE
# =============================
class AIService:
    def generate(self, prompt):
        prompt = prompt[:3000]
        
        # Try DeepSeek
        if Config.DEEPSEEK_KEY:
            try:
                resp = requests.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}]},
                    headers={"Authorization": f"Bearer {Config.DEEPSEEK_KEY}", "Content-Type": "application/json"},
                    timeout=20
                )
                if resp.status_code == 200:
                    return resp.json()['choices'][0]['message']['content'], "deepseek"
            except:
                pass
        
        # Try Groq
        if Config.GROQ_KEY:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": "llama3-70b-8192", "messages": [{"role": "user", "content": prompt}]},
                    headers={"Authorization": f"Bearer {Config.GROQ_KEY}", "Content-Type": "application/json"},
                    timeout=20
                )
                if resp.status_code == 200:
                    return resp.json()['choices'][0]['message']['content'], "groq"
            except:
                pass
        
        # Fallback response
        return "I'm JARVIS. How can I help you today?", "fallback"

ai_service = AIService()

# =============================
# FLASK APP
# =============================
app = Flask(__name__, static_folder='.')
app.secret_key = Config.SECRET_KEY
app.config.update(
    SECRET_KEY=Config.SECRET_KEY,
    SESSION_COOKIE_SECURE=False,  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

CORS(app, origins=[Config.FRONTEND_URL], supports_credentials=True)

# =============================
# DECORATORS
# =============================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth.replace('Bearer ', '') if auth.startswith('Bearer') else None
        if not token:
            token = request.args.get('token')
        
        if token:
            payload = JWTService.verify(token)
            if payload:
                user = storage.get_user(payload['user_id'])
                if user:
                    g.user = user
                    return f(*args, **kwargs)
        
        return jsonify({'success': False, 'error': 'Authentication required'}), 401
    return decorated

# =============================
# ROUTES
# =============================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': '3.1'})

@app.route('/login/google')
def google_login():
    if not Config.GOOGLE_CLIENT_ID:
        return redirect(f"{Config.FRONTEND_URL}?error=no_oauth")
    
    # Generate state for CSRF
    state = secrets.token_hex(32)
    session['oauth_state'] = state
    
    params = {
        'client_id': Config.GOOGLE_CLIENT_ID,
        'redirect_uri': Config.GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'email profile',
        'state': state,
        'access_type': 'online'
    }
    
    auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"
    return redirect(auth_url)

@app.route('/login/callback')
def google_callback():
    # Check for error
    error = request.args.get('error')
    if error:
        logger.error(f"OAuth error: {error}")
        return redirect(f"{Config.FRONTEND_URL}?error={error}")
    
    # Get code
    code = request.args.get('code')
    if not code:
        return redirect(f"{Config.FRONTEND_URL}?error=no_code")
    
    # Verify state
    state = request.args.get('state')
    saved_state = session.pop('oauth_state', None)
    if saved_state and state != saved_state:
        logger.error(f"State mismatch")
        return redirect(f"{Config.FRONTEND_URL}?error=invalid_state")
    
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
            logger.error(f"Token exchange failed: {token_res.text}")
            return redirect(f"{Config.FRONTEND_URL}?error=token_failed")
        
        token_data = token_res.json()
        access_token = token_data.get('access_token')
        
        if not access_token:
            return redirect(f"{Config.FRONTEND_URL}?error=no_access_token")
        
        # Get user info
        user_res = requests.get(
            'https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=15
        )
        
        if user_res.status_code != 200:
            logger.error(f"User info failed: {user_res.text}")
            return redirect(f"{Config.FRONTEND_URL}?error=user_info_failed")
        
        user_info = user_res.json()
        email = user_info.get('email')
        name = user_info.get('name', email.split('@')[0])
        google_id = user_info.get('id')
        picture = user_info.get('picture', '')
        
        if not email:
            return redirect(f"{Config.FRONTEND_URL}?error=no_email")
        
        # Create/update user
        user = storage.get_or_create_user(email, name, google_id, picture)
        
        # Create JWT
        token = JWTService.create(user.id, user.email, user.is_admin)
        
        # Clear session and set new
        session.clear()
        session['user_id'] = user.id
        
        # Redirect with token
        return redirect(f"{Config.FRONTEND_URL}?token={token}")
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        return redirect(f"{Config.FRONTEND_URL}?error=auth_failed")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(Config.FRONTEND_URL)

@app.route('/api/me')
def me():
    auth = request.headers.get('Authorization', '')
    token = auth.replace('Bearer ', '') if auth.startswith('Bearer') else request.args.get('token')
    
    if token:
        payload = JWTService.verify(token)
        if payload:
            user = storage.get_user(payload['user_id'])
            if user:
                return jsonify({
                    'success': True,
                    'user': {
                        'id': user.id,
                        'name': user.name,
                        'email': user.email,
                        'avatar': user.avatar,
                        'is_admin': user.is_admin
                    },
                    'token': token
                })
    
    return jsonify({'success': False, 'error': 'Not authenticated'}), 401

@app.route('/ai/sessions', methods=['GET'])
@login_required
def get_sessions():
    sessions = storage.get_user_sessions(g.user.id)
    return jsonify({'success': True, 'sessions': [asdict(s) for s in sessions]})

@app.route('/ai/session', methods=['POST'])
@login_required
def create_session():
    data = request.json or {}
    sess = storage.create_session(g.user.id, data.get('title', 'New Chat'))
    return jsonify({'success': True, 'session_id': sess.id, 'session': asdict(sess)})

@app.route('/ai/session/<sid>', methods=['GET'])
@login_required
def get_session(sid):
    sess = storage.get_session(sid, g.user.id)
    if not sess:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    messages = storage.get_messages(sid)
    return jsonify({'success': True, 'session': asdict(sess), 'messages': [asdict(m) for m in messages]})

@app.route('/ai/session/<sid>', methods=['DELETE'])
@login_required
def delete_session(sid):
    if storage.delete_session(sid, g.user.id):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.route('/ai/session/<sid>/message', methods=['POST'])
@login_required
def send_message(sid):
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    
    if not prompt:
        return jsonify({'success': False, 'error': 'Empty message'}), 400
    
    sess = storage.get_session(sid, g.user.id)
    if not sess:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
    
    # Save user message
    storage.add_message(sid, 'user', prompt)
    is_first = storage.message_count(sid) <= 2
    
    # Get AI response
    response, model = ai_service.generate(prompt)
    
    # Save AI response
    storage.add_message(sid, 'assistant', response, model)
    
    # Update title if first message
    if is_first:
        title = prompt[:50] + ('...' if len(prompt) > 50 else '')
        storage.update_session_title(sid, title)
    
    return jsonify({
        'success': True,
        'response': response,
        'model': model,
        'is_first_message': is_first
    })

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return send_from_directory('.', 'index.html')

if __name__ == "__main__":
    logger.info(f"Starting JARVIS on port {Config.PORT}")
    app.run(host="0.0.0.0", port=Config.PORT, debug=False)
