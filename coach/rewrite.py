"""The mentor's rewrite: the speaker's own answer, corrected and re-shaped — "say it like this".

Works without an API key. Everything is built from the speaker's words; where the scenario's shape needs a part
they didn't say (a one-line opening, a closing question), the rewrite adds a short line and labels it as added, so
it is always clear which words are theirs.

Steps
1. Sentences. Spoken answers arrive as one long run-on from browser speech-to-text. A new sentence starts where a
   new subject starts ("…had some coffee | my son got up | he woke up at 9:30…"), unless the word before it ties
   the two together ("after I came back", "I think we…").
2. Clean each sentence: fillers, false starts and self-repairs ("have some car had some coffee" → "had some
   coffee"), filler endings ("…then yeah well"), spoken sign-offs ("this is all"), grammar slips, capitals.
3. Rhythm: very short sentences with the same subject are joined ("We came back. We got ready." → "We came back
   and got ready.").
4. Shape: the cleaned sentences are placed into the scenario's framework (e.g. Opening → Detail → Question).
   Missing first/last parts get a short added line; a missing middle part becomes a "[fill in]" prompt — the
   rewrite never invents facts.
"""
import re

from . import corrections as C
from . import grammar

SUBJECTS = {"i", "we", "he", "she", "they", "you"}
POSSESSIVE = {"my", "our", "his", "her", "their"}
NO_BREAK_PREV = {
    "that", "when", "because", "if", "so", "but", "or", "after", "before", "while", "where", "who", "which",
    "than", "until", "since", "as", "the", "a", "an", "to", "for", "with", "of", "about", "and", "think",
    "thought", "know", "knew", "said", "says", "say", "tell", "told", "hope", "guess", "feel", "felt", "believe",
    "mean", "is", "was", "were", "are", "be", "been", "what", "how", "why", "by", "from", "in", "on", "at", "into",
    "like", "whether", "once", "let", "made", "make", "help", "helped", "want", "wanted", "asked", "ask", "sure",
    "guess", "who", "whom", "whose",
}
HEDGE = re.compile(r"^(?:i think\s+)?(?:maybe|probably|i guess|kind of|sort of)\s+(?=\w)", re.I)
# Softeners are good manners in everyday talk ("maybe we could…"); in work answers they read as unsure.
FIRM_MODES = {"Interview", "Leadership", "Technical Explanation", "FDE / Customer", "Toastmasters"}
VERBISH = C.AUX | {"woke", "came", "took", "had", "said", "made", "told", "saw", "is", "was", "were", "loves",
                   "likes", "started", "wanted", "goes", "went", "gets", "got", "came", "left", "called", "asked"}
SUBORD = {"once", "when", "if", "after", "before", "while", "because", "since", "until", "although", "though",
          "whenever", "unless"}
QUESTION_START = {"what", "how", "do", "did"}
LEAD_TRIM = {"and", "then", "so", "but", "also", "well", "yeah", "okay", "ok", "anyway", "plus"}
TAIL_TRIM = {"then", "yeah", "well", "so", "okay", "ok", "and", "but", "um", "uh"}
CLOSER = re.compile(r"^(?:and\s+|so\s+)?(?:yeah\s+)?(?:(?:this|that)\s+(?:is|was)|that's|thats|this's)\s+"
                    r"(?:all|it|about it)\b", re.I)
_ABBREV = re.compile(r"\b([AaPp])\.\s?[Mm]\.?")
_SENT = re.compile(r"(?<=[.!?])(?<!\b[AaPp]\.[Mm]\.)\s+")
RESTART_VERBS = {"have", "had", "has", "get", "got", "take", "took", "make", "made", "do", "did", "go", "went",
                 "eat", "ate", "see", "saw"}
DETS = {"some", "a", "an", "the", "my", "our", "his", "her", "their"}
_TIME_CUES = ("woke up", "morning", "breakfast", "lunch", "dinner", "came back", "went to", "after that",
              "afternoon", "evening", "today", "yesterday", "weekend")
_STOP = {"the", "a", "an", "my", "our", "his", "her", "their", "some", "this", "that", "it", "all", "other"}


def _key(w: str) -> str:
    return C._key(w)


def _verbish(k: str) -> bool:
    return k in grammar.VERBS or k in grammar.PAST_FORMS or k in VERBISH or (k.endswith("ed") and len(k) > 3)


# ------------------------------------------------------------------------------------------- 1. sentences
def _raw_sentences(text: str) -> list[list[str]]:
    """Punctuated sentences first; long ones are split further at new subjects. Returns lists of raw words."""
    out = []
    for sent in _SENT.split((text or "").strip()):
        words = sent.split()
        if not words:
            continue
        # a punctuated sentence is the speaker's (or the recognizer's) own boundary: split only real run-ons
        limit = 12 if len(out) == 0 and not re.search(r"[.!?]\s*$", sent) else 25
        out.extend(_split_clauses(words) if len(words) > limit else [words])
    return out


def _split_clauses(words: list[str]) -> list[list[str]]:
    keys = [_key(w) for w in words]
    cuts = []
    for i in range(2, len(words)):
        k, prev = keys[i], keys[i - 1]
        nxt = keys[i + 1] if i + 1 < len(keys) else ""
        nxt2 = keys[i + 2] if i + 2 < len(keys) else ""
        sign_off = k in {"this", "that"} and nxt in {"is", "was"} and nxt2 in {"all", "it"}
        after_that = prev == "that" and i >= 2 and keys[i - 2] in {"after", "before"}   # "…after that | my son…"
        since = cuts[-1] if cuts else 0
        if words[i - 1].endswith(",") and any(x in SUBORD for x in keys[since:i]):
            continue                                  # "once we're done with lunch, we can relax" is one sentence
        if prev in NO_BREAK_PREV - {"and", "so"} and not sign_off and not after_that:
            continue
        if (k == "you" and nxt == "know") or (k == "i" and nxt == "mean"):
            continue                                  # fillers, not a new subject
        if k in {"he", "she", "they"} and i >= 2 and keys[i - 2] in POSSESSIVE and not _verbish(keys[i - 3] if i >= 3 else ""):
            continue                                  # "my son he says" — one subject said twice ("visit my aunt | she" is not)
        if prev in {"me", "us", "him", "her", "them"} and i >= 2 and \
                keys[i - 2] in {"told", "tell", "tells", "said", "say", "says", "asked", "ask", "promised", "showed"}:
            continue                                  # "he told me it was…" — reported speech stays one sentence
        starts = (k in SUBJECTS and nxt not in {"and", "or", ""}) \
            or k in {"it's", "that's", "there's", "he's", "she's", "they're", "we're", "i'm", "what's", "how's"} \
            or (k == "the" and nxt2 in {"is", "was", "are", "were"}) \
            or (k in QUESTION_START and nxt in {"do", "did", "about", "is", "are", "was", "you", "your", "have"}) \
            or (k in {"this", "that"} and nxt in {"is", "was"} and nxt2 in {"all", "it"}) \
            or (k in POSSESSIVE and (nxt2 in VERBISH or nxt2.endswith("ed") or
                                     (nxt2 in {"he", "she", "they"} and not _verbish(prev)))) \
            or (k == "me" and nxt == "and") \
            or (k == "there" and nxt in {"was", "were", "is", "are"}) \
            or (k == "it" and nxt in {"was", "were", "is", "'s", "felt", "made"})
        if not starts:
            continue
        j = i
        while j - 1 > 0:                              # "…and then we" / "for example my team" → cut before them
            if keys[j - 1] in {"and", "then", "so", "but", "maybe", "probably"}:
                j -= 1
            elif j - 2 > 0 and (keys[j - 2], keys[j - 1]) in {("for", "example"), ("for", "instance"), ("i", "think"),
                                                               ("i", "guess")}:
                j -= 2
            else:
                break
        if not cuts or j - cuts[-1] >= 3:
            if j >= 3:
                cuts.append(j)
    parts, start = [], 0
    for c in cuts:
        parts.append(words[start:c])
        start = c
    parts.append(words[start:])
    merged = []
    for p in parts:                                   # fragments under 3 words join the sentence before
        if merged and len([w for w in p if _key(w) not in LEAD_TRIM]) < 3:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)
    # a short lead-in with no verb ("um so yesterday | I went…") belongs to the sentence after it
    if len(merged) > 1 and len([w for w in merged[0] if _key(w) not in LEAD_TRIM]) < 3 \
            and not any(_verbish(_key(w)) for w in merged[0]):
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged


# ------------------------------------------------------------------------------------------- 2. clean
def _restart_object(words: list[str]) -> tuple[list[str], list[str]]:
    """'have some car had some coffee' → 'had some coffee' (verb + det + noun restated straight away)."""
    keys = [_key(w) for w in words]
    for i in range(len(keys) - 4):
        if keys[i] in RESTART_VERBS and keys[i + 3] in RESTART_VERBS and keys[i + 1] in DETS \
                and keys[i + 1] == keys[i + 4]:
            before, after = list(words[:i]), list(words[i + 3:])
            if before and _key(before[-1]) not in {"and", "then"}:     # "came downstairs had coffee" → "… and had …"
                ands = [j for j, w in enumerate(before) if _key(w) == "and"]
                if ands:                                               # "woke up and came down and had" → commas
                    before[ands[-1] - 1] = before[ands[-1] - 1].rstrip(",") + ","
                    del before[ands[-1]]
                before.append("and")
            return before + after, [" ".join(words[i:i + 3])]
    return words, []


def clean(words: list[str], mode: str = "", grammar_out: list | None = None,
          past_context: bool = False) -> tuple[str, list[str], bool]:
    """Clean one raw sentence. Returns (sentence, reasons, is_sign_off)."""
    reasons = []
    joined = " ".join(words)
    if CLOSER.search(joined.strip(" ,.")):
        return "", ["Dropped the sign-off — end on your last real point or your question instead."], True
    words, fillers = C._remove_fillers(list(words))
    lead = []
    while words and _key(words[0]) in LEAD_TRIM:
        if _key(words[0]) in {"well", "yeah", "okay", "ok"}:
            fillers.append(_key(words[0]))
        else:
            lead.append(_key(words[0]))
        words = words[1:]
    if lead:
        reasons.append(f"Started a new sentence instead of chaining with “{' '.join(lead)}”.")
    while words and _key(words[-1]) in TAIL_TRIM:
        if _key(words[-1]) in {"yeah", "well", "um", "uh"}:
            fillers.append(_key(words[-1]))
        words = words[:-1]
    if len(words) > 1 and _key(words[0]) in {"hi", "hey", "hello", "okay", "ok"} and _key(words[1]) == "so":
        fillers.append("so")
        words = [words[0]] + words[2:]
    if fillers:
        reasons.append("Removed filler" + ("s" if len(fillers) > 1 else "") + ": " +
                       ", ".join(dict.fromkeys(f"“{f}”" for f in fillers)) + ".")
    words, restarts = C._remove_restarts(words)
    for kind, r in restarts:
        reasons.append(f"Removed the repeated “{r}”." if kind == "repeat" else
                       f"Removed the false start “{r}” — say it once, cleanly.")
    words, obj = _restart_object(words)
    for r in obj:
        reasons.append(f"Removed the false start “{r}” — say it once, cleanly.")
    lead_text = " ".join(words)
    h = HEDGE.match(lead_text) if mode in FIRM_MODES else None
    if h and len(lead_text.split()) - len(h.group(0).split()) >= 3:
        reasons.append(f"Dropped the hedge “{h.group(0).strip()}” — say it plainly.")
        words = lead_text[h.end():].split()
    s = C._finish(words)
    s = re.sub(r"^(For example|For instance|Last year|Last week|Yesterday|In the end|After that)\s+(?=\w)", r"\1, ", s)
    first = _key(words[0]) if words else ""
    if s.endswith(".") and (first in {"what's", "how's"} or (first in QUESTION_START and len(words) > 1 and
                                                             _key(words[1]) in {"do", "did", "about", "you", "your", "is", "are", "was", "have"})):
        s = s[:-1] + "?"
    s, gchanges = grammar.fix(s, past_context)       # the mentor's grammar pass, one rule per change
    for g in gchanges:
        reasons.append("Grammar: " + grammar.describe(g))
    if grammar_out is not None:
        grammar_out.extend(gchanges)
    s, gram = C._apply_grammar(s)
    if gram:
        reasons.append("Grammar: " + "; ".join(gram) + ".")
        if grammar_out is not None:
            grammar_out.extend({"from": g.split("”")[0].strip("“"), "to": g.split("→")[-1].split("”")[0].strip(" “"),
                                "rule": "grammar", "kind": "agreement" if "doesn't" in g or "were" in g else "word choice"}
                               for g in gram)
    return s, reasons, False


def _subject(s: str) -> str:
    return _key(s.split()[0]) if s.split() else ""


def _join(a: str, b: str) -> str:
    """'We came back.' + 'We got ready.' → 'We came back and got ready.'"""
    first = a.rstrip(".!?")
    rest = b.split(" ", 1)[1] if _subject(a) == _subject(b) and _subject(a) in SUBJECTS | {"i"} else \
        (b[0].lower() + b[1:] if not b.startswith("I ") else b)
    return f"{first} and {rest}"


# ------------------------------------------------------------------------------------------- the whole answer
def corrected(text: str, mode: str = "") -> dict:
    """Line by line: every sentence as said, how to say it, and why. Plus the whole answer, corrected in order."""
    raw = _raw_sentences(text)
    lines, notes, gram_all = [], [], []
    past_ctx = False                                  # "Yesterday …" keeps the whole story in the past
    for words in raw:
        before = len(gram_all)
        s, why, sign_off = clean(words, mode, gram_all, past_ctx)
        signal = grammar.sets_past(" ".join(words), gram_all[before:])
        if signal is not None:
            past_ctx = signal
        if sign_off:
            notes.append(" ".join(words))
            lines.append({"said": " ".join(words), "better": None, "why": why, "drop": True})
            continue
        if s:
            lines.append({"said": " ".join(words), "better": s, "why": why, "drop": False})
    # rhythm: join very short neighbours with the same subject
    sents, i = [], 0
    kept = [ln for ln in lines if not ln["drop"]]
    while i < len(kept):
        cur = dict(kept[i])
        while i + 1 < len(kept):
            nxt = kept[i + 1]
            if len(cur["better"].split()) <= 7 and len(nxt["better"].split()) <= 7 \
                    and not re.search(r"\b(who|which|that|because|but)\b", cur["better"], re.I) \
                    and _subject(cur["better"]) == _subject(nxt["better"]) and _subject(cur["better"]) in SUBJECTS:
                cur = {"said": cur["said"] + " " + nxt["said"], "better": _join(cur["better"], nxt["better"]),
                       "why": cur["why"] + nxt["why"] + ["Joined two short sentences with the same subject."],
                       "drop": False}
                i += 1
            else:
                break
        sents.append(cur)
        i += 1
    split_into = len(sents)
    full = " ".join(s["better"] for s in sents)
    items = []
    for s in sents:
        if C._same(s["said"], s["better"]) and not s["why"]:
            continue
        why = list(dict.fromkeys(s["why"]))
        if not why:
            why = ["Made it a complete sentence: one idea, a capital and a full stop."]
        items.append({"original": s["said"], "corrected": s["better"], "changes": why[:3], "notes": []})
    for ln in lines:
        if ln["drop"]:
            items.append({"original": ln["said"], "corrected": None, "changes": ln["why"], "notes": []})
    def weight(item):                                 # the fixes that teach the most come first
        why = " ".join(item["changes"])
        return (-1 if "Grammar" in why else 0 if re.search(r"false start|hedge|sign-off|repeated", why) else
                1 if "filler" in why else 2 if "chaining" in why else 3)
    order = {id(x): i for i, x in enumerate(items)}
    top = sorted(items, key=lambda x: (weight(x), order[id(x)]))[:6]   # the six that teach the most…
    items = sorted(top, key=lambda x: order[id(x)])                   # …shown in the order they were said
    run_on = len(raw) == 1 or (len(_SENT.split(text.strip())) == 1 and split_into > 2)
    return {"sentences": [s["better"] for s in sents], "full": full, "items": items,
            "split_from_run_on": split_into if run_on and split_into > 1 else 0,
            "grammar": grammar.patterns(gram_all), "grammar_count": len(gram_all)}


# ------------------------------------------------------------------------------------------- 4. shape
PROMPT_ASK = re.compile(r"\bask (what|how|about|if|whether|which|where|why|who|when)\b([^.?!]*)", re.I)
_SWAP = {"they": "you", "their": "your", "them": "you", "theirs": "yours", "themselves": "yourself"}


def prompt_question(prompt: str) -> str | None:
    """'…and ask what they recommend.' → 'What would you recommend?'"""
    m = PROMPT_ASK.search(prompt or "")
    if not m:
        return None
    wh, rest = m.group(1).lower(), m.group(2).strip()
    toks = [_SWAP.get(t.lower(), t) for t in rest.split()]
    if wh == "about":
        noun = " ".join(toks)
        if re.search(r"\b(day|weekend|week|trip|holiday|vacation|evening|morning)\b", noun):
            return f"How was {noun}?"
        return f"What about {noun}?" if noun else "What about you?"
    if toks and toks[0] == "you" and len(toks) > 1:
        verb = toks[1]
        tail = " ".join(toks[2:])
        if verb in {"recommend", "suggest", "think"}:
            return f"{wh.capitalize()} would you {verb}{(' ' + tail) if tail else ''}?"
        return f"{wh.capitalize()} do you {verb}{(' ' + tail) if tail else ''}?"
    return f"{wh.capitalize()} {' '.join(toks)}?".replace("  ", " ")


def highlights(text: str, n: int = 2) -> list[str]:
    """Concrete things the speaker mentioned, in order: 'piano lessons', 'the library'."""
    out = []
    for m in re.finditer(r"\b(?:to|at|for|into)\s+(the|a|an|my|his|her|our)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)", text):
        det, phrase = m.group(1).lower(), m.group(2)
        words = [w for w in phrase.split() if w.lower() not in _STOP]
        if not words or words[0].lower() in {"time", "way", "end", "rest", "same", "first", "next", "last"}:
            continue
        if len(words) == 2 and words[1].lower() in NO_BREAK_PREV | SUBJECTS | {"and", "we", "there"}:
            words = words[:1]
        plural = words[-1].lower().endswith("s") and not words[-1].lower().endswith("ss")
        item = (det + " " if det in {"the", "my", "our", "his", "her"} and not (det == "the" and plural) else "") \
            + " ".join(words).lower()
        if all(item.split()[-1] != o.split()[-1] for o in out):
            out.append(item)
        if len(out) >= n:
            break
    return out


def answer_kind(text: str) -> str:
    low = (text or "").lower()
    if sum(1 for c in _TIME_CUES if c in low) >= 2:
        return "day"
    return "other"


INTENTS = [
    ("recommendation", re.compile(r"\brecommend", re.I),
     re.compile(r"\b(recommend|you (should|have to|must|gotta|need to)( \w+)? (try|go|see|watch|read|check|visit)|you('d| would) (love|like|enjoy)|you have to try|"
                r"worth (it|trying|a visit|watching|reading)|my favou?rite|i (really )?(loved|enjoyed)|check (it )?out|"
                r"is (so|really) good|the best)\b", re.I),
     "recommend something"),
    ("opinion", re.compile(r"\b(do you think|your (view|opinion)|agree or disagree|is it better|should people)\b", re.I),
     re.compile(r"\b(i think|i believe|in my (view|opinion)|i feel|i'd say|i would say|i agree|i disagree|"
                r"it's better|should)\b", re.I),
     "give your opinion"),
]


def intent_check(prompt: str, text: str) -> dict | None:
    """Did the answer do what the prompt asked? Returns a mismatch description, or None."""
    for name, asks, does, verb in INTENTS:
        if asks.search(prompt or "") and not does.search(text or ""):
            kind = answer_kind(text)
            told = "described your day" if kind == "day" else "talked about something else"
            hl = highlights(text, 1)
            starter = None
            if name == "recommendation":
                starter = (f"One thing I really enjoyed recently was {hl[0]} — " if hl else
                           "One thing I really enjoyed recently was [name it] — ") + "[say why in one sentence]."
            elif name == "opinion":
                starter = "I think [your view], because [your main reason]."
            return {"asked": verb, "told": told,
                    "message": f"You {told}, but the prompt asked you to {verb}.",
                    "retry": f"Start by answering the prompt: {verb} in your first sentence, then give one reason.",
                    "starter": starter}
    return None


def _opening(mode: str, label: str, text: str, prompt: str, mismatch: dict | None) -> str | None:
    hl = highlights(text)
    if mismatch and mismatch.get("starter") and "[" not in mismatch["starter"]:
        return mismatch["starter"]
    if mode == "Everyday Conversation":
        if answer_kind(text) == "day" and hl:
            low = (text or "").lower()
            when = next((w for w in ("yesterday", "last weekend", "last night", "this weekend", "last week")
                         if w in low), None)
            mood = "fun" if re.search(r"\b(fun|enjoy|enjoyed|love|loved|like|liked|best|great)\b", low) else "busy"
            start = f"{when.capitalize()} was a {mood} day" if when and not low.lstrip().startswith(when) else \
                (f"It was a {mood} day" if when else f"It's been a {mood} day")
            if len(hl) > 1:
                return f"{start} — {hl[0]}, then {hl[1]}."
            return f"{start} at {hl[0]}." if hl[0].startswith("the ") else f"{start} — {hl[0]}."
        return f"I've got something fun to tell you about {hl[0]}." if hl else "I've got something to tell you."
    if mode == "Tell a Story":
        return f"Let me tell you about the day with {hl[0]}." if hl else "Let me tell you what happened."
    if mode == "Difficult Conversations":
        return "I can see this matters to you, and I want us to sort it out."
    if mode == "Toastmasters":
        return None
    return None


def _belongs_elsewhere(s: str, labels: list[str], lab: str, cues: dict) -> bool:
    low = " " + s.lower() + " "
    return any(any(c in low for c in cues.get(other, [])) for other in (labels[0], labels[-1]) if other != lab) \
        and "?" in s


MAX_MIDDLE = 6


def _info(s: str, text: str) -> float:
    """How much a sentence tells the listener: concrete words, numbers, names, the things they highlighted."""
    words = [w for w in re.findall(r"[A-Za-z0-9:']+", s) if w.lower() not in _STOP and len(w) > 2]
    score = len(set(w.lower() for w in words))
    score += 2 if re.search(r"\d", s) else 0
    score += 2 * sum(1 for h in highlights(text, 3) if h.split()[-1] in s.lower())
    return score


def _closing(mode: str, label: str, text: str, prompt: str, mismatch: dict | None = None) -> str | None:
    q = None if mismatch else prompt_question(prompt)   # off-topic: close on what they actually talked about
    if label == "Question":
        if q:
            return q
        return "How about you — how was your day?" if answer_kind(text) == "day" else "What about you?"
    return None


def script(text: str, mode: str, spec: dict, cues: dict, prompt: str = "", fixed: dict | None = None,
           mismatch: dict | None = None) -> dict:
    """The answer re-shaped into the scenario's parts. Each part says whether the words are the speaker's."""
    fixed = fixed or corrected(text)
    all_sents = list(fixed["sentences"])
    # fragments ("Science project.") stay in the line-by-line list but not in the version to say aloud
    sents = [s for s in all_sents if len([w for w in s.split() if _key(w) not in _STOP]) >= 3]
    labels = list(spec.get("framework") or [])
    if "Point" in labels and "Reason" in labels:      # "X is better because Y" → "X is better. That's because Y."
        out = []
        for s in sents:
            m = re.match(r"^(.{12,}?)\s+because\s+(.{8,})$", s)
            if m and not any(re.search(r"\bbecause\b", t) for t in out):
                out += [m.group(1).rstrip(",") + ".", "That's because " + m.group(2)]
            else:
                out.append(s)
        sents = out
    middle = labels[1:-1] if len(labels) > 2 else labels[1:]
    parts, used = [], set()
    for idx, lab in enumerate(labels):
        is_mid = lab in middle
        mine = [i for i, s in enumerate(sents) if i not in used and
                any(c in " " + s.lower() + " " for c in cues.get(lab, []))]
        if is_mid and len(middle) == 1:               # one middle part (Detail): everything they said, in order
            mine = [i for i in range(len(sents)) if i not in used and not _belongs_elsewhere(sents[i], labels, lab, cues)]
        if is_mid and len(mine) > MAX_MIDDLE:         # long answers: keep the most informative, in order
            chosen = set(sorted(mine, key=lambda i: -_info(sents[i], text))[:MAX_MIDDLE])
            for i in sorted(chosen):                  # keep who "he / she / they" refers to
                if _subject(sents[i]) in {"he", "she", "they", "it"} and i - 1 in mine and i - 1 not in chosen:
                    chosen.add(i - 1)
            mine = sorted(chosen)
        mine = sorted(mine)[: (MAX_MIDDLE + 2 if is_mid else 1)]
        if mine:
            used.update(mine)
            parts.append({"label": lab, "idx": list(mine), "yours": True})
            continue
        line = _opening(mode, lab, text, prompt, mismatch) if idx == 0 else \
            _closing(mode, lab, text, prompt, mismatch) if idx == len(labels) - 1 else None
        if line:
            parts.append({"label": lab, "text": line, "yours": False})
        else:
            hint = PART_PROMPTS.get(lab, "add one sentence")
            parts.append({"label": lab, "text": f"[{lab}: {hint}]", "yours": False, "fill_in": True})
    leftover = [i for i in range(len(sents)) if i not in used]
    mine_parts = [p for p in parts if p["yours"]]
    if leftover and not mine_parts and len(labels) > 1:        # nothing matched: first sentence opens, the rest is the middle
        first, body = parts[0], (parts[labels.index("Action")] if "Action" in labels else parts[1])
        first.update({"idx": [leftover[0]], "yours": True, "fill_in": False, "text": None})
        mine_parts = [first]
        if len(leftover) > 1 and body is not first:
            body.update({"idx": [], "yours": True, "fill_in": False, "text": None})
            mine_parts.append(body)
        leftover = leftover[1:]
    if len(middle) == 1 and any(p["yours"] and p["label"] in middle for p in parts):
        leftover = []                                           # one middle part already chose what to keep
    for i in leftover:                                          # nothing they said disappears: nearest earlier part
        earlier = [p for p in mine_parts if min(p["idx"] or [i]) <= i]
        (earlier[-1] if earlier else mine_parts[0])["idx"].append(i) if mine_parts else None
    for p in parts:
        if p.get("idx") is not None and p["yours"]:
            p["text"] = " ".join(sents[i] for i in sorted(p.pop("idx")))
        p.pop("idx", None)
    full = " ".join(p["text"] for p in parts if not p.get("fill_in"))
    return {"parts": parts, "full": full, "added": sum(1 for p in parts if not p["yours"])}


PART_PROMPTS = {
    "Situation": "where and when, in one sentence", "Task": "what you had to do", "Action": "what you did",
    "Result": "how it turned out", "Lesson": "what you learned — “What I took from it was …”",
    "Point": "your view in one sentence — “I think …”", "Reason": "your main reason — “because …”",
    "Example": "one real example", "Point again": "your point again — “That's why I think …”",
    "Acknowledge": "show you heard them", "Next step": "what happens next — “What I'd suggest is …”",
    "Decision / Ask": "what you decided or need", "Why": "why it matters", "Trade-offs": "what you're giving up",
    "Next steps": "who does what, by when", "Definition": "what it is, in plain words",
    "Example / analogy": "a picture they already know", "How it works": "the steps, in order",
    "Hook": "a question, number or tiny story", "Message": "the one idea to remember",
    "Story / points": "a real example", "Call-back close": "a last line that returns to your opening",
}


def mentor_rewrite(text: str, mode: str, spec: dict, cues: dict, prompt: str = "") -> dict:
    fixed = corrected(text, mode)
    mismatch = intent_check(prompt, text)
    sc = script(text, mode, spec, cues, prompt, fixed, mismatch)
    return {"corrected": fixed, "script": sc, "off_topic": mismatch}
