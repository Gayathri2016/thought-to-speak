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

## Phase 2 — Focused, actionable feedback ⏳
## Phase 3 — Reliable analysis & usage accounting ⏳
## Phase 4 — Maintainable boundaries ⏳
## Phase 5 — Adaptive learning ⏳
