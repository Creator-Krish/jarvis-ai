"""
JARVIS Enterprise Backend v5.0 — Multi-Fallback AI with Google OAuth
In-memory storage, branded public modes, private provider fallback chains.
APIs: Gemini | DeepSeek | Groq | OpenRouter

FIXES APPLIED:
1. Critical: Token validation properly invalidated on logout
2. Rate limiter memory leak fixed (periodic cleanup)
3. Image provider routing guarded
4. Session management improved
"""

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
from dataclasses import asdict, dataclass
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
logger = logging.getLogger("jarvis")
logging.getLogger("werkzeug").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class Config:
    APP_NAME = "JARVIS Enterprise"
    VERSION = "5.0"
    ENVIRONMENT = os.environ.get("FLASK_ENV", "production").lower()
    PORT = int(os.environ.get("PORT", "5000"))

    FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://jarvis-e76i.onrender.com").rstrip("/")
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
    MAX_SESSION_TITLE = 120
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "18"))
    MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", "45"))

    RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "24"))
    RATE_LIMIT_SESSIONS = int(os.environ.get("RATE_LIMIT_SESSIONS", "80"))
    RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))

    ALLOW_DEV_LOGIN = os.environ.get("ALLOW_DEV_LOGIN", "false").lower() == "true"

    # Provider API keys
    DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_KEY")
    GROQ_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY")
    OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_KEY")

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    # Base URLs
    DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    GEMINI_BASE = os.environ.get(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")

    # FIX: Rate limiter cleanup interval (seconds)
    RATE_LIMIT_CLEANUP_INTERVAL = int(os.environ.get("RATE_LIMIT_CLEANUP_INTERVAL", "300"))

    CORS_ORIGINS = [
        FRONTEND_URL,
        "http://127.0.0.1:8123",
        "http://localhost:8123",
        "http://127.0.0.1:5000",
        "http://localhost:5000",
    ]

    @classmethod
    def display(cls) -> None:
        providers = {
            "DeepSeek": bool(cls.DEEPSEEK_KEY),
            "Groq": bool(cls.GROQ_KEY),
            "Gemini": bool(cls.GEMINI_KEY),
            "OpenRouter": bool(cls.OPENROUTER_KEY),
            "Google OAuth": bool(cls.GOOGLE_CLIENT_ID and cls.GOOGLE_CLIENT_SECRET),
        }
        logger.info("%s v%s starting", cls.APP_NAME, cls.VERSION)
        logger.info("Environment=%s Port=%s Frontend=%s", cls.ENVIRONMENT, cls.PORT, cls.FRONTEND_URL)
        logger.info("Providers=%s", ", ".join(f"{k}:{'on' if v else 'off'}" for k, v in providers.items()))


# ---------------------------------------------------------------------------
# Data transfer objects
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
    session_token: Optional[str] = None  # FIX: None means logged out


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
    mode: str = "jarvis-prime"
    created_at: float = 0.0
    attachments: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    public_mode: str
    max_tokens: int = 4096
    temperature: float = 0.7
    thinking: Optional[str] = None


@dataclass(frozen=True)
class ModeSpec:
    id: str
    label: str
    description: str
    chain: Tuple[ModelCandidate, ...]


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------
class Security:
    HTML_RE = re.compile(r"<[^>]*>")
    BAD_PROTOCOLS = re.compile(r"(?i)\b(javascript|data:text/html)\s*:")

    @staticmethod
    def sanitize(text: str, max_len: int = Config.MAX_MESSAGE_LENGTH) -> str:
        if not text:
            return ""
        text = text.replace("\x00", "")
        text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
        text = Security.HTML_RE.sub("", text)
        text = Security.BAD_PROTOCOLS.sub("", text)
        return text.strip()[:max_len]

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
        if len(words) > 8 and len(set(words)) <= 2:
            return True
        return False


def unix_now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# FIX: Rate limiter with periodic cleanup
# ---------------------------------------------------------------------------
class SlidingWindowLimiter:
    def __init__(self) -> None:
        self.events: Dict[str, Deque[float]] = defaultdict(deque)
        self.lock = threading.RLock()
        self._last_cleanup = unix_now()
        self._cleanup_interval = Config.RATE_LIMIT_CLEANUP_INTERVAL

    def check(self, key: str, limit: int, window: int) -> Tuple[bool, int, float]:
        now = unix_now()
        cutoff = now - window
        with self.lock:
            # FIX: Periodic cleanup of stale keys
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_stale_keys(now, window)
                self._last_cleanup = now

            bucket = self.events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                reset = bucket[0] + window
                return False, 0, reset
            bucket.append(now)
            return True, max(limit - len(bucket), 0), now + window

    def _cleanup_stale_keys(self, now: float, window: int) -> None:
        """Remove keys with empty or expired entries."""
        stale_keys = []
        cutoff = now - window
        for key, bucket in self.events.items():
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                stale_keys.append(key)
        for key in stale_keys:
            del self.events[key]
        
        if stale_keys:
            logger.debug("Rate limiter cleaned up %d stale keys", len(stale_keys))


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
            "iat": now,
            "exp": now + timedelta(hours=Config.JWT_EXPIRY_HOURS),
        }
        return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)

    @staticmethod
    def verify(token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        try:
            return jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
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
        self, google_id: Optional[str], email: str, name: str, avatar: str = ""
    ) -> UserDTO:
        email = (email or "").lower().strip()
        if not email:
            raise ValueError("Email is required")
        with self.lock:
            uid = self.users_by_email.get(email) or (
                self.users_by_google.get(google_id) if google_id else None
            )
            if uid and uid in self.users:
                user = self.users[uid]
                user.google_id = google_id or user.google_id
                user.display_name = name or email.split("@")[0]
                user.avatar_url = avatar or user.avatar_url
                user.last_login = unix_now()
                user.total_logins += 1
                return user
            user = UserDTO(
                id=new_id("usr"),
                google_id=google_id,
                email=email,
                display_name=name or email.split("@")[0],
                avatar_url=avatar or "",
                is_admin=email in {"krish@gmail.com", "admin@jarvis.ai"},
                created_at=unix_now(),
                last_login=unix_now(),
            )
            self.users[user.id] = user
            self.users_by_email[email] = user.id
            if google_id:
                self.users_by_google[google_id] = user.id
            return user

    def get_user(self, uid: str) -> Optional[UserDTO]:
        return self.users.get(uid)

    # FIX: Set session_token properly - None means logged out
    def set_session_token(self, uid: str, token: Optional[str]) -> None:
        with self.lock:
            if uid in self.users:
                self.users[uid].session_token = token

    def create_session(self, uid: str, title: str = "New Conversation") -> SessionDTO:
        with self.lock:
            now = unix_now()
            sess = SessionDTO(
                id=new_id("ses"), user_id=uid, title=Security.title(title),
                created_at=now, updated_at=now,
            )
            self.sessions[sess.id] = sess
            self.user_sessions[uid].append(sess.id)
            return sess

    def get_session(self, sid: str, uid: str) -> Optional[SessionDTO]:
        sess = self.sessions.get(sid)
        return sess if sess and sess.user_id == uid else None

    def get_user_sessions(self, uid: str) -> List[SessionDTO]:
        sids = list(self.user_sessions.get(uid, []))
        sessions = [self.sessions[sid] for sid in sids if sid in self.sessions]
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def delete_session(self, sid: str, uid: str) -> bool:
        with self.lock:
            sess = self.sessions.get(sid)
            if not sess or sess.user_id != uid:
                return False
            self.sessions.pop(sid, None)
            self.messages.pop(sid, None)
            if sid in self.user_sessions.get(uid, []):
                self.user_sessions[uid].remove(sid)
            return True

    def update_title(self, sid: str, title: str) -> None:
        with self.lock:
            if sid in self.sessions:
                self.sessions[sid].title = Security.title(title)
                self.sessions[sid].updated_at = unix_now()

    def add_message(
        self, sid: str, role: str, content: str, mode: str = "jarvis-prime",
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> MessageDTO:
        with self.lock:
            if sid not in self.sessions:
                raise KeyError("Session not found")
            msg = MessageDTO(
                id=new_id("msg"), session_id=sid, role=role,
                content=Security.sanitize(content), mode=normalize_mode(mode),
                created_at=unix_now(), attachments=attachments or [],
            )
            self.messages[sid].append(msg)
            self.sessions[sid].updated_at = unix_now()
            return msg

    def get_messages(self, sid: str) -> List[MessageDTO]:
        return sorted(self.messages.get(sid, []), key=lambda msg: msg.created_at)

    def count_messages(self, sid: str) -> int:
        return len(self.messages.get(sid, []))


storage = Storage()


# ---------------------------------------------------------------------------
# JARVIS mode → real model mapping
# ---------------------------------------------------------------------------
def c(
    provider: str, model: str, public_mode: str, max_tokens: int = 4096,
    temperature: float = 0.7, thinking: Optional[str] = None
) -> ModelCandidate:
    return ModelCandidate(provider, model, public_mode, max_tokens, temperature, thinking)


MODE_SPECS: Dict[str, ModeSpec] = {
    "jarvis-prime": ModeSpec(
        "jarvis-prime", "JARVIS Prime", "Balanced flagship reasoning.",
        (
            c("deepseek", "deepseek-chat", "jarvis-prime", 8192, 0.65, "high"),
            c("gemini", "gemini-2.5-pro-exp-03-25", "jarvis-prime", 8192, 0.65),
            c("openrouter", "openai/gpt-4o", "jarvis-prime", 8192, 0.65),
            c("openrouter", "anthropic/claude-3.5-sonnet", "jarvis-prime", 8192, 0.65),
            c("groq", "llama-3.3-70b-versatile", "jarvis-prime", 4096, 0.65),
        ),
    ),
    "jarvis-swift": ModeSpec(
        "jarvis-swift", "JARVIS Swift", "Low-latency production answers.",
        (
            c("groq", "llama-3.3-70b-versatile", "jarvis-swift", 4096, 0.55),
            c("groq", "mixtral-8x7b-32768", "jarvis-swift", 4096, 0.55),
            c("deepseek", "deepseek-chat", "jarvis-swift", 4096, 0.55, "disabled"),
            c("openrouter", "google/gemini-2.0-flash-001", "jarvis-swift", 4096, 0.55),
        ),
    ),
    "jarvis-deepcore": ModeSpec(
        "jarvis-deepcore", "JARVIS DeepCore", "Hard reasoning, coding, and analysis.",
        (
            c("deepseek", "deepseek-reasoner", "jarvis-deepcore", 12000, 0.45, "max"),
            c("gemini", "gemini-2.5-pro-exp-03-25", "jarvis-deepcore", 12000, 0.45),
            c("openrouter", "anthropic/claude-3.5-sonnet", "jarvis-deepcore", 12000, 0.45),
            c("openrouter", "openai/gpt-4o", "jarvis-deepcore", 12000, 0.45),
        ),
    ),
    "jarvis-oracle": ModeSpec(
        "jarvis-oracle", "JARVIS Oracle", "Broad multi-provider synthesis.",
        (
            c("openrouter", "openai/gpt-4o", "jarvis-oracle", 12000, 0.6),
            c("openrouter", "anthropic/claude-3.5-sonnet", "jarvis-oracle", 12000, 0.6),
            c("openrouter", "google/gemini-2.5-pro-exp-03-25", "jarvis-oracle", 12000, 0.6),
            c("openrouter", "x-ai/grok-2-1212", "jarvis-oracle", 8192, 0.6),
            c("deepseek", "deepseek-chat", "jarvis-oracle", 8192, 0.6, "high"),
        ),
    ),
    "jarvis-vision": ModeSpec(
        "jarvis-vision", "JARVIS Vision", "Image and multimodal understanding.",
        (
            c("gemini", "gemini-2.5-pro-exp-03-25", "jarvis-vision", 8192, 0.5),
            c("gemini", "gemini-2.0-flash-001", "jarvis-vision", 8192, 0.5),
            c("openrouter", "openai/gpt-4o", "jarvis-vision", 8192, 0.5),
        ),
    ),
    "jarvis-forge": ModeSpec(
        "jarvis-forge", "JARVIS Forge", "Image creation.",
        (
            c("gemini-image", "imagen-4.0-ultra-generate-001", "jarvis-forge", 0, 0.0),
            c("gemini-image", "imagen-4.0-generate-001", "jarvis-forge", 0, 0.0),
            c("gemini-image", "imagen-4.0-fast-generate-001", "jarvis-forge", 0, 0.0),
        ),
    ),
}

MODE_ALIASES = {
    "deepseek": "jarvis-deepcore",
    "groq": "jarvis-swift",
    "gemini": "jarvis-prime",
    "openrouter": "jarvis-oracle",
    "vision": "jarvis-vision",
    "image-gen": "jarvis-forge",
    "forge": "jarvis-forge",
    "prime": "jarvis-prime",
    "swift": "jarvis-swift",
    "deepcore": "jarvis-deepcore",
    "oracle": "jarvis-oracle",
}


def normalize_mode(value: Optional[str]) -> str:
    if not value:
        return "jarvis-prime"
    value = value.strip().lower()
    return value if value in MODE_SPECS else MODE_ALIASES.get(value, "jarvis-prime")


# ---------------------------------------------------------------------------
# AI service — multi-fallback text, vision, image generation
# ---------------------------------------------------------------------------
class ProviderUnavailable(Exception):
    pass


class AIService:
    def __init__(self) -> None:
        self.system_prompt = (
            "You are JARVIS, an advanced enterprise assistant created by Krish Paliwal. "
            "Be accurate, direct, professional, and useful. Structure answers clearly. "
            "If something is uncertain, say so. Do not mention private provider routing, "
            "API keys, or internal model names unless the operator explicitly asks for backend diagnostics."
        )

    def generate(
        self, prompt: str, preferred_mode: Optional[str], history: Iterable[MessageDTO] = (),
    ) -> Tuple[str, str, float]:
        start = unix_now()
        clean_prompt = Security.sanitize(prompt)
        if Security.is_low_signal(clean_prompt):
            return "Please send a clearer message so I can help properly.", "jarvis-prime", 0.0

        mode_id = normalize_mode(preferred_mode)
        chain = MODE_SPECS[mode_id].chain
        messages = self._messages(clean_prompt, list(history))
        errors: List[str] = []

        for candidate in chain:
            try:
                text = self._dispatch_text(candidate, messages)
                if text:
                    elapsed = round(unix_now() - start, 2)
                    logger.info(
                        "JARVIS mode=%s provider=%s model=%s elapsed=%ss",
                        mode_id, candidate.provider, candidate.model, elapsed,
                    )
                    return text, mode_id, elapsed
            except Exception as exc:
                errors.append(f"{candidate.provider}:{candidate.model}:{exc}")
                logger.warning(
                    "Model candidate failed mode=%s provider=%s model=%s error=%s",
                    mode_id, candidate.provider, candidate.model, exc,
                )

        elapsed = round(unix_now() - start, 2)
        logger.error("All model candidates failed for mode=%s errors=%s", mode_id, " | ".join(errors[-5:]))
        return self._offline_reply(clean_prompt), mode_id, elapsed

    def analyze_image(
        self, image_b64: str, mime_type: str, prompt: str = ""
    ) -> Tuple[str, str, float]:
        start = unix_now()
        clean_prompt = Security.sanitize(prompt or "Describe this image in detail.")
        candidates = MODE_SPECS["jarvis-vision"].chain
        data_url = f"data:{mime_type};base64,{image_b64}"

        for candidate in candidates:
            try:
                if candidate.provider == "gemini":
                    text = self._gemini_vision(candidate, image_b64, mime_type, clean_prompt)
                elif candidate.provider == "openrouter":
                    text = self._openrouter_vision(candidate, data_url, clean_prompt)
                else:
                    # FIX: Skip non-vision providers explicitly
                    continue
                if text:
                    return text, "jarvis-vision", round(unix_now() - start, 2)
            except Exception as exc:
                logger.warning(
                    "Vision candidate failed provider=%s model=%s error=%s",
                    candidate.provider, candidate.model, exc,
                )

        raise ProviderUnavailable(
            "JARVIS Vision is unavailable. Check GEMINI_API_KEY or OPENROUTER_API_KEY."
        )

    def create_image(self, prompt: str, aspect_ratio: str = "1:1") -> Tuple[str, str, float]:
        start = unix_now()
        clean_prompt = Security.sanitize(prompt, 1600)
        if not clean_prompt:
            raise ValueError("Prompt is required")

        for candidate in MODE_SPECS["jarvis-forge"].chain:
            # FIX: Only process image generation candidates
            if candidate.provider != "gemini-image":
                continue
            try:
                image = self._imagen(candidate.model, clean_prompt, aspect_ratio)
                if image:
                    return image, "jarvis-forge", round(unix_now() - start, 2)
            except Exception as exc:
                logger.warning("Forge candidate failed model=%s error=%s", candidate.model, exc)

        raise ProviderUnavailable(
            "JARVIS Forge is unavailable. Check GEMINI_API_KEY and image model access."
        )

    # --- internal helpers ---
    def _messages(self, prompt: str, history: List[MessageDTO]) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        recent = [m for m in history if m.role in {"user", "assistant"}][-Config.MAX_HISTORY_MESSAGES:]
        for msg in recent:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _dispatch_text(
        self, candidate: ModelCandidate, messages: List[Dict[str, str]]
    ) -> Optional[str]:
        # FIX: Guard against image-only providers
        if candidate.provider == "gemini-image":
            return None
            
        if candidate.provider == "deepseek":
            if not Config.DEEPSEEK_KEY:
                raise ProviderUnavailable("DeepSeek key missing")
            return self._openai_compatible(
                Config.DEEPSEEK_BASE, Config.DEEPSEEK_KEY, candidate, messages, {}, deepseek=True,
            )
        if candidate.provider == "groq":
            if not Config.GROQ_KEY:
                raise ProviderUnavailable("Groq key missing")
            return self._openai_compatible(
                Config.GROQ_BASE, Config.GROQ_KEY, candidate, messages, {},
            )
        if candidate.provider == "openrouter":
            if not Config.OPENROUTER_KEY:
                raise ProviderUnavailable("OpenRouter key missing")
            return self._openai_compatible(
                Config.OPENROUTER_BASE, Config.OPENROUTER_KEY, candidate, messages,
                {"HTTP-Referer": Config.FRONTEND_URL, "X-Title": "JARVIS Enterprise"},
            )
        if candidate.provider == "gemini":
            if not Config.GEMINI_KEY:
                raise ProviderUnavailable("Gemini key missing")
            return self._gemini_text(candidate, messages)
        return None

    def _openai_compatible(
        self, base_url: str, api_key: str, candidate: ModelCandidate,
        messages: List[Dict[str, str]], provider_headers: Dict[str, str],
        deepseek: bool = False,
    ) -> Optional[str]:
        payload: Dict[str, Any] = {
            "model": candidate.model,
            "messages": messages,
            "max_tokens": candidate.max_tokens,
            "temperature": candidate.temperature,
        }
        if deepseek and candidate.thinking:
            payload["thinking"] = {
                "type": "disabled" if candidate.thinking == "disabled" else "enabled"
            }
            if candidate.thinking in {"high", "max"}:
                payload["reasoning_effort"] = candidate.thinking

        response = requests.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json", **provider_headers},
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return (message.get("content") or "").strip()

    def _gemini_text(
        self, candidate: ModelCandidate, messages: List[Dict[str, str]]
    ) -> Optional[str]:
        system_text = self.system_prompt
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
                continue
            contents.append({
                "role": "model" if msg["role"] == "assistant" else "user",
                "parts": [{"text": msg["content"]}],
            })
        payload = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": {
                "temperature": candidate.temperature,
                "maxOutputTokens": candidate.max_tokens,
            },
        }
        response = requests.post(
            f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        return self._gemini_text_from_response(response.json())

    def _gemini_vision(
        self, candidate: ModelCandidate, image_b64: str, mime_type: str, prompt: str
    ) -> Optional[str]:
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                ],
            }],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": candidate.max_tokens},
        }
        response = requests.post(
            f"{Config.GEMINI_BASE}/models/{candidate.model}:generateContent",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        return self._gemini_text_from_response(response.json())

    def _openrouter_vision(
        self, candidate: ModelCandidate, data_url: str, prompt: str
    ) -> Optional[str]:
        payload = {
            "model": candidate.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            "max_tokens": candidate.max_tokens,
            "temperature": candidate.temperature,
        }
        response = requests.post(
            f"{Config.OPENROUTER_BASE}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {Config.OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": Config.FRONTEND_URL,
                "X-Title": "JARVIS Enterprise",
            },
            timeout=Config.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        data = response.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()

    def _imagen(self, model: str, prompt: str, aspect_ratio: str) -> Optional[str]:
        if not Config.GEMINI_KEY:
            raise ProviderUnavailable("Gemini key missing")
        safe_ar = aspect_ratio if aspect_ratio in {"1:1", "3:4", "4:3", "9:16", "16:9"} else "1:1"
        payload = {
            "instances": [{"prompt": prompt[:1800]}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": safe_ar,
                "personGeneration": "allow_adult",
            },
        }
        response = requests.post(
            f"{Config.GEMINI_BASE}/models/{model}:predict",
            params={"key": Config.GEMINI_KEY},
            json=payload,
            timeout=max(Config.REQUEST_TIMEOUT, 60),
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error_text(response))
        data = response.json()
        predictions = data.get("predictions") or []
        if not predictions:
            return None
        item = predictions[0]
        encoded = (
            item.get("bytesBase64Encoded")
            or item.get("image", {}).get("bytesBase64Encoded")
            or item.get("imageBytes")
        )
        return f"data:image/png;base64,{encoded}" if encoded else None

    @staticmethod
    def _gemini_text_from_response(data: Dict[str, Any]) -> Optional[str]:
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "\n".join(part.get("text", "") for part in parts if part.get("text"))
        return text.strip() or None

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        try:
            data = response.json()
            return data.get("error", {}).get("message") or data.get("error") or response.text[:500]
        except Exception:
            return response.text[:500]

    @staticmethod
    def _offline_reply(prompt: str) -> str:
        lower = prompt.lower()
        if any(word in lower for word in ("hello", "hi", "hey")):
            return "Hello. JARVIS is online, but the live model network is temporarily unavailable."
        return (
            "JARVIS could not reach the live model network for this request. "
            "Check provider keys, model access, and service status, then try again."
        )


ai_service = AIService()


# ---------------------------------------------------------------------------
# Flask app
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
# Serializers & helpers
# ---------------------------------------------------------------------------
def get_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",", 1)[0].strip() if forwarded else (request.remote_addr or "0.0.0.0")


def serialize_user(user: UserDTO) -> Dict[str, Any]:
    return {
        "id": user.id, "name": user.display_name, "email": user.email,
        "avatar": user.avatar_url, "is_admin": user.is_admin,
    }


def serialize_session(sess: SessionDTO) -> Dict[str, Any]:
    data = asdict(sess)
    data["createdAt"] = datetime.fromtimestamp(sess.created_at, timezone.utc).isoformat()
    data["updatedAt"] = datetime.fromtimestamp(sess.updated_at, timezone.utc).isoformat()
    return data


def serialize_message(msg: MessageDTO) -> Dict[str, Any]:
    data = asdict(msg)
    data["model_used"] = msg.mode
    data["createdAt"] = datetime.fromtimestamp(msg.created_at, timezone.utc).isoformat()
    return data


def json_body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


# FIX: Proper token validation - None session_token means logged out
def current_user_from_token() -> Optional[UserDTO]:
    token = JWTService.from_request()
    if token:
        payload = JWTService.verify(token)
        if payload:
            user = storage.get_user(payload.get("user_id", ""))
            # FIX: Only accept token if session_token is explicitly set AND matches
            if user and user.session_token is not None and user.session_token == token:
                return user
    uid = session.get("user_id")
    if uid:
        user = storage.get_user(uid)
        # Also verify session token from Flask session if available
        sess_token = session.get("session_token")
        if user and user.session_token is not None and sess_token == user.session_token:
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


def limit_or_429(key: str, limit: int, window: int) -> Optional[Tuple[Any, int]]:
    allowed, remaining, reset = rate_limiter.check(key, limit, window)
    if allowed:
        g.rate_remaining = remaining
        return None
    retry = max(round(reset - unix_now(), 2), 0)
    return jsonify({"success": False, "error": "Rate limited", "retry_after": retry}), 429


def parse_image_payload(value: str) -> Tuple[str, str]:
    if not value:
        raise ValueError("No image provided")
    value = value.strip()
    mime = "image/png"
    if value.startswith("data:"):
        header, _, encoded = value.partition(",")
        match = re.match(r"data:([^;]+);base64", header)
        if match:
            mime = match.group(1)
        value = encoded
    raw = base64.b64decode(value, validate=True)
    if len(raw) > Config.MAX_IMAGE_BYTES:
        raise ValueError("Image is too large")
    if not mime.startswith("image/"):
        raise ValueError("Unsupported image type")
    return base64.b64encode(raw).decode("ascii"), mime


# ---------------------------------------------------------------------------
# Routes — static & health
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> Any:
    filename = "jarvis-enterprise-v5.html" if (ROOT / "jarvis-enterprise-v5.html").exists() else "index.html"
    return send_from_directory(ROOT, filename)


@app.get("/health")
@app.get("/jarvis/health")
def health() -> Any:
    return jsonify({
        "success": True, "status": "healthy", "version": Config.VERSION,
        "modes": [{"id": s.id, "label": s.label, "description": s.description} for s in MODE_SPECS.values()],
    })


@app.get("/jarvis/modes")
def modes() -> Any:
    return jsonify({
        "success": True,
        "modes": [{"id": s.id, "label": s.label, "description": s.description} for s in MODE_SPECS.values()],
    })


# ---------------------------------------------------------------------------
# Auth — Google OAuth
# ---------------------------------------------------------------------------
@app.get("/jarvis/sign-in")
@app.get("/login/google")
def google_login() -> Any:
    # Dev-mode fallback (only in development with ALLOW_DEV_LOGIN=true)
    if Config.ENVIRONMENT == "development" and Config.ALLOW_DEV_LOGIN and not Config.GOOGLE_CLIENT_ID:
        user = storage.create_or_update_user(None, "operator@jarvis.local", "Operator")
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
@app.get("/jarvis/auth/callback")
def google_callback() -> Any:
    if request.args.get("error"):
        return redirect(f"{Config.FRONTEND_URL}?error={request.args.get('error')}")
    code = request.args.get("code")
    state = request.args.get("state")
    saved_state = session.pop("oauth_state", None)
    if not code:
        return redirect(f"{Config.FRONTEND_URL}?error=No+code")
    if saved_state and saved_state != state:
        return redirect(f"{Config.FRONTEND_URL}?error=Invalid+state")

    try:
        token_res = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": Config.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=15)
        token_res.raise_for_status()
        access_token = token_res.json().get("access_token")

        user_res = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
        )
        user_res.raise_for_status()
        info = user_res.json()

        user = storage.create_or_update_user(
            google_id=info.get("id"), email=info.get("email", ""),
            name=info.get("name", ""), avatar=info.get("picture", ""),
        )
        token = JWTService.create(user)
        storage.set_session_token(user.id, token)
        session["user_id"] = user.id
        session["session_token"] = token
        return redirect(f"{Config.FRONTEND_URL}?token={token}")
    except Exception as exc:
        logger.error("OAuth failed: %s", exc)
        return redirect(f"{Config.FRONTEND_URL}?error=Auth+failed")


# FIX: Proper logout - set session_token to None to invalidate
@app.get("/jarvis/sign-out")
@app.get("/logout")
def logout() -> Any:
    uid = session.get("user_id")
    if uid:
        # FIX: Set token to None to invalidate all existing JWTs for this user
        storage.set_session_token(uid, None)
    session.clear()
    return redirect(Config.FRONTEND_URL)


@app.get("/jarvis/me")
@app.get("/api/me")
def me() -> Any:
    user = current_user_from_token()
    if not user:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    return jsonify({"success": True, "user": serialize_user(user),
                    "token": JWTService.from_request()})


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@app.get("/jarvis/conversations")
@app.get("/ai/sessions")
@login_required
def list_conversations() -> Any:
    limited = limit_or_429(f"sessions:{g.user.id}", Config.RATE_LIMIT_SESSIONS, Config.RATE_LIMIT_WINDOW)
    if limited:
        return limited
    sessions = [serialize_session(s) for s in storage.get_user_sessions(g.user.id)]
    return jsonify({"success": True, "conversations": sessions, "sessions": sessions})


@app.post("/jarvis/conversations")
@app.post("/ai/session")
@login_required
def create_conversation() -> Any:
    data = json_body()
    sess = storage.create_session(g.user.id, data.get("title") or "New Conversation")
    payload = serialize_session(sess)
    return jsonify({
        "success": True, "conversation": payload, "session": payload,
        "conversation_id": sess.id, "session_id": sess.id,
    })


@app.get("/jarvis/conversations/<sid>")
@app.get("/ai/session/<sid>")
@login_required
def get_conversation(sid: str) -> Any:
    sess = storage.get_session(sid, g.user.id)
    if not sess:
        return jsonify({"success": False, "error": "Conversation not found"}), 404
    messages = [serialize_message(m) for m in storage.get_messages(sid)]
    return jsonify({
        "success": True, "conversation": serialize_session(sess),
        "session": serialize_session(sess), "messages": messages,
    })


@app.delete("/jarvis/conversations/<sid>")
@app.delete("/ai/session/<sid>")
@login_required
def delete_conversation(sid: str) -> Any:
    if storage.delete_session(sid, g.user.id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Conversation not found"}), 404


@app.post("/jarvis/conversations/<sid>/messages")
@app.post("/ai/session/<sid>/message")
@login_required
def send_message(sid: str) -> Any:
    limited = limit_or_429(f"msg:{g.user.id}", Config.RATE_LIMIT_MESSAGES, Config.RATE_LIMIT_WINDOW)
    if limited:
        return limited

    sess = storage.get_session(sid, g.user.id)
    if not sess:
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    data = json_body()
    prompt = Security.sanitize(data.get("prompt") or data.get("message") or "")
    mode = normalize_mode(data.get("mode") or data.get("model"))
    if not prompt:
        return jsonify({"success": False, "error": "Empty message"}), 400

    prior = storage.get_messages(sid)
    storage.add_message(sid, "user", prompt, mode)
    response, public_mode, elapsed = ai_service.generate(prompt, mode, prior)
    assistant = storage.add_message(sid, "assistant", response, public_mode)

    if storage.count_messages(sid) <= 2:
        storage.update_title(sid, prompt)

    return jsonify({
        "success": True,
        "response": response,
        "message": serialize_message(assistant),
        "mode": public_mode,
        "model": public_mode,
        "response_time": elapsed,
        "is_first_message": storage.count_messages(sid) <= 2,
        "remaining_requests": getattr(g, "rate_remaining", None),
    })


# ---------------------------------------------------------------------------
# Vision & Image generation
# ---------------------------------------------------------------------------
@app.post("/jarvis/vision/analyze")
@app.post("/ai/analyze-image")
@login_required
def analyze_image() -> Any:
    data = json_body()
    sid = data.get("conversation_id") or data.get("session_id")
    if sid and not storage.get_session(sid, g.user.id):
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    try:
        image_b64, mime = parse_image_payload(data.get("image") or "")
        prompt = data.get("prompt") or "Describe this image in detail."
        analysis, mode, elapsed = ai_service.analyze_image(image_b64, mime, prompt)
        if sid:
            storage.add_message(sid, "assistant", analysis, mode)
        return jsonify({
            "success": True, "analysis": analysis, "response": analysis,
            "mode": mode, "model": mode, "response_time": elapsed,
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("Vision failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 503


@app.post("/jarvis/forge/create")
@app.post("/ai/generate-image")
@login_required
def generate_image() -> Any:
    data = json_body()
    sid = data.get("conversation_id") or data.get("session_id")
    if sid and not storage.get_session(sid, g.user.id):
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    prompt = Security.sanitize(data.get("prompt") or "", 1600)
    if not prompt:
        return jsonify({"success": False, "error": "No prompt provided"}), 400

    try:
        image_url, mode, elapsed = ai_service.create_image(
            prompt, data.get("aspect_ratio") or data.get("aspectRatio") or "1:1",
        )
        if sid:
            storage.add_message(
                sid, "assistant", f"Image created: {prompt}", mode,
                [{"url": image_url, "caption": prompt, "alt": prompt}],
            )
        return jsonify({
            "success": True, "image_url": image_url, "url": image_url,
            "mode": mode, "model": mode, "caption": f"Image created: {prompt}",
            "response_time": elapsed,
        })
    except Exception as exc:
        logger.error("Image generation failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 503


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_: Exception) -> Any:
    if request.path.startswith(("/jarvis/", "/api/", "/ai/")):
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
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    Config.display()
    if Config.ENVIRONMENT == "production":
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=Config.PORT, threads=8)
        except ImportError:
            app.run(host="0.0.0.0", port=Config.PORT, debug=False)
    else:
        app.run(host="0.0.0.0", port=Config.PORT, debug=True)
