"""Analysis orchestration: validate a submission, process it (transcribe → measure → score → review), save it.

The HTTP layer and the job worker both call this module; neither contains analysis logic.

Audio lifecycle
- An upload is read with a hard byte limit, decoded, and its duration validated *before* any job is created.
- The decoded audio is written to ``<tmp_dir>/<job id>.wav`` (temporary).
- When the take is saved, the temp file is moved (atomic rename) to ``<audio_dir>/<uuid>.wav`` and referenced by
  the attempt. For ``save=false`` or a failed job the temp file is deleted by the job runner.
"""
import os
import uuid
from datetime import datetime
from pathlib import Path

from . import adaptive, analysis, insights, language, transcribe
from . import audio as audio_mod
from .prompts import normalize_mode
from .schemas import AnalyzeRequest, check_result

MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_BYTES", 25 * 1024 * 1024))
MAX_AUDIO_SECONDS = float(os.getenv("MAX_AUDIO_SECONDS", 600))   # 10 minutes
MIN_AUDIO_SECONDS = 1.0


class InputError(Exception):
    """A problem with what the user submitted (maps to a 4xx response; never retried; quota refunded)."""

    def __init__(self, status: int, message: str, code: str = "invalid_input"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


class AnalysisService:
    def __init__(self, curriculum, audio_dir: Path, tmp_dir: Path):
        self.curriculum = curriculum
        self.audio_dir, self.tmp_dir = Path(audio_dir), Path(tmp_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ validation (cheap, before any work)
    def validate(self, us, req: AnalyzeRequest, now: datetime, has_audio: bool) -> None:
        lesson = self.curriculum.get(req.lesson_id) if req.lesson_id else None
        if req.lesson_id and not lesson:
            raise InputError(404, "Unknown lesson", "unknown_lesson")
        gate = self.curriculum.program_gate(us, lesson, now) if lesson else None
        if gate:
            raise InputError(403, gate, "locked")
        read_aloud = self.curriculum.is_read_aloud(lesson) or req.kind == "read_aloud"
        if read_aloud and not ((req.target_text or "").strip() or lesson):
            raise InputError(400, "A read-aloud drill needs the sentence to read (target_text).")
        if not has_audio and len(req.transcript.split()) < self._min_words(req):
            raise InputError(400, "Speak or paste a response of at least a few words first.", "too_short")
        if req.kind == "baseline" and req.prompt.strip() not in {b["prompt"] for b in adaptive.BASELINE}:
            raise InputError(400, "Baseline takes must answer one of the baseline prompts.")
        if req.kind == "weekly":
            wk = adaptive.weekly_challenge(us, now, us.user_id)
            if req.prompt.strip() != wk["prompt"]:
                raise InputError(400, "The weekly challenge must answer this week's prompt.")

    @staticmethod
    def _min_words(req: AnalyzeRequest) -> int:
        return 1 if req.kind == "read_aloud" or req.target_text else 3

    # ------------------------------------------------------------------ audio
    def decode_upload(self, data: bytes):
        """Decode an upload (already size-limited) and validate its real duration. Returns the 16 kHz signal."""
        if len(data) > MAX_AUDIO_BYTES:
            raise InputError(413, f"Recording too large (max {MAX_AUDIO_BYTES // (1024 * 1024)} MB).", "too_large")
        try:
            x = audio_mod.load_audio(data)
        except ValueError as e:
            raise InputError(400, str(e), "bad_audio")
        seconds = len(x) / audio_mod.SR
        if seconds < MIN_AUDIO_SECONDS:
            raise InputError(400, "The recording is shorter than a second — record your answer again.", "too_short")
        if seconds > MAX_AUDIO_SECONDS:
            raise InputError(413, f"Recordings can be up to {int(MAX_AUDIO_SECONDS // 60)} minutes long "
                                  f"(this one is {seconds / 60:.1f} min).", "too_long")
        return x

    def write_tmp(self, job_id: str, x) -> str:
        name = f"{job_id}.wav"
        tmp = self.tmp_dir / (name + ".part")
        tmp.write_bytes(audio_mod.to_wav_bytes(x))
        os.replace(tmp, self.tmp_dir / name)              # never leave a half-written file under the final name
        return name

    def discard_tmp(self, name: str | None):
        if name:
            (self.tmp_dir / Path(name).name).unlink(missing_ok=True)

    def _finalize_audio(self, tmp_name: str) -> str:
        final = f"{uuid.uuid4()}.wav"
        os.replace(self.tmp_dir / Path(tmp_name).name, self.audio_dir / final)
        return final

    # ------------------------------------------------------------------ processing
    def process(self, us, req: AnalyzeRequest, audio_tmp: str | None, now: datetime) -> dict:
        """Full analysis of one submission. Raises InputError for problems with the submission itself."""
        if not audio_tmp:
            return self.run(us, req, req.transcript.strip(), now=now)
        path = self.tmp_dir / Path(audio_tmp).name
        x = audio_mod.load_audio(path.read_bytes())
        tr = transcribe.transcribe(str(path))
        words, source, warn = None, "browser speech-to-text", None
        text = req.transcript.strip()
        if tr and tr.get("text"):
            text, words, source = tr["text"], tr.get("words") or None, tr["source"]
        elif tr and tr.get("error"):
            warn = tr["error"] + " — used the browser transcript instead."
        try:
            voice = audio_mod.analyze(x, words, len(text.split()))
        except ValueError as e:
            raise InputError(400, str(e), "bad_audio")
        voice["transcript_source"] = source
        voice["browser_transcript"] = req.transcript.strip() if words else None
        out = self.run(us, req, text, voice, words, audio_tmp, now)
        if warn:
            out["analysis"]["warning"] = " ".join(filter(None, [out["analysis"].get("warning"), warn]))
        return out

    def run(self, store, req: AnalyzeRequest, text: str, voice: dict | None = None, words: list[dict] | None = None,
            audio_tmp: str | None = None, now: datetime | None = None) -> dict:
        now = now or datetime.now().astimezone()
        if len(text.split()) < self._min_words(req):
            raise InputError(400, "Speak or paste a response of at least a few words first.", "too_short")
        cur = self.curriculum
        mode = normalize_mode(req.mode)
        lesson = cur.get(req.lesson_id) if req.lesson_id else None
        if req.lesson_id and not lesson:
            raise InputError(404, "Unknown lesson", "unknown_lesson")
        kind = "lesson" if lesson else req.kind
        if lesson:
            mode = lesson["exercise"]["mode"]
        if cur.is_read_aloud(lesson) or req.kind == "read_aloud":
            target = (req.target_text or "").strip() or (cur.read_aloud_texts(lesson)[0] if lesson else "")
            if not target:
                raise InputError(400, "A read-aloud drill needs the sentence to read (target_text).")
            result = analysis.analyze_read_aloud(text, target, words, voice)
            mode, prompt = "Read aloud", target
        else:
            spec = cur.spec(lesson) if lesson else None
            daily = [w["word"] for w in language.daily_words(now.date())]
            history = [h for h in store.recent(12) if h.get("kind") != "read_aloud"][::-1]   # oldest → newest
            result = analysis.analyze(text, mode, req.prompt.strip(), req.challenge_mode, req.duration_seconds, spec,
                                      voice, words, daily, history)
            prompt = req.prompt.strip()
        check_result(result)                              # typed invariants before anything is saved
        lesson_result = cur.evaluate(lesson, result) if lesson else None
        if lesson_result:
            result["lesson_result"] = lesson_result
        if not req.save:
            return {"analysis": result, "lesson_result": lesson_result, "attempt": None}
        if kind == "weekly":
            result["weekly_week"] = adaptive.week_key(now)  # the week this unseen challenge belongs to

        pid = lesson.get("program_id") if lesson else None
        if pid and not store.meta_get(cur.program_key(pid)):
            cur.enroll(store, pid, now)                  # practising Day 1 starts the program
        chain_id = req.chain_id if req.chain_id and store.chain(req.chain_id) else None
        audio_name = self._finalize_audio(audio_tmp) if audio_tmp else None
        try:
            rec = store.add_attempt(mode=mode, prompt=prompt, transcript=text,
                                    challenge_mode=req.challenge_mode, duration_seconds=req.duration_seconds,
                                    analysis=result, chain_id=chain_id, kind=kind,
                                    lesson_id=lesson["id"] if lesson else None,
                                    lesson_passed=lesson_result["passed"] if lesson_result else None,
                                    audio_path=audio_name)
        except Exception:
            if audio_name:                               # don't orphan the moved recording
                (self.audio_dir / audio_name).unlink(missing_ok=True)
            raise
        adaptive.record_take(store, rec, now)             # skill evidence → spaced-review schedule, baseline
        out = {"attempt": rec, "analysis": result, "comparison": None, "lesson_result": lesson_result}
        if pid:
            st = cur.program_state(store, pid, now)
            out["program"] = {k: st[k] for k in ("id", "title", "done", "total", "complete", "current_day",
                                                 "today_status", "streak", "milestones")}
        if rec["attempt_no"] > 1:
            chain = store.chain(rec["chain_id"])
            out["comparison"] = insights.compare(chain[-2], rec, cache=store)
            out["first_vs_latest"] = insights.compare(chain[0], rec, cache=store) if len(chain) > 2 else None
        return out
