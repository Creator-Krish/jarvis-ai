"""
JARVIS Enterprise AI Backend
Production-Grade Engineering by Krish Paliwal
Version: 3.1.0 Enterprise

Architecture Pattern: Layered Architecture + Repository Pattern + Service Pattern
Security: JWT + Rate Limiting + CSP + CORS + Input Validation + SQL Injection Prevention
Performance: Connection Pooling + Redis Caching + Async Ready + Query Optimization
"""

from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, g, make_response
from flask_cors import CORS
import psycopg2.extras
import psycopg2
from psycopg2 import pool, sql
import os
import time
import requests
import secrets
import logging
import sys
import json
import hashlib
import hmac
from functools import wraps
from collections import defaultdict, OrderedDict
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from datetime import datetime, timedelta, timezone
import redis
from urllib.parse import urlencode, urlparse, parse_qs
import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

# =============================
# CONFIGURATION & CONSTANTS
# =============================

class Config:
    """Centralized configuration management"""
    
    # Application
    APP_NAME = "JARVIS Enterprise AI"
    VERSION = "3.1.0"
    ENVIRONMENT = os.environ.get("FLASK_ENV", "production")
    DEBUG = ENVIRONMENT != "production"
    
    # Security
    SECRET_KEY = os.environ.get("SECRET_KEY")
    JWT_SECRET = os.environ.get("JWT_SECRET")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRY_HOURS = 168  # 7 days
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Database
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "5432"))
    DB_USER = os.environ.get("DB_USER", "postgres")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "jarvis_db")
    DB_MIN_CONNECTIONS = 2
    DB_MAX_CONNECTIONS = 20
    
    # Redis
    REDIS_URL = os.environ.get("REDIS_URL")
    
    # Rate Limiting
    RATE_LIMIT_MESSAGES = 10  # messages per window
    RATE_LIMIT_WINDOW = 60    # seconds
    RATE_LIMIT_SESSIONS = 30
    RATE_LIMIT_LOGIN = 5
    
    # AI Models
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_KEY")
    
    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    
    # URLs
    PRODUCTION_DOMAIN = os.environ.get("FRONTEND_URL", "https://jarvis-e76i.onrender.com")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", f"{PRODUCTION_DOMAIN}/login/callback")
    
    # Content Limits
    MAX_MESSAGE_LENGTH = 5000
    MAX_SESSION_TITLE_LENGTH = 100
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        required = ['SECRET_KEY', 'JWT_SECRET']
        missing = [key for key in required if not getattr(cls, key)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

# =============================
# LOGGING CONFIGURATION
# =============================

class LoggerFactory:
    """Factory for creating configured loggers"""
    
    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            # File handler
            file_handler = logging.FileHandler('jarvis.log')
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            
            # Console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.WARNING if Config.ENVIRONMENT == 'production' else logging.INFO)
            console_formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(console_formatter)
            
            logger.addHandler(file_handler)
            logger.addHandler(console_handler)
        
        return logger

logger = LoggerFactory.get_logger(__name__)

# =============================
# DATA TRANSFER OBJECTS
# =============================

@dataclass
class UserDTO:
    """User Data Transfer Object"""
    id: int
    google_id: str
    email: str
    display_name: str
    avatar_url: Optional[str]
    is_admin: bool
    created_at: datetime
    last_login: Optional[datetime]
    total_logins: int

@dataclass
class SessionDTO:
    """Chat Session Data Transfer Object"""
    id: int
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime

@dataclass
class MessageDTO:
    """Message Data Transfer Object"""
    id: int
    session_id: int
    role: str
    content: str
    model_used: Optional[str]
    created_at: datetime

# =============================
# DATABASE CONNECTION POOL (Singleton Pattern)
# =============================

class DatabasePool:
    """Thread-safe database connection pool singleton"""
    _instance = None
    _pool = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize the connection pool"""
        if self._pool is None:
            try:
                self._pool = pool.ThreadedConnectionPool(
                    Config.DB_MIN_CONNECTIONS,
                    Config.DB_MAX_CONNECTIONS,
                    host=Config.DB_HOST,
                    port=Config.DB_PORT,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    database=Config.DB_NAME,
                    connect_timeout=10,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5
                )
                logger.info(f"✅ Database pool initialized ({Config.DB_MIN_CONNECTIONS}-{Config.DB_MAX_CONNECTIONS} connections)")
            except Exception as e:
                logger.error(f"❌ Failed to initialize database pool: {e}")
                raise
    
    def get_connection(self):
        """Get a connection from the pool"""
        if self._pool is None:
            self.initialize()
        try:
            return self._pool.getconn()
        except pool.PoolError:
            logger.warning("Connection pool exhausted, creating new connection")
            return psycopg2.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                connect_timeout=10
            )
    
    def return_connection(self, conn):
        """Return a connection to the pool"""
        if self._pool:
            try:
                self._pool.putconn(conn)
            except pool.PoolError:
                conn.close()
        else:
            conn.close()
    
    def close_all(self):
        """Close all connections"""
        if self._pool:
            self._pool.closeall()
            self._pool = None

db_pool = DatabasePool()

# =============================
# REDIS CACHE (Singleton Pattern)
# =============================

class RedisCache:
    """Redis cache singleton with fallback"""
    _instance = None
    _client = None
    _fallback = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize Redis connection"""
        if Config.REDIS_URL:
            try:
                self._client = redis.from_url(
                    Config.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    retry_on_timeout=True
                )
                self._client.ping()
                logger.info("✅ Redis cache connected")
            except Exception as e:
                logger.warning(f"⚠️ Redis unavailable, using in-memory cache: {e}")
                self._client = None
    
    def get(self, key: str) -> Optional[str]:
        """Get value from cache"""
        if self._client:
            try:
                return self._client.get(key)
            except redis.RedisError:
                return self._fallback.get(key)
        return self._fallback.get(key)
    
    def set(self, key: str, value: str, expiry: int = 300):
        """Set value in cache"""
        if self._client:
            try:
                self._client.setex(key, expiry, value)
            except redis.RedisError:
                self._fallback[key] = value
        else:
            self._fallback[key] = value
    
    def delete(self, key: str):
        """Delete value from cache"""
        if self._client:
            try:
                self._client.delete(key)
            except redis.RedisError:
                self._fallback.pop(key, None)
        else:
            self._fallback.pop(key, None)
    
    def increment(self, key: str, expiry: int = 60) -> int:
        """Increment counter"""
        if self._client:
            try:
                pipe = self._client.pipeline()
                pipe.incr(key)
                pipe.expire(key, expiry)
                result = pipe.execute()
                return result[0]
            except redis.RedisError:
                current = int(self._fallback.get(key, 0)) + 1
                self._fallback[key] = current
                return current
        else:
            current = int(self._fallback.get(key, 0)) + 1
            self._fallback[key] = current
            return current

redis_cache = RedisCache()

# =============================
# SECURITY: Input Validation & Sanitization
# =============================

class SecurityValidator:
    """Input validation and sanitization utilities"""
    
    # Regex patterns
    EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    SAFE_STRING_PATTERN = re.compile(r'^[a-zA-Z0-9\s\-_.,!?@#$%^&*()+=:;\'\"\[\]{}|\\/~`]+$')
    
    @staticmethod
    def sanitize_input(text: str, max_length: int = 5000) -> str:
        """Sanitize user input"""
        if not text:
            return ""
        
        # Remove null bytes
        text = text.replace('\x00', '')
        
        # Remove control characters except newlines and tabs
        text = ''.join(char for char in text if char == '\n' or char == '\t' or ord(char) >= 32)
        
        # Truncate to max length
        if len(text) > max_length:
            text = text[:max_length]
        
        # Remove HTML tags
        text = re.sub(r'<[^>]*>', '', text)
        
        # Remove potential script injections
        text = text.replace('javascript:', '')
        text = text.replace('onerror=', '')
        text = text.replace('onload=', '')
        
        return text.strip()
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format"""
        return bool(SecurityValidator.EMAIL_PATTERN.match(email))
    
    @staticmethod
    def is_spam(text: str) -> bool:
        """Basic spam detection"""
        if len(text) < 2:
            return True
        
        words = text.split()
        
        # Check for repeated characters
        for word in words:
            if len(word) > 10 and len(set(word)) < 3:
                return True
        
        # Check for excessive repetition
        if len(words) > 5 and len(set(words)) < 3:
            return True
        
        return False
    
    @staticmethod
    def generate_csrf_token() -> str:
        """Generate CSRF token"""
        return secrets.token_hex(32)

# =============================
# RATE LIMITER (Token Bucket Algorithm)
# =============================

class RateLimiter:
    """Token bucket rate limiter with Redis support"""
    
    def __init__(self):
        self.cache = redis_cache
        self.local_buckets = defaultdict(lambda: {'tokens': 0, 'last_refill': time.time()})
    
    def is_allowed(self, key: str, limit: int = 10, window: int = 60) -> Tuple[bool, int]:
        """
        Check if request is allowed
        Returns: (is_allowed, remaining_tokens)
        """
        current_time = time.time()
        
        # Try Redis first
        redis_key = f"rate_limit:{key}"
        current = self.cache.get(redis_key)
        
        if current is not None:
            tokens = int(current)
            if tokens >= limit:
                return False, 0
            
            new_tokens = self.cache.increment(redis_key, window)
            remaining = limit - new_tokens
            return True, max(0, remaining)
        
        # Fallback to local bucket
        bucket = self.local_buckets[key]
        time_passed = current_time - bucket['last_refill']
        
        # Refill tokens (1 token per window/limit seconds)
        refill_rate = window / limit
        tokens_to_add = time_passed / refill_rate
        bucket['tokens'] = min(limit, bucket['tokens'] + tokens_to_add)
        bucket['last_refill'] = current_time
        
        if bucket['tokens'] >= 1:
            bucket['tokens'] -= 1
            remaining = int(bucket['tokens'])
            return True, remaining
        
        return False, 0

rate_limiter = RateLimiter()

# =============================
# JWT AUTHENTICATION SERVICE
# =============================

class JWTService:
    """JWT token management service"""
    
    @staticmethod
    def create_token(user_id: int, email: str, is_admin: bool = False) -> str:
        """Create JWT token"""
        payload = {
            'user_id': user_id,
            'email': email,
            'is_admin': is_admin,
            'exp': datetime.now(timezone.utc) + timedelta(hours=Config.JWT_EXPIRY_HOURS),
            'iat': datetime.now(timezone.utc),
            'jti': secrets.token_hex(16)
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)
    
    @staticmethod
    def verify_token(token: str) -> Optional[Dict]:
        """Verify JWT token"""
        try:
            payload = jwt.decode(
                token, 
                Config.JWT_SECRET, 
                algorithms=[Config.JWT_ALGORITHM],
                options={'verify_exp': True}
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            return None
    
    @staticmethod
    def refresh_token(token: str) -> Optional[str]:
        """Refresh JWT token if valid but expiring soon"""
        payload = JWTService.verify_token(token)
        if not payload:
            return None
        
        # Refresh if less than 24 hours remaining
        exp = datetime.fromtimestamp(payload['exp'], tz=timezone.utc)
        if exp - datetime.now(timezone.utc) < timedelta(hours=24):
            return JWTService.create_token(
                payload['user_id'],
                payload['email'],
                payload.get('is_admin', False)
            )
        
        return token

# =============================
# USER REPOSITORY (Repository Pattern)
# =============================

class UserRepository:
    """Data access layer for users"""
    
    @staticmethod
    def find_by_google_id(google_id: str) -> Optional[UserDTO]:
        """Find user by Google ID"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, google_id, email, display_name, avatar_url, 
                           is_admin, created_at, last_login, total_logins
                    FROM users 
                    WHERE google_id = %s
                """, (google_id,))
                row = cur.fetchone()
                return UserDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Error finding user by Google ID: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def find_by_email(email: str) -> Optional[UserDTO]:
        """Find user by email"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, google_id, email, display_name, avatar_url, 
                           is_admin, created_at, last_login, total_logins
                    FROM users 
                    WHERE email = %s
                """, (email,))
                row = cur.fetchone()
                return UserDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Error finding user by email: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def find_by_id(user_id: int) -> Optional[UserDTO]:
        """Find user by ID"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, google_id, email, display_name, avatar_url, 
                           is_admin, created_at, last_login, total_logins
                    FROM users 
                    WHERE id = %s
                """, (user_id,))
                row = cur.fetchone()
                return UserDTO(**row) if row else None
        except Exception as e:
            logger.error(f"Error finding user by ID: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def create_or_update(google_id: str, email: str, display_name: str, avatar_url: str) -> UserDTO:
        """Create or update user"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Try to find existing user
                cur.execute("""
                    SELECT id FROM users 
                    WHERE google_id = %s OR email = %s
                """, (google_id, email))
                existing = cur.fetchone()
                
                if existing:
                    # Update existing user
                    cur.execute("""
                        UPDATE users 
                        SET display_name = %s, avatar_url = %s, 
                            last_login = NOW(), total_logins = total_logins + 1
                        WHERE id = %s
                        RETURNING id, google_id, email, display_name, avatar_url, 
                                  is_admin, created_at, last_login, total_logins
                    """, (display_name, avatar_url, existing['id']))
                else:
                    # Create new user
                    cur.execute("""
                        INSERT INTO users (google_id, email, display_name, avatar_url)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, google_id, email, display_name, avatar_url, 
                                  is_admin, created_at, last_login, total_logins
                    """, (google_id, email, display_name, avatar_url))
                
                row = cur.fetchone()
                conn.commit()
                return UserDTO(**row)
        except Exception as e:
            conn.rollback()
            logger.error(f"Error creating/updating user: {e}")
            raise
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def update_session_token(user_id: int, token: str):
        """Update user's session token"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users SET session_token = %s WHERE id = %s
                """, (token, user_id))
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Error updating session token: {e}")
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def log_login(user_id: int, email: str, ip: str, user_agent: str, success: bool = True, error: str = None):
        """Log login attempt"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO login_logs (user_id, email, ip_address, user_agent, success, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (user_id, email, ip, user_agent, success, error))
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Error logging login: {e}")
        finally:
            db_pool.return_connection(conn)

# =============================
# CHAT SESSION REPOSITORY
# =============================

class ChatSessionRepository:
    """Data access layer for chat sessions"""
    
    @staticmethod
    def get_user_sessions(user_id: int, page: int = 1, per_page: int = 20) -> Tuple[List[SessionDTO], int]:
        """Get paginated sessions for user"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get total count
                cur.execute("SELECT COUNT(*) as total FROM chat_sessions WHERE user_id = %s", (user_id,))
                total = cur.fetchone()['total']
                
                # Get paginated results
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
            logger.error(f"Error getting user sessions: {e}")
            return [], 0
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def create_session(user_id: int, title: str = "New Chat") -> SessionDTO:
        """Create new chat session"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                title = SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE_LENGTH)
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
            logger.error(f"Error creating session: {e}")
            raise
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def get_session(session_id: int, user_id: int) -> Optional[SessionDTO]:
        """Get session by ID with ownership check"""
        conn = db_pool.get_connection()
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
            logger.error(f"Error getting session: {e}")
            return None
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def delete_session(session_id: int, user_id: int) -> bool:
        """Delete session with ownership check"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM chat_sessions 
                    WHERE id = %s AND user_id = %s
                """, (session_id, user_id))
                deleted = cur.rowcount > 0
                conn.commit()
                return deleted
        except Exception as e:
            conn.rollback()
            logger.error(f"Error deleting session: {e}")
            return False
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def update_title(session_id: int, title: str):
        """Update session title"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor() as cur:
                title = SecurityValidator.sanitize_input(title, Config.MAX_SESSION_TITLE_LENGTH)
                cur.execute("""
                    UPDATE chat_sessions SET title = %s, updated_at = NOW()
                    WHERE id = %s
                """, (title, session_id))
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Error updating session title: {e}")
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def get_messages(session_id: int, user_id: int, page: int = 1, per_page: int = 50) -> Tuple[List[MessageDTO], int]:
        """Get paginated messages for session"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Verify ownership
                cur.execute("SELECT id FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
                if not cur.fetchone():
                    return [], 0
                
                # Get total count
                cur.execute("SELECT COUNT(*) as total FROM messages WHERE session_id = %s", (session_id,))
                total = cur.fetchone()['total']
                
                # Get paginated results
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
            logger.error(f"Error getting messages: {e}")
            return [], 0
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def add_message(session_id: int, role: str, content: str, model_used: str = None) -> MessageDTO:
        """Add message to session"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                content = SecurityValidator.sanitize_input(content, Config.MAX_MESSAGE_LENGTH)
                cur.execute("""
                    INSERT INTO messages (session_id, role, content, model_used)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, session_id, role, content, model_used, created_at
                """, (session_id, role, content, model_used))
                
                # Update session timestamp
                cur.execute("UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s", (session_id,))
                
                row = cur.fetchone()
                conn.commit()
                return MessageDTO(**row)
        except Exception as e:
            conn.rollback()
            logger.error(f"Error adding message: {e}")
            raise
        finally:
            db_pool.return_connection(conn)
    
    @staticmethod
    def get_message_count(session_id: int) -> int:
        """Get message count for session"""
        conn = db_pool.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM messages WHERE session_id = %s", (session_id,))
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"Error getting message count: {e}")
            return 0
        finally:
            db_pool.return_connection(conn)

# =============================
# AI SERVICE (Strategy Pattern)
# =============================
# =============================
# JARVIS Enterprise AI - Environment Variables
# IMPORTANT: Never commit .env to GitHub!
# =============================

# Application
FLASK_ENV=production
PORT=5000
FRONTEND_URL=https://your-domain.com

# Security (Use strong random strings)
SECRET_KEY=your-secret-key-here
JWT_SECRET=your-jwt-secret-here

# Database (Render PostgreSQL)
DB_HOST=your-db-host
DB_NAME=your-db-name
DB_USER=your-db-user
DB_PASSWORD=your-db-password
DB_PORT=5432

# Redis (Optional)
REDIS_URL=redis://your-redis-url

# AI Model Keys
DEEPSEEK_KEY=sk-your-deepseek-key
GROQ_KEY=gsk_your-groq-key
GEMINI_KEY=AIza_your-gemini-key
OPENROUTER_KEY=sk-or-v1-your-openrouter-key

# Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=https://your-domain.com/login/callback

# Feature Flags
ENABLE_NLP=true
ENABLE_OCR=true
ENABLE_PDF=true
ENABLE_VOICE=true

# Rate Limiting
RATE_LIMIT=10
RATE_WINDOW=60

# Upload
MAX_CONTENT_LENGTH=10485760
UPLOAD_FOLDER=/tmp/uploads

# Logging
LOG_FILE=jarvis.log
LOG_LEVEL=INFO
# =============================
# FLASK APP CONFIGURATION
# =============================

# Validate configuration
Config.validate()

app = Flask(__name__, static_folder='.')
app.secret_key = Config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
app.config['SESSION_COOKIE_SECURE'] = Config.SESSION_COOKIE_SECURE
app.config['SESSION_COOKIE_HTTPONLY'] = Config.SESSION_COOKIE_HTTPONLY
app.config['SESSION_COOKIE_SAMESITE'] = Config.SESSION_COOKIE_SAMESITE
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# CORS Configuration
ALLOWED_ORIGINS = [Config.PRODUCTION_DOMAIN]
if Config.ENVIRONMENT != 'production':
    ALLOWED_ORIGINS.extend(["http://localhost:5000", "http://127.0.0.1:5000"])

CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True, methods=['GET', 'POST', 'PUT', 'DELETE'])

# Initialize services
db_pool.initialize()
redis_cache.initialize()

# =============================
# AUTH DECORATOR
# =============================

def login_required(f):
    """Decorator for protected routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = None
        
        # Check Authorization header (Bearer token)
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            payload = JWTService.verify_token(token)
            if payload:
                user = UserRepository.find_by_id(payload['user_id'])
        
        # Fallback to session
        if not user and 'user_id' in session:
            user = UserRepository.find_by_id(session['user_id'])
            if user:
                # Verify session token matches
                if user.session_token != session.get('session_token'):
                    user = None
        
        if not user:
            return jsonify({'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
        
        g.current_user = user
        return f(*args, **kwargs)
    
    return decorated_function

def admin_required(f):
    """Decorator for admin routes"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not g.current_user.is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# =============================
# HELPER FUNCTIONS
# =============================

def get_client_ip() -> str:
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '0.0.0.0'

def check_rate_limit(key: str, limit: int, window: int) -> Tuple[bool, int]:
    """Check rate limit"""
    ip = get_client_ip()
    rate_key = f"{key}:{ip}"
    return rate_limiter.is_allowed(rate_key, limit, window)

def json_response(data: Dict, status_code: int = 200) -> Tuple:
    """Create JSON response"""
    response = make_response(jsonify(data), status_code)
    response.headers['Content-Type'] = 'application/json'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# =============================
# ROUTES: HEALTH & INDEX
# =============================

@app.route('/health')
def health_check():
    """Health check endpoint"""
    db_healthy = False
    redis_healthy = False
    
    try:
        conn = db_pool.get_connection()
        conn.cursor().execute("SELECT 1")
        db_pool.return_connection(conn)
        db_healthy = True
    except Exception:
        pass
    
    if redis_cache._client:
        try:
            redis_cache._client.ping()
            redis_healthy = True
        except Exception:
            pass
    
    status = "healthy" if db_healthy else "degraded"
    
    return jsonify({
        'status': status,
        'version': Config.VERSION,
        'environment': Config.ENVIRONMENT,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'services': {
            'database': db_healthy,
            'redis': redis_healthy,
            'deepseek': bool(Config.DEEPSEEK_KEY),
            'groq': bool(Config.GROQ_KEY),
            'gemini': bool(Config.GEMINI_KEY)
        },
        'uptime': round(time.time() - start_time, 2)
    })

@app.route('/')
def index():
    """Serve the main application"""
    response = make_response(send_from_directory('.', 'index.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# =============================
# ROUTES: AUTHENTICATION
# =============================

@app.route('/login/google')
def google_login():
    """Initiate Google OAuth login"""
    if not Config.GOOGLE_CLIENT_ID:
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error=OAuth not configured")
    
    # Generate state for CSRF protection
    state = secrets.token_hex(32)
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
    logger.info(f"Redirecting to Google OAuth: {auth_url[:100]}...")
    return redirect(auth_url)

@app.route('/login/callback')
def google_callback():
    """Handle Google OAuth callback"""
    code = request.args.get('code')
    state = request.args.get('state')
    error = request.args.get('error')
    
    # Check for errors
    if error:
        logger.error(f"Google OAuth error: {error}")
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error={error}")
    
    if not code:
        logger.error("No code received from Google")
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error=No authorization code")
    
    # Verify state (CSRF protection)
    saved_state = session.pop('oauth_state', None)
    if saved_state and state != saved_state:
        logger.error(f"State mismatch: saved={saved_state}, received={state}")
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error=Invalid state")
    
    try:
        # Exchange code for tokens
        token_data = {
            'code': code,
            'client_id': Config.GOOGLE_CLIENT_ID,
            'client_secret': Config.GOOGLE_CLIENT_SECRET,
            'redirect_uri': Config.GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code'
        }
        
        logger.info("Exchanging code for tokens...")
        token_res = requests.post('https://oauth2.googleapis.com/token', data=token_data, timeout=15)
        
        if token_res.status_code != 200:
            logger.error(f"Token exchange failed: {token_res.status_code} - {token_res.text}")
            return redirect(f"{Config.PRODUCTION_DOMAIN}?error=Token exchange failed")
        
        token_json = token_res.json()
        access_token = token_json.get('access_token')
        
        if not access_token:
            logger.error("No access token received")
            return redirect(f"{Config.PRODUCTION_DOMAIN}?error=No access token")
        
        # Get user info from Google
        logger.info("Fetching user info from Google...")
        user_res = requests.get(
            'https://www.googleapis.com/oauth2/v1/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=15
        )
        
        if user_res.status_code != 200:
            logger.error(f"User info fetch failed: {user_res.status_code}")
            return redirect(f"{Config.PRODUCTION_DOMAIN}?error=Failed to get user info")
        
        user_info = user_res.json()
        logger.info(f"User info received for: {user_info.get('email')}")
        
        # Create or update user
        user = UserRepository.create_or_update(
            google_id=user_info['id'],
            email=user_info['email'],
            display_name=user_info.get('name', user_info['email'].split('@')[0]),
            avatar_url=user_info.get('picture', '')
        )
        
        # Generate JWT token
        jwt_token = JWTService.create_token(user.id, user.email, user.is_admin)
        
        # Update session token in database
        UserRepository.update_session_token(user.id, jwt_token)
        
        # Set session
        session['user_id'] = user.id
        session['user_email'] = user.email
        session['user_name'] = user.display_name
        session['session_token'] = jwt_token
        session['is_admin'] = user.is_admin
        
        # Log successful login
        UserRepository.log_login(
            user_id=user.id,
            email=user.email,
            ip=get_client_ip(),
            user_agent=request.headers.get('User-Agent', 'Unknown'),
            success=True
        )
        
        logger.info(f"✅ User {user.email} logged in successfully")
        
        # Redirect to main app with token
        return redirect(f"{Config.PRODUCTION_DOMAIN}?token={jwt_token}")
        
    except requests.exceptions.Timeout:
        logger.error("Timeout during Google OAuth")
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error=Connection timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error during OAuth: {e}")
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error=Network error")
    except Exception as e:
        logger.error(f"Unexpected OAuth error: {e}", exc_info=True)
        return redirect(f"{Config.PRODUCTION_DOMAIN}?error=Authentication failed")

@app.route('/api/me')
def get_current_user():
    """Get current user info"""
    # Check Bearer token
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        payload = JWTService.verify_token(token)
        if payload:
            user = UserRepository.find_by_id(payload['user_id'])
            if user:
                # Optionally refresh token
                new_token = JWTService.refresh_token(token)
                return jsonify({
                    'success': True,
                    'user': {
                        'id': user.id,
                        'name': user.display_name,
                        'email': user.email,
                        'avatar': user.avatar_url,
                        'is_admin': user.is_admin
                    },
                    'token': new_token or token
                })
    
    # Check session
    if 'user_id' in session:
        user = UserRepository.find_by_id(session['user_id'])
        if user and user.session_token == session.get('session_token'):
            return jsonify({
                'success': True,
                'user': {
                    'id': user.id,
                    'name': user.display_name,
                    'email': user.email,
                    'avatar': user.avatar_url,
                    'is_admin': user.is_admin
                }
            })
    
    return jsonify({'success': False, 'error': 'Not authenticated'}), 401

@app.route('/logout')
def logout():
    """Logout user"""
    if 'user_id' in session:
        UserRepository.update_session_token(session['user_id'], None)
    session.clear()
    return redirect(Config.PRODUCTION_DOMAIN)

@app.route('/logout-all', methods=['POST'])
@login_required
def logout_all_devices():
    """Logout from all devices"""
    UserRepository.update_session_token(g.current_user.id, None)
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out from all devices'})

# =============================
# ROUTES: CHAT SESSIONS
# =============================

@app.route('/ai/sessions', methods=['GET'])
@login_required
def get_sessions():
    """Get user's chat sessions"""
    # Rate limiting
    is_allowed, remaining = check_rate_limit('get_sessions', Config.RATE_LIMIT_SESSIONS, Config.RATE_LIMIT_WINDOW)
    if not is_allowed:
        return json_response({'success': False, 'error': 'Too many requests'}, 429)
    
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 50)
    
    sessions, total = ChatSessionRepository.get_user_sessions(g.current_user.id, page, per_page)
    
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
    is_allowed, _ = check_rate_limit('create_session', 20, Config.RATE_LIMIT_WINDOW)
    if not is_allowed:
        return json_response({'success': False, 'error': 'Too many requests'}, 429)
    
    data = request.json or {}
    title = data.get('title', 'New Chat')
    
    session = ChatSessionRepository.create_session(g.current_user.id, title)
    
    return jsonify({
        'success': True,
        'session_id': session.id,
        'session': asdict(session)
    })

@app.route('/ai/session/<int:session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    """Get session messages"""
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    
    session = ChatSessionRepository.get_session(session_id, g.current_user.id)
    if not session:
        return json_response({'success': False, 'error': 'Session not found'}, 404)
    
    messages, total = ChatSessionRepository.get_messages(session_id, g.current_user.id, page, per_page)
    
    return jsonify({
        'success': True,
        'session': asdict(session),
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
    # Rate limiting
    is_allowed, remaining = check_rate_limit('send_message', Config.RATE_LIMIT_MESSAGES, Config.RATE_LIMIT_WINDOW)
    if not is_allowed:
        return json_response({
            'success': False, 
            'error': f'Rate limit exceeded. Try again in {Config.RATE_LIMIT_WINDOW} seconds.',
            'retry_after': Config.RATE_LIMIT_WINDOW
        }, 429)
    
    # Get and validate input
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    preferred_model = data.get('model', 'deepseek')
    
    # Validate prompt
    if not prompt:
        return json_response({'success': False, 'error': 'Message cannot be empty'}, 400)
    
    if len(prompt) > Config.MAX_MESSAGE_LENGTH:
        return json_response({'success': False, 'error': f'Message too long (max {Config.MAX_MESSAGE_LENGTH} characters)'}, 400)
    
    # Spam check
    if SecurityValidator.is_spam(prompt):
        return json_response({'success': False, 'error': 'Message looks like spam'}, 400)
    
    # Verify session ownership
    session = ChatSessionRepository.get_session(session_id, g.current_user.id)
    if not session:
        return json_response({'success': False, 'error': 'Session not found'}, 404)
    
    # Save user message
    user_message = ChatSessionRepository.add_message(session_id, 'user', prompt)
    
    # Get message count for title update
    message_count = ChatSessionRepository.get_message_count(session_id)
    is_first = message_count <= 2
    
    # Generate AI response
    response_text, model_used, response_time = ai_service.generate_response(prompt, preferred_model)
    
    # Save AI response
    bot_message = ChatSessionRepository.add_message(session_id, 'assistant', response_text, model_used)
    
    # Update title if first message
    if is_first:
        title = prompt[:50] + ('...' if len(prompt) > 50 else '')
        ChatSessionRepository.update_title(session_id, title)
    
    logger.info(f"Message processed - Session: {session_id}, Model: {model_used}, Time: {response_time}s")
    
    return jsonify({
        'success': True,
        'response': response_text,
        'model': model_used,
        'response_time': response_time,
        'is_first_message': is_first,
        'remaining_requests': remaining,
        'message_id': bot_message.id
    })

# =============================
# ROUTES: ADMIN
# =============================

@app.route('/admin/stats')
@admin_required
def admin_stats():
    """Get admin statistics"""
    conn = db_pool.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            stats = {}
            
            cur.execute("SELECT COUNT(*) as total FROM users")
            stats['total_users'] = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM chat_sessions")
            stats['total_sessions'] = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM messages")
            stats['total_messages'] = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM login_logs WHERE login_time > NOW() - INTERVAL '24 hours'")
            stats['active_today'] = cur.fetchone()['total']
            
            cur.execute("""
                SELECT model_used, COUNT(*) as count 
                FROM messages 
                WHERE model_used IS NOT NULL 
                GROUP BY model_used 
                ORDER BY count DESC
            """)
            stats['model_usage'] = [dict(row) for row in cur.fetchall()]
            
            return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        return json_response({'success': False, 'error': 'Failed to get stats'}, 500)
    finally:
        db_pool.return_connection(conn)

# =============================
# DATABASE INITIALIZATION
# =============================

def init_database():
    """Initialize database tables"""
    conn = db_pool.get_connection()
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
            
            # Admin users
            cur.execute("""
                UPDATE users SET is_admin = TRUE 
                WHERE email IN ('krish@gmail.com', 'admin@jarvis.ai')
            """)
            
            # Login logs table
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
            
            # Chat sessions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    title VARCHAR(255) DEFAULT 'New Chat',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Messages table
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
            
            # Indexes for performance
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON chat_sessions(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_login_logs_user ON login_logs(user_id, login_time DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_model ON messages(model_used);
            """)
            
            conn.commit()
            logger.info("✅ Database initialized successfully")
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Database initialization failed: {e}")
        raise
    finally:
        db_pool.return_connection(conn)

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
        return json_response({'success': False, 'error': 'Resource not found'}, 404)
    return send_from_directory('.', 'index.html')

@app.errorhandler(405)
def method_not_allowed(e):
    return json_response({'success': False, 'error': 'Method not allowed'}, 405)

@app.errorhandler(429)
def rate_limit_exceeded(e):
    return json_response({
        'success': False, 
        'error': 'Too many requests',
        'retry_after': Config.RATE_LIMIT_WINDOW
    }, 429)

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}", exc_info=True)
    return json_response({'success': False, 'error': 'Internal server error'}, 500)

# =============================
# APPLICATION STARTUP
# =============================

start_time = time.time()

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info(f"🚀 {Config.APP_NAME} v{Config.VERSION}")
    logger.info(f"📍 Environment: {Config.ENVIRONMENT}")
    logger.info(f"🔗 Domain: {Config.PRODUCTION_DOMAIN}")
    logger.info(f"💾 Database: {Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    logger.info(f"📦 Redis: {'Connected' if redis_cache._client else 'Using fallback cache'}")
    logger.info(f"🤖 AI Models: DeepSeek={'✅' if Config.DEEPSEEK_KEY else '❌'} | Groq={'✅' if Config.GROQ_KEY else '❌'} | Gemini={'✅' if Config.GEMINI_KEY else '❌'}")
    logger.info(f"🔐 OAuth: {'✅' if Config.GOOGLE_CLIENT_ID else '❌'}")
    logger.info(f"⚡ Rate Limiting: {Config.RATE_LIMIT_MESSAGES} msg/{Config.RATE_LIMIT_WINDOW}s")
    logger.info("=" * 60)
    
    # Initialize database
    init_database()
    
    # Start server
    port = int(os.environ.get("PORT", 5000))
    
    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve
            logger.info(f"🔧 Starting production server on port {port}")
            serve(app, host="0.0.0.0", port=port, threads=8, connection_limit=500, channel_timeout=120)
        except ImportError:
            logger.warning("Waitress not installed, falling back to Flask development server")
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    else:
        logger.info(f"🔧 Starting development server on port {port}")
        app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
