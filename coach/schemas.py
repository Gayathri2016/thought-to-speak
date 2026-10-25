"""Typed request schemas shared by the HTTP routes, the analysis service and the job worker."""
from pydantic import BaseModel, Field

MAX_TRANSCRIPT_CHARS = 8000


class AnalyzeRequest(BaseModel):
    transcript: str = Field("", max_length=MAX_TRANSCRIPT_CHARS)
    mode: str = Field("Interview", max_length=40)
    prompt: str = Field("", max_length=1000)
    challenge_mode: bool = False
    duration_seconds: float | None = Field(None, ge=0, le=3600)
    chain_id: str | None = Field(None, max_length=64)   # set when this is a retry of an earlier attempt
    save: bool = True
    kind: str = Field("free", pattern="^(free|lesson|warmup|review|read_aloud|baseline|weekly)$")
    lesson_id: str | None = Field(None, max_length=64)
    target_text: str | None = Field(None, max_length=600)   # read-aloud drills: the sentence to read
    idempotency_key: str | None = Field(None, min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)
    name: str | None = Field(None, max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=256)


class DeleteAccountRequest(BaseModel):
    password: str = Field("", max_length=256)


# ----------------------------------------------------------------------------- responses
class JobOut(BaseModel):
    """An analysis job as clients see it. `result` holds the full analysis once completed."""
    id: str
    status: str = Field(pattern="^(queued|running|completed|failed)$")
    created_at: str
    updated_at: str
    finished_at: str | None = None
    error: str | None = None
    error_code: str | None = None
    attempt_id: str | None = None
    tries: int | None = None
    quota: str | None = None
    result: dict | None = None


class UsageOut(BaseModel):
    used: int
    limit: int | None
    remaining: int | None
    resets: str


# ----------------------------------------------------------------------------- analysis result invariants
class Evidence(BaseModel):
    text: str
    quote: str | None = None
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    why: str | None = None


class Focus(BaseModel):
    strength: Evidence
    priority: Evidence
    retry: str = Field(min_length=1)


class Provenance(BaseModel):
    model_config = {"extra": "allow"}
    score_basis: str = Field(pattern="^(ai|heuristic|mixed)$")
    scoring_version: str
    model: str | None = None
    rubric_version: str | None = None
    transcript_source: str


class AnalysisResultCore(BaseModel):
    """The fields every saved analysis must have. Everything else in the result is kept as-is (extra=allow)."""
    model_config = {"extra": "allow"}
    overall_score: int = Field(ge=0, le=100)
    scores: dict[str, int]
    provenance: Provenance
    focus: Focus
    mode: str


def check_result(result: dict) -> None:
    """Raise ValueError if an analysis result breaks its invariants (it is then not saved; the job fails)."""
    AnalysisResultCore.model_validate(result)
    for d, v in result["scores"].items():
        if not 0 <= v <= 100:
            raise ValueError(f"score {d}={v} out of range")
