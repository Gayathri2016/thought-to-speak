"""Thin provider layer: OpenAI, Anthropic, Groq (free Llama models), or none (local fallback).

Groq speaks the OpenAI API, so it uses the same client with Groq's address. Free tiers have daily limits, so
every hosted call goes through a small daily budget: when it runs out (or the provider says "rate limited"),
callers get LLMUnavailable and the built-in coach takes over — practice never stops.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

try:
    from anthropic import Anthropic
except Exception:  # pragma: no cover
    Anthropic = None


class LLMUnavailable(Exception):
    pass


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "openai/gpt-oss-120b"   # open-weight; Groq retired its Llama models in 2026 (see docs/deprecations)


class Budget:
    """Calls per UTC day, kept in memory (a restart starts the count again — fine for a soft cap).
    After the provider rate-limits us, pause for a minute instead of failing every take slowly."""

    def __init__(self, env: str, default: int):
        self.env, self.default = env, default
        self._lock = threading.Lock()
        self._day, self._used, self._pause_until = None, 0, 0.0

    def limit(self) -> int:
        try:
            return max(0, int(os.getenv(self.env, str(self.default))))
        except ValueError:
            return self.default

    def take(self) -> bool:
        """Reserve one call. False when today's budget is used up or we're cooling down."""
        with self._lock:
            today = datetime.now(timezone.utc).date()
            if today != self._day:
                self._day, self._used = today, 0
            if time.monotonic() < self._pause_until:
                return False
            if self.limit() and self._used >= self.limit():
                return False
            self._used += 1
            return True

    def rate_limited(self, seconds: float = 60):
        with self._lock:
            self._pause_until = time.monotonic() + seconds

    def status(self) -> dict:
        with self._lock:
            return {"used": self._used if self._day == datetime.now(timezone.utc).date() else 0, "limit": self.limit()}


chat_budget = Budget("AI_DAILY_TAKES", 300)


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or type(e).__name__ == "RateLimitError"


def active_provider() -> str:
    """Return 'openai', 'anthropic' or 'local' based on LLM_PROVIDER and configured keys."""
    pref = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    has_openai = bool(OpenAI and os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(Anthropic and os.getenv("ANTHROPIC_API_KEY"))
    has_groq = bool(OpenAI and os.getenv("GROQ_API_KEY"))
    if pref == "local":
        return "local"
    if pref == "openai":
        return "openai" if has_openai else "local"
    if pref == "anthropic":
        return "anthropic" if has_anthropic else "local"
    if pref == "groq":
        return "groq" if has_groq else "local"
    if has_openai:
        return "openai"
    if has_anthropic:
        return "anthropic"
    if has_groq:
        return "groq"
    return "local"


def model_name(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-5.6")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    if provider == "groq":
        return os.getenv("GROQ_MODEL", GROQ_MODEL)
    return "heuristic"


def groq_client(timeout: float):
    return OpenAI(api_key=os.getenv("GROQ_API_KEY"), base_url=os.getenv("GROQ_BASE_URL", GROQ_BASE_URL),
                  timeout=timeout, max_retries=0)


def _extract_json(raw: str) -> dict:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(raw[start:end + 1])


def complete_json(system: str, user: str, max_tokens: int = 2000) -> tuple[dict, str]:
    """Call the active provider and parse a JSON object. Raises on any failure."""
    provider = active_provider()
    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    if provider == "openai":
        client = OpenAI(timeout=timeout)
        kwargs = dict(model=model_name("openai"), instructions=system, input=user)
        try:
            r = client.responses.create(**kwargs, text={"format": {"type": "json_object"}})
        except TypeError:
            r = client.responses.create(**kwargs)
        return _extract_json(r.output_text), f"openai:{model_name('openai')}"
    if provider == "anthropic":
        client = Anthropic(timeout=timeout)
        r = client.messages.create(model=model_name("anthropic"), max_tokens=max_tokens, system=system,
                                   messages=[{"role": "user", "content": user}])
        raw = "".join(getattr(b, "text", "") for b in r.content)
        return _extract_json(raw), f"anthropic:{model_name('anthropic')}"
    if provider == "groq":
        if not chat_budget.take():
            raise LLMUnavailable("Today's free AI coaching is used up — the built-in coach is covering.")
        try:
            extra = {"reasoning_effort": "low"} if "gpt-oss" in model_name("groq") else {}   # fewer hidden tokens
            r = groq_client(timeout).chat.completions.create(
                model=model_name("groq"), max_tokens=min(max_tokens, 4000), temperature=0.3,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **extra)
        except Exception as e:
            if _is_rate_limit(e):
                chat_budget.rate_limited()
            raise
        return _extract_json(r.choices[0].message.content), f"groq:{model_name('groq')}"
    raise LLMUnavailable("No LLM provider configured")
