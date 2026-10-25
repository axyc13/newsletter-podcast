"""Episode delivery and failure alerts (Gmail send).

Silent failure is the main reason projects like this get abandoned, so every
failure path in the pipeline ends here.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from .config import Config
from .storage import should_attach, upload_to_drive

log = logging.getLogger(__name__)

MimeMsg = MIMEMultipart | MIMEText


def profile_email(service) -> str:
    return service.users().getProfile(userId="me").execute()["emailAddress"]


def _attach(msg: MIMEMultipart, file_path: Path) -> None:
    ctype, _ = mimetypes.guess_type(str(file_path))
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    part = MIMEBase(maintype, subtype)
    part.set_payload(file_path.read_bytes())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
    msg.attach(part)


def _send(service, msg: MimeMsg) -> str:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"]


def send_episode(
    service, cfg: Config, week_of: date, audio_path: Path, backend: str
) -> str:
    """Email the episode: attach if small, Drive link if too big."""
    sender = profile_email(service)
    recipient = cfg.notify_email or sender
    size = audio_path.stat().st_size
    subject = f"Podcast: newsletters week of {week_of.isoformat()}"

    if should_attach(size, cfg.max_attachment_bytes):
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Subject"] = sender, recipient, subject
        msg.attach(MIMEText(f"Weekly newsletter podcast ({backend} backend).", "plain"))
        _attach(msg, audio_path)
        log.info("attaching %s (%.1f MB)", audio_path.name, size / 1e6)
    else:
        link = upload_to_drive(cfg, audio_path, audio_path.name)
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Subject"] = sender, recipient, subject
        msg.attach(
            MIMEText(
                f"Weekly newsletter podcast ({backend} backend).\n"
                f"File is {size / 1e6:.1f} MB, too big to attach:\n{link}\n",
                "plain",
            )
        )
        log.info("episode sent as Drive link: %s", link)

    message_id = _send(service, msg)
    log.info("episode emailed to %s (message id %s)", recipient, message_id)
    return message_id


def send_failure(service, cfg: Config, week_of: date, step: str, error: str) -> None:
    """Best-effort failure alert — never raises."""
    try:
        sender = profile_email(service)
        recipient = cfg.notify_email or sender
        msg = MIMEText(
            f"The weekly newsletter podcast pipeline failed at the '{step}' step "
            f"(week of {week_of.isoformat()}).\n\n"
            f"Error:\n{error}\n\n"
            "It will retry on the next scheduled run (Monday morning).\n"
            "Common fix for NotebookLM auth errors:\n"
            "    .venv/bin/notebooklm login\n"
            "then re-run:\n"
            "    .venv/bin/newsletter-podcast run\n"
        )
        msg["From"], msg["To"] = sender, recipient
        msg["Subject"] = f"Newsletter podcast FAILED at {step} - week of {week_of.isoformat()}"
        _send(service, msg)
        log.info("failure alert emailed to %s", recipient)
    except Exception:
        log.exception("could not send failure alert")
