# SECTION 1: Configuration and Settings
# EONIX AI Platform - Main Application Configuration

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import jwt
import requests
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("eonix")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class Config:
    APP_NAME = "EONIX"
    VERSION = "5.0"
    API_VERSION = "v1"
    ENVIRONMENT = os.environ.get("FLASK_ENV", "production").lower()
    PORT = int(os.environ.get("PORT", "5000"))

    FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://eonix-7nmk.onrender.com").rstrip("/")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", f"{FRONTEND_URL}/login/callback")

    # -----------------------------------------------------------------------
    # Security - Secrets (MUST be set in production)
    # -----------------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
    JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_urlsafe(48)
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "168"))

    # -----------------------------------------------------------------------
    # Session Security
    # -----------------------------------------------------------------------
    SESSION_COOKIE_SECURE = ENVIRONMENT != "development"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict" if ENVIRONMENT == "production" else "Lax"

    # -----------------------------------------------------------------------
    # Security Headers
    # -----------------------------------------------------------------------
    SECURITY_HEADERS = {
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https:; connect-src 'self' https://api.deepseek.com https://api.groq.com https://generativelanguage.googleapis.com https://openrouter.ai",
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    }

    # -----------------------------------------------------------------------
    # Content Limits (Tuned for production)
    # -----------------------------------------------------------------------
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024)))  # 10MB
    MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", "4000"))  # Reduced for production
    MAX_SESSION_TITLE = int(os.environ.get("MAX_SESSION_TITLE", "120"))
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "18"))
    MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))  # 5MB
    REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", "30"))  # Reduced for production
    IMAGE_REQUEST_TIMEOUT = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT", "60"))

    # -----------------------------------------------------------------------
    # Rate Limiting (Production-tuned with per-endpoint options)
    # -----------------------------------------------------------------------
    RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "20"))  # Reduced
    RATE_LIMIT_SESSIONS = int(os.environ.get("RATE_LIMIT_SESSIONS", "50"))  # Reduced
    RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
    RATE_LIMIT_CLEANUP_INTERVAL = int(os.environ.get("RATE_LIMIT_CLEANUP_INTERVAL", "300"))
    
    # Per-endpoint rate limits
    RATE_LIMIT_PER_ENDPOINT = {
        "chat": {"messages": 20, "window": 60},
        "image_generation": {"messages": 5, "window": 60},
        "auth": {"messages": 10, "window": 300},  # 10 login attempts per 5 minutes
    }

    # -----------------------------------------------------------------------
    # Feature Flags
    # -----------------------------------------------------------------------
    ALLOW_DEV_LOGIN = os.environ.get("ALLOW_DEV_LOGIN", "false").lower() == "true"
    ENABLE_MODEL_BOOT_PROBE = os.environ.get("ENABLE_MODEL_BOOT_PROBE", "false").lower() == "true"
    MODEL_PROBE_TIMEOUT = int(os.environ.get("MODEL_PROBE_TIMEOUT", "12"))

    # -----------------------------------------------------------------------
    # Error Reporting (Enabled by default in production)
    # -----------------------------------------------------------------------
    ERROR_REPORTING_ENABLED = os.environ.get(
        "ERROR_REPORTING_ENABLED", 
        str(ENVIRONMENT == "production").lower()
    ).lower() == "true"
    ERROR_REPORTING_WEBHOOK = os.environ.get("ERROR_REPORTING_WEBHOOK")
    ERROR_REPORTING_SANITIZE = True  # Sanitize sensitive data in error reports
    
    # -----------------------------------------------------------------------
    # Health & Monitoring
    # -----------------------------------------------------------------------
    HEALTH_CHECK_ENDPOINT = os.environ.get("HEALTH_CHECK_ENDPOINT", "/health")
    HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "30"))
    
    # Caching
    CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() == "true"
    CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))
    CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "simple")  # simple, redis, memcached
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    
    # Monitoring
    ENABLE_METRICS = os.environ.get("ENABLE_METRICS", "true").lower() == "true"
    METRICS_PORT = int(os.environ.get("METRICS_PORT", "9090"))
    ENABLE_REQUEST_LOGGING = os.environ.get("ENABLE_REQUEST_LOGGING", "true").lower() == "true"

    # -----------------------------------------------------------------------
    # Admin Configuration (No hardcoded emails)
    # -----------------------------------------------------------------------
    ADMIN_EMAILS = {
        email.strip().lower()
        for email in os.environ.get("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }

    # -----------------------------------------------------------------------
    # Provider API Keys
    # -----------------------------------------------------------------------
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    # -----------------------------------------------------------------------
    # Provider Base URLs
    # -----------------------------------------------------------------------
    DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    GEMINI_BASE = os.environ.get(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")

    # -----------------------------------------------------------------------
    # CORS Configuration (Environment-aware)
    # -----------------------------------------------------------------------
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        """Get CORS origins based on environment"""
        origins = [cls.FRONTEND_URL]
        if cls.ENVIRONMENT == "development":
            origins.extend([
                "http://127.0.0.1:8123",
                "http://localhost:8123",
                "http://127.0.0.1:5000",
                "http://localhost:5000",
            ])
        # Allow custom origins from environment
        custom_origins = os.environ.get("CORS_ORIGINS", "")
        if custom_origins:
            origins.extend([o.strip() for o in custom_origins.split(",") if o.strip()])
        return origins

    # -----------------------------------------------------------------------
    # Validation & Health Check Methods
    # -----------------------------------------------------------------------
    @classmethod
    def has_any_provider(cls) -> bool:
        """Check if at least one AI provider is configured"""
        return any([cls.DEEPSEEK_KEY, cls.GROQ_KEY, cls.GEMINI_KEY, cls.OPENROUTER_KEY])

    @classmethod
    def validate_secrets(cls) -> bool:
        """Validate that secrets are properly set in production"""
        if cls.ENVIRONMENT == "production":
            issues = []
            if not os.environ.get("SECRET_KEY"):
                issues.append("SECRET_KEY must be set via environment variable in production")
            if not os.environ.get("JWT_SECRET"):
                issues.append("JWT_SECRET must be set via environment variable in production")
            if len(cls.SECRET_KEY) < 32:
                issues.append("SECRET_KEY must be at least 32 characters")
            if len(cls.JWT_SECRET) < 32:
                issues.append("JWT_SECRET must be at least 32 characters")
            
            if issues:
                for issue in issues:
                    logger.error(issue)
                return False
        return True

    @classmethod
    def validate_config(cls) -> bool:
        """Validate critical configuration values"""
        valid = True
        
        if cls.MAX_MESSAGE_LENGTH <= 0 or cls.MAX_MESSAGE_LENGTH > 100000:
            logger.error("Invalid MAX_MESSAGE_LENGTH: %s", cls.MAX_MESSAGE_LENGTH)
            valid = False
        
        if cls.REQUEST_TIMEOUT <= 0 or cls.REQUEST_TIMEOUT > 300:
            logger.error("Invalid REQUEST_TIMEOUT: %s", cls.REQUEST_TIMEOUT)
            valid = False
        
        if cls.ENVIRONMENT == "production":
            if not cls.ADMIN_EMAILS:
                logger.warning("No admin emails configured in production")
            if not cls.ERROR_REPORTING_ENABLED:
                logger.warning("Error reporting is disabled in production")
        
        return valid

    @classmethod
    def is_healthy(cls) -> Tuple[bool, List[str]]:
        """Comprehensive health check"""
        issues = []
        
        # Security checks
        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 32:
            issues.append("SECRET_KEY is too short or missing")
        
        if not cls.JWT_SECRET or len(cls.JWT_SECRET) < 32:
            issues.append("JWT_SECRET is too short or missing")
        
        if not cls.has_any_provider():
            issues.append("No AI providers configured")
        
        if cls.ENVIRONMENT == "production" and not cls.SESSION_COOKIE_SECURE:
            issues.append("Session cookies not secure in production")
        
        # Production checks
        if cls.ENVIRONMENT == "production":
            if not cls.ADMIN_EMAILS:
                issues.append("No admin emails configured")
            if not cls.ERROR_REPORTING_ENABLED:
                issues.append("Error reporting disabled in production")
        
        return len(issues) == 0, issues

    @classmethod
    def production_readiness_check(cls) -> Dict[str, bool]:
        """Check if configuration meets production standards"""
        return {
            "secrets_configured": all([
                os.environ.get("SECRET_KEY"),
                os.environ.get("JWT_SECRET")
            ]),
            "secure_cookies": cls.SESSION_COOKIE_SECURE,
            "cors_restricted": len(cls.get_cors_origins()) <= 3,
            "rate_limiting": cls.RATE_LIMIT_MESSAGES > 0,
            "error_reporting": cls.ERROR_REPORTING_ENABLED,
            "monitoring": cls.ENABLE_METRICS,
            "admin_configured": len(cls.ADMIN_EMAILS) > 0,
            "security_headers": True,  # Headers are hardcoded
            "cache_backend": cls.CACHE_BACKEND != "simple" if cls.ENVIRONMENT == "production" else True,
        }

    @classmethod
    def provider_flags(cls) -> Dict[str, bool]:
        return {
            "deepseek": bool(cls.DEEPSEEK_KEY),
            "groq": bool(cls.GROQ_KEY),
            "gemini": bool(cls.GEMINI_KEY),
            "openrouter": bool(cls.OPENROUTER_KEY),
            "google_oauth": bool(cls.GOOGLE_CLIENT_ID and cls.GOOGLE_CLIENT_SECRET),
        }

    @classmethod
    def display(cls) -> None:
        logger.info("%s v%s starting", cls.APP_NAME, cls.VERSION)
        logger.info("Environment=%s Port=%s Frontend=%s", cls.ENVIRONMENT, cls.PORT, cls.FRONTEND_URL)
        logger.info(
            "Providers=%s",
            ", ".join(
                f"{name}:{'on' if enabled else 'off'}"
                for name, enabled in cls.provider_flags().items()
            ),
        )
        
        # Production readiness summary
        if cls.ENVIRONMENT == "production":
            readiness = cls.production_readiness_check()
            failed = [k for k, v in readiness.items() if not v]
            if failed:
                logger.warning("Production readiness issues: %s", ", ".join(failed))
            else:
                logger.info("✅ All production readiness checks passed")# SECTION 1: Configuration and Settings
# EONIX AI Platform - Main Application Configuration

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import jwt
import requests
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("eonix")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class Config:
    APP_NAME = "EONIX"
    VERSION = "5.0"
    API_VERSION = "v1"
    ENVIRONMENT = os.environ.get("FLASK_ENV", "production").lower()
    PORT = int(os.environ.get("PORT", "5000"))

    FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://eonix-7nmk.onrender.com").rstrip("/")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", f"{FRONTEND_URL}/login/callback")

    # -----------------------------------------------------------------------
    # Security - Secrets (MUST be set in production)
    # -----------------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
    JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_urlsafe(48)
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "168"))

    # -----------------------------------------------------------------------
    # Session Security
    # -----------------------------------------------------------------------
    SESSION_COOKIE_SECURE = ENVIRONMENT != "development"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict" if ENVIRONMENT == "production" else "Lax"

    # -----------------------------------------------------------------------
    # Security Headers
    # -----------------------------------------------------------------------
    SECURITY_HEADERS = {
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https:; connect-src 'self' https://api.deepseek.com https://api.groq.com https://generativelanguage.googleapis.com https://openrouter.ai",
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    }

    # -----------------------------------------------------------------------
    # Content Limits (Tuned for production)
    # -----------------------------------------------------------------------
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024)))  # 10MB
    MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", "4000"))  # Reduced for production
    MAX_SESSION_TITLE = int(os.environ.get("MAX_SESSION_TITLE", "120"))
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "18"))
    MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))  # 5MB
    REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", "30"))  # Reduced for production
    IMAGE_REQUEST_TIMEOUT = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT", "60"))

    # -----------------------------------------------------------------------
    # Rate Limiting (Production-tuned with per-endpoint options)
    # -----------------------------------------------------------------------
    RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "20"))  # Reduced
    RATE_LIMIT_SESSIONS = int(os.environ.get("RATE_LIMIT_SESSIONS", "50"))  # Reduced
    RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
    RATE_LIMIT_CLEANUP_INTERVAL = int(os.environ.get("RATE_LIMIT_CLEANUP_INTERVAL", "300"))
    
    # Per-endpoint rate limits
    RATE_LIMIT_PER_ENDPOINT = {
        "chat": {"messages": 20, "window": 60},
        "image_generation": {"messages": 5, "window": 60},
        "auth": {"messages": 10, "window": 300},  # 10 login attempts per 5 minutes
    }

    # -----------------------------------------------------------------------
    # Feature Flags
    # -----------------------------------------------------------------------
    ALLOW_DEV_LOGIN = os.environ.get("ALLOW_DEV_LOGIN", "false").lower() == "true"
    ENABLE_MODEL_BOOT_PROBE = os.environ.get("ENABLE_MODEL_BOOT_PROBE", "false").lower() == "true"
    MODEL_PROBE_TIMEOUT = int(os.environ.get("MODEL_PROBE_TIMEOUT", "12"))

    # -----------------------------------------------------------------------
    # Error Reporting (Enabled by default in production)
    # -----------------------------------------------------------------------
    ERROR_REPORTING_ENABLED = os.environ.get(
        "ERROR_REPORTING_ENABLED", 
        str(ENVIRONMENT == "production").lower()
    ).lower() == "true"
    ERROR_REPORTING_WEBHOOK = os.environ.get("ERROR_REPORTING_WEBHOOK")
    ERROR_REPORTING_SANITIZE = True  # Sanitize sensitive data in error reports
    
    # -----------------------------------------------------------------------
    # Health & Monitoring
    # -----------------------------------------------------------------------
    HEALTH_CHECK_ENDPOINT = os.environ.get("HEALTH_CHECK_ENDPOINT", "/health")
    HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "30"))
    
    # Caching
    CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() == "true"
    CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))
    CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "simple")  # simple, redis, memcached
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    
    # Monitoring
    ENABLE_METRICS = os.environ.get("ENABLE_METRICS", "true").lower() == "true"
    METRICS_PORT = int(os.environ.get("METRICS_PORT", "9090"))
    ENABLE_REQUEST_LOGGING = os.environ.get("ENABLE_REQUEST_LOGGING", "true").lower() == "true"

    # -----------------------------------------------------------------------
    # Admin Configuration (No hardcoded emails)
    # -----------------------------------------------------------------------
    ADMIN_EMAILS = {
        email.strip().lower()
        for email in os.environ.get("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }

    # -----------------------------------------------------------------------
    # Provider API Keys
    # -----------------------------------------------------------------------
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    # -----------------------------------------------------------------------
    # Provider Base URLs
    # -----------------------------------------------------------------------
    DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    GEMINI_BASE = os.environ.get(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")

    # -----------------------------------------------------------------------
    # CORS Configuration (Environment-aware)
    # -----------------------------------------------------------------------
    @classmethod
    def get_cors_origins(cls) -> List[str]:
        """Get CORS origins based on environment"""
        origins = [cls.FRONTEND_URL]
        if cls.ENVIRONMENT == "development":
            origins.extend([
                "http://127.0.0.1:8123",
                "http://localhost:8123",
                "http://127.0.0.1:5000",
                "http://localhost:5000",
            ])
        # Allow custom origins from environment
        custom_origins = os.environ.get("CORS_ORIGINS", "")
        if custom_origins:
            origins.extend([o.strip() for o in custom_origins.split(",") if o.strip()])
        return origins

    # -----------------------------------------------------------------------
    # Validation & Health Check Methods
    # -----------------------------------------------------------------------
    @classmethod
    def has_any_provider(cls) -> bool:
        """Check if at least one AI provider is configured"""
        return any([cls.DEEPSEEK_KEY, cls.GROQ_KEY, cls.GEMINI_KEY, cls.OPENROUTER_KEY])

    @classmethod
    def validate_secrets(cls) -> bool:
        """Validate that secrets are properly set in production"""
        if cls.ENVIRONMENT == "production":
            issues = []
            if not os.environ.get("SECRET_KEY"):
                issues.append("SECRET_KEY must be set via environment variable in production")
            if not os.environ.get("JWT_SECRET"):
                issues.append("JWT_SECRET must be set via environment variable in production")
            if len(cls.SECRET_KEY) < 32:
                issues.append("SECRET_KEY must be at least 32 characters")
            if len(cls.JWT_SECRET) < 32:
                issues.append("JWT_SECRET must be at least 32 characters")
            
            if issues:
                for issue in issues:
                    logger.error(issue)
                return False
        return True

    @classmethod
    def validate_config(cls) -> bool:
        """Validate critical configuration values"""
        valid = True
        
        if cls.MAX_MESSAGE_LENGTH <= 0 or cls.MAX_MESSAGE_LENGTH > 100000:
            logger.error("Invalid MAX_MESSAGE_LENGTH: %s", cls.MAX_MESSAGE_LENGTH)
            valid = False
        
        if cls.REQUEST_TIMEOUT <= 0 or cls.REQUEST_TIMEOUT > 300:
            logger.error("Invalid REQUEST_TIMEOUT: %s", cls.REQUEST_TIMEOUT)
            valid = False
        
        if cls.ENVIRONMENT == "production":
            if not cls.ADMIN_EMAILS:
                logger.warning("No admin emails configured in production")
            if not cls.ERROR_REPORTING_ENABLED:
                logger.warning("Error reporting is disabled in production")
        
        return valid

    @classmethod
    def is_healthy(cls) -> Tuple[bool, List[str]]:
        """Comprehensive health check"""
        issues = []
        
        # Security checks
        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 32:
            issues.append("SECRET_KEY is too short or missing")
        
        if not cls.JWT_SECRET or len(cls.JWT_SECRET) < 32:
            issues.append("JWT_SECRET is too short or missing")
        
        if not cls.has_any_provider():
            issues.append("No AI providers configured")
        
        if cls.ENVIRONMENT == "production" and not cls.SESSION_COOKIE_SECURE:
            issues.append("Session cookies not secure in production")
        
        # Production checks
        if cls.ENVIRONMENT == "production":
            if not cls.ADMIN_EMAILS:
                issues.append("No admin emails configured")
            if not cls.ERROR_REPORTING_ENABLED:
                issues.append("Error reporting disabled in production")
        
        return len(issues) == 0, issues

    @classmethod
    def production_readiness_check(cls) -> Dict[str, bool]:
        """Check if configuration meets production standards"""
        return {
            "secrets_configured": all([
                os.environ.get("SECRET_KEY"),
                os.environ.get("JWT_SECRET")
            ]),
            "secure_cookies": cls.SESSION_COOKIE_SECURE,
            "cors_restricted": len(cls.get_cors_origins()) <= 3,
            "rate_limiting": cls.RATE_LIMIT_MESSAGES > 0,
            "error_reporting": cls.ERROR_REPORTING_ENABLED,
            "monitoring": cls.ENABLE_METRICS,
            "admin_configured": len(cls.ADMIN_EMAILS) > 0,
            "security_headers": True,  # Headers are hardcoded
            "cache_backend": cls.CACHE_BACKEND != "simple" if cls.ENVIRONMENT == "production" else True,
        }

    @classmethod
    def provider_flags(cls) -> Dict[str, bool]:
        return {
            "deepseek": bool(cls.DEEPSEEK_KEY),
            "groq": bool(cls.GROQ_KEY),
            "gemini": bool(cls.GEMINI_KEY),
            "openrouter": bool(cls.OPENROUTER_KEY),
            "google_oauth": bool(cls.GOOGLE_CLIENT_ID and cls.GOOGLE_CLIENT_SECRET),
        }

    @classmethod
    def display(cls) -> None:
        logger.info("%s v%s starting", cls.APP_NAME, cls.VERSION)
        logger.info("Environment=%s Port=%s Frontend=%s", cls.ENVIRONMENT, cls.PORT, cls.FRONTEND_URL)
        logger.info(
            "Providers=%s",
            ", ".join(
                f"{name}:{'on' if enabled else 'off'}"
                for name, enabled in cls.provider_flags().items()
            ),
        )
        
        # Production readiness summary
        if cls.ENVIRONMENT == "production":
            readiness = cls.production_readiness_check()
            failed = [k for k, v in readiness.items() if not v]
            if failed:
                logger.warning("Production readiness issues: %s", ", ".join(failed))
            else:
                logger.info("✅ All production readiness checks passed")

# SECTION 2: Utilities and Security
# Helper functions, security utilities, rate limiting, and content safety

import json
import hashlib
from pathlib import Path
from typing import Optional, Set, Tuple, Dict, Any

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def unix_now() -> float:
    return time.time()

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_id(prefix: str) -> str:
    return f"{re.sub(r'[^a-zA-Z0-9_-]', '', prefix)[:20]}_{uuid.uuid4().hex}"

def safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def get_request_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "0.0.0.0"

def json_body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}

def current_iso_from_ts(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Content Safety (Ban Only - No External Reporting)
# ---------------------------------------------------------------------------
class ContentSafety:
    """Detects dangerous queries and bans users. No external reporting."""
    
    BANS_FILE = Path("banned_users.json")
    BAN_DURATION_DAYS = int(os.environ.get("BAN_DURATION_DAYS", "30"))
    _lock = threading.RLock()
    _banned: Dict[str, Dict[str, float]] = {"emails": {}, "ips": {}, "ids": {}}  # {key: expires_at}
    
    # Dangerous patterns - ban on match
    PATTERNS = [
        re.compile(r'\b(child|minor|underage)\s+(porn|explicit|nude|sexual|abuse)\b', re.I),
        re.compile(r'\b(make|build|create)\s+(bomb|explosive|weapon|detonator)\b', re.I),
        re.compile(r'\b(join|support)\s+(isis|al.qaeda|terrorist)\b', re.I),
        re.compile(r'\b(hire|find)\s+(hitman|assassin|killer)\b', re.I),
        re.compile(r'\b(kill\s+myself|suicide\s+method|end\s+my\s+life)\b', re.I),
    ]
    
    # Educational/protective context - don't ban
    CONTEXT_ALLOW = re.compile(
        r'\b(protect|prevent|report|help|support|counsel|therapy|legal|'
        r'police|fbi|academic|research|study)\b', re.I
    )
    
    @classmethod
    def load(cls) -> None:
        if cls.BANS_FILE.exists():
            try:
                with open(cls.BANS_FILE) as f:
                    data = json.load(f)
                    now = unix_now()
                    # Load only non-expired bans
                    cls._banned = {
                        "emails": {k: v for k, v in data.get("emails", {}).items() if v > now},
                        "ips": {k: v for k, v in data.get("ips", {}).items() if v > now},
                        "ids": {k: v for k, v in data.get("ids", {}).items() if v > now},
                    }
                logger.info("Loaded %d email, %d IP, %d ID bans",
                           len(cls._banned["emails"]), len(cls._banned["ips"]), len(cls._banned["ids"]))
            except Exception as e:
                logger.error("Ban load failed: %s", e)
    
    @classmethod
    def _save(cls) -> None:
        """Atomic save to prevent corruption"""
        tmp = cls.BANS_FILE.with_suffix('.tmp')
        try:
            with cls._lock:
                with open(tmp, 'w') as f:
                    json.dump({k: dict(v) for k, v in cls._banned.items()}, f)
                tmp.replace(cls.BANS_FILE)  # Atomic on Unix
        except Exception as e:
            logger.error("Ban save failed: %s", e)
            if tmp.exists():
                tmp.unlink()
    
    @classmethod
    def is_banned(cls, email: str = "", ip: str = "", uid: str = "") -> bool:
        now = unix_now()
        if email and email.lower() in cls._banned["emails"]:
            if cls._banned["emails"][email.lower()] > now: return True
        if ip and ip in cls._banned["ips"]:
            if cls._banned["ips"][ip] > now: return True
        if uid and uid in cls._banned["ids"]:
            if cls._banned["ids"][uid] > now: return True
        return False
    
    @classmethod
    def ban(cls, email: str, ip: str, uid: str = "", reason: str = "") -> None:
        expires = unix_now() + (cls.BAN_DURATION_DAYS * 86400)
        with cls._lock:
            if email: cls._banned["emails"][email.lower()] = expires
            if ip: cls._banned["ips"][ip] = expires
            if uid: cls._banned["ids"][uid] = expires
        cls._save()
        logger.warning("BANNED %dd | %s | %s | %s | %s", cls.BAN_DURATION_DAYS, email, ip, uid, reason)
    
    @classmethod
    def check(cls, text: str) -> bool:
        """Returns True if content is dangerous"""
        if not text: return False
        if cls.CONTEXT_ALLOW.search(text): return False
        return any(p.search(text) for p in cls.PATTERNS)
    
    @classmethod
    def get_stats(cls) -> Dict[str, int]:
        """Get ban statistics for monitoring"""
        now = unix_now()
        return {
            "active_email_bans": sum(1 for v in cls._banned["emails"].values() if v > now),
            "active_ip_bans": sum(1 for v in cls._banned["ips"].values() if v > now),
            "active_id_bans": sum(1 for v in cls._banned["ids"].values() if v > now),
        }

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
class Security:
    HTML_RE = re.compile(r"<[^>]*>")
    BAD_PROTO = re.compile(r"(?i)\b(javascript|data):", re.I)
    _safety_limiter = None  # Set after rate_limiter created
    
    TASK_PATTERNS = {
        "math": re.compile(r'\b(calculate|solve|equation|math|algebra|calculus)\b', re.I),
        "code": re.compile(r'\b(code|program|function|debug|python|javascript|api|sql)\b', re.I),
        "creative": re.compile(r'\b(write|story|poem|creative|brainstorm|essay)\b', re.I),
        "vision": re.compile(r'\b(analyze|describe|image|picture|photo|what.*in)\b', re.I),
        "forge": re.compile(r'\b(generate|create|make|draw).*\b(image|picture|art)\b', re.I),
        "fact": re.compile(r'\b(fact|history|science|geography|when|who|what|where|why)\b', re.I),
    }

    @staticmethod
    def sanitize(text: str, max_len: int = Config.MAX_MESSAGE_LENGTH) -> str:
        if not text: return ""
        clean = "".join(ch for ch in text.replace("\x00", "") if ch in "\n\t" or ord(ch) >= 32)
        return Security.HTML_RE.sub("", Security.BAD_PROTO.sub("", clean)).strip()[:max_len]

    @staticmethod
    def title(text: str) -> str:
        clean = re.sub(r"\s+", " ", Security.sanitize(text, Config.MAX_SESSION_TITLE)).strip()
        return (clean[:53] + "...") if len(clean) > 53 else (clean or "New Conversation")

    @staticmethod
    def token(n: int = 32) -> str:
        return secrets.token_urlsafe(n)

    @staticmethod
    def is_low_signal(text: str) -> bool:
        clean = text.strip()
        return len(clean) < 2 or bool(re.search(r'(.)\1{10,}', clean))

    @staticmethod
    def detect_task(text: str) -> str:
        scores = {t: len(p.findall(text.lower())) for t, p in Security.TASK_PATTERNS.items()}
        return max(scores, key=scores.get) if max(scores.values()) > 0 else "fact"

    @staticmethod
    def recommend_mode(text: str) -> str:
        return {
            "math": "EONIX-deepcore", "code": "EONIX-oracle", "creative": "EONIX-swift",
            "vision": "EONIX-vision", "forge": "EONIX-forge", "fact": "EONIX-knowledge"
        }.get(Security.detect_task(text), "EONIX-prime")

    @staticmethod
    def safety_check(text: str, email: str = "", uid: str = "") -> Dict[str, Any]:
        """Check content and handle bans. Call before processing any message."""
        ip = get_request_ip()
        
        # Rate limit safety checks
        if Security._safety_limiter:
            allowed, _ = Security._safety_limiter.check(f"safety:{ip}", 20, 60)
            if not allowed:
                return {"safe": False, "banned": False, "msg": "Rate limit exceeded. Try again later."}
        
        # Check if already banned
        if ContentSafety.is_banned(email, ip, uid):
            return {"safe": False, "banned": True, 
                    "msg": "Account suspended for policy violation. Contact support@eonix.ai to appeal."}
        
        # Check content
        if ContentSafety.check(text):
            ContentSafety.ban(email, ip, uid, "Dangerous content detected")
            return {"safe": False, "banned": True, 
                    "msg": "Account suspended for policy violation. Contact support@eonix.ai to appeal."}
        
        return {"safe": True, "banned": False, "msg": ""}

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class SlidingWindowLimiter:
    def __init__(self) -> None:
        self.events: Dict[str, Deque[float]] = defaultdict(deque)
        self.lock = threading.RLock()

    def check(self, key: str, limit: int, window: int) -> Tuple[bool, int]:
        now = unix_now()
        with self.lock:
            bucket = self.events[key]
            while bucket and bucket[0] < now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False, 0
            bucket.append(now)
            return True, max(limit - len(bucket), 0)

rate_limiter = SlidingWindowLimiter()
Security._safety_limiter = SlidingWindowLimiter()  # Set safety limiter

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
class JWTService:
    @staticmethod
    def create(user: 'UserDTO') -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode({
            "user_id": user.id, "email": user.email, "is_admin": user.is_admin,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=Config.JWT_EXPIRY_HOURS)).timestamp()),
        }, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)

    @staticmethod
    def verify(token: str) -> Optional[Dict[str, Any]]:
        if not token: return None
        try:
            data = jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
            return data if isinstance(data, dict) else None
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None

    @staticmethod
    def from_request() -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1].strip()
            if token and len(token.split('.')) == 3:  # Validate JWT format
                return token
        return session.get("session_token") or request.args.get("token")

# Initialize bans on load
ContentSafety.load()


# SECTION 3: DTOs and Data Models
# Data Transfer Objects and storage models

from functools import lru_cache
from typing import Optional, Tuple, List, Dict, Any

# ---------------------------------------------------------------------------
# DTOs with Validation
# ---------------------------------------------------------------------------
@dataclass
class UserDTO:
    id: str
    google_id: Optional[str]
    email: str
    display_name: str
    avatar_url: str = ""
    is_admin: bool = False
    created_at: float = 0.0
    last_login: float = 0.0
    total_logins: int = 1
    session_token: Optional[str] = None

@dataclass
class SessionDTO:
    id: str
    user_id: str
    title: str = "New Conversation"
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0
    
    def can_add_message(self) -> bool:
        return self.message_count < Config.MAX_HISTORY_MESSAGES

@dataclass
class MessageDTO:
    id: str
    session_id: str
    role: str
    content: str
    mode: str = "EONIX-prime"
    created_at: float = 0.0
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    task_type: str = "general_chat"
    validated: bool = False
    
    def __post_init__(self):
        if self.role not in ("user", "assistant", "system"):
            raise ValueError(f"Invalid role: {self.role}")
        if not self.content and not self.attachments:
            raise ValueError("Message must have content or attachments")

@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    public_mode: str
    max_tokens: int = 4096
    temperature: float = 0.7
    thinking: Optional[str] = None
    supports_text: bool = True
    supports_vision: bool = False
    supports_image_generation: bool = False
    task_focus: Optional[str] = None
    fallback_only: bool = False

@dataclass
class ModeSpec:
    id: str
    label: str
    description: str
    chain: Tuple[ModelCandidate, ...]
    icon: str = "🤖"
    requires_auth: bool = True
    rate_limit_multiplier: float = 1.0

# ---------------------------------------------------------------------------
# Model Health Tracker
# ---------------------------------------------------------------------------
class ModelHealthTracker:
    """Tracks model availability with exponential backoff"""
    _health: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "successes": 0, "failures": 0, "last_failure": 0, "cooldown_until": 0
    })
    _lock = threading.RLock()
    
    @classmethod
    def record_success(cls, model: str) -> None:
        with cls._lock:
            cls._health[model]["successes"] += 1
            cls._health[model]["failures"] = 0  # Reset on success
    
    @classmethod
    def record_failure(cls, model: str) -> None:
        now = unix_now()
        with cls._lock:
            stats = cls._health[model]
            stats["failures"] += 1
            stats["last_failure"] = now
            backoff = min(300, 5 * (2 ** min(stats["failures"], 6)))
            stats["cooldown_until"] = now + backoff
            logger.warning("Model %s failed %d times, cooldown %ds", 
                          model, stats["failures"], backoff)
    
    @classmethod
    def is_available(cls, model: str) -> bool:
        return unix_now() > cls._health[model]["cooldown_until"]
    
    @classmethod
    def get_stats(cls) -> Dict[str, Dict[str, Any]]:
        with cls._lock:
            return {k: dict(v) for k, v in cls._health.items()}

# ---------------------------------------------------------------------------
# Model candidate factory
# ---------------------------------------------------------------------------
def c(
    provider: str, model: str, public_mode: str,
    max_tokens: int = 4096, temperature: float = 0.7,
    thinking: Optional[str] = None,
    supports_text: bool = True, supports_vision: bool = False,
    supports_image_generation: bool = False,
    task_focus: Optional[str] = None, fallback_only: bool = False,
) -> ModelCandidate:
    return ModelCandidate(
        provider=provider, model=model, public_mode=public_mode,
        max_tokens=max_tokens, temperature=temperature, thinking=thinking,
        supports_text=supports_text, supports_vision=supports_vision,
        supports_image_generation=supports_image_generation,
        task_focus=task_focus, fallback_only=fallback_only,
    )

# ---------------------------------------------------------------------------
# Mode Specifications
# ---------------------------------------------------------------------------
MODE_SPECS: Dict[str, ModeSpec] = {
    "EONIX-prime": ModeSpec(
        id="EONIX-prime", label="EONIX Prime",
        description="Balanced general-purpose AI for everyday conversations",
        icon="🤖",
        chain=(
            c("deepseek", "deepseek-chat", "EONIX-prime", 4096, 0.65, "high", task_focus="general_chat"),
            c("gemini", "gemini-2.5-pro", "EONIX-prime", 4096, 0.65, task_focus="general_chat"),
            c("openrouter", "openai/gpt-4o", "EONIX-prime", 4096, 0.65, task_focus="general_chat"),
            c("groq", "llama-3.3-70b-versatile", "EONIX-prime", 4096, 0.65, task_focus="general_chat"),
        ),
    ),
    "EONIX-swift": ModeSpec(
        id="EONIX-swift", label="EONIX Swift",
        description="Creative writing and content generation expert",
        icon="✍️",
        chain=(
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-swift", 8192, 0.85, task_focus="creative_writing"),
            c("openrouter", "openai/gpt-4o", "EONIX-swift", 8192, 0.85, task_focus="creative_writing"),
            c("groq", "llama-3.3-70b-versatile", "EONIX-swift", 4096, 0.85, task_focus="creative_writing"),
            c("deepseek", "deepseek-chat", "EONIX-swift", 4096, 0.85, "disabled", task_focus="creative_writing"),
        ),
    ),
    "EONIX-deepcore": ModeSpec(
        id="EONIX-deepcore", label="EONIX DeepCore",
        description="Expert in mathematical reasoning and problem-solving",
        icon="🧮",
        chain=(
            c("deepseek", "deepseek-reasoner", "EONIX-deepcore", 12000, 0.45, "max", task_focus="math_reasoning"),
            c("openrouter", "google/gemini-2.5-pro", "EONIX-deepcore", 12000, 0.45, task_focus="math_reasoning"),
            c("gemini", "gemini-2.5-pro", "EONIX-deepcore", 12000, 0.45, task_focus="math_reasoning"),
            c("openrouter", "openai/gpt-4o", "EONIX-deepcore", 8192, 0.45, task_focus="math_reasoning"),
        ),
    ),
    "EONIX-oracle": ModeSpec(
        id="EONIX-oracle", label="EONIX Oracle",
        description="Expert in programming, algorithms, and software development",
        icon="💻",
        chain=(
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-oracle", 12000, 0.6, task_focus="coding"),
            c("openrouter", "openai/gpt-4o", "EONIX-oracle", 12000, 0.6, task_focus="coding"),
            c("deepseek", "deepseek-chat", "EONIX-oracle", 8192, 0.6, "high", task_focus="coding"),
            c("groq", "llama-3.3-70b-versatile", "EONIX-oracle", 8192, 0.6, task_focus="coding"),
        ),
    ),
    "EONIX-vision": ModeSpec(
        id="EONIX-vision", label="EONIX Vision",
        description="Visual analysis and image understanding expert",
        icon="👁️",
        chain=(
            c("gemini", "gemini-2.5-pro", "EONIX-vision", 8192, 0.4, supports_vision=True, task_focus="image_analysis"),
            c("openrouter", "openai/gpt-4o", "EONIX-vision", 8192, 0.4, supports_vision=True, task_focus="image_analysis"),
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-vision", 8192, 0.4, supports_vision=True, task_focus="image_analysis"),
            c("gemini", "gemini-2.5-flash", "EONIX-vision", 8192, 0.4, supports_vision=True, task_focus="image_analysis"),
        ),
    ),
    "EONIX-forge": ModeSpec(
        id="EONIX-forge", label="EONIX Forge",
        description="Expert in creating images from text descriptions",
        icon="🎨",
        rate_limit_multiplier=0.25,  # Image generation is expensive
        chain=(
            c("gemini-image", "imagen-4.0-ultra-generate-001", "EONIX-forge", supports_text=False, supports_image_generation=True, task_focus="image_generation"),
            c("gemini-image", "imagen-4.0-generate-001", "EONIX-forge", supports_text=False, supports_image_generation=True, task_focus="image_generation"),
            c("gemini-image", "imagen-4.0-fast-generate-001", "EONIX-forge", supports_text=False, supports_image_generation=True, task_focus="image_generation"),
            c("gemini-image", "imagen-3.0-generate-002", "EONIX-forge", supports_text=False, supports_image_generation=True, task_focus="image_generation"),
        ),
    ),
    "EONIX-knowledge": ModeSpec(
        id="EONIX-knowledge", label="EONIX Knowledge",
        description="Expert in general knowledge, facts, and information retrieval",
        icon="📚",
        chain=(
            c("gemini", "gemini-2.5-pro", "EONIX-knowledge", 8192, 0.5, task_focus="fact_checking"),
            c("openrouter", "openai/gpt-4o", "EONIX-knowledge", 8192, 0.5, task_focus="fact_checking"),
            c("deepseek", "deepseek-chat", "EONIX-knowledge", 4096, 0.5, "high", task_focus="fact_checking"),
            c("groq", "llama-3.3-70b-versatile", "EONIX-knowledge", 4096, 0.5, task_focus="fact_checking"),
        ),
    ),
}

# ---------------------------------------------------------------------------
# Mode aliases & helpers
# ---------------------------------------------------------------------------
MODE_ALIASES = {
    "prime": "EONIX-prime", "swift": "EONIX-swift",
    "deepcore": "EONIX-deepcore", "oracle": "EONIX-oracle",
    "vision": "EONIX-vision", "forge": "EONIX-forge",
    "knowledge": "EONIX-knowledge",
    "facts": "EONIX-knowledge", "gk": "EONIX-knowledge",
    "general": "EONIX-prime", "creative": "EONIX-swift",
    "math": "EONIX-deepcore", "coding": "EONIX-oracle",
    "code": "EONIX-oracle", "image-gen": "EONIX-forge",
}

def normalize_mode(value: Optional[str]) -> str:
    if not value: return "EONIX-prime"
    clean = value.strip().lower()
    return clean if clean in MODE_SPECS else MODE_ALIASES.get(clean, "EONIX-prime")

@lru_cache(maxsize=32)
def get_mode_spec(mode: str) -> ModeSpec:
    return MODE_SPECS.get(normalize_mode(mode), MODE_SPECS["EONIX-prime"])

def get_model_chain(mode: str) -> Tuple[ModelCandidate, ...]:
    return get_mode_spec(mode).chain

def get_available_modes() -> List[Dict[str, Any]]:
    return [{
        "id": s.id, "label": s.label, "description": s.description, "icon": s.icon,
    } for s in MODE_SPECS.values()]

def validate_model_chain(mode: str) -> Tuple[bool, List[str]]:
    """Validate provider keys for all models in chain"""
    issues = []
    key_map = {
        "deepseek": Config.DEEPSEEK_KEY, "gemini": Config.GEMINI_KEY,
        "groq": Config.GROQ_KEY, "openrouter": Config.OPENROUTER_KEY,
    }
    for i, c in enumerate(get_model_chain(mode)):
        if c.provider in key_map and not key_map[c.provider]:
            issues.append(f"Chain[{i}]: {c.provider} not configured")
    return len(issues) == 0, issues

def execute_with_fallback(mode: str, messages: List[Dict], **kwargs):
    """Execute with health-aware fallback (for Section 4)"""
    last_error = None
    for candidate in get_model_chain(mode):
        if not ModelHealthTracker.is_available(candidate.model):
            continue
        try:
            result = call_provider(candidate, messages, **kwargs)  # Defined in Section 4
            ModelHealthTracker.record_success(candidate.model)
            return result
        except Exception as e:
            logger.warning("Model %s failed: %s", candidate.model, str(e))
            ModelHealthTracker.record_failure(candidate.model)
            last_error = e
    raise RuntimeError(f"All models exhausted for {mode}: {last_error}")

# ===========================================================================
# EONIX AI Platform - Complete Integration
# Sections 1-4 Working Together
# ===========================================================================

# ---------------------------------------------------------------------------
# SECTION 1: Configuration (Config, ModelSpecialization)
# ---------------------------------------------------------------------------
# ✅ All configuration centralized
# ✅ Environment-based settings
# ✅ Production-ready defaults

# ---------------------------------------------------------------------------
# SECTION 2: Security & Safety (Security, ContentSafety, RateLimiter, JWT)
# ---------------------------------------------------------------------------
# ✅ Content safety with context awareness
# ✅ Ban system with file persistence
# ✅ Rate limiting per endpoint
# ✅ JWT with proper validation

# ---------------------------------------------------------------------------
# SECTION 3: Data Models (DTOs, ModeSpec, ModelHealthTracker)
# ---------------------------------------------------------------------------
# ✅ Immutable model candidates
# ✅ Intelligent fallback chains
# ✅ Health tracking with backoff
# ✅ Cached mode specifications

# ---------------------------------------------------------------------------
# SECTION 4: Diagnostics (ModelDiagnostics, Health Endpoints)
# ---------------------------------------------------------------------------
# ✅ Real model pinging
# ✅ Performance metrics
# ✅ Health endpoints for monitoring
# ✅ Alert system

# ===========================================================================
# STARTUP SEQUENCE
# ===========================================================================

def create_app() -> Flask:
    """Factory function to create and configure the Flask app"""
    app = Flask(__name__)
    
    # 1. Load configuration
    Config.display()
    Config.validate_config()
    Config.validate_secrets()
    
    # 2. Initialize content safety
    ContentSafety.load()
    logger.info("Content safety system loaded: %d banned", 
                ContentSafety.get_stats()["active_email_bans"])
    
    # 3. Register health endpoints
    register_health_endpoint(app)
    
    # 4. Run boot probe (optional)
    if Config.ENABLE_MODEL_BOOT_PROBE:
        with app.app_context():
            probe = model_diagnostics.run_boot_probe()
            failed = [k for k, v in probe.get("results", {}).items() if not v.get("ok")]
            if failed:
                logger.warning("Boot probe failures: %s", ", ".join(failed))
    
    # 5. Production readiness check
    if Config.ENVIRONMENT == "production":
        readiness = Config.production_readiness_check()
        failed = [k for k, v in readiness.items() if not v]
        if failed:
            logger.error("Production readiness FAILED: %s", ", ".join(failed))
        else:
            logger.info("✅ All production checks passed")
    
    return app

# ===========================================================================
# REQUEST FLOW (Example for Section 5+)
# ===========================================================================

def handle_chat_request(user_message: str, mode: str, user: UserDTO):
    """Complete request flow using all sections"""
    
    # SECTION 2: Check content safety FIRST
    safety = Security.safety_check(user_message, user.email, user.id)
    if not safety["safe"]:
        return {"error": safety["msg"], "status": 403}
    
    # SECTION 2: Rate limiting
    ip = get_request_ip()
    allowed, remaining = rate_limiter.check_endpoint("chat", ip, user.id)
    if not allowed:
        return {"error": "Rate limit exceeded", "status": 429}
    
    # SECTION 3: Validate mode for task
    detected_task = Security.detect_task(user_message)
    spec = get_mode_spec(mode)
    
    if detected_task in ModelSpecialization.get_specialization(mode).get("forbidden_tasks", []):
        recommended = Security.recommend_mode(user_message)
        model_diagnostics.record_task_validation_failure(mode, detected_task)
        return {
            "warning": f"This task is better suited for {recommended}",
            "recommended_mode": recommended,
        }
    
    # SECTION 3 & 4: Execute with fallback
    chain = get_model_chain(mode)
    
    for candidate in chain:
        # Check health
        if not ModelHealthTracker.is_available(candidate.model):
            continue
        
        # Check deprecated
        if model_diagnostics.deprecated_reason(candidate.provider, candidate.model):
            continue
        
        try:
            start = time.time()
            result = call_provider(candidate, user_message)  # Section 5
            
            # Record success
            duration = (time.time() - start) * 1000
            model_diagnostics.record_success(candidate.provider, candidate.model)
            model_diagnostics.record_response_time(candidate.model, duration)
            
            return {"result": result, "model": candidate.model}
            
        except Exception as e:
            model_diagnostics.record_failure(candidate.provider, candidate.model, e)
            continue
    
    # All models failed
    return {"error": "All models temporarily unavailable", "status": 503}

# ===========================================================================
# MONITORING DASHBOARD (Quick View)
# ===========================================================================

def get_dashboard_data():
    """Aggregated data for monitoring dashboard"""
    return {
        "system": {
            "app": Config.APP_NAME,
            "version": Config.VERSION,
            "environment": Config.ENVIRONMENT,
        },
        "health": model_diagnostics.get_health_status(),
        "performance": model_diagnostics.get_performance_metrics(),
        "alerts": model_diagnostics.check_alerts(),
        "safety": ContentSafety.get_stats(),
        "rate_limits": rate_limiter.get_stats(),
        "models": model_diagnostics.all_modes_report(),
    }

# SECTION 5: Storage Layer
# In-memory storage with thread safety, JSON persistence, and Redis support

import json
from pathlib import Path
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------------
# Base Storage Interface
# ---------------------------------------------------------------------------
class StorageInterface:
    """Abstract interface for storage backends"""
    
    def create_or_update_user(self, google_id: str, email: str, name: str, avatar: str = "") -> UserDTO:
        raise NotImplementedError
    
    def get_user(self, user_id: str) -> Optional[UserDTO]:
        raise NotImplementedError
    
    def create_session(self, user_id: str, title: str = "New Conversation") -> SessionDTO:
        raise NotImplementedError
    
    def get_session(self, session_id: str, user_id: str) -> Optional[SessionDTO]:
        raise NotImplementedError
    
    def add_message(self, session_id: str, role: str, content: str, 
                    mode: str = "EONIX-prime") -> MessageDTO:
        raise NotImplementedError
    
    def get_recent_messages(self, session_id: str, limit: int = None) -> List[MessageDTO]:
        raise NotImplementedError
    
    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        raise NotImplementedError

# ---------------------------------------------------------------------------
# In-Memory Storage (with JSON persistence)
# ---------------------------------------------------------------------------
class MemoryStorage(StorageInterface):
    """Thread-safe in-memory storage with optional JSON persistence"""
    
    def __init__(self, persist: bool = True) -> None:
        self.users: Dict[str, UserDTO] = {}
        self.users_by_email: Dict[str, str] = {}
        self.users_by_google: Dict[str, str] = {}
        self.sessions: Dict[str, SessionDTO] = {}
        self.messages: Dict[str, List[MessageDTO]] = defaultdict(list)
        self.user_sessions: Dict[str, List[str]] = defaultdict(list)
        self.lock = threading.RLock()
        self._stats = {"total_messages": 0, "total_sessions": 0}
        
        # JSON persistence
        self.persist = persist
        self.data_dir = Path("data")
        if persist:
            self.data_dir.mkdir(exist_ok=True)
            self._load_from_disk()
            self._start_auto_save()
    
    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------
    def _load_from_disk(self) -> None:
        """Load data from JSON files"""
        files = {
            "users": (self.users, UserDTO),
            "sessions": (self.sessions, SessionDTO),
        }
        
        for name, (storage_dict, _) in files.items():
            filepath = self.data_dir / f"{name}.json"
            if filepath.exists():
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    # Rebuild dictionaries (objects are recreated on load)
                    if name == "users":
                        for u in data.values():
                            user = UserDTO(**u) if isinstance(u, dict) else u
                            self.users[user.id] = user
                            self.users_by_email[user.email] = user.id
                            if user.google_id:
                                self.users_by_google[user.google_id] = user.id
                    elif name == "sessions":
                        for s in data.values():
                            session = SessionDTO(**s) if isinstance(s, dict) else s
                            self.sessions[session.id] = session
                            self.user_sessions[session.user_id].append(session.id)
                    
                    logger.info("Loaded %d %s from disk", len(storage_dict), name)
                except Exception as e:
                    logger.error("Failed to load %s: %s", name, e)
        
        # Load messages
        msg_file = self.data_dir / "messages.json"
        if msg_file.exists():
            try:
                with open(msg_file) as f:
                    data = json.load(f)
                for session_id, msgs in data.items():
                    self.messages[session_id] = [
                        MessageDTO(**m) if isinstance(m, dict) else m 
                        for m in msgs
                    ]
                logger.info("Loaded messages for %d sessions", len(self.messages))
            except Exception as e:
                logger.error("Failed to load messages: %s", e)
    
    def _save_to_disk(self) -> None:
        """Save all data to JSON files"""
        if not self.persist:
            return
        
        with self.lock:
            try:
                # Save users
                with open(self.data_dir / "users.json", 'w') as f:
                    json.dump({k: asdict(v) for k, v in self.users.items()}, f, indent=2)
                
                # Save sessions
                with open(self.data_dir / "sessions.json", 'w') as f:
                    json.dump({k: asdict(v) for k, v in self.sessions.items()}, f, indent=2)
                
                # Save messages
                with open(self.data_dir / "messages.json", 'w') as f:
                    json.dump(
                        {k: [asdict(m) for m in v] for k, v in self.messages.items()}, 
                        f, indent=2
                    )
            except Exception as e:
                logger.error("Failed to save data: %s", e)
    
    def _start_auto_save(self) -> None:
        """Auto-save every 5 minutes"""
        def auto_save():
            while True:
                time.sleep(300)  # 5 minutes
                self._save_to_disk()
                logger.debug("Auto-saved storage data")
        
        threading.Thread(target=auto_save, daemon=True).start()
    
    # -----------------------------------------------------------------------
    # User operations
    # -----------------------------------------------------------------------
    def create_or_update_user(
        self, google_id: Optional[str], email: str, name: str, avatar: str = "",
    ) -> UserDTO:
        clean_email = (email or "").lower().strip()
        if not clean_email:
            raise ValueError("Email is required")

        with self.lock:
            # Find existing user
            user_id = self.users_by_email.get(clean_email)
            if not user_id and google_id:
                user_id = self.users_by_google.get(google_id)

            if user_id and user_id in self.users:
                user = self.users[user_id]
                user.google_id = google_id or user.google_id
                user.display_name = name or user.display_name or clean_email.split("@")[0]
                user.avatar_url = avatar or user.avatar_url
                user.last_login = unix_now()
                user.total_logins += 1
                if google_id:
                    self.users_by_google[google_id] = user.id
                return user

            # Create new user
            user = UserDTO(
                id=new_id("usr"), google_id=google_id, email=clean_email,
                display_name=name or clean_email.split("@")[0],
                avatar_url=avatar or "",
                is_admin=clean_email in Config.ADMIN_EMAILS,
                created_at=unix_now(), last_login=unix_now(),
            )
            self.users[user.id] = user
            self.users_by_email[clean_email] = user.id
            if google_id:
                self.users_by_google[google_id] = user.id
            
            logger.info("New user: %s (%s)", clean_email, user.id)
            return user

    def get_user(self, user_id: str) -> Optional[UserDTO]:
        return self.users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[UserDTO]:
        user_id = self.users_by_email.get(email.lower().strip())
        return self.users.get(user_id) if user_id else None

    def get_user_by_google_id(self, google_id: str) -> Optional[UserDTO]:
        user_id = self.users_by_google.get(google_id)
        return self.users.get(user_id) if user_id else None

    def set_session_token(self, user_id: str, token: Optional[str]) -> None:
        with self.lock:
            if user_id in self.users:
                self.users[user_id].session_token = token

    def is_admin(self, user_id: str) -> bool:
        user = self.get_user(user_id)
        return user.is_admin if user else False

    # -----------------------------------------------------------------------
    # Session operations
    # -----------------------------------------------------------------------
    def create_session(self, user_id: str, title: str = "New Conversation") -> SessionDTO:
        now = unix_now()
        with self.lock:
            # Enforce session limit
            user_sessions = self.user_sessions.get(user_id, [])
            if len(user_sessions) >= Config.RATE_LIMIT_SESSIONS:
                self._force_delete_session(user_sessions[0], user_id)
            
            session = SessionDTO(
                id=new_id("ses"), user_id=user_id,
                title=Security.title(title),
                created_at=now, updated_at=now,
            )
            self.sessions[session.id] = session
            self.user_sessions[user_id].append(session.id)
            self._stats["total_sessions"] += 1
            return session

    def get_session(self, session_id: str, user_id: str) -> Optional[SessionDTO]:
        session = self.sessions.get(session_id)
        return session if session and session.user_id == user_id else None

    def get_user_sessions(self, user_id: str) -> List[SessionDTO]:
        with self.lock:
            session_ids = list(self.user_sessions.get(user_id, []))
            sessions = [self.sessions[sid] for sid in session_ids if sid in self.sessions]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def delete_session(self, session_id: str, user_id: str) -> bool:
        with self.lock:
            return self._force_delete_session(session_id, user_id)

    def _force_delete_session(self, session_id: str, user_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or session.user_id != user_id:
            return False
        
        self.sessions.pop(session_id, None)
        self.messages.pop(session_id, None)
        if session_id in self.user_sessions.get(user_id, []):
            self.user_sessions[user_id].remove(session_id)
        self._stats["total_sessions"] = max(0, self._stats["total_sessions"] - 1)
        return True

    def update_title(self, session_id: str, title: str) -> bool:
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                session.title = Security.title(title)
                session.updated_at = unix_now()
                return True
            return False

    # -----------------------------------------------------------------------
    # Message operations
    # -----------------------------------------------------------------------
    def add_message(
        self, session_id: str, role: str, content: str,
        mode: str = "EONIX-prime",
        attachments: Optional[List[Dict[str, Any]]] = None,
        task_type: str = "general_chat", validated: bool = True,
    ) -> MessageDTO:
        with self.lock:
            if session_id not in self.sessions:
                raise KeyError(f"Session not found: {session_id}")
            
            session = self.sessions[session_id]
            
            # Auto-remove oldest message if at limit
            msgs = self.messages[session_id]
            if len(msgs) >= Config.MAX_HISTORY_MESSAGES:
                msgs.pop(0)
            
            message = MessageDTO(
                id=new_id("msg"), session_id=session_id, role=role,
                content=Security.sanitize(content),
                mode=normalize_mode(mode), created_at=unix_now(),
                attachments=list(attachments or []),
                task_type=task_type, validated=validated,
            )
            msgs.append(message)
            session.updated_at = unix_now()
            session.message_count += 1
            self._stats["total_messages"] += 1
            return message

    def get_messages(self, session_id: str, limit: int = None) -> List[MessageDTO]:
        messages = self.messages.get(session_id, [])
        messages = sorted(messages, key=lambda m: m.created_at)
        return messages[-limit:] if limit else messages

    def get_recent_messages(self, session_id: str) -> List[MessageDTO]:
        """Get recent messages for AI context"""
        return self.get_messages(session_id, Config.MAX_HISTORY_MESSAGES)

    def count_messages(self, session_id: str) -> int:
        return len(self.messages.get(session_id, []))

    # -----------------------------------------------------------------------
    # Maintenance
    # -----------------------------------------------------------------------
    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        cutoff = unix_now() - (max_age_days * 86400)
        deleted = 0
        
        with self.lock:
            stale = [
                (sid, s.user_id) for sid, s in self.sessions.items()
                if s.updated_at < cutoff
            ]
            for sid, uid in stale:
                if self._force_delete_session(sid, uid):
                    deleted += 1
        
        if deleted:
            logger.info("Cleaned up %d old sessions", deleted)
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "total_users": len(self.users),
                "total_sessions": len(self.sessions),
                "total_messages": self._stats["total_messages"],
                "active_sessions_24h": sum(
                    1 for s in self.sessions.values()
                    if unix_now() - s.updated_at < 86400
                ),
            }

# ---------------------------------------------------------------------------
# Redis Storage (for production scaling)
# ---------------------------------------------------------------------------
class RedisStorage(StorageInterface):
    """Redis-backed storage for horizontal scaling"""
    
    def __init__(self, redis_url: str = None) -> None:
        import redis
        self.redis = redis.from_url(redis_url or Config.REDIS_URL)
        self.ttl = Config.CACHE_TTL
    
    def create_or_update_user(self, google_id: str, email: str, name: str, avatar: str = "") -> UserDTO:
        # Simplified - use Redis hashes
        user_id = self.redis.hget("users_by_email", email.lower())
        if not user_id and google_id:
            user_id = self.redis.hget("users_by_google", google_id)
        
        if user_id:
            user_id = user_id.decode() if isinstance(user_id, bytes) else user_id
            user_data = self.redis.hgetall(f"user:{user_id}")
            # Update user...
            return UserDTO(**{k.decode(): v.decode() for k, v in user_data.items()})
        
        user_id = new_id("usr")
        user = UserDTO(id=user_id, google_id=google_id, email=email, 
                      display_name=name, avatar_url=avatar,
                      is_admin=email.lower() in Config.ADMIN_EMAILS,
                      created_at=unix_now(), last_login=unix_now())
        
        self.redis.hset(f"user:{user_id}", mapping=asdict(user))
        self.redis.hset("users_by_email", email.lower(), user_id)
        if google_id:
            self.redis.hset("users_by_google", google_id, user_id)
        
        return user
    
    def get_user(self, user_id: str) -> Optional[UserDTO]:
        data = self.redis.hgetall(f"user:{user_id}")
        if data:
            return UserDTO(**{k.decode(): v.decode() for k, v in data.items()})
        return None
    
    def create_session(self, user_id: str, title: str = "New Conversation") -> SessionDTO:
        session_id = new_id("ses")
        session = SessionDTO(id=session_id, user_id=user_id,
                            title=Security.title(title),
                            created_at=unix_now(), updated_at=unix_now())
        
        self.redis.hset(f"session:{session_id}", mapping=asdict(session))
        self.redis.rpush(f"user_sessions:{user_id}", session_id)
        self.redis.expire(f"session:{session_id}", self.ttl)
        
        return session
    
    def add_message(self, session_id: str, role: str, content: str,
                    mode: str = "EONIX-prime") -> MessageDTO:
        msg = MessageDTO(id=new_id("msg"), session_id=session_id, role=role,
                        content=Security.sanitize(content), mode=normalize_mode(mode),
                        created_at=unix_now(), task_type=Security.detect_task(content),
                        validated=True)
        
        self.redis.rpush(f"messages:{session_id}", json.dumps(asdict(msg)))
        self.redis.ltrim(f"messages:{session_id}", -Config.MAX_HISTORY_MESSAGES, -1)
        self.redis.expire(f"messages:{session_id}", self.ttl)
        
        return msg
    
    def get_recent_messages(self, session_id: str, limit: int = None) -> List[MessageDTO]:
        limit = limit or Config.MAX_HISTORY_MESSAGES
        raw = self.redis.lrange(f"messages:{session_id}", -limit, -1)
        return [MessageDTO(**json.loads(m)) for m in raw]
    
    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        # Redis handles expiry automatically via TTL
        return 0
    
    def get_session(self, session_id: str, user_id: str) -> Optional[SessionDTO]:
        data = self.redis.hgetall(f"session:{session_id}")
        if data:
            session = SessionDTO(**{k.decode(): v.decode() for k, v in data.items()})
            return session if session.user_id == user_id else None
        return None


# ---------------------------------------------------------------------------
# Storage factory
# ---------------------------------------------------------------------------
def create_storage(backend: str = None) -> StorageInterface:
    """Factory to create appropriate storage backend"""
    backend = backend or os.environ.get("STORAGE_BACKEND", "memory")
    
    if backend == "redis" and Config.REDIS_URL:
        logger.info("Using Redis storage backend")
        return RedisStorage()
    
    logger.info("Using in-memory storage backend (with JSON persistence)")
    return MemoryStorage(persist=Config.ENVIRONMENT != "testing")


# Global storage instance
storage = create_storage()

# SECTION 6: AI Service Core
# Core AI service with specialized task routing, circuit breaker, and retry logic

import requests
from requests.adapters import HTTPAdapter

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ProviderUnavailable(Exception):
    pass

class TaskNotAllowedError(Exception):
    pass

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """Prevent cascading failures with circuit breaker pattern"""
    
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        self.state = "closed"  # closed → open → half-open → closed
    
    @property
    def is_open(self) -> bool:
        if self.state == "open":
            if unix_now() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
                logger.info("Circuit breaker: open → half-open")
                return False
            return True
        return False
    
    def success(self) -> None:
        if self.state == "half-open":
            self.state = "closed"
            self.failure_count = 0
            logger.info("Circuit breaker: half-open → closed")
    
    def failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = unix_now()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning("Circuit breaker: closed → open (%d failures)", self.failure_count)

# ---------------------------------------------------------------------------
# AI Service
# ---------------------------------------------------------------------------
class AIService:
    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        # Circuit breakers per model
        self._breakers: Dict[str, CircuitBreaker] = defaultdict(
            lambda: CircuitBreaker(failure_threshold=5, reset_timeout=60)
        )

    # -----------------------------------------------------------------------
    # Main generate method
    # -----------------------------------------------------------------------
    def generate(
        self, prompt: str, preferred_mode: Optional[str] = None,
        history: Iterable[MessageDTO] = (), auto_detect_task: bool = True,
        enforce_task_restrictions: bool = True,
    ) -> Tuple[str, str, float, str]:
        """
        Generate AI response with specialized task routing.
        Returns: (response_text, mode_used, elapsed_time, task_type)
        """
        request_id = new_id("req")
        started = unix_now()
        clean_prompt = Security.sanitize(prompt)
        
        logger.info("🔍 [%s] Request: mode=%s len=%d", request_id, preferred_mode, len(clean_prompt))
        
        # Quick rejection
        if Security.is_low_signal(clean_prompt):
            return "Please send a clearer message so I can help properly.", "EONIX-prime", 0.0, "general_chat"

        # Task detection & mode resolution
        task_type = Security.detect_task_type(clean_prompt)
        mode_id = self._resolve_mode(clean_prompt, preferred_mode, task_type, 
                                     auto_detect_task, enforce_task_restrictions)
        
        # Build messages
        system_prompt = ModelSpecialization.get_system_prompt(mode_id)
        messages = self._build_messages(clean_prompt, history, system_prompt, task_type)
        chain = get_model_chain(mode_id)
        
        # Try chain with retry & circuit breaker
        result = self._try_chain(chain, messages, mode_id, task_type, request_id)
        
        if result:
            text, provider, model = result
            elapsed = round(unix_now() - started, 2)
            model_diagnostics.record_success(provider, model)
            model_diagnostics.record_response_time(model, elapsed * 1000)
            logger.info("✅ [%s] %s/%s | %s/%s | %.2fs", 
                       request_id, mode_id, task_type, provider, model, elapsed)
            return text, mode_id, elapsed, task_type
        
        # All failed
        elapsed = round(unix_now() - started, 2)
        logger.error("❌ [%s] All candidates failed for %s/%s", request_id, mode_id, task_type)
        return self._offline_reply(clean_prompt, task_type), mode_id, elapsed, task_type

    # -----------------------------------------------------------------------
    # Mode resolution
    # -----------------------------------------------------------------------
    def _resolve_mode(self, prompt: str, preferred_mode: Optional[str], 
                      task_type: str, auto_detect: bool, enforce: bool) -> str:
        mode_id = normalize_mode(preferred_mode)
        
        if auto_detect and not preferred_mode:
            mode_id = Security.get_recommended_mode(prompt)
        
        if enforce:
            is_valid, msg, recommended = Security.validate_task_for_mode(mode_id, prompt)
            if not is_valid and recommended:
                model_diagnostics.record_task_validation_failure(mode_id, task_type)
                logger.info("🔄 Switching %s → %s: %s", mode_id, recommended, msg)
                mode_id = recommended
        
        return mode_id

    # -----------------------------------------------------------------------
    # Chain execution with circuit breaker & retry
    # -----------------------------------------------------------------------
    def _try_chain(self, chain: Tuple[ModelCandidate, ...], messages: List[Dict],
                   mode_id: str, task_type: str, request_id: str) -> Optional[Tuple[str, str, str]]:
        
        for candidate in chain:
            breaker = self._breakers[candidate.model]
            
            # Skip if circuit breaker is open
            if breaker.is_open:
                logger.debug("[%s] Circuit open for %s", request_id, candidate.model)
                continue
            
            # Skip unhealthy/deprecated/unconfigured
            if not ModelHealthTracker.is_available(candidate.model):
                continue
            if not model_diagnostics.provider_configured(candidate.provider):
                continue
            if model_diagnostics.deprecated_reason(candidate.provider, candidate.model):
                continue
            if candidate.fallback_only and candidate.task_focus != task_type:
                continue
            
            # Try with retry logic
            text = self._try_with_retry(candidate, messages, request_id)
            
            if text:
                breaker.success()
                return text, candidate.provider, candidate.model
            else:
                breaker.failure()
                model_diagnostics.record_failure(
                    candidate.provider, candidate.model, "All retries exhausted"
                )
        
        return None
    
    def _try_with_retry(self, candidate: ModelCandidate, messages: List[Dict],
                        request_id: str, max_retries: int = 2) -> Optional[str]:
        """Try provider with exponential backoff retry"""
        adjusted = self._adjust_candidate(candidate, 
                         Security.detect_task_type(messages[-1]["content"]))
        
        for attempt in range(max_retries + 1):
            try:
                return self._dispatch(adjusted, messages)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < max_retries:
                    wait = (attempt + 1) * 2  # 2s, 4s
                    logger.warning("[%s] Retry %d/%d for %s in %ds: %s",
                                 request_id, attempt + 1, max_retries, 
                                 candidate.model, wait, str(e)[:50])
                    time.sleep(wait)
                else:
                    raise
            except Exception:
                raise  # Non-retryable errors
        
        return None

    # -----------------------------------------------------------------------
    # Candidate adjustment
    # -----------------------------------------------------------------------
    def _adjust_candidate(self, c: ModelCandidate, task_type: str) -> ModelCandidate:
        adjustments = {
            "math_reasoning": {"temperature": 0.3, "max_tokens": min(c.max_tokens + 2000, 16000)},
            "coding": {"temperature": 0.5, "max_tokens": min(c.max_tokens + 4000, 16000)},
            "creative_writing": {"temperature": 0.85},
            "fact_checking": {"temperature": 0.4},
        }
        
        if task_type in adjustments:
            adj = adjustments[task_type]
            return ModelCandidate(
                provider=c.provider, model=c.model, public_mode=c.public_mode,
                max_tokens=adj.get("max_tokens", c.max_tokens),
                temperature=adj.get("temperature", c.temperature),
                thinking=c.thinking, supports_text=c.supports_text,
                supports_vision=c.supports_vision,
                supports_image_generation=c.supports_image_generation,
                task_focus=c.task_focus, fallback_only=c.fallback_only,
            )
        return c

    # -----------------------------------------------------------------------
    # Message building
    # -----------------------------------------------------------------------
    def _build_messages(self, prompt: str, history: Iterable[MessageDTO],
                        system_prompt: str, task_type: str) -> List[Dict[str, str]]:
        task_instructions = {
            "math_reasoning": " Show your work step by step.",
            "coding": " Write clean, working code with explanations.",
            "creative_writing": " Be imaginative and engaging.",
            "fact_checking": " Cite sources when possible.",
        }
        
        full_system = system_prompt + task_instructions.get(task_type, "")
        messages = [{"role": "system", "content": full_system}]
        
        recent = [m for m in history if m.role in ("user", "assistant")][-Config.MAX_HISTORY_MESSAGES:]
        for m in recent:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": prompt})
        return messages

    # -----------------------------------------------------------------------
    # Provider dispatch
    # -----------------------------------------------------------------------
    def _dispatch(self, candidate: ModelCandidate, messages: List[Dict]) -> Optional[str]:
        if not candidate.supports_text:
            return None
        
        handlers = {
            "deepseek": lambda: self._openai_compatible(
                Config.DEEPSEEK_BASE, Config.DEEPSEEK_KEY, candidate, messages, deepseek=True
            ),
            "groq": lambda: self._openai_compatible(
                Config.GROQ_BASE, Config.GROQ_KEY, candidate, messages
            ),
            "openrouter": lambda: self._openai_compatible(
                Config.OPENROUTER_BASE, Config.OPENROUTER_KEY, candidate, messages,
                extra_headers={"HTTP-Referer": Config.FRONTEND_URL, "X-Title": Config.APP_NAME}
            ),
            "gemini": lambda: self._gemini_text(candidate, messages),
        }
        
        handler = handlers.get(candidate.provider)
        return handler() if handler else None

    # -----------------------------------------------------------------------
    # OpenAI-compatible providers
    # -----------------------------------------------------------------------
    def _openai_compatible(
        self, base_url: str, api_key: Optional[str], candidate: ModelCandidate,
        messages: List[Dict], extra_headers: Dict = None, deepseek: bool = False,
    ) -> Optional[str]:
        if not api_key:
            raise ProviderUnavailable(f"Neural Key missing")
        
        payload = {
            "model": candidate.model, "messages": messages,
            "max_tokens": candidate.max_tokens, "temperature": candidate.temperature,
        }
        
        if deepseek and candidate.thinking:
            payload["thinking"] = {"type": "disabled" if candidate.thinking == "disabled" else "enabled"}
            if candidate.thinking in ("high", "max"):
                payload["reasoning_effort"] = candidate.thinking
        
        response = self.session.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **(extra_headers or {}),
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        
        if response.status_code >= 400:
            raise RuntimeError(self._extract_error(response))
        
        data = safe_json(response)
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        
        if not content:
            raise RuntimeError("Empty response")
        
        # Track tokens
        usage = data.get("usage", {})
        if usage:
            model_diagnostics.record_token_usage(candidate.model, usage.get("total_tokens", 0))
        
        return content

    # -----------------------------------------------------------------------
    # Gemini provider
    # -----------------------------------------------------------------------
    def _gemini_text(self, candidate: ModelCandidate, messages: List[Dict]) -> Optional[str]:
        if not Config.GEMINI_KEY:
            raise ProviderUnavailable("Model Neural missing")
        
        system_text = ""
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
                continue
            contents.append({
                "role": "model" if msg["role"] == "assistant" else "user",
                "parts": [{"text": msg["content"]}],
            })
        
        response = self.session.post(
            f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent",
            params={"key": Config.GEMINI_KEY},
            json={
                "systemInstruction": {"parts": [{"text": system_text}]},
                "contents": contents,
                "generationConfig": {
                    "temperature": candidate.temperature,
                    "maxOutputTokens": candidate.max_tokens,
                },
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        
        if response.status_code >= 400:
            raise RuntimeError(self._extract_error(response))
        
        data = safe_json(response)
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
        
        if not text:
            raise RuntimeError("Empty response")
        
        usage = data.get("usageMetadata", {})
        if usage:
            model_diagnostics.record_token_usage(candidate.model, usage.get("totalTokenCount", 0))
        
        return text

    # -----------------------------------------------------------------------
    # Error handling
    # -----------------------------------------------------------------------
    @staticmethod
    def _extract_error(response: requests.Response) -> str:
        try:
            data = response.json()
            error = data.get("error", {})
            return (error.get("message") if isinstance(error, dict) else str(error))[:500]
        except Exception:
            return response.text[:500] or f"HTTP {response.status_code}"

    # -----------------------------------------------------------------------
    # Offline fallback
    # -----------------------------------------------------------------------
    @staticmethod
    def _offline_reply(prompt: str, task_type: str = "general_chat") -> str:
        replies = {
            "math_reasoning": "EONIX DeepCore is temporarily unavailable. Please try again.",
            "coding": "EONIX Oracle is temporarily unavailable. Please try again.",
            "creative_writing": "EONIX Swift is temporarily unavailable. Please try again.",
            "image_analysis": "EONIX Vision is temporarily unavailable. Please try again.",
            "image_generation": "EONIX Forge is temporarily unavailable. Please try again.",
            "fact_checking": "EONIX Knowledge is temporarily unavailable. Please try again.",
        }
        if task_type in replies:
            return replies[task_type]
        
        return "EONIX is temporarily unavailable. Please try again in a moment."


# Global instance
ai_service = AIService()

# SECTION 7: Vision and Image Services
# Extends AIService with image analysis and generation

# ---------------------------------------------------------------------------
# Complete AIService (Sections 6 + 7 combined)
# ---------------------------------------------------------------------------
class AIService:
    def __init__(self) -> None:
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._breakers: Dict[str, CircuitBreaker] = defaultdict(
            lambda: CircuitBreaker(failure_threshold=5, reset_timeout=60)
        )

    # =======================================================================
    # TEXT GENERATION (from Section 6)
    # =======================================================================
    def generate(
        self, prompt: str, preferred_mode: Optional[str] = None,
        history: Iterable[MessageDTO] = (), auto_detect_task: bool = True,
        enforce_task_restrictions: bool = True,
    ) -> Tuple[str, str, float, str]:
        request_id = new_id("req")
        started = unix_now()
        clean_prompt = Security.sanitize(prompt)
        
        logger.info("🔍 [%s] Text: mode=%s len=%d", request_id, preferred_mode, len(clean_prompt))
        
        if Security.is_low_signal(clean_prompt):
            return "Please send a clearer message.", "EONIX-prime", 0.0, "general_chat"

        task_type = Security.detect_task_type(clean_prompt)
        mode_id = self._resolve_mode(clean_prompt, preferred_mode, task_type, 
                                     auto_detect_task, enforce_task_restrictions)
        
        system_prompt = ModelSpecialization.get_system_prompt(mode_id)
        messages = self._build_messages(clean_prompt, history, system_prompt, task_type)
        chain = get_model_chain(mode_id)
        
        result = self._try_chain(chain, messages, mode_id, task_type, request_id)
        
        if result:
            text, provider, model = result
            elapsed = round(unix_now() - started, 2)
            model_diagnostics.record_success(provider, model)
            model_diagnostics.record_response_time(model, elapsed * 1000)
            logger.info("✅ [%s] %s/%s | %s/%s | %.2fs", 
                       request_id, mode_id, task_type, provider, model, elapsed)
            return text, mode_id, elapsed, task_type
        
        elapsed = round(unix_now() - started, 2)
        logger.error("❌ [%s] All failed for %s/%s", request_id, mode_id, task_type)
        return self._offline_reply(clean_prompt, task_type), mode_id, elapsed, task_type

    # =======================================================================
    # IMAGE ANALYSIS (Vision)
    # =======================================================================
    def analyze_image(
        self, image_b64: str, mime_type: str, prompt: str = "",
        preferred_mode: str = "EONIX-vision",
    ) -> Tuple[str, str, float, str]:
        """
        Analyze an image using vision-capable models.
        Returns: (analysis_text, mode_used, elapsed_time, task_type)
        """
        request_id = new_id("vis")
        started = unix_now()
        clean_prompt = Security.sanitize(prompt or "Describe this image in detail.")
        task_type = "image_analysis"
        mode_id = "EONIX-vision"
        
        logger.info("👁️ [%s] Vision: mime=%s prompt_len=%d", request_id, mime_type, len(clean_prompt))
        
        # Validate image size
        img_size = len(base64.b64decode(image_b64))
        if img_size > Config.MAX_IMAGE_BYTES:
            raise ValueError(f"Image too large: {img_size} bytes (max {Config.MAX_IMAGE_BYTES})")
        
        # Get vision-capable chain
        system_prompt = ModelSpecialization.get_system_prompt(mode_id)
        chain = get_model_chain(mode_id)
        
        for candidate in chain:
            if not candidate.supports_vision:
                continue
            
            breaker = self._breakers[candidate.model]
            if breaker.is_open:
                continue
            
            if not ModelHealthTracker.is_available(candidate.model):
                continue
            if not model_diagnostics.provider_configured(candidate.provider):
                continue
            if model_diagnostics.deprecated_reason(candidate.provider, candidate.model):
                continue
            
            try:
                text = self._dispatch_vision(candidate, image_b64, mime_type, 
                                            clean_prompt, system_prompt)
                
                if text:
                    breaker.success()
                    elapsed = round(unix_now() - started, 2)
                    model_diagnostics.record_success(candidate.provider, candidate.model)
                    model_diagnostics.record_response_time(candidate.model, elapsed * 1000)
                    logger.info("✅ [%s] Vision: %s/%s | %.2fs", 
                               request_id, candidate.provider, candidate.model, elapsed)
                    return text, mode_id, elapsed, task_type
                    
            except Exception as e:
                breaker.failure()
                model_diagnostics.record_failure(candidate.provider, candidate.model, e)
                logger.warning("Vision failed: %s/%s - %s", 
                             candidate.provider, candidate.model, str(e)[:100])
        
        elapsed = round(unix_now() - started, 2)
        logger.error("❌ [%s] All vision candidates failed", request_id)
        
        # Fallback: try text-only description
        try:
            fallback_text, _, _, _ = self.generate(
                f"Describe what might be in this image based on context: {clean_prompt}",
                preferred_mode="EONIX-prime", auto_detect_task=False
            )
            return f"[Vision unavailable - text fallback] {fallback_text}", mode_id, elapsed, task_type
        except Exception:
            raise ProviderUnavailable("EONIX Vision is unavailable. Please try again later.")

    def _dispatch_vision(self, candidate: ModelCandidate, image_b64: str,
                         mime_type: str, prompt: str, system_prompt: str) -> Optional[str]:
        """Route vision request to appropriate provider"""
        if candidate.provider == "gemini":
            return self._gemini_vision(candidate, image_b64, mime_type, prompt, system_prompt)
        
        if candidate.provider == "openrouter":
            return self._openrouter_vision(candidate, image_b64, mime_type, prompt, system_prompt)
        
        return None

    def _gemini_vision(self, candidate: ModelCandidate, image_b64: str,
                       mime_type: str, prompt: str, system_prompt: str) -> str:
        """Gemini vision API"""
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                ],
            }],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": candidate.max_tokens,
            },
        }
        
        response = self.session.post(
            f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=Config.IMAGE_REQUEST_TIMEOUT,
        )
        
        if response.status_code >= 400:
            raise RuntimeError(self._extract_error(response))
        
        data = safe_json(response)
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
        
        if not text:
            raise RuntimeError("Empty vision response")
        
        return text

    def _openrouter_vision(self, candidate: ModelCandidate, image_b64: str,
                           mime_type: str, prompt: str, system_prompt: str) -> str:
        """OpenRouter vision API"""
        data_url = f"data:{mime_type};base64,{image_b64}"
        
        payload = {
            "model": candidate.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "max_tokens": candidate.max_tokens,
            "temperature": candidate.temperature,
        }
        
        response = self.session.post(
            f"{Config.OPENROUTER_BASE}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {Config.OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": Config.FRONTEND_URL,
                "X-Title": Config.APP_NAME,
            },
            timeout=Config.IMAGE_REQUEST_TIMEOUT,
        )
        
        if response.status_code >= 400:
            raise RuntimeError(self._extract_error(response))
        
        data = safe_json(response)
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        
        if not content:
            raise RuntimeError("Empty vision response")
        
        return content

    # =======================================================================
    # IMAGE GENERATION (Forge)
    # =======================================================================
    def create_image(
        self, prompt: str, aspect_ratio: str = "1:1",
        preferred_mode: str = "EONIX-forge",
    ) -> Tuple[str, str, float, str]:
        """
        Generate an image from text prompt.
        Returns: (base64_data_url, mode_used, elapsed_time, task_type)
        """
        request_id = new_id("img")
        started = unix_now()
        clean_prompt = Security.sanitize(prompt, 1600)
        task_type = "image_generation"
        mode_id = "EONIX-forge"
        
        if not clean_prompt:
            raise ValueError("Prompt is required")
        
        logger.info("🎨 [%s] Forge: ratio=%s prompt_len=%d", request_id, aspect_ratio, len(clean_prompt))
        
        chain = get_model_chain(mode_id)
        
        for candidate in chain:
            if not candidate.supports_image_generation:
                continue
            
            breaker = self._breakers[candidate.model]
            if breaker.is_open:
                continue
            
            if not ModelHealthTracker.is_available(candidate.model):
                continue
            if not model_diagnostics.provider_configured(candidate.provider):
                continue
            if model_diagnostics.deprecated_reason(candidate.provider, candidate.model):
                continue
            
            try:
                image_url = self._imagen_generate(candidate.model, clean_prompt, aspect_ratio)
                
                if image_url:
                    breaker.success()
                    elapsed = round(unix_now() - started, 2)
                    model_diagnostics.record_success(candidate.provider, candidate.model)
                    model_diagnostics.record_response_time(candidate.model, elapsed * 1000)
                    logger.info("✅ [%s] Forge: %s | %.2fs", request_id, candidate.model, elapsed)
                    return image_url, mode_id, elapsed, task_type
                    
            except Exception as e:
                breaker.failure()
                model_diagnostics.record_failure(candidate.provider, candidate.model, e)
                logger.warning("Forge failed: %s - %s", candidate.model, str(e)[:100])
        
        elapsed = round(unix_now() - started, 2)
        logger.error("❌ [%s] All forge candidates failed", request_id)
        raise ProviderUnavailable("EONIX Forge is unavailable. Please try again later.")

    def _imagen_generate(self, model: str, prompt: str, aspect_ratio: str) -> str:
        """Imagen image generation API"""
        valid_ratios = {"1:1", "3:4", "4:3", "9:16", "16:9"}
        safe_ratio = aspect_ratio if aspect_ratio in valid_ratios else "1:1"
        
        payload = {
            "instances": [{"prompt": prompt[:1800]}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": safe_ratio,
                "personGeneration": "allow_adult",
            },
        }
        
        response = self.session.post(
            f"{Config.GEMINI_BASE}/models/{model}:predict",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=Config.IMAGE_REQUEST_TIMEOUT,
        )
        
        if response.status_code >= 400:
            raise RuntimeError(self._extract_error(response))
        
        data = safe_json(response)
        predictions = data.get("predictions") or []
        
        if not predictions:
            raise RuntimeError("No image generated")
        
        first = predictions[0]
        encoded = (
            first.get("bytesBase64Encoded")
            or (first.get("image") or {}).get("bytesBase64Encoded")
            or first.get("imageBytes")
        )
        
        if not encoded:
            raise RuntimeError("Malformed image payload")
        
        return f"data:image/png;base64,{encoded}"

    # =======================================================================
    # SHARED METHODS (from Section 6)
    # =======================================================================
    def _resolve_mode(self, prompt, preferred_mode, task_type, auto_detect, enforce):
        mode_id = normalize_mode(preferred_mode)
        if auto_detect and not preferred_mode:
            mode_id = Security.get_recommended_mode(prompt)
        if enforce:
            is_valid, msg, recommended = Security.validate_task_for_mode(mode_id, prompt)
            if not is_valid and recommended:
                model_diagnostics.record_task_validation_failure(mode_id, task_type)
                mode_id = recommended
        return mode_id

    def _try_chain(self, chain, messages, mode_id, task_type, request_id):
        for candidate in chain:
            breaker = self._breakers[candidate.model]
            if breaker.is_open: continue
            if not ModelHealthTracker.is_available(candidate.model): continue
            if not model_diagnostics.provider_configured(candidate.provider): continue
            if model_diagnostics.deprecated_reason(candidate.provider, candidate.model): continue
            if candidate.fallback_only and candidate.task_focus != task_type: continue
            
            text = self._try_with_retry(candidate, messages, request_id)
            if text:
                breaker.success()
                return text, candidate.provider, candidate.model
            else:
                breaker.failure()
        return None

    def _try_with_retry(self, candidate, messages, request_id, max_retries=2):
        adjusted = self._adjust_candidate(candidate, 
                         Security.detect_task_type(messages[-1]["content"]))
        for attempt in range(max_retries + 1):
            try:
                return self._dispatch(adjusted, messages)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < max_retries:
                    time.sleep((attempt + 1) * 2)
                else:
                    raise
        return None

    def _adjust_candidate(self, c, task_type):
        adjustments = {
            "math_reasoning": {"temperature": 0.3, "max_tokens": min(c.max_tokens + 2000, 16000)},
            "coding": {"temperature": 0.5, "max_tokens": min(c.max_tokens + 4000, 16000)},
            "creative_writing": {"temperature": 0.85},
            "fact_checking": {"temperature": 0.4},
        }
        if task_type in adjustments:
            adj = adjustments[task_type]
            return ModelCandidate(
                provider=c.provider, model=c.model, public_mode=c.public_mode,
                max_tokens=adj.get("max_tokens", c.max_tokens),
                temperature=adj.get("temperature", c.temperature),
                thinking=c.thinking, supports_text=c.supports_text,
                supports_vision=c.supports_vision,
                supports_image_generation=c.supports_image_generation,
                task_focus=c.task_focus, fallback_only=c.fallback_only,
            )
        return c

    def _build_messages(self, prompt, history, system_prompt, task_type):
        task_instructions = {
            "math_reasoning": " Show your work step by step.",
            "coding": " Write clean, working code with explanations.",
            "creative_writing": " Be imaginative and engaging.",
            "fact_checking": " Cite sources when possible.",
        }
        full_system = system_prompt + task_instructions.get(task_type, "")
        messages = [{"role": "system", "content": full_system}]
        recent = [m for m in history if m.role in ("user", "assistant")][-Config.MAX_HISTORY_MESSAGES:]
        for m in recent:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _dispatch(self, candidate, messages):
        if not candidate.supports_text: return None
        handlers = {
            "deepseek": lambda: self._openai_compatible(Config.DEEPSEEK_BASE, Config.DEEPSEEK_KEY, candidate, messages, deepseek=True),
            "groq": lambda: self._openai_compatible(Config.GROQ_BASE, Config.GROQ_KEY, candidate, messages),
            "openrouter": lambda: self._openai_compatible(Config.OPENROUTER_BASE, Config.OPENROUTER_KEY, candidate, messages, extra_headers={"HTTP-Referer": Config.FRONTEND_URL, "X-Title": Config.APP_NAME}),
            "gemini": lambda: self._gemini_text(candidate, messages),
        }
        handler = handlers.get(candidate.provider)
        return handler() if handler else None

    def _openai_compatible(self, base_url, api_key, candidate, messages, extra_headers=None, deepseek=False):
        if not api_key: raise ProviderUnavailable("API key missing")
        payload = {"model": candidate.model, "messages": messages, "max_tokens": candidate.max_tokens, "temperature": candidate.temperature}
        if deepseek and candidate.thinking:
            payload["thinking"] = {"type": "disabled" if candidate.thinking == "disabled" else "enabled"}
            if candidate.thinking in ("high", "max"): payload["reasoning_effort"] = candidate.thinking
        response = self.session.post(f"{base_url}/chat/completions", json=payload, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **(extra_headers or {})}, timeout=Config.REQUEST_TIMEOUT)
        if response.status_code >= 400: raise RuntimeError(self._extract_error(response))
        data = safe_json(response)
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content: raise RuntimeError("Empty response")
        usage = data.get("usage", {})
        if usage: model_diagnostics.record_token_usage(candidate.model, usage.get("total_tokens", 0))
        return content

    def _gemini_text(self, candidate, messages):
        if not Config.GEMINI_KEY: raise ProviderUnavailable("API key missing")
        system_text = ""; contents = []
        for msg in messages:
            if msg["role"] == "system": system_text = msg["content"]; continue
            contents.append({"role": "model" if msg["role"] == "assistant" else "user", "parts": [{"text": msg["content"]}]})
        response = self.session.post(f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent", params={"key": Config.GEMINI_KEY}, json={"systemInstruction": {"parts": [{"text": system_text}]}, "contents": contents, "generationConfig": {"temperature": candidate.temperature, "maxOutputTokens": candidate.max_tokens}}, timeout=Config.REQUEST_TIMEOUT)
        if response.status_code >= 400: raise RuntimeError(self._extract_error(response))
        data = safe_json(response)
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
        if not text: raise RuntimeError("Empty response")
        usage = data.get("usageMetadata", {})
        if usage: model_diagnostics.record_token_usage(candidate.model, usage.get("totalTokenCount", 0))
        return text

    @staticmethod
    def _extract_error(response):
        try:
            data = response.json()
            error = data.get("error", {})
            return (error.get("message") if isinstance(error, dict) else str(error))[:500]
        except Exception:
            return response.text[:500] or f"HTTP {response.status_code}"

    @staticmethod
    def _offline_reply(prompt, task_type="general_chat"):
        replies = {
            "math_reasoning": "EONIX DeepCore unavailable. Try again shortly.",
            "coding": "EONIX Oracle unavailable. Try again shortly.",
            "creative_writing": "EONIX Swift unavailable. Try again shortly.",
            "image_analysis": "EONIX Vision unavailable. Try again shortly.",
            "image_generation": "EONIX Forge unavailable. Try again shortly.",
            "fact_checking": "EONIX Knowledge unavailable. Try again shortly.",
        }
        return replies.get(task_type, "EONIX is temporarily unavailable. Please try again.")

    # =======================================================================
    # MODEL PROBE
    # =======================================================================
    def probe_candidate(self, candidate: ModelCandidate) -> Tuple[bool, str]:
        """Test if a model is working"""
        try:
            if candidate.supports_image_generation:
                self._imagen_generate(candidate.model, "simple abstract sphere", "1:1")
                return True, "image generation OK"
            
            if candidate.supports_vision:
                tiny_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO0pNxsAAAAASUVORK5CYII="
                self._gemini_vision(candidate, tiny_png, "image/png", "What is this?", "Be brief.")
                return True, "vision OK"
            
            text = self._dispatch(candidate, [{"role": "user", "content": "Reply READY"}])
            return (bool(text), "text OK" if text else "empty response")
        except Exception as e:
            return False, str(e)[:200]


# Global instance
ai_service = AIService()

# SECTION 8: Flask App Setup and Serialization
# Flask application initialization, security, and data serialization

# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent

def create_app() -> Flask:
    """Application factory for proper initialization"""
    app = Flask(__name__, static_folder=str(ROOT))
    
    # Core config
    app.secret_key = Config.SECRET_KEY
    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        MAX_CONTENT_LENGTH=Config.MAX_CONTENT_LENGTH,
        SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
        SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
        SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
    )
    
    # Proxy support
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    
    # CORS
    CORS(app, origins=Config.get_cors_origins(), supports_credentials=True)
    
    # Security headers
    @app.after_request
    def add_security_headers(response):
        for header, value in Config.SECURITY_HEADERS.items():
            response.headers[header] = value
        return response
    
    # Error handlers
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"success": False, "error": "Bad request"}), 400
    
    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"success": False, "error": "Authentication required"}), 401
    
    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"success": False, "error": "Not found"}), 404
    
    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"success": False, "error": "Request too large"}), 413
    
    @app.errorhandler(429)
    def rate_limited(e):
        return jsonify({"success": False, "error": "Rate limited"}), 429
    
    @app.errorhandler(500)
    def server_error(e):
        logger.error("Internal server error: %s", str(e))
        if Config.ERROR_REPORTING_ENABLED and Config.ERROR_REPORTING_WEBHOOK:
            try:
                requests.post(Config.ERROR_REPORTING_WEBHOOK, json={
                    "error": str(e), "timestamp": iso_now(),
                    "path": request.path, "method": request.method,
                }, timeout=5)
            except Exception:
                pass
        return jsonify({"success": False, "error": "Internal server error"}), 500
    
    @app.errorhandler(TaskNotAllowedError)
    def task_not_allowed(e):
        return jsonify({"success": False, "error": str(e)}), 400
    
    @app.errorhandler(ProviderUnavailable)
    def provider_unavailable(e):
        return jsonify({"success": False, "error": str(e)}), 503
    
    # Health endpoint
    register_health_endpoint(app)
    
    # Startup validation
    if not Config.validate_config():
        logger.error("Configuration validation failed")
    
    if not Config.validate_secrets():
        logger.error("Secret validation failed")
    
    return app


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def serialize_user(user: UserDTO) -> Dict[str, Any]:
    return {
        "id": user.id,
        "name": user.display_name,
        "email": user.email,
        "avatar": user.avatar_url,
        "is_admin": user.is_admin,
        "created_at": current_iso_from_ts(user.created_at) if user.created_at else None,
    }

def serialize_session(session_obj: SessionDTO) -> Dict[str, Any]:
    return {
        "id": session_obj.id,
        "title": session_obj.title,
        "user_id": session_obj.user_id,
        "message_count": session_obj.message_count,
        "created_at": current_iso_from_ts(session_obj.created_at),
        "updated_at": current_iso_from_ts(session_obj.updated_at),
    }

def serialize_message(message: MessageDTO) -> Dict[str, Any]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "mode": message.mode,
        "task_type": message.task_type,
        "validated": message.validated,
        "attachments": message.attachments,
        "created_at": current_iso_from_ts(message.created_at),
    }

def serialize_mode_info(mode_id: str) -> Dict[str, Any]:
    """Serialize mode information with availability"""
    spec = get_mode_spec(mode_id)
    report = model_diagnostics.mode_report(mode_id)
    
    return {
        "id": spec.id,
        "label": spec.label,
        "description": spec.description,
        "icon": spec.icon,
        "available": report["available"],
        "healthy": report["healthy"],
        "candidate_count": len(spec.chain),
        "primary_model": spec.chain[0].model if spec.chain else None,
    }

def serialize_available_modes() -> List[Dict[str, Any]]:
    """List all modes with their status"""
    return [serialize_mode_info(mid) for mid in MODE_SPECS]

def serialize_validation_result(
    mode: str, task_type: str, is_valid: bool, 
    message: str, recommended_mode: Optional[str] = None
) -> Dict[str, Any]:
    result = {
        "mode": mode,
        "task_type": task_type,
        "valid": is_valid,
        "message": message,
    }
    if recommended_mode:
        result["recommended_mode"] = recommended_mode
        result["recommended_mode_info"] = serialize_mode_info(recommended_mode)
    return result

def serialize_diagnostics() -> Dict[str, Any]:
    """Serialize full diagnostics for admin dashboard"""
    return {
        "app": {
            "name": Config.APP_NAME,
            "version": Config.VERSION,
            "environment": Config.ENVIRONMENT,
        },
        "health": model_diagnostics.get_health_status(),
        "performance": model_diagnostics.get_performance_metrics(),
        "alerts": model_diagnostics.check_alerts(),
        "modes": [serialize_mode_info(mid) for mid in MODE_SPECS],
        "storage": storage.get_stats(),
        "safety": ContentSafety.get_stats(),
        "rate_limiter": rate_limiter.get_stats(),
    }

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def current_user_from_token() -> Optional[UserDTO]:
    """Extract current user from JWT token or session"""
    # Try JWT first
    token = JWTService.from_request()
    if token:
        payload = JWTService.verify(token)
        if payload:
            user = storage.get_user(payload.get("user_id", ""))
            if user:
                return user
    
    # Fallback to session
    session_user_id = session.get("user_id")
    if session_user_id:
        return storage.get_user(session_user_id)
    
    return None

def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = current_user_from_token()
        if not user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        
        # Check if banned
        if ContentSafety.is_banned(email=user.email, uid=user.id):
            return jsonify({
                "success": False,
                "error": "Account suspended for policy violation. Contact support@eonix.ai to appeal."
            }), 403
        
        g.user = user
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = current_user_from_token()
        if not user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        if not user.is_admin:
            return jsonify({"success": False, "error": "Admin access required"}), 403
        g.user = user
        return fn(*args, **kwargs)
    return wrapper

def optional_user(fn: Callable) -> Callable:
    """Attach user if available, but don't require auth"""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        g.user = current_user_from_token()
        return fn(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
# Rate limiting helper
# ---------------------------------------------------------------------------
def check_rate_limit(key: str, limit: int, window: int) -> Optional[Tuple[Any, int]]:
    """Rate limit check with proper response"""
    allowed, remaining = rate_limiter.check(key, limit, window)
    if not allowed:
        return jsonify({
            "success": False,
            "error": "Rate limit exceeded. Please wait before sending more requests.",
            "retry_after": window,
        }), 429
    
    g.rate_remaining = remaining
    return None

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def parse_image_payload(value: str) -> Tuple[str, str]:
    """Parse and validate base64 image payload"""
    if not value:
        raise ValueError("No image provided")

    clean = value.strip()
    mime = "image/png"
    
    # Handle data URL format
    if clean.startswith("data:"):
        match = re.match(r"data:([^;]+);base64,(.+)", clean, re.DOTALL)
        if match:
            mime = match.group(1)
            clean = match.group(2)
        else:
            raise ValueError("Invalid data URL format")

    # Validate MIME type
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
    if mime not in allowed_types:
        raise ValueError(f"Unsupported image type: {mime}. Allowed: {', '.join(allowed_types)}")

    # Decode and validate
    try:
        raw = base64.b64decode(clean, validate=True)
    except Exception:
        raise ValueError("Invalid base64 encoding")

    if len(raw) > Config.MAX_IMAGE_BYTES:
        raise ValueError(f"Image too large: {len(raw)} bytes (max: {Config.MAX_IMAGE_BYTES})")
    
    if len(raw) < 100:  # Suspiciously small
        raise ValueError("Image too small or corrupted")

    return base64.b64encode(raw).decode("ascii"), mime

# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def get_pagination_params() -> Tuple[int, int]:
    """Extract pagination params from request"""
    page = max(1, request.args.get("page", 1, type=int))
    limit = min(100, max(1, request.args.get("limit", 20, type=int)))
    offset = (page - 1) * limit
    return offset, limit

def success_response(data: Any = None, **kwargs) -> Tuple[Any, int]:
    """Standardized success response"""
    response = {"success": True}
    if data is not None:
        response["data"] = data
    response.update(kwargs)
    return jsonify(response), 200

def error_response(message: str, status_code: int = 400, **kwargs) -> Tuple[Any, int]:
    """Standardized error response"""
    response = {"success": False, "error": message}
    response.update(kwargs)
    return jsonify(response), status_code

# ---------------------------------------------------------------------------
# Create app instance
# ---------------------------------------------------------------------------
app = create_app()

# SECTION 9: Routes - Static, Health, Modes, Tasks, and Diagnostics
# All application routes registered via app factory

def register_routes(app: Flask) -> None:
    """Register all routes on the Flask app"""
    
    # =======================================================================
    # STATIC & ROOT
    # =======================================================================
    @app.get("/")
    def index() -> Any:
        filename = "jarvis-enterprise-v5.html" if (ROOT / "jarvis-enterprise-v5.html").exists() else "index.html"
        return send_from_directory(ROOT, filename)
    
    @app.get("/favicon.ico")
    def favicon():
        favicon_path = ROOT / "favicon.ico"
        if favicon_path.exists():
            return send_from_directory(str(ROOT), "favicon.ico")
        return "", 204
    
    # =======================================================================
    # HEALTH
    # =======================================================================
    @app.get("/health")
    @app.get("/EONIX/health")
    def health() -> Any:
        """Public health check for load balancers"""
        health_status = model_diagnostics.get_health_status()
        
        return jsonify({
            "success": True,
            "status": health_status["status"],
            "version": Config.VERSION,
            "environment": Config.ENVIRONMENT,
            "time": iso_now(),
            "available_models": health_status["total_configured_models"],
            "unhealthy_count": len(health_status["unhealthy_models"]),
        })
    
    # =======================================================================
    # MODES
    # =======================================================================
    @app.get("/EONIX/modes")
    def list_modes() -> Any:
        """List all available modes with status"""
        return jsonify({
            "success": True,
            "modes": serialize_available_modes(),
        })
    
    @app.get("/EONIX/modes/<mode_id>")
    def mode_detail(mode_id: str) -> Any:
        """Get detailed info about a specific mode"""
        normalized = normalize_mode(mode_id)
        if normalized not in MODE_SPECS:
            return error_response("Mode not found", 404)
        
        return jsonify({
            "success": True,
            "mode": serialize_mode_info(normalized),
        })
    
    # =======================================================================
    # TASK DETECTION & VALIDATION
    # =======================================================================
    @app.get("/EONIX/task/detect")
    def detect_task() -> Any:
        """Detect task type and recommend mode"""
        text = request.args.get("text", "")
        if not text:
            return error_response("text parameter required", 400)
        
        task_type = Security.detect_task_type(text)
        recommended = Security.get_recommended_mode(text)
        
        return jsonify({
            "success": True,
            "task_type": task_type,
            "recommended_mode": recommended,
            "recommended_info": serialize_mode_info(recommended),
        })
    
    @app.get("/EONIX/task/validate")
    def validate_task() -> Any:
        """Validate task-mode compatibility"""
        mode = request.args.get("mode", "EONIX-prime")
        text = request.args.get("text", "")
        
        if not text:
            return error_response("text parameter required", 400)
        
        normalized = normalize_mode(mode)
        task_type = Security.detect_task_type(text)
        is_valid, message, recommended = Security.validate_task_for_mode(normalized, text)
        
        return jsonify({
            "success": True,
            "validation": serialize_validation_result(
                normalized, task_type, is_valid, message, recommended
            ),
        })
    
    # =======================================================================
    # USER & SESSION (example routes)
    # =======================================================================
    @app.get("/EONIX/user/me")
    @login_required
    def get_current_user() -> Any:
        """Get current user info"""
        return jsonify({
            "success": True,
            "user": serialize_user(g.user),
        })
    
    @app.get("/EONIX/sessions")
    @login_required
    def list_sessions() -> Any:
        """List user's sessions"""
        sessions = storage.get_user_sessions(g.user.id)
        return jsonify({
            "success": True,
            "sessions": [serialize_session(s) for s in sessions],
        })
    
    @app.post("/EONIX/sessions")
    @login_required
    def create_session() -> Any:
        """Create new session"""
        data = json_body()
        title = data.get("title", "New Conversation")
        session_obj = storage.create_session(g.user.id, title)
        return jsonify({
            "success": True,
            "session": serialize_session(session_obj),
        })
    
    @app.delete("/EONIX/sessions/<session_id>")
    @login_required
    def delete_session(session_id: str) -> Any:
        """Delete a session"""
        deleted = storage.delete_session(session_id, g.user.id)
        if not deleted:
            return error_response("Session not found", 404)
        return jsonify({"success": True, "deleted": True})
    
    @app.get("/EONIX/sessions/<session_id>/messages")
    @login_required
    def get_messages(session_id: str) -> Any:
        """Get messages for a session"""
        session_obj = storage.get_session(session_id, g.user.id)
        if not session_obj:
            return error_response("Session not found", 404)
        
        messages = storage.get_messages(session_id)
        return jsonify({
            "success": True,
            "session": serialize_session(session_obj),
            "messages": [serialize_message(m) for m in messages],
        })
    
    # =======================================================================
    # CHAT
    # =======================================================================
    @app.post("/EONIX/chat")
    @login_required
    def chat() -> Any:
        """Main chat endpoint"""
        data = json_body()
        message = data.get("message", "")
        mode = data.get("mode")
        session_id = data.get("session_id")
        
        if not message:
            return error_response("message is required", 400)
        
        # Content safety check
        safety = Security.safety_check(message, g.user.email, g.user.id)
        if not safety["safe"]:
            return error_response(safety["msg"], 403 if safety.get("banned") else 400)
        
        # Rate limiting
        ip = get_request_ip()
        limit_check = check_rate_limit(
            f"chat:{g.user.id}", 
            Config.RATE_LIMIT_MESSAGES, 
            Config.RATE_LIMIT_WINDOW
        )
        if limit_check:
            return limit_check
        
        # Create session if needed
        if not session_id:
            session_obj = storage.create_session(g.user.id, Security.title(message))
            session_id = session_obj.id
        
        # Validate session ownership
        session_obj = storage.get_session(session_id, g.user.id)
        if not session_obj:
            return error_response("Session not found", 404)
        
        # Store user message
        task_type = Security.detect_task_type(message)
        storage.add_message(session_id, "user", message, mode=mode or "EONIX-prime", task_type=task_type)
        
        # Get history
        history = storage.get_recent_messages(session_id)
        
        # Generate response
        try:
            response_text, used_mode, elapsed, detected_task = ai_service.generate(
                message, mode, history
            )
            
            # Store assistant message
            storage.add_message(
                session_id, "assistant", response_text, 
                mode=used_mode, task_type=detected_task
            )
            
            return jsonify({
                "success": True,
                "response": response_text,
                "mode": used_mode,
                "task_type": detected_task,
                "elapsed": elapsed,
                "session_id": session_id,
                "rate_remaining": getattr(g, 'rate_remaining', 0),
            })
            
        except ProviderUnavailable as e:
            return error_response(str(e), 503)
        except TaskNotAllowedError as e:
            return error_response(str(e), 400)
    
    # =======================================================================
    # VISION
    # =======================================================================
    @app.post("/EONIX/vision")
    @login_required
    def vision() -> Any:
        """Image analysis endpoint"""
        data = json_body()
        image_data = data.get("image", "")
        prompt = data.get("prompt", "")
        session_id = data.get("session_id")
        
        if not image_data:
            return error_response("image is required", 400)
        
        # Parse and validate image
        try:
            image_b64, mime = parse_image_payload(image_data)
        except ValueError as e:
            return error_response(str(e), 400)
        
        # Content safety
        safety = Security.safety_check(prompt or "image analysis", g.user.email, g.user.id)
        if not safety["safe"]:
            return error_response(safety["msg"], 403)
        
        # Rate limiting
        ip = get_request_ip()
        limit_check = check_rate_limit(f"vision:{g.user.id}", 10, 60)
        if limit_check:
            return limit_check
        
        # Create session if needed
        if not session_id:
            session_obj = storage.create_session(g.user.id, "Image Analysis")
            session_id = session_obj.id
        
        # Store user message
        storage.add_message(
            session_id, "user", prompt or "Analyze this image",
            mode="EONIX-vision", task_type="image_analysis"
        )
        
        # Analyze
        try:
            analysis, used_mode, elapsed, task_type = ai_service.analyze_image(
                image_b64, mime, prompt
            )
            
            storage.add_message(
                session_id, "assistant", analysis,
                mode=used_mode, task_type=task_type
            )
            
            return jsonify({
                "success": True,
                "analysis": analysis,
                "mode": used_mode,
                "elapsed": elapsed,
                "session_id": session_id,
            })
            
        except ProviderUnavailable as e:
            return error_response(str(e), 503)
    
    # =======================================================================
    # IMAGE GENERATION
    # =======================================================================
    @app.post("/EONIX/forge")
    @login_required
    def forge() -> Any:
        """Image generation endpoint"""
        data = json_body()
        prompt = data.get("prompt", "")
        aspect_ratio = data.get("aspect_ratio", "1:1")
        
        if not prompt:
            return error_response("prompt is required", 400)
        
        # Content safety
        safety = Security.safety_check(prompt, g.user.email, g.user.id)
        if not safety["safe"]:
            return error_response(safety["msg"], 403)
        
        # Rate limiting (stricter for image generation)
        ip = get_request_ip()
        limit_check = check_rate_limit(f"forge:{g.user.id}", 5, 60)
        if limit_check:
            return limit_check
        
        # Generate image
        try:
            image_url, used_mode, elapsed, task_type = ai_service.create_image(
                prompt, aspect_ratio
            )
            
            return jsonify({
                "success": True,
                "image_url": image_url,
                "mode": used_mode,
                "elapsed": elapsed,
                "prompt": prompt,
            })
            
        except ProviderUnavailable as e:
            return error_response(str(e), 503)
    
    # =======================================================================
    # ADMIN DIAGNOSTICS
    # =======================================================================
    @app.get("/EONIX/admin/diagnostics")
    @admin_required
    def admin_diagnostics() -> Any:
        """Full system diagnostics (admin only)"""
        return jsonify({
            "success": True,
            "diagnostics": serialize_diagnostics(),
        })
    
    @app.get("/EONIX/admin/diagnostics/modes")
    @admin_required
    def admin_mode_diagnostics() -> Any:
        """Detailed mode diagnostics"""
        return jsonify({
            "success": True,
            **model_diagnostics.all_modes_report(),
        })
    
    @app.get("/EONIX/admin/diagnostics/health")
    @admin_required
    def admin_health_diagnostics() -> Any:
        """System health diagnostics"""
        return jsonify({
            "success": True,
            "health": model_diagnostics.get_health_status(),
            "providers": Config.provider_flags(),
        })
    
    @app.get("/EONIX/admin/diagnostics/performance")
    @admin_required
    def admin_performance_diagnostics() -> Any:
        """Performance metrics"""
        return jsonify({
            "success": True,
            "performance": model_diagnostics.get_performance_metrics(),
            "stats": {
                "storage": storage.get_stats(),
                "safety": ContentSafety.get_stats(),
                "rate_limiter": rate_limiter.get_stats(),
            },
        })
    
    @app.get("/EONIX/admin/users")
    @admin_required
    def admin_users() -> Any:
        """List all users (admin only)"""
        with storage.lock:
            users = [serialize_user(u) for u in storage.users.values()]
        return jsonify({
            "success": True,
            "total": len(users),
            "users": users,
        })
    
    # =======================================================================
    # CORS Preflight
    # =======================================================================
    @app.after_request
    def after_request(response):
        """Add CORS headers to all responses"""
        origin = request.headers.get("Origin")
        if origin in Config.get_cors_origins():
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response


# ---------------------------------------------------------------------------
# Register routes on app
# ---------------------------------------------------------------------------
register_routes(app)

# SECTION 10: Authentication Routes
# Google OAuth, token management, and user authentication

def register_auth_routes(app: Flask) -> None:
    """Register authentication routes on the Flask app"""
    
    # =======================================================================
    # GOOGLE OAUTH LOGIN
    # =======================================================================
    @app.get("/EONIX/sign-in")
    @app.get("/login/google")
    def google_login() -> Any:
        """Initiate Google OAuth login flow"""
        
        # Development fallback (no Google OAuth configured)
        if Config.ENVIRONMENT == "development" and Config.ALLOW_DEV_LOGIN and not Config.GOOGLE_CLIENT_ID:
            user = storage.create_or_update_user(
                None, "developer@eonix.local", "Developer"
            )
            token = JWTService.create(user)
            storage.set_session_token(user.id, token)
            session["user_id"] = user.id
            session["session_token"] = token
            logger.info("Dev login: %s", user.id)
            return redirect(f"{Config.FRONTEND_URL}?token={token}")

        # Require Google OAuth config
        if not (Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET):
            logger.error("Google OAuth not configured")
            return redirect(f"{Config.FRONTEND_URL}?error=OAuth+not+configured")

        # Generate state for CSRF protection
        state = Security.token(32)
        session["oauth_state"] = state
        session["oauth_redirect"] = request.args.get("redirect", Config.FRONTEND_URL)
        
        params = {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "redirect_uri": Config.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "email profile openid",
            "state": state,
            "prompt": "select_account",
            "access_type": "offline",
        }
        
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return redirect(auth_url)

    # =======================================================================
    # GOOGLE OAUTH CALLBACK
    # =======================================================================
    @app.get("/login/callback")
    @app.get("/EONIX/auth/callback")
    def google_callback() -> Any:
        """Handle Google OAuth callback"""
        
        # Handle errors
        if request.args.get("error"):
            error_msg = request.args.get("error", "unknown")
            logger.error("OAuth error: %s", error_msg)
            return redirect(f"{Config.FRONTEND_URL}?error={error_msg}")

        code = request.args.get("code")
        state = request.args.get("state")
        saved_state = session.pop("oauth_state", None)
        redirect_url = session.pop("oauth_redirect", Config.FRONTEND_URL)
        
        # Validate required params
        if not code:
            return redirect(f"{redirect_url}?error=No+authorization+code")
        
        # Validate state (CSRF protection)
        if saved_state and state != saved_state:
            logger.warning("OAuth state mismatch")
            return redirect(f"{redirect_url}?error=Invalid+state")

        try:
            # Exchange code for tokens
            token_data = _exchange_google_code(code)
            access_token = token_data.get("access_token")
            
            if not access_token:
                raise RuntimeError("No access token in response")

            # Get user info from Google
            user_info = _get_google_user_info(access_token)
            
            # Check if user is banned
            email = user_info.get("email", "").lower()
            if ContentSafety.is_banned(email=email):
                logger.warning("Banned user attempted login: %s", email)
                return redirect(f"{redirect_url}?error=Account+suspended")

            # Create or update user
            user = storage.create_or_update_user(
                google_id=user_info.get("id"),
                email=email,
                name=user_info.get("name", ""),
                avatar=user_info.get("picture", ""),
            )
            
            # Generate JWT
            token = JWTService.create(user)
            storage.set_session_token(user.id, token)
            
            # Set session
            session["user_id"] = user.id
            session["session_token"] = token
            session.permanent = True
            
            logger.info("✅ User signed in: %s (%s)", user.email, user.id)
            
            # Check if first login
            is_new = user.total_logins == 1
            
            return redirect(
                f"{redirect_url}?token={token}"
                f"{'&new_user=true' if is_new else ''}"
            )
            
        except requests.Timeout:
            logger.error("OAuth timeout")
            return redirect(f"{redirect_url}?error=Auth+timeout")
        except Exception as e:
            logger.error("OAuth failed: %s", str(e)[:200])
            return redirect(f"{redirect_url}?error=Authentication+failed")

    # =======================================================================
    # SIGN OUT
    # =======================================================================
    @app.get("/EONIX/sign-out")
    @app.get("/logout")
    def logout() -> Any:
        """Sign out current user"""
        token = JWTService.from_request()
        if token:
            JWTService.revoke(token)
        
        user_id = session.pop("user_id", None)
        if user_id:
            storage.set_session_token(user_id, None)
            logger.info("User signed out: %s", user_id)
        
        session.clear()
        
        redirect_url = request.args.get("redirect", Config.FRONTEND_URL)
        return redirect(redirect_url)

    # =======================================================================
    # CURRENT USER
    # =======================================================================
    @app.get("/EONIX/me")
    @app.get("/api/me")
    @login_required
    def me() -> Any:
        """Get current user info with stats"""
        user = g.user
        stats = storage.get_user_stats(user.id)
        
        return jsonify({
            "success": True,
            "user": serialize_user(user),
            "stats": stats,
        })

    # =======================================================================
    # TOKEN REFRESH
    # =======================================================================
    @app.post("/EONIX/auth/refresh")
    @login_required
    def refresh_token() -> Any:
        """Refresh JWT token"""
        old_token = JWTService.from_request()
        if old_token:
            JWTService.revoke(old_token)
        
        new_token = JWTService.create(g.user)
        storage.set_session_token(g.user.id, new_token)
        session["session_token"] = new_token
        
        return jsonify({
            "success": True,
            "token": new_token,
            "expires_in": Config.JWT_EXPIRY_HOURS * 3600,
        })

    # =======================================================================
    # TOKEN VALIDATION
    # =======================================================================
    @app.get("/EONIX/auth/verify")
    def verify_token() -> Any:
        """Verify if a token is valid"""
        token = JWTService.from_request()
        if not token:
            return jsonify({"success": False, "valid": False, "error": "No token"}), 401
        
        payload = JWTService.verify(token)
        if not payload:
            return jsonify({"success": False, "valid": False, "error": "Invalid token"}), 401
        
        user = storage.get_user(payload.get("user_id", ""))
        if not user:
            return jsonify({"success": False, "valid": False, "error": "User not found"}), 401
        
        return jsonify({
            "success": True,
            "valid": True,
            "user": serialize_user(user),
            "expires_at": current_iso_from_ts(payload.get("exp", 0)),
        })

    # =======================================================================
    # ACCOUNT DELETION
    # =======================================================================
    @app.delete("/EONIX/account")
    @login_required
    def delete_account() -> Any:
        """Delete user account and all data"""
        user = g.user
        user_id = user.id
        
        # Delete all sessions
        sessions = storage.get_user_sessions(user_id)
        for s in sessions:
            storage.delete_session(s.id, user_id)
        
        # Remove user
        with storage.lock:
            storage.users.pop(user_id, None)
            storage.users_by_email.pop(user.email, None)
            if user.google_id:
                storage.users_by_google.pop(user.google_id, None)
        
        # Revoke token
        token = JWTService.from_request()
        if token:
            JWTService.revoke(token)
        
        session.clear()
        logger.info("Account deleted: %s", user.email)
        
        return jsonify({"success": True, "message": "Account deleted"})


# ---------------------------------------------------------------------------
# OAuth helper functions
# ---------------------------------------------------------------------------
def _exchange_google_code(code: str) -> Dict[str, Any]:
    """Exchange authorization code for tokens"""
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": Config.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    
    if response.status_code >= 400:
        error_data = safe_json(response)
        raise RuntimeError(
            f"Token exchange failed: {error_data.get('error', 'unknown')}"
        )
    
    return safe_json(response)

def _get_google_user_info(access_token: str) -> Dict[str, Any]:
    """Get user info from Google"""
    response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    
    if response.status_code >= 400:
        raise RuntimeError("Failed to get user info")
    
    info = safe_json(response)
    
    if not info.get("email"):
        raise RuntimeError("No email in user info")
    
    return info


# ---------------------------------------------------------------------------
# Register routes
# ---------------------------------------------------------------------------
register_auth_routes(app)

# SECTION 11: Conversation Routes
# Session management, messaging, and conversation endpoints

def register_conversation_routes(app: Flask) -> None:
    """Register conversation routes on the Flask app"""
    
    # =======================================================================
    # LIST CONVERSATIONS
    # =======================================================================
    @app.get("/EONIX/conversations")
    @app.get("/ai/sessions")
    @login_required
    def list_conversations() -> Any:
        """List all conversations for current user"""
        limit_check = check_rate_limit(
            f"sessions:{g.user.id}", 
            Config.RATE_LIMIT_SESSIONS, 
            Config.RATE_LIMIT_WINDOW
        )
        if limit_check:
            return limit_check
        
        sessions = storage.get_user_sessions(g.user.id)
        
        return jsonify({
            "success": True,
            "conversations": [serialize_session(s) for s in sessions],
            "total": len(sessions),
        })

    # =======================================================================
    # CREATE CONVERSATION
    # =======================================================================
    @app.post("/EONIX/conversations")
    @app.post("/ai/session")
    @login_required
    def create_conversation() -> Any:
        """Create a new conversation"""
        data = json_body()
        title = data.get("title", "New Conversation")
        mode = normalize_mode(data.get("mode"))
        
        session_obj = storage.create_session(g.user.id, title)
        
        logger.info("Session created: %s by %s", session_obj.id, g.user.id)
        
        return jsonify({
            "success": True,
            "session": serialize_session(session_obj),
            "session_id": session_obj.id,
            "default_mode": mode,
        })

    # =======================================================================
    # GET CONVERSATION
    # =======================================================================
    @app.get("/EONIX/conversations/<session_id>")
    @app.get("/ai/session/<session_id>")
    @login_required
    def get_conversation(session_id: str) -> Any:
        """Get a conversation with all messages"""
        session_obj = storage.get_session(session_id, g.user.id)
        if not session_obj:
            return error_response("Conversation not found", 404)
        
        messages = storage.get_messages(session_id)
        
        return jsonify({
            "success": True,
            "session": serialize_session(session_obj),
            "messages": [serialize_message(m) for m in messages],
        })

    # =======================================================================
    # UPDATE CONVERSATION TITLE
    # =======================================================================
    @app.patch("/EONIX/conversations/<session_id>")
    @login_required
    def update_conversation(session_id: str) -> Any:
        """Update conversation title"""
        session_obj = storage.get_session(session_id, g.user.id)
        if not session_obj:
            return error_response("Conversation not found", 404)
        
        data = json_body()
        title = data.get("title", "")
        
        if not title:
            return error_response("Title is required", 400)
        
        storage.update_title(session_id, title)
        
        return jsonify({
            "success": True,
            "session": serialize_session(storage.get_session(session_id, g.user.id)),
        })

    # =======================================================================
    # DELETE CONVERSATION
    # =======================================================================
    @app.delete("/EONIX/conversations/<session_id>")
    @app.delete("/ai/session/<session_id>")
    @login_required
    def delete_conversation(session_id: str) -> Any:
        """Delete a conversation"""
        if storage.delete_session(session_id, g.user.id):
            logger.info("Session deleted: %s by %s", session_id, g.user.id)
            return jsonify({"success": True, "deleted": True})
        
        return error_response("Conversation not found", 404)

    # =======================================================================
    # SEND MESSAGE (MAIN CHAT ENDPOINT)
    # =======================================================================
    @app.post("/EONIX/conversations/<session_id>/messages")
    @app.post("/ai/session/<session_id>/message")
    @login_required
    def send_message(session_id: str) -> Any:
        """Send a message and get AI response"""
        
        # Rate limiting
        limit_check = check_rate_limit(
            f"msg:{g.user.id}", 
            Config.RATE_LIMIT_MESSAGES, 
            Config.RATE_LIMIT_WINDOW
        )
        if limit_check:
            return limit_check

        # Validate session
        session_obj = storage.get_session(session_id, g.user.id)
        if not session_obj:
            return error_response("Conversation not found", 404)

        # Parse request
        data = json_body()
        prompt = Security.sanitize(data.get("prompt") or data.get("message") or "")
        mode = normalize_mode(data.get("mode"))
        auto_detect = data.get("auto_detect_task", True)
        enforce = data.get("enforce_task_restrictions", True)
        
        if not prompt:
            return error_response("Message is required", 400)
        
        # Content safety check
        safety = Security.safety_check(prompt, g.user.email, g.user.id)
        if not safety["safe"]:
            return error_response(safety["msg"], 403 if safety.get("banned") else 400)

        # Detect task type
        task_type = Security.detect_task_type(prompt)
        
        # Task validation (if enforced)
        if enforce:
            is_valid, msg, recommended = Security.validate_task_for_mode(mode, prompt)
            
            if not is_valid and not data.get("force_send", False):
                return jsonify({
                    "success": False,
                    "error": "Task not appropriate for this mode",
                    "validation": serialize_validation_result(mode, task_type, is_valid, msg, recommended),
                    "detected_task": task_type,
                    "current_mode": mode,
                }), 400

        # Store user message
        storage.add_message(session_id, "user", prompt, mode, task_type=task_type)
        
        # Get history for context
        history = storage.get_recent_messages(session_id)
        
        # Generate response
        try:
            response_text, used_mode, elapsed, detected_task = ai_service.generate(
                prompt, mode, history,
                auto_detect_task=auto_detect,
                enforce_task_restrictions=enforce,
            )
            
            # Store assistant message
            assistant_msg = storage.add_message(
                session_id, "assistant", response_text,
                mode=used_mode, task_type=detected_task,
            )
            
            # Auto-title on first message pair
            is_first = storage.count_messages(session_id) <= 2
            if is_first:
                storage.update_title(session_id, prompt)
            
            return jsonify({
                "success": True,
                "response": response_text,
                "message": serialize_message(assistant_msg),
                "mode": used_mode,
                "task_type": detected_task,
                "elapsed": elapsed,
                "is_first_message": is_first,
                "session_id": session_id,
                "rate_remaining": getattr(g, "rate_remaining", 0),
            })
            
        except TaskNotAllowedError as e:
            return error_response(str(e), 400)
        except ProviderUnavailable as e:
            return error_response(str(e), 503)
        except Exception as e:
            logger.error("Chat error: %s", str(e)[:200])
            return error_response("Failed to generate response", 500)

    # =======================================================================
    # REGENERATE RESPONSE
    # =======================================================================
    @app.post("/EONIX/conversations/<session_id>/regenerate")
    @login_required
    def regenerate_response(session_id: str) -> Any:
        """Regenerate the last AI response"""
        session_obj = storage.get_session(session_id, g.user.id)
        if not session_obj:
            return error_response("Conversation not found", 404)
        
        messages = storage.get_messages(session_id)
        
        # Find last user message
        last_user_msg = None
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_msg = msg
                break
        
        if not last_user_msg:
            return error_response("No user message to regenerate from", 400)
        
        # Remove last assistant message
        if messages and messages[-1].role == "assistant":
            storage.messages[session_id].pop()
        
        # Regenerate using the last user message
        return send_message(session_id)


# ---------------------------------------------------------------------------
# Register routes
# ---------------------------------------------------------------------------
register_conversation_routes(app)

# SECTION 12: Vision and Image Generation Routes
# Image analysis, batch processing, and image generation endpoints

def register_vision_routes(app: Flask) -> None:
    """Register vision and image generation routes on the Flask app"""
    
    # =======================================================================
    # IMAGE ANALYSIS (SINGLE)
    # =======================================================================
    @app.post("/EONIX/vision/analyze")
    @app.post("/ai/analyze-image")
    @login_required
    def analyze_image_route() -> Any:
        """Analyze an image using EONIX Vision"""
        data = json_body()
        session_id = data.get("conversation_id") or data.get("session_id")
        
        # Validate session if provided
        if session_id and not storage.get_session(session_id, g.user.id):
            return error_response("Conversation not found", 404)

        # Parse and validate image
        try:
            image_b64, mime_type = parse_image_payload(data.get("image") or "")
        except ValueError as e:
            return error_response(str(e), 400)

        prompt = Security.sanitize(data.get("prompt") or "Describe this image in detail.")
        
        # Content safety
        safety = Security.safety_check(prompt, g.user.email, g.user.id)
        if not safety["safe"]:
            return error_response(safety["msg"], 403 if safety.get("banned") else 400)
        
        # Rate limiting
        limit_check = check_rate_limit(f"vision:{g.user.id}", 10, 60)
        if limit_check:
            return limit_check

        try:
            # Analyze image
            analysis, used_mode, elapsed, task_type = ai_service.analyze_image(
                image_b64, mime_type, prompt
            )
            
            # Store in session if provided
            if session_id:
                storage.add_message(
                    session_id, "user", f"[Image Analysis] {prompt}",
                    used_mode, task_type=task_type,
                    attachments=[{"type": "image", "mime_type": mime_type}]
                )
                storage.add_message(
                    session_id, "assistant", analysis, used_mode,
                    task_type=task_type
                )
            
            return jsonify({
                "success": True,
                "analysis": analysis,
                "mode": used_mode,
                "task_type": task_type,
                "elapsed": elapsed,
                "mime_type": mime_type,
                "session_id": session_id,
            })
            
        except ProviderUnavailable as e:
            return error_response(str(e), 503)
        except Exception as e:
            logger.error("Vision analysis failed: %s", str(e)[:200])
            return error_response("Image analysis failed", 500)

    # =======================================================================
    # BATCH IMAGE ANALYSIS
    # =======================================================================
    @app.post("/EONIX/vision/batch-analyze")
    @login_required
    def batch_analyze_images() -> Any:
        """Analyze multiple images (max 5)"""
        data = json_body()
        images = data.get("images", [])
        
        if not images or not isinstance(images, list):
            return error_response("Images array required", 400)
        
        if len(images) > 5:
            return error_response("Maximum 5 images per batch", 400)
        
        session_id = data.get("conversation_id") or data.get("session_id")
        prompt = Security.sanitize(data.get("prompt") or "Describe these images.")
        
        # Rate limiting (higher cost)
        limit_check = check_rate_limit(f"vision_batch:{g.user.id}", 3, 120)
        if limit_check:
            return limit_check
        
        results = []
        errors = []
        
        for idx, image_data in enumerate(images):
            try:
                image_b64, mime_type = parse_image_payload(image_data)
                analysis, used_mode, elapsed, task_type = ai_service.analyze_image(
                    image_b64, mime_type, f"{prompt} (Image {idx + 1})"
                )
                results.append({
                    "index": idx,
                    "analysis": analysis,
                    "mode": used_mode,
                    "elapsed": elapsed,
                    "mime_type": mime_type,
                })
            except Exception as e:
                errors.append({"index": idx, "error": str(e)[:200]})
        
        # Store summary in session
        if session_id and results:
            summary_parts = [f"Image {r['index']+1}: {r['analysis'][:150]}..." for r in results]
            summary = f"Batch analysis of {len(results)} images:\n" + "\n".join(summary_parts)
            
            storage.add_message(
                session_id, "assistant", summary[:8000],
                "EONIX-vision", task_type="image_analysis"
            )
        
        return jsonify({
            "success": True,
            "results": results,
            "errors": errors,
            "total": len(results),
            "failed": len(errors),
        })

    # =======================================================================
    # IMAGE GENERATION
    # =======================================================================
    @app.post("/EONIX/forge/create")
    @app.post("/ai/generate-image")
    @login_required
    def generate_image_route() -> Any:
        """Generate an image using EONIX Forge"""
        data = json_body()
        session_id = data.get("conversation_id") or data.get("session_id")
        
        if session_id and not storage.get_session(session_id, g.user.id):
            return error_response("Conversation not found", 404)

        prompt = Security.sanitize(data.get("prompt") or "", 1600)
        if not prompt:
            return error_response("Prompt is required", 400)
        
        aspect_ratio = data.get("aspect_ratio", "1:1")
        valid_ratios = {"1:1", "3:4", "4:3", "9:16", "16:9"}
        if aspect_ratio not in valid_ratios:
            return error_response(f"Invalid aspect ratio. Allowed: {', '.join(valid_ratios)}", 400)
        
        # Content safety (stricter for image generation)
        safety = Security.safety_check(prompt, g.user.email, g.user.id)
        if not safety["safe"]:
            return error_response(safety["msg"], 403 if safety.get("banned") else 400)
        
        # Rate limiting (stricter - image generation is expensive)
        limit_check = check_rate_limit(f"forge:{g.user.id}", 5, 60)
        if limit_check:
            return limit_check

        try:
            image_url, used_mode, elapsed, task_type = ai_service.create_image(
                prompt, aspect_ratio
            )
            
            # Store in session if provided
            if session_id:
                storage.add_message(
                    session_id, "user", f"[Image Generation] {prompt}",
                    used_mode, task_type=task_type
                )
                storage.add_message(
                    session_id, "assistant", f"Generated image: {prompt}",
                    used_mode, task_type=task_type,
                    attachments=[{
                        "type": "generated_image",
                        "url": image_url,
                        "prompt": prompt,
                        "aspect_ratio": aspect_ratio,
                    }]
                )
            
            return jsonify({
                "success": True,
                "image_url": image_url,
                "mode": used_mode,
                "task_type": task_type,
                "elapsed": elapsed,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "session_id": session_id,
            })
            
        except ProviderUnavailable as e:
            return error_response(str(e), 503)
        except Exception as e:
            logger.error("Image generation failed: %s", str(e)[:200])
            return error_response("Image generation failed", 500)

    # =======================================================================
    # IMAGE VARIATIONS
    # =======================================================================
    @app.post("/EONIX/forge/variations")
    @login_required
    def generate_image_variations() -> Any:
        """Generate multiple variations of an image"""
        data = json_body()
        prompt = Security.sanitize(data.get("prompt") or "", 1600)
        
        if not prompt:
            return error_response("Prompt is required", 400)
        
        count = min(data.get("count", 2), 4)  # Max 4 variations
        aspect_ratio = data.get("aspect_ratio", "1:1")
        
        # Rate limiting (stricter for variations)
        limit_check = check_rate_limit(f"forge_var:{g.user.id}", 3, 120)
        if limit_check:
            return limit_check
        
        variations = []
        errors = []
        
        for i in range(count):
            try:
                variation_prompt = f"{prompt} (variation {i + 1})"
                image_url, used_mode, elapsed, task_type = ai_service.create_image(
                    variation_prompt, aspect_ratio
                )
                variations.append({
                    "index": i,
                    "url": image_url,
                    "prompt": variation_prompt,
                    "elapsed": elapsed,
                })
            except Exception as e:
                errors.append({"index": i, "error": str(e)[:200]})
        
        return jsonify({
            "success": True,
            "variations": variations,
            "errors": errors,
            "total": len(variations),
            "failed": len(errors),
            "mode": "EONIX-forge",
        })


# ---------------------------------------------------------------------------
# Register routes
# ---------------------------------------------------------------------------
register_vision_routes(app)

# SECTION 13: Error Handlers, Startup, and Main Entry Point
# Complete application initialization and production server setup

# ---------------------------------------------------------------------------
# App State (shared settings)
# ---------------------------------------------------------------------------
class AppState:
    """Application-wide state and settings"""
    def __init__(self):
        self.settings = {
            "theme": "dark",
            "accent": "cyan",
            "density": "comfortable",
            "message_scale": 14,
            "default_mode": "EONIX-prime",
            "auto_detect_task": True,
            "enforce_task_restrictions": True,
        }
        self.start_time = unix_now()

state = AppState()

# ---------------------------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------------------------
def register_error_handlers(app: Flask) -> None:
    """Register all error handlers on the app"""
    
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"success": False, "error": "Bad request"}), 400
    
    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"success": False, "error": "Authentication required"}), 401
    
    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"success": False, "error": "Forbidden"}), 403
    
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith(("/EONIX/", "/api/", "/ai/")):
            return jsonify({"success": False, "error": "Not found"}), 404
        return index()
    
    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"success": False, "error": "Payload too large"}), 413
    
    @app.errorhandler(429)
    def rate_limited(e):
        return jsonify({"success": False, "error": "Rate limited"}), 429
    
    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Internal server error")
        return jsonify({"success": False, "error": "Internal server error"}), 500
    
    @app.errorhandler(TaskNotAllowedError)
    def task_not_allowed(e):
        logger.warning("Task not allowed: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)}), 400
    
    @app.errorhandler(ProviderUnavailable)
    def provider_unavailable(e):
        logger.warning("Provider unavailable: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)}), 503
    
    @app.errorhandler(ValueError)
    def value_error(e):
        logger.warning("Value error: %s", str(e)[:200])
        return jsonify({"success": False, "error": str(e)}), 400

# ---------------------------------------------------------------------------
# Boot Probes
# ---------------------------------------------------------------------------
def run_boot_probes() -> Dict[str, Any]:
    """Run boot-time probes for all configured models"""
    if not Config.ENABLE_MODEL_BOOT_PROBE:
        logger.info("Boot probes disabled")
        return {"status": "disabled"}
    
    logger.info("🔍 Running model boot probes...")
    results = {}
    passed = 0
    failed = 0
    
    for mode_id, spec in MODE_SPECS.items():
        for candidate in spec.chain:
            key = f"{candidate.provider}/{candidate.model}"
            
            # Skip unconfigured
            if not model_diagnostics.provider_configured(candidate.provider):
                model_diagnostics.record_boot_probe(
                    candidate.provider, candidate.model, False, "Provider key missing"
                )
                results[key] = {"ok": False, "detail": "Provider key missing"}
                failed += 1
                continue
            
            # Skip deprecated
            deprecated = model_diagnostics.deprecated_reason(candidate.provider, candidate.model)
            if deprecated:
                model_diagnostics.record_boot_probe(
                    candidate.provider, candidate.model, False, deprecated
                )
                results[key] = {"ok": False, "detail": deprecated}
                failed += 1
                continue
            
            # Probe the model
            try:
                ok, detail = ai_service.probe_candidate(candidate)
                model_diagnostics.record_boot_probe(
                    candidate.provider, candidate.model, ok, detail
                )
                results[key] = {"ok": ok, "detail": detail}
                if ok:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                model_diagnostics.record_boot_probe(
                    candidate.provider, candidate.model, False, str(e)[:200]
                )
                results[key] = {"ok": False, "detail": str(e)[:200]}
                failed += 1
    
    total = passed + failed
    logger.info("✅ Boot probe complete: %d/%d passed, %d failed", passed, total, failed)
    
    if failed > 0:
        for key, result in results.items():
            if not result["ok"]:
                logger.warning("  ❌ %s: %s", key, result["detail"])
    
    return {
        "status": "completed",
        "total": total,
        "passed": passed,
        "failed": failed,
        "results": results,
    }

# ---------------------------------------------------------------------------
# Startup Tasks
# ---------------------------------------------------------------------------
def startup_tasks() -> None:
    """Run all startup tasks"""
    
    # Display configuration
    Config.display()
    
    # Validate configuration
    if not Config.validate_config():
        logger.error("❌ Configuration validation FAILED")
    else:
        logger.info("✅ Configuration validated")
    
    if not Config.validate_secrets():
        logger.error("❌ Secret validation FAILED")
    else:
        logger.info("✅ Secrets validated")
    
    # Load content safety bans
    ContentSafety.load()
    logger.info("✅ Content safety: %d banned emails", 
                ContentSafety.get_stats()["active_email_bans"])
    
    # Run boot probes
    probe_results = run_boot_probes()
    
    # Log mode availability
    available_modes = []
    unavailable_modes = []
    
    for mode_id in MODE_SPECS:
        valid, issues = validate_model_chain(mode_id)
        if valid:
            available_modes.append(mode_id)
        else:
            unavailable_modes.append(f"{mode_id}: {', '.join(issues)}")
    
    logger.info("Available modes: %s", ", ".join(available_modes) if available_modes else "NONE")
    if unavailable_modes:
        logger.warning("Unavailable modes: %s", "; ".join(unavailable_modes))
    
    # Production readiness check
    if Config.ENVIRONMENT == "production":
        readiness = Config.production_readiness_check()
        failed = [k for k, v in readiness.items() if not v]
        if failed:
            logger.warning("⚠️ Production readiness issues: %s", ", ".join(failed))
        else:
            logger.info("✅ All production checks passed")
    
    # Print banner
    _print_banner(len(available_modes))

def _print_banner(available_count: int) -> None:
    """Print startup banner"""
    banner = f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║    ▄████████  ▄█   ▄█          ▄████████ ███▄▄▄▄        ║
    ║   ███    ███ ███  ███         ███    ███ ███▀▀▀██▄      ║
    ║   ███    █▀  ███▌ ███         ███    █▀  ███   ███      ║
    ║  ▄███▄▄▄     ███▌ ███        ▄███▄▄▄     ███   ███      ║
    ║ ▀▀███▀▀▀     ███▌ ███       ▀▀███▀▀▀     ███   ███      ║
    ║   ███    █▄  ███  ███         ███    █▄  ███   ███      ║
    ║   ███    ███ ███  ███▄▄▄      ███    ███ ███   ███      ║
    ║   ██████████ █▀    ▀▀▀▀▀      ██████████  ▀█   █▀       ║
    ║                                                           ║
    ║   Executive Operational Network for Intelligence          ║
    ║                                                           ║
    ║   Version: {Config.VERSION:<10} Environment: {Config.ENVIRONMENT:<15} ║
    ║   Modes: {available_count:<10} Port: {Config.PORT:<15}        ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    logger.info(banner)

# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Run startup tasks
    startup_tasks()
    
    # Print mode specializations
    logger.info("=== Mode Specializations ===")
    for mode_id in MODE_SPECS:
        spec = ModelSpecialization.get_specialization(mode_id)
        mode_spec = get_mode_spec(mode_id)
        logger.info("  %s (%s): %s", mode_id, mode_spec.icon, spec.get("specialization", "General"))
    
    # Start server
    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve
            logger.info("🚀 Starting production server (Waitress) on 0.0.0.0:%s", Config.PORT)
            serve(app, host="0.0.0.0", port=Config.PORT, threads=8, 
                  connection_limit=1000, channel_timeout=120)
        except ImportError:
            logger.warning("Waitress not installed, falling back to Gunicorn/Flask")
            try:
                from gunicorn.app.base import BaseApplication
                logger.info("🚀 Starting production server (Gunicorn) on 0.0.0.0:%s", Config.PORT)
                app.run(host="0.0.0.0", port=Config.PORT, debug=False, threaded=True)
            except Exception:
                logger.error("No production server available. Install waitress or gunicorn.")
                app.run(host="0.0.0.0", port=Config.PORT, debug=False)
    else:
        logger.info("🚀 Starting development server on 0.0.0.0:%s", Config.PORT)
        app.run(host="0.0.0.0", port=Config.PORT, debug=True, threaded=True)

# ---------------------------------------------------------------------------
# WSGI Entry Point (for Gunicorn)
# ---------------------------------------------------------------------------
# Use: gunicorn "main:app" --workers 4 --bind 0.0.0.0:5000
# At the end of your app.py, replace the startup section with:

# ===========================================================================
# RENDER.COM DEPLOYMENT - Main Entry Points
# ===========================================================================

if __name__ == "__main__":
    # Run startup tasks
    startup_tasks()
    
    # Get port from environment
    port = int(os.environ.get("PORT", 5000))
    
    # Check if we're on Render
    is_render = os.environ.get("RENDER", "false").lower() == "true"
    
    if is_render or Config.ENVIRONMENT == "production":
        # Use Gunicorn-style production server
        try:
            from waitress import serve
            logger.info("🚀 Starting Waitress on 0.0.0.0:%s", port)
            serve(app, host="0.0.0.0", port=port, threads=4)
        except ImportError:
            logger.info("🚀 Starting Flask on 0.0.0.0:%s", port)
            app.run(host="0.0.0.0", port=port, debug=False)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)

# ===========================================================================
# GUNICORN ENTRY POINT (Render uses this)
# ===========================================================================
# The 'app' variable is the Flask application instance
# Render will automatically use: gunicorn app:app
