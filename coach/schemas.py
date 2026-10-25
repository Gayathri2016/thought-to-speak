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
