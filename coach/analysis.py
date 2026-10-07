"""Speech analysis: local heuristics + optional LLM, merged into one normalized result."""
import logging
import re

from . import audio as audio_mod
from . import llm, metrics
from .prompts import MODES, normalize_mode

log = logging.getLogger("thought_to_speak")

DIMENSIONS = ["structure", "clarity", "relevance", "answer_quality", "delivery"]
DIMENSION_LABELS = {
    "structure": "Structure",
    "clarity": "Clarity",
    "relevance": "Relevance",
    "answer_quality": "Answer quality",
    "delivery": "Delivery (voice & fillers)",
}
WEIGHTS = {"structure": 0.25, "clarity": 0.20, "relevance": 0.20, "answer_quality": 0.20, "delivery": 0.15}

STOPWORDS = set("""a an the and or but if then so to of in on at for with from by as is are was were be been being
do does did done have has had i you he she it we they me my your our their this that these those what which who whom
how why when where tell about time your yours explain describe give walk through would could should can will just
one some any all into out up down over under again more most very not no yes there here than too also it's i'm
you're let's us them his her its""".split())

# Cue words used to detect whether each framework element shows up in the transcript.
FRAMEWORK_CUES = {
    "Situation": ["when i", "at my", "we were", "there was", "the situation", "back in", "last year", "context"],
    "Task": ["my role", "i was responsible", "i needed", "the goal", "my job", "tasked", "i owned", "objective"],
    "Action": ["i decided", "i built", "i led", "i proposed", "i worked", "i created", "i talked", "so i", "i set up",
               "i designed", "i started", "i reached out"],
    "Result": ["result", "as a result", "reduced", "increased", "improved", "saved", "delivered", "launched",
               "percent", "%", "outcome", "ended up"],
    "Lesson": ["learned", "lesson", "taught me", "next time", "takeaway", "going forward"],
    "Decision / Ask": ["we will", "we are going", "i'm asking", "i am asking", "my ask", "decided", "decision",
                       "i recommend", "we need"],
    "Why": ["because", "the reason", "so that", "this matters", "why"],
    "Trade-offs": ["trade-off", "tradeoff", "trade off", "cost", "downside", "risk", "instead of", "means we won't",
                   "at the expense"],
    "Next steps": ["next step", "next week", "by friday", "will own", "follow up", "going forward", "plan is",
                   "timeline"],
    "Definition": [" is a ", " is the ", " are ", "refers to", "means", "in simple terms", "simply put"],
    "Example / analogy": ["for example", "for instance", "imagine", "think of", "like a", "such as", "analogy"],
    "How it works": ["first", "then", "step", "works by", "under the hood", "when a", "each"],
    "Hook": ["imagine", "what if", "have you ever", "picture", "?"],
    "Message": ["my point", "the lesson", "what i learned", "i believe", "the key", "the truth"],
    "Story / points": ["one day", "i remember", "years ago", "first", "second", "once"],
    "Call-back close": ["so next time", "remember", "in the end", "that's why", "so, ", "finally"],
    "Acknowledge": ["i understand", "i hear", "that makes sense", "great question", "fair point", "i appreciate",
                    "you're right"],
    "Restate problem": ["what i'm hearing", "if i understand", "your goal", "the problem", "the concern", "you need"],
    "Recommendation": ["i recommend", "i suggest", "my recommendation", "we propose", "the best option", "we should"],
    "Risks": ["risk", "concern", "mitigate", "trade-off", "tradeoff", "downside", "caveat"],
    "Bottom line": ["short answer", "bottom line", "my recommendation", "the answer is", " yes ", " no ", "we will",
                    "we won't", "we'll", "i recommend", "my answer"],
    "Context": ["because", "the reason", "since", "we found", "what happened", "due to"],
    "Point": ["i believe", "my view", "i think", "my point", "in my opinion", "my position", "should", "is better",
              "i'd say", "definitely"],
    "Reason": ["because", "the reason", "the main reason", "since", "that's because"],
    "Example": ["for example", "for instance", "last year", "last quarter", "when i", "i remember", "one time"],
    "Point again": ["that's why", "so i believe", "so my view", "which is why", "so overall", "so i think",
                    "so go", "in short", "so yes", "so no"],
    "Preview": ["three reasons", "three things", "three steps", "three points", "three ways", "three risks",
                "three key", "two reasons", "two things", "three priorities", "three areas"],
    "First": ["first", "number one", "to start"],
    "Second": ["second", "number two", "next", "also"],
    "Third": ["third", "finally", "number three", "lastly", "last one"],
    "Opening line": ["i'm a", "i am a", "my name", "i work", "i've been", "i lead", "i build"],
    "Body": ["years", "worked", "built", "focus", "responsible", "currently", "before that"],
    "Close": ["right now", "next", "looking for", "that's why", "excited", "so next time", "try it", "remember",
              "so now", "i challenge", "ask you", "tomorrow"],
    "Bridge": ["what i can tell you", "the key point", "here's what", "what we know", "what we found",
               "the real question", "let me", "the important thing"],
    "Answer": ["the answer", "directly", "the reason is", "because", "we will", "we'll", "my answer", "the short answer"],
    "What": ["what happened", "here's what", "we had", "there was", "we shipped", "the situation", "today",
             "last week", "yesterday", "we are", "we're"],
    "So what": ["this matters", "that matters", "matters because", "the impact", "which means", "means that",
                "so what", "the risk", "the cost"],
    "Now what": ["next", "now we", "we'll", "we will", "i recommend", "going forward", "plan is", "next step",
                 "i propose"],
    "Big idea": ["in simple terms", "simply put", "is a way", "basically is", "means", "refers to", " is a ",
                 " is like "],
    "Concrete example": ["think of", "imagine", "for example", "like a", "for instance", "picture"],
    "Why it matters": ["matters", "trade-off", "tradeoff", "trade off", "the benefit", "for you", "this means",
                       "downside", "the cost", "at the cost"],
    "Claim": ["proud", "i believe", "the main", "biggest", "went well", "strength", "the key", "two things",
              "three things"],
    "Specifics": ["percent", "%", " from ", " to ", "weeks", "months", "hours", "users", "customers", "million",
                  "thousand"],
    "Impact": ["cut", "reduced", "saved", "doubled", "so that", "which meant", "result", "unblocked", "faster",
               "increased", "improved"],
    "Next step": ["next step", "follow up", "schedule", "by next", "i'll send", "let's set up", "action item"],
}

QUALITY_CUES = {
    "Interview": [r"\bi\b", r"\bresult", r"\blearn", r"\bimpact", r"\bdecid"],
    "Leadership": [r"\bteam\b", r"\bbecause\b", r"\bpriorit", r"\bnext\b", r"\bown"],
    "Technical Explanation": [r"for example|for instance", r"\bbecause\b", r"trade-?off|downside|instead",
                              r"\bstep|first|then\b", r"\blike a\b|imagine|think of"],
    "Toastmasters": [r"\?", r"\bi remember|one day|years ago", r"\bremember\b|that's why|the lesson",
                     r"\byou\b", r"\bimagine|picture"],
    "FDE / Customer": [r"\bunderstand|hear you|makes sense", r"\brecommend|suggest|propose",
                       r"\brisk|mitigat|caveat", r"\bnext step|follow up|schedule", r"\bcost|latency|impact|roi"],
}


def _content_words(text: str) -> set[str]:
    return {t for t in metrics.tokens(text) if t not in STOPWORDS and len(t) > 2}


def _clamp(v, lo=0, hi=100):
    try:
        return int(max(lo, min(hi, round(float(v)))))
    except (TypeError, ValueError):
        return None


def mode_spec(mode: str) -> dict:
    r = MODES[mode]
    return {"name": mode, "framework": list(r["framework"]), "rubric": r["rubric"], "goal": r["goal"],
            "target_seconds": tuple(r["target_seconds"])}


def framework_map(text: str, labels: list[str]) -> list[dict]:
    low = " " + (text or "").lower() + " "
    out = []
    for label in labels:
        hits = [c for c in FRAMEWORK_CUES.get(label, []) if c in low]
        status = "present" if hits else "missing"
        note = {
            "present": f"Detected ({', '.join(h.strip() for h in hits[:2])}).",
            "weak": "Only a faint signal — make this part explicit.",
            "missing": "Not detected — add one sentence for this.",
        }[status]
        out.append({"label": label, "status": status, "note": note})
    return out


def heuristic_scores(text: str, mode: str, prompt: str, m: dict, spec: dict) -> dict:
    wc = m["word_count"]
    fmap = framework_map(text, spec["framework"])
    coverage = sum({"present": 1, "weak": 0.5, "missing": 0}[f["status"]] for f in fmap) / len(fmap)

    structure = 45 + coverage * 35 + min(4, len(m["transitions"])) * 4
    if wc < 40:
        structure -= 10

    # Clarity: sentence length (only meaningful with punctuation), repetition, word length
    clarity = 72.0
    if m["punctuated"] and m["sentence_count"]:
        avg = wc / m["sentence_count"]
        if avg > 28:
            clarity -= min(18, (avg - 28) * 1.2)
        elif avg < 7:
            clarity -= 8
    else:
        clarity -= 4  # run-on transcript; we cannot judge sentence boundaries
    clarity -= min(10, m["repetitions"] * 2.5)
    clarity -= min(10, m["filler_rate"] * 1.0)
    if wc < 30:
        clarity -= 10

    # Relevance: content-word overlap with the prompt
    if prompt.strip():
        pw = _content_words(prompt)
        shared = pw & _content_words(text)
        ratio = len(shared) / max(1, len(pw))
        relevance = 45 + 45 * min(1.0, ratio * 1.6)
    else:
        relevance = 70
    if wc < 40:  # too short to fully answer anything
        relevance = min(relevance, 65)

    # Answer quality: mode-specific substance markers + evidence + length fit
    cues = QUALITY_CUES.get(mode, [])
    low = (text or "").lower()
    cue_hits = sum(1 for c in cues if re.search(c, low))
    quality = 42 + cue_hits * 7 + (8 if m["has_numbers"] else 0)
    quality -= min(10, max(0, m.get("vague_count", 0) - 2) * 2.5)
    lo, hi = spec["target_seconds"]
    secs = m["seconds"]
    if secs < lo * 0.5:
        quality -= 12
    elif secs > hi * 1.5:
        quality -= 8

    scores = {
        "structure": _clamp(structure, 20, 95),
        "clarity": _clamp(clarity, 20, 95),
        "relevance": _clamp(relevance, 20, 95),
        "answer_quality": _clamp(quality, 20, 95),
        "delivery": metrics.delivery_score(m),
    }
    return {"scores": scores, "structure_map": fmap}


def heuristic_feedback(mode: str, m: dict, scores: dict, fmap: list[dict], challenge: bool, spec: dict) -> dict:
    strengths, improvements = [], []
    present = [f["label"] for f in fmap if f["status"] == "present"]
    missing = [f["label"] for f in fmap if f["status"] == "missing"]
    if present:
        strengths.append(f"Your answer already covers: {', '.join(present)}.")
    if m["filler_rate"] < 2 and m["word_count"] >= 30:
        strengths.append(f"Low filler rate ({m['filler_rate']} per 100 words) — you sound composed.")
    if m["transitions"]:
        strengths.append(f"You used signposting words ({', '.join(m['transitions'][:3])}) that help listeners follow.")
    if m["has_numbers"]:
        strengths.append("You included concrete numbers or quantities — that makes claims credible.")
    if not strengths:
        strengths.append("You spoke in your own words instead of reciting a script — that is the raw material to build on.")

    if missing:
        improvements.append(f"Add the missing part(s) of the {spec['name']} shape: {', '.join(missing)}.")
    if m["filler_count"]:
        top = ", ".join(f"'{k}' ×{v}" for k, v in list(m["fillers"].items())[:3])
        if m["filler_rate"] >= 2:
            improvements.append(f"Replace fillers with a short pause: {top}.")
    if m["hedge_count"] >= 2:
        improvements.append(f"You hedged {m['hedge_count']} times ('I think', 'maybe'…). State your point with conviction.")
    if m.get("vague_count", 0) >= 3:
        words = ", ".join(f"'{k}'" for k in list(m.get("vague_words", {}))[:4])
        improvements.append(f"Swap vague words ({words}) for names, numbers or examples.")
    if not m["punctuated"] and m["word_count"] > 50:
        improvements.append("The transcript reads as one long run-on. Speak in shorter sentences and pause between ideas.")
    if scores["relevance"] < 60:
        improvements.append("Tie your answer back to the exact question — name it in your first sentence.")
    if not m["has_numbers"] and mode in ("Interview", "FDE / Customer", "Leadership"):
        improvements.append("Quantify at least one outcome (%, time saved, $, users).")
    if not improvements:
        improvements.append("Tighten the opening: say your main point in the first 10 seconds.")

    weakest = min(scores, key=scores.get)
    hints = {
        "structure": f"Before you speak, name the {len(fmap)} parts you'll hit: {' → '.join(f['label'] for f in fmap)}.",
        "clarity": "One idea per sentence. If a sentence needs 'and… and… and', split it.",
        "relevance": "Repeat the key words of the question in your first sentence, then answer it directly.",
        "answer_quality": "Add one specific example and one measurable result.",
        "delivery": "When you feel a filler coming, close your mouth and pause for one beat instead.",
    }
    questions = {
        "structure": ["What is the one sentence you want the listener to remember?",
                      f"Which part of {' → '.join(f['label'] for f in fmap)} did you skip?"],
        "clarity": ["Could a listener repeat your main point after hearing it once?",
                    "Which sentence could you cut without losing meaning?"],
        "relevance": ["What exactly was the question asking for?",
                      "Where in your answer did you answer it directly?"],
        "answer_quality": ["What changed because of what you did — can you measure it?",
                           "What is one concrete example that proves your point?"],
        "delivery": ["Where did you slow down to think — could a silent pause replace the filler there?",
                     "Which phrase did you repeat most?"],
    }
    lo, hi = spec["target_seconds"]
    return {
        "strengths": strengths[:3],
        "improvements": improvements[:4],
        "coaching_hint": hints[weakest],
        "coaching_questions": questions[weakest],
        "next_challenge": f"Retry the same prompt in {lo}–{hi} seconds and focus only on "
                          f"{DIMENSION_LABELS[weakest].lower()}.",
        "suggested_outline": [] if challenge else
        [f"{f['label']}: <your own one-line point>" for f in fmap],
    }


SYSTEM_PROMPT = """You are Thought → Speak, a communication coach for spontaneous spoken answers.
Your job is to make the speaker independent of AI, so you diagnose and coach; you do not perform for them.
The transcript comes from browser speech-to-text: ignore missing punctuation and capitalization,
and do not penalize obvious recognition errors.

Return ONLY a JSON object with exactly these keys:
{
  "scores": {"structure": int, "clarity": int, "relevance": int, "answer_quality": int},
  "structure_map": [{"label": str, "status": "present"|"weak"|"missing", "note": str}],
  "strengths": [str, ...],          // 2-3, each citing what they actually said
  "improvements": [str, ...],       // 2-4, specific and actionable, highest impact first
  "coaching_hint": str,             // one sentence, the single most useful thing to try next
  "coaching_questions": [str, ...], // 2-3 Socratic questions that make the speaker find the fix
  "next_challenge": str,            // a concrete retry instruction (constraint + focus)
  "suggested_outline": [str, ...]   // see rules below
}

Scoring (0-100, integers; 90+ exceptional, 75 strong, 60 adequate, 45 weak, <35 off-track):
- structure: does it follow the mode framework, with the main point early and a clear ending?
- clarity: could a listener repeat the main point after one hearing? concise, concrete, no rambling.
- relevance: does it answer the actual prompt, directly, without drifting?
- answer_quality: substance for this mode (evidence, ownership, specifics, trade-offs, results).
Delivery/filler metrics are computed separately; do not score them.

structure_map: one entry per framework label given, in order.
Never invent facts the speaker did not say.
If challenge_mode is true: suggested_outline MUST be [] and you must NOT write any replacement
sentences, sample phrasing, or rewritten answer anywhere. Coach only with observations, hints, and questions.
If challenge_mode is false: suggested_outline may contain 3-5 short skeleton bullets ("Label: idea")
built only from the speaker's own content — an outline, never a full rewritten answer."""


def _voice_line(v):
    if not v:
        return "Voice: not recorded (transcript only)."
    p = v["pitch"]
    return (f"Voice (measured from audio): {v.get('speaking_rate_wpm')} wpm, {v['pause_count']} pauses "
            f"({v['stalls']} stalls > {audio_mod.STALL}s, longest {v['longest_pause']}s), pitch range "
            f"{p.get('range_semitones')} semitones ({p.get('label')}), volume trails off: {v['volume']['trails_off']}.")


def _llm_feedback(text, mode, prompt, challenge, m, spec, voice=None):
    user = (
        f"Mode: {mode}\nGoal: {spec['goal']}\nRubric: {spec['rubric']}\n"
        f"Framework ({spec['name']}) labels: {spec['framework']}\nTarget length: {spec['target_seconds'][0]}-"
        f"{spec['target_seconds'][1]} seconds\nchallenge_mode: {str(challenge).lower()}\n"
        f"Prompt the speaker was answering: {prompt or '(free practice — no prompt)'}\n"
        f"Measured: {m['word_count']} words, ~{m['seconds']}s, {m['wpm']} wpm, "
        f"{m['filler_count']} fillers, {m['hedge_count']} hedges, {m.get('vague_count', 0)} vague words\n"
        f"{_voice_line(voice)}\n\nTranscript:\n\"\"\"{text}\"\"\""
    )
    return llm.complete_json(SYSTEM_PROMPT, user)


def _str_list(v, limit):
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:limit]


def overall_from(scores: dict) -> int:
    return int(round(sum(scores[d] * WEIGHTS[d] for d in DIMENSIONS)))


def analyze(text: str, mode: str, prompt: str = "", challenge: bool = False,
            duration_seconds: float | None = None, spec: dict | None = None, voice: dict | None = None) -> dict:
    """spec overrides the mode's framework/rubric/target (used by lessons).
    voice = acoustic report from coach.audio.analyze (real recording) — drives delivery scoring."""
    mode = normalize_mode(mode)
    spec = {**mode_spec(mode), **(spec or {})}
    if voice:
        duration_seconds = voice["window_seconds"]
    m = metrics.compute(text, duration_seconds)
    if voice and voice.get("speaking_rate_wpm"):
        m["wpm"] = voice["speaking_rate_wpm"]
    h = heuristic_scores(text, mode, prompt, m, spec)
    scores = dict(h["scores"])
    voice_scores = None
    if voice:
        voice_scores = audio_mod.voice_scores(voice)
        vals = [v for v in voice_scores.values() if v is not None]
        if vals:  # delivery = 40% words (fillers/hedges) + 60% voice (pace, pauses, pitch, volume)
            scores["delivery"] = int(round(0.4 * scores["delivery"] + 0.6 * sum(vals) / len(vals)))
    fb = heuristic_feedback(mode, m, scores, h["structure_map"], challenge, spec)
    structure_map = h["structure_map"]
    provider, warning = "local:heuristic", None

    if llm.active_provider() != "local":
        try:
            data, provider = _llm_feedback(text, mode, prompt, challenge, m, spec, voice)
            for d in ("structure", "clarity", "relevance", "answer_quality"):
                v = _clamp((data.get("scores") or {}).get(d))
                if v is not None:
                    scores[d] = v
            smap = data.get("structure_map")
            if isinstance(smap, list) and smap:
                structure_map = [
                    {"label": str(x.get("label", "")), "status": x.get("status") if x.get("status") in
                     ("present", "weak", "missing") else "weak", "note": str(x.get("note", ""))}
                    for x in smap if isinstance(x, dict)
                ] or structure_map
            for key, limit in (("strengths", 3), ("improvements", 4), ("coaching_questions", 3),
                               ("suggested_outline", 5)):
                vals = _str_list(data.get(key), limit)
                if vals or key == "suggested_outline":
                    fb[key] = vals
            for key in ("coaching_hint", "next_challenge"):
                if isinstance(data.get(key), str) and data[key].strip():
                    fb[key] = data[key].strip()
        except Exception as e:  # network, auth, bad JSON — never break the practice loop
            log.warning("LLM analysis failed, using local fallback: %s", e)
            provider = "local:heuristic"
            warning = f"AI provider failed ({type(e).__name__}); showing local analysis."

    if voice:  # measured voice feedback always comes from the audio, not the LLM
        vgood, vfix = audio_mod.feedback(voice, voice_scores)
        fb["strengths"] = (vgood[:1] + fb["strengths"])[:4]
        fb["improvements"] = (vfix[:2] + fb["improvements"])[:5]

    if challenge:
        fb["suggested_outline"] = []  # hard guarantee: Challenge Mode never hands over an answer

    overall = overall_from(scores)
    return {
        "overall_score": overall,
        "scores": scores,
        "metrics": m,
        "structure_map": structure_map,
        **fb,
        "mode": mode,
        "framework_name": spec["name"],
        "framework_coverage": round(sum(1 for x in structure_map if x["status"] == "present")
                                    / max(1, len(structure_map)), 2),
        "challenge_mode": challenge,
        "voice": ({**voice, "scores": voice_scores} if voice else None),
        "provider": provider,
        "warning": warning,
        # legacy keys kept for older history entries / clients
        "structure_score": scores["structure"],
        "clarity_score": scores["clarity"],
        "filler_count": m["filler_count"],
        "word_count": m["word_count"],
        "estimated_seconds": m["seconds"],
    }
