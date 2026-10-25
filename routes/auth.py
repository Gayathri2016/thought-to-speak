"""Accounts: sign up, sign in, sign out, delete account."""
import sqlite3
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
    return {"id": u["id"], "email": u["email"], "name": u.get("name"), "created_at": u.get("created_at")}


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
    try:
        user = c.store.create_user(email, auth.hash_password(req.password), (req.name or "").strip() or None)
    except sqlite3.IntegrityError:   # two sign-ups for the same email at the same moment
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    auth.signups.hit(ip)
    _set_session(c.store, request, response, user["id"])
    return {"user": public_user(user)}


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
    if auth.mode() == "accounts":
        full = c.store.user_by_email(user["email"])
        if not full or not auth.verify_password(req.password, full["password_hash"]):
            raise HTTPException(403, "Password is incorrect.")
    for name in c.store.delete_user(user["id"]):
        (c.audio_dir / Path(name).name).unlink(missing_ok=True)
        (c.tmp_dir / Path(name).name).unlink(missing_ok=True)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}
