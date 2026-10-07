"""Deterministic speech metrics. These are always computed locally and override any
LLM-reported counts, because LLMs are unreliable at counting."""
import re
from collections import Counter

WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Multi-word fillers / hedges are matched on the lowercase token stream.
MULTI_FILLERS = [("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of")]
SINGLE_FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "basically", "actually",
                  "literally", "honestly", "obviously"}
# 'like' is only a filler when it is not a verb/comparison.
LIKE_NOT_FILLER_PREV = {"would", "i'd", "you'd", "we'd", "i", "you", "we", "they", "feel", "feels", "felt",
                        "look", "looks", "looked", "seem", "seems", "sound", "sounds", "something",
                        "things", "more", "much", "just", "don't", "didn't", "not", "really", "also",
                        "people", "to", "and", "what", "anything", "nothing"}
HEDGES = [("i", "think"), ("i", "guess"), ("i", "feel", "like"), ("maybe",), ("probably",),
          ("hopefully",), ("i", "believe"), ("not", "sure"), ("try", "to")]
# Fillers and hedges are counted separately; this list is only for imprecise content words.
VAGUE_SINGLE = {"stuff", "things", "thing", "really", "very", "nice", "issues", "whatever", "somehow", "etc"}
VAGUE_MULTI = [("a", "lot"), ("lots", "of"), ("and", "so", "on"), ("or", "something")]
TRANSITIONS = ["first", "second", "third", "next", "then", "finally", "because", "so that", "as a result",
               "for example", "for instance", "however", "but", "therefore", "in summary", "to summarize",
               "the result", "which meant", "that led", "in the end", "overall", "the key", "the reason"]


def tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(text or "")]


def _count_seq(toks: list[str], seq: tuple) -> int:
    n = len(seq)
    return sum(1 for i in range(len(toks) - n + 1) if tuple(toks[i:i + n]) == seq)


def filler_breakdown(text: str) -> dict:
    toks = tokens(text)
    c = Counter()
    for i, t in enumerate(toks):
        if t in SINGLE_FILLERS:
            c[t] += 1
        elif t == "like":
            prev = toks[i - 1] if i else ""
            if prev not in LIKE_NOT_FILLER_PREV:
                c["like"] += 1
        elif t == "so" and (i == 0 or toks[i - 1] in {"and", "but", "um", "uh"}):
            c["so (opener)"] += 1
    for seq in MULTI_FILLERS:
        k = _count_seq(toks, seq)
        if k:
            c[" ".join(seq)] += k
    return dict(c.most_common())


def hedge_count(text: str) -> int:
    toks = tokens(text)
    return sum(_count_seq(toks, h) for h in HEDGES)


def repetitions(text: str) -> int:
    toks = tokens(text)
    return sum(1 for a, b in zip(toks, toks[1:]) if a == b and a not in {"very", "no", "bye"})


def vague_breakdown(text: str) -> dict:
    toks = tokens(text)
    c = Counter(t for t in toks if t in VAGUE_SINGLE)
    for seq in VAGUE_MULTI:
        k = _count_seq(toks, seq)
        if k:
            c[" ".join(seq)] += k
    return dict(c.most_common())


def sentences(text: str) -> list[str]:
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]
    return parts


def compute(text: str, duration_seconds: float | None = None) -> dict:
    toks = tokens(text)
    wc = len(toks)
    fillers = filler_breakdown(text)
    filler_total = sum(fillers.values())
    sents = sentences(text)
    punctuated = len(sents) > 1 or bool(re.search(r"[.!?]\s*$", text or ""))
    est_seconds = round(wc / 2.3) if wc else 0  # ~140 wpm conversational
    seconds = duration_seconds if duration_seconds and duration_seconds > 3 else est_seconds
    wpm = round(wc / (seconds / 60)) if seconds else 0
    low = (text or "").lower()
    transitions = sorted({t for t in TRANSITIONS if re.search(r"\b" + re.escape(t) + r"\b", low)})
    return {
        "word_count": wc,
        "filler_count": filler_total,
        "fillers": fillers,
        "filler_rate": round(100 * filler_total / wc, 1) if wc else 0.0,  # per 100 words
        "hedge_count": hedge_count(text),
        "vague_words": (vague := vague_breakdown(text)),
        "vague_count": sum(vague.values()),
        "repetitions": repetitions(text),
        "sentence_count": len(sents) if punctuated else max(1, round(wc / 18)) if wc else 0,
        "punctuated": punctuated,
        "transitions": transitions,
        "seconds": round(seconds) if seconds else 0,
        "seconds_measured": bool(duration_seconds and duration_seconds > 3),
        "wpm": wpm,
        "has_numbers": bool(re.search(r"\d|percent|\bhalf\b|\bdouble|\btwice\b|\bx\b", low)),
    }


def delivery_score(m: dict) -> int:
    """Score delivery from filler rate, hedging, pace, repetition. 0-100."""
    if not m["word_count"]:
        return 0
    score = 92.0
    score -= min(30, m["filler_rate"] * 3.0)                 # 5 fillers/100 words -> -15
    score -= min(12, m["hedge_count"] * 3)
    score -= min(10, m["repetitions"] * 2)
    wpm = m["wpm"]
    if m["seconds_measured"] and wpm:
        if wpm < 110:
            score -= min(12, (110 - wpm) * 0.4)
        elif wpm > 175:
            score -= min(12, (wpm - 175) * 0.4)
    return int(max(20, min(98, round(score))))
