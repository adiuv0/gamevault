"""The app's memory of what it has already handled, one database per server.

Each file is remembered by path, size and modified time, so a file that hasn't
changed is never read or hashed again, and a file that reached the server is
never sent again, even if it is later deleted there on purpose.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SYNCED = "synced"  # on the server (uploaded, or it was already there)
REJECTED = "rejected"  # the server refused it; retried only if the file changes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    status TEXT,
    detail TEXT,
    -- Uploads that failed for a reason that may pass (a server error, an
    -- unknown error): retried later and later, so one broken file isn't sent
    -- again every minute forever.
    failures INTEGER NOT NULL DEFAULT 0,
    retry_at REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);
CREATE TABLE IF NOT EXISTS games (
    key TEXT PRIMARY KEY,
    server_id INTEGER,
    name TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    game TEXT NOT NULL,
    filename TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_HISTORY_KEEP = 300
# After the 1st, 2nd, 3rd… failure: 1 min, 4 min, 16 min, about 1 h, 4 h, then every 6 h.
_RETRY_BASE_SECONDS = 60
_RETRY_MAX_SECONDS = 6 * 3600


@dataclass(frozen=True)
class FileRecord:
    size: int
    mtime_ns: int
    sha256: str | None
    status: str | None
    detail: str | None
    retry_at: float | None = None


@dataclass(frozen=True)
class HistoryEntry:
    at: float
    game: str
    filename: str
    outcome: str
    detail: str | None


class SyncState:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except BaseException:
            self._db.close()
            raise

    @classmethod
    def open(cls, path: Path | str) -> SyncState:
        """Open the memory at ``path``, starting a fresh one if the file is damaged.

        Losing it only costs time: files are read again and checked with the
        server, which still has every hash. The damaged file is kept, renamed.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            state = cls(path)
            ok = state._db.execute("PRAGMA quick_check").fetchone()
            if ok and ok[0] == "ok":
                return state
            state.close()
        except sqlite3.DatabaseError:
            pass
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log.warning("the sync memory at %s is damaged; starting a new one", path)
        for suffix in ("", "-wal", "-shm"):
            damaged = Path(f"{path}{suffix}")
            if damaged.exists():
                damaged.replace(Path(f"{path}.corrupt-{stamp}{suffix}"))
        return cls(path)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def forget_sync_status(self) -> None:
        """Re-check every file against the server on the next sync (hashes are kept)."""
        with self._lock:
            self._db.execute("UPDATE files SET status = NULL, detail = NULL, failures = 0, retry_at = NULL")
            self._db.execute("DELETE FROM games")
            self._db.commit()

    # ── Files ────────────────────────────────────────────────────────────────

    def lookup(self, paths: list[str]) -> dict[str, FileRecord]:
        out: dict[str, FileRecord] = {}
        with self._lock:
            for start in range(0, len(paths), 500):
                chunk = paths[start:start + 500]
                marks = ",".join("?" * len(chunk))
                for row in self._db.execute(
                    f"""SELECT path, size, mtime_ns, sha256, status, detail, retry_at
                        FROM files WHERE path IN ({marks})""",
                    chunk,
                ):
                    out[row[0]] = FileRecord(*row[1:])
        return out

    def remember_hashes(self, items: list[tuple[str, int, int, str]]) -> None:
        """Store ``(path, size, mtime_ns, sha256)``; a changed file loses its status."""
        now = time.time()
        with self._lock:
            self._db.executemany(
                """INSERT INTO files (path, size, mtime_ns, sha256, status, detail, updated_at)
                   VALUES (?, ?, ?, ?, NULL, NULL, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       size = excluded.size, mtime_ns = excluded.mtime_ns,
                       sha256 = excluded.sha256, status = NULL, detail = NULL,
                       failures = 0, retry_at = NULL, updated_at = excluded.updated_at""",
                [(p, s, m, h, now) for p, s, m, h in items],
            )
            self._db.commit()

    def mark(self, paths: list[str], status: str | None, detail: str | None = None) -> None:
        now = time.time()
        with self._lock:
            self._db.executemany(
                """UPDATE files SET status = ?, detail = ?, failures = 0, retry_at = NULL, updated_at = ?
                   WHERE path = ?""",
                [(status, detail, now, p) for p in paths],
            )
            self._db.commit()

    def mark_failed(self, paths: list[str], detail: str | None) -> None:
        """An upload failed in a way that may pass; try again later, then later still."""
        now = time.time()
        with self._lock:
            self._db.executemany(
                f"""UPDATE files SET detail = ?, failures = failures + 1, updated_at = ?,
                       retry_at = ? + MIN({_RETRY_MAX_SECONDS},
                                          {_RETRY_BASE_SECONDS} * (1 << (2 * MIN(failures, 8))))
                   WHERE path = ?""",
                [(detail, now, now, p) for p in paths],
            )
            self._db.commit()

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall()
        return {status or "pending": count for status, count in rows}

    # ── Games ────────────────────────────────────────────────────────────────

    def game(self, key: str) -> tuple[int | None, str | None]:
        with self._lock:
            row = self._db.execute("SELECT server_id, name FROM games WHERE key = ?", (key,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def remember_game(self, key: str, server_id: int | None, name: str | None) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO games (key, server_id, name) VALUES (?, ?, ?)",
                             (key, server_id, name))
            self._db.commit()

    def forget_game(self, key: str) -> None:
        with self._lock:
            self._db.execute("UPDATE games SET server_id = NULL WHERE key = ?", (key,))
            self._db.commit()

    def known_names(self) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT key, name FROM games WHERE name IS NOT NULL").fetchall()
        return dict(rows)

    # ── History ──────────────────────────────────────────────────────────────

    def add_history(self, game: str, filename: str, outcome: str, detail: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO history (at, game, filename, outcome, detail) VALUES (?, ?, ?, ?, ?)",
                (time.time(), game, filename, outcome, detail),
            )
            self._db.execute(
                "DELETE FROM history WHERE id <= (SELECT MAX(id) FROM history) - ?", (_HISTORY_KEEP,)
            )
            self._db.commit()

    def recent(self, limit: int = 50) -> list[HistoryEntry]:
        with self._lock:
            rows = self._db.execute(
                "SELECT at, game, filename, outcome, detail FROM history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [HistoryEntry(*row) for row in rows]

    # ── Small values ─────────────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self._db.commit()
