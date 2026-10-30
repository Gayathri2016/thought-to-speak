"""Evidence for feedback: quotes that really are in the transcript, with audio timestamps only when measured.

Rules
- A quote is kept only if it can be found in the transcript (exactly, as a prefix ending in "…", or as a very
  close match of the same length). It is then replaced by the speaker's verbatim words, so the UI never shows
  words they didn't say. Unverifiable quotes are removed and counted in ``evidence.dropped``.
- Timestamps come only from Whisper word timings of the same transcript. If the quote's words can't be located
  exactly in those timings, no timestamp is given — never estimated or interpolated.
- In Challenge Mode, any quoted wording that is *not* the speaker's own (i.e. suggested replacement wording) is
  redacted from coaching text, so the app never hands over an answer.
"""
import re
from difflib import SequenceMatcher

_TOK = re.compile(r"[a-z0-9']+")
REDACTED = "[your own wording]"


def _toks(s: str) -> list[str]:
    return _TOK.findall((s or "").lower().replace("’", "'"))


def locate(quote: str, transcript: str) -> tuple[int, int] | None:
    """Token span [start, end) of `quote` within `transcript`, or None."""
    q = (quote or "").strip()
    if not q:
        return None
    truncated = q.endswith("…") or q.endswith("...")
    qt, tt = _toks(q), _toks(transcript)
    if not qt or len(qt) > len(tt):
        return None
    n = len(qt)
    for i in range(len(tt) - n + 1):                  # exact (or prefix of a longer sentence when truncated)
        if tt[i:i + n] == qt:
            return (i, i + n)
    if n >= 4 and not truncated:                      # close match of the same length (typos, "forty" vs "40")
        qs, best, at = " ".join(qt), 0.0, None
        for i in range(len(tt) - n + 1):
            r = SequenceMatcher(None, qs, " ".join(tt[i:i + n])).ratio()
            if r > best:
                best, at = r, i
        if best >= 0.88 and _safe_variant(qt, tt[at:at + n]):
            return (at, at + n)
    return None


_PRONOUNS = {"i", "we", "my", "our", "me", "us", "you", "your", "they", "their", "he", "she", "it"}
_NEG_PREFIX = ("dis", "un", "in", "im", "non", "mis", "ir", "il")
_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
        "nine": "9", "ten": "10", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "hundred": "100"}


def _safe_variant(q: list[str], t: list[str]) -> bool:
    """A near-match may differ by typos or number spelling — never by meaning (negation) or by who did it."""
    for a, b in zip(q, t):
        if a == b or _NUM.get(a, a) == _NUM.get(b, b):
            continue
        if a in _PRONOUNS or b in _PRONOUNS:
            return False                              # "I" vs "we" changes ownership
        long_, short = (a, b) if len(a) > len(b) else (b, a)
        if long_.endswith(short) and long_[:-len(short)] in _NEG_PREFIX:
            return False                              # "agreed" vs "disagreed" flips the meaning
        if a in ("not", "never", "no") or b in ("not", "never", "no"):
            return False
        if SequenceMatcher(None, a, b).ratio() < 0.75:
            return False
    return True


def verbatim(span: tuple[int, int], transcript: str, truncated: bool) -> str:
    """The speaker's own words for a token span (original casing/punctuation)."""
    words = (transcript or "").split()
    # map token index → word index (a word can hold one or more tokens, e.g. "Q3." → "q3")
    idx = []
    for wi, w in enumerate(words):
        for _ in _toks(w):
            idx.append(wi)
    if not idx:
        return ""
    a, b = idx[span[0]], idx[min(span[1], len(idx)) - 1]
    text = " ".join(words[a:b + 1]).strip(" ,;:")
    return text + ("…" if truncated and not text.endswith((".", "!", "?")) else "")


def timestamps(span: tuple[int, int], transcript: str, words: list[dict] | None) -> tuple[float, float] | None:
    """Start/end seconds of a span, only if the timed words spell out the same tokens."""
    if not words:
        return None
    timed = []                                         # (token, start, end)
    for w in words:
        for t in _toks(w.get("word", "")):
            if w.get("start") is None or w.get("end") is None:
                return None
            timed.append((t, float(w["start"]), float(w["end"])))
    target = _toks(transcript)[span[0]:span[1]]
    n = len(target)
    seq = [t for t, _, _ in timed]
    for i in range(len(seq) - n + 1):
        if seq[i:i + n] == target:
            return (round(timed[i][1], 2), round(timed[i + n - 1][2], 2))
    return None


def check(quote: str | None, transcript: str, words: list[dict] | None = None) -> dict | None:
    """Verified evidence for one quote: {"quote", "start", "end"} or None if it isn't in the transcript."""
    if not quote:
        return None
    span = locate(quote, transcript)
    if not span:
        return None
    truncated = quote.strip().endswith(("…", "..."))
    ts = timestamps(span, transcript, words)
    return {"quote": verbatim(span, transcript, truncated), "start": ts[0] if ts else None,
            "end": ts[1] if ts else None}


_SENTS = re.compile(r"(?<=[.!?])\s+")
_LIGHT = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "is", "are", "was", "it", "that", "this", "so",
          "for", "with", "as", "be"}


def nearest_sentence(quote: str | None, transcript: str, words: list[dict] | None = None) -> dict | None:
    """A paraphrased quote → the transcript sentence it plainly refers to (most of its content words, in one
    sentence). Shows the speaker's real words instead of dropping the part. None when nothing clearly matches."""
    q = {t for t in _toks(quote) if t not in _LIGHT}
    if len(q) < 3:
        return None
    best, score = None, 0.0
    for sent in _SENTS.split(transcript or ""):
        st = set(_toks(sent))
        r = len(q & st) / len(q)
        if r > score:
            best, score = sent.strip(), r
    if best and score >= 0.6:
        return check(best, transcript, words)
    return None


_QUOTED = re.compile(r"[“\"]([^”\"]{2,200})[”\"]")


def redact_suggestions(text: str, transcript: str) -> str:
    """Challenge Mode: hide quoted wording that the speaker didn't say (i.e. suggested replacements)."""
    if not text:
        return text

    def sub(m):
        inner = m.group(1).rstrip("….").strip()
        return m.group(0) if locate(inner, transcript) else "“" + REDACTED + "”"
    return _QUOTED.sub(sub, text)


def validate_review(rv: dict, transcript: str, words: list[dict] | None, challenge: bool) -> dict:
    """Verify every quote in a review against the transcript; attach timestamps; redact in Challenge Mode."""
    dropped = 0

    def fix_items(items, keep_without_quote=True):
        nonlocal dropped
        out = []
        for x in items or []:
            if not isinstance(x, dict):
                continue
            q = x.get("quote")
            if q:
                ev = check(q, transcript, words)
                if not ev:
                    dropped += 1
                    if not keep_without_quote:
                        continue
                    x = {**x, "quote": None, "start": None, "end": None}
                else:
                    x = {**x, **ev}
            out.append(x)
        return out

    ev = rv.get("evaluator") or {}
    # a "what landed" item is only praise for something they said — without a verified quote it's dropped
    ev["landed"] = fix_items(ev.get("landed"), keep_without_quote=False)
    ev["doubts"] = fix_items(ev.get("doubts"))
    co = rv.get("coach") or {}
    co["fixes"] = fix_items(co.get("fixes"))
    bd = rv.get("breakdown") or {}
    parts = []
    for p in bd.get("parts") or []:
        if p.get("quote"):
            e = check(p["quote"], transcript, words) or nearest_sentence(p["quote"], transcript, words)
            if e:
                p = {**p, **e}
            else:
                dropped += 1
                p = {**p, "quote": None, "status": "weak" if p.get("status") == "present" else p.get("status")}
        parts.append(p)
    bd["parts"] = parts
    cr = rv.get("corrections") or {}
    items = []
    for x in cr.get("items") or []:
        e = check(x.get("original"), transcript, words)
        if not e:
            dropped += 1
            continue
        items.append({**x, "original": e["quote"], "start": e["start"], "end": e["end"]})
    cr["items"] = items

    if challenge:
        red = lambda s: redact_suggestions(s, transcript) if isinstance(s, str) else s  # noqa: E731
        for f in co.get("fixes", []):
            f["fix"] = red(f.get("fix"))
        co["do_next"] = [red(x) for x in co.get("do_next") or []]
        ev["to_strong_yes"] = [red(x) for x in ev.get("to_strong_yes") or []]
        for x in cr.get("items", []):
            x["changes"] = [red(c) for c in x.get("changes") or []]
            x["corrected"] = None
        cr["corrected_full"] = None
        co["stronger_version"] = None
    rv["evidence"] = {"verified": True, "dropped": dropped, "timestamps": bool(words)}
    return rv


def focus(rv: dict, fb: dict, mode_target: tuple | None = None) -> dict:
    """The default results view: one strength, one priority improvement, one retry instruction."""
    ev, co = rv.get("evaluator") or {}, rv.get("coach") or {}
    landed = (ev.get("landed") or [None])[0]
    strength = ({"text": landed["why"], "quote": landed.get("quote"), "start": landed.get("start"),
                 "end": landed.get("end")} if landed else
                {"text": (fb.get("strengths") or ["You answered in your own words — that's the raw material."])[0],
                 "quote": None, "start": None, "end": None})
    fix = (co.get("fixes") or [None])[0]
    doubt = next((d for d in ev.get("doubts") or [] if d.get("quote")), None)
    if fix:
        priority = {"text": fix["problem"], "quote": fix.get("quote"), "start": fix.get("start"),
                    "end": fix.get("end"), "why": None}
        retry = fix["fix"]
    elif doubt:
        priority = {"text": doubt["why"], "quote": doubt["quote"], "start": doubt.get("start"),
                    "end": doubt.get("end"), "why": None}
        retry = (co.get("do_next") or [fb.get("next_challenge") or "Retry with one change."])[0]
    else:
        priority = {"text": (fb.get("improvements") or ["Tighten it: same content, fewer words."])[0],
                    "quote": None, "start": None, "end": None, "why": None}
        retry = (co.get("do_next") or [fb.get("next_challenge") or "Retry with one change."])[0]
    off = rv.get("off_topic")
    if off:                                           # not answering the question outranks every other fix
        priority = {"text": off["message"], "quote": None, "start": None, "end": None, "why": None}
        retry = off["retry"]
    if strength.get("quote") and strength["quote"] == priority.get("quote"):
        strength = {**strength, "quote": None, "start": None, "end": None}   # never praise and fault the same words
    if mode_target:
        retry = retry.rstrip(".") + f". Aim for {mode_target[0]}–{mode_target[1]} seconds."
    return {"strength": strength, "priority": priority, "retry": retry}
