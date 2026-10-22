"""Thought → Speak v5 — a personal AI communication coach.

Run:  uvicorn app:app --reload   then open http://127.0.0.1:8000
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from coach import analysis, auth, insights, llm, transcribe  # noqa: E402  (after load_dotenv)
from coach import audio as audio_mod  # noqa: E402
from coach import language, pronunciation  # noqa: E402
from coach.prompts import MODES, normalize_mode, public_modes, random_prompt  # noqa: E402
from coach.storage import Store, UserStore  # noqa: E402
from coach.curriculum import Curriculum  # noqa: E402

DB_PATH = Path(os.getenv("TTS_DB_PATH", BASE_DIR / "data" / "thought_to_speak.db"))
AUDIO_DIR = Path(os.getenv("TTS_AUDIO_DIR", DB_PATH.parent / "audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
store = Store(DB_PATH, legacy_json=BASE_DIR / "sessions.json")
curriculum = Curriculum(BASE_DIR / "content" / "journeys")

app = FastAPI(title="Thought → Speak", version="6.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

MAX_TRANSCRIPT_CHARS = 8000


class AnalyzeRequest(BaseModel):
    transcript: str = Field(..., max_length=MAX_TRANSCRIPT_CHARS)
    mode: str = "Interview"
    prompt: str = Field("", max_length=1000)
    challenge_mode: bool = False
    duration_seconds: float | None = Field(None, ge=0, le=3600)
    chain_id: str | None = None   # set when this is a retry of an earlier attempt
    save: bool = True
    kind: str = Field("free", pattern="^(free|lesson|warmup|review|read_aloud)$")
    lesson_id: str | None = None
    target_text: str | None = Field(None, max_length=600)   # read-aloud drills: the sentence to read


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)
    name: str | None = Field(None, max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)


class DeleteAccountRequest(BaseModel):
    password: str = Field("", max_length=256)


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


def _check_quota(us: UserStore):
    limit = auth.daily_limit()
    if not limit:
        return
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if us.count_since(day_start.isoformat(timespec="seconds")) >= limit:
        raise HTTPException(429, f"You've reached today's limit of {limit} takes. It resets at midnight UTC.")


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


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest, us: UserStore = Depends(user_store)):
    _check_quota(us)
    return _analyze(us, req, req.transcript.strip())


@app.post("/api/analyze-audio")
async def analyze_audio(audio: UploadFile = File(...), payload: str = Form("{}"),
                        us: UserStore = Depends(user_store)):
    """Real-audio path: the recorded take is analyzed acoustically (pauses, pace, pitch, volume) and,
    when Whisper is available, re-transcribed verbatim with word timestamps."""
    try:
        req = AnalyzeRequest(**{"transcript": "", **json.loads(payload)})
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(422, f"Bad payload: {e}")
    _check_quota(us)
    data = await audio.read()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Recording too large (max 25 MB).")
    try:
        x = await run_in_threadpool(audio_mod.load_audio, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = f"{uuid.uuid4()}.wav"
    path = AUDIO_DIR / name
    path.write_bytes(audio_mod.to_wav_bytes(x))

    tr = await run_in_threadpool(transcribe.transcribe, str(path))
    words, source, warn = None, "browser speech-to-text", None
    text = req.transcript.strip()
    if tr and tr.get("text"):
        text, words, source = tr["text"], tr.get("words") or None, tr["source"]
    elif tr and tr.get("error"):
        warn = tr["error"] + " — used the browser transcript instead."
    try:
        voice = await run_in_threadpool(audio_mod.analyze, x, words, len(text.split()))
    except ValueError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    voice["transcript_source"] = source
    voice["browser_transcript"] = req.transcript.strip() if words else None
    out = await run_in_threadpool(_analyze, us, req, text, voice, name, words)
    if warn:
        out["analysis"]["warning"] = " ".join(filter(None, [out["analysis"].get("warning"), warn]))
    return out


@app.get("/api/audio/{attempt_id}")
def get_audio(attempt_id: str, us: UserStore = Depends(user_store)):
    rec = us.get(attempt_id)
    if not rec or not rec.get("audio_path"):
        raise HTTPException(404, "No audio for this take")
    path = AUDIO_DIR / Path(rec["audio_path"]).name
    if not path.exists():
        raise HTTPException(404, "Audio file missing")
    return FileResponse(path, media_type="audio/wav")


def _analyze(store: UserStore, req: AnalyzeRequest, text: str, voice: dict | None = None, audio_name: str | None = None,
             words: list[dict] | None = None):
    if len(text.split()) < (1 if req.kind == "read_aloud" or req.target_text else 3):
        if audio_name:
            (AUDIO_DIR / audio_name).unlink(missing_ok=True)
        raise HTTPException(400, "Speak or paste a response of at least a few words first.")
    mode = normalize_mode(req.mode)
    lesson = curriculum.get(req.lesson_id) if req.lesson_id else None
    if req.lesson_id and not lesson:
        raise HTTPException(404, "Unknown lesson")
    kind = "lesson" if lesson else req.kind
    if lesson:
        mode = lesson["exercise"]["mode"]
    read_aloud = curriculum.is_read_aloud(lesson) or req.kind == "read_aloud"
    if read_aloud:
        target = (req.target_text or "").strip() or (curriculum.read_aloud_texts(lesson)[0] if lesson else "")
        if not target:
            raise HTTPException(400, "A read-aloud drill needs the sentence to read (target_text).")
        result = analysis.analyze_read_aloud(text, target, words, voice)
        mode = "Read aloud"
        prompt = target
    else:
        spec = curriculum.spec(lesson) if lesson else None
        daily = [w["word"] for w in language.daily_words()]
        result = analysis.analyze(text, mode, req.prompt.strip(), req.challenge_mode, req.duration_seconds, spec,
                                  voice, words, daily)
        prompt = req.prompt.strip()
    lesson_result = curriculum.evaluate(lesson, result) if lesson else None
    if lesson_result:
        result["lesson_result"] = lesson_result
    if not req.save:
        return {"analysis": result, "lesson_result": lesson_result}

    chain_id = req.chain_id if req.chain_id and store.chain(req.chain_id) else None
    rec = store.add_attempt(mode=mode, prompt=prompt, transcript=text,
                            challenge_mode=req.challenge_mode, duration_seconds=req.duration_seconds,
                            analysis=result, chain_id=chain_id, kind=kind,
                            lesson_id=lesson["id"] if lesson else None,
                            lesson_passed=lesson_result["passed"] if lesson_result else None,
                            audio_path=audio_name)
    out = {"attempt": rec, "analysis": result, "comparison": None, "lesson_result": lesson_result}
    if rec["attempt_no"] > 1:
        chain = store.chain(rec["chain_id"])
        out["comparison"] = insights.compare(chain[-2], rec)
        out["first_vs_latest"] = insights.compare(chain[0], rec) if len(chain) > 2 else None
    return out


@app.get("/api/journey")
def journey(us: UserStore = Depends(user_store)):
    return curriculum.journey_view(us.lesson_stats())


@app.get("/api/lessons/{lesson_id}")
def lesson_detail(lesson_id: str, us: UserStore = Depends(user_store)):
    les = curriculum.get(lesson_id)
    if not les:
        raise HTTPException(404, "Unknown lesson")
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
def today(us: UserStore = Depends(user_store)):
    return curriculum.today(us)


@app.get("/api/compare")
def compare(base_id: str, current_id: str, us: UserStore = Depends(user_store)):
    a, b = us.get(base_id), us.get(current_id)
    if not a or not b:
        raise HTTPException(404, "Attempt not found")
    return insights.compare(a, b)


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
