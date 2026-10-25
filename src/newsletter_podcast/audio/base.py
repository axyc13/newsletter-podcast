"""Swappable audio-generation interface — the design doc's insurance policy.

Keep every backend (NotebookLM today, Open Notebook / DIY+TTS tomorrow) behind
this one function so the pipeline never depends on a single provider.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol

from ..config import Config


class AudioGenerator(Protocol):
    """Any backend that can turn the merged weekly document into an episode file."""

    name: str

    def generate(self, doc_path: Path, week_of: date) -> Path:
        """Return the local path of the generated audio file."""
        ...


class DummyAudioGenerator:
    """Writes a placeholder file — for tests and pipeline dry-runs (no network)."""

    name = "dummy"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def generate(self, doc_path: Path, week_of: date) -> Path:
        out = self.cfg.output_dir / f"episode-{week_of.isoformat()}.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        head = doc_path.read_text(encoding="utf-8", errors="replace")[:300]
        out.write_bytes(b"ID3dummy:" + week_of.isoformat().encode() + b"\n" + head.encode())
        return out


def get_audio_generator(cfg: Config) -> AudioGenerator:
    backend = cfg.audio_backend.lower()
    if backend == "dummy":
        return DummyAudioGenerator(cfg)
    if backend == "notebooklm":
        from .notebooklm import NotebookLMAudioGenerator

        return NotebookLMAudioGenerator(cfg)
    raise ValueError(
        f"Unknown AUDIO_BACKEND {cfg.audio_backend!r}; use 'notebooklm' or 'dummy'"
    )
