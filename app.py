"""Thought → Speak v4 — a personal AI communication coach.

Run:  uvicorn app:app --reload   then open http://127.0.0.1:8000
"""
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from coach import analysis, insights, llm, transcribe  # noqa: E402  (after load_dotenv)
from coach import audio as audio_mod  # noqa: E402
from coach.prompts import MODES, normalize_mode, public_modes, random_prompt  # noqa: E402
from coach.storage import Store  # noqa: E402
from coach.curriculum import Curriculum  # noqa: E402

DB_PATH = Path(os.getenv("TTS_DB_PATH", BASE_DIR / "data" / "thought_to_speak.db"))
AUDIO_DIR = Path(os.getenv("TTS_AUDIO_DIR", DB_PATH.parent / "audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MAX_AUDIO_BYTES = 25 * 1024 * 1024
store = Store(DB_PATH, legacy_json=BASE_DIR / "sessions.json")
curriculum = Curriculum(BASE_DIR / "content")

app = FastAPI(title="Thought → Speak", version="4.0")
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
    kind: str = Field("free", pattern="^(free|lesson|warmup|review)$")
    lesson_id: str | None = None


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
def config():
    p = llm.active_provider()
    return {"provider": p, "model": llm.model_name(p), "modes": public_modes(),
            "dimensions": analysis.DIMENSION_LABELS, "transcriber": transcribe.available()}


@app.get("/api/prompt")
def get_prompt(mode: str = "Interview", exclude: str | None = None):
    m = normalize_mode(mode)
    return {"mode": m, "prompt": random_prompt(m, exclude)}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    return _analyze(req, req.transcript.strip())


@app.post("/api/analyze-audio")
async def analyze_audio(audio: UploadFile = File(...), payload: str = Form("{}")):
    """Real-audio path: the recorded take is analyzed acoustically (pauses, pace, pitch, volume) and,
    when Whisper is available, re-transcribed verbatim with word timestamps."""
    try:
        req = AnalyzeRequest(**{"transcript": "", **json.loads(payload)})
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(422, f"Bad payload: {e}")
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
    out = await run_in_threadpool(_analyze, req, text, voice, name)
    if warn:
        out["analysis"]["warning"] = " ".join(filter(None, [out["analysis"].get("warning"), warn]))
    return out


@app.get("/api/audio/{attempt_id}")
def get_audio(attempt_id: str):
    rec = store.get(attempt_id)
    if not rec or not rec.get("audio_path"):
        raise HTTPException(404, "No audio for this take")
    path = AUDIO_DIR / Path(rec["audio_path"]).name
    if not path.exists():
        raise HTTPException(404, "Audio file missing")
    return FileResponse(path, media_type="audio/wav")


def _analyze(req: AnalyzeRequest, text: str, voice: dict | None = None, audio_name: str | None = None):
    if len(text.split()) < 3:
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
    spec = curriculum.spec(lesson) if lesson else None
    result = analysis.analyze(text, mode, req.prompt.strip(), req.challenge_mode, req.duration_seconds, spec, voice)
    lesson_result = curriculum.evaluate(lesson, result) if lesson else None
    if lesson_result:
        result["lesson_result"] = lesson_result
    if not req.save:
        return {"analysis": result, "lesson_result": lesson_result}

    chain_id = req.chain_id if req.chain_id and store.chain(req.chain_id) else None
    rec = store.add_attempt(mode=mode, prompt=req.prompt.strip(), transcript=text,
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
def journey():
    return curriculum.journey_view(store.lesson_stats())


@app.get("/api/lessons/{lesson_id}")
def lesson_detail(lesson_id: str):
    les = curriculum.get(lesson_id)
    if not les:
        raise HTTPException(404, "Unknown lesson")
    idx = curriculum.order.index(lesson_id)
    nxt = curriculum.order[idx + 1] if idx + 1 < len(curriculum.order) else None
    return {**les, "progress": store.lesson_stats().get(lesson_id, {}), "next_lesson_id": nxt,
            "index": idx + 1, "count": len(curriculum.order)}


@app.get("/api/today")
def today():
    return curriculum.today(store)


@app.get("/api/compare")
def compare(base_id: str, current_id: str):
    a, b = store.get(base_id), store.get(current_id)
    if not a or not b:
        raise HTTPException(404, "Attempt not found")
    return insights.compare(a, b)


@app.get("/api/history")
def history(limit: int = Query(200, ge=1, le=1000), mode: str | None = None):
    return store.recent(limit, normalize_mode(mode) if mode else None)


@app.get("/api/chain/{chain_id}")
def chain(chain_id: str):
    return store.chain(chain_id)


@app.delete("/api/attempts/{attempt_id}")
def delete_attempt(attempt_id: str):
    rec = store.get(attempt_id)
    if rec and rec.get("audio_path"):
        (AUDIO_DIR / Path(rec["audio_path"]).name).unlink(missing_ok=True)
    if not store.delete(attempt_id):
        raise HTTPException(404, "Attempt not found")
    return {"ok": True}


@app.get("/api/progress")
def progress():
    return insights.progress(store.all_chronological())


@app.get("/api/recommendation")
def recommendation():
    return insights.recommend(store.all_chronological())


@app.get("/api/export")
def export():
    return JSONResponse(store.all_chronological(),
                        headers={"Content-Disposition": "attachment; filename=thought_to_speak_history.json"})


@app.get("/api/health")
def health():
    return {"ok": True, "modes": list(MODES)}
