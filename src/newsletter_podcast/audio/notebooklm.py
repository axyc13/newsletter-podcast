"""NotebookLM backend via the unofficial notebooklm-py library (v0.8.x).

Login is cookie/profile based and expires every few weeks:
    .venv/bin/notebooklm login      (or: newsletter-podcast notebooklm-login)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import TypeVar

from ..config import Config

log = logging.getLogger(__name__)

_T = TypeVar("_T")
_RETRY_DELAYS = (0, 30, 90)  # seconds between attempts (rate limits, transient breaks)


class NotebookLMAudioGenerator:
    name = "notebooklm"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def generate(self, doc_path: Path, week_of: date) -> Path:
        return asyncio.run(self._generate(doc_path, week_of))

    async def _generate(self, doc_path: Path, week_of: date) -> Path:
        from notebooklm import NotebookLMClient

        text = doc_path.read_text(encoding="utf-8")
        if not text.strip():
            raise RuntimeError(f"weekly document is empty: {doc_path}")
        out = self.cfg.output_dir / f"episode-{week_of.isoformat()}.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        instructions = (
            "Turn this week's newsletters into a friendly, factual two-host podcast "
            "briefing. Cover every story in the source, be specific, skip sponsor "
            f"reads and filler, keep it under {self.cfg.audio_max_minutes} minutes."
        )

        async with NotebookLMClient.from_storage() as client:
            notebook = await client.notebooks.create(f"Newsletters {week_of.isoformat()}")
            log.info("created NotebookLM notebook %r (%s)", notebook.title, notebook.id)
            try:
                await self._call(
                    lambda: client.sources.add_text(
                        notebook.id,
                        f"Weekly newsletters {week_of.isoformat()}",
                        text,
                        wait=True,
                        wait_timeout=180.0,
                    ),
                    "add source",
                )
                status = await self._call(
                    lambda: client.artifacts.generate_audio(
                        notebook.id, instructions=instructions
                    ),
                    "start audio generation",
                )
                # One long wait (no retries): generation takes 10-20 min per the design doc.
                await self._call(
                    lambda: client.artifacts.wait_for_completion(
                        notebook.id, status.task_id, timeout=2700.0
                    ),
                    "wait for audio",
                    retries=False,
                )
                await self._call(
                    lambda: client.artifacts.download_audio(notebook.id, str(out)),
                    "download audio",
                )
            finally:
                await self._delete_notebook(client, notebook.id)

        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("NotebookLM download produced no audio file")
        log.info("episode downloaded: %s (%.1f MB)", out.name, out.stat().st_size / 1e6)
        return out

    async def _call(
        self, factory: Callable[[], Awaitable[_T]], description: str, retries: bool = True
    ) -> _T:
        """Run a library call with retries (rate limits / transient API errors)."""
        delays = _RETRY_DELAYS if retries else (0,)
        last_exc: Exception | None = None
        for delay in delays:
            if delay:
                log.warning("%s failed (%s); retrying in %ss", description, last_exc, delay)
                await asyncio.sleep(delay)
            try:
                return await factory()
            except Exception as exc:  # unofficial library raises assorted error types
                last_exc = exc
        raise RuntimeError(
            f"{description} failed after {len(delays)} attempt(s): {last_exc}"
        ) from last_exc

    async def _delete_notebook(self, client, notebook_id: str) -> None:
        """Housekeeping: one notebook per week, deleted after download."""
        try:
            await client.notebooks.delete(notebook_id)
        except Exception as exc:
            log.warning("could not delete notebook %s: %s", notebook_id, exc)
