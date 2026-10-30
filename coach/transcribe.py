"""Verbatim speech-to-text with word timestamps.

Order (TRANSCRIBE_PROVIDER=auto): OpenAI Whisper API if OPENAI_API_KEY is set → Groq's free Whisper if
GROQ_API_KEY is set → local faster-whisper if installed → none (the browser's live transcript is used instead).
Hosted engines have a daily budget (TRANSCRIBE_DAILY); past it, the browser transcript is used.

Whisper normally "cleans up" disfluencies. The initial prompt below is written in a disfluent style,
which biases it to keep um / uh / like — exactly what a speaking coach needs to count.
For read-aloud drills the target sentence is deliberately NOT passed as a prompt: biasing the
recognizer toward the expected words would hide the very mispronunciations we want to catch.
Local faster-whisper also returns per-word confidence, used to flag unclear words.
"""
import logging
import os
import threading

log = logging.getLogger("thought_to_speak")

DISFLUENT_PROMPT = ("Umm, so, uh, let me think, like, hmm... Okay, so I mean, you know, I was, uh, "
                    "I was kind of thinking that, um, we should, like, start.")

_local_model = None


def _stt_budget():
    from coach.llm import Budget
    return Budget("TRANSCRIBE_DAILY", 500)


stt_budget = _stt_budget()
_local_lock = threading.Lock()


def available() -> str:
    pref = os.getenv("TRANSCRIBE_PROVIDER", "auto").lower()
    if pref == "none":
        return "none"
    if pref in ("auto", "openai") and os.getenv("OPENAI_API_KEY"):
        try:
            import openai  # noqa: F401
            return "openai"
        except ImportError:
            pass
    if pref in ("auto", "groq") and os.getenv("GROQ_API_KEY"):
        try:
            import openai  # noqa: F401
            return "groq"
        except ImportError:
            pass
    if pref in ("auto", "local"):
        try:
            import faster_whisper  # noqa: F401
            return "local"
        except ImportError:
            pass
    return "none"


def transcribe(wav_path: str) -> dict | None:
    """Return {text, words:[{word,start,end}], source} or None if no engine is available/works."""
    engine = available()
    try:
        if engine == "openai":
            return _openai(wav_path)
        if engine == "groq":
            return _groq(wav_path)
        if engine == "local":
            return _local(wav_path)
    except Exception as e:  # never break the practice loop
        log.warning("Transcription (%s) failed: %s", engine, e)
        return {"error": f"{engine} transcription failed: {type(e).__name__}"}
    return None


def _openai(path):
    from openai import OpenAI
    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    client = OpenAI(timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))
    with open(path, "rb") as f:
        r = client.audio.transcriptions.create(model=model, file=f, language="en", prompt=DISFLUENT_PROMPT,
                                               response_format="verbose_json", timestamp_granularities=["word"])
    words = [{"word": w.word, "start": float(w.start), "end": float(w.end)} for w in (getattr(r, "words", None) or [])]
    return {"text": r.text.strip(), "words": words, "source": f"openai:{model}"}


def _groq(path):
    from coach import llm
    if not stt_budget.take():
        return {"error": "today's free transcription is used up"}
    model = os.getenv("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3")
    client = llm.groq_client(float(os.getenv("LLM_TIMEOUT_SECONDS", "60")))
    try:
        with open(path, "rb") as f:
            r = client.audio.transcriptions.create(model=model, file=f, language="en", prompt=DISFLUENT_PROMPT,
                                                   response_format="verbose_json",
                                                   timestamp_granularities=["word", "segment"])
    except Exception as e:
        if llm._is_rate_limit(e):
            stt_budget.rate_limited()
        raise
    get = lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None)  # noqa: E731
    words = [{"word": get(w, "word"), "start": float(get(w, "start")), "end": float(get(w, "end"))}
             for w in (get(r, "words") or [])]
    return {"text": (get(r, "text") or "").strip(), "words": words, "source": f"groq:{model}"}


def _local(path):
    global _local_model
    from faster_whisper import WhisperModel
    name = os.getenv("WHISPER_LOCAL_MODEL", "base.en")
    with _local_lock:
        if _local_model is None:
            log.info("Loading local Whisper model %s (first run downloads it)…", name)
            _local_model = WhisperModel(name, device="cpu", compute_type="int8")
        segments, _ = _local_model.transcribe(path, language="en", word_timestamps=True,
                                              initial_prompt=DISFLUENT_PROMPT, vad_filter=False,
                                              condition_on_previous_text=False)
        words, texts = [], []
        for seg in segments:
            texts.append(seg.text)
            for w in seg.words or []:
                words.append({"word": w.word, "start": float(w.start), "end": float(w.end),
                              "probability": round(float(w.probability), 3)})
    return {"text": "".join(texts).strip(), "words": words, "source": f"local:faster-whisper {name}"}
