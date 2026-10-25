"""Deterministic email cleaning: HTML -> noise-stripped Markdown. No token cost.

Design-doc jobs handled here:
  1. HTML -> plain text / Markdown
  2. strip unsubscribe footers, tracking links, "read more"/"view in browser" lines, ads
  3. merge the week's emails into one clean document
(processed message IDs live in state.py)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import trafilatura
from bs4 import BeautifulSoup, Comment, Tag

# --- noise patterns ----------------------------------------------------------

_FOOTER_TEXT_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bunsubscribe\b",
        r"\bmanage (your )?preferences\b",
        r"\bupdate (your )?(email )?preferences\b",
        r"\bview (this )?(email )?in (your )?browser\b",
        r"\byou are receiving this\b",
        r"\bstop receiving\b",
        r"\bno longer wish to receive\b",
        r"\bmanage (your )?(mailing|email|subscription)\b",
        r"\bwhy did (i|we) receive this\b",
        r"\bforward this email\b",
        r"\bupdate your subscription\b",
        r"\bclick here to (unsubscribe|manage|update)\b",
        r"\ball rights reserved\b",
        r"&copy;|\u00a9\s*20\d\d",
    )
)

_READ_MORE_TEXT = re.compile(
    r"^\s*(read more|continue reading|keep reading|see more|view in browser|"
    r"view this email in your browser|click here to read more)",
    re.IGNORECASE,
)

_UNSUB_HREF = re.compile(
    r"(unsubscribe|list-manage\.com|manage-subscription|email-prefs|/preferences\b)",
    re.IGNORECASE,
)

_TRACKING_HREF = re.compile(
    r"(/[tp]track/|track(er)?\.(gif|png|js)|/t/open|beehive|click\.[a-z0-9-]+\.[a-z]{2,})",
    re.IGNORECASE,
)

# class/id tokens whose element is boilerplate (split on - _ and whitespace)
_NOISE_TOKENS = frozenset(
    {
        "ad", "ads", "advertisement", "advertising", "sponsored", "sponsor",
        "sponsors", "sponsorship", "promo",
        "footer", "footers", "footer-links", "email-footer", "mailing-footer",
        "unsubscribe", "unsubscribe-link", "preferences", "view-in-browser",
        "tracking", "pixel",
    }
)

_TOKEN_SPLIT = re.compile(r"[-_\s]+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_REMOVE_TAGS = (
    "script", "style", "noscript", "iframe", "form", "input", "select",
    "option", "svg", "canvas", "link", "meta", "base", "audio", "video",
    "source", "track", "object", "embed",
)

_BLOCK_TAGS = ("p", "div", "td", "li", "section", "tr", "blockquote", "h1", "h2", "h3")
_MIN_CONVERSION_CHARS = 80  # below this, trafilatura output counts as a failed extraction

def _drop(tag: Tag) -> bool:
    try:
        if getattr(tag, "decomposed", False):
            return False
        tag.decompose()
        return True
    except Exception:  # already-detached nodes in a mutated tree
        return False


def _alive(tag: Tag) -> bool:
    """False for tags whose ancestor (or self) was decomposed earlier in the pass."""
    try:
        return not getattr(tag, "decomposed", False) and tag.attrs is not None
    except Exception:
        return False


def _strip_utm(href: str) -> str:
    if "utm_" not in href:
        return href
    base, hash_mark, fragment = href.partition("#")
    url, _question, query = base.partition("?")
    if not query:
        return href
    kept = [p for p in query.split("&") if p and not p.lower().startswith("utm_")]
    cleaned = url + ("?" + "&".join(kept) if kept else "")
    return cleaned + (hash_mark + fragment if hash_mark else "")


def _has_noise_class(tag: Tag) -> bool:
    raw = " ".join(tag.get("class") or []) + " " + (tag.get("id") or "")
    lowered = raw.lower()
    for token in _TOKEN_SPLIT.split(lowered):
        if token in _NOISE_TOKENS:
            return True
    for token in _TOKEN_RE.findall(lowered):
        if token in _NOISE_TOKENS:
            return True
    return False


def strip_noise(soup: BeautifulSoup) -> dict[str, int]:
    """Remove boilerplate from the DOM in place; return per-category removal counts."""
    stats: dict[str, int] = {}

    def bump(category: str) -> None:
        stats[category] = stats.get(category, 0) + 1

    # 1. comments
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        try:
            comment.extract()
            bump("comments")
        except Exception:
            pass

    # 2. hard-wanted-out tags
    for tag in soup.find_all(_REMOVE_TAGS):
        if _drop(tag):
            bump("tags")

    # 3. hidden elements (display:none, mso-hide)
    for tag in list(soup.find_all(True)):
        if not _alive(tag):
            continue
        style = (tag.get("style") or "").lower().replace(" ", "")
        if "display:none" in style or "mso-hide:all" in style:
            if _drop(tag):
                bump("hidden")

    # 4. noise class/id blocks (ads, footers, pixels)
    for tag in list(soup.find_all(True)):
        if not _alive(tag):
            continue
        if _has_noise_class(tag):
            if _drop(tag):
                bump("classed_blocks")

    # 5. short text blocks that read like footer boilerplate
    for tag in soup.find_all(_BLOCK_TAGS):
        if not _alive(tag):
            continue
        try:
            text = tag.get_text(" ", strip=True)
        except Exception:
            continue
        if not text or len(text) > 400:
            continue
        if any(pat.search(text) for pat in _FOOTER_TEXT_PATTERNS):
            if _drop(tag):
                bump("footer_text")

    # 6. images: pixels out, meaningful alt text kept
    for img in soup.find_all("img"):
        try:
            src = img.get("src", "")
            width = int(img.get("width") or 0)
            height = int(img.get("height") or 0)
            alt = (img.get("alt") or "").strip()
            is_pixel = (
                width <= 2
                or height <= 2
                or _TRACKING_HREF.search(src)
                or "pixel" in src.lower()
                or not src
            )
            if is_pixel or len(alt) < 4:
                if _drop(img):
                    bump("images")
            else:
                img.replace_with(f" {alt} ")
        except Exception:
            continue

    # 7. links: read-more / unsubscribe anchors out; utm params stripped
    for a in soup.find_all("a"):
        try:
            text = a.get_text(" ", strip=True)
            href = a.get("href", "") or ""
            if _READ_MORE_TEXT.search(text) or _UNSUB_HREF.search(href) or _TRACKING_HREF.search(href):
                if _drop(a):
                    bump("links")
                continue
            if "utm_" in href:
                new_href = _strip_utm(href)
                if new_href != href:
                    a["href"] = new_href
        except Exception:
            continue

    # 8. containers left empty after removals
    for tag in soup.find_all(["table", "tr", "td", "tbody", "ul", "ol"]):
        try:
            if not tag.get_text(strip=True) and not tag.find("img"):
                if _drop(tag):
                    bump("empty_containers")
        except Exception:
            continue

    return stats


def _soup_to_text(soup: BeautifulSoup) -> str:
    """Plain-text fallback when trafilatura's extraction comes back empty."""
    text = soup.get_text("\n")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip())


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _demote_headings(soup: BeautifulSoup) -> int:
    """trafilatura can treat the top <h1> as page metadata and drop it from the body.

    Our headings are content (newsletter titles), so convert them to emphasized
    paragraphs before extraction — content preservation beats markdown structure.
    """
    count = 0
    for tag in soup.find_all(("h1", "h2", "h3", "h4", "h5", "h6")):
        if not _alive(tag):
            continue
        text = tag.get_text(" ", strip=True)
        if not text:
            _drop(tag)
            continue
        paragraph = soup.new_tag("p")
        strong = soup.new_tag("strong")
        strong.string = text
        paragraph.append(strong)
        tag.replace_with(paragraph)
        count += 1
    return count


def html_to_markdown(html: str) -> tuple[str, dict[str, int]]:
    """Clean HTML and convert it to Markdown/plain text. Returns (text, removal stats)."""
    soup = BeautifulSoup(html, "html.parser")
    stats = strip_noise(soup)
    demoted = _demote_headings(soup)
    if demoted:
        stats["headings_demoted"] = demoted

    md = ""
    try:
        md = (
            trafilatura.extract(
                str(soup),
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_links=True,
                include_formatting=True,
                include_images=False,
            )
            or ""
        ).strip()
    except Exception:
        md = ""

    if len(md) < _MIN_CONVERSION_CHARS:
        md = _soup_to_text(soup)

    return _normalize_whitespace(md), stats


def plain_text_to_markdown(text: str) -> str:
    return _normalize_whitespace(text)


# --- merging -----------------------------------------------------------------


@dataclass
class EmailSection:
    message_id: str
    subject: str
    sender: str
    date: str
    body: str


def merge_emails(sections: list[EmailSection], week_of: date) -> str:
    """Merge the week's cleaned emails into one document for the audio backend."""
    parts: list[str] = [f"# Weekly Newsletter Digest \u2014 week of {week_of.isoformat()}", ""]
    if not sections:
        parts.append("_No newsletters this week._")
        return "\n".join(parts) + "\n"
    for i, section in enumerate(sections, 1):
        subject = section.subject or "(no subject)"
        parts += [
            f"## {i}. {subject}",
            "",
            f"_From: {section.sender} \u00b7 {section.date}_",
            "",
            section.body.strip(),
            "",
            "---",
            "",
        ]
    return "\n".join(parts).strip() + "\n"
