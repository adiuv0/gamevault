"""Markdown rendering + HTML sanitization for screenshot annotations.

Annotations are user-supplied Markdown that gets rendered to HTML, then
embedded into the authenticated UI (via ``dangerouslySetInnerHTML``) and
the public share page. To prevent stored XSS:

  1. Markdown is rendered with HTML pass-through DISABLED — raw ``<script>``,
     ``<img onerror=...>``, etc. become literal text, not HTML.
  2. The rendered HTML is then run through ``nh3`` (a Rust binding to
     Mozilla's ammonia HTML sanitizer) with a strict allowlist.
  3. Link href schemes are restricted to ``http``, ``https``, ``mailto``.

Both are defenses-in-depth: even if a future Markdown bug let a tag
through, the sanitizer catches it.
"""

from __future__ import annotations

from markdown_it import MarkdownIt

import nh3


# Markdown with html disabled. Raw HTML in the input becomes text.
_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})


# Tags safe for read-only viewing in a self-hosted single-user app.
# Notably absent: img (annotations should not embed external images),
# iframe, object, embed, form, input, script, style, link, meta.
_ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "code",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "a",
    "del",
    "s",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
}

_ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}


def render_and_sanitize(content: str) -> str:
    """Convert annotation Markdown to safe HTML.

    Use this anywhere annotation HTML is generated (save endpoint, the
    re-sanitize migration). Never store user-supplied HTML directly.
    """
    raw_html = _md.render(content or "")
    return nh3.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
