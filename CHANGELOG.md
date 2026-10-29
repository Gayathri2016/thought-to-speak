# Changelog

## Unreleased — the coach reads answer-first takes correctly

- **Bottom line by echo.** An answer that opens by restating the question ("The biggest risk on my project is…")
  now counts as the Bottom line (also Point and Decision / Ask). The score, "Your answer, part by part" and
  "Say it like this" share one matcher, so a part can no longer be "missing" in one place and filled with your
  words in another. In a three-part framework, the next real sentence after the opening counts as the middle part.
- **Grammar the mentor now catches:** "a tangible results" → "a tangible result"; "multi-applying multiple" →
  "applying multiple"; "the risk … wants to" → "the risk … is that we have to". Lessons name the specific rule.
- **No more "came through clean" for a 40-word sentence.** Any sentence over 28 words gets a line-by-line note
  to split it, with a reminder to fix words the browser misheard.
- Scorer version h11; benchmark baseline refreshed (one intended change: the weak leadership update now gets
  credit for opening with its status).

## Unreleased — try it before signing up

- **Guest mode.** "Try it now — no account needed" on the welcome screen starts a guest session: up to 3 takes a
  day (`GUEST_TAKE_LIMIT`), no email asked. "Save my progress" turns the guest into a real account in place, so the
  takes, streak and today's goal carry over. Guests don't use beta spots, are rate-limited per network (10 an hour),
  can delete their practice without a password, and are deleted automatically after 14 days. `GUEST_MODE=0` turns
  it off.
- **Welcome screen shows the mentor.** The example is now a real before/after from the built-in coach: what you
  said, the grammar fixes, and the mentor's version in your words.
- **Mentor fixes:** a short lead-in ("so yesterday…") stays with its sentence instead of becoming "Yesterday.";
  "it were" → "it was" (but "if it were" is left alone); "…a lot it were fun" splits into two sentences.
- Setup: Caddy's log folder is handed to the `caddy` user after the config check, so Caddy starts on a fresh server.

## Unreleased — free public beta
- Hosting kit for a $0 beta on Oracle Cloud Always Free (`deploy/oracle/`): one-command `setup.sh` (Python env,
  Caddy with automatic HTTPS, firewall ports, hardened systemd service, swap on 1 GB servers, optional DuckDNS
  updater), nightly SQLite-safe backups kept 7 days, `update.sh` that backs up and rolls back automatically if a
  release doesn't start, and `pull-backup.sh` for off-site copies. Runbook: `docs/DEPLOY_BETA.md`.
- Beta settings: `BETA` (badge + footer feedback link), `BETA_MAX_USERS` (sign-up cap), `FEEDBACK_URL`,
  `CONTACT_EMAIL`; `API_DOCS=0` hides /docs in production.
- `/privacy` page in plain language; linked from sign-up and the footer.
- Security headers on every response (CSP that allows the microphone worklet, nosniff, referrer and permissions
  policies, frame denial, HSTS over HTTPS).
- Behind a proxy, uvicorn must run with `--proxy-headers` so sign-up / login rate limits see each visitor's IP —
  tested end to end.

## Unreleased — the mentor corrects grammar
- New `coach/grammar.py`: rule-based grammar corrections for the most common spoken / second-language slips, each
  with a one-line rule — past tense ("Yesterday we go … play" → "went … played", carried across the story and
  stopped by "now / next time"), agreement ("she have", "he like", "people saves", "they is", "everybody are"),
  verb forms ("can goes", "want go", "I am agree", "have went", "enjoy to cook", "have seen … last week"), pronouns
  ("me and my son went" → "my son and I went", "my son he"), nouns ("informations", "two time"), word choice
  ("explain me", "say me", "discuss about", "go to home", "more easy") and capitals. Checked against correct
  English and the scoring benchmark for false corrections.
- Grammar fixes appear in "Say it like this" and line by line ("“go” → “went”: you're talking about the past…");
  "Grammar to practise" lists the speaker's most frequent patterns with a lesson and their own examples.
- Three or more slips make grammar the first fix (after an off-topic answer), with the lesson as the retry focus.
- Challenge Mode keeps the lessons but hides the corrected words.
- The grammar list under Words & sentences uses the same engine.

## Unreleased — the built-in mentor rewrites your answer
- "Say it like this": without an API key, the coach now rewrites the take from the speaker's own words — a spoken
  run-on becomes sentences (split where a new subject starts), fillers, false starts ("have some car had some
  coffee"), stacked hedges in work modes and sign-offs ("that's all") are removed, short same-subject sentences are
  joined, and the answer is placed in the scenario's order (e.g. Opening → Detail → Question). Lines the
  scenario needs but the speaker didn't say are added and labelled; a missing middle part becomes a "[fill in]"
  prompt — no invented facts. Opinions get "Point. That's because …" split out; prompt follow-ups are turned
  around ("ask what they recommend" → "What would you recommend?"). Shown in the focus card and at the top of
  the full review, with Listen. Challenge Mode never shows it.
- Line by line: each sentence as said → how to say it → why, most instructive fixes first.
- Off-topic answers: when the prompt asks for a recommendation or an opinion and the answer doesn't give one,
  that becomes the first fix (relevance capped at 40), with a starter line built from the speaker's own words.
- Accuracy: "a.m." / "p.m." no longer end a sentence or trigger an "a m → an m" grammar flag; "Keep doing" and
  "Fix first" never quote the same words; a missing opening is no longer called "warm-up"; two fillers in a
  100-word stretch no longer outrank structure; quotes from unpunctuated speech are short clauses, not 24-word
  chunks; the conversation partner asks a natural follow-up. Heuristic scoring version h10.
- Removed "add an API key" messages from results.

## Unreleased — a more inviting first run
- Sign-up shows what you get: the practice loop, a short before/after example, and three plain promises
  (on phones: the promise, then the form).
- Practice is built around one large mic button whose halo follows your voice; "Get my feedback" and
  "Start over" replace Analyze / Clear; transcript caveats moved under "About the transcript".
- A day strip on every tab: greeting, today's takes toward a 3-take goal, the day streak, and one next step.
  Reaching the goal shows a one-time toast.
- Results open with one honest headline (and "Up N points" after an improved retry); the score ring draws
  and counts up once (skipped with reduced motion). The empty coaching panel explains the speak → tip → retry loop.
- Phones: tabs become a bottom bar; scenarios scroll in one row so the mic sits near the fold.
- New look: studio teal, recording red only while the mic is live, amber for streaks; rounded system display
  type (no web fonts, so it still works offline and loads nothing from third parties); sentence-case labels.
- Header badge reads "Built-in coach" / "AI coach", with engine details in its tooltip.
- `static/js/habit.js` (daily goal, streak copy, result headlines) with node tests.

## Unreleased — everyday practice and transcript handling
- Practice opens first with four everyday scenarios and 24 prompts. Professional scenarios remain available
  under Work & presentations; everyday scenarios use their own goals, rubrics and listener feedback.
- Removed FDE / Customer from scenario choices; its scoring metadata remains for older lessons and history.
- Analysis waits for final browser recognition results after Stop, deduplicates repeated final segments and
  discards late results from a cleared take. A bounded timeout never promotes unconfirmed previews to speech.
- Prolonged hesitation spellings such as “ummm” and “uhhh” are counted. Filler counts are explicitly described
  as transcript observations: recognition can omit fillers and zero detected does not mean none were spoken.
- Recording results retain the submitted live draft even without word timings, so differing transcripts can
  be compared. Heuristic scoring version is h9; existing saved attempts remain unchanged.

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
