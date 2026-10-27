"""Shared dependencies: the app context, the signed-in user, and the user's timezone."""
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException, Request

from coach import auth
from coach.storage import UserStore


@dataclass
class Context:
    store: object
    curriculum: object
    service: object
    runner: object
    base_dir: Path
    audio_dir: Path
    tmp_dir: Path


def get_ctx(request: Request) -> Context:
    return request.app.state.ctx


def current_user(request: Request) -> dict:
    store = get_ctx(request).store
    if auth.mode() == "single":
        return store.ensure_user(auth.SINGLE_USER_ID, "local@localhost")
    user = auth.session_user(store, request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def user_store(request: Request, user: dict = Depends(current_user)) -> UserStore:
    us = get_ctx(request).store.for_user(user["id"])
    us.user = user
    return us


def user_tz(request: Request) -> tzinfo:
    """The user's IANA timezone from the browser's X-Timezone header (validated); else the server's zone."""
    name = (request.headers.get("x-timezone") or "").strip()
    if name and len(name) < 64:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


# The clock is a single seam so tests can move time (e.g. "tomorrow morning") without patching every router.
CLOCK = datetime.now


def user_now(request: Request) -> datetime:
    """Now, in the user's timezone — used for daily plans, program unlocks, streaks and dates in progress."""
    return CLOCK(user_tz(request))
