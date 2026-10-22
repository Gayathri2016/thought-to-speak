# Thought → Speak

**A personal AI communication coach that listens to how you actually speak.**
Learn a framework → answer out loud → get coached on content *and* voice → retry → compare.

![Learn tab: today's session and the Communication Mastery journey](docs/screenshots/learn.png)

Most people don't have a knowledge problem — they have an articulation problem. You know the answer,
but under pressure it comes out as background first, point last, with a few "um"s in between.
Thought → Speak is built to close that gap, and to make you *less* dependent on AI, not more:
in Challenge Mode it coaches with questions and never writes your answer for you.

## Features

**Learn**
- *Communication Mastery* journey — 10 short lessons in 3 modules: BLUF, PREP, Rule of three,
  Pause-don't-fill, Acknowledge–Bridge–Answer, What / So what / Now what, STAR, the abstraction ladder,
  precise words, and landing the ending.
- Each lesson: concept → framework card → cheat sheet → before/after example → spoken exercise with
  explicit pass criteria. Your answer is scored against *that lesson's* framework.
- **Today's session (~15 min):** a no-prep warm-up, your current lesson, and a review drill on your weakest skill.

**Pronounce — new in v5**

![Pronounce tab: TH drill with minimal pairs and a word's stress](docs/screenshots/pronounce.png)

- **Read-aloud drills** in 7 sets: technical clarity, word stress, TH, V/W, R/L, short vs long vowels, word endings.
- **Word-by-word result:** what you read is aligned with the target sentence; every word is marked clear,
  unclear, swapped ("think" heard as "sink") or missed.
- **Sound-swap detection:** a swapped word is compared with the target in the CMU Pronouncing Dictionary to name
  the sound that changed (`th → s`, `v → w`, `r → l`, long *ee* → short *i*), and tracked per sound over time.
- **Stress & respelling** for any word — `development` → dih-VEH-luhp-muhnt — plus ▶ hear / 🐢 slowly
  (browser text-to-speech) and minimal-pair drills.

![Word-by-word read-aloud result](docs/screenshots/read-aloud.png)

**Words & sentences — new in v5**

![Vocabulary and sentence-structure panel](docs/screenshots/words-sentences.png)

- **Vocabulary:** variety, share of precise (less common) words, vague/weak wording with stronger alternatives,
  repeated words, and **3 words of the day** from a 60-word professional word bank — the coach checks if you used them.
- **Sentence structure:** sentence lengths (from punctuation or your pauses), run-ons, repeated openers,
  passive voice, and basic grammar checks (`he don't`, `there's many`, `a hour`…). With an API key, the AI adds
  context-aware word upgrades and grammar notes.
- **Speak Clearly journey:** 9 lessons — word stress, technical words, endings, TH, V/W, R/L, precise words,
  varied sentences, active voice.

**The 28-Day Speaking Sprint — new in v7**
- A daily program: one real-world speaking skill per day, ~10 minutes, four themed weeks
  (Foundations → Voice & presence → Conversations → Influence), ending with your signature talk.
- Each day: concept, framework, cheat sheet, before/after example, and a spoken exercise with pass criteria.
- The next day unlocks the following morning in your timezone; streaks and weekly milestones keep you going.
- Program content lives in `content/programs/speaking-sprint-28.json`.

**Practice**
- Five scenarios — Interview, Leadership, Technical explanation, Toastmasters, FDE / Customer — or your own prompt.
- Scores for **structure, clarity, relevance, answer quality and delivery**, plus a framework check.
- **Retry loop:** retries are chained to the same prompt and compared take by take.
- **Challenge Mode:** Socratic questions and a hint only; the server strips any outline or rewrite.

**Your voice — measured from the recording, not the transcript**

![Voice panel: waveform with a 3.4 s stall marked, pace and pitch charts](docs/screenshots/voice.png)

| Metric | How it's measured |
|---|---|
| Pauses: natural (< 1 s), long (1–2.5 s), **stalls** (> 2.5 s) | energy-based voice activity detection on 20 ms frames, adaptive noise floor |
| Pace overall, while talking, and every 10 s | speaking window + Whisper word timestamps |
| **Pitch variety** (monotone / limited / expressive) | autocorrelation F0 tracking (75–400 Hz), range in semitones |
| Volume consistency and **trailing off** | speech-frame dB, last 15 % vs. the body |
| Fillers that speech-to-text usually deletes | Whisper with a disfluent prompt so "um / uh" are kept |
| Unclear words in free speech | local Whisper per-word confidence (< 50 %) |

**Progress** — score trend, skills last-10 vs. previous-10, average gain from retrying, streak,
per-scenario stats, top filler words, and a personalised next exercise.

| Coaching | Progress |
|---|---|
| ![Coaching panel](docs/screenshots/coaching.png) | ![Progress dashboard](docs/screenshots/progress.png) |

## Quick start

```bash
git clone https://github.com/Gayathri2016/thought-to-speak.git
cd thought-to-speak
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # optional: add OPENAI_API_KEY
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000> in Chrome or Edge (live transcript). The microphone works only on `localhost` or HTTPS.

**Optional — free offline transcription:** `pip install -r requirements-whisper.txt` installs
[faster-whisper](https://github.com/SYSTRAN/faster-whisper); the `base.en` model (~140 MB) downloads on first use.

| `.env` setting | Effect |
|---|---|
| none | Works fully offline: local heuristic scoring + waveform analysis + browser transcript |
| `OPENAI_API_KEY` | AI coaching feedback and Whisper API transcription |
| `ANTHROPIC_API_KEY` | AI coaching feedback via Claude (used when no OpenAI key is set) |
| `LLM_PROVIDER`, `TRANSCRIBE_PROVIDER` | Force `openai` / `anthropic` / `local` / `none` |
| `AUTH_MODE` | `accounts` (default): sign-in required, data private per user · `single`: no sign-in, one local user |
| `DAILY_TAKE_LIMIT` | Analyzed takes per user per day (default 60, `0` = unlimited) |
| `COOKIE_SECURE` | `auto` (default) · `1` behind an HTTPS proxy |

**Accounts.** On first launch you'll see a sign-up screen. The first account you create takes over any practice
you recorded before accounts existed. Prefer no sign-in for personal use? Set `AUTH_MODE=single`.

## How it works

```mermaid
flowchart LR
  A[Browser<br/>mic → 16 kHz WAV<br/>+ live transcript] -->|multipart| B[FastAPI]
  B --> C[Whisper<br/>verbatim + word timestamps]
  B --> D[Waveform analysis<br/>pauses · pace · pitch · volume]
  C --> E[Text metrics<br/>fillers · hedges · vague words]
  C --> F[LLM coach<br/>structure · clarity · relevance · quality]
  D --> G[Scoring + feedback]
  E --> G
  F --> G
  G --> H[(SQLite + local audio)]
  G --> I[Lesson checks · retry comparison · progress · next exercise]
```

Design choices:
- **LLMs judge, code counts.** Fillers, hedges, pace, pauses and pitch are computed deterministically; the model
  only scores content. If the LLM call fails, you get local analysis instead of an error.
- **Delivery = 40 % words + 60 % voice.**
- **Lessons are data.** Each `content/*.json` file is a journey, validated at startup. Pass checks can use
  `overall`, `framework_coverage`, `scores.*`, `metrics.*` or `voice.*`.
- **Private by default.** Every take belongs to an account and is visible only to it. History and recordings
  are stored in `data/`. Transcripts go to your AI
  provider only if you configure one. Chrome's live transcript uses Google's speech service.

```
app.py               FastAPI routes
coach/analysis.py    heuristic + LLM analysis, Challenge Mode guard
coach/audio.py       waveform analysis and voice scores (numpy)
coach/transcribe.py  Whisper: OpenAI API or local faster-whisper
coach/metrics.py     fillers, hedges, vague words, WPM
coach/curriculum.py  journeys, lesson pass checks, daily plan
coach/pronunciation.py  read-aloud alignment, sound swaps, stress & respelling
coach/language.py    vocabulary, sentence structure, grammar checks, word bank
coach/insights.py    retry comparison, progress, recommendation
coach/storage.py     SQLite + migrations; per-user view (UserStore)
coach/auth.py        passwords (scrypt), sessions, rate limits, quotas
content/journeys/    lessons (JSON) — one file per journey
content/programs/    daily programs (JSON) — e.g. the 28-Day Speaking Sprint
content/pronunciation.json, content/vocabulary.json, content/lexicon/  drills, word bank, CMU dictionary subset
static/              UI — plain HTML/CSS/JS, no build step
tests/               pytest
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Limitations

- Pitch tracking and pause detection are signal-processing heuristics, not trained models; background noise
  lowers accuracy (the app warns when the recording is quiet or noisy).
- Pronunciation is assessed **at the word level** (what the recognizer heard vs. what you were asked to read),
  not phoneme by phoneme. A recognizer can "autocorrect" a slightly off word, so a clean result means you were
  understood — not that every sound was perfect. Unclear-word confidence needs local Whisper.
- Without an API key, content scores are keyword heuristics — directional, not precise.
- Single-user, local app. Multi-user deployment would need auth, per-user storage and rate limiting.

## Roadmap

- Follow-up interviewer agent that probes your answer in real time
- "Idea spark" hints that fade as you improve
- AI-generated prompts tailored to a role or job description
- Conversation simulator (small talk, difficult customer)

See [CHANGELOG.md](CHANGELOG.md) for version history.

## License

[MIT](LICENSE). `content/lexicon/cmudict_common.txt` is a subset of the
[CMU Pronouncing Dictionary](https://github.com/cmusphinx/cmudict) (BSD-style license, see
`content/lexicon/LICENSE-cmudict`). Word-frequency data comes from the
[wordfreq](https://github.com/rspeer/wordfreq) package (installed via pip, not bundled).
