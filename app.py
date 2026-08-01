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
    VERSION = "1.1"
    ENVIRONMENT = os.environ.get("FLASK_ENV", "production").lower()
    PORT = int(os.environ.get("PORT", "5000"))

    FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://eonix-7nmk.onrender.com").rstrip("/")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", f"{FRONTEND_URL}/login/callback")

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
    JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_urlsafe(48)
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "168"))

    SESSION_COOKIE_SECURE = ENVIRONMENT != "development"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(14 * 1024 * 1024)))
    MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", "8000"))
    MAX_SESSION_TITLE = int(os.environ.get("MAX_SESSION_TITLE", "120"))
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "18"))
    MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", "45"))
    IMAGE_REQUEST_TIMEOUT = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT", "75"))

    RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "24"))
    RATE_LIMIT_SESSIONS = int(os.environ.get("RATE_LIMIT_SESSIONS", "80"))
    RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
    RATE_LIMIT_CLEANUP_INTERVAL = int(os.environ.get("RATE_LIMIT_CLEANUP_INTERVAL", "300"))

    ALLOW_DEV_LOGIN = os.environ.get("ALLOW_DEV_LOGIN", "false").lower() == "true"
    ENABLE_MODEL_BOOT_PROBE = os.environ.get("ENABLE_MODEL_BOOT_PROBE", "false").lower() == "true"
    MODEL_PROBE_TIMEOUT = int(os.environ.get("MODEL_PROBE_TIMEOUT", "12"))

    ADMIN_EMAILS = {
        email.strip().lower()
        for email in os.environ.get("ADMIN_EMAILS", "krish@gmail.com,admin@EONIX.ai").split(",")
        if email.strip()
    }

    # Provider API keys
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    # Provider base URLs
    DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    GEMINI_BASE = os.environ.get(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")

    CORS_ORIGINS = [
        FRONTEND_URL,
        "http://127.0.0.1:8123",
        "http://localhost:8123",
        "http://127.0.0.1:5000",
        "http://localhost:5000",
    ]

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


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def unix_now() -> float:
    return time.time()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
# Security
# ---------------------------------------------------------------------------
class Security:
    HTML_RE = re.compile(r"<[^>]*>")
    BAD_PROTOCOLS = re.compile(r"(?i)\b(javascript|data:text/html)\s*:")

    @staticmethod
    def sanitize(text: str, max_len: int = Config.MAX_MESSAGE_LENGTH) -> str:
        if not text:
            return ""
        clean = text.replace("\x00", "")
        clean = "".join(ch for ch in clean if ch in "\n\t" or ord(ch) >= 32)
        clean = Security.HTML_RE.sub("", clean)
        clean = Security.BAD_PROTOCOLS.sub("", clean)
        return clean.strip()[:max_len]

    @staticmethod
    def title(text: str) -> str:
        clean = Security.sanitize(text, Config.MAX_SESSION_TITLE).replace("\n", " ")
        clean = re.sub(r"\s+", " ", clean).strip()
        return (clean[:56] + "...") if len(clean) > 56 else (clean or "New Conversation")

    @staticmethod
    def token(length: int = 32) -> str:
        return secrets.token_urlsafe(length)

    @staticmethod
    def is_low_signal(text: str) -> bool:
        clean = text.strip()
        if len(clean) < 2:
            return True
        words = clean.lower().split()
        return len(words) > 8 and len(set(words)) <= 2


# ---------------------------------------------------------------------------
# DTOs
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


@dataclass
class MessageDTO:
    id: str
    session_id: str
    role: str
    content: str
    mode: str = "EONIX-prime"
    created_at: float = 0.0
    attachments: List[Dict[str, Any]] = field(default_factory=list)


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


@dataclass(frozen=True)
class ModeSpec:
    id: str
    label: str
    description: str
    chain: Tuple[ModelCandidate, ...]


def c(
    provider: str,
    model: str,
    public_mode: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    thinking: Optional[str] = None,
    supports_text: bool = True,
    supports_vision: bool = False,
    supports_image_generation: bool = False,
) -> ModelCandidate:
    return ModelCandidate(
        provider=provider,
        model=model,
        public_mode=public_mode,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking=thinking,
        supports_text=supports_text,
        supports_vision=supports_vision,
        supports_image_generation=supports_image_generation,
    )


# ---------------------------------------------------------------------------
# Current mode routing
# ---------------------------------------------------------------------------
MODE_SPECS: Dict[str, ModeSpec] = {
    "EONIX-prime": ModeSpec(
        id="EONIX-prime",
        label="EONIX Prime",
        description="Balanced flagship reasoning.",
        chain=(
            c("deepseek", "deepseek-chat", "EONIX-prime", 8192, 0.65, "high"),
            c("gemini", "gemini-2.5-pro", "EONIX-prime", 8192, 0.65),
            c("openrouter", "openai/gpt-4o", "EONIX-prime", 8192, 0.65),
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-prime", 8192, 0.65, supports_vision=True),
            c("groq", "llama-3.3-70b-versatile", "EONIX-prime", 4096, 0.65),
        ),
    ),
    "EONIX-swift": ModeSpec(
        id="EONIX-swift",
        label="EONIX Swift",
        description="Low-latency production answers.",
        chain=(
            c("groq", "llama-3.3-70b-versatile", "EONIX-swift", 4096, 0.55),
            c("gemini", "gemini-2.5-flash", "EONIX-swift", 4096, 0.55),
            c("openrouter", "google/gemini-2.5-flash-lite", "EONIX-swift", 4096, 0.55),
            c("deepseek", "deepseek-chat", "EONIX-swift", 4096, 0.55, "disabled"),
        ),
    ),
    "EONIX-deepcore": ModeSpec(
        id="EONIX-deepcore",
        label="EONIX DeepCore",
        description="Hard reasoning, coding, and analysis.",
        chain=(
            c("deepseek", "deepseek-reasoner", "EONIX-deepcore", 12000, 0.45, "max"),
            c("gemini", "gemini-2.5-pro", "EONIX-deepcore", 12000, 0.45),
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-deepcore", 12000, 0.45, supports_vision=True),
            c("openrouter", "google/gemini-2.5-flash", "EONIX-deepcore", 8192, 0.45),
            c("openrouter", "x-ai/grok-2-1212", "EONIX-deepcore", 8192, 0.45),
        ),
    ),
    "EONIX-oracle": ModeSpec(
        id="EONIX-oracle",
        label="EONIX Oracle",
        description="Broad multi-provider synthesis.",
        chain=(
            c("openrouter", "openai/gpt-4o", "EONIX-oracle", 12000, 0.6, supports_vision=True),
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-oracle", 12000, 0.6, supports_vision=True),
            c("gemini", "gemini-2.5-pro", "EONIX-oracle", 12000, 0.6),
            c("deepseek", "deepseek-chat", "EONIX-oracle", 8192, 0.6, "high"),
            c("groq", "llama-3.3-70b-versatile", "EONIX-oracle", 4096, 0.6),
        ),
    ),
    "EONIX-vision": ModeSpec(
        id="EONIX-vision",
        label="EONIX Vision",
        description="Image and multimodal understanding.",
        chain=(
            c("gemini", "gemini-2.5-pro", "EONIX-vision", 8192, 0.4, supports_vision=True),
            c("gemini", "gemini-2.5-flash", "EONIX-vision", 8192, 0.4, supports_vision=True),
            c("openrouter", "openai/gpt-4o", "EONIX-vision", 8192, 0.4, supports_vision=True),
            c("openrouter", "anthropic/claude-3.5-sonnet", "EONIX-vision", 8192, 0.4, supports_vision=True),
        ),
    ),
    "EONIX-forge": ModeSpec(
        id="EONIX-forge",
        label="EONIX Forge",
        description="Image creation.",
        chain=(
            c("gemini-image", "imagen-4.0-ultra-generate-001", "EONIX-forge", supports_text=False, supports_image_generation=True),
            c("gemini-image", "imagen-4.0-generate-001", "EONIX-forge", supports_text=False, supports_image_generation=True),
            c("gemini-image", "imagen-4.0-fast-generate-001", "EONIX-forge", supports_text=False, supports_image_generation=True),
            c("gemini-image", "imagen-3.0-generate-002", "EONIX-forge", supports_text=False, supports_image_generation=True),
            c("gemini-image", "imagen-3.0-fast-generate-002", "EONIX-forge", supports_text=False, supports_image_generation=True)
        ),
    ),
    "EONIX-knowledge": ModeSpec(
    id="EONIX-knowledge",
    label="EONIX Knowledge",
    description="General knowledge, facts, and information retrieval.",
    chain=(
        c("gemini", "gemini-2.5-pro", "EONIX-knowledge", 8192, 0.5),
        c("openrouter", "openai/gpt-4o", "EONIX-knowledge", 8192, 0.5),
        c("deepseek", "deepseek-chat", "EONIX-knowledge", 4096, 0.5, "high"),
        c("groq", "llama-3.3-70b-versatile", "EONIX-knowledge", 4096, 0.5),
    ),
),
}

MODE_ALIASES = {
    "prime": "EONIX-prime",
    "swift": "EONIX-swift",
    "deepcore": "EONIX-deepcore",
    "oracle": "EONIX-oracle",
    "vision": "EONIX-vision",
    "forge": "EONIX-forge",
    "image-gen": "EONIX-forge",
    "deepseek": "EONIX-deepcore",
    "groq": "EONIX-swift",
    "gemini": "EONIX-prime",
    "openrouter": "EONIX-oracle",
     "knowledge": "EONIX-knowledge",      # ← ADD THIS
    "facts": "EONIX-knowledge",           # ← ADD THIS
    "gk": "EONIX-knowledge", 
}


def normalize_mode(value: Optional[str]) -> str:
    if not value:
        return "EONIX-prime"
    clean = value.strip().lower()
    return clean if clean in MODE_SPECS else MODE_ALIASES.get(clean, "EONIX-prime")


# ---------------------------------------------------------------------------
# Model diagnostics
# ---------------------------------------------------------------------------
class ModelDiagnostics:
    DEPRECATED_MODELS = {
        ("openrouter", "google/gemini-2.5-pro-exp-03-25"): "Deprecated by Google in favor of newer Gemini 2.5 Pro variants.",
        ("groq", "mixtral-8x7b-32768"): "Deprecated by Groq in March 2025.",
        ("openrouter", "google/gemini-2.0-flash-001"): "Going away; prefer Gemini 2.5 Flash or Flash-Lite.",
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_errors: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._boot_probe: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def provider_configured(self, provider: str) -> bool:
        if provider == "deepseek":
            return bool(Config.DEEPSEEK_KEY)
        if provider == "groq":
            return bool(Config.GROQ_KEY)
        if provider in {"gemini", "gemini-image"}:
            return bool(Config.GEMINI_KEY)
        if provider == "openrouter":
            return bool(Config.OPENROUTER_KEY)
        return False

    def deprecated_reason(self, provider: str, model: str) -> Optional[str]:
        return self.DEPRECATED_MODELS.get((provider, model))

    def record_failure(self, provider: str, model: str, error: Exception | str) -> None:
        with self._lock:
            self._last_errors[(provider, model)] = {
                "error": str(error),
                "timestamp": iso_now(),
            }

    def record_success(self, provider: str, model: str) -> None:
        with self._lock:
            self._last_errors.pop((provider, model), None)

    def record_boot_probe(self, provider: str, model: str, ok: bool, detail: str) -> None:
        with self._lock:
            self._boot_probe[(provider, model)] = {
                "ok": ok,
                "detail": detail,
                "timestamp": iso_now(),
            }

    def candidate_report(self, candidate: ModelCandidate) -> Dict[str, Any]:
        deprecated = self.deprecated_reason(candidate.provider, candidate.model)
        last_error = self._last_errors.get((candidate.provider, candidate.model))
        boot_probe = self._boot_probe.get((candidate.provider, candidate.model))
        return {
            "provider": candidate.provider,
            "model": candidate.model,
            "configured": self.provider_configured(candidate.provider),
            "deprecated": bool(deprecated),
            "deprecation_reason": deprecated,
            "supports_text": candidate.supports_text,
            "supports_vision": candidate.supports_vision,
            "supports_image_generation": candidate.supports_image_generation,
            "last_error": last_error,
            "boot_probe": boot_probe,
        }

    def mode_report(self, mode_id: str) -> Dict[str, Any]:
        spec = MODE_SPECS[mode_id]
        candidates = [self.candidate_report(candidate) for candidate in spec.chain]
        available = any(item["configured"] and not item["deprecated"] for item in candidates)
        return {
            "id": spec.id,
            "label": spec.label,
            "description": spec.description,
            "available": available,
            "candidates": candidates,
        }

    def all_modes_report(self) -> Dict[str, Any]:
        modes = [self.mode_report(mode_id) for mode_id in MODE_SPECS]
        return {
            "generated_at": iso_now(),
            "modes": modes,
        }


model_diagnostics = ModelDiagnostics()


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class SlidingWindowLimiter:
    def __init__(self) -> None:
        self.events: Dict[str, Deque[float]] = defaultdict(deque)
        self.lock = threading.RLock()
        self.last_cleanup = unix_now()

    def check(self, key: str, limit: int, window: int) -> Tuple[bool, int, float]:
        now = unix_now()
        cutoff = now - window
        with self.lock:
            if now - self.last_cleanup >= Config.RATE_LIMIT_CLEANUP_INTERVAL:
                self.cleanup(now)
                self.last_cleanup = now

            bucket = self.events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                return False, 0, bucket[0] + window

            bucket.append(now)
            return True, max(limit - len(bucket), 0), now + window

    def cleanup(self, now: Optional[float] = None) -> None:
        current = now or unix_now()
        cutoff = current - Config.RATE_LIMIT_WINDOW
        stale = []
        for key, bucket in self.events.items():
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                stale.append(key)
        for key in stale:
            del self.events[key]
        if stale:
            logger.debug("Rate limiter cleaned up %d stale keys", len(stale))


rate_limiter = SlidingWindowLimiter()


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
class JWTService:
    @staticmethod
    def create(user: UserDTO) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "user_id": user.id,
            "email": user.email,
            "is_admin": user.is_admin,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=Config.JWT_EXPIRY_HOURS)).timestamp()),
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)

    @staticmethod
    def verify(token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        try:
            data = jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
            return data if isinstance(data, dict) else None
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return None

    @staticmethod
    def from_request() -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth.split(" ", 1)[1].strip()
        return request.args.get("token") or session.get("session_token")


# ---------------------------------------------------------------------------
# In-memory storage
# ---------------------------------------------------------------------------
class Storage:
    def __init__(self) -> None:
        self.users: Dict[str, UserDTO] = {}
        self.users_by_email: Dict[str, str] = {}
        self.users_by_google: Dict[str, str] = {}
        self.sessions: Dict[str, SessionDTO] = {}
        self.messages: Dict[str, List[MessageDTO]] = defaultdict(list)
        self.user_sessions: Dict[str, List[str]] = defaultdict(list)
        self.lock = threading.RLock()

    def create_or_update_user(
        self,
        google_id: Optional[str],
        email: str,
        name: str,
        avatar: str = "",
    ) -> UserDTO:
        clean_email = (email or "").lower().strip()
        if not clean_email:
            raise ValueError("Email is required")

        with self.lock:
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

            user = UserDTO(
                id=new_id("usr"),
                google_id=google_id,
                email=clean_email,
                display_name=name or clean_email.split("@")[0],
                avatar_url=avatar or "",
                is_admin=clean_email in Config.ADMIN_EMAILS,
                created_at=unix_now(),
                last_login=unix_now(),
            )
            self.users[user.id] = user
            self.users_by_email[clean_email] = user.id
            if google_id:
                self.users_by_google[google_id] = user.id
            return user

    def get_user(self, user_id: str) -> Optional[UserDTO]:
        return self.users.get(user_id)

    def set_session_token(self, user_id: str, token: Optional[str]) -> None:
        with self.lock:
            if user_id in self.users:
                self.users[user_id].session_token = token

    def create_session(self, user_id: str, title: str = "New Conversation") -> SessionDTO:
        now = unix_now()
        with self.lock:
            session_obj = SessionDTO(
                id=new_id("ses"),
                user_id=user_id,
                title=Security.title(title),
                created_at=now,
                updated_at=now,
            )
            self.sessions[session_obj.id] = session_obj
            self.user_sessions[user_id].append(session_obj.id)
            return session_obj

    def get_session(self, session_id: str, user_id: str) -> Optional[SessionDTO]:
        session_obj = self.sessions.get(session_id)
        if not session_obj or session_obj.user_id != user_id:
            return None
        return session_obj

    def get_user_sessions(self, user_id: str) -> List[SessionDTO]:
        session_ids = list(self.user_sessions.get(user_id, []))
        sessions = [self.sessions[sid] for sid in session_ids if sid in self.sessions]
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def delete_session(self, session_id: str, user_id: str) -> bool:
        with self.lock:
            session_obj = self.sessions.get(session_id)
            if not session_obj or session_obj.user_id != user_id:
                return False
            self.sessions.pop(session_id, None)
            self.messages.pop(session_id, None)
            if session_id in self.user_sessions.get(user_id, []):
                self.user_sessions[user_id].remove(session_id)
            return True

    def update_title(self, session_id: str, title: str) -> None:
        with self.lock:
            session_obj = self.sessions.get(session_id)
            if session_obj:
                session_obj.title = Security.title(title)
                session_obj.updated_at = unix_now()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        mode: str = "EONIX-prime",
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> MessageDTO:
        with self.lock:
            if session_id not in self.sessions:
                raise KeyError("Session not found")
            message = MessageDTO(
                id=new_id("msg"),
                session_id=session_id,
                role=role,
                content=Security.sanitize(content),
                mode=normalize_mode(mode),
                created_at=unix_now(),
                attachments=list(attachments or []),
            )
            self.messages[session_id].append(message)
            self.sessions[session_id].updated_at = unix_now()
            return message

    def get_messages(self, session_id: str) -> List[MessageDTO]:
        return sorted(self.messages.get(session_id, []), key=lambda message: message.created_at)

    def count_messages(self, session_id: str) -> int:
        return len(self.messages.get(session_id, []))


storage = Storage()


# ---------------------------------------------------------------------------
# AI service
# ---------------------------------------------------------------------------
class ProviderUnavailable(Exception):
    pass


class AIService:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.system_prompt = (
            "You are EONIX, an advanced assistant created by Krish Paliwal. "
            "Be accurate, direct, professional, cheery, cute, happy and useful. Structure answers clearly. "
            "If something is uncertain, say so. Do not mention private provider routing, "
            "API keys, or internal model names unless the operator explicitly asks for backend diagnostics."
        )

    def generate(
        self,
        prompt: str,
        preferred_mode: Optional[str],
        history: Iterable[MessageDTO] = (),
    ) -> Tuple[str, str, float]:
        started = unix_now()
        clean_prompt = Security.sanitize(prompt)
        if Security.is_low_signal(clean_prompt):
            return "Please send a clearer message so I can help properly.", "EONIX-prime", 0.0

        mode_id = normalize_mode(preferred_mode)
        messages = self._messages(clean_prompt, list(history))
        chain = MODE_SPECS[mode_id].chain
        errors: List[str] = []

        for candidate in chain:
            try:
                if not model_diagnostics.provider_configured(candidate.provider):
                    raise ProviderUnavailable(f"{candidate.provider} key missing")
                if model_diagnostics.deprecated_reason(candidate.provider, candidate.model):
                    raise ProviderUnavailable("model is marked deprecated")
                text = self._dispatch_text(candidate, messages)
                if text:
                    model_diagnostics.record_success(candidate.provider, candidate.model)
                    elapsed = round(unix_now() - started, 2)
                    logger.info(
                        "EONIX mode=%s provider=%s model=%s elapsed=%ss",
                        mode_id,
                        candidate.provider,
                        candidate.model,
                        elapsed,
                    )
                    return text, mode_id, elapsed
            except Exception as exc:
                model_diagnostics.record_failure(candidate.provider, candidate.model, exc)
                errors.append(f"{candidate.provider}:{candidate.model}:{exc}")
                logger.warning(
                    "Candidate failed mode=%s provider=%s model=%s error=%s",
                    mode_id,
                    candidate.provider,
                    candidate.model,
                    exc,
                )

        elapsed = round(unix_now() - started, 2)
        logger.error("All candidates failed for mode=%s errors=%s", mode_id, " | ".join(errors[-6:]))
        return self._offline_reply(clean_prompt), mode_id, elapsed

    def analyze_image(self, image_b64: str, mime_type: str, prompt: str = "") -> Tuple[str, str, float]:
        started = unix_now()
        clean_prompt = Security.sanitize(prompt or "Describe this image in detail.")
        for candidate in MODE_SPECS["EONIX-vision"].chain:
            if not candidate.supports_vision:
                continue
            try:
                if not model_diagnostics.provider_configured(candidate.provider):
                    raise ProviderUnavailable(f"{candidate.provider} key missing")
                if candidate.provider == "gemini":
                    text = self._gemini_vision(candidate, image_b64, mime_type, clean_prompt)
                elif candidate.provider == "openrouter":
                    data_url = f"data:{mime_type};base64,{image_b64}"
                    text = self._openrouter_vision(candidate, data_url, clean_prompt)
                else:
                    continue
                if text:
                    model_diagnostics.record_success(candidate.provider, candidate.model)
                    return text, "EONIX-vision", round(unix_now() - started, 2)
            except Exception as exc:
                model_diagnostics.record_failure(candidate.provider, candidate.model, exc)
                logger.warning(
                    "Vision candidate failed provider=%s model=%s error=%s",
                    candidate.provider,
                    candidate.model,
                    exc,
                )
        raise ProviderUnavailable(
            "EONIX Vision is unavailable. Check model access."
        )

    def create_image(self, prompt: str, aspect_ratio: str = "1:1") -> Tuple[str, str, float]:
        started = unix_now()
        clean_prompt = Security.sanitize(prompt, 1600)
        if not clean_prompt:
            raise ValueError("Prompt is required")

        for candidate in MODE_SPECS["EONIX-forge"].chain:
            if not candidate.supports_image_generation:
                continue
            try:
                if not model_diagnostics.provider_configured(candidate.provider):
                    raise ProviderUnavailable(f"{candidate.provider} key missing")
                image_url = self._imagen(candidate.model, clean_prompt, aspect_ratio)
                if image_url:
                    model_diagnostics.record_success(candidate.provider, candidate.model)
                    return image_url, "EONIX-forge", round(unix_now() - started, 2)
            except Exception as exc:
                model_diagnostics.record_failure(candidate.provider, candidate.model, exc)
                logger.warning("Forge candidate failed model=%s error=%s", candidate.model, exc)

        raise ProviderUnavailable(
            "EONIX Forge is unavailable. Check Imagen model access."
        )

    def probe_candidate(self, candidate: ModelCandidate) -> Tuple[bool, str]:
        try:
            if candidate.supports_image_generation:
                self._imagen(candidate.model, "simple abstract sphere", "1:1")
                return True, "image generation probe passed"
            if candidate.supports_vision:
                sample_png = (
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO0pNxsAAAAASUVORK5CYII="
                )
                self.analyze_image(sample_png, "image/png", "What is visible in this image?")
                return True, "vision probe passed"
            text = self._dispatch_text(candidate, self._messages("Reply with the word READY.", []))
            return (bool(text), "text probe passed" if text else "empty response")
        except Exception as exc:
            return False, str(exc)

    def _messages(self, prompt: str, history: List[MessageDTO]) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        recent = [item for item in history if item.role in {"user", "assistant"}][-Config.MAX_HISTORY_MESSAGES :]
        for item in recent:
            messages.append({"role": item.role, "content": item.content})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _dispatch_text(self, candidate: ModelCandidate, messages: List[Dict[str, str]]) -> Optional[str]:
        if not candidate.supports_text:
            return None
        if candidate.provider == "deepseek":
            return self._openai_compatible(
                base_url=Config.DEEPSEEK_BASE,
                api_key=Config.DEEPSEEK_KEY or "",
                candidate=candidate,
                messages=messages,
                provider_headers={},
                deepseek=True,
            )
        if candidate.provider == "groq":
            return self._openai_compatible(
                base_url=Config.GROQ_BASE,
                api_key=Config.GROQ_KEY or "",
                candidate=candidate,
                messages=messages,
                provider_headers={},
            )
        if candidate.provider == "openrouter":
            return self._openai_compatible(
                base_url=Config.OPENROUTER_BASE,
                api_key=Config.OPENROUTER_KEY or "",
                candidate=candidate,
                messages=messages,
                provider_headers={
                    "HTTP-Referer": Config.FRONTEND_URL,
                    "X-Title": Config.APP_NAME,
                },
            )
        if candidate.provider == "gemini":
            return self._gemini_text(candidate, messages)
        return None

    def _openai_compatible(
        self,
        base_url: str,
        api_key: str,
        candidate: ModelCandidate,
        messages: List[Dict[str, str]],
        provider_headers: Dict[str, str],
        deepseek: bool = False,
    ) -> Optional[str]:
        payload: Dict[str, Any] = {
            "model": candidate.model,
            "messages": messages,
            "max_tokens": candidate.max_tokens,
            "temperature": candidate.temperature,
        }
        if deepseek and candidate.thinking:
            payload["thinking"] = {"type": "disabled" if candidate.thinking == "disabled" else "enabled"}
            if candidate.thinking in {"high", "max"}:
                payload["reasoning_effort"] = candidate.thinking

        response = self.session.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **provider_headers,
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        data = safe_json(response)
        choice = (data.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("provider returned empty content")
        return content

    def _gemini_text(self, candidate: ModelCandidate, messages: List[Dict[str, str]]) -> Optional[str]:
        system_text = self.system_prompt
        contents = []
        for item in messages:
            if item["role"] == "system":
                system_text = item["content"]
                continue
            contents.append(
                {
                    "role": "model" if item["role"] == "assistant" else "user",
                    "parts": [{"text": item["content"]}],
                }
            )

        payload = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": {
                "temperature": candidate.temperature,
                "maxOutputTokens": candidate.max_tokens,
            },
        }

        response = self.session.post(
            f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        text = self._gemini_text_from_response(safe_json(response))
        if not text:
            raise RuntimeError("provider returned empty content")
        return text

    def _gemini_vision(
        self,
        candidate: ModelCandidate,
        image_b64: str,
        mime_type: str,
        prompt: str,
    ) -> Optional[str]:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": candidate.max_tokens,
            },
        }
        response = self.session.post(
            f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        text = self._gemini_text_from_response(safe_json(response))
        if not text:
            raise RuntimeError("provider returned empty content")
        return text

    def _openrouter_vision(self, candidate: ModelCandidate, data_url: str, prompt: str) -> Optional[str]:
        payload = {
            "model": candidate.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
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
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        data = safe_json(response)
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("provider returned empty content")
        return content

    def _imagen(self, model: str, prompt: str, aspect_ratio: str) -> Optional[str]:
        safe_ratio = aspect_ratio if aspect_ratio in {"1:1", "3:4", "4:3", "9:16", "16:9"} else "1:1"
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
            raise RuntimeError(self._error_text(response))
        data = safe_json(response)
        predictions = data.get("predictions") or []
        if not predictions:
            raise RuntimeError("provider returned no image")
        first = predictions[0]
        encoded = (
            first.get("bytesBase64Encoded")
            or (first.get("image") or {}).get("bytesBase64Encoded")
            or first.get("imageBytes")
        )
        if not encoded:
            raise RuntimeError("provider returned malformed image payload")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _gemini_text_from_response(data: Dict[str, Any]) -> Optional[str]:
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
        return text.strip() or None

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        data = safe_json(response)
        return (
            (data.get("error") or {}).get("message")
            if isinstance(data.get("error"), dict)
            else data.get("error")
            or response.text[:500]
        )

    @staticmethod
    def _offline_reply(prompt: str) -> str:
        lower = prompt.lower()
        if any(word in lower for word in ("hello", "hi", "hey")):
            return "Hello. EONIX is online, but the live model network is temporarily unavailable."
        return (
            "EONIX could not reach the live model network for this request. "
            "Check provider keys, account access, and current model availability, then try again."
        )


ai_service = AIService()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT))
app.secret_key = Config.SECRET_KEY
app.config.update(
    SECRET_KEY=Config.SECRET_KEY,
    MAX_CONTENT_LENGTH=Config.MAX_CONTENT_LENGTH,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app, origins=Config.CORS_ORIGINS, supports_credentials=True)


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
    }


def serialize_session(session_obj: SessionDTO) -> Dict[str, Any]:
    data = asdict(session_obj)
    data["createdAt"] = current_iso_from_ts(session_obj.created_at)
    data["updatedAt"] = current_iso_from_ts(session_obj.updated_at)
    return data


def serialize_message(message: MessageDTO) -> Dict[str, Any]:
    data = asdict(message)
    data["model_used"] = message.mode
    data["createdAt"] = current_iso_from_ts(message.created_at)
    return data


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def current_user_from_token() -> Optional[UserDTO]:
    token = JWTService.from_request()
    if token:
        payload = JWTService.verify(token)
        if payload:
            user = storage.get_user(payload.get("user_id", ""))
            if user and user.session_token is not None and user.session_token == token:
                return user

    session_user_id = session.get("user_id")
    session_token = session.get("session_token")
    if session_user_id and session_token:
        user = storage.get_user(session_user_id)
        if user and user.session_token is not None and user.session_token == session_token:
            return user
    return None


def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = current_user_from_token()
        if not user:
            return jsonify({"success": False, "error": "Authentication required"}), 401
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


def limit_or_429(key: str, limit: int, window: int) -> Optional[Tuple[Any, int]]:
    allowed, remaining, reset = rate_limiter.check(key, limit, window)
    if allowed:
        g.rate_remaining = remaining
        return None
    retry_after = max(round(reset - unix_now(), 2), 0)
    return jsonify({"success": False, "error": "Rate limited", "retry_after": retry_after}), 429


def parse_image_payload(value: str) -> Tuple[str, str]:
    if not value:
        raise ValueError("No image provided")

    clean = value.strip()
    mime = "image/png"
    if clean.startswith("data:"):
        header, _, encoded = clean.partition(",")
        match = re.match(r"data:([^;]+);base64", header)
        if match:
            mime = match.group(1)
        clean = encoded

    raw = base64.b64decode(clean, validate=True)
    if len(raw) > Config.MAX_IMAGE_BYTES:
        raise ValueError("Image is too large")
    if not mime.startswith("image/"):
        raise ValueError("Unsupported image type")
    return base64.b64encode(raw).decode("ascii"), mime


# ---------------------------------------------------------------------------
# Routes: static and health
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> Any:
    filename = "jarvis-enterprise-v5.html" if (ROOT / "jarvis-enterprise-v5.html").exists() else "index.html"
    return send_from_directory(ROOT, filename)


@app.get("/health")
@app.get("/EONIX/health")
def health() -> Any:
    return jsonify(
        {
            "success": True,
            "status": "healthy",
            "version": Config.VERSION,
            "time": iso_now(),
            "modes": [
                {"id": spec.id, "label": spec.label, "description": spec.description}
                for spec in MODE_SPECS.values()
            ],
        }
    )


@app.get("/EONIX/modes")
def modes() -> Any:
    return jsonify(
        {
            "success": True,
            "modes": [
                {"id": spec.id, "label": spec.label, "description": spec.description}
                for spec in MODE_SPECS.values()
            ],
        }
    )


@app.get("/EONIX/diagnostics/modes")
@admin_required
def mode_diagnostics() -> Any:
    return jsonify({"success": True, **model_diagnostics.all_modes_report()})


# ---------------------------------------------------------------------------
# Auth: Google OAuth
# ---------------------------------------------------------------------------
@app.get("/EONIX/sign-in")
@app.get("/login/google")
def google_login() -> Any:
    if Config.ENVIRONMENT == "development" and Config.ALLOW_DEV_LOGIN and not Config.GOOGLE_CLIENT_ID:
        user = storage.create_or_update_user(None, "operator@EONIX.local", "Operator")
        token = JWTService.create(user)
        storage.set_session_token(user.id, token)
        session["user_id"] = user.id
        session["session_token"] = token
        return redirect(f"{Config.FRONTEND_URL}?token={token}")

    if not (Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET):
        return redirect(f"{Config.FRONTEND_URL}?error=OAuth+not+configured")

    state = Security.token(32)
    session["oauth_state"] = state
    params = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": Config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")


@app.get("/login/callback")
@app.get("/EONIX/auth/callback")
def google_callback() -> Any:
    if request.args.get("error"):
        return redirect(f"{Config.FRONTEND_URL}?error={request.args.get('error')}")

    code = request.args.get("code")
    state = request.args.get("state")
    saved_state = session.pop("oauth_state", None)
    if not code:
        return redirect(f"{Config.FRONTEND_URL}?error=No+code")
    if saved_state and state != saved_state:
        return redirect(f"{Config.FRONTEND_URL}?error=Invalid+state")

    try:
        token_response = requests.post(
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
        token_response.raise_for_status()
        access_token = safe_json(token_response).get("access_token")
        if not access_token:
            raise RuntimeError("OAuth token exchange returned no access token")

        user_response = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        user_response.raise_for_status()
        info = safe_json(user_response)

        user = storage.create_or_update_user(
            google_id=info.get("id"),
            email=info.get("email", ""),
            name=info.get("name", ""),
            avatar=info.get("picture", ""),
        )
        token = JWTService.create(user)
        storage.set_session_token(user.id, token)
        session["user_id"] = user.id
        session["session_token"] = token
        return redirect(f"{Config.FRONTEND_URL}?token={token}")
    except Exception as exc:
        logger.error("OAuth failed: %s", exc)
        return redirect(f"{Config.FRONTEND_URL}?error=Auth+failed")


@app.get("/EONIX/sign-out")
@app.get("/logout")
def logout() -> Any:
    user_id = session.get("user_id")
    if user_id:
        storage.set_session_token(user_id, None)
    session.clear()
    return redirect(Config.FRONTEND_URL)


@app.get("/EONIX/me")
@app.get("/api/me")
def me() -> Any:
    user = current_user_from_token()
    if not user:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    return jsonify({"success": True, "user": serialize_user(user), "token": JWTService.from_request()})


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@app.get("/EONIX/conversations")
@app.get("/ai/sessions")
@login_required
def list_conversations() -> Any:
    limited = limit_or_429(f"sessions:{g.user.id}", Config.RATE_LIMIT_SESSIONS, Config.RATE_LIMIT_WINDOW)
    if limited:
        return limited
    sessions = [serialize_session(item) for item in storage.get_user_sessions(g.user.id)]
    return jsonify({"success": True, "conversations": sessions, "sessions": sessions})


@app.post("/EONIX/conversations")
@app.post("/ai/session")
@login_required
def create_conversation() -> Any:
    data = json_body()
    session_obj = storage.create_session(g.user.id, data.get("title") or "New Conversation")
    payload = serialize_session(session_obj)
    return jsonify(
        {
            "success": True,
            "conversation": payload,
            "session": payload,
            "conversation_id": session_obj.id,
            "session_id": session_obj.id,
        }
    )


@app.get("/EONIX/conversations/<session_id>")
@app.get("/ai/session/<session_id>")
@login_required
def get_conversation(session_id: str) -> Any:
    session_obj = storage.get_session(session_id, g.user.id)
    if not session_obj:
        return jsonify({"success": False, "error": "Conversation not found"}), 404
    messages = [serialize_message(item) for item in storage.get_messages(session_id)]
    payload = serialize_session(session_obj)
    return jsonify({"success": True, "conversation": payload, "session": payload, "messages": messages})


@app.delete("/EONIX/conversations/<session_id>")
@app.delete("/ai/session/<session_id>")
@login_required
def delete_conversation(session_id: str) -> Any:
    if storage.delete_session(session_id, g.user.id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Conversation not found"}), 404


@app.post("/EONIX/conversations/<session_id>/messages")
@app.post("/ai/session/<session_id>/message")
@login_required
def send_message_route(session_id: str) -> Any:
    limited = limit_or_429(f"msg:{g.user.id}", Config.RATE_LIMIT_MESSAGES, Config.RATE_LIMIT_WINDOW)
    if limited:
        return limited

    session_obj = storage.get_session(session_id, g.user.id)
    if not session_obj:
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    data = json_body()
    prompt = Security.sanitize(data.get("prompt") or data.get("message") or "")
    mode = normalize_mode(data.get("mode") or data.get("model"))
    if not prompt:
        return jsonify({"success": False, "error": "Empty message"}), 400

    prior_messages = storage.get_messages(session_id)
    storage.add_message(session_id, "user", prompt, mode)
    response_text, public_mode, elapsed = ai_service.generate(prompt, mode, prior_messages)
    assistant = storage.add_message(session_id, "assistant", response_text, public_mode)

    if storage.count_messages(session_id) <= 2:
        storage.update_title(session_id, prompt)

    return jsonify(
        {
            "success": True,
            "response": response_text,
            "message": serialize_message(assistant),
            "mode": public_mode,
            "model": public_mode,
            "response_time": elapsed,
            "is_first_message": storage.count_messages(session_id) <= 2,
            "remaining_requests": getattr(g, "rate_remaining", None),
        }
    )


# ---------------------------------------------------------------------------
# Vision and image generation
# ---------------------------------------------------------------------------
@app.post("/EONIX/vision/analyze")
@app.post("/ai/analyze-image")
@login_required
def analyze_image_route() -> Any:
    data = json_body()
    session_id = data.get("conversation_id") or data.get("session_id")
    if session_id and not storage.get_session(session_id, g.user.id):
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    try:
        image_b64, mime_type = parse_image_payload(data.get("image") or "")
        prompt = data.get("prompt") or "Describe this image in detail."
        analysis, mode, elapsed = ai_service.analyze_image(image_b64, mime_type, prompt)
        if session_id:
            storage.add_message(session_id, "assistant", analysis, mode)
        return jsonify(
            {
                "success": True,
                "analysis": analysis,
                "response": analysis,
                "mode": mode,
                "model": mode,
                "response_time": elapsed,
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("Vision failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 503


@app.post("/EONIX/forge/create")
@app.post("/ai/generate-image")
@login_required
def generate_image_route() -> Any:
    data = json_body()
    session_id = data.get("conversation_id") or data.get("session_id")
    if session_id and not storage.get_session(session_id, g.user.id):
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    prompt = Security.sanitize(data.get("prompt") or "", 1600)
    if not prompt:
        return jsonify({"success": False, "error": "No prompt provided"}), 400

    try:
        image_url, mode, elapsed = ai_service.create_image(
            prompt,
            data.get("aspect_ratio") or data.get("aspectRatio") or "1:1",
        )
        if session_id:
            storage.add_message(
                session_id,
                "assistant",
                f"Image created: {prompt}",
                mode,
                [{"url": image_url, "caption": prompt, "alt": prompt}],
            )
        return jsonify(
            {
                "success": True,
                "image_url": image_url,
                "url": image_url,
                "mode": mode,
                "model": mode,
                "caption": f"Image created: {prompt}",
                "response_time": elapsed,
            }
        )
    except Exception as exc:
        logger.error("Image generation failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 503


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_: Exception) -> Any:
    if request.path.startswith(("/EONIX/", "/api/", "/ai/")):
        return jsonify({"success": False, "error": "Not found"}), 404
    return index()


@app.errorhandler(413)
def too_large(_: Exception) -> Any:
    return jsonify({"success": False, "error": "Payload too large"}), 413


@app.errorhandler(500)
def server_error(exc: Exception) -> Any:
    logger.exception("Server error: %s", exc)
    return jsonify({"success": False, "error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Optional boot probes
# ---------------------------------------------------------------------------
def run_boot_probes() -> None:
    if not Config.ENABLE_MODEL_BOOT_PROBE:
        return

    logger.info("Running model boot probes")
    for spec in MODE_SPECS.values():
        for candidate in spec.chain:
            if not model_diagnostics.provider_configured(candidate.provider):
                model_diagnostics.record_boot_probe(candidate.provider, candidate.model, False, "provider key missing")
                continue
            if model_diagnostics.deprecated_reason(candidate.provider, candidate.model):
                model_diagnostics.record_boot_probe(
                    candidate.provider,
                    candidate.model,
                    False,
                    model_diagnostics.deprecated_reason(candidate.provider, candidate.model) or "deprecated",
                )
                continue
            ok, detail = ai_service.probe_candidate(candidate)
            model_diagnostics.record_boot_probe(candidate.provider, candidate.model, ok, detail)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    Config.display()
    run_boot_probes()
    logger.info("Mode availability=%s", model_diagnostics.all_modes_report())

    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve

            serve(app, host="0.0.0.0", port=Config.PORT, threads=8)
        except ImportError:
            app.run(host="0.0.0.0", port=Config.PORT, debug=False)
    else:
        app.run(host="0.0.0.0", port=Config.PORT, debug=True)
