"""SQLite storage. One row per spoken attempt; retries share a chain_id.

Every attempt belongs to a user (``user_id``). Rows created before accounts existed have ``user_id`` NULL and are
handed to the first account that signs up. Route handlers never touch ``Store`` directly for attempts — they use
``store.for_user(user_id)``, a view whose every query is filtered by that user.
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import scoring
from .analysis import DIMENSIONS

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY,
  chain_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  mode TEXT NOT NULL,
  prompt TEXT,
  transcript TEXT NOT NULL,
  challenge_mode INTEGER NOT NULL DEFAULT 0,
  duration_seconds REAL,
  provider TEXT,
  overall INTEGER,
  structure INTEGER, clarity INTEGER, relevance INTEGER, answer_quality INTEGER, delivery INTEGER,
  filler_count INTEGER, word_count INTEGER, wpm INTEGER,
  analysis_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_created ON attempts(created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_chain ON attempts(chain_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
-- v8.4: analysis jobs, usage accounting, cached comparison narratives
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  idempotency_key TEXT,
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  request_json TEXT NOT NULL,
  audio_tmp TEXT,
  attempt_id TEXT,
  result_json TEXT,
  error TEXT,
  error_code TEXT,
  tries INTEGER NOT NULL DEFAULT 0,
  quota_day TEXT,
  quota_state TEXT CHECK (quota_state IN ('reserved', 'consumed', 'refunded') OR quota_state IS NULL),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idem ON jobs(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at);
CREATE TABLE IF NOT EXISTS usage (
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,
  reserved INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS comparisons (
  base_id TEXT NOT NULL,
  current_id TEXT NOT NULL,
  user_id TEXT,
  narrative_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (base_id, current_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path, legacy_json: Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")     # readers don't block the analysis worker's writes
            c.executescript(SCHEMA)
            self._migrate(c)
        if legacy_json:
            self._import_legacy(Path(legacy_json))

    SCHEMA_VERSION = 4

    @classmethod
    def _migrate(cls, c):
        """Additive, idempotent migrations. Never drops or rewrites existing data — only adds columns and fills
        new columns that are still NULL.
        v3: lesson/daily-plan columns · v6: user_id · schema 2 (v8.2): scoring provenance ·
        schema 4 (v9.1): attempts.job_id — the analysis job that saved the take (unique), so a recovered job reuses
        its committed take instead of saving a second one."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(attempts)")}
        for name, ddl in (("kind", "TEXT NOT NULL DEFAULT 'free'"), ("lesson_id", "TEXT"),
                          ("lesson_passed", "INTEGER"), ("audio_path", "TEXT"), ("user_id", "TEXT"),
                          ("score_basis", "TEXT"), ("scoring_version", "TEXT"), ("model", "TEXT"),
                          ("rubric_version", "TEXT"), ("transcript_source", "TEXT"), ("job_id", "TEXT")):
            if name not in cols:
                c.execute(f"ALTER TABLE attempts ADD COLUMN {name} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attempts_lesson ON attempts(lesson_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attempts_user ON attempts(user_id, created_at)")
        # Link takes saved by completed jobs before schema 4 (one job → one take; first job wins if ever ambiguous).
        c.execute("""UPDATE attempts SET job_id = (SELECT j.id FROM jobs j WHERE j.attempt_id = attempts.id
                                                   ORDER BY j.created_at LIMIT 1)
                     WHERE job_id IS NULL AND id IN (SELECT attempt_id FROM jobs WHERE attempt_id IS NOT NULL)""")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_job ON attempts(job_id) WHERE job_id IS NOT NULL")
        cls._backfill_provenance(c)
        c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (json.dumps(cls.SCHEMA_VERSION),))

    @staticmethod
    def _backfill_provenance(c):
        """Takes saved before scoring was versioned get an explicit 'legacy' scoring version, so they're never
        silently mixed with newer scores. Basis, model and transcript source are recovered from what was stored."""
        rows = c.execute("SELECT id, provider, kind, analysis_json FROM attempts WHERE scoring_version IS NULL").fetchall()
        for r in rows:
            provider = r["provider"] or ""
            try:
                a = json.loads(r["analysis_json"] or "{}")
            except ValueError:
                a = {}
            if r["kind"] == "read_aloud" or a.get("kind") == "read_aloud":
                basis, model = "heuristic", None
                version = f"pronunciation-{scoring.LEGACY}"
            elif provider.startswith(("openai:", "anthropic:")):
                basis, model = "ai", provider.split(":", 1)[1]
                version = f"ai-{scoring.LEGACY}"
            else:
                basis, model = "heuristic", None
                version = f"heuristic-{scoring.LEGACY}"
            voice = a.get("voice") or {}
            source = scoring.normalize_transcript_source(voice.get("transcript_source"), bool(voice))
            c.execute("""UPDATE attempts SET score_basis=?, scoring_version=?, model=?, transcript_source=?
                         WHERE id=? AND scoring_version IS NULL""", (basis, version, model, source, r["id"]))

    @contextmanager
    def _conn(self):
        # timeout: wait for a concurrent writer (worker threads) instead of failing with "database is locked"
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---------- writes ----------
    def add_attempt(self, *, mode, prompt, transcript, challenge_mode, duration_seconds, analysis,
                    chain_id=None, created_at=None, kind="free", lesson_id=None, lesson_passed=None,
                    audio_path=None, user_id=None, job_id=None) -> dict:
        """Save a take. With `job_id`, at most one take is ever saved per job: a second call returns the first."""
        with _lock, self._conn() as c:
            if job_id:
                r = c.execute("SELECT id FROM attempts WHERE job_id=? AND user_id IS ?", (job_id, user_id)).fetchone()
                if r:
                    return self.get(r["id"], user_id)
            if chain_id:
                row = c.execute("SELECT MAX(attempt_no) n FROM attempts WHERE chain_id=?", (chain_id,)).fetchone()
                attempt_no = (row["n"] or 0) + 1
            else:
                chain_id, attempt_no = str(uuid.uuid4()), 1
            s = analysis.get("scores", {})
            m = analysis.get("metrics", {})
            rec = {
                "id": str(uuid.uuid4()), "chain_id": chain_id, "attempt_no": attempt_no,
                "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mode": mode, "prompt": prompt or "", "transcript": transcript,
                "challenge_mode": int(bool(challenge_mode)), "duration_seconds": duration_seconds,
                "provider": analysis.get("provider"), "overall": analysis.get("overall_score"),
                **{d: s.get(d) for d in DIMENSIONS},
                "filler_count": m.get("filler_count", analysis.get("filler_count")),
                "word_count": m.get("word_count", analysis.get("word_count")),
                "wpm": m.get("wpm"),
                "analysis_json": json.dumps(analysis),
                "kind": kind or "free", "lesson_id": lesson_id,
                "lesson_passed": None if lesson_passed is None else int(bool(lesson_passed)),
                "audio_path": audio_path, "user_id": user_id, "job_id": job_id,
                **{k: (analysis.get("provenance") or {}).get(k)
                   for k in ("score_basis", "scoring_version", "model", "rubric_version", "transcript_source")},
            }
            cols = ",".join(rec)
            c.execute(f"INSERT INTO attempts ({cols}) VALUES ({','.join('?' * len(rec))})", list(rec.values()))
            if not rec["scoring_version"]:         # imported / pre-provenance data → explicit legacy labels now
                self._backfill_provenance(c)
        return self.get(rec["id"], user_id)

    DELETED_MSG = "This take was deleted. Start a new take to analyze it again."

    @contextmanager
    def transaction(self, user_id=None):
        """One write transaction (``BEGIN IMMEDIATE``) for read-check-write logic that must not interleave with a
        deletion or another writer: everything read inside stays true until commit. Don't call other Store
        methods inside (they take the same lock); use the yielded ``Txn``."""
        with _lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            yield Txn(c, user_id)

    def delete(self, attempt_id: str, user_id=None, after=None) -> bool:
        """Delete one take and everything that still holds its content, in one transaction (user-scoped):
        - the job that saved it keeps its quota accounting but loses its request and result (marked 'deleted'),
          so polling, job listing and idempotency replay can't return the take or silently re-create it;
        - other jobs' results drop retry comparisons made against it; cached comparison narratives go;
        - ``after(txn)`` runs in the same transaction once the row is gone (e.g. rebuilding learning state)."""
        with self.transaction(user_id) as tx:
            c = tx.c
            row = c.execute("SELECT job_id FROM attempts WHERE id=? AND user_id IS ?", (attempt_id, user_id)).fetchone()
            if not row:
                return False
            now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            c.execute("""UPDATE jobs SET result_json=NULL, request_json=?, attempt_id=NULL, audio_tmp=NULL,
                                         error=?, error_code='deleted', updated_at=?
                         WHERE user_id IS ? AND (attempt_id=? OR id IS ?)""",
                      (json.dumps({"redacted": True}), self.DELETED_MSG, now, user_id, attempt_id, row["job_id"]))
            for j in c.execute("SELECT id, result_json FROM jobs WHERE user_id IS ? AND result_json LIKE ?",
                               (user_id, f"%{attempt_id}%")).fetchall():
                res = json.loads(j["result_json"])
                for k in ("comparison", "first_vs_latest"):
                    cmp = res.get(k) or {}
                    if attempt_id in (cmp.get("base_id"), cmp.get("current_id")):
                        res[k] = None
                c.execute("UPDATE jobs SET result_json=? WHERE id=?", (json.dumps(res), j["id"]))
            c.execute("DELETE FROM comparisons WHERE (base_id=? OR current_id=?) AND user_id IS ?",
                      (attempt_id, attempt_id, user_id))
            deleted = c.execute("DELETE FROM attempts WHERE id=? AND user_id IS ?", (attempt_id, user_id)).rowcount > 0
            if deleted and after:
                after(tx)
            return deleted

    # ---------- reads ----------
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["analysis"] = json.loads(d.pop("analysis_json") or "{}")
        d["challenge_mode"] = bool(d["challenge_mode"])
        return d

    def get(self, attempt_id: str, user_id=None) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM attempts WHERE id=? AND user_id IS ?", (attempt_id, user_id)).fetchone()
        return self._row(r) if r else None

    def attempt_for_job(self, job_id: str, user_id=None) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM attempts WHERE job_id=? AND user_id IS ?", (job_id, user_id)).fetchone()
        return self._row(r) if r else None

    def chain(self, chain_id: str, user_id=None) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM attempts WHERE chain_id=? AND user_id IS ? ORDER BY attempt_no",
                             (chain_id, user_id)).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 200, mode: str | None = None, user_id=None) -> list[dict]:
        q, args = "SELECT * FROM attempts WHERE user_id IS ?", [user_id]
        if mode:
            q += " AND mode=?"
            args.append(mode)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [self._row(r) for r in c.execute(q, args).fetchall()]

    def all_chronological(self, user_id=None) -> list[dict]:
        with self._conn() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM attempts WHERE user_id IS ? ORDER BY created_at",
                                                    (user_id,)).fetchall()]

    def lesson_stats(self, user_id=None) -> dict:
        """lesson_id -> {attempts, best, passed, last_at}"""
        q = """SELECT lesson_id, COUNT(*) n, MAX(overall) best, MAX(COALESCE(lesson_passed,0)) passed,
                      MAX(created_at) last_at FROM attempts WHERE lesson_id IS NOT NULL AND user_id IS ?
               GROUP BY lesson_id"""
        with self._conn() as c:
            return {r["lesson_id"]: {"attempts": r["n"], "best": r["best"], "passed": bool(r["passed"]),
                                     "last_at": r["last_at"]} for r in c.execute(q, (user_id,))}

    def since(self, iso_utc: str, user_id=None) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM attempts WHERE created_at >= ? AND user_id IS ? ORDER BY created_at",
                             (iso_utc, user_id)).fetchall()
        return [self._row(r) for r in rows]

    def lesson_pass_times(self, since_iso: str, after_row: int, user_id=None) -> dict:
        """lesson_id -> time of the first passing take saved after a marker (`since_iso` + `last_row()` taken
        together). Timestamps are per-second, so within the marker's own second the row id decides."""
        q = """SELECT lesson_id, MIN(created_at) t FROM attempts
               WHERE lesson_passed=1 AND user_id IS ?
                 AND (created_at > ? OR (created_at = ? AND rowid > ?)) GROUP BY lesson_id"""
        with self._conn() as c:
            return {r["lesson_id"]: r["t"] for r in c.execute(q, (user_id, since_iso, since_iso, after_row))}

    def last_row(self) -> int:
        """A position marker: every take saved after this call gets a larger rowid."""
        with self._conn() as c:
            return c.execute("SELECT COALESCE(MAX(rowid), 0) FROM attempts").fetchone()[0]

    def count_since(self, iso_utc: str, user_id) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM attempts WHERE created_at >= ? AND user_id IS ?",
                             (iso_utc, user_id)).fetchone()[0]

    def meta_get(self, key: str):
        with self._conn() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None

    def meta_set(self, key: str, value):
        with _lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    # ---------- users & sessions ----------
    def create_user(self, email: str, password_hash: str, name: str | None = None) -> dict:
        """Create an account. The very first account inherits any attempts recorded before accounts existed."""
        uid = str(uuid.uuid4())
        with _lock, self._conn() as c:
            first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            c.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?,?,?,?,?)",
                      (uid, email, name, password_hash, _now()))
            if first:
                c.execute("UPDATE attempts SET user_id=? WHERE user_id IS NULL", (uid,))
                for r in c.execute("SELECT key, value FROM meta WHERE key LIKE 'plan:%'").fetchall():
                    c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"u:{uid}:{r['key']}", r["value"]))
        return self.user(uid)

    def user(self, user_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT id, email, name, created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None

    def user_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def user_by_email(self, email: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(r) if r else None

    def ensure_user(self, user_id: str, email: str) -> dict:
        """Single-user mode: a fixed local account (no password) that owns all practice."""
        if not self.user(user_id):
            with _lock, self._conn() as c:
                c.execute("INSERT OR IGNORE INTO users (id, email, name, password_hash, created_at) VALUES (?,?,?,?,?)",
                          (user_id, email, "You", "!", _now()))
                c.execute("UPDATE attempts SET user_id=? WHERE user_id IS NULL", (user_id,))
        return self.user(user_id)

    def delete_user(self, user_id: str) -> list[str]:
        """Delete an account and all its practice. Returns the audio file names to remove from disk."""
        with _lock, self._conn() as c:
            audio = [r[0] for r in c.execute("SELECT audio_path FROM attempts WHERE user_id=? AND audio_path IS NOT NULL",
                                             (user_id,))]
            audio += [r[0] for r in c.execute("SELECT audio_tmp FROM jobs WHERE user_id=? AND audio_tmp IS NOT NULL",
                                              (user_id,))]
            c.execute("DELETE FROM attempts WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM jobs WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM usage WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM comparisons WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM meta WHERE key LIKE ?", (f"u:{user_id}:%",))
            c.execute("DELETE FROM users WHERE id=?", (user_id,))
        return audio

    def add_session(self, token_hash: str, user_id: str, expires_at: str):
        with _lock, self._conn() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
            c.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token_hash, user_id, _now(), expires_at))

    def session_user(self, token_hash: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("""SELECT u.id, u.email, u.name, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id
                             WHERE s.token_hash=? AND s.expires_at > ?""", (token_hash, _now())).fetchone()
        return dict(r) if r else None

    def delete_session(self, token_hash: str):
        with _lock, self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    # ---------- cached comparison narratives ----------
    def get_comparison(self, base_id: str, current_id: str, user_id=None) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT narrative_json FROM comparisons WHERE base_id=? AND current_id=? AND user_id IS ?",
                          (base_id, current_id, user_id)).fetchone()
        return json.loads(r[0]) if r else None

    def put_comparison(self, base_id: str, current_id: str, narrative: dict, user_id=None):
        """Cache a narrative — only while both takes still exist for this user. One statement, so it is atomic
        with respect to a deletion (which removes the take and its cached narratives in one transaction)."""
        with _lock, self._conn() as c:
            c.execute("""INSERT OR REPLACE INTO comparisons
                         SELECT ?, ?, ?, ?, ?
                         WHERE EXISTS (SELECT 1 FROM attempts WHERE id=? AND user_id IS ?)
                           AND EXISTS (SELECT 1 FROM attempts WHERE id=? AND user_id IS ?)""",
                      (base_id, current_id, user_id, json.dumps(narrative), _now(),
                       base_id, user_id, current_id, user_id))

    def for_user(self, user_id: str) -> "UserStore":
        return UserStore(self, user_id)

    # ---------- legacy ----------
    def _import_legacy(self, path: Path):
        """One-time import of the v1 MVP's sessions.json so old practice shows up in progress."""
        if not path.exists():
            return
        with self._conn() as c:
            if c.execute("SELECT value FROM meta WHERE key='legacy_imported'").fetchone():
                return
        try:
            records = json.loads(path.read_text())
        except Exception:
            records = []
        n = 0
        for r in records if isinstance(records, list) else []:
            a = r.get("analysis") or {}
            if not r.get("transcript"):
                continue
            a.setdefault("scores", {"structure": a.get("structure_score"), "clarity": a.get("clarity_score")})
            a.setdefault("metrics", {"word_count": a.get("word_count"), "filler_count": a.get("filler_count")})
            a.setdefault("provider", "legacy-v1")
            self.add_attempt(mode=r.get("mode", "Interview"), prompt="", transcript=r["transcript"],
                             challenge_mode=False, duration_seconds=None, analysis=a,
                             created_at=r.get("created_at"))
            n += 1
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES ('legacy_imported', ?)", (str(n),))


class Txn:
    """Reads and writes inside one ``Store.transaction`` (same connection, user-scoped like ``UserStore``)."""

    def __init__(self, conn, user_id):
        self.c, self.user_id = conn, user_id

    def _key(self, key):
        return key if self.user_id is None else f"u:{self.user_id}:{key}"

    def attempt_exists(self, attempt_id: str) -> bool:
        return self.c.execute("SELECT 1 FROM attempts WHERE id=? AND user_id IS ?",
                              (attempt_id, self.user_id)).fetchone() is not None

    def all_chronological(self) -> list[dict]:
        return [Store._row(r) for r in self.c.execute(
            "SELECT * FROM attempts WHERE user_id IS ? ORDER BY created_at", (self.user_id,)).fetchall()]

    def meta_get(self, key):
        r = self.c.execute("SELECT value FROM meta WHERE key=?", (self._key(key),)).fetchone()
        return json.loads(r["value"]) if r else None

    def meta_set(self, key, value):
        self.c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (self._key(key), json.dumps(value)))


class UserStore:
    """The slice of the store that belongs to one user. Same read/write API as ``Store`` for attempts, so the
    curriculum and insights code works unchanged, but every query is filtered by ``user_id``."""

    def __init__(self, store: Store, user_id: str):
        self._s, self.user_id = store, user_id

    def add_attempt(self, **kw) -> dict:
        return self._s.add_attempt(**kw, user_id=self.user_id)

    def delete(self, attempt_id, after=None):
        return self._s.delete(attempt_id, self.user_id, after)

    def transaction(self):
        return self._s.transaction(self.user_id)

    def get(self, attempt_id):
        return self._s.get(attempt_id, self.user_id)

    def attempt_for_job(self, job_id):
        return self._s.attempt_for_job(job_id, self.user_id)

    def chain(self, chain_id):
        return self._s.chain(chain_id, self.user_id)

    def recent(self, limit=200, mode=None):
        return self._s.recent(limit, mode, self.user_id)

    def all_chronological(self):
        return self._s.all_chronological(self.user_id)

    def lesson_stats(self):
        return self._s.lesson_stats(self.user_id)

    def since(self, iso_utc):
        return self._s.since(iso_utc, self.user_id)

    def lesson_pass_times(self, since_iso, after_row):
        return self._s.lesson_pass_times(since_iso, after_row, self.user_id)

    def last_row(self):
        return self._s.last_row()

    def count_since(self, iso_utc):
        return self._s.count_since(iso_utc, self.user_id)

    def get_comparison(self, base_id, current_id):
        return self._s.get_comparison(base_id, current_id, self.user_id)

    def put_comparison(self, base_id, current_id, narrative):
        return self._s.put_comparison(base_id, current_id, narrative, self.user_id)

    def meta_get(self, key):
        return self._s.meta_get(f"u:{self.user_id}:{key}")

    def meta_set(self, key, value):
        return self._s.meta_set(f"u:{self.user_id}:{key}", value)
