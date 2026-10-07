"""Thin provider layer: OpenAI (default when a key is set), Anthropic, or none (local fallback)."""
import json
import os
import re

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


def active_provider() -> str:
    """Return 'openai', 'anthropic' or 'local' based on LLM_PROVIDER and configured keys."""
    pref = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    has_openai = bool(OpenAI and os.getenv("OPENAI_API_KEY"))
    has_anthropic = bool(Anthropic and os.getenv("ANTHROPIC_API_KEY"))
    if pref == "local":
        return "local"
    if pref == "openai":
        return "openai" if has_openai else "local"
    if pref == "anthropic":
        return "anthropic" if has_anthropic else "local"
    if has_openai:
        return "openai"
    if has_anthropic:
        return "anthropic"
    return "local"


def model_name(provider: str) -> str:
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-5.6")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    return "heuristic"


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
    raise LLMUnavailable("No LLM provider configured")
