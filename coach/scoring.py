"""Scoring provenance and compatibility.

Every saved take records *how* it was scored, so we never compare numbers produced by different scorers as
if they meant the same thing:

- ``score_basis``     — who produced the content scores (structure, clarity, relevance, answer quality):
                         ``ai`` (an LLM), ``heuristic`` (built-in rules) or ``mixed`` (the LLM returned only some).
- ``scoring_version`` — ``<basis>-<engine version>``. Bump ``HEURISTIC_VERSION`` whenever the built-in scoring
                         rules change, and ``AI_PROMPT_VERSION`` whenever the AI scoring prompt changes.
- ``model``           — the LLM model for AI scores (different models score on different scales).
- ``rubric_version``  — a short fingerprint of the framework, rubric and target length the take was judged
                         against. Editing a lesson or mode rubric changes it.
- ``transcript_source`` — where the words came from. Whisper keeps "um/uh"; browser speech-to-text drops many
                         of them, so filler counts are only comparable within the same transcript class.

Scores are comparable only when basis, engine version, model and rubric all match. Measured metrics (word
counts, fillers, pace, pauses) are comparable when the transcript class matches.
"""
import hashlib
import json

HEURISTIC_VERSION = "h11"       # h9: everyday scenarios, hesitations · h10: off-topic cap, a.m./p.m. sentences · h11: an answer that echoes the question is the bottom line
AI_PROMPT_VERSION = "p8"        # SYSTEM_PROMPT schema/rubric for AI scoring
PRONUNCIATION_VERSION = "r1"    # read-aloud word alignment
LEGACY = "legacy"               # takes saved before provenance was recorded

CONTENT_DIMS = ("structure", "clarity", "relevance", "answer_quality")


def rubric_version(spec: dict) -> str:
    """Stable fingerprint of what a take is judged against (framework, rubric text, target length)."""
    key = {"name": spec.get("name"), "framework": list(spec.get("framework") or []),
           "rubric": spec.get("rubric"), "target": list(spec.get("target_seconds") or [])}
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:10]


def transcript_class(source: str | None) -> str:
    """'whisper' (verbatim, keeps disfluencies), 'text' (browser speech-to-text or typed), or 'unknown'."""
    s = (source or "").lower()
    if "whisper" in s:
        return "whisper"
    if not s or s == "unknown":
        return "unknown"
    return "text"


def normalize_transcript_source(source: str | None, has_audio: bool) -> str:
    if source and "whisper" in source.lower():
        return source
    return "browser speech-to-text" if has_audio else "text (typed or browser speech-to-text)"


def basis_from(dim_sources: dict) -> str:
    srcs = {dim_sources.get(d) for d in CONTENT_DIMS}
    if srcs == {"ai"}:
        return "ai"
    if srcs == {"heuristic"}:
        return "heuristic"
    return "mixed"


def scoring_version(basis: str, kind: str = "speech") -> str:
    if kind == "read_aloud":
        return f"pronunciation-{PRONUNCIATION_VERSION}"
    engine = AI_PROMPT_VERSION if basis == "ai" else HEURISTIC_VERSION if basis == "heuristic" \
        else f"{AI_PROMPT_VERSION}+{HEURISTIC_VERSION}"
    return f"{basis}-{engine}"


def provenance(*, dim_sources: dict, kind: str, provider: str, spec: dict | None, transcript_source: str,
               seconds_measured: bool, has_audio: bool) -> dict:
    """What produced each number in a result — stored with the take and shown in the UI."""
    basis = "heuristic" if kind == "read_aloud" else basis_from(dim_sources)
    model = provider.split(":", 1)[1] if basis in ("ai", "mixed") and ":" in provider else None
    metrics = {
        "word_count": "measured", "filler_rate": "measured", "hedge_count": "measured",
        "seconds": "measured" if seconds_measured else "estimated",
        "wpm": "measured" if seconds_measured else "estimated",
        "voice": "measured" if has_audio else None,
    }
    scores = {d: dim_sources.get(d, "heuristic") for d in CONTENT_DIMS}
    scores["delivery"] = "heuristic"     # a formula over measured signals (fillers, hedges, pace, pauses, pitch)
    out = {"score_basis": basis, "scoring_version": scoring_version(basis, kind), "model": model,
           "rubric_version": rubric_version(spec) if spec else None,
           "transcript_source": transcript_source, "transcript_class": transcript_class(transcript_source),
           "scores": scores, "metrics": metrics}
    out["scorer"] = describe_scorer(out)
    return out


# ----------------------------------------------------------------------------------------------- compatibility
def scoring_key(a: dict) -> tuple:
    """Takes with the same key were scored by the same scorer (rubric aside)."""
    return (a.get("scoring_version") or LEGACY, a.get("model") or "")


def describe_scorer(a: dict) -> str:
    sv = a.get("scoring_version") or LEGACY
    if sv == LEGACY or sv.endswith("-" + LEGACY):
        return "an earlier version of the app (before scoring was versioned)"
    basis = a.get("score_basis") or sv.split("-")[0]
    if basis == "ai":
        return f"the AI coach ({a.get('model') or 'unknown model'}, prompt {sv.split('-', 1)[1]})"
    if basis == "mixed":
        return f"a mix of AI ({a.get('model') or 'unknown model'}) and built-in scoring"
    if basis == "pronunciation" or sv.startswith("pronunciation"):
        return "the pronunciation checker"
    return f"the built-in scorer (version {sv.split('-', 1)[1] if '-' in sv else sv})"


def compatibility(base: dict, cur: dict) -> dict:
    """Can two takes' scores / metrics be compared? Returns flags plus plain-language reasons."""
    reasons = []
    if scoring_key(base) != scoring_key(cur):
        reasons.append(f"Take {base.get('attempt_no', '?')} was scored by {describe_scorer(base)}; "
                       f"take {cur.get('attempt_no', '?')} by {describe_scorer(cur)}.")
    rb, rc = base.get("rubric_version"), cur.get("rubric_version")
    if rb != rc and not (rb is None and rc is None):
        reasons.append("They were judged against different rubrics (the lesson or framework changed).")
    scores_ok = not reasons
    tb, tc = transcript_class(base.get("transcript_source")), transcript_class(cur.get("transcript_source"))
    metrics_ok = tb == tc and tb != "unknown"
    metric_reason = None
    if not metrics_ok:
        metric_reason = ("Filler and word counts came from different transcription sources "
                         f"({base.get('transcript_source') or 'unknown'} vs {cur.get('transcript_source') or 'unknown'}) "
                         "— Whisper keeps “um/uh” that browser speech-to-text drops.")
    return {"scores": scores_ok, "metrics": metrics_ok, "reasons": reasons, "metric_reason": metric_reason}
