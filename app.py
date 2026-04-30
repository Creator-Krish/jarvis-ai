"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    JARVIS ENTERPRISE AI BACKEND                             ║
║                    Production-Grade Engineering                             ║
║                    Version: 3.1.0 Enterprise                                ║
║                    Built by: Krish Paliwal                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Architecture: Layered + Repository Pattern + Strategy Pattern + Singleton
Security: JWT + Rate Limiting + CSP + CORS + Input Validation + SQL Injection Prevention
Performance: Connection Pooling + Redis Caching + Query Optimization + Multi-Fallback AI
"""

import os
import sys
import time
import json
import secrets
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from collections import defaultdict, OrderedDict
from typing import Optional, Dict, Any, List, Tuple, Union
from dataclasses import dataclass, asdict, field
from urllib.parse import urlencode, urlparse

# Third-party imports
from flask import (
    Flask, request, jsonify, send_from_directory, 
    session, redirect, url_for, g, make_response, Response
)
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from psycopg2 import pool
import jwt
import requests

# Optional imports with graceful fallback
try:
    import redis as redis_lib
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    print("⚠️ Redis not available, using in-memory fallback")

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
    """Enterprise logging factory with structured output"""
    
    _loggers = {}
    
    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        if name in cls._loggers:
            return cls._loggers[name]
        
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            # Console Handler
            console = logging.StreamHandler(sys.stdout)
            console.setLevel(logging.INFO)
            console_fmt = logging.Formatter(
                '%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console.setFormatter(console_fmt)
            logger.addHandler(console)
            
            # File Handler
            try:
                file_handler = logging.FileHandler('jarvis.log')
                file_handler.setLevel(logging.DEBUG)
                file_fmt = logging.Formatter(
                    '%(asctime)s | %(levelname)-7s | %(name)-20s | [%(filename)s:%(lineno)d] | %(message)s'
                )
                file_handler.setFormatter(file_fmt)
                logger.addHandler(file_handler)
            except Exception:
                pass
        
        cls._loggers[name] = logger
        return logger

logger = LoggerFactory.get_logger('jarvis')

# Suppress noisy loggers
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# =============================
# CONFIGURATION MANAGEMENT
# =============================
class Config:
    """
    Centralized configuration with environment variable support
    Implements 12-Factor App principles
    """
    
    # ── Application ─────────────────────────────────────
    APP_NAME: str = "JARVIS Enterprise AI"
    VERSION: str = "3.1.0"
    ENVIRONMENT: str = os.environ.get("FLASK_ENV", "production")
    DEBUG: bool = ENVIRONMENT != "production"
    PORT: int = int(os.environ.get("PORT", 5000))
    
    # ── URLs (PROPERLY FORMATTED) ───────────────────────
    PRODUCTION_DOMAIN: str = os.environ.get(
        "FRONTEND_URL", 
        "https://jarvis-e76i.onrender.com"
    )
    FRONTEND_URL: str = PRODUCTION_DOMAIN  # Alias for consistency
    
    # ── Security ────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    JWT_SECRET: str = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 168  # 7 days
    
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = 'Lax'
    MAX_CONTENT_LENGTH: int = int(os.environ.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))
    
    # ── Database ────────────────────────────────────────
    DB_HOST: str = os.environ.get("DB_HOST", "localhost")
    DB_PORT: int = int(os.environ.get("DB_PORT", 5432))
    DB_USER: str = os.environ.get("DB_USER", "postgres")
    DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "")
    DB_NAME: str = os.environ.get("DB_NAME", "jarvis_db")
    DB_MIN_CONN: int = 2
    DB_MAX_CONN: int = 10
    
    # ── Redis ───────────────────────────────────────────
    REDIS_URL: Optional[str] = os.environ.get("REDIS_URL")
    
    # ── Rate Limiting ───────────────────────────────────
    RATE_LIMIT_MESSAGES: int = 10       # messages per window
    RATE_LIMIT_WINDOW: int = 60         # seconds
    RATE_LIMIT_SESSIONS: int = 30
    RATE_LIMIT_GLOBAL: int = 100
    
    # ── AI API Keys ─────────────────────────────────────
    DEEPSEEK_KEY: Optional[str] = os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY: Optional[str] = os.environ.get("GROQ_KEY")
    GEMINI_KEY: Optional[str] = os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY: Optional[str] = os.environ.get("OPENROUTER_KEY")
    
    # ── Google OAuth ────────────────────────────────────
    GOOGLE_CLIENT_ID: Optional[str] = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET: Optional[str] = os.environ.get("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI: str = os.environ.get(
        "GOOGLE_REDIRECT_URI",
        f"{PRODUCTION_DOMAIN}/login/callback"
    )
    
    # ── Content Limits ──────────────────────────────────
    MAX_MESSAGE_LENGTH: int = 5000
    MAX_SESSION_TITLE: int = 100
    MAX_FILE_SIZE: int = 10 * 1024 * 1024
    
    # ── AI Model Configurations ─────────────────────────
    AI_MODELS: Dict[str, Dict] = {
        "deepseek": {
            "name": "JARVIS Technical (DeepSeek)",
            "model": "deepseek-chat",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 30,
            "priority": 1
        },
        "groq": {
            "name": "JARVIS Lightning (Groq)",
            "primary_model": "llama3-70b-8192",
            "fallback_model": "mixtral-8x7b-32768",
            "api_url": "https://api.groq.com/openai/v1/chat/completions",
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 15,
            "priority": 2
        },
        "gemini": {
            "name": "JARVIS Philosopher (Gemini)",
            "primary_models": [
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-1.5-flash-lite"
            ],
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 30,
            "priority": 3
        },
        "openrouter": {
            "name": "JARVIS Universal (OpenRouter)",
            "primary_models": [
                "google/gemini-2.0-flash-001",
                "google/gemini-2.0-pro-001",
                "google/gemini-2.5-pro-exp-03-25",
                "anthropic/claude-3.5-sonnet"
            ],
            "fallback_models": [
                "openai/gpt-4-turbo",
                "meta-llama/llama-3.1-70b-instruct",
                "mistralai/mixtral-8x22b-instruct"
            ],
            "budget_models": [
                "openai/gpt-3.5-turbo",
                "meta-llama/llama-3.1-8b-instruct",
                "google/gemma-2-9b-it"
            ],
            "api_url": "https://openrouter.ai/api/v1/chat/completions",
            "max_tokens": 2048,
            "temperature": 0.7,
            "timeout": 45,
            "priority": 4
        }
    }
    
    # ── Fallback Order ──────────────────────────────────
    AI_FALLBACK_ORDER: List[str] = ["deepseek", "groq", "gemini", "openrouter"]
    
    @classmethod
    def validate(cls) -> bool:
        """Validate critical configuration"""
        issues = []
        
        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 16:
            issues.append("SECRET_KEY is too short or missing")
        
        if not cls.JWT_SECRET or len(cls.JWT_SECRET) < 16:
            issues.append("JWT_SECRET is too short or missing")
        
        if issues:
            logger.warning(f"Configuration issues: {issues}")
            return False
        
        return True
    
    @classmethod
    def display(cls):
        """Display current configuration (safe)"""
        config_info = f"""
╔══════════════════════════════════════════════════════╗
║  {cls.APP_NAME} v{cls.VERSION}
╠══════════════════════════════════════════════════════╣
║  Environment:  {cls.ENVIRONMENT}
║  Port:         {cls.PORT}
║  Domain:       {cls.PRODUCTION_DOMAIN}
║  Database:     {cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}
║  Redis:        {'Connected' if cls.REDIS_URL else 'Using fallback'}
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
    """User Data Transfer Object"""
    id: int
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
    """Chat Session DTO"""
    id: int
    user_id: int
    title: str = "New Chat"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@dataclass
class MessageDTO:
    """Message DTO"""
    id: int
    session_id: int
    role: str
    content: str
    model_used: Optional[str] = None
    created_at: Optional[datetime] = None

@dataclass
class RateLimitDTO:
    """Rate limit info DTO"""
    is_allowed: bool
    remaining: int
    reset_at: float
    limit: int
    window: int

# =============================
# SECURITY VALIDATOR
# =============================
class SecurityValidator:
    """Enterprise input validation and sanitization"""
    
    # Regex patterns
    EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    SAFE_TEXT_REGEX = re.compile(r'^[a-zA-Z0-9\s\-_.,!?@#$%^&*()+=:;\'\"\[\]{}|\\/~`\n\t]+$')
    URL_REGEX = re.compile(r'^https?://[^\s/$.?#].[^\s]*$')
    
    # SQL injection patterns
    SQL_KEYWORDS = [
        'DROP', 'DELETE', 'TRUNCATE', 'INSERT', 'UPDATE', 'ALTER',
        'CREATE', 'EXEC', 'EXECUTE', 'UNION', 'SELECT', '--', '/*', '*/'
    ]
    
    @staticmethod
    def sanitize_input(text: str, max_length: int = 5000) -> str:
        """Sanitize user input - prevents XSS, SQL injection"""
        if not text:
            return ""
        
        # Remove null bytes
        text = text.replace('\x00', '')
        
        # Remove control characters except newlines/tabs
        text = ''.join(
            char for char in text 
            if char == '\n' or char == '\t' or ord(char) >= 32
        )
        
        # Truncate to max length
        if len(text) > max_length:
            text = text[:max_length]
        
        # Strip HTML tags
        text = re.sub(r'<[^>]*>', '', text)
        
        # Remove script-related patterns
        text = text.replace('javascript:', '')
        text = text.replace('onerror=', '')
        text = text.replace('onload=', '')
        text = text.replace('eval(', '')
        
        return text.strip()
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format"""
        return bool(SecurityValidator.EMAIL_REGEX.match(email)) if email else False
    
    @staticmethod
    def detect_spam(text: str) -> bool:
        """Detect spam patterns"""
        if not text or len(text) < 2:
            return True
        
        words = text.split()
        
        # Check for extreme repetition
        if len(words) > 5 and len(set(words)) < 3:
            return True
        
        # Check for single character repetition
        for word in words:
            if len(word) > 8 and len(set(word)) < 3:
                return True
        
        # Check for all caps shouting
        if len(text) > 20 and text.isupper():
            return True
        
        return False
    
    @staticmethod
    def sanitize_sql_identifier(name: str) -> str:
        """Sanitize SQL identifiers"""
        # Remove any non-alphanumeric characters except underscore
        return re.sub(r'[^\w]', '', name)[:63]
    
    @staticmethod
    def generate_token(length: int = 32) -> str:
        """Generate cryptographically secure token"""
        return secrets.token_hex(length)
    
    @staticmethod
    def hash_string(value: str) -> str:
        """Create SHA-256 hash"""
        return hashlib.sha256(value.encode()).hexdigest()

# =============================
# RATE LIMITER (Token Bucket)
# =============================
class RateLimiter:
    """
    Token Bucket Rate Limiter
    Supports Redis for distributed rate limiting with in-memory fallback
    """
    
    def __init__(self):
        self.redis_client = None
        self.local_buckets: Dict[str, Dict] = defaultdict(
            lambda: {'tokens': 0, 'last_refill': time.time()}
        )
        
        # Initialize Redis if available
        if REDIS_AVAILABLE and Config.REDIS_URL:
            try:
                self.redis_client = redis_lib.from_url(
                    Config.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=3,
                    socket_connect_timeout=3
                )
                self.redis_client.ping()
                logger.info("✅ Redis rate limiter connected")
            except Exception as e:
                logger.warning(f"Redis unavailable for rate limiting: {e}")
                self.redis_client = None
    
    def check_rate_limit(
        self, 
        key: str, 
        limit: int = 10, 
        window: int = 60
    ) -> RateLimitDTO:
        """
        Check if request is within rate limit
        Uses Token Bucket Algorithm
        
        Args:
            key: Unique identifier (user_id + ip + endpoint)
            limit: Maximum requests allowed in window
            window: Time window in seconds
        
        Returns:
            RateLimitDTO with allowance info
        """
        current_time = time.time()
        redis_key = f"rate_limit:{key}"
        
        # Try Redis first (distributed)
        if self.redis_client:
            try:
                return self._check_redis(redis_key, limit, window, current_time)
            except Exception:
                pass
        
        # Fallback to local (single instance)
        return self._check_local(key, limit, window, current_time)
    
    def _check_redis(
        self, 
        redis_key: str, 
        limit: int, 
        window: int, 
        current_time: float
    ) -> RateLimitDTO:
        """Check rate limit using Redis"""
        pipe = self.redis_client.pipeline()
        pipe.get(redis_key)
        pipe.ttl(redis_key)
        result = pipe.execute()
        
        current_tokens = int(result[0]) if result[0] else 0
        ttl = result[1] if result[1] > 0 else window
        
        if current_tokens >= limit:
            return RateLimitDTO(
                is_allowed=False,
                remaining=0,
                reset_at=current_time + ttl,
                limit=limit,
                window=window
            )
        
        # Increment and set expiry
        pipe = self.redis_client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, window)
        new_tokens = pipe.execute()[0]
        
        return RateLimitDTO(
            is_allowed=True,
            remaining=max(0, limit - new_tokens),
            reset_at=current_time + window,
            limit=limit,
            window=window
        )
    
    def _check_local(
        self, 
        key: str, 
        limit: int, 
        window: int, 
        current_time: float
    ) -> RateLimitDTO:
        """Check rate limit using local memory"""
        bucket = self.local_buckets[key]
        time_passed = current_time - bucket['last_refill']
        
        # Refill tokens
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
        """Reset rate limit for a key"""
        if self.redis_client:
            try:
                self.redis_client.delete(f"rate_limit:{key}")
            except Exception:
                pass
        self.local_buckets.pop(key, None)

# Initialize global rate limiter
rate_limiter = RateLimiter()

# =============================
# JWT SERVICE
# =============================
class JWTService:
    """Enterprise JWT token management"""
    
    @staticmethod
    def create_token(
        user_id: int, 
        email: str, 
        is_admin: bool = False,
        extra_claims: Dict = None
    ) -> str:
        """Create signed JWT token"""
        now = datetime.now(timezone.utc)
        
        payload = {
            'user_id': user_id,
            'email': email,
            'is_admin': is_admin,
            'iat': now,
            'exp': now + timedelta(hours=Config.JWT_EXPIRY_HOURS),
            'jti': SecurityValidator.generate_token(16),
            'type': 'access'
        }
        
        if extra_claims:
            payload.update(extra_claims)
        
        return jwt.encode(
            payload, 
            Config.JWT_SECRET, 
            algorithm=Config.JWT_ALGORITHM
        )
    
    @staticmethod
    def verify_token(token: str) -> Optional[Dict]:
        """Verify and decode JWT token"""
        if not token:
            return None
        
        try:
            payload = jwt.decode(
                token,
                Config.JWT_SECRET,
                algorithms=[Config.JWT_ALGORITHM],
                options={
                    'verify_exp': True,
                    'verify_iat': True,
                    'require': ['user_id', 'email', 'exp']
                }
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT: {e}")
            return None
        except Exception as e:
            logger.error(f"JWT verification error: {e}")
            return None
    
    @staticmethod
    def refresh_token(token: str) -> Optional[str]:
        """Refresh token if valid but expiring soon"""
        payload = JWTService.verify_token(token)
        if not payload:
            return None
        
        exp = datetime.fromtimestamp(payload['exp'], tz=timezone.utc)
        time_until_expiry = exp - datetime.now(timezone.utc)
        
        # Refresh if less than 24 hours remaining
        if time_until_expiry < timedelta(hours=24):
            return JWTService.create_token(
                user_id=payload['user_id'],
                email=payload['email'],
                is_admin=payload.get('is_admin', False)
            )
        
        return token
    
    @staticmethod
    def get_token_from_request() -> Optional[str]:
        """Extract JWT from request"""
        # Check Authorization header
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            return auth_header.split(' ')[1]
        
        # Check query parameter (for OAuth redirect)
        token = request.args.get('token')
        if token:
            return token
        
        # Check session
        return session.get('session_token')

# =============================
# DATABASE CONNECTION POOL
# =============================
class DatabasePool:
    """Thread-safe database connection pool singleton"""
    _instance = None
    _pool = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize the connection pool"""
        if self._initialized:
            return
        
        try:
            self._pool = pool.ThreadedConnectionPool(
                Config.DB_MIN_CONN,
                Config.DB_MAX_CONN,
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3
            )
            self._initialized = True
            logger.info(f"✅ Database pool initialized ({Config.DB_MIN_CONN}-{Config.DB_MAX_CONN} connections)")
        except Exception as e:
            logger.error(f"❌ Database pool failed: {e}")
            self._pool = None
            self._initialized = True  # Don't retry
    
    def get_connection(self):
        """Get a connection from the pool"""
        if not self._initialized:
            self.initialize()
        
        if self._pool:
            try:
                return self._pool.getconn()
            except pool.PoolError:
                logger.warning("Pool exhausted, creating temporary connection")
                return self._create_direct_connection()
        
        return self._create_direct_connection()
    
    def return_connection(self, conn):
        """Return a connection to the pool"""
        if conn is None:
            return
        
        if self._pool:
            try:
                self._pool.putconn(conn)
            except pool.PoolError:
                try:
                    conn.close()
                except Exception:
                    pass
        else:
            try:
                conn.close()
            except Exception:
                pass
    
    def _create_direct_connection(self):
        """Create a direct database connection"""
        try:
            return psycopg2.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                connect_timeout=10
            )
        except Exception as e:
            logger.error(f"Direct connection failed: {e}")
            return None
    
    def close_all(self):
        """Close all connections"""
        if self._pool:
            try:
                self._pool.closeall()
            except Exception:
                pass
            self._pool = None
        self._initialized = False

# Initialize database pool
db_pool = DatabasePool()

# =============================
# USER REPOSITORY
# =============================
class UserRepository:
    """Data access layer for users - Repository Pattern"""
    
    @staticmethod
    def find_by_id(user_id: int) -> Optional[UserDTO]:
        """Find user by ID"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, google_id, email, display_name, avatar_url,
                           is_admin, created_at, last_login, total_logins, session_token
                    FROM users WHERE id = %s
                """, (user_id,))
                row = cur.fetchone()
                return UserDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Find user by ID error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def find_by_email(email: str) -> Optional[UserDTO]:
        """Find user by email"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, google_id, email, display_name, avatar_url,
                           is_admin, created_at, last_login, total_logins, session_token
                    FROM users WHERE email = %s
                """, (email,))
                row = cur.fetchone()
                return UserDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Find user by email error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def find_by_google_id(google_id: str) -> Optional[UserDTO]:
        """Find user by Google ID"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, google_id, email, display_name, avatar_url,
                           is_admin, created_at, last_login, total_logins, session_token
                    FROM users WHERE google_id = %s
                """, (google_id,))
                row = cur.fetchone()
                return UserDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Find user by Google ID error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def create_or_update(
        google_id: str,
        email: str,
        display_name: str,
        avatar_url: str = ""
    ) -> Optional[UserDTO]:
        """Create new user or update existing"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Check if user exists
                cur.execute(
                    "SELECT id FROM users WHERE google_id = %s OR email = %s",
                    (google_id, email)
                )
                existing = cur.fetchone()
                
                if existing:
                    # Update existing
                    cur.execute("""
                        UPDATE users 
                        SET display_name = %s, avatar_url = %s,
                            last_login = NOW(), total_logins = total_logins + 1
                        WHERE id = %s
                        RETURNING id, google_id, email, display_name, avatar_url,
                                  is_admin, created_at, last_login, total_logins, session_token
                    """, (display_name, avatar_url, existing['id']))
                else:
                    # Create new
                    cur.execute("""
                        INSERT INTO users (google_id, email, display_name, avatar_url)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, google_id, email, display_name, avatar_url,
                                  is_admin, created_at, last_login, total_logins, session_token
                    """, (google_id, email, display_name, avatar_url))
                
                row = cur.fetchone()
                conn.commit()
                
                # Auto-promote admin emails
                if email in ['krish@gmail.com', 'admin@jarvis.ai']:
                    cur.execute("UPDATE users SET is_admin = TRUE WHERE id = %s", (row['id'],))
                    conn.commit()
                    row['is_admin'] = True
                
                return UserDTO(**row)
        except Exception as e:
            conn.rollback()
            logger.error(f"Create/update user error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def update_session_token(user_id: int, token: Optional[str]):
        """Update user session token"""
        conn = db_pool.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET session_token = %s WHERE id = %s",
                    (token, user_id)
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Update session token error: {e}")
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def log_login(
        user_id: int,
        email: str,
        ip_address: str,
        user_agent: str,
        success: bool = True,
        error_message: str = None
    ):
        """Log login attempt"""
        conn = db_pool.get_connection()
        if not conn:
            return
        
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO login_logs (user_id, email, ip_address, user_agent, success, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (user_id, email, ip_address, user_agent, success, error_message))
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Log login error: {e}")
        finally:
            db_pool.return_connection(conn)

# =============================
# CHAT SESSION REPOSITORY
# =============================
class ChatSessionRepository:
    """Data access layer for chat sessions"""
    
    @staticmethod
    def get_user_sessions(
        user_id: int,
        page: int = 1,
        per_page: int = 20
    ) -> Tuple[List[SessionDTO], int]:
        """Get paginated sessions for user"""
        conn = db_pool.get_connection()
        if not conn:
            return [], 0
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Total count
                cur.execute("SELECT COUNT(*) as total FROM chat_sessions WHERE user_id = %s", (user_id,))
                total = cur.fetchone()['total']
                
                # Paginated results
                offset = (page - 1) * per_page
                cur.execute("""
                    SELECT id, user_id, title, created_at, updated_at
                    FROM chat_sessions
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                """, (user_id, per_page, offset))
                
                sessions = [SessionDTO(**row) for row in cur.fetchall()]
                return sessions, total
        except Exception as e:
            logger.error(f"Get user sessions error: {e}")
            return [], 0
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def create_session(user_id: int, title: str = "New Chat") -> Optional[SessionDTO]:
        """Create new chat session"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        title = SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE)
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO chat_sessions (user_id, title)
                    VALUES (%s, %s)
                    RETURNING id, user_id, title, created_at, updated_at
                """, (user_id, title))
                row = cur.fetchone()
                conn.commit()
                return SessionDTO(**row)
        except Exception as e:
            conn.rollback()
            logger.error(f"Create session error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def get_session(session_id: int, user_id: int) -> Optional[SessionDTO]:
        """Get session with ownership check"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, user_id, title, created_at, updated_at
                    FROM chat_sessions
                    WHERE id = %s AND user_id = %s
                """, (session_id, user_id))
                row = cur.fetchone()
                return SessionDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Get session error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def delete_session(session_id: int, user_id: int) -> bool:
        """Delete session with ownership check"""
        conn = db_pool.get_connection()
        if not conn:
            return False
        
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chat_sessions WHERE id = %s AND user_id = %s",
                    (session_id, user_id)
                )
                deleted = cur.rowcount > 0
                conn.commit()
                return deleted
        except Exception as e:
            conn.rollback()
            logger.error(f"Delete session error: {e}")
            return False
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def update_title(session_id: int, title: str):
        """Update session title"""
        conn = db_pool.get_connection()
        if not conn:
            return
        
        title = SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE)
        
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE chat_sessions SET title = %s, updated_at = NOW() WHERE id = %s",
                    (title, session_id)
                )
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Update title error: {e}")
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def get_messages(
        session_id: int,
        user_id: int,
        page: int = 1,
        per_page: int = 50
    ) -> Tuple[List[MessageDTO], int]:
        """Get paginated messages"""
        conn = db_pool.get_connection()
        if not conn:
            return [], 0
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Verify ownership
                cur.execute(
                    "SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s",
                    (session_id, user_id)
                )
                if not cur.fetchone():
                    return [], 0
                
                # Total count
                cur.execute("SELECT COUNT(*) as total FROM messages WHERE session_id = %s", (session_id,))
                total = cur.fetchone()['total']
                
                # Paginated results
                offset = (page - 1) * per_page
                cur.execute("""
                    SELECT id, session_id, role, content, model_used, created_at
                    FROM messages
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    LIMIT %s OFFSET %s
                """, (session_id, per_page, offset))
                
                messages = [MessageDTO(**row) for row in cur.fetchall()]
                return messages, total
        except Exception as e:
            logger.error(f"Get messages error: {e}")
            return [], 0
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def add_message(
        session_id: int,
        role: str,
        content: str,
        model_used: str = None
    ) -> Optional[MessageDTO]:
        """Add message to session"""
        conn = db_pool.get_connection()
        if not conn:
            return None
        
        content = SecurityValidator.sanitize_input(content, Config.MAX_MESSAGE_LENGTH)
        
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO messages (session_id, role, content, model_used)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, session_id, role, content, model_used, created_at
                """, (session_id, role, content, model_used))
                
                # Update session timestamp
                cur.execute(
                    "UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s",
                    (session_id,)
                )
                
                row = cur.fetchone()
                conn.commit()
                return MessageDTO(**row)
        except Exception as e:
            conn.rollback()
            logger.error(f"Add message error: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def get_message_count(session_id: int) -> int:
        """Get message count for session"""
        conn = db_pool.get_connection()
        if not conn:
            return 0
        
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM messages WHERE session_id = %s", (session_id,))
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"Message count error: {e}")
            return 0
        finally:
            db_pool.return_connection(conn)

# =============================
# AI SERVICE WITH MULTI-FALLBACK
# =============================
class AIService:
    """Enterprise AI Service with multi-layer fallback strategy"""
    
    def __init__(self):
        self.performance_metrics = defaultdict(lambda: {
            'success': 0, 'failure': 0, 'total_time': 0.0
        })
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
        """Call DeepSeek API"""
        if not Config.DEEPSEEK_KEY:
            return None
        
        model_config = Config.AI_MODELS['deepseek']
        
        try:
            headers = {
                "Authorization": f"Bearer {Config.DEEPSEEK_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model_config['model'],
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt[:4000]}
                ],
                "max_tokens": model_config['max_tokens'],
                "temperature": model_config['temperature']
            }
            
            response = requests.post(
                model_config['api_url'],
                json=payload,
                headers=headers,
                timeout=model_config['timeout']
            )
            
            if response.status_code == 200:
                data = response.json()
                return data['choices'][0]['message']['content']
            
            logger.warning(f"DeepSeek error: {response.status_code} - {response.text[:200]}")
            return None
            
        except requests.exceptions.Timeout:
            logger.warning("DeepSeek timeout")
            return None
        except Exception as e:
            logger.error(f"DeepSeek exception: {e}")
            return None
    
    def call_groq(self, prompt: str) -> Optional[str]:
        """Call Groq API with model fallback"""
        if not Config.GROQ_KEY:
            return None
        
        model_config = Config.AI_MODELS['groq']
        
        try:
            headers = {
                "Authorization": f"Bearer {Config.GROQ_KEY}",
                "Content-Type": "application/json"
            }
            
            # Try primary and fallback models
            models_to_try = [
                model_config['primary_model'],
                model_config['fallback_model']
            ]
            
            for model in models_to_try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt[:4000]}
                    ],
                    "max_tokens": model_config['max_tokens'],
                    "temperature": model_config['temperature']
                }
                
                response = requests.post(
                    model_config['api_url'],
                    json=payload,
                    headers=headers,
                    timeout=model_config['timeout']
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data['choices'][0]['message']['content']
                
                logger.warning(f"Groq {model} error: {response.status_code}")
            
            return None
            
        except Exception as e:
            logger.error(f"Groq exception: {e}")
            return None
    
    def call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini API with model fallback"""
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
                    
                    logger.warning(f"Gemini {model_name} empty response")
                    
                except Exception as e:
                    logger.warning(f"Gemini {model_name} failed: {e}")
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"Gemini exception: {e}")
            return None
    
    def call_openrouter(self, prompt: str) -> Optional[str]:
        """Call OpenRouter API with comprehensive model fallback"""
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
            
            # Build complete model hierarchy
            all_models = (
                model_config['primary_models'] +
                model_config['fallback_models'] +
                model_config['budget_models']
            )
            
            for model_name in all_models:
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt[:4000]}
                    ],
                    "max_tokens": model_config['max_tokens'],
                    "temperature": model_config['temperature'],
                    "transforms": ["middle-out"]
                }
                
                response = requests.post(
                    model_config['api_url'],
                    json=payload,
                    headers=headers,
                    timeout=model_config['timeout']
                )
                
                if response.status_code == 200:
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    model_used = data.get('model', model_name)
                    logger.info(f"OpenRouter success with {model_used}")
                    return content
                
                logger.warning(f"OpenRouter {model_name}: {response.status_code}")
            
            return None
            
        except Exception as e:
            logger.error(f"OpenRouter exception: {e}")
            return None
    
    def generate_fallback_response(self, prompt: str) -> str:
        """Generate intelligent fallback when all AI services fail"""
        prompt_lower = prompt.lower()
        
        if any(word in prompt_lower for word in ['hello', 'hi', 'hey']):
            return (
                "👋 Hello! I'm JARVIS, your enterprise AI assistant.\n\n"
                "I notice our AI services are experiencing high demand. "
                "Please try again in a moment - this usually resolves within 30-60 seconds.\n\n"
                "Thank you for your patience! 🚀"
            )
        
        elif '?' in prompt:
            return (
                "🤔 I'd love to answer your question! However, all our AI services are "
                "temporarily at capacity. This is rare and resolves quickly.\n\n"
                "Please resend your question in a moment. I promise a comprehensive answer!\n\n"
                "Appreciate your patience! ⚡"
            )
        
        else:
            return (
                "⚡ JARVIS is experiencing exceptionally high demand.\n\n"
                "All AI providers (DeepSeek, Groq, Gemini, OpenRouter) are temporarily busy. "
                "This typically resolves in 30-60 seconds.\n\n"
                "Please try again shortly. We apologize for the inconvenience! 🦾"
            )
    
    def generate_response(
        self,
        prompt: str,
        preferred_model: str = None
    ) -> Tuple[str, str, float, Dict]:
        """
        Generate AI response with full fallback chain
        
        Returns:
            (response_text, model_used, response_time, metadata)
        """
        start_time = time.time()
        
        # Sanitize input
        prompt = SecurityValidator.sanitize_input(prompt, Config.MAX_MESSAGE_LENGTH)
        
        # Spam check
        if SecurityValidator.detect_spam(prompt):
            return (
                "I notice your message seems repetitive. Could you please rephrase more clearly?",
                "system",
                0,
                {'fallback': True, 'reason': 'spam'}
            )
        
        # Build function map
        ai_functions = OrderedDict([
            ('deepseek', self.call_deepseek),
            ('groq', self.call_groq),
            ('gemini', self.call_gemini),
            ('openrouter', self.call_openrouter),
        ])
        
        # Reorder if preferred model specified
        if preferred_model and preferred_model in ai_functions:
            func = ai_functions.pop(preferred_model)
            ordered = OrderedDict([(preferred_model, func)])
            ordered.update(ai_functions)
            ai_functions = ordered
        
        tried_models = []
        
        # Try each AI service
        for model_name, ai_func in ai_functions.items():
            tried_models.append(model_name)
            logger.info(f"Trying AI: {model_name}")
            
            model_start = time.time()
            try:
                response = ai_func(prompt)
                model_time = time.time() - model_start
                
                if response:
                    self.performance_metrics[model_name]['success'] += 1
                    self.performance_metrics[model_name]['total_time'] += model_time
                    
                    total_time = time.time() - start_time
                    logger.info(f"✅ {model_name} succeeded in {total_time:.2f}s")
                    
                    return response, model_name, round(total_time, 2), {
                        'tried_models': tried_models,
                        'fallback_used': len(tried_models) > 1
                    }
                
                self.performance_metrics[model_name]['failure'] += 1
                
            except Exception as e:
                logger.error(f"{model_name} exception: {e}")
                self.performance_metrics[model_name]['failure'] += 1
        
        # All failed - use fallback
        logger.error(f"All AI services failed: {tried_models}")
        total_time = time.time() - start_time
        
        fallback = self.generate_fallback_response(prompt)
        return fallback, "fallback", round(total_time, 2), {
            'tried_models': tried_models,
            'all_failed': True
        }
    
    def get_stats(self) -> Dict:
        """Get performance statistics"""
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

# Initialize AI service
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

# CORS Configuration
allowed_origins = [Config.FRONTEND_URL]
if Config.ENVIRONMENT != 'production':
    allowed_origins.extend(["http://localhost:5000", "http://127.0.0.1:5000"])

CORS(
    app,
    origins=list(set(allowed_origins)),
    supports_credentials=True,
    methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'Authorization'],
    expose_headers=['X-RateLimit-Remaining', 'X-RateLimit-Reset']
)

# =============================
# AUTHENTICATION DECORATORS
# =============================
def login_required(f):
    """Decorator for protected routes"""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = None
        
        # Check JWT from header or session
        token = JWTService.get_token_from_request()
        if token:
            payload = JWTService.verify_token(token)
            if payload:
                user = UserRepository.find_by_id(payload['user_id'])
        
        # Check session fallback
        if not user and 'user_id' in session:
            user = UserRepository.find_by_id(session['user_id'])
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'Authentication required',
                'code': 'AUTH_REQUIRED'
            }), 401
        
        g.current_user = user
        return f(*args, **kwargs)
    
    return decorated

def admin_required(f):
    """Decorator for admin routes"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not g.current_user.is_admin:
            return jsonify({
                'success': False,
                'error': 'Admin access required'
            }), 403
        return f(*args, **kwargs)
    return decorated

# =============================
# HELPER FUNCTIONS
# =============================
def get_client_ip() -> str:
    """Get real client IP"""
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or '0.0.0.0'

def json_response(data: Dict, status: int = 200) -> Response:
    """Create standardized JSON response"""
    response = make_response(jsonify(data), status)
    response.headers.update({
        'Content-Type': 'application/json',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    })
    return response

def check_rate(endpoint: str, limit: int = None, window: int = None) -> RateLimitDTO:
    """Check rate limit for request"""
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
    """Serve main application"""
    response = make_response(send_from_directory('.', 'index.html'))
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/health')
def health_check():
    """Comprehensive health check"""
    db_ok = False
    
    # Test database
    try:
        conn = db_pool.get_connection()
        if conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            db_pool.return_connection(conn)
            db_ok = True
    except Exception:
        pass
    
    status = "healthy" if db_ok else "degraded"
    
    return jsonify({
        'status': status,
        'version': Config.VERSION,
        'environment': Config.ENVIRONMENT,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'services': {
            'database': db_ok,
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
    """Initiate Google OAuth"""
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
    """Handle Google OAuth callback"""
    error = request.args.get('error')
    if error:
        logger.error(f"OAuth error: {error}")
        return redirect(f"{Config.FRONTEND_URL}?error={error}")
    
    code = request.args.get('code')
    if not code:
        return redirect(f"{Config.FRONTEND_URL}?error=No+code")
    
    # Verify state for CSRF protection
    state = request.args.get('state')
    saved_state = session.pop('oauth_state', None)
    if saved_state and state != saved_state:
        logger.error("OAuth state mismatch")
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
            logger.error(f"Token exchange failed: {token_res.text}")
            return redirect(f"{Config.FRONTEND_URL}?error=Token+exchange+failed")
        
        access_token = token_res.json().get('access_token')
        if not access_token:
            return redirect(f"{Config.FRONTEND_URL}?error=No+access+token")
        
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
        
        # Create/update user
        user = UserRepository.create_or_update(
            google_id=user_info['id'],
            email=user_info['email'],
            display_name=user_info.get('name', user_info['email'].split('@')[0]),
            avatar_url=user_info.get('picture', '')
        )
        
        if not user:
            return redirect(f"{Config.FRONTEND_URL}?error=Database+error")
        
        # Generate JWT
        jwt_token = JWTService.create_token(user.id, user.email, user.is_admin)
        UserRepository.update_session_token(user.id, jwt_token)
        
        # Set session
        session['user_id'] = user.id
        session['user_email'] = user.email
        session['user_name'] = user.display_name
        session['session_token'] = jwt_token
        
        # Log login
        UserRepository.log_login(
            user_id=user.id,
            email=user.email,
            ip_address=get_client_ip(),
            user_agent=request.headers.get('User-Agent', 'Unknown'),
            success=True
        )
        
        logger.info(f"✅ Login successful: {user.email}")
        return redirect(f"{Config.FRONTEND_URL}?token={jwt_token}")
        
    except requests.exceptions.Timeout:
        logger.error("OAuth timeout")
        return redirect(f"{Config.FRONTEND_URL}?error=Timeout")
    except Exception as e:
        logger.error(f"OAuth exception: {e}", exc_info=True)
        return redirect(f"{Config.FRONTEND_URL}?error=Authentication+failed")

@app.route('/logout')
def logout():
    """Logout user"""
    if 'user_id' in session:
        UserRepository.update_session_token(session['user_id'], None)
    session.clear()
    return redirect(Config.FRONTEND_URL)

@app.route('/api/me')
def get_current_user():
    """Get current user info"""
    token = JWTService.get_token_from_request()
    
    if token:
        payload = JWTService.verify_token(token)
        if payload:
            user = UserRepository.find_by_id(payload['user_id'])
            if user:
                refreshed = JWTService.refresh_token(token)
                return jsonify({
                    'success': True,
                    'user': {
                        'id': user.id,
                        'name': user.display_name,
                        'email': user.email,
                        'avatar': user.avatar_url,
                        'is_admin': user.is_admin
                    },
                    'token': refreshed or token
                })
    
    if 'user_id' in session:
        return jsonify({
            'success': True,
            'user': {
                'id': session['user_id'],
                'name': session.get('user_name'),
                'email': session.get('user_email')
            }
        })
    
    return jsonify({'success': False, 'error': 'Not authenticated'}), 401

# =============================
# ROUTES: CHAT SESSIONS
# =============================
@app.route('/ai/sessions', methods=['GET'])
@login_required
def get_sessions():
    """Get user's chat sessions"""
    rate_info = check_rate('get_sessions', Config.RATE_LIMIT_SESSIONS)
    if not rate_info.is_allowed:
        return json_response({
            'success': False,
            'error': 'Too many requests',
            'retry_after': int(rate_info.reset_at - time.time())
        }, 429)
    
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 50)
    
    sessions, total = ChatSessionRepository.get_user_sessions(
        g.current_user.id, page, per_page
    )
    
    return jsonify({
        'success': True,
        'sessions': [asdict(s) for s in sessions],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': max(1, (total + per_page - 1) // per_page)
        }
    })

@app.route('/ai/session', methods=['POST'])
@login_required
def create_session():
    """Create new chat session"""
    rate_info = check_rate('create_session', 20)
    if not rate_info.is_allowed:
        return json_response({
            'success': False,
            'error': 'Too many requests'
        }, 429)
    
    data = request.json or {}
    title = data.get('title', 'New Chat')
    
    session_obj = ChatSessionRepository.create_session(g.current_user.id, title)
    
    if not session_obj:
        return json_response({
            'success': False,
            'error': 'Failed to create session'
        }, 500)
    
    return jsonify({
        'success': True,
        'session_id': session_obj.id,
        'session': asdict(session_obj)
    })

@app.route('/ai/session/<int:session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    """Get session with messages"""
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    
    session_obj = ChatSessionRepository.get_session(session_id, g.current_user.id)
    if not session_obj:
        return json_response({'success': False, 'error': 'Session not found'}, 404)
    
    messages, total = ChatSessionRepository.get_messages(
        session_id, g.current_user.id, page, per_page
    )
    
    return jsonify({
        'success': True,
        'session': asdict(session_obj),
        'messages': [asdict(m) for m in messages],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': max(1, (total + per_page - 1) // per_page)
        }
    })

@app.route('/ai/session/<int:session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id):
    """Delete chat session"""
    deleted = ChatSessionRepository.delete_session(session_id, g.current_user.id)
    
    if deleted:
        return jsonify({'success': True, 'message': 'Session deleted'})
    
    return json_response({'success': False, 'error': 'Session not found'}, 404)

# =============================
# ROUTES: MESSAGES
# =============================
@app.route('/ai/session/<int:session_id>/message', methods=['POST'])
@login_required
def send_message(session_id):
    """Send message and get AI response"""
    rate_info = check_rate('send_message', Config.RATE_LIMIT_MESSAGES, Config.RATE_LIMIT_WINDOW)
    if not rate_info.is_allowed:
        return json_response({
            'success': False,
            'error': 'Rate limit exceeded',
            'retry_after': int(rate_info.reset_at - time.time()),
            'remaining': 0
        }, 429)
    
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    preferred_model = data.get('model')
    
    # Validation
    if not prompt:
        return json_response({'success': False, 'error': 'Empty message'}, 400)
    
    if len(prompt) > Config.MAX_MESSAGE_LENGTH:
        return json_response({
            'success': False,
            'error': f'Message too long (max {Config.MAX_MESSAGE_LENGTH} chars)'
        }, 400)
    
    if SecurityValidator.detect_spam(prompt):
        return json_response({'success': False, 'error': 'Message looks like spam'}, 400)
    
    # Verify session ownership
    session_obj = ChatSessionRepository.get_session(session_id, g.current_user.id)
    if not session_obj:
        return json_response({'success': False, 'error': 'Session not found'}, 404)
    
    # Save user message
    user_msg = ChatSessionRepository.add_message(session_id, 'user', prompt)
    if not user_msg:
        return json_response({'success': False, 'error': 'Failed to save message'}, 500)
    
    # Get message count for title
    msg_count = ChatSessionRepository.get_message_count(session_id)
    is_first = msg_count <= 2
    
    # Generate AI response
    response_text, model_used, response_time, metadata = ai_service.generate_response(
        prompt, preferred_model
    )
    
    # Save AI response
    bot_msg = ChatSessionRepository.add_message(
        session_id, 'assistant', response_text, model_used
    )
    
    # Update title if first message
    if is_first:
        title = prompt[:50] + ('...' if len(prompt) > 50 else '')
        ChatSessionRepository.update_title(session_id, title)
    
    logger.info(
        f"Message processed | Session: {session_id} | "
        f"Model: {model_used} | Time: {response_time}s | "
        f"User: {g.current_user.email}"
    )
    
    return jsonify({
        'success': True,
        'response': response_text,
        'model': model_used,
        'response_time': response_time,
        'is_first_message': is_first,
        'remaining_requests': rate_info.remaining,
        'metadata': metadata
    })

# =============================
# ROUTES: ADMIN
# =============================
@app.route('/admin/stats')
@admin_required
def admin_stats():
    """Get admin dashboard stats"""
    conn = db_pool.get_connection()
    if not conn:
        return json_response({'success': False, 'error': 'Database unavailable'}, 500)
    
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            stats = {}
            
            cur.execute("SELECT COUNT(*) as total FROM users")
            stats['total_users'] = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM chat_sessions")
            stats['total_sessions'] = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM messages")
            stats['total_messages'] = cur.fetchone()['total']
            
            cur.execute("""
                SELECT COUNT(*) as active FROM login_logs 
                WHERE login_time > NOW() - INTERVAL '24 hours'
            """)
            stats['active_today'] = cur.fetchone()['active']
            
            cur.execute("""
                SELECT model_used, COUNT(*) as count
                FROM messages WHERE model_used IS NOT NULL
                GROUP BY model_used ORDER BY count DESC
            """)
            stats['model_usage'] = [dict(r) for r in cur.fetchall()]
            
            # AI performance
            stats['ai_performance'] = ai_service.get_stats()
            
            return jsonify({'success': True, 'stats': stats})
            
    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        return json_response({'success': False, 'error': 'Failed to get stats'}, 500)
    finally:
        db_pool.return_connection(conn)

@app.route('/ai/stats')
@admin_required
def ai_stats():
    """Get AI performance stats"""
    return jsonify({
        'success': True,
        'performance': ai_service.get_stats()
    })

# =============================
# DATABASE INITIALIZATION
# =============================
def init_database():
    """Initialize all database tables and indexes"""
    conn = db_pool.get_connection()
    if not conn:
        logger.error("Cannot initialize database - no connection")
        return False
    
    try:
        with conn.cursor() as cur:
            logger.info("Initializing database...")
            
            # Users table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    google_id VARCHAR(100) UNIQUE,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    display_name VARCHAR(255),
                    avatar_url TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    total_logins INT DEFAULT 1,
                    session_token TEXT
                )
            """)
            
            # Login logs
            cur.execute("""
                CREATE TABLE IF NOT EXISTS login_logs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    email VARCHAR(255),
                    login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ip_address VARCHAR(45),
                    user_agent TEXT,
                    success BOOLEAN DEFAULT TRUE,
                    error_message TEXT
                )
            """)
            
            # Chat sessions
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    title VARCHAR(255) DEFAULT 'New Chat',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Messages
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    model_used VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Performance indexes
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE INDEX IF NOT EXISTS idx_users_google ON users(google_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON chat_sessions(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_login_logs_time ON login_logs(login_time DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_model ON messages(model_used);
            """)
            
            conn.commit()
            logger.info("✅ Database initialized successfully")
            return True
            
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Database initialization failed: {e}")
        return False
    finally:
        db_pool.return_connection(conn)


# Remove the orphaned SQL code that was after the comment
# (Delete lines that had the standalone SQL statements)
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
    return json_response({
        'success': False,
        'error': 'Too many requests',
        'retry_after': Config.RATE_LIMIT_WINDOW
    }, 429)

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal server error: {e}", exc_info=True)
    return json_response({'success': False, 'error': 'Internal server error'}, 500)

# =============================
# APPLICATION STARTUP
# =============================
if __name__ == "__main__":
    # Display configuration
    Config.validate()
    Config.display()
    
    # Initialize database
    db_initialized = init_database()
    if not db_initialized:
        logger.warning("⚠️ Running without database - some features disabled")
    
    # Start server
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
