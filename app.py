"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    JARVIS ENTERPRISE AI BACKEND                             ║
║                    NO DATABASE VERSION - In-Memory Storage                  ║
║                    Version: 3.1.0 Enterprise                                ║
║                    Built by: Krish Paliwal                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Architecture: Layered + Repository Pattern + Strategy Pattern + Singleton
Performance: In-Memory Storage + Multi-Fallback AI
"""

import os
import sys
import time
import json
import secrets
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from collections import defaultdict, OrderedDict
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict, field
from urllib.parse import urlencode

# Third-party imports
from flask import (
    Flask, request, jsonify, send_from_directory, 
    session, redirect, g, make_response, Response
)
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import jwt
import requests

# Optional imports with graceful fallback
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️ Gemini SDK not available")

# =============================
# LOGGING CONFIGURATION
# =============================
class LoggerFactory:
    _loggers = {}
    
    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        if name in cls._loggers:
            return cls._loggers[name]
        
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            console = logging.StreamHandler(sys.stdout)
            console.setLevel(logging.INFO)
            console_fmt = logging.Formatter(
                '%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console.setFormatter(console_fmt)
            logger.addHandler(console)
        
        cls._loggers[name] = logger
        return logger

logger = LoggerFactory.get_logger('jarvis')

logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# =============================
# CONFIGURATION MANAGEMENT
# =============================
class Config:
    APP_NAME: str = "JARVIS Enterprise AI"
    VERSION: str = "3.1.0"
    ENVIRONMENT: str = os.environ.get("FLASK_ENV", "production")
    DEBUG: bool = ENVIRONMENT != "production"
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
    MAX_CONTENT_LENGTH: int = 10 * 1024 * 1024
    
    # Rate Limiting
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
    GOOGLE_REDIRECT_URI: str = os.environ.get("GOOGLE_REDIRECT_URI", f"{PRODUCTION_DOMAIN}/login/callback")
    
    MAX_MESSAGE_LENGTH: int = 5000
    MAX_SESSION_TITLE: int = 100
    
    AI_MODELS: Dict[str, Dict] = {
        "deepseek": {
            "name": "JARVIS Technical (DeepSeek)",
            "model": "deepseek-chat",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 30,
        },
        "groq": {
            "name": "JARVIS Lightning (Groq)",
            "primary_model": "llama3-70b-8192",
            "fallback_model": "mixtral-8x7b-32768",
            "api_url": "https://api.groq.com/openai/v1/chat/completions",
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 15,
        },
        "gemini": {
            "name": "JARVIS Philosopher (Gemini)",
            "primary_models": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-lite"],
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 30,
        },
        "openrouter": {
            "name": "JARVIS Universal (OpenRouter)",
            "primary_models": ["google/gemini-2.0-flash-001", "google/gemini-2.0-pro-001"],
            "fallback_models": ["openai/gpt-4-turbo", "meta-llama/llama-3.1-70b-instruct"],
            "budget_models": ["openai/gpt-3.5-turbo"],
            "api_url": "https://openrouter.ai/api/v1/chat/completions",
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 45,
        }
    }
    
    @classmethod
    def validate(cls) -> bool:
        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 16:
            logger.warning("SECRET_KEY is too short")
            return False
        if not cls.JWT_SECRET or len(cls.JWT_SECRET) < 16:
            logger.warning("JWT_SECRET is too short")
            return False
        return True
    
    @classmethod
    def display(cls):
        config_info = f"""
╔══════════════════════════════════════════════════════╗
║  {cls.APP_NAME} v{cls.VERSION} (No Database Mode)
╠══════════════════════════════════════════════════════╣
║  Environment:  {cls.ENVIRONMENT}
║  Port:         {cls.PORT}
║  Domain:       {cls.PRODUCTION_DOMAIN}
║  Storage:      In-Memory (No Database)
║  DeepSeek:     {'✅' if cls.DEEPSEEK_KEY else '❌'}
║  Groq:         {'✅' if cls.GROQ_KEY else '❌'}
║  Gemini:       {'✅' if cls.GEMINI_KEY else '❌'}
║  OpenRouter:   {'✅' if cls.OPENROUTER_KEY else '❌'}
║  OAuth:        {'✅' if cls.GOOGLE_CLIENT_ID else '❌'}
║  Rate Limit:   {cls.RATE_LIMIT_MESSAGES}/{cls.RATE_LIMIT_WINDOW}s
╚══════════════════════════════════════════════════════╝
"""
        logger.info(config_info)

# =============================
# DATA TRANSFER OBJECTS (DTOs)
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
    EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    
    @staticmethod
    def sanitize_input(text: str, max_length: int = 5000) -> str:
        if not text:
            return ""
        text = text.replace('\x00', '')
        text = ''.join(char for char in text if char == '\n' or char == '\t' or ord(char) >= 32)
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
        for word in words:
            if len(word) > 8 and len(set(word)) < 3:
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
        self.local_buckets: Dict[str, Dict] = defaultdict(lambda: {'tokens': 0, 'last_refill': time.time()})
    
    def check_rate_limit(self, key: str, limit: int = 10, window: int = 60) -> RateLimitDTO:
        current_time = time.time()
        bucket = self.local_buckets[key]
        time_passed = current_time - bucket['last_refill']
        refill_rate = limit / window
        tokens_to_add = time_passed * refill_rate
        bucket['tokens'] = min(limit, bucket['tokens'] + tokens_to_add)
        bucket['last_refill'] = current_time
        
        if bucket['tokens'] >= 1:
            bucket['tokens'] -= 1
            remaining = int(bucket['tokens'])
            return RateLimitDTO(
                is_allowed=True,
                remaining=remaining,
                reset_at=current_time + (remaining * window / limit) if remaining > 0 else current_time + window,
                limit=limit,
                window=window
            )
        
        time_to_reset = (1 - bucket['tokens']) * (window / limit)
        return RateLimitDTO(
            is_allowed=False,
            remaining=0,
            reset_at=current_time + time_to_reset,
            limit=limit,
            window=window
        )
    
    def reset(self, key: str):
        self.local_buckets.pop(key, None)

rate_limiter = RateLimiter()

# =============================
# JWT SERVICE
# =============================
class JWTService:
    @staticmethod
    def create_token(user_id: str, email: str, is_admin: bool = False) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            'user_id': user_id,
            'email': email,
            'is_admin': is_admin,
            'iat': now,
            'exp': now + timedelta(hours=Config.JWT_EXPIRY_HOURS),
            'jti': SecurityValidator.generate_token(16)
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)
    
    @staticmethod
    def verify_token(token: str) -> Optional[Dict]:
        if not token:
            return None
        try:
            return jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
        except Exception as e:
            logger.warning(f"JWT verification failed: {e}")
            return None
    
    @staticmethod
    def get_token_from_request() -> Optional[str]:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            return auth_header.split(' ')[1]
        token = request.args.get('token')
        if token:
            return token
        return session.get('session_token')

# =============================
# IN-MEMORY STORAGE (代替数据库)
# =============================
class InMemoryStorage:
    """Complete in-memory storage - No database needed"""
    
    def __init__(self):
        self.users: Dict[str, UserDTO] = {}
        self.sessions: Dict[str, List[SessionDTO]] = defaultdict(list)
        self.messages: Dict[str, List[MessageDTO]] = defaultdict(list)
        self.user_sessions_map: Dict[str, List[str]] = defaultdict(list)
        self.next_ids: Dict[str, int] = defaultdict(int)
    
    def _get_next_id(self, prefix: str) -> str:
        self.next_ids[prefix] += 1
        return f"{prefix}_{self.next_ids[prefix]}"
    
    def create_or_update_user(self, google_id: str, email: str, display_name: str, avatar_url: str = "") -> UserDTO:
        # Check if user exists
        for user in self.users.values():
            if user.google_id == google_id or user.email == email:
                user.display_name = display_name
                user.avatar_url = avatar_url
                user.last_login = datetime.now()
                # Auto promote admin
                if email in ['krish@gmail.com', 'admin@jarvis.ai']:
                    user.is_admin = True
                return user
        
        # Create new user
        user_id = self._get_next_id("user")
        user = UserDTO(
            id=user_id,
            google_id=google_id,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
            is_admin=email in ['krish@gmail.com', 'admin@jarvis.ai'],
            created_at=datetime.now(),
            last_login=datetime.now(),
            total_logins=1
        )
        self.users[user_id] = user
        logger.info(f"Created new user: {email} (ID: {user_id})")
        return user
    
    def find_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        return self.users.get(user_id)
    
    def find_user_by_email(self, email: str) -> Optional[UserDTO]:
        for user in self.users.values():
            if user.email == email:
                return user
        return None
    
    def find_user_by_google_id(self, google_id: str) -> Optional[UserDTO]:
        for user in self.users.values():
            if user.google_id == google_id:
                return user
        return None
    
    def update_session_token(self, user_id: str, token: Optional[str]):
        user = self.users.get(user_id)
        if user:
            user.session_token = token
    
    def create_session(self, user_id: str, title: str = "New Chat") -> Optional[SessionDTO]:
        session_id = self._get_next_id("session")
        session_obj = SessionDTO(
            id=session_id,
            user_id=user_id,
            title=SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE),
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.sessions[user_id].append(session_obj)
        return session_obj
    
    def get_user_sessions(self, user_id: str) -> List[SessionDTO]:
        sessions_list = self.sessions.get(user_id, [])
        return sorted(sessions_list, key=lambda x: x.updated_at or datetime.min, reverse=True)
    
    def get_session(self, session_id: str, user_id: str) -> Optional[SessionDTO]:
        for session in self.sessions.get(user_id, []):
            if session.id == session_id:
                return session
        return None
    
    def delete_session(self, session_id: str, user_id: str) -> bool:
        sessions_list = self.sessions.get(user_id, [])
        for i, session in enumerate(sessions_list):
            if session.id == session_id:
                sessions_list.pop(i)
                # Also delete messages
                if session_id in self.messages:
                    del self.messages[session_id]
                logger.info(f"Deleted session {session_id} for user {user_id}")
                return True
        return False
    
    def update_session_title(self, session_id: str, title: str):
        for user_sessions in self.sessions.values():
            for session in user_sessions:
                if session.id == session_id:
                    session.title = title
                    session.updated_at = datetime.now()
                    return
    
    def add_message(self, session_id: str, role: str, content: str, model_used: str = None) -> Optional[MessageDTO]:
        message_id = self._get_next_id("msg")
        message = MessageDTO(
            id=message_id,
            session_id=session_id,
            role=role,
            content=SecurityValidator.sanitize_input(content, Config.MAX_MESSAGE_LENGTH),
            model_used=model_used,
            created_at=datetime.now()
        )
        self.messages[session_id].append(message)
        
        # Update session updated_at
        for user_sessions in self.sessions.values():
            for session in user_sessions:
                if session.id == session_id:
                    session.updated_at = datetime.now()
                    break
        
        return message
    
    def get_messages(self, session_id: str) -> List[MessageDTO]:
        return sorted(self.messages.get(session_id, []), key=lambda x: x.created_at or datetime.min)
    
    def get_message_count(self, session_id: str) -> int:
        return len(self.messages.get(session_id, []))
    
    def get_all_sessions_count(self) -> int:
        total = 0
        for sessions_list in self.sessions.values():
            total += len(sessions_list)
        return total
    
    def get_all_messages_count(self) -> int:
        total = 0
        for messages_list in self.messages.values():
            total += len(messages_list)
        return total
    
    def get_active_users_today(self) -> int:
        today = datetime.now().date()
        count = 0
        for user in self.users.values():
            if user.last_login and user.last_login.date() == today:
                count += 1
        return count
    
    def get_model_usage(self) -> List[Dict]:
        usage = defaultdict(int)
        for messages_list in self.messages.values():
            for msg in messages_list:
                if msg.model_used:
                    usage[msg.model_used] += 1
        return [{"model_used": k, "count": v} for k, v in usage.items()]

# Initialize in-memory storage
storage = InMemoryStorage()

# =============================
# AI SERVICE WITH MULTI-FALLBACK
# =============================
class AIService:
    def __init__(self):
        self.performance_metrics = defaultdict(lambda: {'success': 0, 'failure': 0, 'total_time': 0.0})
        self.system_prompt = """You are JARVIS, an advanced enterprise AI assistant built by Krish Paliwal.
You are designed to be helpful, intelligent, professional, and provide accurate responses.
Key traits:
- Expert in all domains (technology, science, business, creative arts)
- Professional and articulate communication
- Provide well-structured, comprehensive answers
- Use bullet points and formatting for clarity
- Be honest about limitations
- Maintain a helpful and positive tone"""
    
    def call_deepseek(self, prompt: str) -> Optional[str]:
        if not Config.DEEPSEEK_KEY:
            return None
        model_config = Config.AI_MODELS['deepseek']
        try:
            headers = {"Authorization": f"Bearer {Config.DEEPSEEK_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": model_config['model'],
                "messages": [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": prompt[:4000]}],
                "max_tokens": model_config['max_tokens'],
                "temperature": model_config['temperature']
            }
            response = requests.post(model_config['api_url'], json=payload, headers=headers, timeout=model_config['timeout'])
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            return None
        except Exception as e:
            logger.error(f"DeepSeek error: {e}")
            return None
    
    def call_groq(self, prompt: str) -> Optional[str]:
        if not Config.GROQ_KEY:
            return None
        model_config = Config.AI_MODELS['groq']
        try:
            headers = {"Authorization": f"Bearer {Config.GROQ_KEY}", "Content-Type": "application/json"}
            models_to_try = [model_config['primary_model'], model_config['fallback_model']]
            for model in models_to_try:
                payload = {
                    "model": model,
                    "messages": [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": prompt[:4000]}],
                    "max_tokens": model_config['max_tokens'],
                    "temperature": model_config['temperature']
                }
                response = requests.post(model_config['api_url'], json=payload, headers=headers, timeout=model_config['timeout'])
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
            return None
        except Exception as e:
            logger.error(f"Groq error: {e}")
            return None
    
    def call_gemini(self, prompt: str) -> Optional[str]:
        if not Config.GEMINI_KEY or not GEMINI_AVAILABLE:
            return None
        model_config = Config.AI_MODELS['gemini']
        try:
            genai.configure(api_key=Config.GEMINI_KEY)
            for model_name in model_config['primary_models']:
                try:
                    model = genai.GenerativeModel(model_name)
                    full_prompt = f"{self.system_prompt}\n\nUser: {prompt[:3000]}"
                    response = model.generate_content(full_prompt)
                    if response and response.text:
                        return response.text
                except Exception as e:
                    logger.warning(f"Gemini {model_name} failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return None
    
    def call_openrouter(self, prompt: str) -> Optional[str]:
        if not Config.OPENROUTER_KEY:
            return None
        model_config = Config.AI_MODELS['openrouter']
        try:
            headers = {
                "Authorization": f"Bearer {Config.OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": Config.FRONTEND_URL,
                "X-Title": "JARVIS Enterprise AI"
            }
            all_models = model_config['primary_models'] + model_config['fallback_models'] + model_config['budget_models']
            for model_name in all_models:
                payload = {
                    "model": model_name,
                    "messages": [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": prompt[:4000]}],
                    "max_tokens": model_config['max_tokens'],
                    "temperature": model_config['temperature']
                }
                response = requests.post(model_config['api_url'], json=payload, headers=headers, timeout=model_config['timeout'])
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
            return None
        except Exception as e:
            logger.error(f"OpenRouter error: {e}")
            return None
    
    def generate_fallback_response(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if any(word in prompt_lower for word in ['hello', 'hi', 'hey']):
            return "👋 Hello! I'm JARVIS, your enterprise AI assistant.\n\nHow can I help you today?"
        elif '?' in prompt:
            return "🤔 I'd love to answer your question! Please try again in a moment.\n\nI'm here to help with any questions you have!"
        else:
            return "⚡ JARVIS is ready to assist!\n\nPlease try your request again. I'm here to help with any task!"
    
    def generate_response(self, prompt: str, preferred_model: str = None) -> Tuple[str, str, float, Dict]:
        start_time = time.time()
        prompt = SecurityValidator.sanitize_input(prompt, Config.MAX_MESSAGE_LENGTH)
        
        if SecurityValidator.detect_spam(prompt):
            return ("Please rephrase your message more clearly.", "system", 0, {'fallback': True, 'reason': 'spam'})
        
        ai_functions = OrderedDict([
            ('deepseek', self.call_deepseek),
            ('groq', self.call_groq),
            ('gemini', self.call_gemini),
            ('openrouter', self.call_openrouter),
        ])
        
        if preferred_model and preferred_model in ai_functions:
            func = ai_functions.pop(preferred_model)
            ordered = OrderedDict([(preferred_model, func)])
            ordered.update(ai_functions)
            ai_functions = ordered
        
        tried_models = []
        for model_name, ai_func in ai_functions.items():
            tried_models.append(model_name)
            logger.info(f"Trying AI: {model_name}")
            model_start = time.time()
            try:
                response = ai_func(prompt)
                if response:
                    self.performance_metrics[model_name]['success'] += 1
                    self.performance_metrics[model_name]['total_time'] += (time.time() - model_start)
                    total_time = time.time() - start_time
                    logger.info(f"✅ {model_name} succeeded")
                    return response, model_name, round(total_time, 2), {'tried_models': tried_models}
                self.performance_metrics[model_name]['failure'] += 1
            except Exception as e:
                logger.error(f"{model_name} exception: {e}")
                self.performance_metrics[model_name]['failure'] += 1
        
        total_time = time.time() - start_time
        fallback = self.generate_fallback_response(prompt)
        return fallback, "fallback", round(total_time, 2), {'tried_models': tried_models, 'all_failed': True}
    
    def get_stats(self) -> Dict:
        stats = {}
        for model, metrics in self.performance_metrics.items():
            total = metrics['success'] + metrics['failure']
            stats[model] = {
                'total': total,
                'success': metrics['success'],
                'failure': metrics['failure'],
                'success_rate': round(metrics['success'] / total * 100, 1) if total > 0 else 0,
                'avg_time': round(metrics['total_time'] / metrics['success'], 2) if metrics['success'] > 0 else 0
            }
        return stats

ai_service = AIService()

# =============================
# FLASK APPLICATION SETUP
# =============================
app = Flask(__name__, static_folder='.')
app.secret_key = Config.SECRET_KEY
app.config.update(
    MAX_CONTENT_LENGTH=Config.MAX_CONTENT_LENGTH,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

allowed_origins = [Config.FRONTEND_URL]
if Config.ENVIRONMENT != 'production':
    allowed_origins.extend(["http://localhost:5000", "http://127.0.0.1:5000"])

CORS(app, origins=list(set(allowed_origins)), supports_credentials=True,
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
     allow_headers=['Content-Type', 'Authorization'],
     expose_headers=['X-RateLimit-Remaining', 'X-RateLimit-Reset'])

# =============================
# AUTHENTICATION DECORATORS
# =============================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = None
        token = JWTService.get_token_from_request()
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

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not g.current_user.is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# =============================
# HELPER FUNCTIONS
# =============================
def get_client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or '0.0.0.0'

def json_response(data: Dict, status: int = 200) -> Response:
    response = make_response(jsonify(data), status)
    response.headers.update({
        'Content-Type': 'application/json',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
    })
    return response

def check_rate(endpoint: str, limit: int = None, window: int = None) -> RateLimitDTO:
    ip = get_client_ip()
    user_id = getattr(g, 'current_user', None)
    user_id = user_id.id if user_id else 'anonymous'
    key = f"{endpoint}:{user_id}:{ip}"
    limit = limit or Config.RATE_LIMIT_MESSAGES
    window = window or Config.RATE_LIMIT_WINDOW
    return rate_limiter.check_rate_limit(key, limit, window)

# =============================
# ROUTES: CORE
# =============================
@app.route('/')
def index():
    response = make_response(send_from_directory('.', 'index.html'))
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'version': Config.VERSION,
        'environment': Config.ENVIRONMENT,
        'storage': 'in-memory',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'services': {
            'deepseek': bool(Config.DEEPSEEK_KEY),
            'groq': bool(Config.GROQ_KEY),
            'gemini': bool(Config.GEMINI_KEY),
            'openrouter': bool(Config.OPENROUTER_KEY),
            'oauth': bool(Config.GOOGLE_CLIENT_ID)
        }
    })

# =============================
# ROUTES: AUTHENTICATION
# =============================
@app.route('/login/google')
def google_login():
    if not Config.GOOGLE_CLIENT_ID:
        return redirect(f"{Config.FRONTEND_URL}?error=OAuth+not+configured")
    state = SecurityValidator.generate_token(32)
    session['oauth_state'] = state
    params = {
        'client_id': Config.GOOGLE_CLIENT_ID,
        'redirect_uri': Config.GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'email profile',
        'state': state,
        'access_type': 'offline',
        'prompt': 'consent'
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
        token_res = requests.post('https://oauth2.googleapis.com/token', data={
            'code': code, 'client_id': Config.GOOGLE_CLIENT_ID,
            'client_secret': Config.GOOGLE_CLIENT_SECRET,
            'redirect_uri': Config.GOOGLE_REDIRECT_URI, 'grant_type': 'authorization_code'
        }, timeout=15)
        
        if token_res.status_code != 200:
            return redirect(f"{Config.FRONTEND_URL}?error=Token+exchange+failed")
        
        access_token = token_res.json().get('access_token')
        user_res = requests.get('https://www.googleapis.com/oauth2/v1/userinfo',
                                headers={'Authorization': f'Bearer {access_token}'}, timeout=15)
        
        if user_res.status_code != 200:
            return redirect(f"{Config.FRONTEND_URL}?error=User+info+failed")
        
        user_info = user_res.json()
        logger.info(f"User authenticated: {user_info.get('email')}")
        
        user = storage.create_or_update_user(
            google_id=user_info['id'],
            email=user_info['email'],
            display_name=user_info.get('name', user_info['email'].split('@')[0]),
            avatar_url=user_info.get('picture', '')
        )
        
        if not user:
            return redirect(f"{Config.FRONTEND_URL}?error=User+creation+failed")
        
        jwt_token = JWTService.create_token(user.id, user.email, user.is_admin)
        storage.update_session_token(user.id, jwt_token)
        
        session['user_id'] = user.id
        session['user_email'] = user.email
        session['user_name'] = user.display_name
        session['session_token'] = jwt_token
        
        logger.info(f"✅ Login successful: {user.email}")
        return redirect(f"{Config.FRONTEND_URL}?token={jwt_token}")
        
    except Exception as e:
        logger.error(f"OAuth exception: {e}")
        return redirect(f"{Config.FRONTEND_URL}?error=Authentication+failed")

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
                        'id': user.id, 'name': user.display_name,
                        'email': user.email, 'avatar': user.avatar_url, 'is_admin': user.is_admin
                    },
                    'token': token
                })
    if 'user_id' in session:
        return jsonify({
            'success': True,
            'user': {'id': session['user_id'], 'name': session.get('user_name'), 'email': session.get('user_email')}
        })
    return jsonify({'success': False, 'error': 'Not authenticated'}), 401

# =============================
# ROUTES: CHAT SESSIONS
# =============================
@app.route('/ai/sessions', methods=['GET'])
@login_required
def get_sessions():
    rate_info = check_rate('get_sessions', Config.RATE_LIMIT_SESSIONS)
    if not rate_info.is_allowed:
        return json_response({'success': False, 'error': 'Too many requests'}, 429)
    
    sessions = storage.get_user_sessions(g.current_user.id)
    return jsonify({'success': True, 'sessions': [asdict(s) for s in sessions]})

@app.route('/ai/session', methods=['POST'])
@login_required
def create_session():
    rate_info = check_rate('create_session', 20)
    if not rate_info.is_allowed:
        return json_response({'success': False, 'error': 'Too many requests'}, 429)
    
    data = request.json or {}
    title = data.get('title', 'New Chat')
    session_obj = storage.create_session(g.current_user.id, title)
    
    if not session_obj:
        return json_response({'success': False, 'error': 'Failed to create session'}, 500)
    
    return jsonify({'success': True, 'session_id': session_obj.id, 'session': asdict(session_obj)})

@app.route('/ai/session/<session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    session_obj = storage.get_session(session_id, g.current_user.id)
    if not session_obj:
        return json_response({'success': False, 'error': 'Session not found'}, 404)
    
    messages = storage.get_messages(session_id)
    return jsonify({'success': True, 'session': asdict(session_obj), 'messages': [asdict(m) for m in messages]})

@app.route('/ai/session/<session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id):
    deleted = storage.delete_session(session_id, g.current_user.id)
    if deleted:
        return jsonify({'success': True, 'message': 'Session deleted'})
    return json_response({'success': False, 'error': 'Session not found'}, 404)

# =============================
# ROUTES: MESSAGES
# =============================
@app.route('/ai/session/<session_id>/message', methods=['POST'])
@login_required
def send_message(session_id):
    rate_info = check_rate('send_message', Config.RATE_LIMIT_MESSAGES, Config.RATE_LIMIT_WINDOW)
    if not rate_info.is_allowed:
        return json_response({'success': False, 'error': 'Rate limit exceeded', 'retry_after': int(rate_info.reset_at - time.time())}, 429)
    
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    preferred_model = data.get('model')
    
    if not prompt:
        return json_response({'success': False, 'error': 'Empty message'}, 400)
    if len(prompt) > Config.MAX_MESSAGE_LENGTH:
        return json_response({'success': False, 'error': f'Message too long (max {Config.MAX_MESSAGE_LENGTH} chars)'}, 400)
    if SecurityValidator.detect_spam(prompt):
        return json_response({'success': False, 'error': 'Message looks like spam'}, 400)
    
    session_obj = storage.get_session(session_id, g.current_user.id)
    if not session_obj:
        return json_response({'success': False, 'error': 'Session not found'}, 404)
    
    # Save user message
    user_msg = storage.add_message(session_id, 'user', prompt)
    if not user_msg:
        return json_response({'success': False, 'error': 'Failed to save message'}, 500)
    
    msg_count = storage.get_message_count(session_id)
    is_first = msg_count <= 2
    
    # Generate AI response
    response_text, model_used, response_time, metadata = ai_service.generate_response(prompt, preferred_model)
    
    # Save AI response
    bot_msg = storage.add_message(session_id, 'assistant', response_text, model_used)
    
    # Update title if first message
    if is_first:
        title = prompt[:50] + ('...' if len(prompt) > 50 else '')
        storage.update_session_title(session_id, title)
    
    logger.info(f"Message processed | Session: {session_id} | Model: {model_used} | User: {g.current_user.email}")
    
    return jsonify({
        'success': True, 'response': response_text, 'model': model_used,
        'response_time': response_time, 'is_first_message': is_first,
        'remaining_requests': rate_info.remaining, 'metadata': metadata
    })

# =============================
# ROUTES: ADMIN
# =============================
@app.route('/admin/stats')
@admin_required
def admin_stats():
    stats = {
        'total_users': len(storage.users),
        'total_sessions': storage.get_all_sessions_count(),
        'total_messages': storage.get_all_messages_count(),
        'active_today': storage.get_active_users_today(),
        'model_usage': storage.get_model_usage(),
        'ai_performance': ai_service.get_stats(),
        'storage_type': 'in-memory'
    }
    return jsonify({'success': True, 'stats': stats})

@app.route('/ai/stats')
@admin_required
def ai_stats():
    return jsonify({'success': True, 'performance': ai_service.get_stats()})

# =============================
# ERROR HANDLERS
# =============================
@app.errorhandler(400)
def bad_request(e):
    return json_response({'success': False, 'error': 'Bad request'}, 400)

@app.errorhandler(401)
def unauthorized(e):
    return json_response({'success': False, 'error': 'Unauthorized'}, 401)

@app.errorhandler(403)
def forbidden(e):
    return json_response({'success': False, 'error': 'Forbidden'}, 403)

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return json_response({'success': False, 'error': 'Not found'}, 404)
    return send_from_directory('.', 'index.html')

@app.errorhandler(429)
def rate_limited(e):
    return json_response({'success': False, 'error': 'Too many requests'}, 429)

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal server error: {e}")
    return json_response({'success': False, 'error': 'Internal server error'}, 500)

# =============================
# APPLICATION STARTUP
# =============================
if __name__ == "__main__":
    Config.validate()
    Config.display()
    
    logger.info("🚀 JARVIS starting in NO DATABASE MODE (In-Memory Storage)")
    logger.info("⚠️ Data will be lost on server restart")
    
    port = Config.PORT
    
    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve
            logger.info(f"🚀 Starting production server on port {port}")
            logger.info(f"📍 Access at: {Config.FRONTEND_URL}")
            serve(app, host="0.0.0.0", port=port, threads=8, connection_limit=500)
        except ImportError:
            logger.warning("Waitress not installed, using Flask dev server")
            app.run(host="0.0.0.0", port=port, debug=False)
    else:
        logger.info(f"🔧 Starting development server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=True)
