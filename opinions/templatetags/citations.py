"""Bluebook citation formatting for opinions -- all live states.

``bluebook_cite_for(opinion)`` returns a plain-text, copy-pasteable
Bluebook citation. The parenthetical depends on what the reporter cite
already tells the reader (Bluebook R10.4):

* **Regional reporter** (N.W.2d, P.3d, So. 3d, A.2d ...) does not identify
  the court, so the parenthetical names it::

      Doe v. Roe, 900 N.W.2d 100 (Minn. Ct. App. 2018).

* **Official state reporter** (Minn., Ariz., Ariz. App., N.H., La. Ann.)
  identifies the court, so the parenthetical is the year alone::

      Doe v. Roe, 300 Minn. 100 (1974).

* **Neutral cite** (NH since 2024: "2026 N.H. 7") carries the year and the
  court already; we keep the courtesy date parenthetical NH shipped with::

      State v. Smith, 2026 N.H. 7 (June 11, 2026).

* **No reporter cite** (unpublished, or too recent for West to have
  assigned one) falls back to the court's own docket number with the court
  and the full decision date::

      State v. Smith, No. A26-0649 (Minn. Ct. App. Sept. 2, 2026).

The case name is the parser's already-normalized ``Opinion.title`` used AS
IS -- we don't re-munge party names. A docket that already carries its own
"No."/"Nos." prefix (CL bulk rows: "No. 2016-CJ-1426") keeps it rather
than doubling it. Output is plain text suitable to paste into a brief; the
template/CSS layer handles any italics.

Everything here is assembled from stored fields. Nothing is generated.
"""
from __future__ import annotations

import re

from django import template
from django.urls import reverse

register = template.Library()

# Bluebook Table T12 month abbreviations (May / June / July are not
# abbreviated; the rest take a trailing period).
_BLUEBOOK_MONTHS = {
    1: "Jan.", 2: "Feb.", 3: "Mar.", 4: "Apr.", 5: "May", 6: "June",
    7: "July", 8: "Aug.", 9: "Sept.", 10: "Oct.", 11: "Nov.", 12: "Dec.",
}

# Reporters that identify the deciding court by themselves, per state.
# Measured on prod 2026-10-05: everything else we hold is regional (N.W.,
# N.W.2d, P., P.2d, P.3d, A., A.2d, A.3d, So., So. 2d, So. 3d) or Teiss.
# (Orleans Court of Appeal, not self-identifying in modern usage).
_OFFICIAL_REPORTERS = {
    "MN": {"Minn."},
    "AZ": {"Ariz.", "Ariz. App."},
    "NH": {"N.H."},
    "LA": {"La.", "La. Ann."},
}

_CITE_RE = re.compile(r"^(?P<vol>\d+)\s+(?P<reporter>.+?)\s+(?P<page>\d+)$")
_DOCKET_PREFIX_RE = re.compile(r"^(nos?)\.?\s*", re.IGNORECASE)


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
    """Bluebook court abbreviation for a parenthetical.

    Louisiana keeps its circuit ("La. Ct. App. 1st Cir.") -- Louisiana
    practice always names it, and the five circuits are separate courts.
    Arizona drops the division: Bluebook omits it unless it matters, and
    "Ariz. Ct. App." is how the court's own opinions cite one another.
    """
    court = opinion.court
    if court.state_id == "LA":
        return court.short_label
    return court._base_short_label()


def _docket(opinion) -> str:
    """'No. A26-0649' -- reusing the stored prefix instead of doubling it."""
    docket = _collapse(opinion.case_number)
    m = _DOCKET_PREFIX_RE.match(docket)
    if m:
        label = "Nos." if m.group(1).lower() == "nos" else "No."
        return "%s %s" % (label, docket[m.end():])
    return "No. %s" % docket


def _parenthetical(opinion, cite: str) -> str:
    """The parenthetical's contents for a reporter cite, or '' for none."""
    date = opinion.release_date
    year = str(date.year) if date else ""
    m = _CITE_RE.match(cite)
    official = _OFFICIAL_REPORTERS.get(opinion.court.state_id, set())
    if m and m.group("reporter") in official:
        if len(m.group("vol")) == 4:
            # Neutral cite ("2026 N.H. 7"): year + court are in the cite.
            return _bluebook_date(date)
        return year
    return ("%s %s" % (_court_abbrev(opinion), year)).strip()


def bluebook_cite_for(opinion, name_limit: int | None = None) -> str:
    """Full Bluebook citation, without the trailing period.

    ``name_limit`` truncates a long caption (with an ellipsis) for places
    with a hard width budget, like the <title> tag. The copyable cite never
    truncates.
    """
    name = _collapse(opinion.title)
    if name_limit and len(name) > name_limit:
        name = name[: name_limit - 1].rstrip(" ,;:") + "…"
    cite = _collapse(opinion.reporter_cite)

    if cite:
        paren = _parenthetical(opinion, cite)
        rest = "%s (%s)" % (cite, paren) if paren else cite
    else:
        date_str = _bluebook_date(opinion.release_date)
        court = _court_abbrev(opinion)
        rest = "%s (%s)" % (_docket(opinion), ("%s %s" % (court, date_str)).strip())

    return _collapse("%s, %s" % (name, rest) if name else rest)


def plain_cite_for(opinion) -> str:
    """Short reference cite -- no parenthetical, no trailing period.

        State v. Smith, 2026 N.H. 7
        State v. Smith, No. 2024-0123   (reporter_cite missing)
    """
    name = _collapse(opinion.title)
    rest = _collapse(opinion.reporter_cite) or _docket(opinion)
    return _collapse("%s, %s" % (name, rest) if name else rest)


@register.simple_tag
def bluebook_cite(opinion) -> str:
    # simple_tag output is auto-escaped in the template context, so case
    # names carrying stray '&' / '<' punctuation render safely.
    return bluebook_cite_for(opinion) + "."


@register.simple_tag
def head_cite(opinion) -> str:
    """Name-first cite for <title> / og:title: no period, caption capped so
    the reporter cite survives Google's ~60-char truncation more often."""
    return bluebook_cite_for(opinion, name_limit=70)


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
