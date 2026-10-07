"""Retry comparison, progress aggregation, and next-exercise recommendation."""
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import llm
from .analysis import DIMENSION_LABELS, DIMENSIONS
from .prompts import MODES, random_prompt

log = logging.getLogger("thought_to_speak")


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone()  # local time of the machine running the app


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 1) if xs else None


# --------------------------------------------------------------------------- compare
def compare(base: dict, cur: dict) -> dict:
    deltas = {}
    for d in ["overall"] + DIMENSIONS:
        a, b = base.get(d), cur.get(d)
        deltas[d] = None if a is None or b is None else b - a
    bm, cm = base["analysis"].get("metrics", {}), cur["analysis"].get("metrics", {})
    metric_deltas = {k: (cm.get(k, 0) or 0) - (bm.get(k, 0) or 0)
                     for k in ("filler_count", "filler_rate", "hedge_count", "word_count", "seconds", "wpm")}

    def present(a):
        return {x["label"] for x in a["analysis"].get("structure_map", []) if x.get("status") == "present"}
    gained = sorted(present(cur) - present(base))
    lost = sorted(present(base) - present(cur))

    improved = [d for d in DIMENSIONS if (deltas[d] or 0) >= 3]
    regressed = [d for d in DIMENSIONS if (deltas[d] or 0) <= -3]
    od = deltas["overall"] or 0
    verdict = "improved" if od >= 3 else "regressed" if od <= -3 else "steady"

    narrative = _heuristic_narrative(improved, regressed, metric_deltas, gained, lost, verdict)
    if llm.active_provider() != "local":
        try:
            narrative = _llm_narrative(base, cur, deltas) or narrative
        except Exception as e:
            log.warning("LLM compare failed, using local narrative: %s", e)

    return {
        "base_id": base["id"], "current_id": cur["id"],
        "base_attempt": base["attempt_no"], "current_attempt": cur["attempt_no"],
        "base_scores": {d: base.get(d) for d in ["overall"] + DIMENSIONS},
        "current_scores": {d: cur.get(d) for d in ["overall"] + DIMENSIONS},
        "deltas": deltas, "metric_deltas": metric_deltas,
        "base_fillers": bm.get("filler_count", 0) or 0, "current_fillers": cm.get("filler_count", 0) or 0,
        "framework_gained": gained, "framework_lost": lost,
        "verdict": verdict, **narrative,
    }


def _heuristic_narrative(improved, regressed, md, gained, lost, verdict):
    better, worse = [], []
    for d in improved:
        better.append(f"{DIMENSION_LABELS[d]} went up.")
    if gained:
        better.append(f"You added: {', '.join(gained)}.")
    if md["filler_count"] < 0:
        better.append(f"{abs(md['filler_count'])} fewer fillers.")
    for d in regressed:
        worse.append(f"{DIMENSION_LABELS[d]} dropped — check you didn't lose it while fixing something else.")
    if lost:
        worse.append(f"You dropped: {', '.join(lost)}.")
    if md["filler_count"] > 0:
        worse.append(f"{md['filler_count']} more fillers than before.")
    summary = {"improved": "Real progress — the retry loop is working.",
               "regressed": "This take was weaker overall. That's normal when you change approach; try once more.",
               "steady": "Roughly the same overall. Pick ONE dimension and exaggerate it on the next take."}[verdict]
    return {"summary": summary, "what_improved": better or ["No clear gains yet."],
            "still_to_work_on": worse or ["Nothing regressed — keep the gains and push the weakest dimension."]}


def _llm_narrative(base, cur, deltas):
    system = ("You compare two spoken attempts at the same prompt for a communication coach. "
              "Never rewrite the answer or provide sample phrasing. Return ONLY JSON: "
              '{"summary": str, "what_improved": [str], "still_to_work_on": [str]} '
              "with 1-3 short, specific items per list that cite what the speaker actually said.")
    user = (f"Mode: {cur['mode']}\nPrompt: {cur['prompt'] or '(free practice)'}\n"
            f"Score deltas (current - previous): {deltas}\n\n"
            f"PREVIOUS attempt #{base['attempt_no']}:\n\"\"\"{base['transcript']}\"\"\"\n\n"
            f"CURRENT attempt #{cur['attempt_no']}:\n\"\"\"{cur['transcript']}\"\"\"")
    data, _ = llm.complete_json(system, user, max_tokens=800)
    out = {"summary": str(data.get("summary", "")).strip(),
           "what_improved": [str(x) for x in data.get("what_improved", []) if str(x).strip()][:3],
           "still_to_work_on": [str(x) for x in data.get("still_to_work_on", []) if str(x).strip()][:3]}
    return out if out["summary"] else None


# --------------------------------------------------------------------------- recommendation
EXERCISES = {
    "structure": {
        "title": "Framework sprint",
        "why": "Listeners follow shape before content. Your answers have the ideas but not a reliable shape.",
        "steps": ["Before speaking, say the framework labels out loud ({framework}).",
                  "Speak one or two sentences per label — no more.",
                  "Retry once with the labels silent but the same order."],
        "constraint": "Hit every framework part in order.",
    },
    "clarity": {
        "title": "One-breath headline",
        "why": "Your main point gets buried. Clarity starts with a sentence someone could repeat back.",
        "steps": ["Start with a single sentence that states your answer — say it in one breath.",
                  "Support it with exactly two details.",
                  "End by repeating the headline in different words."],
        "constraint": "Headline in the first 10 seconds; max 2 supporting details.",
    },
    "relevance": {
        "title": "Answer-first drill",
        "why": "You drift from the question. Interviewers and customers score relevance before anything else.",
        "steps": ["Repeat the question's key words in your first sentence.",
                  "Answer it directly in that same sentence.",
                  "Only then add context. Cut anything that doesn't serve the question."],
        "constraint": "The first sentence must directly answer the prompt.",
    },
    "answer_quality": {
        "title": "Evidence ladder",
        "why": "Your answers stay general. Specific examples and numbers are what make an answer convincing.",
        "steps": ["Name one specific example (who, what, when).",
                  "Say what YOU did, using 'I'.",
                  "Close with a measurable result or a clear trade-off."],
        "constraint": "Include at least one number and one concrete example.",
    },
    "delivery": {
        "title": "Pause, don't fill",
        "why": "Fillers and hedges make you sound less certain than you are.",
        "steps": ["Speak at a deliberately slower pace.",
                  "Each time you feel a filler coming, close your mouth and pause for one beat.",
                  "Replace 'I think' with a direct statement at least twice."],
        "constraint": "Zero 'um/uh', at most 2 hedges.",
    },
}


def recommend(attempts: list[dict]) -> dict:
    scored = [a for a in attempts if all(a.get(d) is not None for d in DIMENSIONS)]
    recent = scored[-10:]
    if not recent:
        mode = "Interview"
        return {
            "kind": "baseline", "mode": mode, "dimension": None,
            "title": "Record your baseline",
            "why": "No full-scored attempts yet. A baseline lets the coach find your weak areas.",
            "evidence": None,
            "steps": ["Answer the prompt naturally — don't script it.", "Analyze, then retry once."],
            "constraint": "Speak for 60–90 seconds.", "challenge_mode": False,
            "prompt": random_prompt(mode), "secondary_tips": [],
        }

    dim_avgs = {d: _avg([a[d] for a in recent]) for d in DIMENSIONS}
    weakest = min(dim_avgs, key=dim_avgs.get)
    others = _avg([v for d, v in dim_avgs.items() if d != weakest])

    by_mode = defaultdict(list)
    for a in recent:
        by_mode[a["mode"]].append(a[weakest])
    mode = min(by_mode, key=lambda k: _avg(by_mode[k])) if by_mode else recent[-1]["mode"]

    ex = EXERCISES[weakest]
    framework = " → ".join(MODES.get(mode, MODES["Interview"])["framework"])
    gap = round(others - dim_avgs[weakest]) if others is not None else 0
    tips = []
    fill = Counter()
    for a in recent:
        fill.update((a["analysis"].get("metrics") or {}).get("fillers") or {})
    if fill and weakest != "delivery":
        w, n = fill.most_common(1)[0]
        tips.append(f"Your most frequent filler is '{w}' ({n}× in your last {len(recent)} takes).")
    chains = Counter(a["chain_id"] for a in scored)
    if chains and sum(1 for c in chains.values() if c == 1) / len(chains) > 0.6:
        tips.append("Most of your prompts end after one take. The retry is where improvement happens — always do take 2.")
    practiced = Counter(a["mode"] for a in scored)
    unpracticed = [m for m in MODES if practiced[m] == 0]
    if unpracticed:
        tips.append(f"Stretch: you haven't tried {unpracticed[0]} yet.")

    return {
        "kind": "weak_area", "mode": mode, "dimension": weakest,
        "dimension_label": DIMENSION_LABELS[weakest],
        "title": ex["title"], "why": ex["why"],
        "evidence": f"{DIMENSION_LABELS[weakest]} averaged {dim_avgs[weakest]:.0f} over your last "
                    f"{len(recent)} take{'s' if len(recent) != 1 else ''} — {gap} points below your other skills"
                    f"{', weakest in ' + mode if len(by_mode) > 1 else ''}.",
        "steps": [s.format(framework=framework) for s in ex["steps"]],
        "constraint": ex["constraint"], "challenge_mode": True,
        "prompt": random_prompt(mode), "secondary_tips": tips,
    }


# --------------------------------------------------------------------------- progress
def progress(attempts: list[dict]) -> dict:
    if not attempts:
        return {"total_attempts": 0, "recommendation": recommend([])}
    days = sorted({_dt(a["created_at"]).date() for a in attempts})
    today = datetime.now().astimezone().date()
    streak, d = 0, today if today in days else today - timedelta(days=1)
    dayset = set(days)
    while d in dayset:
        streak += 1
        d -= timedelta(days=1)

    overall = [a for a in attempts if a.get("overall") is not None]
    last10, prev10 = overall[-10:], overall[-20:-10]
    scored = [a for a in attempts if all(a.get(x) is not None for x in DIMENSIONS)]

    by_mode = defaultdict(list)
    for a in overall:
        by_mode[a["mode"]].append(a["overall"])

    chains = defaultdict(list)
    for a in overall:
        chains[a["chain_id"]].append(a)
    gains = [c[-1]["overall"] - c[0]["overall"] for c in chains.values() if len(c) >= 2]

    fill = Counter()
    for a in attempts[-20:]:
        fill.update((a["analysis"].get("metrics") or {}).get("fillers") or {})

    return {
        "total_attempts": len(attempts),
        "total_prompts": len({a["chain_id"] for a in attempts}),
        "practice_days": len(days),
        "streak_days": streak,
        "avg_overall_last10": _avg([a["overall"] for a in last10]),
        "avg_overall_prev10": _avg([a["overall"] for a in prev10]),
        "best_overall": max((a["overall"] for a in overall), default=None),
        "avg_retry_gain": _avg(gains),
        "retry_chains": len(gains),
        "trend": [{"date": a["created_at"], "overall": a["overall"], "mode": a["mode"],
                   "attempt_no": a["attempt_no"]} for a in overall[-40:]],
        "dimension_avgs": {d: _avg([a[d] for a in scored[-10:]]) for d in DIMENSIONS},
        "dimension_avgs_prev": {d: _avg([a[d] for a in scored[-20:-10]]) for d in DIMENSIONS},
        "dimension_labels": DIMENSION_LABELS,
        "by_mode": {m: {"count": len(v), "avg": _avg(v), "best": max(v)} for m, v in by_mode.items()},
        "top_fillers": fill.most_common(6),
        "avg_filler_rate": _avg([(a["analysis"].get("metrics") or {}).get("filler_rate") for a in attempts[-10:]]),
        "recommendation": recommend(attempts),
    }
