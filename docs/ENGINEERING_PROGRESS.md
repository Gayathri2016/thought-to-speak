# Engineering progress — architecture upgrade (v8.2 → v9)

A running log so work can resume without losing decisions. Newest phase last.

## Baseline (v8.1)
- FastAPI modular monolith: `app.py` (all routes) + `coach/*` domain modules; vanilla JS SPA (`static/`), no build.
- SQLite (`attempts` with an `analysis_json` blob, `users`, `sessions`, `meta`), WAV files in `data/audio`.
- Analysis ran synchronously inside the request; no scoring versioning; quota counted only saved takes.

## Phase 1 — Trustworthy scoring ✅
**What:** every take records how it was scored; comparisons and progress only mix compatible scores.

Decisions
- New module `coach/scoring.py` owns provenance + compatibility rules.
- Columns added to `attempts` (additive migration, `meta.schema_version = 2`): `score_basis`
  (`ai` | `heuristic` | `mixed`), `scoring_version` (`<basis>-<engine>`), `model`, `rubric_version`
  (10-char SHA-1 of framework + rubric + target length), `transcript_source`.
- Engine versions: `HEURISTIC_VERSION = "h8"`, `AI_PROMPT_VERSION = "p8"`, `PRONUNCIATION_VERSION = "r1"`.
  **Bump them whenever scoring logic / the AI scoring prompt changes**, then `python scripts/eval_scoring.py --update-baseline`.
- Pre-provenance takes are backfilled as `<basis>-legacy` (basis/model recovered from `provider`, transcript source
  from the stored voice report). `analysis_json` is never rewritten. Migration is idempotent.
- Compatibility: scores comparable only if scoring_version + model + rubric_version match; measured counts
  (fillers/words) only if transcript class matches (`whisper` keeps "um/uh", `text` = browser STT or typed).
- Retry comparison with incompatible scores → verdict `incompatible`, score deltas `null`, plain-language reason,
  no AI narrative; the take-height sparkline is hidden.
- Progress: averages/best/by-mode/recommendation use only takes with the latest take's scoring key; others are
  returned in `trend` with `comparable: false` (faded in the chart) and explained in `scoring.note`.
  Retry gains count only chains whose first/last takes are compatible; filler stats use the same transcript class.
- UI: each score has an `AI` / `est.` / `formula` badge; metrics estimated from word count are marked `est`;
  a provenance line names the scorer, transcript source and rubric.
- Benchmark: `tests/benchmark/cases.json` (13 human-ranked cases, 6 prompt groups, all 5 modes) +
  `baseline_local.json`; `scripts/eval_scoring.py` checks ordering, framework parts, verdict bands and drift (±3).
  Deterministic run is a pytest test; `--live` evaluates the configured AI provider (pytest marker `live`,
  skipped unless `RUN_LIVE_EVAL=1`).
- The benchmark immediately caught a gap ("In my last role…" not recognised as Situation) — fixed.

Verification: 54 passed, 1 skipped (live). UI checked at 1280px and 390px (badges, provenance line,
incompatible comparison, faded trend + note).

## Phase 2 — Focused, actionable feedback ✅
**What:** the results open on three things — Keep doing / Fix first / On your retry — with everything else in
expandable sections (Full review, Your voice, Words & sentences, Measurements; open state remembered per browser).

Decisions
- `coach/evidence.py`: every quote in the review (landed, doubts, fixes, breakdown parts, corrections) is located in
  the transcript — exact, truncated prefix ("…"), or a ≥0.88 character-similar window — and replaced with the
  speaker's verbatim words. Near-matches are rejected if they change a pronoun (I/we → ownership) or add/remove a
  negation (agreed/disagreed, not). Unverifiable quotes are dropped (praise items removed entirely; critique items
  keep the point without a quote; corrections removed) and counted in `review.evidence.dropped`.
- Timestamps only from Whisper word timings, and only when the quote's tokens appear in those timings in order;
  otherwise `start/end = null`. Browser transcripts never get timestamps. ▶ chips seek the recording.
- `focus` = first verified "landed" item (else strength) · first coach fix's problem (else first quoted doubt) ·
  that fix as the retry instruction + target length. Read-aloud drills get a focus from pronunciation feedback.
- Challenge Mode: quoted wording not found in the transcript (i.e. suggested replacements) is redacted to
  "[your own wording]" in fixes, game plan, verdict tips and correction notes; corrected sentences, the corrected
  answer and the stronger version are always null.
- Pronunciation copy reworded: "words recognised", "likely sound swap", explicitly word-level not phoneme-level.

Verification: 60 passed, 1 skipped. UI checked at 1280px and 390px (focus card, ▶ chips, collapsed sections).
## Phase 3 — Reliable analysis & usage accounting ✅
**What:** submissions are persisted as jobs before processing; quota is reserved atomically; duplicates are
prevented; interrupted work recovers on restart; audio has an explicit temp → final lifecycle.

Decisions
- Worker design: in-process thread pool (`ANALYSIS_WORKERS`, default 2) + jobs in SQLite (`coach/jobs.py`).
  Smallest reliable option for one-process deployments on SQLite; no Redis/Celery. Requires a single uvicorn
  process. SQLite in WAL mode with a 30 s busy timeout.
- Schema 3 (additive): `jobs` (status CHECK, request JSON + hash, temp audio name, result JSON, error code, tries,
  quota day/state), `usage` (user, UTC day, reserved), `comparisons` (cached narratives). Unique
  `(user_id, idempotency_key)`.
- Atomicity: `BEGIN IMMEDIATE` around idempotency lookup + quota check + usage increment + job insert.
  Claim = `UPDATE … WHERE status='queued'`; refund happens in the same transaction that marks a job failed.
- Policies: validation errors → 4xx before any job (no quota); processing input errors → failed + refunded, not
  retried; transient SQLite errors → retried up to `JOB_MAX_TRIES`; unexpected errors → failed + refunded;
  restart recovery re-queues `running` jobs (tries < max) else fails + refunds. AI/Whisper timeouts fall back to
  local analysis inside the job. At-least-once processing: a provider call can repeat after a crash.
- `coach/service.py` now owns orchestration (moved out of `app.py`); `coach/schemas.py` holds request models.
- Audio: bounded chunked upload read (413 past `MAX_AUDIO_BYTES`), decoded duration 1 s–`MAX_AUDIO_SECONDS`;
  written to `data/tmp/<job>.wav` atomically (`.part` + rename); `os.replace` into `data/audio/<uuid>.wav` when
  saved; deleted for save=false and failures; sweep deletes unreferenced temp files older than
  `TMP_RETENTION_SECONDS`. Saved recordings are never auto-deleted (no destructive retention by default).
- Legacy `/api/analyze` + `/api/analyze-audio` = submit + wait (`JOB_WAIT_SECONDS`), returning 202 + job on timeout.
  Frontend uses `/api/jobs*`, one idempotency key per take (new key after an edit → 409), polls with backoff, and
  resumes a pending job from localStorage or `GET /api/jobs?active=true` after reload.
- Comparison narratives cached in `comparisons`; AI fallback narratives are not cached.

Verification: 73 passed, 1 skipped (×5 runs, no flakes). Browser: one POST per take with key; reload recovered
a queued job and rendered it.
## Phase 4 — Maintainable boundaries ⏳
## Phase 5 — Adaptive learning ⏳
