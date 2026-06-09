"""Scraper for the Arizona Supreme Court current roster.

The Arizona Judicial Branch publishes ALL seven seated justices on a
single page (https://www.azcourts.gov/MeettheJustices). Every justice
block on that page contains:

  - portrait image (``<img>``)
  - role + full name (``<strong><span style="...font-size:21px">``)
  - bio summary (next ``<p>`` block, font-size:14px)
  - "READ MORE" link to the canonical bio page (a button-styled <a>)

That's everything we need -- no need to fetch each justice's bio page
individually. One request, full roster, ~7 KB of structured HTML.

Strategy:

1. Fetch the meet-the-justices listing once.
2. Walk every ``<tr>`` row that contains both an image and a name
   heading (filters out chrome / navigation rows).
3. Per row, extract the four fields above.

Polite: descriptive User-Agent that links back to the project; single
HTTP call so no inter-fetch sleeping needed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# Single-page roster (no sitemap chase, no per-judge fetches).
ROSTER_URL = "https://www.azcourts.gov/MeettheJustices"
BASE_URL = "https://www.azcourts.gov"
USER_AGENT = "DocketDrift/0.1 (+https://docketdrift.com)"
DEFAULT_TIMEOUT = 30

# Role prefixes that appear before the justice's name on the listing,
# in priority order so we strip the longest match first.
_AZ_ROLE_PREFIXES = (
    "Vice Chief Justice",
    "Chief Justice",
    "Vice Chief Judge",
    "Chief Judge",
    "Presiding Judge",
    "Justice",
    "Judge",
)

# Map the parsed role text to the Judge.Role enum value our model uses.
_AZ_ROLE_MAP = {
    "Chief Justice": "CHIEF_JUSTICE",
    "Vice Chief Justice": "ASSOCIATE_JUSTICE",
    "Justice": "ASSOCIATE_JUSTICE",
    "Chief Judge": "CHIEF_JUDGE",
    "Vice Chief Judge": "JUDGE",
    "Presiding Judge": "JUDGE",
    "Judge": "JUDGE",
}


@dataclass
class ScrapedJudge:
    """One justice's parsed bio. ``court_kind`` is 'SUPREME' or 'APPEALS'."""

    source_id: str
    bio_url: str
    full_name: str
    role: str
    court_kind: str
    photo_url: str = ""
    bio_summary: str = ""
    appointment_date: Optional[date] = None
    confidence: dict[str, float] = field(default_factory=dict)


def _split_role_and_name(blob: str) -> tuple[str, str]:
    """Pull a role prefix off the front of ``blob`` and return
    (role_text, remainder)."""
    s = (blob or "").strip()
    for prefix in _AZ_ROLE_PREFIXES:
        if s.startswith(prefix):
            name = s[len(prefix):].strip()
            return prefix, name
    return "Justice", s


def _absolutize(href: str) -> str:
    """Resolve a relative href against azcourts.gov, return absolute URL."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return f"{BASE_URL}/{href}"


def _clean(text: str) -> str:
    """Collapse runs of whitespace + unicode non-breaking spaces."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


class AZSupremeJudgeScraper:
    """Public surface for the Arizona Supreme Court roster scrape."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def fetch_roster(self) -> list[ScrapedJudge]:
        """Return a list of ScrapedJudge entries for every seated justice.

        Returns an empty list (and logs at WARNING) if the page can't be
        fetched or doesn't contain any parseable justice rows -- the
        management command treats the empty result as "no changes",
        which is safer than crashing the cron.
        """
        try:
            resp = self._session.get(ROSTER_URL, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("az supreme roster fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Each justice block is a <tr> with both an <img> and a heading
        # span at font-size:21px (the navy-blue justice name). Filter on
        # both so we don't pick up chrome rows.
        results: list[ScrapedJudge] = []
        for tr in soup.find_all("tr"):
            img = tr.find("img")
            name_span = tr.find("span", style=re.compile(r"font-size\s*:\s*21px"))
            if img is None or name_span is None:
                continue

            raw_name = _clean(name_span.get_text(strip=True))
            role_text, full_name = _split_role_and_name(raw_name)
            if not full_name:
                continue  # heading wasn't a justice name

            # Bio summary: the first 14px-text <span> in this row that
            # is NOT the heading itself. Falls back to first <p> if the
            # site changes the styling convention.
            bio_summary = ""
            for span in tr.find_all("span", style=re.compile(r"font-size\s*:\s*14px")):
                txt = _clean(span.get_text(" ", strip=True))
                if txt and txt != raw_name:
                    bio_summary = txt
                    break
            if not bio_summary:
                first_p = tr.find("p")
                if first_p is not None:
                    bio_summary = _clean(first_p.get_text(" ", strip=True))

            # Bio URL: the "READ MORE" button-styled <a>. Some justices
            # have button-styled <a class="button">, others have a span
            # inside <a>; both forms work via the get-href pattern.
            bio_url = ""
            for a in tr.find_all("a"):
                href = a.get("href")
                if not href:
                    continue
                if "meetthejustices" in href.lower():
                    bio_url = _absolutize(href)
                    break

            photo_url = _absolutize(img.get("src") or "")

            # Stable per-judge id: last path segment of the bio URL when
            # we have one; otherwise a slug derived from the name.
            if bio_url:
                source_id = bio_url.rstrip("/").rsplit("/", 1)[-1].lower()
            else:
                from django.utils.text import slugify
                source_id = slugify(full_name)

            results.append(ScrapedJudge(
                source_id=f"azcourts:{source_id}",
                bio_url=bio_url,
                full_name=full_name,
                role=_AZ_ROLE_MAP.get(role_text, "ASSOCIATE_JUSTICE"),
                court_kind="SUPREME",
                photo_url=photo_url,
                bio_summary=bio_summary,
                appointment_date=None,  # not reliably present in the listing summary
                confidence={
                    "full_name": 1.0,
                    "role": 1.0 if role_text in _AZ_ROLE_MAP else 0.5,
                    "bio_summary": 1.0 if bio_summary else 0.0,
                    "photo_url": 1.0 if photo_url else 0.0,
                },
            ))

        return results
