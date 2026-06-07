"""Template filters for rendering opinion ``raw_text`` as structured HTML.

The CL bulk ingest stores opinion bodies as plain text (often extracted
from PDFs via pypdf, or stripped from xml_harvard / html_lawbox). A raw
dump in a ``<pre>`` block is technically faithful but unreadable for
long opinions. This module's ``format_opinion_text`` filter turns it
into something a reader actually wants to spend time with:

- Blank-line-separated chunks -> ``<p>`` blocks
- All-caps standalone lines (1-5 words) -> ``<h3>`` section headings
  (FACTS, ANALYSIS, DECISION, BACKGROUND, OPINION, etc.)
- ``Minn. Stat. § N.NN`` -> linked to revisor.mn.gov
- ``Name v. Name, NNN Reporter NNN`` -> wrapped in ``<cite>``

The filter is HTML-safe -- it escapes the input before injecting
structural tags. Citations and links use a small fixed allowlist of
HTML elements/attributes.
"""
from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


# A section heading is a standalone short line of mostly-uppercase words.
# We're conservative: 1-5 words, no lowercase letters, between 3 and 60
# characters. Catches FACTS / ANALYSIS / DECISION / DISCUSSION / OPINION /
# BACKGROUND / INTRODUCTION / DISSENT / CONCURRENCE. Some false positives
# from caption blocks ("STATE OF MINNESOTA") get rendered as headings too,
# which is structurally fine even if it's not strictly a section heading.
_MIN_HEADING_LEN = 3
_MAX_HEADING_LEN = 60
_MAX_HEADING_WORDS = 5


def _is_heading(text: str) -> bool:
    stripped = text.strip()
    if not (_MIN_HEADING_LEN <= len(stripped) <= _MAX_HEADING_LEN):
        return False
    # No newlines -- must be single-line
    if "\n" in stripped:
        return False
    words = stripped.split()
    if not (1 <= len(words) <= _MAX_HEADING_WORDS):
        return False
    # All-caps + has at least one letter
    return stripped == stripped.upper() and any(c.isalpha() for c in stripped)


# Minn. Stat. § N.NN (with optional subdivisions) -- link to revisor.mn.gov.
# We capture just the statute number so we can build the canonical URL.
_STATUTE_RE = re.compile(
    r"(Minn\.?\s+Stat\.?\s*§?\s*)(\d+[A-Z]?\.\d+)",
)


def _linkify_statute(match: re.Match) -> str:
    prefix = match.group(1)
    statute_num = match.group(2)
    url = f"https://www.revisor.mn.gov/statutes/cite/{statute_num}"
    return (
        f'{prefix}<a class="op-statute" href="{url}" '
        f'target="_blank" rel="noopener noreferrer">{statute_num}</a>'
    )


# Case citation heuristic: "Name v. Name, NNN Reporter NNN (Year)".
#
# Each side of "v." must be 1-3 words AND each word must start with a
# capital -- this rejects prose like "This case is controlled by State
# v. Smith" (the regex engine can't latch onto "This" as the case-name
# start because "case", "is", etc. are lowercase). Missing some long
# case names is an acceptable tradeoff for not over-wrapping prose.
_CASE_NAME_PART = r"[A-Z][A-Za-z\.\']+(?:\s+[A-Z][A-Za-z\.\']+){0,2}"
_CITATION_RE = re.compile(
    rf"({_CASE_NAME_PART}\s+v\.\s+{_CASE_NAME_PART}),\s+"
    rf"(\d+\s+[A-Z][A-Za-z\.\s']*?\s+\d+(?:,\s*\d+)?"
    rf"(?:\s*\([A-Za-z\.\s]*?\d{{4}}\))?)"
)


def _wrap_citation(match: re.Match) -> str:
    case_name = match.group(1)
    citation = match.group(2)
    return f'<cite class="op-cite">{case_name}, {citation}</cite>'


@register.filter(is_safe=True)
def format_opinion_text(raw_text: str | None) -> str:
    """Render opinion raw_text as structured HTML.

    Returns an empty string when raw_text is empty/falsy, so the
    "no body" branch in the template is just ``{% if formatted %}``.
    """
    if not raw_text:
        return ""

    # Chunks separated by blank lines (1+ blank lines = paragraph break)
    chunks = re.split(r"\n\s*\n", raw_text)

    parts = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        if _is_heading(chunk):
            parts.append(f'<h3 class="op-heading">{escape(chunk)}</h3>')
            continue

        # Body paragraph. Escape first, then inject statute links + case
        # citation wrappers. Internal hard newlines become <br> so
        # mid-paragraph line wraps from the source survive (e.g. case
        # captions like "State of Minnesota,\n                Respondent,").
        escaped = escape(chunk)
        escaped = _STATUTE_RE.sub(_linkify_statute, escaped)
        escaped = _CITATION_RE.sub(_wrap_citation, escaped)
        escaped = escaped.replace("\n", "<br>")
        parts.append(f'<p class="op-para">{escaped}</p>')

    return mark_safe("\n".join(parts))
