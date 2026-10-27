"""Practice: submit takes for analysis (as jobs), poll them, fetch recordings, prompts and configuration."""
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from coach import analysis, auth, llm, transcribe
from coach import service as svc_mod
from coach.jobs import IdempotencyConflict, QuotaExceeded, public_job
from coach.prompts import MODES, normalize_mode, public_modes, random_prompt
from coach.schemas import AnalyzeRequest, JobOut, UsageOut
from coach.service import InputError
from coach.storage import UserStore

from .deps import Context, get_ctx, user_now, user_store

router = APIRouter(tags=["practice"])

ERROR_STATUS = {"invalid_input": 400, "too_short": 400, "bad_audio": 400, "locked": 403, "unknown_lesson": 404,
                "too_large": 413, "too_long": 413, "audio_missing": 400, "internal": 500, "interrupted": 500}
JOB_WAIT_SECONDS = float(os.getenv("JOB_WAIT_SECONDS", 180))


@router.get("/api/config")
def config():
    p = llm.active_provider()
    return {"provider": p, "model": llm.model_name(p), "modes": public_modes(),
            "dimensions": analysis.DIMENSION_LABELS, "transcriber": transcribe.available(),
            "auth_mode": auth.mode(), "daily_take_limit": auth.daily_limit(),
            "beta": {k: v for k, v in auth.beta().items() if k != "max_users"}}


@router.get("/api/prompt")
def get_prompt(mode: str = "Interview", exclude: str | None = None):
    m = normalize_mode(mode)
    return {"mode": m, "prompt": random_prompt(m, exclude)}


@router.get("/api/health")
def health():
    return {"ok": True, "modes": list(MODES)}


# ----------------------------------------------------------------------------- submissions
async def read_bounded(upload: UploadFile, limit: int) -> bytes:
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


def _submit(c: Context, us: UserStore, req: AnalyzeRequest, request: Request, audio=None, audio_bytes=None):
    now = user_now(request)
    key = request.headers.get("idempotency-key") or req.idempotency_key
    if key and not (8 <= len(key) <= 80):
        raise HTTPException(422, "Idempotency-Key must be 8–80 characters.")
    try:
        c.service.validate(us, req, now, has_audio=audio is not None)
        return c.runner.submit(us, req, now, auth.daily_limit(), audio=audio, audio_bytes=audio_bytes, key=key)
    except InputError as e:
        raise HTTPException(e.status, e.message)
    except QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except IdempotencyConflict as e:
        raise HTTPException(409, str(e))


async def _decode_audio_upload(c: Context, audio: UploadFile):
    data = await read_bounded(audio, svc_mod.MAX_AUDIO_BYTES)
    try:
        x = await run_in_threadpool(c.service.decode_upload, data)
    except InputError as e:
        raise HTTPException(e.status, e.message)
    return x, data


def _finish(job: dict):
    """Synchronous endpoints: return the analysis, an HTTP error for a failed job, or 202 if still running."""
    if job["status"] == "completed":
        if job.get("error_code") == "deleted" or not job.get("result_json"):
            raise HTTPException(410, job.get("error") or "This take was deleted.")   # a replay never re-creates it
        return json.loads(job["result_json"])
    if job["status"] == "failed":
        raise HTTPException(ERROR_STATUS.get(job.get("error_code"), 500), job.get("error") or "Analysis failed")
    return JSONResponse(public_job(job), status_code=202)


@router.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request, us: UserStore = Depends(user_store),
                  c: Context = Depends(get_ctx)):
    """Analyze a typed / browser transcript and wait for the result (kept for API compatibility)."""
    job, _ = _submit(c, us, req, request)
    return _finish(await run_in_threadpool(c.runner.wait, job["id"], JOB_WAIT_SECONDS))


@router.post("/api/analyze-audio")
async def analyze_audio(request: Request, audio: UploadFile = File(...), payload: str = Form("{}"),
                        us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    """Real-audio path, waiting for the result: acoustic analysis plus Whisper re-transcription when available."""
    req = _parse_payload(payload)
    x, data = await _decode_audio_upload(c, audio)
    job, _ = _submit(c, us, req, request, audio=x, audio_bytes=data)
    return _finish(await run_in_threadpool(c.runner.wait, job["id"], JOB_WAIT_SECONDS))


@router.post("/api/jobs", status_code=202, response_model=JobOut)
def create_job(req: AnalyzeRequest, request: Request, us: UserStore = Depends(user_store),
               c: Context = Depends(get_ctx)):
    """Queue an analysis and return immediately. Poll GET /api/jobs/{id}. Safe to retry with the same key."""
    job, created = _submit(c, us, req, request)
    return JSONResponse(JobOut(**public_job(job)).model_dump(), status_code=202 if created else 200)


@router.post("/api/jobs/audio", status_code=202, response_model=JobOut)
async def create_audio_job(request: Request, audio: UploadFile = File(...), payload: str = Form("{}"),
                           us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    req = _parse_payload(payload)
    x, data = await _decode_audio_upload(c, audio)
    job, created = _submit(c, us, req, request, audio=x, audio_bytes=data)
    return JSONResponse(JobOut(**public_job(job)).model_dump(), status_code=202 if created else 200)


@router.get("/api/jobs", response_model=list[JobOut])
def list_jobs(active: bool = False, us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    return [public_job(j) for j in c.runner.jobs.list_for_user(us.user_id, active_only=active)]


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    job = c.runner.jobs.get(job_id, us.user_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return public_job(job)


@router.get("/api/usage", response_model=UsageOut)
def usage(us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    limit = auth.daily_limit()
    used = c.runner.jobs.usage(us.user_id)
    return {"used": used, "limit": limit or None, "remaining": max(0, limit - used) if limit else None,
            "resets": "midnight UTC"}


@router.get("/api/audio/{attempt_id}")
def get_audio(attempt_id: str, us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    rec = us.get(attempt_id)
    if not rec or not rec.get("audio_path"):
        raise HTTPException(404, "No audio for this take")
    path = c.audio_dir / Path(rec["audio_path"]).name
    if not path.exists():
        raise HTTPException(404, "Audio file missing")
    return FileResponse(path, media_type="audio/wav")
