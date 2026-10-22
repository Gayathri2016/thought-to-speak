"""Accounts: email + password sign-in with server-side sessions in an HttpOnly cookie.

Standard library only:
- passwords are hashed with scrypt (per-user random salt) and compared in constant time;
- the session cookie carries a random 256-bit token, and only its SHA-256 hash is stored, so a leaked
  database can't be replayed as live sessions;
- failed sign-ins are rate-limited per email and per IP address.

AUTH_MODE=accounts (default) requires sign-in. AUTH_MODE=single skips it: everything belongs to one local
user, the way the app worked before accounts, which is handy for personal use on localhost.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

COOKIE = "tts_session"
SESSION_DAYS = 30
SINGLE_USER_ID = "local-user"
MIN_PASSWORD = 8
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# scrypt cost: N=2^14, r=8, p=1 (~16 MB, ~50 ms) — the commonly recommended interactive-login setting.
_N, _R, _P = 2 ** 14, 8, 1


def mode() -> str:
    return "single" if os.getenv("AUTH_MODE", "accounts").strip().lower() == "single" else "accounts"


def cookie_secure(scheme: str) -> bool:
    """Mark the cookie HTTPS-only. COOKIE_SECURE=auto (default) does so whenever the request came over HTTPS;
    behind a proxy that terminates TLS, set COOKIE_SECURE=1 (or run uvicorn with --proxy-headers)."""
    v = os.getenv("COOKIE_SECURE", "auto").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return scheme == "https"


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want))
        return hmac.compare_digest(got, want)
    except Exception:
        return False


# A real-looking hash to verify against when the email doesn't exist, so response time doesn't reveal
# which emails have accounts.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def check_login(store, email: str, password: str) -> dict | None:
    user = store.user_by_email(email)
    if not user:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user["password_hash"]) else None


# ---------------------------------------------------------------- validation
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_signup(email: str, password: str) -> str | None:
    if not _EMAIL_RE.match(email) or len(email) > 254:
        return "Enter a valid email address."
    if len(password) < MIN_PASSWORD:
        return f"Use a password of at least {MIN_PASSWORD} characters."
    if len(password) > 256:
        return "That password is too long."
    return None


# ---------------------------------------------------------------- sessions
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(store, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    store.add_session(token_hash(token), user_id, expires.isoformat(timespec="seconds"))
    return token


def session_user(store, token: str | None) -> dict | None:
    return store.session_user(token_hash(token)) if token else None


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window counter kept in memory. Good for one server process; use Redis when you run several."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float):
        while q and q[0] <= now - self.window:
            q.popleft()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            return len(q) >= self.limit

    def hit(self, key: str):
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            q.append(now)

    def reset(self, key: str):
        with self._lock:
            self._hits.pop(key, None)


# 10 failed sign-ins per email or per IP in 15 minutes, then wait.
login_failures = RateLimiter(10, 15 * 60)
# 5 new accounts per IP per hour.
signups = RateLimiter(5, 60 * 60)


def daily_limit() -> int:
    """Max analyzed takes per user per day (each costs a transcription + an LLM call). 0 = unlimited."""
    try:
        return max(0, int(os.getenv("DAILY_TAKE_LIMIT", "60")))
    except ValueError:
        return 60
