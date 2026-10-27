"""Sentence-by-sentence corrections: "You said … → Say …", with what changed and why.

Works without an API key. It repairs the things spoken English typically needs fixing, keeping the speaker's
own words wherever possible:
  - fillers ("um", "you know", filler "like", an opening "so")
  - false starts / self-repairs ("I am ready for, we are ready for lunch" → "we are ready for lunch")
  - immediate repeats ("I went to the, I went to the store", "the the")
  - run-on sentences chained with "and … and …" (split into separate sentences)
  - common grammar slips (from coach.language's rules: "he don't", "there's many", "a hour" …)
  - capitals and end punctuation
Things it can't fix by itself (a vague "do something else") come back as notes. An LLM, when configured,
replaces this with its own corrections; see coach.review.merge_llm_review.
"""
import re

from . import language
from . import metrics

PRONOUNS = {"i", "we", "you", "they", "he", "she", "it", "my", "our"}
AUX = {"am", "are", "is", "was", "were", "have", "has", "had", "will", "would", "can", "could", "should",
       "did", "do", "'m", "'re", "'ve", "'ll", "think", "want", "need", "went", "got", "just"}
# A new sentence can start at "and" / "so" when one of these follows it.
SPLIT_AFTER = PRONOUNS | {"then", "once", "after", "when", "now", "later", "afterwards", "the", "this", "that"}
SINGLE_FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "basically", "literally"}
MULTI_FILLERS = [("you", "know"), ("i", "mean")]
VAGUE = {"something else": "name what you'll actually do", "stuff": "name the actual thing",
         "things": "name the actual things", "a lot of": "give a number or example",
         "and so on": "finish the list or cut it", "or something": "say exactly what you mean",
         "whatever": "say exactly what you mean", "kind of": "commit — say it plainly",
         "sort of": "commit — say it plainly"}


def _key(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))


def _remove_fillers(words: list[str]) -> tuple[list[str], list[str]]:
    out, removed, i = [], [], 0
    while i < len(words):
        k = _key(words[i])
        nxt = _key(words[i + 1]) if i + 1 < len(words) else ""
        prev = _key(out[-1]) if out else ""
        if (k, nxt) in MULTI_FILLERS and not (k == "i" and nxt == "mean" and prev in {"what", "that"}):
            removed.append(f"{k} {nxt}")
            _carry_punct(out, words[i + 1])
            i += 2
            continue
        if k in SINGLE_FILLERS or (k == "like" and prev not in metrics.LIKE_NOT_FILLER_PREV and i > 0
                                   and nxt not in {"a", "an", "the", "this", "that", "to"}) \
                or (k == "so" and not out and nxt):
            removed.append(k)
            _carry_punct(out, words[i])
            i += 1
            continue
        out.append(words[i])
        i += 1
    return out, removed


def _carry_punct(out: list[str], dropped: str):
    """Keep sentence punctuation that was attached to a dropped word ("…um." keeps the full stop)."""
    tail = re.search(r"[.!?]+$", dropped)
    if tail and out:
        out[-1] = out[-1].rstrip(",;:") + tail.group(0)


def _remove_restarts(words: list[str]) -> tuple[list[str], list[str]]:
    removed = []
    changed = True
    while changed:
        changed = False
        keys = [_key(w) for w in words]
        n = len(keys)
        # 1) immediate repeats: "the the", "I went to the I went to the store"
        for k in range(5, 0, -1):
            for i in range(0, n - 2 * k + 1):
                if keys[i:i + k] == keys[i + k:i + 2 * k] and all(keys[i:i + k]) and \
                        (k > 1 or keys[i] not in {"very", "no", "bye", "that", "had", "really", "so"}):
                    removed.append(("repeat", " ".join(w.strip(",") for w in words[i:i + k])))
                    words = words[:i] + words[i + k:]
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue
        # 2) restart with a new subject: "I am ready for, we are ready for lunch" → "we are ready for lunch"
        for a in range(n):
            if keys[a] not in PRONOUNS or a + 1 >= n or keys[a + 1] not in AUX:
                continue
            for b in range(a + 3, min(n - 2, a + 7)):
                if keys[b] in PRONOUNS and keys[b + 1] in AUX:
                    k = b - a - 2
                    if k >= 1 and keys[a + 2:b] == keys[b + 2:b + 2 + k]:
                        removed.append(("restart", " ".join(w.strip(",") for w in words[a:b])))
                        words = words[:a] + words[b:]
                        changed = True
                        break
            if changed:
                break
    return words, removed


def _split_run_on(words: list[str]) -> tuple[list[list[str]], int]:
    """Split at "and"/"so" that start a new clause, when the sentence chains several of them or runs long."""
    keys = [_key(w) for w in words]
    joins = [i for i, k in enumerate(keys) if k in {"and", "so"} and 2 < i < len(keys) - 2
             and keys[i + 1] in SPLIT_AFTER]
    if not joins or (len(joins) < 2 and len(words) <= 24):
        return [words], 0
    parts, start = [], 0
    for j in joins:
        if j - start >= 3:
            parts.append(words[start:j])
            start = j + 1
    parts.append(words[start:])
    parts = [p for p in parts if p]
    return parts, len(parts) - 1


def _finish(words: list[str]) -> str:
    s = " ".join(words).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r"^[,;:\s]+", "", s)
    s = re.sub(r"[,;:\s]+$", "", s)
    s = re.sub(r"\bi\b", "I", s)
    s = re.sub(r"\bi'(m|ve|ll|d)\b", lambda m: "I'" + m.group(1), s)
    s = re.sub(r"^(yes|no|okay|ok|well|sure|right|hi|hey|hello)\s+(?=\w)", lambda m: m.group(1) + ", ", s, flags=re.I)
    if s:
        s = s[0].upper() + s[1:]
        if not re.search(r"[.!?]$", s):
            s += "."
    return s


# Spoken-English slips the general grammar rules don't cover: (pattern, replacement, label).
EXTRA_RULES = [
    (re.compile(r"\b(for|with|after|before|at|during|have|had|having|eat|ate|make|made|cook|cooked) the "
                r"(breakfast|lunch|dinner|brunch)\b", re.I),
     lambda m: f"{m.group(1)} {m.group(2)}", "meals don't take \u201cthe\u201d"),
    (re.compile(r"\b(discuss|explain|mention|describe) about\b", re.I), lambda m: m.group(1), "no \u201cabout\u201d after this verb"),
    (re.compile(r"\b(more|most) (better|easier|faster|bigger|smaller|harder|simpler)\b", re.I),
     lambda m: m.group(2), "double comparative"),
]


def _apply_grammar(s: str) -> tuple[str, list[str]]:
    fixed = []
    for rx, fix, why in EXTRA_RULES:
        m = rx.search(s)
        if m:
            fixed.append(f"\u201c{m.group(0)}\u201d \u2192 \u201c{fix(m)}\u201d ({why})")
            s = rx.sub(fix, s)
    for pattern, fix, why in language.GRAMMAR_RULES:
        if not fix:
            continue
        rx = re.compile(pattern, re.I)
        m = rx.search(s)
        if m:
            rep = fix(m)
            if rep and rep.lower() != m.group(0).lower():
                fixed.append(f"“{m.group(0)}” → “{rep}”")
                s = rx.sub(lambda mm: fix(mm), s, count=1)
    return s, fixed


def _same(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"[^a-z0-9' ]", "", x.lower().replace("’", "'"))  # noqa: E731
    return " ".join(norm(a).split()) == " ".join(norm(b).split())


def correct_sentence(sentence: str) -> dict | None:
    """Return {original, corrected, changes, notes} or None if nothing beyond capitals/punctuation changed."""
    words = sentence.split()
    changes, notes = [], []
    words, fillers = _remove_fillers(words)
    if fillers:
        changes.append("Removed filler" + ("s" if len(fillers) > 1 else "") + ": " +
                       ", ".join(dict.fromkeys(f"“{f}”" for f in fillers)))
    words, restarts = _remove_restarts(words)
    for kind, r in restarts:
        changes.append(f"Removed the repeated “{r}”." if kind == "repeat" and " " not in r else
                       f"Removed the false start “{r}” — say the sentence once, cleanly.")
    parts, splits = _split_run_on(words)
    if splits:
        changes.append(f"Split a run-on into {splits + 1} sentences — one idea each.")
    corrected = " ".join(_finish(p) for p in parts)
    corrected, gram = _apply_grammar(corrected)
    if gram:
        changes.append("Grammar: " + "; ".join(gram))
    low = " " + corrected.lower() + " "
    for phrase, advice in VAGUE.items():
        if f" {phrase} " in low or f" {phrase}." in low or f" {phrase}," in low:
            notes.append(f"Vague: “{phrase}” — {advice}.")
    if _same(sentence, corrected) and not notes:
        return None
    return {"original": sentence.strip(), "corrected": corrected if not _same(sentence, corrected) else None,
            "changes": changes, "notes": notes[:2]}


def segment(text: str) -> list[str]:
    """Sentences by punctuation only — a long unpunctuated stretch stays whole so run-on splitting sees it."""
    return [s for s in metrics.sentences(text or "") if s.strip()]


def correct(sentences: list[str] | str, limit: int = 6) -> dict:
    """Corrections for a whole answer (text or sentence list): changed sentences plus the full corrected version."""
    if isinstance(sentences, str):
        sentences = segment(sentences)
    items, full = [], []
    for s in sentences:
        c = correct_sentence(s)
        if c:
            items.append(c)
            full.append(c["corrected"] or _finish(s.split()))
        else:
            full.append(_finish(s.split()))
    return {"items": items[:limit], "corrected_full": " ".join(full) if any(i["corrected"] for i in items) else None,
            "source": "local"}
