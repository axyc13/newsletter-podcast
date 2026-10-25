from datetime import date
from pathlib import Path

from newsletter_podcast.clean import EmailSection, html_to_markdown, merge_emails

FIXTURES = Path(__file__).parent / "fixtures"


def _sample(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_body_survives():
    md, _ = html_to_markdown(_sample("newsletter_sample.html"))
    assert "Big Story This Week" in md
    assert "37% faster" in md
    assert "Second story without tracking" in md


def test_footer_and_unsubscribe_stripped():
    md, _ = html_to_markdown(_sample("newsletter_sample.html"))
    assert "Unsubscribe" not in md
    assert "Manage preferences" not in md
    assert "signed up for the digest" not in md


def test_tracking_pixel_and_hidden_content_stripped():
    md, _ = html_to_markdown(_sample("newsletter_sample.html"))
    assert "pixel.gif" not in md
    assert "Hidden open-tracking content" not in md
    assert "View in browser" not in md


def test_sponsored_block_stripped():
    md, _ = html_to_markdown(_sample("newsletter_sample.html"))
    assert "Sponsored" not in md
    assert "Buy our stuff" not in md


def test_read_more_link_removed_but_sentence_kept():
    md, _ = html_to_markdown(_sample("newsletter_sample.html"))
    assert "Read more" not in md
    assert "offline mode" in md


def test_utm_params_stripped_from_surviving_links():
    md, _ = html_to_markdown(_sample("newsletter_sample.html"))
    assert "utm_" not in md


def test_stats_report_removals():
    _, stats = html_to_markdown(_sample("newsletter_sample.html"))
    assert stats, "expected at least one removal category"
    assert stats.get("footer_text", 0) + stats.get("classed_blocks", 0) >= 1


def test_second_fixture_cleans_too():
    md, _ = html_to_markdown(_sample("newsletter_sample_2.html"))
    assert "cold-start times" in md
    assert "unsubscribe" not in md.lower()


def test_merge_emails_builds_one_document():
    body1, _ = html_to_markdown(_sample("newsletter_sample.html"))
    body2, _ = html_to_markdown(_sample("newsletter_sample_2.html"))
    sections = [
        EmailSection("id1", "Weekly Digest", "a@example.com", "2026-10-25", body1),
        EmailSection("id2", "Dev Tools Weekly #84", "b@example.com", "2026-10-24", body2),
    ]
    doc = merge_emails(sections, date(2026, 10, 25))
    assert doc.startswith("# Weekly Newsletter Digest")
    assert "## 1. Weekly Digest" in doc
    assert "## 2. Dev Tools Weekly #84" in doc
    assert "37% faster" in doc
    assert "cold-start times" in doc
    assert "unsubscribe" not in doc.lower()


def test_merge_empty_week():
    doc = merge_emails([], date(2026, 10, 25))
    assert doc.startswith("# Weekly Newsletter Digest")
    assert "No newsletters this week" in doc
