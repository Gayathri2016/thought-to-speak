# Changelog

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
