# Changelog

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
