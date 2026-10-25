"""Progress and history: dashboards, retry comparisons, take history, deletion and export."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from coach import adaptive, insights
from coach.prompts import normalize_mode
from coach.storage import UserStore

from .deps import Context, get_ctx, user_now, user_store

router = APIRouter(tags=["progress"])


@router.get("/api/progress")
def progress(request: Request, us: UserStore = Depends(user_store)):
    attempts = us.all_chronological()
    out = insights.progress(attempts, now=user_now(request))
    out["weekly"] = adaptive.weekly_history(us, attempts)       # retained skill, separate from retry gains
    return out


@router.get("/api/recommendation")
def recommendation(us: UserStore = Depends(user_store)):
    return insights.recommend(us.all_chronological())


@router.get("/api/compare")
def compare(base_id: str, current_id: str, us: UserStore = Depends(user_store)):
    a, b = us.get(base_id), us.get(current_id)
    if not a or not b:
        raise HTTPException(404, "Attempt not found")
    return insights.compare(a, b, cache=us)


@router.get("/api/history")
def history(limit: int = Query(200, ge=1, le=1000), mode: str | None = None, us: UserStore = Depends(user_store)):
    return us.recent(limit, normalize_mode(mode) if mode else None)


@router.get("/api/chain/{chain_id}")
def chain(chain_id: str, us: UserStore = Depends(user_store)):
    return us.chain(chain_id)


@router.delete("/api/attempts/{attempt_id}")
def delete_attempt(attempt_id: str, request: Request, us: UserStore = Depends(user_store),
                   c: Context = Depends(get_ctx)):
    """Delete a take: its row, its job's stored request/result, comparisons against it, its recording — and its
    effect on the skill schedule and baseline. Quota already used stays used."""
    rec = us.get(attempt_id)
    if not rec or not us.delete(attempt_id):
        raise HTTPException(404, "Attempt not found")
    if rec.get("audio_path"):                         # delete the file only once the row is gone
        (c.audio_dir / Path(rec["audio_path"]).name).unlink(missing_ok=True)
    adaptive.rebuild_after_delete(us, rec, user_now(request))
    return {"ok": True}


@router.get("/api/export")
def export(us: UserStore = Depends(user_store)):
    return JSONResponse(us.all_chronological(),
                        headers={"Content-Disposition": "attachment; filename=thought_to_speak_history.json"})
