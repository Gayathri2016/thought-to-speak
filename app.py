"""Thought → Speak v5 — a personal AI communication coach.

Run:  uvicorn app:app --reload   then open http://127.0.0.1:8000
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from coach import analysis, auth, insights, llm, transcribe  # noqa: E402  (after load_dotenv)
from coach import service as svc_mod  # noqa: E402
from coach.jobs import IdempotencyConflict, JobRunner, QuotaExceeded, public_job  # noqa: E402
from coach.schemas import AnalyzeRequest, DeleteAccountRequest, LoginRequest, SignupRequest  # noqa: E402
from coach.service import AnalysisService, InputError  # noqa: E402
from coach import audio as audio_mod  # noqa: E402
from coach import language, pronunciation  # noqa: E402
from coach.prompts import MODES, normalize_mode, public_modes, random_prompt  # noqa: E402
from coach.storage import Store, UserStore  # noqa: E402
from coach.curriculum import Curriculum  # noqa: E402

DB_PATH = Path(os.getenv("TTS_DB_PATH", BASE_DIR / "data" / "thought_to_speak.db"))
AUDIO_DIR = Path(os.getenv("TTS_AUDIO_DIR", DB_PATH.parent / "audio"))
TMP_DIR = Path(os.getenv("TTS_TMP_DIR", DB_PATH.parent / "tmp"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
store = Store(DB_PATH, legacy_json=BASE_DIR / "sessions.json")
curriculum = Curriculum(BASE_DIR / "content" / "journeys")
service = AnalysisService(curriculum, AUDIO_DIR, TMP_DIR)
runner = JobRunner(store, service)

app = FastAPI(title="Thought → Speak", version="8.4")


@app.on_event("startup")
def _start_worker():
    runner.start()       # recover interrupted jobs and start the analysis workers (also started lazily)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def revalidate_app_files(request: Request, call_next):
    """Make browsers re-check the page and its JS/CSS on every load (cheap: unchanged files return 304),
    so an update never runs with a stale, cached app.js."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# ----------------------------------------------------------------------------- accounts
def current_user(request: Request) -> dict:
    if auth.mode() == "single":
        return store.ensure_user(auth.SINGLE_USER_ID, "local@localhost")
    user = auth.session_user(store, request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def user_store(user: dict = Depends(current_user)) -> UserStore:
    return store.for_user(user["id"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session(request: Request, response: Response, user_id: str):
    token = auth.new_session(store, user_id)
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=auth.cookie_secure(request.url.scheme), path="/")


def _public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u.get("name"), "created_at": u.get("created_at")}


def user_now(request: Request) -> datetime:
    """The current time in the user's own timezone (sent by the browser as X-Timezone, e.g. America/Los_Angeles),
    so "today" and daily unlocks follow their calendar, not the server's."""
    name = (request.headers.get("x-timezone") or "").strip()
    if name and len(name) < 64:
        try:
            return datetime.now(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone()


@app.get("/api/auth/me")
def me(request: Request):
    if auth.mode() == "single":
        return {"mode": "single", "user": _public_user(current_user(request))}
    u = auth.session_user(store, request.cookies.get(auth.COOKIE))
    return {"mode": "accounts", "user": _public_user(u) if u else None}


@app.post("/api/auth/signup")
def signup(req: SignupRequest, request: Request, response: Response):
    if auth.mode() == "single":
        raise HTTPException(400, "Accounts are turned off (AUTH_MODE=single).")
    ip = _client_ip(request)
    if auth.signups.blocked(ip):
        raise HTTPException(429, "Too many new accounts from this network. Try again later.")
    email = auth.normalize_email(req.email)
    err = auth.validate_signup(email, req.password)
    if err:
        raise HTTPException(400, err)
    if store.user_by_email(email):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    try:
        user = store.create_user(email, auth.hash_password(req.password), (req.name or "").strip() or None)
    except sqlite3.IntegrityError:   # two sign-ups for the same email at the same moment
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    auth.signups.hit(ip)
    _set_session(request, response, user["id"])
    return {"user": _public_user(user)}


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request, response: Response):
    if auth.mode() == "single":
        raise HTTPException(400, "Accounts are turned off (AUTH_MODE=single).")
    email, ip = auth.normalize_email(req.email), _client_ip(request)
    if auth.login_failures.blocked(f"e:{email}") or auth.login_failures.blocked(f"ip:{ip}"):
        raise HTTPException(429, "Too many sign-in attempts. Wait 15 minutes and try again.")
    user = auth.check_login(store, email, req.password)
    if not user:
        auth.login_failures.hit(f"e:{email}")
        auth.login_failures.hit(f"ip:{ip}")
        raise HTTPException(401, "Email or password is incorrect.")
    auth.login_failures.reset(f"e:{email}")
    _set_session(request, response, user["id"])
    return {"user": _public_user(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        store.delete_session(auth.token_hash(token))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.delete("/api/auth/account")
def delete_account(req: DeleteAccountRequest, request: Request, response: Response,
                   user: dict = Depends(current_user)):
    """Permanently delete the signed-in user's account, practice history and recordings."""
    if auth.mode() == "accounts":
        full = store.user_by_email(user["email"])
        if not full or not auth.verify_password(req.password, full["password_hash"]):
            raise HTTPException(403, "Password is incorrect.")
    for name in store.delete_user(user["id"]):
        (AUDIO_DIR / Path(name).name).unlink(missing_ok=True)
        (TMP_DIR / Path(name).name).unlink(missing_ok=True)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
def config():
    p = llm.active_provider()
    return {"provider": p, "model": llm.model_name(p), "modes": public_modes(),
            "dimensions": analysis.DIMENSION_LABELS, "transcriber": transcribe.available(),
            "auth_mode": auth.mode(), "daily_take_limit": auth.daily_limit()}


@app.get("/api/prompt")
def get_prompt(mode: str = "Interview", exclude: str | None = None):
    m = normalize_mode(mode)
    return {"mode": m, "prompt": random_prompt(m, exclude)}


# ----------------------------------------------------------------------------- analysis (jobs)
ERROR_STATUS = {"invalid_input": 400, "too_short": 400, "bad_audio": 400, "locked": 403, "unknown_lesson": 404,
                "too_large": 413, "too_long": 413, "internal": 500, "interrupted": 500}
JOB_WAIT_SECONDS = float(os.getenv("JOB_WAIT_SECONDS", 180))


async def _read_bounded(upload: UploadFile, limit: int) -> bytes:
    """Read an upload in chunks and stop as soon as it exceeds the limit (never buffers an oversized file)."""
    chunks, size = [], 0
    while True:
        chunk = await upload.read(256 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"Recording too large (max {limit // (1024 * 1024)} MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_payload(payload: str) -> AnalyzeRequest:
    try:
        return AnalyzeRequest(**{"transcript": "", **json.loads(payload)})
    except (ValidationError, json.JSONDecodeError, TypeError) as e:
        raise HTTPException(422, f"Bad payload: {e}")


def _submit(us: UserStore, req: AnalyzeRequest, request: Request, audio=None, audio_bytes=None) -> tuple[dict, bool]:
    now = user_now(request)
    key = request.headers.get("idempotency-key") or req.idempotency_key
    if key and not (8 <= len(key) <= 80):
        raise HTTPException(422, "Idempotency-Key must be 8–80 characters.")
    try:
        service.validate(us, req, now, has_audio=audio is not None)
        return runner.submit(us, req, now, auth.daily_limit(), audio=audio, audio_bytes=audio_bytes, key=key)
    except InputError as e:
        raise HTTPException(e.status, e.message)
    except QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except IdempotencyConflict as e:
        raise HTTPException(409, str(e))


async def _decode_audio_upload(audio: UploadFile):
    data = await _read_bounded(audio, svc_mod.MAX_AUDIO_BYTES)
    try:
        x = await run_in_threadpool(service.decode_upload, data)
    except InputError as e:
        raise HTTPException(e.status, e.message)
    return x, data


def _finish(job: dict):
    """Synchronous endpoints: return the analysis, an HTTP error for a failed job, or 202 if still running."""
    if job["status"] == "completed":
        return json.loads(job["result_json"])
    if job["status"] == "failed":
        raise HTTPException(ERROR_STATUS.get(job.get("error_code"), 500), job.get("error") or "Analysis failed")
    return JSONResponse(public_job(job), status_code=202)


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request, us: UserStore = Depends(user_store)):
    """Analyze a typed / browser transcript and wait for the result (kept for API compatibility)."""
    job, _ = _submit(us, req, request)
    return _finish(await run_in_threadpool(runner.wait, job["id"], JOB_WAIT_SECONDS))


@app.post("/api/analyze-audio")
async def analyze_audio(request: Request, audio: UploadFile = File(...), payload: str = Form("{}"),
                        us: UserStore = Depends(user_store)):
    """Real-audio path, waiting for the result: acoustic analysis plus Whisper re-transcription when available."""
    req = _parse_payload(payload)
    x, data = await _decode_audio_upload(audio)
    job, _ = _submit(us, req, request, audio=x, audio_bytes=data)
    return _finish(await run_in_threadpool(runner.wait, job["id"], JOB_WAIT_SECONDS))


@app.post("/api/jobs", status_code=202)
def create_job(req: AnalyzeRequest, request: Request, us: UserStore = Depends(user_store)):
    """Queue an analysis and return immediately. Poll GET /api/jobs/{id}. Safe to retry with the same key."""
    job, created = _submit(us, req, request)
    return JSONResponse(public_job(job), status_code=202 if created else 200)


@app.post("/api/jobs/audio", status_code=202)
async def create_audio_job(request: Request, audio: UploadFile = File(...), payload: str = Form("{}"),
                           us: UserStore = Depends(user_store)):
    req = _parse_payload(payload)
    x, data = await _decode_audio_upload(audio)
    job, created = _submit(us, req, request, audio=x, audio_bytes=data)
    return JSONResponse(public_job(job), status_code=202 if created else 200)


@app.get("/api/jobs")
def list_jobs(active: bool = False, us: UserStore = Depends(user_store)):
    return [public_job(j) for j in runner.jobs.list_for_user(us.user_id, active_only=active)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, us: UserStore = Depends(user_store)):
    job = runner.jobs.get(job_id, us.user_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return public_job(job)


@app.get("/api/usage")
def usage(us: UserStore = Depends(user_store)):
    limit = auth.daily_limit()
    used = runner.jobs.usage(us.user_id)
    return {"used": used, "limit": limit or None, "remaining": max(0, limit - used) if limit else None,
            "resets": "midnight UTC"}


@app.get("/api/audio/{attempt_id}")
def get_audio(attempt_id: str, us: UserStore = Depends(user_store)):
    rec = us.get(attempt_id)
    if not rec or not rec.get("audio_path"):
        raise HTTPException(404, "No audio for this take")
    path = AUDIO_DIR / Path(rec["audio_path"]).name
    if not path.exists():
        raise HTTPException(404, "Audio file missing")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/journey")
def journey(us: UserStore = Depends(user_store)):
    return curriculum.journey_view(us.lesson_stats())


@app.get("/api/lessons/{lesson_id}")
def lesson_detail(lesson_id: str, request: Request, us: UserStore = Depends(user_store)):
    les = curriculum.get(lesson_id)
    if not les:
        raise HTTPException(404, "Unknown lesson")
    if les.get("program_id"):
        pid = les["program_id"]
        st = curriculum.program_state(us, pid, user_now(request))
        day = next(d for w in st["weeks"] for d in w["days"] if d["id"] == lesson_id)
        return {**les, "progress": us.lesson_stats().get(lesson_id, {}), "next_lesson_id": None,
                "index": les["day"], "count": st["total"],
                "program": {"id": pid, "title": st["title"], "day": les["day"], "status": day["status"],
                            "enrolled": st["enrolled"], "gate": curriculum.program_gate(us, les, user_now(request))}}
    order = [i for i in curriculum.order if curriculum.get(i)["journey_id"] == les["journey_id"]]
    idx = order.index(lesson_id)
    nxt = order[idx + 1] if idx + 1 < len(order) else None
    out = {**les, "progress": us.lesson_stats().get(lesson_id, {}), "next_lesson_id": nxt,
           "index": idx + 1, "count": len(order)}
    if curriculum.is_read_aloud(les):
        st = pronunciation.get_set(les["exercise"]["set"])
        out["read_aloud"] = {"set": st["id"], "title": st["title"], "tip": st.get("tip"), "items": st["items"],
                             "pairs": st.get("pairs", [])}
    return out


@app.get("/api/pronounce/sets")
def pronounce_sets():
    out = []
    for st in pronunciation.drill_sets():
        out.append({**st, "items": [{**i, "focus_info": [pronunciation.describe(w) for w in i.get("focus", [])]}
                                    for i in st["items"]],
                    "pairs_info": [[pronunciation.describe(a), pronunciation.describe(b)] for a, b in st.get("pairs", [])]})
    return out


@app.get("/api/pronounce/lookup")
def pronounce_lookup(words: str = Query(..., max_length=500)):
    return [pronunciation.describe(w) for w in words.replace(",", " ").split()[:40]]


@app.get("/api/vocab/today")
def vocab_today():
    return language.daily_words()


@app.get("/api/vocab/bank")
def vocab_bank(us: UserStore = Depends(user_store)):
    used = dict(insights.language_progress(us.all_chronological())["words_used"])
    return [{**w, "times_used": used.get(w["word"], 0)} for w in language.word_bank()]


@app.get("/api/today")
def today(request: Request, us: UserStore = Depends(user_store)):
    return curriculum.today(us, user_now(request))


@app.get("/api/programs")
def programs(request: Request, us: UserStore = Depends(user_store)):
    now = user_now(request)
    return [curriculum.program_state(us, pid, now) for pid in curriculum.programs]


@app.post("/api/programs/{program_id}/start")
def start_program(program_id: str, request: Request, us: UserStore = Depends(user_store)):
    """Enroll in a program — or restart it from Day 1 (earlier takes stay in your history)."""
    if program_id not in curriculum.programs:
        raise HTTPException(404, "Unknown program")
    now = user_now(request)
    curriculum.enroll(us, program_id, now)
    return curriculum.program_state(us, program_id, now)


@app.get("/api/compare")
def compare(base_id: str, current_id: str, us: UserStore = Depends(user_store)):
    a, b = us.get(base_id), us.get(current_id)
    if not a or not b:
        raise HTTPException(404, "Attempt not found")
    return insights.compare(a, b, cache=us)


@app.get("/api/history")
def history(limit: int = Query(200, ge=1, le=1000), mode: str | None = None, us: UserStore = Depends(user_store)):
    return us.recent(limit, normalize_mode(mode) if mode else None)


@app.get("/api/chain/{chain_id}")
def chain(chain_id: str, us: UserStore = Depends(user_store)):
    return us.chain(chain_id)


@app.delete("/api/attempts/{attempt_id}")
def delete_attempt(attempt_id: str, us: UserStore = Depends(user_store)):
    rec = us.get(attempt_id)
    if rec and rec.get("audio_path"):
        (AUDIO_DIR / Path(rec["audio_path"]).name).unlink(missing_ok=True)
    if not us.delete(attempt_id):
        raise HTTPException(404, "Attempt not found")
    return {"ok": True}


@app.get("/api/progress")
def progress(us: UserStore = Depends(user_store)):
    return insights.progress(us.all_chronological())


@app.get("/api/recommendation")
def recommendation(us: UserStore = Depends(user_store)):
    return insights.recommend(us.all_chronological())


@app.get("/api/export")
def export(us: UserStore = Depends(user_store)):
    return JSONResponse(us.all_chronological(),
                        headers={"Content-Disposition": "attachment; filename=thought_to_speak_history.json"})


@app.get("/api/health")
def health():
    return {"ok": True, "modes": list(MODES)}
