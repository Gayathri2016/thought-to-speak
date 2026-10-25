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
## Phase 4 — Maintainable boundaries ✅
Decisions
- Routers: `routes/{auth,practice,curriculum,progress}.py`; `routes/deps.py` holds the `Context` (store, curriculum,
  service, runner, folders) stored on `app.state.ctx`, `current_user`, `user_store`, `user_tz`, `user_now`, and a
  single patchable `CLOCK` seam for tests. `app.py` = composition root + lifespan (worker start/stop). Module-level
  `store/service/runner/AUDIO_DIR/TMP_DIR` kept on `app` for tests and tooling.
- Orchestration in `coach/service.py` (from Phase 3). Typed schemas in `coach/schemas.py`: requests, `JobOut`,
  `UsageOut`, and `AnalysisResultCore`/`Focus`/`Provenance` checked by `check_result()` before any save (a broken
  result fails the job and refunds quota).
- AI output: `coach/provider_schema.py` validates every field before merge. Section objects are validated field by
  field, list items one by one, scores individually (0–100), strings length-capped — one bad value drops only
  itself. Counts reported in `analysis.provider_validation`.
- Frontend: `static/js/` native ES modules, split mechanically with acorn by section + name overrides; exports and
  imports derived from identifier references. `core.js` has no imports and every module imports it first, so it's
  always initialised first (fixes TDZ in import cycles); `boot()` runs from `main.js` after all modules evaluate.
  No module assigns to an imported binding (checked).
- Timezone: progress practice days/streak take `now` in the user's zone; vocab-of-the-day and today's plan use the
  user's date; quota stays UTC (documented).

Verification: 79 passed, 1 skipped; browser smoke test over every tab (sign-in, learn, lesson, sprint, practice,
review, retry/compare, pronounce popup, progress, history, account menu, theme) with no console errors.
## Phase 5 — Adaptive learning ✅
Decisions
- `coach/adaptive.py` + `content/assessments.json` (3 baseline prompts, 20 held-out weekly prompts).
- Skills = the five scored dimensions. Schedule per user in `meta["skills"]` (box, due date, last score,
  reason). Leitner: intervals [1, 2, 4, 7, 14, 30]; ≥75 on/after due → +1 box; <60 any time → box 1; 60–74 → same box
  rescheduled; ≥75 early → unchanged. First evidence places the skill (box 1/2/3 by score). Dates in user's tz.
- `record_take()` runs after every saved, non-read-aloud take (in `AnalysisService.run`).
- Baseline: `kind="baseline"`, prompt must be a baseline prompt (validated); first take per item recorded in
  `meta["baseline"]`; summary = per-skill averages, strongest/weakest.
- Weekly: ISO week in the user's tz; assignment persisted in `meta["weekly:<week>"]`, chosen deterministically from
  prompts the user hasn't answered or been assigned (hash of user + week). `kind="weekly"` must answer this week's
  prompt (validated); the attempt stores `weekly_week`; history = first takes only (`attempt_no == 1`, first per
  week), flagged `comparable` by scoring key. Test guarantees weekly prompts appear nowhere else in the app.
- Today's plan: review slot = most overdue skill (lowest box, then lowest score) with the matching exercise;
  otherwise the recommendation, which now refreshes when the weakest skill changes during the day.
- UI: baseline card (until complete), Skill reviews card (box dots, due, reason), Weekly challenge card (history
  bars, faded when scored differently), "Retained skill (weekly)" KPI in Progress.

Verification: 87 passed, 1 skipped. Browser: baseline → practice → recorded (1/3), weekly challenge recorded,
skill cards with reasons, retained-skill KPI; mobile layout checked.

## Status & remaining limitations
- Commits on the Mac: phase 1 `d7431b3`, phase 2 `882e662`, phase 3 `de46a2f`, phases 4–5 in the commit that follows
  (v8.5 + v9).
- Not verified here: a live AI-provider run (`RUN_LIVE_EVAL=1 pytest -m live`) and real-microphone Whisper
  timestamps in a browser (covered by unit tests with synthetic word timings).
- Single-process design: the worker pool lives in the app process — run one uvicorn worker. Moving to
  Postgres/S3 or multiple processes needs a shared queue and quota store (the job/usage tables port directly).
- Processing is at-least-once: an AI call can repeat after a crash mid-call (bounded by `JOB_MAX_TRIES`).
- Daily quota resets at midnight UTC (plans, unlocks and streaks use the user's timezone).
- Skill reviews treat scores from different scorers as one 0–100 scale; progress averages and comparisons do not.
- The built-in scorer is keyword- and rule-based; the benchmark guards regressions, not absolute accuracy.
- The Pronounce tab's word-level checks are estimates, not phoneme-level pronunciation assessment.
