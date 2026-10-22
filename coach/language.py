"""Vocabulary and sentence-structure analysis of a spoken answer, plus the daily word bank.

All measurements are local and deterministic. When an LLM is configured, analysis.py adds its
context-aware word upgrades and grammar notes on top (never in place of these numbers).
"""
import json
import random
import re
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import pronunciation

try:
    from wordfreq import zipf_frequency
except Exception:  # optional: vocabulary "sophistication" needs it
    zipf_frequency = None

VOCAB_FILE = Path(__file__).resolve().parent.parent / "content" / "vocabulary.json"
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")

STOPWORDS = set("""a an the and or but if then so to of in on at for with from by as is are was were be been being am
do does did done doing have has had having i you he she it we they me him her us them my your his its our their
mine yours this that these those there here what which who whom whose how why when where not no yes all any some
each every both either neither more most much many few less least than too very just also only even still really
can could will would shall should may might must about into over under up down out off again once because while
until after before during through between against among within without per via like um uh er ah oh okay ok yeah
well actually basically literally anyway etc i'm i've i'd i'll you're we're they're it's that's there's don't didn't
doesn't can't won't isn't aren't wasn't weren't let's one two three four five first second third lot""".split())

# weak / vague wording → stronger options (multi-word phrases are matched first)
WEAK = {
    "very good": ["excellent", "strong", "outstanding"], "really good": ["excellent", "impressive"],
    "pretty good": ["solid", "effective"], "very bad": ["severe", "poor", "damaging"],
    "very big": ["massive", "substantial"], "very important": ["critical", "essential", "vital"],
    "very small": ["minor", "marginal"], "very hard": ["challenging", "demanding"],
    "very fast": ["rapid", "swift"], "very clear": ["evident", "unmistakable"],
    "a lot of": ["significant", "numerous", "a large share of"], "lots of": ["numerous", "plenty of"],
    "kind of": ["somewhat", "(cut it)"], "sort of": ["somewhat", "(cut it)"],
    "make sure": ["ensure", "confirm"], "find out": ["discover", "determine"],
    "come up with": ["devise", "propose"], "deal with": ["handle", "address", "resolve"],
    "get better": ["improve"], "look into": ["investigate", "examine"],
    "good": ["effective", "strong", "valuable"], "bad": ["poor", "harmful", "flawed"],
    "nice": ["helpful", "elegant", "pleasant"], "big": ["significant", "major", "large-scale"],
    "thing": ["issue", "factor", "aspect"], "things": ["issues", "factors", "details"],
    "stuff": ["work", "material", "details"], "got": ["received", "achieved", "obtained"],
    "fix": ["resolve", "correct"], "show": ["demonstrate", "reveal"], "help": ["enable", "support"],
}
SINGLE_MIN_COUNT = {"got": 2, "fix": 3, "show": 3, "help": 3}   # common verbs only flag when overused
INTENSIFIERS = {"very", "really", "so", "super", "totally", "extremely", "pretty"}

SUBORDINATORS = ["because", "although", "though", "which", "while", "whereas", "unless", "since", "so that",
                 "when", "if", "after", "before", "even though", "that's why", "which means"]
CONNECTORS = re.compile(r"\b(and|so|then|but|and then|and so)\b")
IRREGULAR_PP = ("done|made|taken|given|written|seen|known|shown|built|sent|found|told|held|kept|left|lost|paid|"
                "run|set|put|chosen|driven|spoken|broken|thrown|grown|drawn|begun|brought|bought|caught|taught|"
                "thought|said|led|met|read|cut|hit|hidden|forgotten|gotten")
PASSIVE = re.compile(r"\b(am|is|are|was|were|be|been|being|get|gets|got)\s+(?:\w+ly\s+)?(\w+ed|" + IRREGULAR_PP + r")\b")
PASSIVE_FALSE = {"interested", "excited", "tired", "worried", "concerned", "supposed", "used", "based", "related",
                 "involved", "engaged", "married", "needed", "required", "finished", "done", "located", "focused",
                 "scared", "bored", "pleased", "surprised", "disappointed", "satisfied", "prepared", "allowed"}

PAST_TO_BASE = {"went": "go", "knew": "know", "saw": "see", "did": "do", "made": "make", "took": "take",
                "came": "come", "gave": "give", "wrote": "write", "ran": "run", "got": "get", "thought": "think",
                "told": "tell", "found": "find", "spoke": "speak", "broke": "break", "said": "say", "bought": "buy"}
GRAMMAR_RULES = [
    (r"\b(he|she|it)\s+don'?t\b", lambda m: f"{m.group(1)} doesn't", "Use “doesn't” with he / she / it."),
    (r"\b(they|we|you)\s+was\b", lambda m: f"{m.group(1)} were", "Use “were” with they / we / you."),
    (r"\b(i|he|she|it)\s+are\b|\b(they|we)\s+is\b", None, "Subject and verb don't agree."),
    (r"\bthere'?s\s+(many|several|lots|two|three|four|five|multiple|a few)\b",
     lambda m: f"there are {m.group(1)}", "Use “there are” before a plural."),
    (r"\bmore\s+(better|worse|easier|harder|faster|bigger|smaller|cheaper|simpler)\b",
     lambda m: m.group(1), "Double comparative — drop “more”."),
    (r"\b(didn'?t|doesn'?t|don'?t|did not|does not|do not|can'?t|cannot|won'?t|will not)\s+(" +
     "|".join(PAST_TO_BASE) + r")\b", lambda m: f"{m.group(1)} {PAST_TO_BASE[m.group(2)]}",
     "After do / did / can / will, use the base verb."),
    (r"\bless\s+(people|things|issues|bugs|errors|customers|users|items|meetings|tickets|mistakes|requests)\b",
     lambda m: f"fewer {m.group(1)}", "Use “fewer” with things you can count."),
    (r"\b(don'?t|didn'?t|doesn'?t|can'?t|won'?t|never)\s+(?:\w+\s+)?(nothing|nobody|no one|nowhere)\b",
     None, "Double negative."),
    (r"\b(could|should|would|must)\s+of\b", lambda m: f"{m.group(1)} have", "It's “could have”, not “could of”."),
]


def tokens(text: str) -> list[str]:
    return [t.lower().replace("’", "'") for t in WORD_RE.findall(text or "")]


# --------------------------------------------------------------------------- sentences
def split_sentences(text: str, words: list[dict] | None = None) -> tuple[list[str], str]:
    """Return (sentences, method). Uses punctuation if present, else pauses between timestamped words."""
    t = (text or "").strip()
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    if len(parts) > 1 or (parts and re.search(r"[.!?]$", t) and len(tokens(t)) < 35):
        return parts, "punctuation"
    if words and len(words) > 3:
        out, cur = [], [words[0]["word"].strip()]
        for prev, w in zip(words, words[1:]):
            if w["start"] - prev["end"] >= 0.6 or prev["word"].strip()[-1:] in ".?!":
                out.append(" ".join(cur))
                cur = []
            cur.append(w["word"].strip())
        out.append(" ".join(cur))
        return [s for s in out if s], "pauses"
    return ([t] if t else []), "none"


def _mattr(words: list[str], window: int = 25) -> float:
    if not words:
        return 0.0
    if len(words) <= window:
        return len(set(words)) / len(words)
    vals = [len(set(words[i:i + window])) / window for i in range(len(words) - window + 1)]
    return sum(vals) / len(vals)


def vocabulary(text: str, daily: list[str] | None = None) -> dict:
    toks = tokens(text)
    low = " " + " ".join(toks) + " "
    content = [w for w in toks if w not in STOPWORDS and len(w) > 2]
    diversity = _mattr(content)

    advanced = []
    if zipf_frequency:
        for w in dict.fromkeys(content):
            z = zipf_frequency(w, "en")
            if 1.0 < z < 4.0 and len(w) >= 5:
                advanced.append((w, z))
        advanced.sort(key=lambda x: x[1])
    adv_share = len([w for w in content if any(w == a for a, _ in advanced)]) / len(content) if content else 0

    weak, consumed = [], low
    for phrase in sorted(WEAK, key=lambda p: -len(p)):
        n = len(re.findall(r"\b" + re.escape(phrase) + r"\b", consumed))
        need = SINGLE_MIN_COUNT.get(phrase, 1)
        if n >= need:
            weak.append({"phrase": phrase, "count": n, "alternatives": WEAK[phrase]})
            consumed = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", consumed)
    intensifiers = sum(1 for w in toks if w in INTENSIFIERS)

    counts = Counter(w for w in content if len(w) >= 4)
    weak_words = {p for x in weak for p in x["phrase"].split()}
    overused = [(w, n) for w, n in counts.most_common(8) if n >= 3 and w not in weak_words]

    used_daily = [w for w in (daily or []) if uses_word(toks, w)]

    score = 55.0
    score += max(0.0, min(22.0, (diversity - 0.55) * 75))         # 0.55 → +0, 0.85 → +22
    score += min(14.0, adv_share * 100)                            # 14% advanced words → +14
    score -= min(20, sum(min(2, x["count"]) * 4 for x in weak))
    score -= min(9, len(overused) * 3)
    score -= min(6, max(0, intensifiers - 2) * 2)
    if len(content) < 12:
        score = min(score, 60)
    return {
        "score": int(max(20, min(97, round(score)))),
        "content_words": len(content), "unique_content_words": len(set(content)),
        "diversity": round(diversity, 2),
        "advanced_share": round(adv_share, 2), "advanced_words": [w for w, _ in advanced[:8]],
        "weak": weak, "overused": [{"word": w, "count": n} for w, n in overused],
        "intensifiers": intensifiers, "daily_used": used_daily,
        "frequency_data": bool(zipf_frequency),
    }


def grammar_issues(text: str) -> list[dict]:
    low = " ".join(tokens(text))
    issues = []
    for pattern, fix, why in GRAMMAR_RULES:
        for m in re.finditer(pattern, low):
            issues.append({"original": m.group(0), "suggestion": fix(m) if fix else None, "issue": why})
    toks = tokens(text)
    for a, b in zip(toks, toks[1:]):                       # a / an by sound, not spelling
        ph = pronunciation.phones(b)
        if a in ("a", "an") and ph:
            vowel_start = ph[0].rstrip("012") in pronunciation.VOWELS
            if a == "a" and vowel_start:
                issues.append({"original": f"a {b}", "suggestion": f"an {b}", "issue": "Use “an” before a vowel sound."})
            elif a == "an" and not vowel_start:
                issues.append({"original": f"an {b}", "suggestion": f"a {b}", "issue": "Use “a” before a consonant sound."})
    return issues[:8]


def sentences(text: str, words: list[dict] | None = None) -> dict:
    sents, method = split_sentences(text, words)
    lens = [len(tokens(s)) for s in sents]
    n = len(sents)
    out = {"method": method, "count": n}
    if method == "none" or n == 0:
        wc = len(tokens(text))
        conn = len(CONNECTORS.findall(" ".join(tokens(text))))
        out.update({"avg_length": None, "connector_rate": round(100 * conn / wc, 1) if wc else 0,
                    "note": "No punctuation or word timings — sentence boundaries unknown. Record audio with "
                            "Whisper enabled for full sentence analysis.",
                    "run_ons": [], "long": [], "passive": [], "repeated_opener": None, "complex_share": None,
                    "grammar": grammar_issues(text)})
        score = 70 - min(20, max(0, (out["connector_rate"] - 6)) * 3) - min(16, len(out["grammar"]) * 4)
        out["score"] = int(max(20, min(90, score)))
        return out

    avg = sum(lens) / n
    long_s = [s for s, ln in zip(sents, lens) if ln > 30]
    run_ons = [s for s, ln in zip(sents, lens)
               if (ln >= 18 and len(CONNECTORS.findall(s.lower())) >= 3) or len(CONNECTORS.findall(s.lower())) >= 5]
    openers = Counter(tokens(s)[0] for s in sents if tokens(s))
    top = openers.most_common(1)[0] if openers else (None, 0)
    repeated_opener = top[0] if n >= 3 and top[1] >= 3 and top[1] / n >= 0.4 else None
    passive = []
    for s in sents:
        for m in PASSIVE.finditer(s.lower()):
            if m.group(2) not in PASSIVE_FALSE:
                passive.append(m.group(0))
    complex_n = sum(1 for s in sents if any(re.search(r"\b" + re.escape(k) + r"\b", s.lower()) for k in SUBORDINATORS))
    complex_share = complex_n / n
    questions = sum(1 for s in sents if s.rstrip().endswith("?"))
    grammar = grammar_issues(text)

    score = 86.0
    if avg > 22:
        score -= min(15, (avg - 22) * 1.5)
    elif avg < 8:
        score -= min(10, (8 - avg) * 2)
    score -= min(15, len(long_s) * 5)
    score -= min(18, len(run_ons) * 6)
    score -= 8 if repeated_opener else 0
    score -= min(9, max(0, len(passive) - 1) * 3)
    score -= min(16, len(grammar) * 4)
    if n >= 3 and 0.2 <= complex_share <= 0.7:
        score += 5
    if n >= 3 and max(lens) - min(lens) >= 6:                      # varied rhythm
        score += 3
    out.update({
        "avg_length": round(avg, 1), "shortest": min(lens), "longest": max(lens), "lengths": lens,
        "long": long_s[:3], "run_ons": run_ons[:3], "repeated_opener": repeated_opener,
        "openers": openers.most_common(3), "passive": passive[:5], "complex_share": round(complex_share, 2),
        "questions": questions, "grammar": grammar, "score": int(max(20, min(97, round(score)))),
    })
    return out


def feedback(v: dict, s: dict, challenge: bool) -> tuple[list[str], list[str]]:
    good, fix = [], []
    if v["weak"]:
        w = v["weak"][0]
        alt = "" if challenge else f" — try “{w['alternatives'][0]}”"
        fix.append(f"Weak wording: “{w['phrase']}” ×{w['count']}{alt}.")
    if v["overused"]:
        fix.append(f"You repeated “{v['overused'][0]['word']}” {v['overused'][0]['count']} times — vary it.")
    if v["advanced_words"]:
        good.append(f"Precise vocabulary: {', '.join(v['advanced_words'][:3])}.")
    if s.get("run_ons"):
        fix.append("Run-on sentence: you chained ideas with “and / so / then”. Stop, then start a new sentence.")
    if s.get("repeated_opener"):
        fix.append(f"Most sentences start with “{s['repeated_opener']}”. Vary your openers.")
    if s.get("grammar"):
        g = s["grammar"][0]
        fix.append(f"Grammar: “{g['original']}” — {g['issue']}")
    if s.get("avg_length") and 10 <= s["avg_length"] <= 22 and not s.get("run_ons"):
        good.append(f"Well-sized sentences (average {s['avg_length']} words).")
    return good, fix


# --------------------------------------------------------------------------- word bank
@lru_cache(maxsize=1)
def word_bank() -> list[dict]:
    if not VOCAB_FILE.exists():
        return []
    return json.loads(VOCAB_FILE.read_text(encoding="utf-8"))["words"]


def daily_words(day: date | None = None, n: int = 3) -> list[dict]:
    bank = word_bank()
    if not bank:
        return []
    rng = random.Random((day or date.today()).isoformat())
    return rng.sample(bank, min(n, len(bank)))


def forms(word: str) -> set[str]:
    w = word.lower()
    out = {w, w + "s", w + "es", w + "ed", w + "d", w + "ing", w + "ly"}
    if w.endswith("e"):
        out |= {w[:-1] + "ing", w[:-1] + "ion", w[:-1] + "ation"}
    if w.endswith("y"):
        out |= {w[:-1] + "ies", w[:-1] + "ied"}
    if w.endswith("ize"):
        out |= {w[:-3] + "ization"}
    return out


def uses_word(toks: list[str], word: str) -> bool:
    if "-" in word:
        return word.lower() in " ".join(toks) or word.lower().replace("-", " ") in " ".join(toks)
    f = forms(word)
    return any(t in f for t in toks)
