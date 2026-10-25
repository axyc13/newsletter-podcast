"""SQLite state: processed message IDs and episode lifecycle.

Episode lifecycle:  pending -> fetched -> generated -> sent
A failure keeps the furthest status reached and records the error text.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id   TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    week_of      TEXT NOT NULL DEFAULT '',
    trashed_at   TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week_of    TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'pending',
    doc_path   TEXT,
    audio_path TEXT,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

EPISODE_STATUSES = ("pending", "fetched", "generated", "sent")
_ALLOWED_FIELDS = {"status", "doc_path", "audio_path", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Upgrade DBs created before trash-tracking; backfill week_of once."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(processed_messages)")}
        if "week_of" not in cols:
            self.conn.execute(
                "ALTER TABLE processed_messages ADD COLUMN week_of TEXT NOT NULL DEFAULT ''"
            )
        if "trashed_at" not in cols:
            self.conn.execute("ALTER TABLE processed_messages ADD COLUMN trashed_at TEXT")
        latest = self.conn.execute(
            "SELECT week_of FROM episodes ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if latest is not None:
            # rows recorded before this feature belong to the most recent episode
            self.conn.execute(
                "UPDATE processed_messages SET week_of = ? WHERE week_of = ''",
                (latest["week_of"],),
            )
        self.conn.commit()

    # -- processed messages ---------------------------------------------------

    def is_processed(self, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_processed(self, message_ids: Iterable[str], week_of: str = "") -> None:
        now = _now()
        self.conn.executemany(
            "INSERT OR IGNORE INTO processed_messages (message_id, processed_at, week_of) "
            "VALUES (?, ?, ?)",
            [(mid, now, week_of) for mid in message_ids],
        )
        self.conn.commit()

    def pending_trash(self, week_of: str) -> list[str]:
        """Message ids ingested for this episode that haven't been moved to Trash."""
        rows = self.conn.execute(
            "SELECT message_id FROM processed_messages "
            "WHERE week_of = ? AND trashed_at IS NULL ORDER BY rowid",
            (week_of,),
        ).fetchall()
        return [row["message_id"] for row in rows]

    def mark_trashed(self, message_ids: Iterable[str]) -> None:
        now = _now()
        self.conn.executemany(
            "UPDATE processed_messages SET trashed_at = ? WHERE message_id = ?",
            [(now, mid) for mid in message_ids],
        )
        self.conn.commit()

    def processed_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM processed_messages").fetchone()[0])

    # -- episodes -------------------------------------------------------------

    def get_episode(self, week_of: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM episodes WHERE week_of = ?", (week_of,)
        ).fetchone()

    def get_or_create_episode(self, week_of: str) -> sqlite3.Row:
        existing = self.get_episode(week_of)
        if existing is not None:
            return existing
        now = _now()
        self.conn.execute(
            "INSERT INTO episodes (week_of, status, created_at, updated_at) "
            "VALUES (?, 'pending', ?, ?)",
            (week_of, now, now),
        )
        self.conn.commit()
        return self.get_episode(week_of)

    def update_episode(self, week_of: str, **fields) -> None:
        unknown = set(fields) - _ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"unknown episode fields: {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{name} = ?" for name in fields)
        params = list(fields.values())
        self.conn.execute(
            f"UPDATE episodes SET {sets}, updated_at = ? WHERE week_of = ?",
            [*params, _now(), week_of],
        )
        self.conn.commit()

    def list_episodes(self, limit: int = 12) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM episodes ORDER BY week_of DESC LIMIT ?", (limit,)
        ).fetchall()
        return list(rows)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
