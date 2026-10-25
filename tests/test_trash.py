"""Trash-after-ingestion: scope, per-message results, and the main.py step."""

from datetime import date
from pathlib import Path

from newsletter_podcast.config import AUTH_SCOPES, Config
from newsletter_podcast.gmail_fetch import trash_messages
from newsletter_podcast.main import trash_ingested
from newsletter_podcast.state import State


class FakeTrashService:
    """Mimics service.users().messages().trash(...).execute()."""

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.calls: list[str] = []

    def users(self):
        return self

    def messages(self):
        return self

    def trash(self, *, userId: str, id: str):
        assert userId == "me"
        self.calls.append(id)
        fail = id in self.fail_ids

        class Req:
            def execute(_):
                if fail:
                    raise RuntimeError("403 forbidden")
                return {}

        return Req()


def test_scope_is_gmail_modify():
    assert "https://www.googleapis.com/auth/gmail.modify" in AUTH_SCOPES
    assert "https://www.googleapis.com/auth/gmail.readonly" not in AUTH_SCOPES


def test_trash_messages_separates_failures():
    svc = FakeTrashService(fail_ids={"m2"})
    trashed, failed = trash_messages(svc, ["m1", "m2", "m3"])
    assert trashed == ["m1", "m3"]
    assert failed == ["m2"]
    assert svc.calls == ["m1", "m2", "m3"]  # a failure must not stop the batch


def _state(tmp_path: Path) -> State:
    state = State(tmp_path / "state.db")
    state.mark_processed(["cur1", "cur2"], "2026-10-25")
    state.mark_processed(["prev"], "2026-10-18")
    return state


def test_trash_ingested_only_touches_its_week(tmp_path: Path, monkeypatch):
    svc = FakeTrashService()
    monkeypatch.setattr("newsletter_podcast.gmail_fetch.get_gmail_service", lambda cfg: svc)
    state = _state(tmp_path)
    trash_ingested(Config(), state, date(2026, 10, 25))
    assert svc.calls == ["cur1", "cur2"]
    assert state.pending_trash("2026-10-25") == []
    assert state.pending_trash("2026-10-18") == ["prev"]  # not this episode's job
    state.close()


def test_partial_failure_stays_pending_and_never_raises(tmp_path: Path, monkeypatch):
    svc = FakeTrashService(fail_ids={"cur2"})
    monkeypatch.setattr("newsletter_podcast.gmail_fetch.get_gmail_service", lambda cfg: svc)
    state = _state(tmp_path)
    trash_ingested(Config(), state, date(2026, 10, 25))  # must not raise
    assert state.pending_trash("2026-10-25") == ["cur2"]  # retried at send
    state.close()


def test_kill_switch_never_touches_gmail(tmp_path: Path, monkeypatch):
    def _boom(cfg):
        raise AssertionError("kill switch must not build a service")

    monkeypatch.setattr("newsletter_podcast.gmail_fetch.get_gmail_service", _boom)
    state = _state(tmp_path)
    trash_ingested(Config(trash_ingested=False), state, date(2026, 10, 25))
    assert state.pending_trash("2026-10-25") == ["cur1", "cur2"]
    state.close()


def test_service_failure_is_non_fatal(tmp_path: Path, monkeypatch):
    def _boom(cfg):
        raise RuntimeError("no usable token")

    monkeypatch.setattr("newsletter_podcast.gmail_fetch.get_gmail_service", _boom)
    state = _state(tmp_path)
    trash_ingested(Config(), state, date(2026, 10, 25))  # must not raise
    assert state.pending_trash("2026-10-25") == ["cur1", "cur2"]
    state.close()


def test_nothing_pending_short_circuits(tmp_path: Path, monkeypatch):
    def _boom(cfg):
        raise AssertionError("no pending work must not authenticate")

    monkeypatch.setattr("newsletter_podcast.gmail_fetch.get_gmail_service", _boom)
    state = State(tmp_path / "state.db")
    trash_ingested(Config(), state, date(2026, 10, 25))
    state.close()
