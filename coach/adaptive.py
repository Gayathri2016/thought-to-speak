"""Adaptive learning: a baseline assessment, per-skill spaced reviews, and an unseen weekly challenge.

Skills are the five scored dimensions. Every scored take (not read-aloud drills) is evidence for each skill.

Spaced reviews — a Leitner schedule, chosen because every decision can be explained in one sentence:
  boxes:      1 → 2 → 3 → 4 → 5 → 6      review every 1, 2, 4, 7, 14, 30 days
  pass  (≥ 75) on or after the due date  → up one box   ("you've held it — see it less often")
  pass  (≥ 75) before it's due           → no change    (no credit for cramming the same day)
  fail  (< 60) any time                  → back to box 1 ("review it tomorrow")
  partial (60–74)                        → same box, rescheduled from today
Takes scored by a different scorer than the skill's last evidence still count (scores are on the same 0–100
scale), but the explanation names the score that moved it.

Weekly challenge — one prompt per ISO week (in the user's timezone) from a held-out bank the app never uses
elsewhere, never a prompt the user has answered before. Only the first take of that prompt counts, so the weekly
score measures retained skill on an unseen prompt — separate from same-prompt retry gains.
"""
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .analysis import DIMENSION_LABELS, DIMENSIONS
from . import scoring

CONTENT = Path(__file__).resolve().parent.parent / "content" / "assessments.json"
INTERVALS = [1, 2, 4, 7, 14, 30]
PASS, FAIL = 75, 60
SKILL_KEY = "skills"
BASELINE_KEY = "baseline"
SKILL_NAMES = {**DIMENSION_LABELS, "answer_quality": "Substance", "delivery": "Delivery"}


def _load():
    return json.loads(CONTENT.read_text(encoding="utf-8"))


ASSESS = _load()
BASELINE = ASSESS["baseline"]
WEEKLY_BANK = ASSESS["weekly"]


def _local_date(iso: str, tz) -> date:
    return datetime.fromisoformat(iso).astimezone(tz).date()


def _fmt(d: date) -> str:
    return f"{d:%b} {d.day}"                          # "Oct 24" (portable: no platform-specific %-d)


# ----------------------------------------------------------------------------------------------- spaced reviews
def update_skill(state: dict | None, score: int, day: date) -> tuple[dict, str]:
    """Apply one observation to one skill's Leitner state. Returns (new_state, plain-language reason)."""
    if state is None:                                 # first evidence: place it by level
        box = 0 if score < FAIL else 1 if score < PASS else 2
        new = {"box": box, "due": (day + timedelta(days=INTERVALS[box])).isoformat(),
               "last_score": score, "last_seen": day.isoformat(), "reviews": 1}
        return new, f"Started in box {box + 1} from your first score ({score})."
    box, due = state["box"], date.fromisoformat(state["due"])
    if score < FAIL:
        box, why = 0, f"Scored {score} (under {FAIL}) on {_fmt(day)} — back to box 1, review tomorrow."
    elif score >= PASS and day >= due:
        box = min(box + 1, len(INTERVALS) - 1)
        why = f"Scored {score} ({PASS}+) on {_fmt(day)} when it was due — moved up to box {box + 1}."
    elif score >= PASS:
        return ({**state, "last_score": score, "last_seen": day.isoformat(), "reviews": state["reviews"] + 1},
                f"Scored {score} on {_fmt(day)}, before it was due — schedule unchanged (no credit for cramming).")
    else:
        why = f"Scored {score} ({FAIL}–{PASS - 1}) on {_fmt(day)} — stays in box {box + 1}, rescheduled."
    new = {"box": box, "due": (day + timedelta(days=INTERVALS[box])).isoformat(), "last_score": score,
           "last_seen": day.isoformat(), "reviews": state["reviews"] + 1}
    return new, why


APPLIED = "_applied"          # ids of takes already applied to the schedule (written in the same value → exactly once)
APPLIED_KEEP = 200


def _scorable(attempt: dict) -> bool:
    a = attempt.get("analysis") or {}
    return not (attempt.get("kind") == "read_aloud" or a.get("kind") == "read_aloud")


def _apply(skills: dict, attempt: dict, tz) -> None:
    scores = (attempt.get("analysis") or {}).get("scores") or {}
    day = _local_date(attempt["created_at"], tz)
    for d in DIMENSIONS:
        if isinstance(scores.get(d), int):
            new, why = update_skill(skills.get(d), scores[d], day)
            skills[d] = {**new, "why": why}
    skills[APPLIED] = (skills.get(APPLIED, []) + [attempt["id"]])[-APPLIED_KEEP:]


def record_take(store, attempt: dict, now: datetime) -> None:
    """Update skill schedules (and baseline progress) after a saved take. Idempotent: a take that a recovered
    job finalizes again is applied once (its id is stored with the schedule in the same write)."""
    if not _scorable(attempt):
        return
    skills = store.meta_get(SKILL_KEY) or {}
    if attempt["id"] not in skills.get(APPLIED, []):
        _apply(skills, attempt, now.tzinfo)
        store.meta_set(SKILL_KEY, skills)
    if attempt.get("kind") == "baseline":
        item = next((b for b in BASELINE if b["prompt"] == attempt.get("prompt")), None)
        if item:
            base = store.meta_get(BASELINE_KEY) or {"items": {}}
            if item["key"] not in base["items"]:                       # the first take of each item counts
                base["items"][item["key"]] = attempt["id"]
                store.meta_set(BASELINE_KEY, base)


def rebuild_after_delete(store, deleted: dict, now: datetime) -> None:
    """A deleted take must stop counting: replay the skill schedule from the remaining takes (oldest first —
    the same rule, same order) and re-pick each baseline item's first remaining take."""
    if not _scorable(deleted):
        return
    remaining = [a for a in store.all_chronological() if _scorable(a)]
    skills: dict = {}
    for a in remaining:
        _apply(skills, a, now.tzinfo)
    store.meta_set(SKILL_KEY, skills)
    base = store.meta_get(BASELINE_KEY)
    if base and deleted["id"] in base.get("items", {}).values():
        by_prompt = {b["prompt"]: b["key"] for b in BASELINE}
        items: dict = {}
        for a in remaining:
            k = by_prompt.get(a.get("prompt"))
            if a.get("kind") == "baseline" and k:
                items.setdefault(k, a["id"])
        store.meta_set(BASELINE_KEY, {**base, "items": items})


def skills_view(store, now: datetime) -> dict:
    today = now.date()
    skills = store.meta_get(SKILL_KEY) or {}
    out = []
    for d in DIMENSIONS:
        s = skills.get(d)
        if not s:
            out.append({"skill": d, "label": SKILL_NAMES[d], "status": "new", "box": None, "due": None,
                        "why": "No evidence yet — do the baseline or any practice take."})
            continue
        due = date.fromisoformat(s["due"])
        days = (due - today).days
        out.append({"skill": d, "label": SKILL_NAMES[d], "box": s["box"] + 1, "boxes": len(INTERVALS),
                    "interval_days": INTERVALS[s["box"]], "due": s["due"], "last_score": s["last_score"],
                    "status": "due" if days <= 0 else "scheduled", "due_in_days": days, "why": s.get("why", "")})
    due = sorted([x for x in out if x["status"] == "due"], key=lambda x: (x["box"], x["last_score"]))
    return {"skills": out, "due": [x["skill"] for x in due], "rule":
            f"Score {PASS}+ when a skill is due to move it up a box (reviews every "
            f"{', '.join(map(str, INTERVALS))} days); under {FAIL} sends it back to box 1."}


def next_review(store, now: datetime) -> dict | None:
    """The most urgent due skill (lowest box, then lowest last score), or None."""
    v = skills_view(store, now)
    if not v["due"]:
        return None
    return next(x for x in v["skills"] if x["skill"] == v["due"][0])


# ----------------------------------------------------------------------------------------------- baseline
def baseline_view(store) -> dict:
    base = store.meta_get(BASELINE_KEY) or {"items": {}}
    items = [{**b, "done": b["key"] in base["items"], "attempt_id": base["items"].get(b["key"])} for b in BASELINE]
    done = all(i["done"] for i in items)
    summary = None
    if done:
        takes = [store.get(i["attempt_id"]) for i in items]
        takes = [t for t in takes if t]
        if takes:
            avg = {d: round(sum(t.get(d) or 0 for t in takes) / len(takes)) for d in DIMENSIONS}
            strongest, weakest = max(avg, key=avg.get), min(avg, key=avg.get)
            summary = {"averages": avg, "strongest": strongest, "weakest": weakest,
                       "text": f"Your starting point: strongest in {SKILL_NAMES[strongest].lower()} "
                               f"({avg[strongest]}), most room to grow in {SKILL_NAMES[weakest].lower()} "
                               f"({avg[weakest]}). Reviews are scheduled from these scores."}
    return {"complete": done, "items": items, "summary": summary, "minutes": 3}


# ----------------------------------------------------------------------------------------------- weekly challenge
def week_key(now: datetime) -> str:
    y, w, _ = now.date().isocalendar()
    return f"{y}-W{w:02d}"


def weekly_challenge(store, now: datetime, user_id: str) -> dict:
    """This week's unseen challenge (stable for the week), whether it's done, and the retained-skill history."""
    wk = week_key(now)
    key = f"weekly:{wk}"
    assigned = store.meta_get(key)
    attempts = store.all_chronological()
    if not assigned:
        seen = {a.get("prompt") for a in attempts}
        used = {(store.meta_get(f"weekly:{w}") or {}).get("prompt") for w in _recent_weeks(now, 60)}
        fresh = [p for p in WEEKLY_BANK if p["prompt"] not in seen and p["prompt"] not in used]
        pool = fresh or [p for p in WEEKLY_BANK if p["prompt"] not in seen] or WEEKLY_BANK
        idx = int(hashlib.sha256(f"{user_id}:{wk}".encode()).hexdigest(), 16) % len(pool)
        assigned = {**pool[idx], "week": wk, "assigned_at": now.isoformat()}
        store.meta_set(key, assigned)
    history = weekly_history(store, attempts)
    this = next((h for h in history if h["week"] == wk), None)
    return {"week": wk, "mode": assigned["mode"], "prompt": assigned["prompt"], "done": bool(this),
            "score": this["overall"] if this else None, "history": history,
            "why": "A new, unseen prompt each week. Only your first take counts, so this measures what you've "
                   "retained — not how much a retry helped."}


def _recent_weeks(now: datetime, n: int) -> list[str]:
    return [week_key(now - timedelta(weeks=i)) for i in range(n)]


def weekly_history(store, attempts: list[dict] | None = None) -> list[dict]:
    """First take of each week's challenge, with comparability to the latest one."""
    attempts = attempts if attempts is not None else store.all_chronological()
    firsts = [a for a in attempts if a.get("kind") == "weekly" and a.get("attempt_no") == 1
              and a.get("overall") is not None]
    out, seen_weeks = [], set()
    for a in firsts:
        wk = (a.get("analysis") or {}).get("weekly_week") or a["created_at"][:10]
        if wk in seen_weeks:
            continue
        seen_weeks.add(wk)
        out.append({"week": wk, "overall": a["overall"], "prompt": a["prompt"], "attempt_id": a["id"],
                    "scoring_key": list(scoring.scoring_key(a))})
    if out:
        latest = out[-1]["scoring_key"]
        for h in out:
            h["comparable"] = h["scoring_key"] == latest
    return out
