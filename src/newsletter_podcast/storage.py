"""Google Drive hosting for episodes too big to attach to email.

Files are created by this app (drive.file scope) and stay owner-only:
the episode is emailed to the same account, so the link only opens for you.
"""

from __future__ import annotations

import logging
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import Config
from .gmail_fetch import load_credentials

log = logging.getLogger(__name__)


def should_attach(file_size: int, max_bytes: int) -> bool:
    return file_size <= max_bytes


def _drive_service(cfg: Config):
    return build("drive", "v3", credentials=load_credentials(cfg), cache_discovery=False)


def upload_to_drive(cfg: Config, path: Path, title: str) -> str:
    service = _drive_service(cfg)
    media = MediaFileUpload(str(path), mimetype="audio/mpeg", resumable=True)
    created = (
        service.files()
        .create(body={"name": title}, media_body=media, fields="id,webViewLink")
        .execute()
    )
    link = created.get("webViewLink") or f"https://drive.google.com/file/d/{created['id']}/view"
    log.info("uploaded %s to Drive: %s", path.name, link)
    return link


def prune_drive(cfg: Config) -> int:
    """Keep only the newest cfg.retention_episodes 'episode-' files. Returns deletion count."""
    service = _drive_service(cfg)
    response = (
        service.files()
        .list(
            q="name contains 'episode-' and trashed = false",
            fields="files(id,name,createdTime)",
            orderBy="createdTime desc",
            pageSize=100,
        )
        .execute()
    )
    doomed = response.get("files", [])[cfg.retention_episodes:]
    for file in doomed:
        service.files().delete(fileId=file["id"]).execute()
    if doomed:
        log.info("pruned %d old episodes from Drive", len(doomed))
    return len(doomed)
