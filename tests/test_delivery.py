from dataclasses import replace
from datetime import date
from pathlib import Path

from newsletter_podcast.audio.base import DummyAudioGenerator, get_audio_generator
from newsletter_podcast.clean import _strip_utm
from newsletter_podcast.config import Config
from newsletter_podcast.storage import should_attach


def test_should_attach_threshold():
    limit = 20 * 1024 * 1024
    assert should_attach(limit, limit)
    assert should_attach(1024, limit)
    assert not should_attach(limit + 1, limit)


def test_strip_utm_keeps_other_params_and_fragment():
    assert _strip_utm("https://x.dev/a?utm_source=n&id=7#sec") == "https://x.dev/a?id=7#sec"
    assert _strip_utm("https://x.dev/a?utm_source=n&utm_medium=e") == "https://x.dev/a"
    assert _strip_utm("https://x.dev/a?id=7") == "https://x.dev/a?id=7"
    assert _strip_utm("https://x.dev/a") == "https://x.dev/a"


def test_dummy_generator_writes_file(tmp_path: Path):
    cfg = replace(Config.from_env(), output_dir=tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("# Test\nsome content", encoding="utf-8")
    out = DummyAudioGenerator(cfg).generate(doc, date(2026, 10, 25))
    assert out.exists()
    assert out.name == "episode-2026-10-25.mp3"
    assert out.read_bytes().startswith(b"ID3dummy:")


def test_backend_selection_is_swappable():
    dummy_cfg = replace(Config.from_env(), audio_backend="dummy")
    assert get_audio_generator(dummy_cfg).name == "dummy"
    nblm_cfg = replace(Config.from_env(), audio_backend="notebooklm")
    assert get_audio_generator(nblm_cfg).name == "notebooklm"
