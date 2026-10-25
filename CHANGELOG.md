# Changelog

## v9.2 — deletion races
- A retry job finishing after you delete an earlier take no longer writes its comparison against that take back:
  completion re-checks both comparison fields inside its transaction. Comparison-cache writes only land while both
  takes exist (single atomic statement).
- Skill-review updates run in one write transaction that first checks the take still exists, so a take deleted
  while its job was finishing never counts again and its baseline slot isn't restored. The same transaction stops
  two jobs finishing at once from overwriting each other's update.
- Deleting a take now rebuilds skills and baseline inside the deletion's own transaction.

## v9.1 — reliability and deletion fixes
- Crash-safe job finalization: each saved take records the job that created it (`attempts.job_id`, unique), so a
  job re-run after a crash or a failed completion reuses its saved take instead of saving (and charging) a second
  one; the saved take is never re-analyzed. Recordings move to `data/audio/<job id>.wav`, recoverable from either
  folder. Skill-schedule updates are applied once per take. A job whose take was saved is completed, never
  failed + refunded.
- Deleting a take also removes its content from the job that created it (request and result), from retry
  comparisons stored in other jobs and from cached narratives; quota stays as used; replaying the key returns the
  deleted job (sync endpoints: 410) and never re-creates the take. Skills and baseline are recomputed without it.
- Retrying after a failed analysis starts a new job (the idempotency key is cleared on any terminal outcome and
  kept only when the outcome is unknown). Key rules live in `static/js/takes.js`, with Node unit tests.
- Schema 4 migration (additive): `attempts.job_id`, back-filled from completed jobs; nothing else changes.

## v9 — adaptive learning
- 3-prompt baseline assessment for new users, with a starting-point summary.
- Per-skill spaced reviews (Leitner boxes: 1, 2, 4, 7, 14, 30 days) with a plain-language reason for each skill;
  today's session reviews the most overdue skill.
- Unseen weekly challenge from a held-out prompt bank; only the first take counts, tracked as retained skill.
- Today's review recommendation refreshes when new takes change the weakest skill.

## v8.5 — maintainable boundaries
- Routes split into `routes/auth|practice|curriculum|progress` with a shared app context; `app.py` is now only
  the composition root (FastAPI lifespan starts/stops the worker).
- Typed schemas for requests, job/usage responses and analysis-result invariants (checked before saving).
- AI output is validated field by field against typed models before merging; invalid fields are dropped and counted.
- Frontend split into native ES modules (`static/js/`: core, api, recording, results, curriculum, progress, auth,
  main) — no build step.
- The user's timezone is used consistently for daily plans, program unlocks, practice days and streaks.

## v8.4 — reliable analysis and usage accounting
- Takes are processed as persistent jobs (queued → running → completed/failed) by an in-process worker pool;
  the page recovers a pending analysis after a refresh. Jobs interrupted by a restart are re-queued.
- Per-take idempotency keys: no duplicate takes or AI calls from double clicks, retries or refreshes.
- Daily quota is reserved atomically before processing (including unsaved analyses); failed jobs are refunded.
- Uploads are read with a hard size limit; the decoded recording's duration is validated (1 s – 10 min).
- Audio goes to a temporary folder and is moved into place only when the take is saved; stale temp files are swept.
- Comparison narratives are cached per pair of takes.

## v8.3 — focused, evidenced feedback
- Results open on one strength, one priority fix and one retry instruction; the full review, voice, language and
  measurements are in expandable sections.
- Every quote is checked against your transcript (verbatim words only; meaning-changing near-matches rejected);
  ▶ timestamps appear only when Whisper measured them.
- Challenge Mode redacts any suggested replacement wording.
- Pronunciation results are labelled as word-level estimates, not phoneme-level measurements.

## v8.2 — trustworthy scoring
- Each take stores its scoring version, score basis (AI / built-in / mixed), model, rubric fingerprint and
  transcript source. Older takes are migrated in place as "legacy" without changing their stored analysis.
- Retry comparisons and progress only compare compatible scores and explain why when they can't.
- Results label each score as AI, estimate or formula, and mark estimated time/pace.
- Scoring benchmark (`tests/benchmark`) and `scripts/eval_scoring.py`, with an optional live-provider run.

## v8.1 — corrections
- New **Corrections** section in every review: each sentence that needs work as "You said → Say", with what
  changed and why, plus your whole answer corrected (with ▶ Listen).
- Works without an API key: removes fillers, false starts and self-repairs ("I am ready for, we are ready
  for lunch"), repeated words, splits "and … and …" run-ons, fixes common grammar and article slips, and flags
  vague phrases. With an API key the AI corrects word choice and grammar more fully.
- Challenge Mode shows what's wrong but not the corrected words.

## v8 — feedback from three angles
- Every take now gets a review from the people who matter:
  - **Evaluator** — the interviewer, audience, leadership team or customer (depending on the mode) gives a
    verdict (e.g. Strong yes / Lean yes / Lean no / Not yet), quotes what landed and what raised a doubt, asks
    the follow-up question they'd ask next (one click to practise answering it), and says what would earn the
    top verdict.
  - **Your answer, part by part** — each framework part with the exact words that did it, or "not in your
    answer", plus a note when parts came out of order.
  - **Coach** — top three fixes quoting your words, a game plan for the next take, a 2-minute drill and,
    with an API key, a stronger version of *your* answer (never in Challenge Mode).
  - **Mentor** — the pattern across your recent takes, what's genuinely improving, and one habit for the week.
- Works offline with the built-in coach; with an OpenAI or Anthropic key the AI writes the review, using a
  summary of your recent history. Malformed AI output falls back to the built-in review, field by field.
- Default Anthropic model updated to `claude-sonnet-5-5`.

## v7.1 — The 28-Day Speaking Sprint
- The daily program has its own identity: renamed to *The 28-Day Speaking Sprint*, with new day titles, a new
  order and week themes (Foundations → Voice & presence → Conversations → Influence), new milestones, and colours
  that follow the app's theme in light and dark mode.

## v7 — a 28-day daily program
- New daily program on the Learn tab: 28 one-skill-a-day lessons in four weeks — introductions, meeting
  contributions, explaining complex ideas, persuasion, hooks, tough questions, feedback, listening, email,
  networking, vocal presence, posture, leading discussions, negotiation, storytelling, impromptu, interviews,
  conflict, trade-offs, exec updates, saying no, pitching, disagreeing, owning mistakes, teaching, 30-second
  answers, recognition, and a final talk.
- One day unlocks per calendar day (in your own timezone) after you pass the previous one; locked days can be read
  but not practised. Streaks, milestones at 1, 2, 3 and 4 weeks, restart from Day 1.
- While enrolled, Today's session uses the program's day as its lesson.
- The browser sends its timezone (`X-Timezone`), so "today" follows the user's calendar, not the server's.
- Better local scoring: framework cues now recognise the phrases the lessons teach, and "includes a number"
  accepts number words ("three hours"). Every program day is tested: its model answer passes, its "before" fails.

## v6 — accounts
- Sign up / sign in with email and password; sessions in an HttpOnly, SameSite=Lax cookie (30 days).
- Every take, recording, lesson result, daily plan and export is private to its account — all queries are
  filtered by user, and other users' takes return 404.
- Passwords hashed with scrypt; only a SHA-256 hash of each session token is stored.
- Rate limits: 10 failed sign-ins per email or IP per 15 min, 5 sign-ups per IP per hour, and a daily take
  quota per user (`DAILY_TAKE_LIMIT`, default 60) to cap API spend.
- Delete account (password-confirmed) removes the account, all takes and audio files.
- Practice recorded before v6 is moved into the first account that signs up.
- `AUTH_MODE=single` keeps the old no-sign-in behaviour for personal use.

## v5 — pronunciation, vocabulary and sentence structure
- New **Pronounce** tab: 7 read-aloud drill sets with minimal pairs, stress guide and text-to-speech.
- Read-aloud scoring: word-by-word alignment (clear / unclear / swapped / missed), sound-swap detection via the
  CMU Pronouncing Dictionary, per-sound accuracy over time, trouble words.
- Stress & respelling for any word (bundled CMU dictionary subset + technical-word lexicon).
- Vocabulary analysis: variety, precise-word share (wordfreq), weak/vague wording with alternatives, repeats.
- Sentence analysis: lengths (punctuation or pauses), run-ons, repeated openers, passive voice, grammar checks.
- AI (when configured) adds word upgrades, grammar notes and a sentence tip; hidden fixes in Challenge Mode.
- Word bank: 3 words of the day, usage tracked automatically.
- New **Speak Clearly** journey (9 lessons) incl. read-aloud lessons with pronunciation pass checks.
- Today's session adds a pronunciation drill targeting your weakest sound; Progress adds a Language skills section.
- Lessons moved to `content/journeys/`.

## v4 — real audio
- Records the actual audio in the browser (AudioWorklet → 16 kHz mono WAV) alongside the live transcript.
- Waveform analysis with numpy: pauses (natural / long / stalls), speaking vs. silent time, pace every 10 s,
  pitch variety in semitones, volume consistency and trail-off, recording-quality warning.
- Verbatim transcription with word timestamps via Whisper (OpenAI API or local faster-whisper); a disfluent
  prompt biases Whisper to keep "um/uh".
- Delivery score = 40% words (fillers, hedges) + 60% voice (pace, pauses, pitch variety, volume).
- Voice panel: waveform with pauses marked, click-to-seek playback, pace and pitch charts.
- Lessons can require audio-only checks.

## v3 — lessons and a daily plan
- "Communication Mastery" journey: 10 lessons in 3 modules, each with concept, framework card, cheat sheet,
  before/after example and a spoken exercise with pass criteria.
- Answers in a lesson are scored against that lesson's framework.
- Today's session: warm-up → current lesson → review drill on your weakest skill.
- Vague-word metric.

## v2 — coaching loop
- Five practice modes with prompt banks and frameworks; five scored dimensions; filler / hedge / WPM metrics.
- Challenge Mode (questions only, never a rewritten answer — enforced server-side).
- Retry chains with take-by-take comparison; progress dashboard; next-exercise recommendation.
- SQLite history with export; OpenAI or Anthropic with a local heuristic fallback.

## v1 — MVP
- Browser speech-to-text, single LLM analysis call, JSON history.
