import sqlite3
from pathlib import Path

import pytest

from newsletter_podcast.state import State


def test_processed_messages_are_idempotent(tmp_path: Path):
    state = State(tmp_path / "state.db")
    assert not state.is_processed("m1")
    state.mark_processed(["m1", "m2"])
    state.mark_processed(["m2", "m3"])  # re-running must not duplicate or fail
    assert state.is_processed("m1")
    assert state.is_processed("m2")
    assert state.is_processed("m3")
    assert state.processed_count() == 3
    state.close()


def test_episode_lifecycle_and_resume(tmp_path: Path):
    state = State(tmp_path / "state.db")
    episode = state.get_or_create_episode("2026-10-25")
    assert episode["status"] == "pending"
    assert episode["doc_path"] is None

    state.update_episode("2026-10-25", status="fetched", doc_path="/x/doc.md")
    again = state.get_or_create_episode("2026-10-25")  # must not create a duplicate
    assert again["status"] == "fetched"
    assert again["doc_path"] == "/x/doc.md"

    state.update_episode("2026-10-25", status="generated", audio_path="/x/ep.mp3")
    state.update_episode("2026-10-25", error="generate: boom")
    row = state.get_episode("2026-10-25")
    assert row["status"] == "generated"
    assert row["audio_path"] == "/x/ep.mp3"
    assert row["error"] == "generate: boom"

    state.update_episode("2026-10-25", status="sent", error=None)
    row = state.get_episode("2026-10-25")
    assert row["status"] == "sent"
    assert row["error"] is None
    state.close()


def test_unknown_episode_field_rejected(tmp_path: Path):
    state = State(tmp_path / "state.db")
    state.get_or_create_episode("2026-10-25")
    with pytest.raises(ValueError):
        state.update_episode("2026-10-25", nonsense="x")
    state.close()


def test_list_episodes_orders_newest_first(tmp_path: Path):
    state = State(tmp_path / "state.db")
    for key in ("2026-10-11", "2026-10-18", "2026-10-25"):
        state.get_or_create_episode(key)
    weeks = [ep["week_of"] for ep in state.list_episodes()]
    assert weeks == ["2026-10-25", "2026-10-18", "2026-10-11"]
    state.close()


def test_migration_upgrades_a_pre_trash_schema(tmp_path: Path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE processed_messages (
            message_id   TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
        CREATE TABLE episodes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            week_of    TEXT NOT NULL UNIQUE,
            status     TEXT NOT NULL DEFAULT 'pending',
            doc_path   TEXT,
            audio_path TEXT,
            error      TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO processed_messages VALUES ('legacy', '2026-10-25T01:21:00+00:00');
        INSERT INTO episodes (week_of, status, created_at, updated_at)
            VALUES ('2026-10-25', 'sent',
                    '2026-10-25T01:21:00+00:00', '2026-10-25T01:21:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    state = State(db)
    # the legacy row is backfilled onto the episode it actually fed
    assert state.pending_trash("2026-10-25") == ["legacy"]
    state.mark_trashed(["legacy"])
    assert state.pending_trash("2026-10-25") == []
    state.close()

    # reopening runs the migration again without breaking anything
    reopened = State(db)
    assert reopened.pending_trash("2026-10-25") == []
    reopened.close()
