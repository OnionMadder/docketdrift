"""Template filters for rendering opinion ``raw_text`` as structured HTML.

The CL bulk ingest stores opinion bodies as plain text (often extracted
from PDFs via pypdf, or stripped from xml_harvard / html_lawbox). A raw
dump in a ``<pre>`` block is technically faithful but unreadable for
long opinions. This module's ``format_opinion_text`` filter turns it
into something a reader actually wants to spend time with:

- Blank-line-separated chunks -> ``<p>`` blocks
- All-caps standalone lines (1-5 words) -> ``<h3>`` section headings
  (FACTS, ANALYSIS, DECISION, BACKGROUND, OPINION, etc.)
- ``Minn. Stat. § N.NN`` -> linked to our /statute/<slug>/ aggregator page,
  which itself deep-links out to revisor.mn.gov. Internal-first because
  the value-add is the per-statute opinion list, not the statute text.
- ``Name v. Name, NNN Reporter NNN`` -> wrapped in ``<cite>``
- **Reporter cites the graph has resolved** -> wrapped in ``<a>`` linking
  to the target opinion's page. Uses OpinionCitation rows for THIS opinion
  (only rows with a resolved ``cited_opinion`` FK; unresolved rows stay
  as plain text). The lookup key is the whitespace-normalized cite string
  the extractor stored in ``cited_reference``, so cites the extractor
  found but that don't yet resolve to a target simply don't become links.

The filter is HTML-safe -- it escapes the input before injecting
structural tags. Citations and links use a small fixed allowlist of
HTML elements/attributes.
"""
from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from opinions.models import OpinionCitation

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


# Paragraph number marker at the START of a chunk:
#   "[¶1] The plaintiffs..."   (NH / AZ Supreme convention -- bracketed)
#   "¶1 The respondent..."     (AZ COA / older NH convention -- bare)
# When present, we render the chunk's <p> tag with id="para-N" so URLs
# can deep-link via #para-N, and we wrap the marker itself in a clickable
# self-anchor so users can copy "share this paragraph" links.
_PARA_NUM_RE = re.compile(r"^\[?¶\s*(\d+)\]?\s+")


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


# Minn. Stat. § N.NN with optional letter suffix on the chapter
# (e.g. ``609A.005``) -- chapter.section is captured separately so we
# can build the canonical /statute/<slug>/ URL on the DocketDrift side.
# Subdivisions are intentionally NOT captured here -- they live on a
# subpage we don't have yet, and rolling the link to the section page
# is the right granularity for in-text browsing.
_STATUTE_RE = re.compile(
    r"(Minn\.?\s+Stat\.?\s*§?\s*)(?P<chapter>\d+[A-Z]?)\.(?P<section>\d+[a-zA-Z]?)",
)


def _linkify_statute(match: re.Match) -> str:
    prefix = match.group(1)
    chapter = match.group("chapter")
    section = match.group("section")
    statute_num = f"{chapter}.{section}"
    # /statute/<slug>/ is the internal aggregator that itself links out
    # to revisor.mn.gov via its header button. Lowercase-only slug to
    # match what the StatuteCitation extractor stores.
    slug = f"minn.stat.{chapter}.{section}".lower()
    href = f"/statute/{slug}/"
    return (
        f'{prefix}<a class="op-statute" href="{href}">{statute_num}</a>'
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


def _normalize_cite(s: str) -> str:
    """Collapse runs of whitespace to a single space, strip ends.

    The extractor's ``cited_reference`` values are normalized to a
    canonical spaced form (e.g. ``"902 So. 2d 373"``) but real opinion
    text can carry the same cite as ``"902 So.2d 373"`` (no space) or
    with a linebreak in the middle. Normalizing both sides of the map
    lookup makes the linker forgiving of the source spelling.
    """
    return " ".join((s or "").split())


def _build_cite_pattern(cite_strings: list[str]) -> re.Pattern | None:
    """Compile ONE regex that matches any of ``cite_strings`` verbatim
    but tolerates arbitrary whitespace between tokens.

    Sorting longest-first prevents e.g. ``"12 N.H. 34"`` from being
    matched as a prefix of ``"12 N.H. 345"``. Empty list -> None so the
    caller can skip the substitution pass entirely.
    """
    if not cite_strings:
        return None
    parts = []
    for cite in sorted(set(cite_strings), key=len, reverse=True):
        # Escape then relax whitespace: any run of whitespace in the
        # canonical cite matches any whitespace run in the text (e.g.
        # linebreak between "N.H." and the page number in a PDF extract).
        p = re.escape(cite).replace(r"\ ", r"\s+")
        parts.append(p)
    return re.compile("(?:" + "|".join(parts) + ")")


@register.filter(is_safe=True)
def format_opinion_text(opinion, highlight: str = "") -> str:
    """Render an Opinion's raw_text as structured HTML.

    ``opinion`` can be an Opinion instance or -- for backwards
    compatibility with earlier callers that only had the raw string --
    a plain string. When an Opinion is passed, this filter also fetches
    that opinion's resolved OpinionCitation rows and turns each cite
    string in the body into a hyperlink to the target opinion's page.
    Unresolved cites (extracted but with no ``cited_opinion`` FK) stay
    as plain text -- we don't link to nothing.

    When ``highlight`` is a non-empty string (typically passed from the
    request query as ``?q=...``), every case-insensitive occurrence of
    the highlight phrase in the body gets wrapped in ``<mark>`` tags.

    Returns an empty string when the raw text is empty/falsy, so the
    "no body" branch in the template is just ``{% if formatted %}``.
    """
    # Accept either an Opinion instance (preferred, gives us cite-linking)
    # or a raw string (legacy call sites).
    raw_text = ""
    cite_targets: dict[str, object] = {}
    if opinion is None:
        return ""
    if isinstance(opinion, str):
        raw_text = opinion
    else:
        raw_text = opinion.raw_text or ""
        if getattr(opinion, "pk", None):
            # Fetch resolved outgoing citations for THIS opinion. Each row
            # has a target opinion + the exact cite string as it was
            # extracted. Multiple rows may share a cite string (e.g. the
            # same cite appearing in different text_offsets); the map
            # keeps the last-seen target, which is fine because all rows
            # for the same cite string point at the same case anyway.
            for oc in (OpinionCitation.objects
                       .filter(citing_opinion=opinion,
                               cited_opinion__isnull=False)
                       .select_related("cited_opinion",
                                       "cited_opinion__court",
                                       "cited_opinion__court__state")
                       .only("cited_reference",
                             "cited_opinion__id",
                             "cited_opinion__case_number",
                             "cited_opinion__court__state_id",
                             "cited_opinion__court__state__slug")):
                key = _normalize_cite(oc.cited_reference)
                if key:
                    cite_targets[key] = oc.cited_opinion

    if not raw_text:
        return ""

    # Build the highlight regex once. Escape so phrase queries with regex
    # metacharacters (parens, dots, brackets) match literally. Empty
    # highlight = no-op pattern that never matches.
    highlight = (highlight or "").strip()
    if highlight:
        hl_re = re.compile(re.escape(highlight), re.IGNORECASE)
    else:
        hl_re = None

    def _highlight(escaped_html: str) -> str:
        if hl_re is None:
            return escaped_html
        return hl_re.sub(
            lambda m: f"<mark>{m.group(0)}</mark>",
            escaped_html,
        )

    # Build the cite-link substitution ONCE per opinion. Runs AFTER
    # _CITATION_RE so a "Name v. Name, cite" match still gets its
    # <cite> italic wrapper -- then this pass wraps just the cite
    # portion (nested inside <cite>) in <a href>. Nesting is fine:
    # <cite class="op-cite">Name v. Name, <a href="...">cite</a></cite>.
    cite_pattern = _build_cite_pattern(list(cite_targets.keys()))

    def _linkify_cites(escaped_html: str) -> str:
        if cite_pattern is None:
            return escaped_html

        def _wrap(m: re.Match) -> str:
            key = _normalize_cite(m.group(0))
            target = cite_targets.get(key)
            if target is None:
                return m.group(0)
            href = target.get_absolute_url()
            return f'<a class="op-cite-link" href="{href}">{m.group(0)}</a>'

        return cite_pattern.sub(_wrap, escaped_html)

    # Page-break handling: pypdf writes a form-feed (U+000C) between
    # pages of the source PDF. AZ opinions carry them reliably (~10/opinion),
    # NH/MN/LA generally do not. When present, we split the body into pages
    # FIRST, render each page's content with the existing blank-line chunk
    # logic, and inject a page anchor between them so URLs can deep-link
    # to `#page-N` (the same shape as `#para-N`). When absent (no \f in the
    # source), the whole body is one page and this is a no-op.
    pages = raw_text.split("\f")

    parts = []
    for page_num, page_text in enumerate(pages, start=1):
        # Anchor + visible marker between pages. Page 1 = top of document,
        # no marker needed; pages 2+ get an `<hr>`-style separator with a
        # click-to-copy anchor that mirrors the ¶N pilcrow convention.
        if page_num > 1:
            parts.append(
                f'<div class="op-page-break">'
                f'<a class="op-page-anchor" href="#page-{page_num}"'
                f' id="page-{page_num}"'
                f' aria-label="Link to page {page_num}">'
                f'p. {page_num}</a>'
                f'</div>'
            )

        # Chunks separated by blank lines (1+ blank lines = paragraph break)
        chunks = re.split(r"\n\s*\n", page_text)

        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue

            if _is_heading(chunk):
                parts.append(f'<h3 class="op-heading">{_highlight(escape(chunk))}</h3>')
                continue

            # Detect a leading paragraph marker like "[¶23]" so we can attach
            # id="para-23" to the <p> for deep-linking and wrap the marker
            # itself as an in-page self-link the user can right-click + copy.
            para_match = _PARA_NUM_RE.match(chunk)
            para_id_attr = ""
            para_marker_html = ""
            if para_match:
                para_n = para_match.group(1)
                para_id_attr = f' id="para-{para_n}"'
                para_marker_html = (
                    f'<a class="op-para-anchor" href="#para-{para_n}"'
                    f' aria-label="Link to paragraph {para_n}">'
                    f'¶{para_n}</a> '
                )
                chunk = chunk[para_match.end():]

            # Body paragraph. Escape first, then inject statute links + case
            # citation wrappers. Internal hard newlines become <br> so
            # mid-paragraph line wraps from the source survive (e.g. case
            # captions like "State of Minnesota,\n                Respondent,").
            escaped = escape(chunk)
            escaped = _STATUTE_RE.sub(_linkify_statute, escaped)
            escaped = _CITATION_RE.sub(_wrap_citation, escaped)
            # Case-citation wrappers above added <cite>...</cite> around
            # "Name v. Name, cite" pairs; now turn the cite portion into a
            # hyperlink if the graph has resolved it to a target opinion.
            # Also linkifies BARE cites (no "Name v. Name" prefix) since
            # the extractor stores them at the granularity of the cite
            # string alone -- so a bare "160 N.H. 732" reference becomes
            # clickable too.
            escaped = _linkify_cites(escaped)
            escaped = _highlight(escaped)
            escaped = escaped.replace("\n", "<br>")
            parts.append(
                f'<p class="op-para"{para_id_attr}>{para_marker_html}{escaped}</p>'
            )

    return mark_safe("\n".join(parts))
