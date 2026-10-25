"""Central configuration for the weekly newsletter podcast pipeline.

All values can be overridden via environment variables (or a .env file).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional .env support
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# gmail.modify covers read + label/trash writes (superset of readonly); send keeps
# explicit delivery rights. Plus drive.file (only files this app creates).
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_SCOPES = GMAIL_SCOPES + [DRIVE_SCOPE]


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Config:
    project_root: Path = PROJECT_ROOT
    gmail_label: str = "Newsletter"
    credentials_file: Path = PROJECT_ROOT / "credentials.json"
    token_file: Path = PROJECT_ROOT / "token.json"
    state_db: Path = PROJECT_ROOT / "state.db"
    output_dir: Path = PROJECT_ROOT / "output"
    logs_dir: Path = PROJECT_ROOT / "logs"
    fetch_days: int = 7
    max_attachment_bytes: int = 20 * 1024 * 1024  # stay under Gmail's ~25MB cap
    retention_episodes: int = 10
    notify_email: str = ""  # empty -> the authenticated account itself
    audio_backend: str = "notebooklm"  # or "dummy"
    audio_max_minutes: int = 15
    trash_ingested: bool = True  # move sources to Trash after ingestion

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            gmail_label=os.environ.get("GMAIL_LABEL", "").strip() or "Newsletter",
            fetch_days=_int_env("FETCH_DAYS", 7),
            notify_email=os.environ.get("NOTIFY_EMAIL", "").strip(),
            audio_backend=os.environ.get("AUDIO_BACKEND", "").strip() or "notebooklm",
            retention_episodes=_int_env("RETENTION_EPISODES", 10),
            audio_max_minutes=_int_env("AUDIO_MAX_MINUTES", 15),
            trash_ingested=_bool_env("TRASH_INGESTED", True),
        )

    def ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
