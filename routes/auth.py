"""Accounts: sign up, sign in, sign out, delete account."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from coach import auth
from coach.schemas import DeleteAccountRequest, LoginRequest, SignupRequest

from .deps import Context, current_user, get_ctx

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session(store, request: Request, response: Response, user_id: str):
    token = auth.new_session(store, user_id)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=auth.cookie_secure(request.url.scheme), path="/")


def public_user(u: dict) -> dict:
    guest = auth.is_guest(u)
    return {"id": u["id"], "email": None if guest else u["email"], "name": u.get("name"),
            "created_at": u.get("created_at"), "guest": guest}


def _remove_user(c: Context, user_id: str):
    for name in c.store.delete_user(user_id):
        (c.audio_dir / Path(name).name).unlink(missing_ok=True)
        (c.tmp_dir / Path(name).name).unlink(missing_ok=True)


def purge_stale_guests(c: Context) -> int:
    """Delete guest practice older than GUEST_DAYS (the privacy page promises this)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=auth.GUEST_DAYS)).isoformat(timespec="seconds")
    ids = c.store.stale_guests(cutoff)
    for uid in ids:
        _remove_user(c, uid)
    return len(ids)


def _beta_full(c: Context) -> bool:
    cap = auth.beta()["max_users"]
    return bool(cap and c.store.user_count() >= cap)


BETA_FULL = ("The free beta is full right now. Please try again in a few days — "
             "we're opening more spots as we go.")


@router.get("/me")
def me(request: Request, c: Context = Depends(get_ctx)):
    if auth.mode() == "single":
        return {"mode": "single", "user": public_user(current_user(request))}
    u = auth.session_user(c.store, request.cookies.get(auth.COOKIE))
    return {"mode": "accounts", "user": public_user(u) if u else None}


@router.post("/signup")
def signup(req: SignupRequest, request: Request, response: Response, c: Context = Depends(get_ctx)):
    if auth.mode() == "single":
        raise HTTPException(400, "Accounts are turned off (AUTH_MODE=single).")
    ip = _client_ip(request)
    if auth.signups.blocked(ip):
        raise HTTPException(429, "Too many new accounts from this network. Try again later.")
    email = auth.normalize_email(req.email)
    err = auth.validate_signup(email, req.password)
    if err:
        raise HTTPException(400, err)
    if c.store.user_by_email(email):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    if _beta_full(c):
        raise HTTPException(403, BETA_FULL)
    try:
        user = c.store.create_user(email, auth.hash_password(req.password), (req.name or "").strip() or None)
    except sqlite3.IntegrityError:   # two sign-ups for the same email at the same moment
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    auth.signups.hit(ip)
    _set_session(c.store, request, response, user["id"])
    return {"user": public_user(user)}


@router.post("/guest")
def guest(request: Request, response: Response, c: Context = Depends(get_ctx)):
    """Try the app without an account: a guest session that can later be upgraded, keeping its practice."""
    if not auth.guests_enabled():
        raise HTTPException(400, "Guest mode is turned off. Create an account to practice.")
    current = auth.session_user(c.store, request.cookies.get(auth.COOKIE))
    if current:                                   # already signed in (or already a guest): nothing to do
        return {"user": public_user(current)}
    ip = _client_ip(request)
    if auth.guests.blocked(ip):
        raise HTTPException(429, "Too many guest sessions from this network. Create a free account instead.")
    purge_stale_guests(c)
    user = c.store.create_guest()
    auth.guests.hit(ip)
    _set_session(c.store, request, response, user["id"])
    return {"user": public_user(user)}


@router.post("/upgrade")
def upgrade(req: SignupRequest, request: Request, user: dict = Depends(current_user), c: Context = Depends(get_ctx)):
    """A guest creates a real account in place: same practice history, streak and today's takes."""
    if not auth.is_guest(user):
        raise HTTPException(400, "You already have an account.")
    email = auth.normalize_email(req.email)
    err = auth.validate_signup(email, req.password)
    if err:
        raise HTTPException(400, err)
    if c.store.user_by_email(email):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    if _beta_full(c):
        raise HTTPException(403, BETA_FULL)
    try:
        u = c.store.upgrade_guest(user["id"], email, auth.hash_password(req.password), (req.name or "").strip() or None)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    if not u:
        raise HTTPException(409, "Couldn't save this account. Please try again.")
    return {"user": public_user(u)}


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response, c: Context = Depends(get_ctx)):
    if auth.mode() == "single":
        raise HTTPException(400, "Accounts are turned off (AUTH_MODE=single).")
    email, ip = auth.normalize_email(req.email), _client_ip(request)
    if auth.login_failures.blocked(f"e:{email}") or auth.login_failures.blocked(f"ip:{ip}"):
        raise HTTPException(429, "Too many sign-in attempts. Wait 15 minutes and try again.")
    user = auth.check_login(c.store, email, req.password)
    if not user:
        auth.login_failures.hit(f"e:{email}")
        auth.login_failures.hit(f"ip:{ip}")
        raise HTTPException(401, "Email or password is incorrect.")
    auth.login_failures.reset(f"e:{email}")
    _set_session(c.store, request, response, user["id"])
    return {"user": public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response, c: Context = Depends(get_ctx)):
    token = request.cookies.get(auth.COOKIE)
    if token:
        c.store.delete_session(auth.token_hash(token))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.delete("/account")
def delete_account(req: DeleteAccountRequest, response: Response, user: dict = Depends(current_user),
                   c: Context = Depends(get_ctx)):
    """Permanently delete the signed-in user's account, practice history, jobs and recordings."""
    if auth.mode() == "accounts" and not auth.is_guest(user):   # a guest has no password; the cookie is the proof
        full = c.store.user_by_email(user["email"])
        if not full or not auth.verify_password(req.password, full["password_hash"]):
            raise HTTPException(403, "Password is incorrect.")
    _remove_user(c, user["id"])
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}
