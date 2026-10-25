"""Persistent analysis jobs with atomic quota accounting, idempotency, and an in-process worker pool.

Why this design
- The app runs as a single process (one uvicorn worker) on small hosts, with SQLite. A thread pool inside the
  process, with jobs persisted in SQLite, is the smallest reliable design: no extra service (Redis/Celery) to run
  or pay for, and nothing is lost on restart because every job is in the database before any work starts.

Lifecycle:  queued → running → completed | failed
- ``submit`` validates and stores the job and reserves one unit of the user's daily quota in a single
  ``BEGIN IMMEDIATE`` transaction, so two concurrent submissions can't both take the last unit.
- A worker *claims* a job with ``UPDATE … WHERE status='queued'`` (only one claimer wins), runs it, then marks it
  completed (quota → consumed) or failed (quota → refunded).
- ``recover()`` runs once at start-up: jobs left ``running`` by a crash/restart are re-queued, unless they have
  already been tried ``JOB_MAX_TRIES`` times, in which case they fail and are refunded.

Idempotency
- Clients send an idempotency key per take (scoped to the user). Re-submitting the same key returns the existing
  job (no second reservation, no second provider call). The same key with a different payload → conflict (409).

Policies (also in README)
- Quota is reserved for every analysis, including ``save=false``. Daily limit, UTC day.
- Invalid input (too short, bad/too long audio, locked lesson) is rejected before a job exists: no quota used.
- Input problems found during processing (e.g. Whisper heard nothing) → failed + refunded, not retried.
- Unexpected errors → failed + refunded. Transient database errors are retried up to ``JOB_MAX_TRIES``.
- External providers: an AI or Whisper call that times out falls back to local analysis inside the job (not a
  failure). If the process dies mid-call, the job is re-run on restart, so a provider call can repeat —
  processing is at-least-once, bounded by ``JOB_MAX_TRIES``.
- Once a job's take is saved (``attempts.job_id``), a re-run reuses it — no second take, no second analysis — and
  any later error completes the job from that take instead of failing and refunding it.
- Deleting a take marks its job ``error_code='deleted'`` (request/result removed, quota kept). Such a job never
  re-runs analysis, and completing it never writes the result back.
"""
import hashlib
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from . import adaptive
from .schemas import AnalyzeRequest
from .service import InputError
from .storage import Store

DELETED_MSG = Store.DELETED_MSG

log = logging.getLogger("thought_to_speak")

MAX_TRIES = int(os.getenv("JOB_MAX_TRIES", 3))
TMP_RETENTION_SECONDS = int(os.getenv("TMP_RETENTION_SECONDS", 3600))
CLEANUP_EVERY_SECONDS = 600
ACTIVE = ("queued", "running")


class QuotaExceeded(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def request_hash(req: dict, audio_bytes: bytes | None) -> str:
    body = {k: v for k, v in req.items() if k not in ("idempotency_key",) and not k.startswith("_")}
    h = hashlib.sha256(json.dumps(body, sort_keys=True).encode())
    if audio_bytes is not None:
        h.update(hashlib.sha256(audio_bytes).digest())
    return h.hexdigest()


def public_job(row: dict | None) -> dict | None:
    """What clients see. Never includes internal fields (request hash, temp file names)."""
    if not row:
        return None
    return {"id": row["id"], "status": row["status"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "finished_at": row.get("finished_at"),
            "error": row.get("error"), "error_code": row.get("error_code"), "attempt_id": row.get("attempt_id"),
            "tries": row.get("tries"), "quota": row.get("quota_state"),
            "result": json.loads(row["result_json"]) if row.get("result_json") else None}


COMPARISON_FIELDS = ("comparison", "first_vs_latest")


def _job_user(c, job_id):
    r = c.execute("SELECT user_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    return r["user_id"] if r else None


def _drop_deleted_comparisons(c, result: dict, user_id) -> dict:
    """Inside the completion transaction: a retry comparison whose takes no longer exist for this user (deleted
    after the job built it in memory) is not persisted. Deletion and completion are serialized by the write lock,
    so whichever commits second sees the other's effect."""
    out = dict(result)
    for k in COMPARISON_FIELDS:
        cmp = out.get(k)
        if not cmp:
            continue
        ids = [i for i in (cmp.get("base_id"), cmp.get("current_id")) if i]
        if any(not c.execute("SELECT 1 FROM attempts WHERE id=? AND user_id IS ?", (i, user_id)).fetchone()
               for i in ids):
            out[k] = None
    return out


class JobStore:
    """SQL for jobs + usage. Uses the app Store's connection settings (WAL, busy timeout)."""

    def __init__(self, store):
        self.s = store

    def _row(self, r):
        return dict(r) if r else None

    def find_by_key(self, user_id: str, key: str) -> dict | None:
        with self.s._conn() as c:
            return self._row(c.execute("SELECT * FROM jobs WHERE user_id=? AND idempotency_key=?",
                                       (user_id, key)).fetchone())

    def create(self, *, job_id: str, user_id: str, req: dict, rhash: str, key: str | None,
               audio_tmp: str | None, limit: int) -> tuple[dict, bool]:
        """Atomically: return the existing job for this key, or reserve quota and insert a new queued job."""
        day, now = utc_day(), _now()
        with self.s._conn() as c:
            c.execute("BEGIN IMMEDIATE")              # one writer at a time across threads *and* processes
            if key:
                existing = c.execute("SELECT * FROM jobs WHERE user_id=? AND idempotency_key=?",
                                     (user_id, key)).fetchone()
                if existing:
                    if existing["request_hash"] != rhash:
                        raise IdempotencyConflict("This idempotency key was already used for a different submission.")
                    return dict(existing), False
            if limit:
                used = c.execute("SELECT reserved FROM usage WHERE user_id=? AND day=?", (user_id, day)).fetchone()
                if used and used[0] >= limit:
                    raise QuotaExceeded(f"You've reached today's limit of {limit} takes. It resets at midnight UTC.")
            c.execute("""INSERT INTO usage (user_id, day, reserved) VALUES (?, ?, 1)
                         ON CONFLICT(user_id, day) DO UPDATE SET reserved = reserved + 1""", (user_id, day))
            c.execute("""INSERT INTO jobs (id, user_id, idempotency_key, request_hash, status, request_json, audio_tmp,
                                           quota_day, quota_state, created_at, updated_at)
                         VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 'reserved', ?, ?)""",
                      (job_id, user_id, key, rhash, json.dumps(req), audio_tmp, day, now, now))
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row), True

    def get(self, job_id: str, user_id: str | None = None) -> dict | None:
        with self.s._conn() as c:
            if user_id is None:
                return self._row(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            return self._row(c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone())

    def list_for_user(self, user_id: str, active_only: bool, limit: int = 20) -> list[dict]:
        q = "SELECT * FROM jobs WHERE user_id=?" + (" AND status IN ('queued','running')" if active_only else "")
        with self.s._conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY created_at DESC LIMIT ?", (user_id, limit))]

    def queued_ids(self) -> list[str]:
        with self.s._conn() as c:
            return [r[0] for r in c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at")]

    def claim(self, job_id: str) -> dict | None:
        now = _now()
        with self.s._conn() as c:
            n = c.execute("""UPDATE jobs SET status='running', tries=tries+1, started_at=?, updated_at=?
                             WHERE id=? AND status='queued'""", (now, now, job_id)).rowcount
            return dict(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()) if n else None

    def complete(self, job_id: str, result: dict | None, attempt_id: str | None):
        """Mark completed (quota → consumed). If the take was deleted while the job was finishing, the job
        completes without its result — deleted content is never written back."""
        now = _now()
        with self.s._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            j = c.execute("SELECT error_code FROM jobs WHERE id=?", (job_id,)).fetchone()
            gone = j is not None and (j["error_code"] == "deleted" or (attempt_id is not None and not c.execute(
                "SELECT 1 FROM attempts WHERE id=?", (attempt_id,)).fetchone()))
            if gone:
                c.execute("""UPDATE jobs SET status='completed', result_json=NULL, attempt_id=NULL, audio_tmp=NULL,
                                             request_json=?, error=?, error_code='deleted', quota_state='consumed',
                                             finished_at=?, updated_at=? WHERE id=? AND status='running'""",
                          (json.dumps({"redacted": True}), DELETED_MSG, now, now, job_id))
                return
            if result:                                # comparisons against takes deleted meanwhile are dropped
                result = _drop_deleted_comparisons(c, result, _job_user(c, job_id))
            c.execute("""UPDATE jobs SET status='completed', result_json=?, attempt_id=?, audio_tmp=NULL,
                                         quota_state='consumed', finished_at=?, updated_at=?
                         WHERE id=? AND status='running'""", (json.dumps(result), attempt_id, now, now, job_id))

    def fail(self, job_id: str, message: str, code: str):
        """Mark failed and refund the reserved quota unit (once)."""
        now = _now()
        with self.s._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT user_id, quota_day, quota_state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not r:
                return
            if r["quota_state"] == "reserved":
                c.execute("UPDATE usage SET reserved = MAX(0, reserved - 1) WHERE user_id=? AND day=?",
                          (r["user_id"], r["quota_day"]))
            c.execute("""UPDATE jobs SET status='failed', error=?, error_code=?, audio_tmp=NULL,
                                         quota_state=CASE WHEN quota_state='reserved' THEN 'refunded' ELSE quota_state END,
                                         finished_at=?, updated_at=? WHERE id=?""", (message, code, now, now, job_id))

    def requeue(self, job_id: str):
        with self.s._conn() as c:
            c.execute("UPDATE jobs SET status='queued', updated_at=? WHERE id=? AND status='running'", (_now(), job_id))

    def interrupted(self) -> list[dict]:
        with self.s._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM jobs WHERE status='running'")]

    def usage(self, user_id: str, day: str | None = None) -> int:
        with self.s._conn() as c:
            r = c.execute("SELECT reserved FROM usage WHERE user_id=? AND day=?", (user_id, day or utc_day())).fetchone()
        return r[0] if r else 0

    def referenced_tmp(self) -> set[str]:
        with self.s._conn() as c:
            return {r[0] for r in c.execute("SELECT audio_tmp FROM jobs WHERE status IN ('queued','running') "
                                             "AND audio_tmp IS NOT NULL")}


class JobRunner:
    """In-process worker pool. Start lazily (first submission) or at app start-up; safe to call start() twice."""

    def __init__(self, store, service, workers: int | None = None):
        self.jobs = JobStore(store)
        self.store, self.service = store, service
        self.workers = workers or int(os.getenv("ANALYSIS_WORKERS", 2))
        self._q: queue.Queue = queue.Queue()
        self._events: dict[str, threading.Event] = {}
        self._events_lock = threading.Lock()
        self._started = False
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_cleanup = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        with self._start_lock:
            if self._started:
                return
            self._started = True
            self.recover()
            self.cleanup()
            for i in range(self.workers):
                t = threading.Thread(target=self._loop, name=f"analysis-worker-{i}", daemon=True)
                t.start()
                self._threads.append(t)
            for jid in self.jobs.queued_ids():
                self._q.put(jid)

    def stop(self, timeout: float = 5):
        self._stop.set()
        for t in self._threads:
            t.join(timeout)

    def recover(self) -> dict:
        """Re-queue jobs interrupted by a crash or restart; fail (and refund) those out of tries."""
        requeued, failed = 0, 0
        for j in self.jobs.interrupted():
            if j["tries"] >= MAX_TRIES:
                if self._salvage(j):                  # its take was saved: finish it, don't refund or re-run
                    continue
                self.jobs.fail(j["id"], "The analysis was interrupted too many times. You were not charged — "
                                        "please try again.", "interrupted")
                self.service.discard_job_audio(j)
                failed += 1
            else:
                self.jobs.requeue(j["id"])
                requeued += 1
        if requeued or failed:
            log.info("Job recovery: %d re-queued, %d failed", requeued, failed)
        return {"requeued": requeued, "failed": failed}

    def cleanup(self) -> int:
        """Delete temp audio that no active job references and that is older than the retention window.
        Saved recordings in the audio folder are never touched here."""
        self._last_cleanup = time.monotonic()
        keep, removed = self.jobs.referenced_tmp(), 0
        cutoff = time.time() - TMP_RETENTION_SECONDS
        for f in self.service.tmp_dir.glob("*"):
            if f.is_file() and f.name not in keep and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                removed += 1
        return removed

    # ------------------------------------------------------------------ API
    def submit(self, us, req: AnalyzeRequest, now: datetime, limit: int, audio=None, audio_bytes: bytes | None = None,
               key: str | None = None) -> tuple[dict, bool]:
        """Store a job and reserve quota. `audio` is the already-decoded, validated signal (or None)."""
        self.start()
        key = key or req.idempotency_key
        payload = {**req.model_dump(), "_now": now.isoformat()}
        rhash = request_hash(req.model_dump(), audio_bytes)
        if key:                                       # fast path: a replay doesn't write temp audio again
            existing = self.jobs.find_by_key(us.user_id, key)
            if existing:
                if existing["request_hash"] != rhash:
                    raise IdempotencyConflict("This idempotency key was already used for a different submission.")
                return existing, False
        job_id = str(uuid.uuid4())
        tmp = self.service.write_tmp(job_id, audio) if audio is not None else None
        try:
            job, created = self.jobs.create(job_id=job_id, user_id=us.user_id, req=payload, rhash=rhash, key=key,
                                            audio_tmp=tmp, limit=limit)
        except Exception:
            self.service.discard_tmp(tmp)
            raise
        if not created:
            self.service.discard_tmp(tmp)             # lost a race with an identical submission
        else:
            self._q.put(job["id"])
        return job, created

    def wait(self, job_id: str, timeout: float) -> dict:
        ev = self._event(job_id)
        deadline = time.monotonic() + timeout
        while True:
            job = self.jobs.get(job_id)
            if not job or job["status"] not in ACTIVE:
                return job
            left = deadline - time.monotonic()
            if left <= 0:
                return job
            ev.wait(min(left, 0.5))

    # ------------------------------------------------------------------ worker
    def _event(self, job_id):
        with self._events_lock:
            return self._events.setdefault(job_id, threading.Event())

    def _signal(self, job_id):
        with self._events_lock:
            ev = self._events.pop(job_id, None)
        if ev:
            ev.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                job_id = self._q.get(timeout=1)
            except queue.Empty:
                if time.monotonic() - self._last_cleanup > CLEANUP_EVERY_SECONDS:
                    self.cleanup()
                continue
            try:
                self.run_one(job_id)
            except Exception:                         # never let a worker thread die
                log.exception("Job runner error for %s", job_id)

    @staticmethod
    def _job_now(job) -> datetime:
        try:
            ts = json.loads(job.get("request_json") or "{}").get("_now")
        except ValueError:
            ts = None
        return datetime.fromisoformat(ts) if ts else datetime.now().astimezone()

    def _salvage(self, job: dict) -> bool:
        """If this job already saved its take, complete the job from that take (quota consumed) and return True.
        Used when finishing normally is impossible, so a saved take is never refunded or left with a failed job."""
        try:
            rec = self.store.attempt_for_job(job["id"], job["user_id"])
            if not rec:
                return False
            try:
                adaptive.record_take(self.store.for_user(job["user_id"]), rec, self._job_now(job))
            except Exception:
                log.exception("Skill update failed while salvaging job %s", job["id"])
            self.service.discard_tmp(job.get("audio_tmp"))
            self.jobs.complete(job["id"], {"attempt": rec, "analysis": rec["analysis"], "comparison": None,
                                           "lesson_result": rec["analysis"].get("lesson_result")}, rec["id"])
            return True
        except Exception:
            log.exception("Could not salvage job %s", job["id"])
            return False

    def _give_up(self, job: dict, message: str, code: str):
        """Terminal failure — unless the take was already saved, in which case the job completes from it."""
        if self._salvage(job):
            return
        self.service.discard_job_audio(job)
        self.jobs.fail(job["id"], message, code)

    def run_one(self, job_id: str):
        job = self.jobs.claim(job_id)
        if not job:
            return                                    # already taken by another worker, or not queued
        try:
            if job.get("error_code") == "deleted":    # the take was deleted mid-run: nothing to bring back
                self.jobs.complete(job_id, None, None)
                return
            req_data = json.loads(job["request_json"])
            now = datetime.fromisoformat(req_data.pop("_now")) if req_data.get("_now") else datetime.now().astimezone()
            req = AnalyzeRequest(**req_data)
            us = self.store.for_user(job["user_id"])
            out = self.service.process(us, req, job.get("audio_tmp"), now, job_id=job_id)
            if not req.save:
                self.service.discard_tmp(job.get("audio_tmp"))
            self.jobs.complete(job_id, out, (out.get("attempt") or {}).get("id"))
        except InputError as e:
            self._give_up(job, e.message, e.code)
        except sqlite3.OperationalError as e:         # e.g. database locked for too long — transient
            log.warning("Transient error on job %s (try %s): %s", job_id, job["tries"], e)
            if job["tries"] < MAX_TRIES:
                self.jobs.requeue(job_id)             # the re-run reuses anything this run already saved
                self._q.put(job_id)
                return
            self._give_up(job, "The analysis couldn't be saved. You were not charged — please try again.", "internal")
        except Exception:
            log.exception("Job %s failed", job_id)
            self._give_up(job, "Something went wrong analysing this take. You were not charged — please try "
                               "again.", "internal")
        finally:
            j = self.jobs.get(job_id)
            if j and j["status"] not in ACTIVE:
                self._signal(job_id)
