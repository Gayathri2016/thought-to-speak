"""Typed validation of what an LLM returns, *before* any of it is merged into a result.

Models are lenient about missing fields and strict about types, ranges and sizes. Validation is field by field:
an invalid field (or list item) is dropped and reported, and the rest of the response is still used — one bad
value never discards a whole review, and a malformed one never reaches the user.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

S = 600        # max length for short coaching strings
L = 4000       # max length for long text (stronger version, corrected answer)


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Scores(_M):
    structure: int | None = Field(None, ge=0, le=100)
    clarity: int | None = Field(None, ge=0, le=100)
    relevance: int | None = Field(None, ge=0, le=100)
    answer_quality: int | None = Field(None, ge=0, le=100)


class MapItem(_M):
    label: str = Field(max_length=60)
    status: Literal["present", "weak", "missing"] = "weak"
    note: str = Field("", max_length=S)


class Quote(_M):
    quote: str | None = Field(None, max_length=S)
    why: str = Field(max_length=S)


class Evaluator(_M):
    verdict: Literal["strong_yes", "lean_yes", "lean_no", "no"] | None = None
    summary: str | None = Field(None, max_length=S)
    landed: list[Quote] = []
    doubts: list[Quote] = []
    follow_up: str | None = Field(None, max_length=S)
    to_strong_yes: list[str] = []


class Fix(_M):
    quote: str | None = Field(None, max_length=S)
    problem: str = Field(max_length=S)
    fix: str = Field(max_length=S)


class Drill(_M):
    title: str = Field(max_length=120)
    steps: list[str] = Field(min_length=1, max_length=6)


class ScriptPart(_M):
    label: str = Field(max_length=60)
    text: str = Field(max_length=L)
    yours: bool = True


class Coach(_M):
    fixes: list[Fix] = []
    do_next: list[str] = []
    drill: Drill | None = None
    stronger_version: str | None = Field(None, max_length=L)
    script: list[ScriptPart] = []


class CorrectionItem(_M):
    original: str = Field(max_length=L)
    corrected: str | None = Field(None, max_length=L)
    changes: list[str] = []
    notes: list[str] = []


class Corrections(_M):
    items: list[CorrectionItem] = []
    corrected_full: str | None = Field(None, max_length=L * 2)


class BreakdownItem(_M):
    label: str = Field(max_length=60)
    status: Literal["present", "weak", "missing"] = "weak"
    quote: str | None = Field(None, max_length=S)
    note: str | None = Field(None, max_length=S)


class Mentor(_M):
    pattern: str | None = Field(None, max_length=S)
    improving: str | None = Field(None, max_length=S)
    habit: str | None = Field(None, max_length=S)


class WordUpgrade(_M):
    original: str = Field(max_length=120)
    better: str = Field("", max_length=120)
    why: str = Field("", max_length=S)


class GrammarNote(_M):
    original: str = Field(max_length=300)
    corrected: str = Field("", max_length=300)
    issue: str = Field("", max_length=S)


class Language(_M):
    word_upgrades: list[WordUpgrade] = []
    grammar: list[GrammarNote] = []
    sentence_tip: str | None = Field(None, max_length=S)


def _one(model, value, path, problems):
    try:
        return model.model_validate(value).model_dump(exclude_none=True)
    except ValidationError as e:
        problems.append(f"{path}: {e.errors()[0]['msg']}")
        return None


def _many(model, value, path, problems, limit):
    if value is None:
        return None
    if not isinstance(value, list):
        problems.append(f"{path}: expected a list")
        return None
    out = []
    for i, v in enumerate(value[:limit * 2]):
        if model is str:
            if isinstance(v, str) and v.strip() and len(v) <= S:
                out.append(v.strip())
            else:
                problems.append(f"{path}[{i}]: not a short string")
        else:
            item = _one(model, v, f"{path}[{i}]", problems)
            if item is not None:
                out.append(item)
    return out[:limit]


def _lenient(model, value, path, problems) -> dict | None:
    """Validate an all-optional section model field by field (list fields item by item), so one bad value only
    drops itself."""
    import typing
    if not isinstance(value, dict):
        problems.append(f"{path}: expected an object")
        return None
    out = {}
    for name, field in model.model_fields.items():
        if name not in value or value[name] is None:
            continue
        v, ann = value[name], field.annotation
        if typing.get_origin(ann) is list:
            item = typing.get_args(ann)[0]
            items = _many(item if item is not str else str, v, f"{path}.{name}", problems, 10)
            if items:
                out[name] = items
            continue
        try:
            got = model.model_validate({name: v}).model_dump(exclude_none=True).get(name)
            if got is not None:
                out[name] = got
        except ValidationError as e:
            problems.append(f"{path}.{name}: {e.errors()[0]['msg']}")
    return out


def _section(spec: dict, value, path, problems) -> dict | None:
    """Validate a dict whose keys each have their own validator; invalid keys are dropped individually."""
    if value is None:
        return None
    if not isinstance(value, dict):
        problems.append(f"{path}: expected an object")
        return None
    out = {}
    for key, check in spec.items():
        if key in value and value[key] is not None:
            v = check(value[key], f"{path}.{key}")
            if v is not None:
                out[key] = v
    return out


def validate_feedback(data) -> tuple[dict, list[str]]:
    """Clean a raw LLM feedback object. Returns (clean_data, problems)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return {}, ["response: not a JSON object"]
    text = lambda n=S: (lambda v, p: v.strip() if isinstance(v, str) and v.strip() and len(v) <= n  # noqa: E731
                        else problems.append(f"{p}: invalid text") or None)
    strs = lambda limit: (lambda v, p: _many(str, v, p, problems, limit))  # noqa: E731
    lenient = lambda m: (lambda v, p: _lenient(m, v, p, problems))  # noqa: E731
    many = lambda m, limit: (lambda v, p: _many(m, v, p, problems, limit))  # noqa: E731

    def scores(v, p):                                # each score stands alone: one bad value drops only itself
        if not isinstance(v, dict):
            problems.append(f"{p}: expected an object")
            return None
        out = {}
        for d in Scores.model_fields:
            if d in v:
                x = v[d]
                if isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 100:
                    out[d] = int(round(x))
                else:
                    problems.append(f"{p}.{d}: must be a number 0-100")
        return out

    clean = _section({
        "scores": scores,
        "structure_map": many(MapItem, 8),
        "strengths": strs(4), "improvements": strs(5), "coaching_questions": strs(4), "suggested_outline": strs(6),
        "coaching_hint": text(), "next_challenge": text(),
        "language": lenient(Language),
        "review": lambda v, p: _section({
            "evaluator": lenient(Evaluator),
            "breakdown": many(BreakdownItem, 8),
            "order_note": text(),
            "coach": lenient(Coach),
            "corrections": lenient(Corrections),
            "mentor": lenient(Mentor),
        }, v, p, problems),
    }, data, "response", problems) or {}
    # nested lists inside validated sections are capped too
    rv = clean.get("review") or {}
    for key, limit in (("landed", 3), ("doubts", 3), ("to_strong_yes", 2)):
        if key in (rv.get("evaluator") or {}):
            rv["evaluator"][key] = rv["evaluator"][key][:limit]
    for key, limit in (("fixes", 3), ("do_next", 3), ("script", 6)):
        if key in (rv.get("coach") or {}):
            rv["coach"][key] = rv["coach"][key][:limit]
    return clean, problems
