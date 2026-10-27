"""Three-angle review of a take: Evaluator (interviewer / audience), Coach (what to do next) and Mentor
(patterns across recent takes), plus the answer broken into its framework parts.

`local_review` works with no API key — it quotes the speaker's own sentences and builds everything from
deterministic signals. When an LLM is configured, `merge_llm_review` replaces the parts the model wrote
well and keeps the local version for anything missing, so the panel is always complete.
"""
import re

from . import corrections as corrections_mod
from . import rewrite
from . import metrics

# Who is listening, per mode — the evaluator speaks as this person.
ROLES = {
    "Everyday Conversation": {"role": "Your conversation partner", "question": "Was it easy to respond to you?",
                              "labels": {"strong_yes": "Easy to connect", "lean_yes": "Welcoming",
                                         "lean_no": "Needs an opening", "no": "Try a simpler start"}},
    "Tell a Story": {"role": "Your listener", "question": "Could they follow your story?",
                     "labels": {"strong_yes": "Easy to follow", "lean_yes": "Mostly clear",
                                "lean_no": "Needs a clearer thread", "no": "Try a simpler story"}},
    "Share an Opinion": {"role": "Your listener", "question": "Did they understand your view and your reason?",
                         "labels": {"strong_yes": "Clear and supported", "lean_yes": "Mostly clear",
                                    "lean_no": "Needs a reason", "no": "Start with your view"}},
    "Difficult Conversations": {"role": "The other person", "question": "Was your request clear and respectful?",
                                "labels": {"strong_yes": "Clear and considerate", "lean_yes": "Mostly clear",
                                           "lean_no": "Needs a clearer request", "no": "Try a gentler opening"}},
    "Interview": {"role": "Interviewer", "question": "Would this answer move you to the next round?",
                  "labels": {"strong_yes": "Strong yes", "lean_yes": "Lean yes", "lean_no": "Lean no",
                             "no": "Not yet"}},
    "Leadership": {"role": "Leadership team", "question": "Would they back your message?",
                   "labels": {"strong_yes": "Fully on board", "lean_yes": "Mostly on board",
                              "lean_no": "Unconvinced", "no": "Lost them"}},
    "Technical Explanation": {"role": "Your audience", "question": "Did they understand it the first time?",
                              "labels": {"strong_yes": "Crystal clear", "lean_yes": "Mostly clear",
                                         "lean_no": "Partly lost", "no": "Lost"}},
    "Toastmasters": {"role": "The audience", "question": "Did the speech land?",
                     "labels": {"strong_yes": "Hooked", "lean_yes": "Engaged", "lean_no": "Drifting",
                                "no": "Lost them"}},
    "FDE / Customer": {"role": "The customer", "question": "Would they trust your recommendation?",
                       "labels": {"strong_yes": "Ready to move ahead", "lean_yes": "Interested",
                                  "lean_no": "Skeptical", "no": "Not convinced"}},
}
VERDICTS = ("strong_yes", "lean_yes", "lean_no", "no")

_PRONOUN_I = re.compile(r"\b(i|i'm|i've|i'd|my|me)\b")
_PRONOUN_WE = re.compile(r"\b(we|we're|we've|our|us|the team)\b")
_NUM_UNIT = re.compile(r"\$?\d[\d,.]*\s?(?:%|percent|x\b|k\b|ms\b|seconds|minutes|hours|days|weeks|months|users|"
                       r"customers|dollars|million|thousand)|\$\d[\d,.]*|\b(?:two|three|four|five|six|seven|eight|nine|"
                       r"ten|twenty|thirty|fifty|hundred)\s+(?:percent|seconds|minutes|hours|days|weeks|months|users|"
                       r"customers|dollars)", re.I)
# What each common framework part should contain — used to make "missing part" fixes concrete.
PART_HINTS = {
    "Opening": "a friendly hello and a little context", "Detail": "one thing about your experience or interest",
    "Question": "an easy question that invites them to share", "Point": "your view or need in plain words",
    "Reason": "why it matters to you", "Example": "a familiar example", "Point again": "your main idea, briefly",
    "Situation": "where and when, in one sentence", "Task": "what you were responsible for",
    "Action": "what you did — say “I”, not “we”", "Result": "the outcome, with a number",
    "Lesson": "what you learned or would do differently", "Decision / Ask": "your decision or ask, first",
    "Why": "the reason, tied to a goal", "Trade-offs": "what you're giving up and why it's worth it",
    "Next steps": "who does what, by when", "Next step": "who does what, by when",
    "Definition": "what it is, in plain words", "Example / analogy": "a picture they already know",
    "How it works": "the steps, in order", "Hook": "a question, number or tiny story",
    "Message": "the one idea you want them to remember", "Story / points": "a real example that proves it",
    "Call-back close": "a last line that returns to your opening", "Acknowledge": "show you heard their concern",
    "Restate problem": "their problem, in their words", "Recommendation": "what you recommend, plainly",
    "Risks": "what could go wrong and how you'd handle it",
}
_BACKGROUND_OPENERS = ("so ", "um", "uh", "well", "basically", "okay", "ok ", "yeah", "i mean", "like ",
                       "there was", "back in", "a while ago", "so,", "so basically")


# ----------------------------------------------------------------------------------------------- sentences
def split_sentences(text: str) -> list[str]:
    """Sentences from punctuation; for unpunctuated speech-to-text, ~18-word chunks cut at natural joins."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [s.strip() for s in metrics.SENT_SPLIT.split(text) if s.strip()]
    if len(parts) > 1 or len(text.split()) <= 25:
        return parts
    clauses = [" ".join(w) for w in rewrite._raw_sentences(text)]
    if len(clauses) > 1 and max(len(c.split()) for c in clauses) <= 30:
        return clauses
    out, cur = [], []
    for w in text.split():
        joiner = w.lower().strip(",") in {"and", "so", "but", "because", "then"}
        if cur and ((len(cur) >= 14 and joiner) or len(cur) >= 24):
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return [s for s in out if s]


def _short(s: str, n: int = 24) -> str:
    w = s.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def _hedged(s: str) -> bool:
    return metrics.hedge_count(s) > 0


def _filler_n(s: str) -> int:
    return sum(metrics.filler_breakdown(s).values())


def _pick(sents: list[str], pred, used: set) -> str | None:
    """First sentence matching `pred` that hasn't been quoted yet in this list."""
    for s in sents:
        if _short(s) not in used and pred(s):
            used.add(_short(s))
            return s
    return None


def _warmup(s: str) -> bool:
    return s.lower().strip().startswith(_BACKGROUND_OPENERS)


# ----------------------------------------------------------------------------------------------- breakdown
def breakdown(sents: list[str], labels: list[str], cues: dict) -> dict:
    """Which of the speaker's sentences does each framework part? Returns parts in framework order plus a note
    when the parts came out of order (e.g. the result before the situation)."""
    found = {}
    for lab in labels:
        for i, s in enumerate(sents):
            if any(c in " " + s.lower() + " " for c in cues.get(lab, [])):
                found[lab] = i
                break
    parts = [{"label": lab, "status": "present" if lab in found else "missing",
              "quote": _short(sents[found[lab]]) if lab in found else None,
              "position": found.get(lab)} for lab in labels]
    order = [p["position"] for p in parts if p["position"] is not None]
    order_note = None
    if order != sorted(order):
        seq = [p["label"] for p in sorted((p for p in parts if p["position"] is not None),
                                          key=lambda p: p["position"])]
        order_note = f"You said these parts in the order {' → '.join(seq)}. Listeners follow " \
                     f"{' → '.join(p['label'] for p in parts if p['position'] is not None)} more easily."
    return {"parts": parts, "order_note": order_note}


# ----------------------------------------------------------------------------------------------- evaluator
def _verdict(scores: dict) -> str:
    core = (scores["structure"] + scores["relevance"] + scores["answer_quality"] + scores["clarity"]) / 4
    return "strong_yes" if core >= 76 else "lean_yes" if core >= 64 else "lean_no" if core >= 50 else "no"


def _follow_up(mode: str, text: str, parts: list[dict], m: dict) -> str:
    low = text.lower()
    missing = {p["label"] for p in parts if p["status"] == "missing"}
    num = _NUM_UNIT.search(text)
    if mode == "Everyday Conversation":               # what a friend would actually ask back
        hl = rewrite.highlights(text, 1)
        if rewrite.answer_kind(text) == "day":
            return f"Oh nice — how did {hl[0]} go?" if hl else "What was the best part of your day?"
        return f"What did you like most about {hl[0]}?" if hl else "Oh really — what happened next?"
    everyday = {
        "Tell a Story": "How did that experience change the way you do things?",
        "Share an Opinion": "Can you share an everyday example that supports your view?",
        "Difficult Conversations": "What would be a fair next step for both of you?",
    }
    if mode in everyday:
        return everyday[mode]
    if mode == "Interview":
        if "Result" in missing:
            return "What was the outcome — and how did you measure it?"
        if not _PRONOUN_I.search(low) or len(_PRONOUN_WE.findall(low)) > 2 * len(_PRONOUN_I.findall(low)):
            return "You said “we” a lot — what did you personally do?"
        if num:
            return f"You mentioned “{num.group(0).strip(' .,')}”. How did you measure that, and what was the baseline?"
        return "Looking back, what would you do differently?"
    if mode == "Leadership":
        if "Trade-offs" in missing:
            return "What are we giving up by doing this?"
        if "Next steps" in missing:
            return "Who owns this, and by when?"
        return "What would make you change this decision?"
    if mode == "Technical Explanation":
        if "Example / analogy" in missing:
            return "Can you give me an example I'd recognise?"
        if "Trade-offs" in missing:
            return "When would you not use this?"
        return "How would this fail in production?"
    if mode == "FDE / Customer":
        if "Risks" in missing:
            return "What could go wrong, and how would you handle it?"
        if "Next step" in missing:
            return "So what happens next, and who does it?"
        return "What will this cost us, and how long until we see value?"
    if "Story / points" in missing:
        return "Can you give us a real example of that?"
    return "What's the one thing you want us to do differently after hearing this?"


def evaluator(mode: str, sents: list[str], text: str, scores: dict, parts: list[dict], m: dict) -> dict:
    role = ROLES.get(mode, ROLES["Interview"])
    verdict = _verdict(scores)
    if verdict == "lean_yes" and all(p["status"] == "present" for p in parts) and m["has_numbers"] \
            and m["hedge_count"] <= 1 and m["filler_rate"] < 3:
        verdict = "strong_yes"   # every part there, quantified and confident
    landed, doubts = [], []
    first = sents[0] if sents else ""
    first_low = first.lower().strip()
    present = [p for p in parts if p["status"] == "present"]
    missing = [p["label"] for p in parts if p["status"] == "missing"]

    result_cues = ("result", "reduced", "increased", "improved", "saved", "cut ", "grew", "approved", "launched",
                   "delivered", "dropped", "doubled")
    for s in sents:
        low = s.lower()
        if len(landed) >= 3:
            break
        if _NUM_UNIT.search(s) and any(c in low for c in result_cues):
            landed.append({"quote": _short(s), "why": "A measurable result — exactly what I listen for."})
        elif re.search(r"\bi (decided|built|led|proposed|created|designed|wrote|ran|set up|mapped|automated|"
                       r"reached out|talked|owned|shared|chose|pushed)\b", low):
            landed.append({"quote": _short(s), "why": "Clear personal ownership — I can see what you did."})
        elif re.search(r"\b(i learned|lesson|taught me|next time|going forward)\b", low):
            landed.append({"quote": _short(s), "why": "Reflection — shows you grow from experience."})
        elif _NUM_UNIT.search(s):
            landed.append({"quote": _short(s), "why": "Specific detail — it makes the story believable."})
    if present and not landed:
        p = present[0]
        landed.append({"quote": p["quote"], "why": f"This clearly does the “{p['label']}” part."})

    used = set()
    opening_issue = bool(first) and (_warmup(first) or (parts and parts[0]["status"] == "missing" and mode != "Interview"))
    warm = bool(first) and _warmup(first)
    if opening_issue:
        used.add(_short(first))
        doubts.append({"quote": _short(first), "why": "The opening is warm-up, not your point — I'm still waiting "
                                                      "to hear what you're telling me." if warm else
                       "You went straight into details — one opening line would tell me what this is about."})
    hedge = _pick(sents, _hedged, used)
    if hedge:
        doubts.append({"quote": _short(hedge), "why": "Hedging here makes you sound unsure of your own work."})
    if mode == "Interview":
        low = text.lower()
        if len(_PRONOUN_WE.findall(low)) > 2 * max(1, len(_PRONOUN_I.findall(low))):
            we = _pick(sents, lambda s: bool(_PRONOUN_WE.search(s.lower())), used)
            doubts.append({"quote": _short(we) if we else None,
                           "why": "Lots of “we” — I can't tell what you personally did."})
    vague = _pick(sents, lambda s: any(v in re.findall(r"[a-z']+", s.lower()) for v in ("stuff", "things", "issues")),
                  used)
    if vague and len(doubts) < 3:
        doubts.append({"quote": _short(vague), "why": "Vague words here — name the actual thing."})
    if missing and len(doubts) < 3:
        doubts.append({"quote": None, "why": f"I never heard the “{missing[0]}” part."})
    hedge = hedge or next((s for s in sents if _hedged(s)), None)

    to_yes = []
    if missing:
        lab = missing[0]
        to_yes.append(f"Add the {lab} part" + (f" — {PART_HINTS[lab]}." if lab in PART_HINTS else "."))
    if opening_issue and warm:
        to_yes.append("Cut the warm-up and open with your main point.")
    if not m["has_numbers"] and mode in ("Interview", "FDE / Customer", "Leadership"):
        to_yes.append("Put a number on the outcome.")
    if hedge:
        to_yes.append("Drop the hedges and state it plainly.")
    if not to_yes:
        to_yes.append("Make it even sharper: same content in two-thirds of the words." if verdict == "strong_yes"
                      else "Tighten it by a third — same content, fewer words.")

    label = role["labels"][verdict]
    summary = {
        "strong_yes": "A clear, credible answer. I'd want to hear more from you.",
        "lean_yes": "Good substance, but a couple of gaps keep it from being memorable.",
        "lean_no": "There's something here, but I had to work to find your point.",
        "no": "I couldn't follow what you were telling me yet.",
    }[verdict]
    return {"role": role["role"], "question": role["question"], "verdict": verdict, "verdict_label": label,
            "summary": summary, "landed": landed[:3], "doubts": doubts[:3],
            "follow_up": _follow_up(mode, text, parts, m), "to_strong_yes": to_yes[:2]}


# ----------------------------------------------------------------------------------------------- coach
DRILLS = {
    "structure": {"title": "The 3-line skeleton", "steps": [
        "Before speaking, say only the first word of each framework part out loud.",
        "Answer again using exactly one sentence per part.",
        "Record it — check every part is there, in order."]},
    "clarity": {"title": "Ten-word headline", "steps": [
        "Say your whole answer in ten words or fewer.",
        "Use that headline as your first sentence.",
        "Add at most two supporting sentences."]},
    "relevance": {"title": "Echo the question", "steps": [
        "Repeat the key words of the question in your first sentence.",
        "Answer it directly before adding any context.",
        "End by linking back to the question."]},
    "answer_quality": {"title": "Name, number, result", "steps": [
        "Pick one specific example — a real project, person or moment.",
        "Add one number: %, time, money or people.",
        "Finish with what changed because of it."]},
    "delivery": {"title": "Pause instead of filler", "steps": [
        "Read your first two sentences aloud slowly.",
        "Every time a filler is coming, close your mouth and pause one beat.",
        "Record 30 seconds — aim for zero 'um/uh'."]},
}


def coach(mode: str, sents: list[str], scores: dict, parts: list[dict], order_note: str | None,
          m: dict, framework: list[str], off_topic: dict | None = None) -> dict:
    fixes, used = [], set()
    if off_topic:                                     # answering the question beats every polish fix
        fixes.append({"quote": None, "problem": off_topic["message"], "fix": off_topic["retry"]
                      + (f" For example: “{off_topic['starter']}”" if off_topic.get("starter") else "")})
    first = sents[0] if sents else ""
    if first and (_warmup(first) or len(first.split()) > 30):
        used.add(_short(first))
        opener = " ".join(first.split()[:3])
        fixes.append({"quote": _short(first), "problem": "Your opening is warm-up — the point arrives late.",
                      "fix": f"Drop “{opener}…” and open with your "
                             f"{framework[0] if framework else 'main point'} in one crisp sentence."})
    # fillers make the top fixes only when they're dense (2 in a 100-word stretch is not the problem)
    filler = _pick(sorted(sents, key=_filler_n, reverse=True),
                   lambda s: _filler_n(s) >= 3 or (_filler_n(s) >= 2 and len(s.split()) <= 25), used)
    if filler:
        fixes.append({"quote": _short(filler), "problem": f"{_filler_n(filler)} fillers in one sentence.",
                      "fix": "Say this sentence again with a silent pause where each filler was."})
    hedge = _pick(sents, _hedged, used)
    if hedge:
        fixes.append({"quote": _short(hedge), "problem": "Hedging (“I think”, “maybe”) weakens it.",
                      "fix": "Say it as a statement. Keep a hedge only where you genuinely aren't sure."})
    long_s = _pick(sorted(sents, key=lambda s: len(s.split()), reverse=True),
                   lambda s: len(s.split()) > 35 and m.get("punctuated"), used)   # unpunctuated speech-to-text: not their fault
    if long_s:
        fixes.append({"quote": _short(long_s), "problem": f"A {len(long_s.split())}-word sentence is hard to follow.",
                      "fix": "Split it into two or three short sentences — one idea each."})
    for p in parts:
        if p["status"] == "missing":
            hint = PART_HINTS.get(p["label"])
            if mode == "Tell a Story" and p["label"] == "Result":
                hint = "how it turned out and what it meant to you"
            fixes.append({"quote": None, "problem": f"No “{p['label']}” part.",
                          "fix": f"Add one sentence for {p['label']}" + (f": {hint}." if hint else ".")})
    if order_note:
        fixes.append({"quote": None, "problem": "Parts out of order.", "fix": order_note})
    if not m["has_numbers"] and mode in ("Interview", "FDE / Customer", "Leadership"):
        fixes.append({"quote": None, "problem": "No numbers anywhere.",
                      "fix": "Quantify one result: a percentage, time saved, money or people affected."})

    weakest = "relevance" if off_topic else \
        min(("structure", "clarity", "relevance", "answer_quality", "delivery"), key=lambda d: scores[d])
    # A short game plan for the next take — the single biggest fix plus habits, not a repeat of the fix list.
    do_next = [fixes[0]["fix"]] if fixes else ["Keep the same shape and cut 20% of the words."]
    if framework:
        do_next.append(f"Before you hit record, say the parts out loud: {' → '.join(framework)}.")
    if m["filler_rate"] >= 3:
        do_next.append("When you feel an “um” coming, pause silently instead.")
    elif not m["has_numbers"] and mode in ("Interview", "FDE / Customer", "Leadership"):
        do_next.append("Say one number out loud — %, time, money or people.")
    return {"fixes": fixes[:3], "do_next": do_next[:3], "drill": DRILLS[weakest], "stronger_version": None,
            "focus": weakest}


# ----------------------------------------------------------------------------------------------- mentor
def history_digest(history: list[dict]) -> dict:
    """Summarise recent free/lesson takes (newest last) for the mentor."""
    takes = [h for h in history if (h.get("analysis") or {}).get("kind") != "read_aloud" and h.get("overall")]
    if not takes:
        return {"count": 0}
    dims = ("structure", "clarity", "relevance", "answer_quality", "delivery")
    avgs = {d: round(sum(t.get(d) or 0 for t in takes) / len(takes)) for d in dims}
    missing = {}
    for t in takes:
        for p in (t["analysis"].get("structure_map") or []):
            if p.get("status") == "missing":
                missing[p["label"]] = missing.get(p["label"], 0) + 1
    fill = [((t["analysis"].get("metrics") or {}).get("filler_rate") or 0) for t in takes]
    hedges = [((t["analysis"].get("metrics") or {}).get("hedge_count") or 0) for t in takes]
    half = max(1, len(takes) // 2)
    early, late = takes[:half], takes[half:] or takes[-1:]
    trend = {d: round(sum(t.get(d) or 0 for t in late) / len(late) - sum(t.get(d) or 0 for t in early) / len(early))
             for d in dims} if len(takes) >= 3 else {}
    return {"count": len(takes), "avgs": avgs, "weakest": min(avgs, key=avgs.get), "strongest": max(avgs, key=avgs.get),
            "recurring_missing": sorted(missing.items(), key=lambda kv: -kv[1])[:2],
            "avg_filler_rate": round(sum(fill) / len(fill), 1), "avg_hedges": round(sum(hedges) / len(hedges), 1),
            "trend": trend, "overall": [t["overall"] for t in takes]}


LABEL = {"structure": "structure", "clarity": "clarity", "relevance": "staying on the question",
         "answer_quality": "substance", "delivery": "delivery"}
HABITS = {
    "structure": "Say your framework's first word out loud before every answer this week.",
    "clarity": "Start every answer this week with a one-sentence headline.",
    "relevance": "Begin every answer this week by echoing the question's key words.",
    "answer_quality": "Put one number and one name in every answer this week.",
    "delivery": "Replace every 'um' with a silent pause this week — slower is fine.",
}


def mentor(digest: dict, current_scores: dict) -> dict:
    n = digest.get("count", 0)
    if n < 3:
        return {"based_on": n, "pattern": None, "improving": None,
                "habit": HABITS[min(current_scores, key=current_scores.get)],
                "note": f"After {3 - n} more take{'s' if 3 - n != 1 else ''} I can spot your patterns."}
    w, s = digest["weakest"], digest["strongest"]
    pattern = f"Across your last {n} takes, {LABEL[w]} is your weakest area (average {digest['avgs'][w]})"
    if digest["recurring_missing"]:
        lab, k = digest["recurring_missing"][0]
        pattern += f", and you left out “{lab}” in {k} of them"
    pattern += "."
    if digest["avg_filler_rate"] >= 3:
        pattern += f" Fillers average {digest['avg_filler_rate']} per 100 words."
    ups = sorted(((d, v) for d, v in (digest.get("trend") or {}).items() if v >= 3), key=lambda kv: -kv[1])
    improving = (f"Your {LABEL[ups[0][0]]} is up {ups[0][1]} points from your earlier takes — keep doing what "
                 f"you're doing there." if ups else
                 f"Your strongest area is {LABEL[s]} (average {digest['avgs'][s]}). Lean on it while you fix the rest.")
    return {"based_on": n, "pattern": pattern, "improving": improving, "habit": HABITS[w], "note": None}


# ----------------------------------------------------------------------------------------------- assemble
GRAMMAR_NAMES = {"tense": "past tense", "agreement": "subject–verb agreement", "verb form": "verb forms",
                 "pronouns": "pronouns", "nouns": "plurals", "word choice": "word choice", "capitals": "capital letters"}


def local_review(text: str, mode: str, spec: dict, m: dict, scores: dict, cues: dict,
                 history: list[dict] | None = None, prompt: str = "") -> dict:
    sents = split_sentences(text)
    labels = list(spec.get("framework") or [])
    bd = breakdown(sents, labels, cues)
    current = {"overall": round(sum(scores.values()) / len(scores)), **scores, "analysis": {"structure_map": bd["parts"], "metrics": m}}
    digest = history_digest((history or []) + [current])    # the mentor sees this take too
    rw = rewrite.mentor_rewrite(text, mode, spec, cues, prompt)
    off = rw["off_topic"]
    co = coach(mode, sents, scores, bd["parts"], bd["order_note"], m, labels, off)
    co["stronger_version"] = rw["script"]["full"] or None      # "say it like this", built from their own words
    co["script"] = rw["script"]["parts"]
    gram, n_gram = rw["corrected"]["grammar"], rw["corrected"]["grammar_count"]
    if n_gram:                                        # the mentor's grammar point: the most frequent pattern
        top = gram[0]
        quote = next((x["original"] for x in rw["corrected"]["items"] if any("Grammar" in c for c in x["changes"])), None)
        fix = {"quote": quote, "problem": f"{n_gram} grammar slip{'s' if n_gram > 1 else ''} — mostly "
                                          f"{GRAMMAR_NAMES.get(top['kind'], top['kind'])}.",
               "fix": top["lesson"] + (f" Like this: {top['examples'][0]}." if top["examples"] else "")}
        at = (1 if off else 0) if n_gram >= 3 else len(co["fixes"])   # 3+ slips: grammar is the first fix
        co["fixes"] = (co["fixes"][:at] + [fix] + co["fixes"][at:])[:3]
        if n_gram >= 3 and not off:
            co["do_next"] = ([fix["fix"]] + [d for d in co["do_next"] if d != fix["fix"]])[:3]
    ev = evaluator(mode, sents, text, scores, bd["parts"], m)
    if off:
        ev["doubts"] = ([{"quote": None, "why": off["message"]}] + ev["doubts"])[:3]
        ev["to_strong_yes"] = ([off["retry"]] + ev["to_strong_yes"])[:2]
    fixed = rw["corrected"]
    return {
        "evaluator": ev,
        "corrections": {"items": fixed["items"][:6], "corrected_full": fixed["full"] or None,
                        "split_from_run_on": fixed["split_from_run_on"], "grammar": fixed["grammar"],
                        "grammar_count": fixed["grammar_count"], "source": "local"},
        "breakdown": bd,
        "coach": co,
        "mentor": mentor(digest, scores),
        "off_topic": off,
        "source": "local",
    }


def _quotes(items, limit=3):
    out = []
    for x in items if isinstance(items, list) else []:
        if isinstance(x, dict) and (x.get("why") or x.get("problem")):
            out.append({k: (str(v).strip() if v is not None else None) for k, v in x.items()
                        if k in ("quote", "why", "problem", "fix")})
    return out[:limit]


def merge_llm_review(local: dict, data: dict | None, challenge: bool, mode: str) -> dict:
    """Overlay the model's review on the local one, field by field; never trust its structure blindly."""
    r = {k: (dict(v) if isinstance(v, dict) else v) for k, v in local.items()}
    if isinstance(data, dict):
        ev = data.get("evaluator") or {}
        if isinstance(ev, dict):
            if ev.get("verdict") in VERDICTS:
                r["evaluator"]["verdict"] = ev["verdict"]
                r["evaluator"]["verdict_label"] = ROLES.get(mode, ROLES["Interview"])["labels"][ev["verdict"]]
            for key in ("summary", "follow_up"):
                if isinstance(ev.get(key), str) and ev[key].strip():
                    r["evaluator"][key] = ev[key].strip()
            for key in ("landed", "doubts"):
                q = _quotes(ev.get(key))
                if q:
                    r["evaluator"][key] = q
            ty = [str(x).strip() for x in ev.get("to_strong_yes") or [] if str(x).strip()]
            if ty:
                r["evaluator"]["to_strong_yes"] = ty[:2]
        bd = data.get("breakdown")
        if isinstance(bd, list) and bd:
            parts = []
            for x in bd:
                if isinstance(x, dict) and x.get("label"):
                    st = x.get("status") if x.get("status") in ("present", "weak", "missing") else "weak"
                    parts.append({"label": str(x["label"]), "status": st,
                                  "quote": (str(x["quote"]).strip() or None) if x.get("quote") else None,
                                  "note": str(x.get("note") or "").strip() or None, "position": None})
            if parts:
                r["breakdown"] = {"parts": parts, "order_note": (str(data.get("order_note")).strip()
                                                                 if data.get("order_note") else None)}
        co = data.get("coach") or {}
        if isinstance(co, dict):
            fx = _quotes(co.get("fixes"))
            if fx:
                r["coach"]["fixes"] = fx
            dn = [str(x).strip() for x in co.get("do_next") or [] if str(x).strip()]
            if dn:
                r["coach"]["do_next"] = dn[:3]
            dr = co.get("drill")
            if isinstance(dr, dict) and dr.get("title") and isinstance(dr.get("steps"), list):
                r["coach"]["drill"] = {"title": str(dr["title"]), "steps": [str(s) for s in dr["steps"]][:4]}
            if isinstance(co.get("stronger_version"), str) and co["stronger_version"].strip():
                r["coach"]["stronger_version"] = co["stronger_version"].strip()
                r["coach"]["script"] = None            # the AI's version replaces the built-in, part-labelled one
        cr = data.get("corrections")
        if isinstance(cr, dict) and isinstance(cr.get("items"), list):
            items = []
            for x in cr["items"][:6]:
                if isinstance(x, dict) and x.get("original") and (x.get("corrected") or x.get("changes")):
                    items.append({"original": str(x["original"]).strip(),
                                  "corrected": (str(x["corrected"]).strip() or None) if x.get("corrected") else None,
                                  "changes": [str(c).strip() for c in x.get("changes") or [] if str(c).strip()][:4],
                                  "notes": [str(c).strip() for c in x.get("notes") or [] if str(c).strip()][:2]})
            if items or cr.get("corrected_full"):
                r["corrections"] = {"items": items, "source": "ai", "grammar": (local.get("corrections") or {}).get("grammar") or [],
                                    "corrected_full": (str(cr.get("corrected_full")).strip() or None)
                                    if cr.get("corrected_full") else None}
        me = data.get("mentor") or {}
        if isinstance(me, dict):
            for key in ("pattern", "improving", "habit"):
                if isinstance(me.get(key), str) and me[key].strip():
                    r["mentor"][key] = me[key].strip()
        r["source"] = "ai"
    if challenge:  # Challenge Mode never hands over an answer: show what's wrong, not the corrected words
        r["coach"]["stronger_version"] = None
        r["coach"]["script"] = None
        if r.get("off_topic"):
            r["off_topic"] = {**r["off_topic"], "starter": None}
        strip = lambda t: re.sub(r"\s*Like this: [^.]*\.", "", t) if isinstance(t, str) else t  # noqa: E731
        r["coach"]["fixes"] = [{**f, "fix": strip(f.get("fix"))} for f in r["coach"].get("fixes") or []]
        r["coach"]["do_next"] = [strip(d) for d in r["coach"].get("do_next") or []]
        cr = dict(r.get("corrections") or {})
        cr["items"] = [{**x, "corrected": None} for x in cr.get("items", [])]
        cr["corrected_full"] = None
        cr["grammar"] = [{**g, "examples": []} for g in cr.get("grammar") or []]   # the lesson, not the answer
        r["corrections"] = cr
    return r
