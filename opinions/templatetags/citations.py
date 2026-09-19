"""Bluebook citation formatting for opinions (NH proving-ground first).

``bluebook_cite_for(opinion)`` returns a plain-text, copy-pasteable
Bluebook citation. NH adopted neutral citations in 2024, so a modern NH
slip opinion's ``reporter_cite`` ("2026 N.H. 7") already identifies the
court -- no court name in the parenthetical, just the decision date:

    State v. Smith, 2026 N.H. 7 (June 11, 2026).

When ``reporter_cite`` is empty (older opinions, or a state we haven't
backfilled), we fall back to the court-assigned docket number with an
explicit court tag in the parenthetical:

    State v. Smith, No. 2024-0123 (N.H. June 11, 2026).

The case name is the parser's already-normalized ``Opinion.title`` used
AS IS -- we don't re-munge party names. Output is plain text suitable to
paste into a Word brief; the template/CSS layer handles any italics.

The court abbreviation is hard-coded ``N.H.`` because the live corpus is
NH Supreme only. When NH Superior Court / NH Court of Appeals are added,
revisit ``_court_abbrev`` against Bluebook Table T1.3.
"""
from __future__ import annotations

from django import template
from django.urls import reverse

register = template.Library()

# Bluebook Table T12 month abbreviations (May / June / July are not
# abbreviated; the rest take a trailing period).
_BLUEBOOK_MONTHS = {
    1: "Jan.", 2: "Feb.", 3: "Mar.", 4: "Apr.", 5: "May", 6: "June",
    7: "July", 8: "Aug.", 9: "Sept.", 10: "Oct.", 11: "Nov.", 12: "Dec.",
}


def _collapse(text: str) -> str:
    """Squash internal whitespace/newlines from multi-line captions."""
    return " ".join((text or "").split())


def _bluebook_date(release_date) -> str:
    if not release_date:
        return ""
    return "%s %d, %d" % (
        _BLUEBOOK_MONTHS.get(release_date.month, ""),
        release_date.day,
        release_date.year,
    )


def _court_abbrev(opinion) -> str:
    """Bluebook court abbreviation for the parenthetical fallback.

    Live corpus is NH Supreme only, so this is constant for now. Kept as a
    function so the NH-Superior / NH-COA rollout has one place to extend.
    """
    return "N.H."


def bluebook_cite_for(opinion) -> str:
    """Full Bluebook citation string (with courtesy date parenthetical)."""
    name = _collapse(opinion.title)
    date_str = _bluebook_date(opinion.release_date)
    cite = _collapse(opinion.reporter_cite)

    if cite:
        paren = " (%s)" % date_str if date_str else ""
        body = "%s, %s%s" % (name, cite, paren)
    else:
        docket = _collapse(opinion.case_number)
        court = _court_abbrev(opinion)
        inner = ("%s %s" % (court, date_str)).strip() if date_str else court
        body = "%s, No. %s (%s)" % (name, docket, inner)

    return _collapse(body) + "."


def plain_cite_for(opinion) -> str:
    """Short reference cite -- no courtesy parenthetical, no trailing period.

        State v. Smith, 2026 N.H. 7
        State v. Smith, No. 2024-0123   (reporter_cite missing)
    """
    name = _collapse(opinion.title)
    cite = _collapse(opinion.reporter_cite)
    if cite:
        return _collapse("%s, %s" % (name, cite))
    docket = _collapse(opinion.case_number)
    return _collapse("%s, No. %s" % (name, docket))


@register.simple_tag
def bluebook_cite(opinion) -> str:
    # simple_tag output is auto-escaped in the template context, so case
    # names carrying stray '&' / '<' punctuation render safely.
    return bluebook_cite_for(opinion)


@register.simple_tag
def plain_cite(opinion) -> str:
    return plain_cite_for(opinion)


@register.simple_tag
def opinion_href(opinion, current_state=None):
    """Correct href for an opinion, ACROSS state subdomains.

    `{% url 'opinions:detail' %}` emits a relative path, which resolves
    against whatever subdomain the reader is on. Opinion pages are
    per-state scoped, so a link to an opinion in ANOTHER state 404s.

    That is not hypothetical: the citation graph keeps every internal
    edge, including cross-state ones (a Minnesota court citing an
    Arizona case), and those are exactly the links the "Cited by" and
    "Authorities cited" panels emit. Googlebot 404'd on 72 distinct
    opinion paths over ten days and nearly every one resolved 200 on its
    OWN state's subdomain -- correct scoping, wrong host in the link.

    Same state -> relative path (keeps the reader on their subdomain and
    avoids a pointless absolute URL). Different state -> absolute URL on
    that opinion's own subdomain.

    Callers MUST select_related("...__court__state") or this is an N+1.
    """
    path = reverse("opinions:detail", kwargs={"case_number": opinion.case_number})
    state = opinion.court.state
    if current_state is not None and state.code == current_state.code:
        return path
    return "https://%s.docketdrift.com%s" % (state.slug, path)
