"""CLI entry point: newsletter-podcast {auth,notebooklm-login,fetch,generate,send,run,status}."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .state import State

log = logging.getLogger("newsletter_podcast")


def _setup_logging(cfg: Config, verbose: bool) -> None:
    cfg.ensure_dirs()
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
        file_handler = logging.FileHandler(cfg.logs_dir / "run.log")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


def recent_sunday(today: date | None = None) -> date:
    """The Sunday of the current run window (a Sunday run and its Monday retry share a key)."""
    today = today or datetime.now(ZoneInfo("Pacific/Auckland")).date()
    return today - timedelta(days=(today.weekday() + 1) % 7)


def _doc_path(cfg: Config, week_of: date) -> Path:
    return cfg.output_dir / f"newsletter-{week_of.isoformat()}.md"


def do_fetch(cfg: Config, state: State, week_of: date, dry_run: bool = False):
    """Fetch unseen emails, clean them, merge into this week's document.

    Returns FetchResult; .path is None when there is nothing to process this week.
    """
    from .clean import EmailSection, html_to_markdown, merge_emails, plain_text_to_markdown
    from .gmail_fetch import fetch_new_messages

    messages, empty_ids = fetch_new_messages(cfg, state.is_processed)
    log.info(
        "fetched %d new messages (%d with no body) from label %r",
        len(messages), len(empty_ids), cfg.gmail_label,
    )

    sections: list[EmailSection] = []
    totals: dict[str, int] = {}
    for message in messages:
        if message["html"]:
            body, stats = html_to_markdown(message["html"])
        else:
            body, stats = plain_text_to_markdown(message["text"]), {}
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
        sections.append(
            EmailSection(
                message["id"], message["subject"], message["sender"],
                message["date"], body,
            )
        )

    doc_file = _doc_path(cfg, week_of)
    if not sections and not doc_file.exists():
        log.info("no newsletters this week; nothing to do")
        return None, "", totals

    rendered = merge_emails(sections, week_of) if sections else ""
    if dry_run:
        return None, rendered, totals

    if sections:
        if doc_file.exists():
            # Keep this week's existing document and append the new sections.
            existing = doc_file.read_text(encoding="utf-8").rstrip()
            appended = rendered.split("\n", 2)[-1].strip()
            doc_file.write_text(existing + "\n\n" + appended + "\n", encoding="utf-8")
        else:
            doc_file.parent.mkdir(parents=True, exist_ok=True)
            doc_file.write_text(rendered, encoding="utf-8")
        state.mark_processed([m["id"] for m in messages] + empty_ids, week_of.isoformat())

    key = week_of.isoformat()
    episode = state.get_or_create_episode(key)
    if episode["status"] == "pending" and doc_file.exists():
        state.update_episode(
            key, status="fetched", doc_path=str(doc_file), error=None
        )
        episode = state.get_episode(key)
    return doc_file, rendered, totals


def do_generate(
    cfg: Config, state: State, week_of: date, doc: Path | None = None
) -> Path:
    from .audio.base import get_audio_generator

    key = week_of.isoformat()
    episode = state.get_episode(key)
    if doc is None and episode and episode["doc_path"]:
        doc = Path(episode["doc_path"])
    doc = doc or _doc_path(cfg, week_of)
    if not doc.exists():
        raise FileNotFoundError(
            f"weekly document not found: {doc} (run `newsletter-podcast fetch` first)"
        )

    generator = get_audio_generator(cfg)
    log.info("generating audio with backend %r from %s", generator.name, doc)
    audio_file = generator.generate(doc, week_of)

    state.get_or_create_episode(key)
    episode = state.get_episode(key)
    if episode["status"] != "sent":
        state.update_episode(
            key, status="generated", doc_path=str(doc),
            audio_path=str(audio_file), error=None,
        )
    trash_ingested(cfg, state, week_of)
    return audio_file


def do_send(
    cfg: Config, state: State, week_of: date, audio: Path | None = None
) -> str:
    from .audio.base import get_audio_generator
    from .gmail_fetch import get_gmail_service
    from .notify import send_episode

    key = week_of.isoformat()
    episode = state.get_episode(key)
    if audio is None and episode and episode["audio_path"]:
        audio = Path(episode["audio_path"])
    if audio is None or not audio.exists():
        raise FileNotFoundError(
            "no generated audio found (run `newsletter-podcast generate` first)"
        )

    service = get_gmail_service(cfg)
    backend = get_audio_generator(cfg).name
    message_id = send_episode(service, cfg, week_of, audio, backend)

    state.get_or_create_episode(key)
    if state.get_episode(key)["status"] != "sent":
        state.update_episode(key, status="sent", audio_path=str(audio), error=None)
    trash_ingested(cfg, state, week_of)
    return message_id


def trash_ingested(cfg: Config, state: State, week_of: date) -> None:
    """Move this week's ingested source emails to Gmail Trash (best-effort).

    Called after generation (sources are now safe in NotebookLM) and again
    after send to retry per-message failures. Never raises: ingestion is
    already complete at this point, so a trash problem only logs a warning.
    """
    if not cfg.trash_ingested:
        return
    try:
        from .gmail_fetch import get_gmail_service, trash_messages

        pending = state.pending_trash(week_of.isoformat())
        if not pending:
            return
        trashed, failed = trash_messages(get_gmail_service(cfg), pending)
        state.mark_trashed(trashed)
        if trashed:
            log.info(
                "[trash] moved %d ingested newsletter(s) to Trash for week %s",
                len(trashed),
                week_of,
            )
        if failed:
            log.warning(
                "[trash] %d message(s) not trashed yet (will retry): %s",
                len(failed),
                ", ".join(failed),
            )
    except Exception:
        log.exception("[trash] trash step failed (non-fatal; sources already ingested)")


def cmd_run(cfg: Config, state: State, args) -> int:
    """Full pipeline, resumable: whatever step the episode hasn't finished, do next."""
    from .gmail_fetch import get_gmail_service
    from .notify import send_failure

    week_of = recent_sunday()
    key = week_of.isoformat()
    episode = state.get_or_create_episode(key)
    step = "startup"

    try:
        if episode["status"] == "sent":
            log.info("episode %s already sent; nothing to do", key)
            return 0

        step = "fetch"
        if not episode["doc_path"] or not Path(episode["doc_path"]).exists():
            doc, _, _ = do_fetch(cfg, state, week_of)
            if doc is None:
                log.info("no newsletters this week; episode %s skipped", key)
                return 0
            episode = state.get_episode(key)

        step = "generate"
        if not episode["audio_path"] or not Path(episode["audio_path"]).exists():
            do_generate(cfg, state, week_of)
            episode = state.get_episode(key)

        step = "send"
        do_send(cfg, state, week_of)
        log.info("episode %s complete: delivered to your inbox", key)
        return 0

    except Exception as exc:
        log.exception("pipeline failed at step %r", step)
        try:
            state.update_episode(key, error=f"{step}: {exc}")
        except Exception:
            log.exception("could not record failure in state")
        try:
            send_failure(get_gmail_service(cfg), cfg, week_of, step, str(exc))
        except Exception:
            log.exception("failure alert skipped (Gmail unavailable)")
        return 1


def cmd_auth(cfg: Config, state: State, args) -> int:
    from .gmail_fetch import run_oauth_flow

    run_oauth_flow(cfg)
    print(f"Google auth OK. Token stored at {cfg.token_file}")
    print("Next: .venv/bin/notebooklm login   (NotebookLM cookie login)")
    return 0


def cmd_notebooklm_login(cfg: Config, state: State, args) -> int:
    cli_path = Path(sys.executable).parent / "notebooklm"
    if not cli_path.exists():
        raise SystemExit(
            "notebooklm CLI not found in this environment (pip install notebooklm-py)"
        )
    return subprocess.call([str(cli_path), "login"])


def cmd_fetch(cfg: Config, state: State, args) -> int:
    week_of = recent_sunday()
    doc, rendered, totals = do_fetch(cfg, state, week_of, dry_run=args.dry_run)
    if args.dry_run:
        print(rendered if rendered else "(nothing new this week)")
        if totals:
            print(f"\n[stats] removals: {totals}", file=sys.stderr)
        return 0
    if doc is None:
        print("Nothing to process this week.")
    else:
        print(f"Saved: {doc}")
        if totals:
            print(f"[stats] removals: {totals}", file=sys.stderr)
    return 0


def cmd_generate(cfg: Config, state: State, args) -> int:
    audio = do_generate(cfg, state, recent_sunday(), doc=args.doc)
    print(f"Audio ready: {audio}")
    return 0


def cmd_send(cfg: Config, state: State, args) -> int:
    message_id = do_send(cfg, state, recent_sunday(), audio=args.audio)
    print(f"Episode emailed (message id {message_id})")
    return 0


def cmd_status(cfg: Config, state: State, args) -> int:
    print(f"Processed messages: {state.processed_count()}")
    episodes = state.list_episodes()
    if not episodes:
        print("No episodes yet.")
        return 0
    print(f"{'WEEK':<12} {'STATUS':<10} DOC / AUDIO / ERROR")
    for ep in episodes:
        print(
            f"{ep['week_of']:<12} {ep['status']:<10} "
            f"doc={ep['doc_path'] or '-'}  audio={ep['audio_path'] or '-'}"
            + (f"  error={ep['error']}" if ep["error"] else "")
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newsletter-podcast",
        description="Weekly Gmail newsletters -> cleaned doc -> podcast -> email",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="one-time Google OAuth login (gmail modify+send, drive)")
    sub.add_parser("notebooklm-login", help="one-time NotebookLM cookie login")

    fetch = sub.add_parser("fetch", help="fetch, clean and merge this week's newsletters")
    fetch.add_argument(
        "--dry-run", action="store_true",
        help="print the merged result without saving it or marking messages processed",
    )

    gen = sub.add_parser("generate", help="generate episode audio from the saved document")
    gen.add_argument("--doc", type=Path, default=None, help="override weekly document path")

    send = sub.add_parser("send", help="email the generated episode to yourself")
    send.add_argument("--audio", type=Path, default=None, help="override audio path")

    sub.add_parser("run", help="full pipeline: fetch -> generate -> send (resumable)")
    sub.add_parser("status", help="show processed counts and episode states")
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.from_env()
    _setup_logging(cfg, args.verbose)

    handlers = {
        "auth": cmd_auth,
        "notebooklm-login": cmd_notebooklm_login,
        "fetch": cmd_fetch,
        "generate": cmd_generate,
        "send": cmd_send,
        "run": cmd_run,
        "status": cmd_status,
    }
    with State(cfg.state_db) as state:
        return handlers[args.command](cfg, state, args)


if __name__ == "__main__":
    raise SystemExit(cli())
