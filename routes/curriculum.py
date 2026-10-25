"""Learning content: journeys, lessons, the daily plan, programs, pronunciation drills and the word bank."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from coach import adaptive, insights, language, pronunciation
from coach.storage import UserStore

from .deps import Context, get_ctx, user_now, user_store

router = APIRouter(tags=["curriculum"])


@router.get("/api/journey")
def journey(us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    return c.curriculum.journey_view(us.lesson_stats())


@router.get("/api/lessons/{lesson_id}")
def lesson_detail(lesson_id: str, request: Request, us: UserStore = Depends(user_store),
                  c: Context = Depends(get_ctx)):
    cur = c.curriculum
    les = cur.get(lesson_id)
    if not les:
        raise HTTPException(404, "Unknown lesson")
    now = user_now(request)
    if les.get("program_id"):
        pid = les["program_id"]
        st = cur.program_state(us, pid, now)
        day = next(d for w in st["weeks"] for d in w["days"] if d["id"] == lesson_id)
        return {**les, "progress": us.lesson_stats().get(lesson_id, {}), "next_lesson_id": None,
                "index": les["day"], "count": st["total"],
                "program": {"id": pid, "title": st["title"], "day": les["day"], "status": day["status"],
                            "enrolled": st["enrolled"], "gate": cur.program_gate(us, les, now)}}
    order = [i for i in cur.order if cur.get(i)["journey_id"] == les["journey_id"]]
    idx = order.index(lesson_id)
    nxt = order[idx + 1] if idx + 1 < len(order) else None
    out = {**les, "progress": us.lesson_stats().get(lesson_id, {}), "next_lesson_id": nxt,
           "index": idx + 1, "count": len(order)}
    if cur.is_read_aloud(les):
        st = pronunciation.get_set(les["exercise"]["set"])
        out["read_aloud"] = {"set": st["id"], "title": st["title"], "tip": st.get("tip"), "items": st["items"],
                             "pairs": st.get("pairs", [])}
    return out


@router.get("/api/today")
def today(request: Request, us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    return c.curriculum.today(us, user_now(request))


@router.get("/api/programs")
def programs(request: Request, us: UserStore = Depends(user_store), c: Context = Depends(get_ctx)):
    now = user_now(request)
    return [c.curriculum.program_state(us, pid, now) for pid in c.curriculum.programs]


@router.post("/api/programs/{program_id}/start")
def start_program(program_id: str, request: Request, us: UserStore = Depends(user_store),
                  c: Context = Depends(get_ctx)):
    """Enroll in a program — or restart it from Day 1 (earlier takes stay in your history)."""
    if program_id not in c.curriculum.programs:
        raise HTTPException(404, "Unknown program")
    now = user_now(request)
    c.curriculum.enroll(us, program_id, now)
    return c.curriculum.program_state(us, program_id, now)


@router.get("/api/baseline")
def baseline(us: UserStore = Depends(user_store)):
    """The 3-take baseline assessment: which items are done, and the starting-point summary when complete."""
    return adaptive.baseline_view(us)


@router.get("/api/skills")
def skills(request: Request, us: UserStore = Depends(user_store)):
    """Per-skill spaced-review schedule with a plain-language reason for each skill's current box."""
    return adaptive.skills_view(us, user_now(request))


@router.get("/api/weekly")
def weekly(request: Request, us: UserStore = Depends(user_store)):
    """This week's unseen challenge and the retained-skill history (first takes only)."""
    return adaptive.weekly_challenge(us, user_now(request), us.user_id)


@router.get("/api/pronounce/sets")
def pronounce_sets():
    out = []
    for st in pronunciation.drill_sets():
        out.append({**st, "items": [{**i, "focus_info": [pronunciation.describe(w) for w in i.get("focus", [])]}
                                    for i in st["items"]],
                    "pairs_info": [[pronunciation.describe(a), pronunciation.describe(b)] for a, b in st.get("pairs", [])]})
    return out


@router.get("/api/pronounce/lookup")
def pronounce_lookup(words: str = Query(..., max_length=500)):
    return [pronunciation.describe(w) for w in words.replace(",", " ").split()[:40]]


@router.get("/api/vocab/today")
def vocab_today(request: Request):
    return language.daily_words(user_now(request).date())


@router.get("/api/vocab/bank")
def vocab_bank(us: UserStore = Depends(user_store)):
    used = dict(insights.language_progress(us.all_chronological())["words_used"])
    return [{**w, "times_used": used.get(w["word"], 0)} for w in language.word_bank()]
