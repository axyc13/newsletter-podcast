"""Gmail access: one-time OAuth flow + fetching the newsletters label."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import AUTH_SCOPES, Config

log = logging.getLogger(__name__)


def run_oauth_flow(cfg: Config) -> Credentials:
    """One-time interactive login; stores a refresh token in token.json."""
    if not cfg.credentials_file.exists():
        raise SystemExit(
            f"Missing {cfg.credentials_file} - create a Google Cloud OAuth *Desktop* client "
            "and download its JSON (see README, 'One-time setup')."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.credentials_file), AUTH_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    save_token(cfg, creds)
    return creds


def save_token(cfg: Config, creds: Credentials) -> None:
    cfg.token_file.write_text(creds.to_json(), encoding="utf-8")


def load_credentials(cfg: Config) -> Credentials:
    if not cfg.token_file.exists():
        raise SystemExit("Not authenticated yet. Run: newsletter-podcast auth")
    creds = Credentials.from_authorized_user_file(str(cfg.token_file), AUTH_SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(cfg, creds)
        return creds
    raise SystemExit(
        "Google token is no longer usable. Run: rm token.json && newsletter-podcast auth"
    )


def get_gmail_service(cfg: Config):
    return build("gmail", "v1", credentials=load_credentials(cfg), cache_discovery=False)


def resolve_label_id(service, label_name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"].lower() == label_name.lower():
            return label["id"]
    available = ", ".join(sorted(l["name"] for l in labels if l.get("type") == "user"))
    raise SystemExit(f"Gmail label {label_name!r} not found. Your labels: {available}")


def list_message_ids(service, label_id: str, since: datetime) -> list[str]:
    ts = int(since.replace(tzinfo=timezone.utc).timestamp())
    ids: list[str] = []
    request = service.users().messages().list(
        userId="me", labelIds=[label_id], q=f"after:{ts}", maxResults=500
    )
    while request is not None:
        response = request.execute()
        ids.extend(m["id"] for m in response.get("messages", []))
        request = service.users().messages().list_next(request, response)
    return ids


def _decode_b64(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    """Walk the MIME tree; return (html, plain_text) preferring whichever parts exist."""
    html, text = "", ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    data = body.get("data")
    if data:
        if mime == "text/html":
            html = _decode_b64(data)
        elif mime == "text/plain":
            text = _decode_b64(data)
    for part in payload.get("parts") or []:
        part_html, part_text = _extract_bodies(part)
        html = html or part_html
        text = text or part_text
    return html, text


def get_message(service, message_id: str) -> dict[str, Any]:
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {
        h["name"].lower(): h["value"]
        for h in (message.get("payload") or {}).get("headers", [])
    }
    html, text = _extract_bodies(message.get("payload") or {})
    received = ""
    internal = int(message.get("internalDate") or 0)
    if internal:
        received = datetime.fromtimestamp(
            internal / 1000, tz=timezone.utc
        ).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return {
        "id": message["id"],
        "thread_id": message.get("threadId", ""),
        "subject": headers.get("subject", "(no subject)"),
        "sender": headers.get("from", ""),
        "date": received,
        "html": html,
        "text": text,
    }


def fetch_new_messages(
    cfg: Config, is_processed: Callable[[str], bool]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch unseen messages from the label.

    Returns (messages that have usable content, ids with no usable body so
    they can be marked processed and never retried).
    """
    since = datetime.now(timezone.utc) - timedelta(days=cfg.fetch_days)
    service = get_gmail_service(cfg)
    label_id = resolve_label_id(service, cfg.gmail_label)
    new_ids = [mid for mid in list_message_ids(service, label_id, since) if not is_processed(mid)]

    messages: list[dict[str, Any]] = []
    empty_ids: list[str] = []
    for mid in new_ids:
        message = get_message(service, mid)
        if message["html"] or message["text"]:
            messages.append(message)
        else:
            empty_ids.append(mid)
    return messages, empty_ids


def trash_messages(service, message_ids: list[str]) -> tuple[list[str], list[str]]:
    """Move messages to Gmail Trash (recoverable ~30 days). Returns (trashed, failed)."""
    trashed: list[str] = []
    failed: list[str] = []
    for mid in message_ids:
        try:
            service.users().messages().trash(userId="me", id=mid).execute()
            trashed.append(mid)
        except Exception as exc:  # per-message failure must not stop the batch
            log.warning("could not trash message %s: %s", mid, exc)
            failed.append(mid)
    return trashed, failed
