"""Pronunciation help that runs locally.

What it can and can't do (be honest in the UI too):
* Read-aloud assessment: you read a known sentence; we align what the recognizer heard against the
  target, word by word. Missed and substituted words are strong signals; when local Whisper gives
  per-word confidence, low-confidence words are flagged as "unclear".
* Sound-pair detection: when a target word is heard as a different word (think → sink), we compare
  their dictionary pronunciations to name the sound that changed (TH → S).
* Stress & respelling from the CMU Pronouncing Dictionary: "development" → dih-VEL-uhp-muhnt.
This is word-level evidence, not a phoneme-level acoustic model.
"""
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

LEX_DIR = Path(__file__).resolve().parent.parent / "content" / "lexicon"
SETS_FILE = Path(__file__).resolve().parent.parent / "content" / "pronunciation.json"

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
RESPELL = {
    "AA": "ah", "AE": "a", "AH": "uh", "AO": "aw", "AW": "ow", "AY": "eye", "EH": "eh", "ER": "er",
    "EY": "ay", "IH": "ih", "IY": "ee", "OW": "oh", "OY": "oy", "UH": "u", "UW": "oo",
    "B": "b", "CH": "ch", "D": "d", "DH": "th", "F": "f", "G": "g", "HH": "h", "JH": "j", "K": "k",
    "L": "l", "M": "m", "N": "n", "NG": "ng", "P": "p", "R": "r", "S": "s", "SH": "sh", "T": "t",
    "TH": "th", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "zh",
}
# Sound groups that learners commonly confuse → drill set ids in content/pronunciation.json
SOUND_GROUPS = {  # consonant groups: any occurrence; vowel groups: only in the stressed syllable
    "TH": {"TH", "DH"}, "V/W": {"V", "W"}, "R/L": {"R", "L"}, "IH/IY": {"IH", "IY"}, "AE/EH": {"AE", "EH"},
}
VOWEL_GROUPS = {"IH/IY", "AE/EH"}
SPELLING_CONFUSIONS = [  # fallback when the heard word isn't in the dictionary (e.g. "wery")
    ("th", "s", "th → s", "TH"), ("th", "t", "th → t", "TH"), ("th", "d", "th → d", "TH"),
    ("th", "f", "th → f", "TH"), ("v", "w", "v → w", "V/W"), ("w", "v", "w → v", "V/W"),
    ("v", "b", "v → b", "B/V"), ("r", "l", "r → l", "R/L"), ("l", "r", "l → r", "R/L"),
]
CONFUSIONS = {  # (expected, heard) phone → readable label + group
    ("TH", "S"): ("th → s", "TH"), ("TH", "T"): ("th → t", "TH"), ("TH", "F"): ("th → f", "TH"),
    ("DH", "D"): ("th → d", "TH"), ("DH", "Z"): ("th → z", "TH"),
    ("V", "W"): ("v → w", "V/W"), ("W", "V"): ("w → v", "V/W"), ("V", "B"): ("v → b", "B/V"),
    ("B", "V"): ("b → v", "B/V"), ("R", "L"): ("r → l", "R/L"), ("L", "R"): ("l → r", "R/L"),
    ("IY", "IH"): ("long ee → short i", "IH/IY"), ("IH", "IY"): ("short i → long ee", "IH/IY"),
    ("AE", "EH"): ("a → e", "AE/EH"), ("EH", "AE"): ("e → a", "AE/EH"),
}
FILLERS = {"um", "uh", "umm", "uhh", "er", "erm", "ah", "hmm", "mm"}


# --------------------------------------------------------------------------- lexicon
@lru_cache(maxsize=1)
def lexicon() -> dict:
    lex = {}
    for name in ("cmudict_common.txt", "custom.txt"):
        p = LEX_DIR / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            w, *ph = line.split()
            lex[w] = ph                      # custom.txt is read last → overrides
    return lex


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower().replace("’", "'")).strip("'")


def phones(word: str) -> list[str] | None:
    w = norm(word)
    lex = lexicon()
    if w in lex:
        return lex[w]
    if w.endswith("'s") and w[:-2] in lex:          # analyst's → analyst + z/s
        base = lex[w[:-2]]
        return base + (["S"] if base[-1] in {"P", "T", "K", "F", "TH"} else ["Z"])
    return None


def _syllables(ph: list[str]) -> list[list[str]]:
    """Split phones into syllables: each vowel is a nucleus; consonants between vowels go to the next
    syllable if there's one, otherwise the first stays with the previous syllable (simple maximal onset)."""
    nuclei = [i for i, p in enumerate(ph) if p.rstrip("012") in VOWELS]
    if not nuclei:
        return [ph]
    bounds = [0]
    for a, b in zip(nuclei, nuclei[1:]):
        gap = b - a - 1
        bounds.append(a + 1 + (0 if gap <= 1 else 1))
    bounds.append(len(ph))
    return [ph[bounds[i]:bounds[i + 1]] for i in range(len(nuclei))]


def describe(word: str) -> dict:
    """Respelling with the stressed syllable in capitals, syllable count, stress position, sounds."""
    w = norm(word)
    ph = phones(w)
    if not ph:
        return {"word": w, "known": False}
    sylls = _syllables(ph)
    parts, stress_idx = [], None
    for i, s in enumerate(sylls):
        txt = "".join(("eye" if k == 0 else "ai") if p.rstrip("012") == "AY" else RESPELL.get(p.rstrip("012"), p.lower())
                      for k, p in enumerate(s))
        txt = txt.replace("ihng", "ing").replace("ngk", "nk").replace("ihn", "in") if len(txt) > 2 else txt
        if any(p.endswith("1") for p in s):
            stress_idx = i
            if len(sylls) > 1:          # capitals only mean something when there's a choice
                txt = txt.upper()
        parts.append(txt)
    consonants = {p for p in ph if p.rstrip("012") not in VOWELS}
    stressed = {p.rstrip("012") for p in ph if p.endswith("1")}
    sounds = sorted(g for g, members in SOUND_GROUPS.items()
                    if (stressed if g in VOWEL_GROUPS else consonants) & members)
    ordinal = ["1st", "2nd", "3rd", "4th", "5th", "6th"]
    return {
        "word": w, "known": True, "phones": " ".join(ph), "respell": "-".join(parts),
        "syllables": len(sylls),
        "stress": None if stress_idx is None or len(sylls) == 1 else
        f"{ordinal[min(stress_idx, 5)]} of {len(sylls)} syllables",
        "sounds": sounds,
    }


# --------------------------------------------------------------------------- drills
@lru_cache(maxsize=1)
def drill_sets() -> list[dict]:
    if not SETS_FILE.exists():
        return []
    return json.loads(SETS_FILE.read_text(encoding="utf-8"))["sets"]


def get_set(set_id: str) -> dict | None:
    return next((s for s in drill_sets() if s["id"] == set_id), None)


# --------------------------------------------------------------------------- assessment
def _tokens_from_text(text: str) -> list[str]:
    return [t for t in (norm(x) for x in re.split(r"[\s\-–—/]+", text or "")) if t]


def _confusion(expected: str, heard: str) -> list[dict]:
    """Name the sound(s) that changed between two dictionary pronunciations."""
    a, b = phones(expected), phones(heard)
    if not a or not b:
        e, h = norm(expected), norm(heard)
        for x, y, label, group in SPELLING_CONFUSIONS:
            if e.startswith(x) and h.startswith(y) and e[len(x):] == h[len(y):]:
                return [{"label": label, "group": group}]
        return []
    a0 = [p.rstrip("012") for p in a]
    b0 = [p.rstrip("012") for p in b]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a0, b=b0, autojunk=False).get_opcodes():
        if op == "replace":
            for x, y in zip(a0[i1:i2], b0[j1:j2]):
                if (x, y) in CONFUSIONS:
                    label, group = CONFUSIONS[(x, y)]
                    out.append({"label": label, "group": group})
    return out


def assess_read_aloud(target: str, heard_text: str, words: list[dict] | None = None) -> dict:
    """Compare what was heard with the sentence the speaker was asked to read."""
    target_tokens = _tokens_from_text(target)
    if words:  # Whisper words carry timing + (local) confidence
        heard = [{"w": norm(w["word"]), "p": w.get("probability")} for w in words if norm(w["word"])]
    else:
        heard = [{"w": t, "p": None} for t in _tokens_from_text(heard_text)]
    heard = [h for h in heard if h["w"] not in FILLERS]
    sm = difflib.SequenceMatcher(a=target_tokens, b=[h["w"] for h in heard], autojunk=False)

    results, extra = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                p = heard[j1 + k]["p"]
                status = "unclear" if p is not None and p < 0.55 else "good"
                results.append({"word": target_tokens[i1 + k], "status": status,
                                "confidence": None if p is None else round(p, 2)})
        elif op == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                if i1 + k >= i2:
                    extra.append(heard[j1 + k]["w"])
                    continue
                tw = target_tokens[i1 + k]
                if j1 + k < j2:
                    hw = heard[j1 + k]["w"]
                    results.append({"word": tw, "status": "substituted", "heard": hw,
                                    "confusions": _confusion(tw, hw)})
                else:
                    results.append({"word": tw, "status": "missed"})
        elif op == "delete":
            results += [{"word": target_tokens[k], "status": "missed"} for k in range(i1, i2)]
        elif op == "insert":
            extra += [heard[k]["w"] for k in range(j1, j2)]

    total = len(results) or 1
    weights = {"good": 1.0, "unclear": 0.5, "substituted": 0.0, "missed": 0.0}
    accuracy = round(100 * sum(weights[r["status"]] for r in results) / total)
    problems = [r for r in results if r["status"] != "good"]
    for r in problems:
        r["guide"] = describe(r["word"])

    # per-sound tallies: every target word containing a tracked sound counts once per group
    sounds = {}
    for r in results:
        d = describe(r["word"])
        for g in d.get("sounds", []):
            ok = r["status"] == "good"
            # a confusion in another group shouldn't count against this one
            if r["status"] == "substituted" and r.get("confusions"):
                ok = g not in {c["group"] for c in r["confusions"]}
            t = sounds.setdefault(g, [0, 0])
            t[0] += int(ok)
            t[1] += 1
    confusions = [c["label"] for r in results for c in r.get("confusions", [])]
    return {
        "target": target, "words": results, "accuracy": accuracy,
        "correct": sum(1 for r in results if r["status"] == "good"), "total": len(results),
        "missed": [r["word"] for r in results if r["status"] == "missed"],
        "substituted": [{"word": r["word"], "heard": r["heard"]} for r in results if r["status"] == "substituted"],
        "unclear": [r["word"] for r in results if r["status"] == "unclear"],
        "extra_words": extra[:10],
        "confusions": sorted(set(confusions)),
        "sounds": sounds,
        "has_confidence": any(h["p"] is not None for h in heard),
    }


def unclear_in_free_speech(words: list[dict] | None, limit: int = 8) -> list[dict]:
    """Low-confidence words from a free answer (needs local Whisper word probabilities)."""
    if not words or not any(w.get("probability") is not None for w in words):
        return []
    out, seen = [], set()
    for w in words:
        p, t = w.get("probability"), norm(w["word"])
        if p is None or len(t) < 3 or t in FILLERS or t in seen:
            continue
        if p < 0.5:
            seen.add(t)
            out.append({"word": t, "confidence": round(p, 2), "at": round(w["start"], 1), "guide": describe(t)})
    return sorted(out, key=lambda x: x["confidence"])[:limit]


def feedback(a: dict) -> tuple[list[str], list[str]]:
    good, fix = [], []
    if a["accuracy"] >= 90:
        good.append(f"Clear reading — {a['correct']} of {a['total']} words came through cleanly.")
    elif a["accuracy"] >= 50:
        good.append(f"{a['correct']} of {a['total']} words came through cleanly — fix the highlighted ones and read it again.")
    if a["substituted"]:
        w = next(r for r in a["words"] if r["status"] == "substituted")
        labels = [c["label"] for c in w.get("confusions", [])]
        fix.append(f"“{w['word']}” was heard as “{w['heard']}”" + (f" ({', '.join(labels)})" if labels else "") + ".")
        if len(a["substituted"]) > 1:
            fix.append("Also swapped: " + ", ".join(f"{x['word']} → {x['heard']}" for x in a["substituted"][1:4]) + ".")
    if a["missed"]:
        fix.append(f"Words that didn't come through: {', '.join(a['missed'][:5])}. Slow down and finish each word.")
    if a["unclear"]:
        fix.append(f"Unclear (low confidence): {', '.join(a['unclear'][:5])}. Open your mouth more and hit the stressed syllable.")
    if not fix:
        good.append("No missed or swapped words.")
    return good, fix
