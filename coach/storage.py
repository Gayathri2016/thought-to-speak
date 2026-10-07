"""Local SQLite storage for attempts. One row per spoken attempt; retries share a chain_id."""
import json
import sqlite3
import threading
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
"""


class Store:
    def __init__(self, db_path: Path, legacy_json: Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)
        if legacy_json:
            self._import_legacy(Path(legacy_json))

    @staticmethod
    def _migrate(c):
        """v3: attempts can belong to a lesson / daily-plan item."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(attempts)")}
        for name, ddl in (("kind", "TEXT NOT NULL DEFAULT 'free'"), ("lesson_id", "TEXT"),
                          ("lesson_passed", "INTEGER"), ("audio_path", "TEXT")):
            if name not in cols:
                c.execute(f"ALTER TABLE attempts ADD COLUMN {name} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_attempts_lesson ON attempts(lesson_id)")

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---------- writes ----------
    def add_attempt(self, *, mode, prompt, transcript, challenge_mode, duration_seconds, analysis,
                    chain_id=None, created_at=None, kind="free", lesson_id=None, lesson_passed=None,
                    audio_path=None) -> dict:
        with _lock, self._conn() as c:
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
                "audio_path": audio_path,
            }
            cols = ",".join(rec)
            c.execute(f"INSERT INTO attempts ({cols}) VALUES ({','.join('?' * len(rec))})", list(rec.values()))
        return self.get(rec["id"])

    def delete(self, attempt_id: str) -> bool:
        with _lock, self._conn() as c:
            return c.execute("DELETE FROM attempts WHERE id=?", (attempt_id,)).rowcount > 0

    # ---------- reads ----------
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["analysis"] = json.loads(d.pop("analysis_json") or "{}")
        d["challenge_mode"] = bool(d["challenge_mode"])
        return d

    def get(self, attempt_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        return self._row(r) if r else None

    def chain(self, chain_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM attempts WHERE chain_id=? ORDER BY attempt_no", (chain_id,)).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 200, mode: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM attempts", []
        if mode:
            q += " WHERE mode=?"
            args.append(mode)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [self._row(r) for r in c.execute(q, args).fetchall()]

    def all_chronological(self) -> list[dict]:
        with self._conn() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM attempts ORDER BY created_at").fetchall()]

    def lesson_stats(self) -> dict:
        """lesson_id -> {attempts, best, passed, last_at}"""
        q = """SELECT lesson_id, COUNT(*) n, MAX(overall) best, MAX(COALESCE(lesson_passed,0)) passed,
                      MAX(created_at) last_at FROM attempts WHERE lesson_id IS NOT NULL GROUP BY lesson_id"""
        with self._conn() as c:
            return {r["lesson_id"]: {"attempts": r["n"], "best": r["best"], "passed": bool(r["passed"]),
                                     "last_at": r["last_at"]} for r in c.execute(q)}

    def since(self, iso_utc: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM attempts WHERE created_at >= ? ORDER BY created_at", (iso_utc,)).fetchall()
        return [self._row(r) for r in rows]

    def meta_get(self, key: str):
        with self._conn() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None

    def meta_set(self, key: str, value):
        with _lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

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
