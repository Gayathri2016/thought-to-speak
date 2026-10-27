"""Grammar corrections a mentor would make, each with a one-line rule the speaker can learn from.

Works without an API key. Rules target the slips that come up most in spoken English, especially for people
speaking English as a second language, and only fire on patterns specific enough to be safe:

  tense       "Yesterday we go to the park and play" → "went … played"; "I woke up and go" → "went"
  agreement   "she have", "he like", "people is", "everybody are"
  verb form   "can goes", "want go", "must to", "I am agree", "have went", "enjoy to cook"
  pronouns    "Me and my son went" → "My son and I went"; "My son he likes" → "My son likes"
  nouns       "informations", "two time", "many information"
  words       "explain me", "say me", "discuss about", "go to home", "more easy", "on monday"

`fix(sentence)` returns the corrected sentence and a list of changes:
  {"from": "go", "to": "went", "rule": "…", "kind": "tense"}
"""
import re

# ------------------------------------------------------------------------------------------------ verbs
IRREGULAR = {
    "be": ("was", "been"), "become": ("became", "become"), "begin": ("began", "begun"), "break": ("broke", "broken"),
    "bring": ("brought", "brought"), "build": ("built", "built"), "buy": ("bought", "bought"),
    "catch": ("caught", "caught"), "choose": ("chose", "chosen"), "come": ("came", "come"), "cut": ("cut", "cut"),
    "do": ("did", "done"), "drink": ("drank", "drunk"), "drive": ("drove", "driven"), "eat": ("ate", "eaten"),
    "fall": ("fell", "fallen"), "feel": ("felt", "felt"), "find": ("found", "found"), "fly": ("flew", "flown"),
    "forget": ("forgot", "forgotten"), "get": ("got", "gotten"), "give": ("gave", "given"), "go": ("went", "gone"),
    "grow": ("grew", "grown"), "have": ("had", "had"), "hear": ("heard", "heard"), "hold": ("held", "held"),
    "keep": ("kept", "kept"), "know": ("knew", "known"), "leave": ("left", "left"), "lose": ("lost", "lost"),
    "make": ("made", "made"), "meet": ("met", "met"), "pay": ("paid", "paid"), "put": ("put", "put"),
    "read": ("read", "read"), "ride": ("rode", "ridden"), "run": ("ran", "run"), "say": ("said", "said"),
    "see": ("saw", "seen"), "sell": ("sold", "sold"), "send": ("sent", "sent"), "sing": ("sang", "sung"),
    "sit": ("sat", "sat"), "sleep": ("slept", "slept"), "speak": ("spoke", "spoken"), "spend": ("spent", "spent"),
    "stand": ("stood", "stood"), "steal": ("stole", "stolen"), "swim": ("swam", "swum"), "take": ("took", "taken"),
    "teach": ("taught", "taught"), "tell": ("told", "told"), "think": ("thought", "thought"),
    "throw": ("threw", "thrown"), "understand": ("understood", "understood"), "wake": ("woke", "woken"),
    "wear": ("wore", "worn"), "win": ("won", "won"), "write": ("wrote", "written"),
}
REGULAR = set("""walk talk play watch work start finish visit cook clean wash help ask call try stay live love like want
need plan decide arrive return open close move change learn use wait enjoy miss stop pick drop check order share
show join attend celebrate prepare travel explain listen look laugh cry dance shop study practice practise relax
rest invite enter fix paint paste type text email answer agree argue offer remember happen hope serve deliver
save earn carry worry reply apply marry kiss push pull turn print fill climb borrow follow believe receive achieve
reach touch hate prefer suggest mention complain introduce improve reduce increase cause create manage support
report test hire count waste book rent drop jump kick laugh smile thank wish complete organize organise discuss
""".split())
VERBS = set(IRREGULAR) | REGULAR
PAST_FORMS = {p for p, _ in IRREGULAR.values()}
PARTICIPLE = {p: pp for p, pp in IRREGULAR.values() if p != pp}          # went → gone (for "have went")
VOWELS = "aeiou"


def past(v: str) -> str:
    if v in IRREGULAR:
        return IRREGULAR[v][0]
    if v.endswith("e"):
        return v + "d"
    if len(v) > 2 and v.endswith("y") and v[-2] not in VOWELS:
        return v[:-1] + "ied"
    if _cvc(v):
        return v + v[-1] + "ed"
    return v + "ed"


def third(v: str) -> str:
    special = {"have": "has", "do": "does", "go": "goes", "be": "is"}
    if v in special:
        return special[v]
    if re.search(r"(s|sh|ch|x|z|o)$", v):
        return v + "es"
    if len(v) > 2 and v.endswith("y") and v[-2] not in VOWELS:
        return v[:-1] + "ies"
    return v + "s"


def gerund(v: str) -> str:
    if v.endswith("ie"):
        return v[:-2] + "ying"
    if v.endswith("e") and not v.endswith("ee") and v != "be":
        return v[:-1] + "ing"
    if _cvc(v):
        return v + v[-1] + "ing"
    return v + "ing"


def _cvc(v: str) -> bool:                 # stop → stopped, plan → planned (short consonant-vowel-consonant words)
    return len(v) <= 4 and len(v) >= 3 and v[-1] not in VOWELS + "wxy" and v[-2] in VOWELS and v[-3] not in VOWELS


# ------------------------------------------------------------------------------------------------ word lists
PRONOUN_SUBJ = {"i", "we", "you", "they", "he", "she", "it"}
POSS = {"my", "our", "his", "her", "their", "the", "your"}
PERSON = {"son", "daughter", "wife", "husband", "mom", "mum", "mother", "dad", "father", "friend", "brother",
          "sister", "boss", "manager", "teacher", "neighbor", "neighbour", "kid", "child", "baby", "team", "family",
          "colleague", "roommate", "partner", "aunt", "uncle", "cousin", "grandma", "grandpa", "doctor"}
MODALS = {"can", "could", "will", "would", "should", "must", "may", "might", "to", "did", "didn't", "do", "does",
          "don't", "doesn't", "let", "make", "makes", "made", "help", "helps", "helped", "let's", "won't", "can't",
          "cannot", "couldn't", "shouldn't", "wouldn't", "not", "never"}
PAST_MARK = re.compile(r"\b(yesterday|last (night|week|weekend|month|year|summer|winter|spring|fall|time|monday|tuesday|"
                       r"wednesday|thursday|friday|saturday|sunday)|\d+ (days?|weeks?|months?|years?) ago|"
                       r"(a|one|two|three|few) (days?|weeks?|months?|years?) ago|earlier today|this morning|"
                       r"when i was (a )?(kid|child|young|little)|in (19|20)\d\d)\b", re.I)
PRESENT_RE = re.compile(r"\b(now|nowadays|usually|every (day|morning|week|night|weekend)|these days|currently|always|"
                        r"in general|today i|next (time|week|month|year)|tomorrow|from now on|will|going to|"
                        r"remember:|try this)\b", re.I)
PRESENT_MARK = {"now", "nowadays", "usually", "today", "currently", "always", "still"}
STANCE = {"think", "know", "guess", "mean", "believe", "feel", "hope", "suppose", "agree", "disagree", "remember",
          "wonder", "love", "like"}
UNCOUNT = {"informations": "information", "advices": "advice", "furnitures": "furniture",
           "equipments": "equipment", "homeworks": "homework", "luggages": "luggage", "feedbacks": "feedback",
           "softwares": "software", "knowledges": "knowledge", "researches": "research", "evidences": "evidence",
           "staffs": "staff", "breads": "bread", "moneys": "money", "traffics": "traffic"}
COUNT_UNITS = {"hour", "minute", "day", "week", "month", "year", "time", "kid", "book", "place", "friend",
               "dollar", "mile", "kilometer", "page", "people", "child", "person", "thing", "car", "language"}
AFTER_NOUN = {"of", "and", "ago", "later", "in", "at", "on", "with", "to", "before", "after", "from", "for",
              "there", "here", "now", "the", "a", "an", "or", "but", "so", "because", "when", "a", "every", "each",
              "this", "that", "last", "early", "late", "long", "since", "until", "i", "we", "he", "she", "they",
              "you", "it", "my", "our", "is", "was", "are", "were", "already", "too", "again", "back", "left"}
NUMBERS = {"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "many", "several", "few",
           "twenty", "thirty", "hundred"}
PLURAL_IRR = {"child": "children", "person": "people", "man": "men", "woman": "women"}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
        "march", "april", "june", "july", "august", "september", "october", "november", "december", "english",
        "spanish", "french", "hindi", "telugu", "tamil", "chinese", "japanese", "german", "korean", "arabic"]
SHORT_ADJ = {"easy": "easier", "big": "bigger", "fast": "faster", "cheap": "cheaper", "happy": "happier",
             "hard": "harder", "small": "smaller", "busy": "busier", "nice": "nicer", "quick": "quicker",
             "simple": "simpler", "early": "earlier", "late": "later", "long": "longer", "short": "shorter",
             "good": "better", "bad": "worse"}

RULES_TEXT = {
    "tense": "you're talking about the past, so use the past tense",
    "tense_chain": "keep the same tense through the sentence — the first verb is in the past",
    "agreement_s": "with he / she / it (or one person), add -s to the verb",
    "agreement_have": "with he / she / it, use “has”",
    "people": "“people” is plural — use “are / were”",
    "everyone": "“everybody / everyone” takes a singular verb",
    "modal": "after can / will / should / must, use the base verb",
    "to_inf": "use “to” before the second verb (want to go)",
    "no_to": "no “to” after can / must / should / will / let",
    "agree": "“agree” is a verb — say “I agree”, not “I am agree”",
    "participle": "after have / has / had, use the past participle",
    "gerund": "after “enjoy”, use the -ing form",
    "me_and": "when you're the one doing it, say “… and I”, and put yourself last",
    "double_subject": "don't repeat the subject with a pronoun",
    "uncount": "this noun has no plural",
    "much": "use “much” with nouns you can't count",
    "plural": "after a number, use the plural",
    "explain_to": "you explain something to someone",
    "tell": "you tell someone something (you say something)",
    "about": "no “about” after this verb",
    "home": "no “to” before “home”",
    "comparative": "short adjectives take -er: easier, bigger",
    "capital": "days, months and languages start with a capital letter",
    "present_perfect": "with a finished time (“last week”), use the simple past",
    "agreement_plural": "with I / we / you / they (or people), the verb has no -s",
    "be": "match “be” to the subject: I am, he is, they are (I was, they were)",
}

KIND = {"tense": "tense", "tense_chain": "tense", "present_perfect": "tense", "agreement_s": "agreement",
        "agreement_have": "agreement", "people": "agreement", "everyone": "agreement", "modal": "verb form",
        "to_inf": "verb form", "no_to": "verb form", "agree": "verb form", "participle": "verb form",
        "gerund": "verb form", "agreement_plural": "agreement", "be": "agreement", "me_and": "pronouns", "double_subject": "pronouns", "uncount": "nouns",
        "much": "nouns", "plural": "nouns", "explain_to": "word choice", "tell": "word choice",
        "about": "word choice", "home": "word choice", "comparative": "word choice", "capital": "capitals"}

LESSONS = {
    "tense": "When you talk about yesterday or last weekend, put every verb in the past: went, had, played.",
    "agreement": "With he, she, it or one person, the verb takes -s: she works, he has, my son likes.",
    "verb form": "Watch the verb after small words: can go (not goes), want to go, I agree, have gone.",
    "pronouns": "Say “my son and I went” — the other person first, and “I” when you're doing it.",
    "nouns": "Some nouns never take -s (information, advice); after a number, nouns do (two times).",
    "word choice": "Explain to someone, tell someone, discuss something (no “about”), go home (no “to”).",
    "capitals": "Days, months and languages are names: Monday, March, English.",
}


# ------------------------------------------------------------------------------------------------ helpers
def _core(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower().replace("’", "'"))


def _keep_case(old: str, new: str) -> str:
    """Put `new` in the place of `old`, keeping its capital letter and trailing punctuation."""
    m = re.match(r"^([^A-Za-z']*)([A-Za-z']+)([^A-Za-z']*)$", old)
    if not m:
        return new
    pre, word, post = m.groups()
    if word[:1].isupper():
        new = new[:1].upper() + new[1:]
    return pre + new + post


class _Fixer:
    def __init__(self, sentence: str):
        self.w = sentence.split()
        self.changes = []

    def note(self, before: str, after: str, key: str):
        self.changes.append({"from": before, "to": after, "rule": RULES_TEXT[key], "kind": KIND[key]})

    def set(self, i: int, new: str, key: str, before: str | None = None):
        old = self.w[i]
        if _core(old) == new.lower():
            return                                    # "read" → "read": nothing to teach
        self.w[i] = _keep_case(old, new)
        self.note(before or _core(old), new if before is None else before.replace(_core(old), new, 1), key)

    def text(self) -> str:
        return " ".join(w for w in self.w if w)

    def k(self, i: int) -> str:
        return _core(self.w[i]) if 0 <= i < len(self.w) else ""


ADVERBS = {"really", "also", "just", "always", "actually", "finally", "then", "usually", "often", "still", "even",
           "only", "all", "both", "quickly", "first"}


def _subject_before(f: _Fixer, i: int) -> str | None:
    """'he' / 'person' / 'plural' / 'i' for the subject right before word i (one adverb may sit between)."""
    if f.k(i - 1) in ADVERBS:
        i -= 1
    a, b = f.k(i - 1), f.k(i - 2)
    if a in PRONOUN_SUBJ:
        return "he" if a in {"he", "she", "it"} else a
    if a in PERSON and b in POSS:
        return "person"
    return None


# ------------------------------------------------------------------------------------------------ rules
def _tense(f: _Fixer, past_context: bool = False):
    sent = f.text()
    marked = bool(PAST_MARK.search(sent)) or (past_context and not PRESENT_RE.search(sent))
    participles = {pp for _, pp in IRREGULAR.values()} | {"been"}
    if marked:                                        # "I have seen him last week" → "I saw him last week"
        for i in range(len(f.w) - 1):
            if f.k(i) in {"have", "has"} and _subject_before(f, i) and f.k(i + 1) in participles:
                simple = next((p for v, (p, pp) in IRREGULAR.items() if pp == f.k(i + 1)), None)
                if simple:
                    f.note(f"{f.k(i)} {f.k(i + 1)}", simple, "present_perfect")
                    f.w[i + 1] = _keep_case(f.w[i + 1], simple)
                    f.w[i] = ""
                    f.w = [w for w in f.w if w]
                    break
    first_past = None
    for i in range(len(f.w)):
        k = f.k(i)
        if (k in PAST_FORMS or (k.endswith("ed") and len(k) > 4)) and _subject_before(f, i):
            first_past = i
            break
    if not marked and first_past is None:
        return
    start = 0 if marked else first_past + 1
    stop = next((i for i in range(start, len(f.w)) if f.k(i) in PRESENT_MARK or
                 (f.k(i) == "every" and f.k(i + 1) in {"day", "morning", "week", "night", "weekend", "year"})), len(f.w))
    for i in range(start, stop):
        k = f.k(i)
        if k in STANCE and f.k(i - 1) in {"i", "you", "we"}:
            continue                                  # "I think", "you know": a comment now, not a past event
        if k not in VERBS or f.k(i - 1) in MODALS or (k in {"have", "has"} and f.k(i + 1) in participles):
            continue
        subj = _subject_before(f, i)
        chained = f.k(i - 1) in {"and", "then"} and i + 1 < len(f.w) and f.k(i + 1) not in {"", "and"}
        if subj or chained:
            if subj == "he" and k == "be":
                continue
            f.set(i, past(k), "tense" if marked else "tense_chain")


def _agreement(f: _Fixer, past_context: bool = False):
    if PAST_MARK.search(f.text()) or (past_context and not PRESENT_RE.search(f.text())):
        return
    for i in range(len(f.w)):
        k = f.k(i)
        if k not in VERBS or f.k(i - 1) in MODALS:
            continue
        if _subject_before(f, i) in {"he", "person"}:
            if k == "be":
                continue
            f.set(i, third(k), "agreement_have" if k == "have" else "agreement_s")
    for i in range(len(f.w)):                        # "people saves", "I says", "they goes" → base verb
        k, prev = f.k(i), f.k(i - 1)
        if prev in {"i", "we", "you", "they", "people"} and f.k(i - 2) not in POSS:
            base = next((v for v in VERBS if third(v) == k and k != v), None)
            if base and k not in {"is", "has", "does"} | {"was"}:
                f.set(i, base, "agreement_plural")
    for i in range(len(f.w) - 1):
        if f.k(i) == "people" and f.k(i + 1) in {"is", "was"}:
            f.set(i + 1, "are" if f.k(i + 1) == "is" else "were", "people")
        if f.k(i) in {"everybody", "everyone", "nobody", "somebody", "someone"} and f.k(i + 1) in {"are", "were", "have"}:
            f.set(i + 1, {"are": "is", "were": "was", "have": "has"}[f.k(i + 1)], "everyone")


BE_FIX = {("i", "is"): "am", ("i", "are"): "am", ("they", "is"): "are", ("we", "is"): "are", ("you", "is"): "are",
          ("he", "are"): "is", ("she", "are"): "is", ("it", "are"): "is", ("they", "was"): "were",
          ("we", "was"): "were", ("you", "was"): "were", ("he", "were"): "was", ("she", "were"): "was",
          ("i", "were"): "was", ("it", "were"): "was"}


def _be(f: _Fixer, past_context: bool = False):
    for i in range(1, len(f.w)):
        key = (f.k(i - 1), f.k(i))
        if key in BE_FIX and not (key[1] == "were" and f.k(i - 2) in {"if", "wish"}):   # "if I were" is right
            new = BE_FIX[key]
            if past_context and new in {"are", "am", "is"}:
                new = "were" if new == "are" else "was"
            f.set(i, new, "be")


def _verb_forms(f: _Fixer):
    for i in range(1, len(f.w)):
        k, prev = f.k(i), f.k(i - 1)
        if prev in {"can", "could", "will", "would", "should", "must", "may", "might"}:
            base = next((v for v in VERBS if k in (third(v),) and k != v), None)
            if base and k not in {"is", "was"}:
                f.set(i, base, "modal")
            elif k == "to" and i + 1 < len(f.w) and f.k(i + 1) in VERBS:
                f.note(f"{prev} to {f.k(i + 1)}", f"{prev} {f.k(i + 1)}", "no_to")
                f.w[i] = ""
        if prev in {"want", "wants", "wanted", "need", "needs", "needed", "like", "likes", "liked", "try", "tried",
                    "decide", "decided", "plan", "planned", "hope", "hoped"} and k in VERBS - {"like", "love"} \
                and _subject_before(f, i - 1):
            f.note(f"{prev} {k}", f"{prev} to {k}", "to_inf")
            f.w[i] = "to " + f.w[i]
        if prev in {"let", "make", "made", "let's"} and k == "to":
            f.note(f"{prev} to", prev, "no_to")
            f.w[i] = ""
        if prev in {"enjoy", "enjoys", "enjoyed", "finish", "finished", "avoid", "avoided", "keep", "kept", "mind"} \
                and k == "to" and i + 1 < len(f.w) and f.k(i + 1) in VERBS:
            g = gerund(f.k(i + 1))
            f.note(f"{prev} to {f.k(i + 1)}", f"{prev} {g}", "gerund")
            f.w[i] = ""
            f.w[i + 1] = _keep_case(f.w[i + 1], g)
        if prev in {"have", "has", "had"} and k in PARTICIPLE:
            f.set(i, PARTICIPLE[k], "participle")
        if prev in {"am", "'m", "i'm", "is", "are"} and k == "agree":
            f.note(f"{prev} agree", "agree", "agree")
            f.w[i - 1] = ""
    f.w = [w for w in f.w if w]


def _pronouns(f: _Fixer):
    lead = {"yesterday", "today", "then", "so", "and", "weekend", "night", "week", "morning", "later", "afterwards"}
    p = next((i for i in range(len(f.w) - 2) if f.k(i) == "me" and f.k(i + 1) == "and" and
              (i == 0 or f.w[i - 1].endswith(",") or f.k(i - 1) in lead)), None)
    if p is not None:
        j = p + 3 if f.k(p + 2) not in POSS else p + 4
        obj = {"him": "he", "her": "she", "them": "they"}.get(f.k(p + 2))
        phrase = (" ".join(f.w[p + 2:j]) if not obj else obj).rstrip(",")
        new = (phrase[:1].upper() + phrase[1:] if p == 0 else phrase) + " and I"
        before = " ".join(_core(x) for x in f.w[p:j])
        f.w = f.w[:p] + new.split() + f.w[j:]
        f.note(before, new[:1].lower() + new[1:] if p == 0 else new, "me_and")
    for i in range(len(f.w) - 2):
        if f.k(i) in POSS and f.k(i + 1) in PERSON and f.k(i + 2) in {"he", "she", "they", "it"}:
            f.note(f"{f.k(i)} {f.k(i + 1)} {f.k(i + 2)}", f"{f.k(i)} {f.k(i + 1)}", "double_subject")
            f.w[i + 2] = ""
            break
    f.w = [w for w in f.w if w]


def _nouns(f: _Fixer):
    for i in range(len(f.w)):
        k = f.k(i)
        if k in UNCOUNT:
            f.set(i, UNCOUNT[k], "uncount")
            if f.k(i - 1) == "many":
                f.set(i - 1, "much", "much")
        prev = f.k(i - 1)
        nxt = f.k(i + 1)
        compound = nxt.isalpha() and nxt not in AFTER_NOUN and not re.search(r"[.,!?;:]$", f.w[i])
        if (prev in NUMBERS or re.fullmatch(r"\d+", prev or "")) and prev not in {"one", "1"} and k in COUNT_UNITS \
                and nxt != "old" and k != "people" and not compound:   # "a two-week trip" stays singular
            f.set(i, PLURAL_IRR.get(k, k + "s"), "plural")
        if prev == "many" and k in {"information", "advice", "furniture", "equipment", "homework", "luggage",
                                    "feedback", "money", "traffic", "time"} and k != "time":
            f.set(i - 1, "much", "much")


def _phrases(f: _Fixer):
    s = f.text()
    subs = [
        (r"\b(explain|explained|explains)\s+(me|him|her|us|them)\b", lambda m: f"{m.group(1)} to {m.group(2)}", "explain_to"),
        (r"\b(say|says|said)\s+(me|him|her|us|them)\b",
         lambda m: {"say": "tell", "says": "tells", "said": "told"}[m.group(1).lower()] + " " + m.group(2), "tell"),
        (r"\b(discuss|discussed|discusses|mention|mentioned|describe|described)\s+about\b", lambda m: m.group(1), "about"),
        (r"\b(go|goes|went|come|comes|came|get|got|going|coming)\s+to\s+home\b", lambda m: f"{m.group(1)} home", "home"),
        (r"\bmore\s+(" + "|".join(SHORT_ADJ) + r")\b", lambda m: SHORT_ADJ[m.group(1).lower()], "comparative"),
    ]
    for pattern, rep, key in subs:
        m = re.search(pattern, s, re.I)
        if m:
            new = rep(m)
            f.note(m.group(0).lower(), new, key)
            s = s[:m.start()] + new + s[m.end():]
    for d in DAYS:
        if re.search(rf"\b{d}\b", s):
            s = re.sub(rf"\b{d}\b", d.capitalize(), s)
            f.note(d, d.capitalize(), "capital")
    f.w = s.split()


def fix(sentence: str, past_context: bool = False) -> tuple[str, list[dict]]:
    """Correct one sentence. `past_context`: an earlier sentence set the story in the past ("Yesterday …"),
    so present-tense verbs here are tense slips too. Returns (corrected, changes)."""
    f = _Fixer(sentence)
    if not f.w:
        return sentence, []
    _pronouns(f)
    _verb_forms(f)
    _tense(f, past_context)
    _be(f, past_context or bool(PAST_MARK.search(f.text())))
    _agreement(f, past_context)
    _nouns(f)
    _phrases(f)
    out = f.text()
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out, f.changes


def sets_past(sentence: str, changes: list[dict]) -> bool | None:
    """Does this sentence set (True) or end (False) a past-time story? None = no signal."""
    if PRESENT_RE.search(sentence):
        return False
    if PAST_MARK.search(sentence) or any(c["kind"] == "tense" for c in changes):
        return True
    return None


def describe(change: dict) -> str:
    return f"“{change['from']}” → “{change['to']}”: {change['rule']}."


def patterns(all_changes: list[dict], examples: list[tuple[str, str]] | None = None, limit: int = 3) -> list[dict]:
    """The grammar points to practise, most frequent first, each with a lesson and the speaker's own example."""
    by_kind: dict = {}
    for c in all_changes:
        by_kind.setdefault(c["kind"], []).append(c)
    out = []
    for kind, cs in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        out.append({"kind": kind, "count": len(cs), "lesson": LESSONS.get(kind, ""),
                    "examples": [f"{c['from']} → {c['to']}" for c in cs[:3]]})
    return out[:limit]
