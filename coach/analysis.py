"""Speech analysis: local heuristics + optional LLM, merged into one normalized result."""
import logging
import re

from . import audio as audio_mod
from . import language as lang
from . import llm, metrics
from . import pronunciation as pron
from . import evidence
from . import provider_schema
from . import review as review_mod
from . import rewrite
from . import scoring
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
    "Opening": [" hi ", " hi,", " hi!", " hello", " hey ", " hey,", " hey!", "good morning", "nice to meet", "my name", "i'm", "i am"],
    "Detail": ["i enjoy", "i like", "i love", "my weekend", "yesterday", "today", "we went", "i went", "i tried", "i spent", "because"],
    "Question": ["?", "how about you", "what about you", "do you", "have you", "what do you", "how was your"],
    "Situation": ["when i", "at my", "we were", "there was", "the situation", "back in", "last year", "context",
                  "in my last role", "in my previous role", "in my last job", "in my current role", "last quarter"],
    "Task": ["my role", "i was responsible", "i needed", "the goal", "my job", "tasked", "i owned", "objective", "my task", "my responsibility", "i had to"],
    "Action": ["i decided", "i built", "i led", "i proposed", "i worked", "i created", "i talked", "so i", "i set up",
               "i designed", "i started", "i reached out", "i mapped", "i automated", "i rebuilt", "i wrote", "i ran", "i moved", "i added", "i changed", "i pushed"],
    "Result": ["result", "as a result", "reduced", "increased", "improved", "saved", "delivered", "launched",
               "percent", "%", "outcome", "ended up", "dropped to", "went from", "cut ", "fell to", "grew to"],
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
    "Hook": ["imagine", "what if", "have you ever", "picture", "?", "last month", "last week", "last year", "it was ", "weeks ago", "months ago", "years ago", "i remember"],
    "Message": ["my point", "the lesson", "what i learned", "i believe", "the key", "the truth", "my message", "the point is", "what i learned is"],
    "Story / points": ["one day", "i remember", "years ago", "first", "second", "once", "that's when", "i realised", "i realized", "here's what changed", "i had pushed", "it turned out"],
    "Call-back close": ["so next time", "remember", "in the end", "that's why", "so, ", "finally", "today, i", "today i", "and now i", "that's the difference"],
    "Acknowledge": ["i understand", "i hear", "that makes sense", "great question", "fair point", "i appreciate",
                    "you're right", "fair concern", "that's fair", "i'm sorry", "i can see why", "understandable", "you're right that"],
    "Restate problem": ["what i'm hearing", "if i understand", "your goal", "the problem", "the concern", "you need"],
    "Recommendation": ["i recommend", "i suggest", "my recommendation", "we propose", "the best option", "we should"],
    "Risks": ["risk", "concern", "mitigate", "trade-off", "tradeoff", "downside", "caveat"],
    "Bottom line": ["short answer", "bottom line", "my recommendation", "the answer is", " yes ", " no ", "we will",
                    "we won't", "we'll", "i recommend", "my answer", " yes,", " yes —", " yes -", " no,", " no —", " no -", "i'm asking for", "i'm writing to", "we're at risk", "we are at risk"],
    "Context": ["because", "the reason", "since", "we found", "what happened", "due to", "currently", "right now", "at the moment", "until ", "which takes", "takes two", "so far"],
    "Point": ["i believe", "my view", "i think", "my point", "in my opinion", "my position", "should", "is better",
              "i'd say", "definitely", "my suggestion", "i'd push for", "i suggest", "we should", "i see it differently", "i see the", "my concern", "i'd argue"],
    "Reason": ["because", "the reason", "the main reason", "since", "that's because", "lets us", "so that", "otherwise", "my concern is that", "the data shows", "the evidence"],
    "Example": ["for example", "for instance", "last year", "last quarter", "when i", "i remember", "one time"],
    "Point again": ["that's why", "so i believe", "so my view", "which is why", "so overall", "so i think",
                    "so go", "in short", "so yes", "so no"],
    "Preview": ["three reasons", "three things", "three steps", "three points", "three ways", "three risks",
                "three key", "two reasons", "two things", "three priorities", "three areas", "i'll show you", "i'll cover", "three changes", "three lessons", "three questions", "here's what changed"],
    "First": ["first", "number one", "to start"],
    "Second": ["second", "number two", "next", "also"],
    "Third": ["third", "finally", "number three", "lastly", "last one"],
    "Opening line": ["i'm a", "i am a", "my name", "i work", "i've been", "i lead", "i build"],
    "Body": ["years", "worked", "built", "focus", "responsible", "currently", "before that"],
    "Close": ["right now", "next", "looking for", "that's why", "excited", "so next time", "try it", "remember",
              "so now", "i challenge", "ask you", "tomorrow"],
    "Bridge": ["what i can tell you", "the key point", "here's what", "what we know", "what we found",
               "the real question", "let me", "the important thing"],
    "Answer": ["the answer", "directly", "the reason is", "because", "we will", "we'll", "my answer", "the short answer", "the direct answer", "here's the answer", "you won't", "i'll send", "i will send"],
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
    "Next step": ["next step", "follow up", "schedule", "by next", "i'll send", "let's set up", "action item", "could you", "by monday", "by tuesday", "by wednesday", "by thursday", "by friday", "by end of", "i'll bring", "i'll set up", "could we try", "let's try", "i'll back", "i'm on board"],
    # v7: the 28-Day Speaking Sprint
    "Behavior": ["you said", "you did", "you merged", "you joined", "you interrupted", "you wrote", "you sent",
                 "you skipped", "you missed", "you shared", "you took", "you presented"],
    "Request": ["next time", "could you", "would you", "i'd like you", "i'd ask", "going forward", "can you",
                "please"],
    "Question": ["what's", "what is", "what are", "what would", "how do", "how does", "how would", "how did",
                 "can you tell", "tell me", "could you share", "?"],
    "Connect": ["how did you", "what got you", "what brought you", "same for me", "me too", "i also",
                "similar to", "curious", "that's close to", "i'd love to hear"],
    "Frame": ["our goal", "the goal", "by the end", "we're here to", "we are here to", "minutes to", "agenda",
              "we need to decide", "today we"],
    "Invite": ["what do you think", "what are you hearing", "i'd like to hear", "anyone", "thoughts",
               "you haven't", "weigh in", "your view", "what's your take"],
    "Anchor": ["i'm looking for", "i am looking for", "we need", "our target", "the number", "i'm asking for",
               "we'd need", "it costs", "we're asking", "percent", "%", "$", "dollars", "thousand"],
    "Trade": ["if you", "in exchange", "if we", "then we can", "then we could", "would you be open",
              "in return", "if you can"],
    "Interests": ["we both", "both want", "common goal", "what matters to", "you care about", "we all want",
                  "shared goal", "same goal"],
    "Alternative": ["instead", "what i can do", "alternatively", "how about", "could we", "i can offer",
                    "another option", "or suggest", "i'd suggest"],
    "Problem": ["the problem", "every week", "every day", "we lose", "costs us", "pain", "struggle",
                "right now", "today we", "spend about", "waste"],
    "Own it": ["my mistake", "i made a mistake", "that's on me", "i missed", "i should have", "i messed up",
               "i'm sorry", "i apologize", "my fault", "i made"],
    "Fix": ["i've already", "i have already", "we fixed", "to fix", "the fix", "corrected", "i've sent",
            "resolved", "we've rolled back", "patched"],
    "Thanks": ["thank you", "thanks", "grateful", "appreciate", "recognize", "shout out", "shout-out"],
}

QUALITY_CUES = {
    "Everyday Conversation": [r"\bhi\b|\bhello\b|nice to meet", r"\bi\b", r"\benjoy|\blike|\blove",
                              r"\bbecause\b|for example", r"\?|how about you|what about you|do you"],
    "Tell a Story": [r"\bwhen\b|\blast\b|\bone day\b", r"\bi\b", r"\bthen\b|\bdecided\b",
                     r"\bresult\b|\bended up\b|\bfinally\b", r"\blearned\b|\brealized\b|\btaught\b"],
    "Share an Opinion": [r"\bi prefer\b|\bi think\b|\bi believe\b", r"\bbecause\b", r"for example|for instance",
                         r"\bwhen\b|\bi tried\b", r"that's why|so my view|in the end"],
    "Difficult Conversations": [r"\bunderstand\b|\bappreciate\b", r"\bi need\b|\bi would\b|\bcould\b",
                                r"\bbecause\b", r"\blet's\b|\bnext time\b", r"\bplease\b|\bthank\b"],
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


def framework_map(text: str, labels: list[str], prompt: str = "") -> list[dict]:
    low = " " + (text or "").lower() + " "
    pos = rewrite.part_positions(review_mod.split_sentences(text), labels, FRAMEWORK_CUES, prompt)
    out = []
    for label in labels:
        hits = [c for c in FRAMEWORK_CUES.get(label, []) if c in low]
        status = "present" if hits or label in pos else "missing"
        if not hits and label in pos:
            hits = ["answers the question first"] if label == (labels[0] if labels else None) else ["supporting detail"]
        note = {
            "present": f"Detected ({', '.join(h.strip() for h in hits[:2])}).",
            "weak": "Only a faint signal — make this part explicit.",
            "missing": "Not detected — add one sentence for this.",
        }[status]
        out.append({"label": label, "status": status, "note": note})
    return out


def heuristic_scores(text: str, mode: str, prompt: str, m: dict, spec: dict) -> dict:
    wc = m["word_count"]
    fmap = framework_map(text, spec["framework"], prompt)
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
        strengths.append(f"Few fillers appear in this transcript ({m['filler_rate']} per 100 words). "
                         "Speech recognition may leave some out.")
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
The transcript comes from speech-to-text: ignore missing punctuation and capitalization, and do not penalize
obvious recognition errors. When a word is clearly a mishearing and the intended word is obvious from context
(e.g. "music catalyst" where "AI is a catalyst" fits), use the intended word in corrected text, stronger_version
and script — never quote the mishearing as the speaker's mistake.

Return ONLY a JSON object with exactly these keys:
{
  "scores": {"structure": int, "clarity": int, "relevance": int, "answer_quality": int},
  "structure_map": [{"label": str, "status": "present"|"weak"|"missing", "note": str}],
  "strengths": [str, ...],          // 2-3, each citing what they actually said
  "improvements": [str, ...],       // 2-4, specific and actionable, highest impact first
  "coaching_hint": str,             // one sentence, the single most useful thing to try next
  "coaching_questions": [str, ...], // 2-3 Socratic questions that make the speaker find the fix
  "next_challenge": str,            // a concrete retry instruction (constraint + focus)
  "suggested_outline": [str, ...],  // see rules below
  "language": {
    "word_upgrades": [{"original": str, "better": str, "why": str}],  // 0-3 vague/weak words → precise ones
    "grammar": [{"original": str, "corrected": str, "issue": str}],   // 0-3 real grammar errors only
    "sentence_tip": str                                               // one tip on sentence structure
  },
  "review": {                       // the same take seen from three angles — see REVIEW rules below
    "evaluator": {
      "verdict": "strong_yes"|"lean_yes"|"lean_no"|"no",
      "summary": str,               // 1-2 sentences, first person, in the evaluator's voice
      "landed": [{"quote": str, "why": str}],   // 1-3: the speaker's exact words that worked, and why
      "doubts": [{"quote": str|null, "why": str}], // 1-3: exact words that raised a doubt (null quote = something missing)
      "follow_up": str,             // the question this evaluator would most likely ask next
      "to_strong_yes": [str]        // 1-2 specific changes that would earn the top verdict
    },
    "corrections": {                // sentence-level corrections of what they actually said
      "items": [{"original": str, "corrected": str, "changes": [str], "notes": [str]}],  // 0-6, worst first
      "corrected_full": str         // their whole answer, corrected — same words and meaning, just fixed
    },
    "breakdown": [{"label": str, "status": "present"|"weak"|"missing", "quote": str|null, "note": str}],
    "order_note": str|null,         // only if the parts came out in a confusing order
    "coach": {
      "fixes": [{"quote": str|null, "problem": str, "fix": str}],  // top 3, highest impact first
      "do_next": [str],             // 2-3 imperative instructions for the very next take
      "drill": {"title": str, "steps": [str]},   // one 2-4 minute drill for the biggest weakness
      "stronger_version": str,      // see rules; "" in challenge_mode
      "script": [{"label": str, "text": str, "yours": bool}]   // stronger_version split into the framework parts
    },
    "mentor": {
      "pattern": str|null,          // the recurring pattern across recent takes (null if no history)
      "improving": str|null,        // what is genuinely improving, with evidence
      "habit": str                  // one habit to practise this week
    }
  }
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
built only from the speaker's own content — an outline, never a full rewritten answer.
language: quote the speaker's exact words in "original". Ignore speech-to-text artifacts. In challenge_mode,
set "better" and "corrected" to "" and describe the issue only, so the speaker finds the fix.

REVIEW rules — the review is the heart of the feedback:
- evaluator: you ARE the evaluator named in the request (an interviewer, the audience, a leadership team or a
  customer). Speak in first person as that person, honestly and specifically: would this move them, and why.
  Use "strong_yes" only for an answer you would genuinely remember. Quotes must be the speaker's exact words
  (short excerpts, <= 25 words). follow_up must be the natural next question that person would ask.
- corrections: correct what they actually SAID, sentence by sentence, the way a good English teacher would:
  false starts and self-repairs ("I am ready for, we are ready for lunch"), run-ons chained with "and",
  grammar, wrong or missing articles, word choice, fillers, and vague phrases. "original" is their exact
  sentence; "corrected" keeps their words and meaning (fix, don't rewrite); "changes" explains each fix in a
  few words. Skip speech-to-text artifacts (missing punctuation/capitals alone is not an error). In
  challenge_mode set "corrected" and "corrected_full" to "" and describe the problems only.
- breakdown: one entry per framework label, in framework order; "quote" is the speaker's words that did that
  part (null if missing); "note" says how well it did it.
- coach: you are their speaking coach preparing them for the real thing. fixes quote their words and say
  exactly what to change; do_next is what to do differently on the very next take.
  stronger_version: when challenge_mode is false, rewrite THEIR answer at its best — keep their facts, story
  and voice, invent nothing, about the target length. When challenge_mode is true, return "" and give no
  sample wording anywhere.
  script: the same stronger_version, one entry per framework label, in framework order, each 1-3 short
  spoken sentences. "yours": true when the line is built from what they said; false when the part was missing
  and you suggest a line for it — a suggestion grounded in their situation (e.g. a sensible next step), never a
  made-up fact, number or past event. Aim for the target length when spoken. [] in challenge_mode.
- mentor: you are their long-term mentor. Use the recent-history summary to name the pattern, what is genuinely
  improving, and one habit for this week. If there is no history, set pattern and improving to null."""


def _voice_line(v):
    if not v:
        return "Voice: not recorded (transcript only)."
    p = v["pitch"]
    return (f"Voice (measured from audio): {v.get('speaking_rate_wpm')} wpm, {v['pause_count']} pauses "
            f"({v['stalls']} stalls > {audio_mod.STALL}s, longest {v['longest_pause']}s), pitch range "
            f"{p.get('range_semitones')} semitones ({p.get('label')}), volume trails off: {v['volume']['trails_off']}.")


def _history_line(digest: dict) -> str:
    if not digest.get("count"):
        return "Recent history: none (this is one of their first takes)."
    miss = ", ".join(f"{lab} missing in {k}" for lab, k in digest["recurring_missing"]) or "none"
    trend = ", ".join(f"{d} {v:+d}" for d, v in (digest.get("trend") or {}).items()) or "not enough takes"
    return (f"Recent history ({digest['count']} takes, oldest→newest overall {digest['overall']}): averages "
            f"{digest['avgs']}; recurring gaps: {miss}; filler rate avg {digest['avg_filler_rate']}/100 words; "
            f"hedges avg {digest['avg_hedges']}; trend early→late: {trend}.")


def _llm_feedback(text, mode, prompt, challenge, m, spec, voice=None, digest=None):
    role = review_mod.ROLES.get(mode, review_mod.ROLES["Interview"])
    user = (
        f"Mode: {mode}\nEvaluator: {role['role']} — {role['question']}\n"
        f"Goal: {spec['goal']}\nRubric: {spec['rubric']}\n"
        f"Framework ({spec['name']}) labels: {spec['framework']}\nTarget length: {spec['target_seconds'][0]}-"
        f"{spec['target_seconds'][1]} seconds\nchallenge_mode: {str(challenge).lower()}\n"
        f"Prompt the speaker was answering: {prompt or '(free practice — no prompt)'}\n"
        f"Measured: {m['word_count']} words, ~{m['seconds']}s, {m['wpm']} wpm, "
        f"{m['filler_count']} fillers, {m['hedge_count']} hedges, {m.get('vague_count', 0)} vague words\n"
        f"{_voice_line(voice)}\n{_history_line(digest or {})}\n\nTranscript:\n\"\"\"{text}\"\"\""
    )
    return llm.complete_json(SYSTEM_PROMPT, user, max_tokens=4000)


def _str_list(v, limit):
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:limit]


def overall_from(scores: dict) -> int:
    return int(round(sum(scores[d] * WEIGHTS[d] for d in DIMENSIONS)))


def language_report(text: str, words: list[dict] | None, challenge: bool, daily: list[str] | None) -> dict:
    v = lang.vocabulary(text, daily)
    sn = lang.sentences(text, words)
    if challenge:  # don't hand over the replacement words
        for g in sn.get("grammar", []):
            g["suggestion"] = None
        for w in v.get("weak", []):
            w["alternatives"] = []
    return {"vocabulary": v, "sentences": sn,
            "unclear_words": pron.unclear_in_free_speech(words),
            "scores": {"vocabulary": v["score"], "sentences": sn["score"]},
            "word_upgrades": [], "sentence_tip": None}


def _merge_llm_language(rep: dict, data: dict, challenge: bool):
    ld = data.get("language") or {}
    if not isinstance(ld, dict):
        return
    ups = [u for u in ld.get("word_upgrades") or [] if isinstance(u, dict) and u.get("original")]
    rep["word_upgrades"] = [{"original": str(u["original"]), "better": "" if challenge else str(u.get("better", "")),
                             "why": str(u.get("why", ""))} for u in ups[:3]]
    seen = {g["original"] for g in rep["sentences"].get("grammar", [])}
    for g in (ld.get("grammar") or [])[:3]:
        if isinstance(g, dict) and g.get("original") and g["original"].lower() not in seen:
            rep["sentences"].setdefault("grammar", []).append(
                {"original": str(g["original"]), "suggestion": None if challenge else str(g.get("corrected") or "") or None,
                 "issue": str(g.get("issue", "")), "source": "ai"})
    if isinstance(ld.get("sentence_tip"), str) and ld["sentence_tip"].strip():
        rep["sentence_tip"] = ld["sentence_tip"].strip()


def analyze_read_aloud(text: str, target: str, words: list[dict] | None = None, voice: dict | None = None) -> dict:
    """Pronunciation drill: score how clearly a known sentence came through."""
    a = pron.assess_read_aloud(target, text, words)
    m = metrics.compute(text, voice["window_seconds"] if voice else None)
    voice_scores = audio_mod.voice_scores(voice) if voice else None
    vals = [v for v in (voice_scores or {}).values() if v is not None]
    delivery = int(round(sum(vals) / len(vals))) if vals else metrics.delivery_score(m)
    overall = int(round(0.75 * a["accuracy"] + 0.25 * delivery))
    good, fix = pron.feedback(a)
    if voice:
        vg, vf = audio_mod.feedback(voice, voice_scores)
        good, fix = good + vg[:1], fix + vf[:1]
    focus = (a["unclear"] + a["missed"] + [x["word"] for x in a["substituted"]])[:3]
    return {
        "kind": "read_aloud", "overall_score": overall, "scores": {}, "metrics": m,
        "pronunciation": a, "delivery_score": delivery, "strengths": good[:3], "improvements": fix[:4],
        "coaching_hint": ("Click each highlighted word to hear it, copy the stressed syllable, then read the sentence again."
                          if focus else "Clean read. Try the next sentence, or read this one 15% faster."),
        "coaching_questions": [], "suggested_outline": [], "structure_map": [],
        "focus": {"strength": {"text": good[0] if good else "You read the whole sentence.", "quote": None,
                               "start": None, "end": None},
                  "priority": {"text": fix[0] if fix else "Every word came through clearly.", "quote": None,
                               "start": None, "end": None, "why": None},
                  "retry": (f"Practise {', '.join(focus)} on their own, then read the sentence again."
                            if focus else "Read the next sentence in this set.")},
        "next_challenge": f"Practise these words in isolation, then in the sentence: {', '.join(focus)}." if focus
        else "Move to the next sentence in this set.",
        "voice": ({**voice, "scores": voice_scores} if voice else None), "challenge_mode": False,
        "provider": "local:pronunciation", "warning": None, "mode": "Read aloud",
        "provenance": scoring.provenance(
            dim_sources={}, kind="read_aloud", provider="local:pronunciation",
            spec={"name": "Read aloud", "framework": [], "rubric": target, "target_seconds": []},
            transcript_source=scoring.normalize_transcript_source((voice or {}).get("transcript_source"), bool(voice)),
            seconds_measured=bool(m.get("seconds_measured")), has_audio=bool(voice)),
        "framework_name": "Read aloud", "framework_coverage": None,
        "filler_count": m["filler_count"], "word_count": m["word_count"], "estimated_seconds": m["seconds"],
    }


def analyze(text: str, mode: str, prompt: str = "", challenge: bool = False,
            duration_seconds: float | None = None, spec: dict | None = None, voice: dict | None = None,
            words: list[dict] | None = None, daily_words: list[str] | None = None,
            history: list[dict] | None = None) -> dict:
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
    off_topic = rewrite.intent_check(prompt, text)
    if off_topic:                                     # sharing words with the prompt isn't answering it
        h["scores"]["relevance"] = min(h["scores"]["relevance"], 40)
    scores = dict(h["scores"])
    voice_scores = None
    if voice:
        voice_scores = audio_mod.voice_scores(voice)
        vals = [v for v in voice_scores.values() if v is not None]
        if vals:  # delivery = 40% words (fillers/hedges) + 60% voice (pace, pauses, pitch, volume)
            scores["delivery"] = int(round(0.4 * scores["delivery"] + 0.6 * sum(vals) / len(vals)))
    fb = heuristic_feedback(mode, m, scores, h["structure_map"], challenge, spec)
    structure_map = h["structure_map"]
    language = language_report(text, words, challenge, daily_words)
    provider, warning = "local:heuristic", None
    digest = review_mod.history_digest(history or [])
    llm_review = None
    provider_problems: list[str] = []
    dim_sources = {d: "heuristic" for d in scoring.CONTENT_DIMS}

    if llm.active_provider() != "local":
        try:
            raw, provider = _llm_feedback(text, mode, prompt, challenge, m, spec, voice, digest)
            data, provider_problems = provider_schema.validate_feedback(raw)   # typed check before any merge
            if provider_problems:
                log.info("Dropped %d invalid AI field(s): %s", len(provider_problems), provider_problems[:5])
            llm_review = data.get("review") if isinstance(data.get("review"), dict) else None
            for d in ("structure", "clarity", "relevance", "answer_quality"):
                v = _clamp((data.get("scores") or {}).get(d))
                if v is not None:
                    scores[d] = v
                    dim_sources[d] = "ai"
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
            _merge_llm_language(language, data, challenge)
        except Exception as e:  # network, auth, bad JSON — never break the practice loop
            log.warning("LLM analysis failed, using local fallback: %s", e)
            provider = "local:heuristic"
            for d in scoring.CONTENT_DIMS:      # discard any partial AI scores from a failed merge
                scores[d] = h["scores"][d]
            dim_sources = {d: "heuristic" for d in scoring.CONTENT_DIMS}
            warning = str(e) if isinstance(e, llm.LLMUnavailable) else \
                f"AI provider failed ({type(e).__name__}); showing local analysis."

    if voice:  # measured voice feedback always comes from the audio, not the LLM
        vgood, vfix = audio_mod.feedback(voice, voice_scores)
        fb["strengths"] = (vgood[:1] + fb["strengths"])[:4]
        fb["improvements"] = (vfix[:2] + fb["improvements"])[:5]

    lgood, lfix = lang.feedback(language["vocabulary"], language["sentences"], challenge)
    fb["strengths"] = (fb["strengths"] + lgood[:1])[:4]
    fb["improvements"] = (fb["improvements"] + lfix[:2])[:6]

    if challenge:
        fb["suggested_outline"] = []  # hard guarantee: Challenge Mode never hands over an answer

    rv = review_mod.local_review(text, mode, spec, m, scores, FRAMEWORK_CUES, history, prompt)
    rv = review_mod.merge_llm_review(rv, llm_review, challenge, mode)
    rv = evidence.validate_review(rv, text, words, challenge)   # quotes must be in the transcript
    focus = evidence.focus(rv, fb, tuple(spec["target_seconds"]))

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
        "language": language,
        "review": rv,
        "focus": focus,
        "provider_validation": {"dropped": len(provider_problems), "problems": provider_problems[:10]},
        "provenance": scoring.provenance(
            dim_sources=dim_sources, kind="speech", provider=provider, spec=spec,
            transcript_source=scoring.normalize_transcript_source((voice or {}).get("transcript_source"), bool(voice)),
            seconds_measured=bool(m.get("seconds_measured")), has_audio=bool(voice)),
        "provider": provider,
        "warning": warning,
        # legacy keys kept for older history entries / clients
        "structure_score": scores["structure"],
        "clarity_score": scores["clarity"],
        "filler_count": m["filler_count"],
        "word_count": m["word_count"],
        "estimated_seconds": m["seconds"],
    }
