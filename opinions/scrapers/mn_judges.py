"""Scraper for the Minnesota judicial branch current roster.

Strategy:

1. Fetch ``https://mncourts.gov/sitemap.xml``. The sitemap is the canonical
   index of every page on the site, including each justice/judge's static
   bio page. It auto-updates when new bios are published, so we don't need
   to hand-curate a roster.

2. Filter sitemap entries to the two appellate court prefixes:
   - ``/active-judicial-officers/supreme-court-justices/<slug>``
   - ``/active-judicial-officers/court-of-appeals-judges/<slug>``

3. For each discovered bio URL, fetch the static HTML and parse:
   - ``<h1 class="judical-directory__heading">`` -- role + full name
     (the site's CSS class name has a typo for "judicial" but it's
     stable across pages, so we key off it).
   - ``<img class="judical-directory__image">`` -- portrait URL (lives
     under /_media/, which is robots-disallowed for crawling, but
     referencing the URL is fine -- browsers fetch it normally).
   - First ``<p>`` after the heading -- bio summary.
   - Regex search of the bio for an appointment-date phrase.

4. Return a normalized dict per judge. The management command in
   ``opinions.management.commands.scrape_judges`` handles persistence.

Polite: we send a descriptive User-Agent that links back to the project
and sleep briefly between requests so the cron is friendly upstream.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from django.utils.text import slugify

logger = logging.getLogger(__name__)


SITEMAP_URL = "https://mncourts.gov/sitemap.xml"
SCT_PREFIX = (
    "https://mncourts.gov/about-the-courts/judicialdirectory/"
    "active-judicial-officers/supreme-court-justices/"
)
COA_PREFIX = (
    "https://mncourts.gov/about-the-courts/judicialdirectory/"
    "active-judicial-officers/court-of-appeals-judges/"
)
USER_AGENT = "DocketDrift/0.1 (+https://docketdrift.com)"
DEFAULT_TIMEOUT = 60
SLEEP_BETWEEN_FETCHES = 0.4  # seconds; polite-by-default

# Role-extraction patterns, ordered most-specific to least-specific.
_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CHIEF_JUSTICE", re.compile(r"^Chief\s+Justice\s+(.+)$", re.IGNORECASE)),
    ("ASSOCIATE_JUSTICE", re.compile(r"^Associate\s+Justice\s+(.+)$", re.IGNORECASE)),
    ("ASSOCIATE_JUSTICE", re.compile(r"^Justice\s+(.+)$", re.IGNORECASE)),
    ("CHIEF_JUDGE", re.compile(r"^Chief\s+Judge\s+(.+)$", re.IGNORECASE)),
    ("JUDGE", re.compile(r"^Judge\s+(.+)$", re.IGNORECASE)),
)

# Best-effort appointment date extraction from bio prose.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"appointed[^.]*?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"effective\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"sworn\s+in[^.]*?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"joined\s+the\s+[\w\s]+?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
)


@dataclass
class ScrapedJudge:
    """One judge's parsed bio. ``court_kind`` is 'SUPREME' or 'APPEALS'."""

    source_id: str
    bio_url: str
    full_name: str
    role: str
    court_kind: str
    photo_url: str = ""
    bio_summary: str = ""
    appointment_date: Optional[date] = None
    confidence: dict[str, float] = field(default_factory=dict)


def _parse_appointment_date(text: str) -> Optional[date]:
    """Try a few canonical phrase patterns; return None if none match."""
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1).strip().replace(",", "").replace(".", "")
        # MN bios occasionally write "Sept" instead of "Sep" -- strptime's
        # %b doesn't accept the longer abbreviation, normalize it.
        raw = re.sub(r"^Sept\b", "Sep", raw, flags=re.IGNORECASE)
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def _split_role_and_name(heading: str) -> tuple[str, str]:
    """Return (role, full_name) from the bio page's H1 text.

    Falls back to (``UNKNOWN``, heading) when no known role prefix matches,
    so the row still saves with usable data.
    """
    heading = (heading or "").strip()
    for role_key, pattern in _ROLE_PATTERNS:
        m = pattern.match(heading)
        if m:
            return role_key, m.group(1).strip()
    return "UNKNOWN", heading


def _normalize_photo_url(src: str) -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return f"https:{src}"
    if src.startswith("/"):
        return f"https://mncourts.gov{src}"
    return src


def _slug_from_url(url: str) -> str:
    """Last path segment of a bio URL ('natalie-e.-hudson')."""
    return url.rstrip("/").rsplit("/", 1)[-1]


class MNJudgeScraper:
    """Stateful HTTP session wrapper for the MN judicial branch site."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self._sleep = sleep_fn
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def discover_bio_urls(self) -> tuple[list[str], list[str]]:
        """Pull current Supreme Court + Court of Appeals bio URLs from the sitemap.

        Returns ``(sct_urls, coa_urls)``. Parent URLs (the roster index
        pages themselves) are filtered out. Note: the MN sitemap pretty-
        prints its XML with whitespace INSIDE ``<loc>`` tags, so we strip
        each match before pattern-matching.
        """
        resp = self._session.get(SITEMAP_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        all_locs = [u.strip() for u in re.findall(r"<loc>([^<]+)</loc>", resp.text)]
        sct = sorted(
            u
            for u in all_locs
            if u.startswith(SCT_PREFIX) and len(u) > len(SCT_PREFIX)
        )
        coa = sorted(
            u
            for u in all_locs
            if u.startswith(COA_PREFIX) and len(u) > len(COA_PREFIX)
        )
        return sct, coa

    def fetch_bio(self, url: str, court_kind: str) -> Optional[ScrapedJudge]:
        """Fetch one bio page and parse it. Returns None on failure."""
        try:
            resp = self._session.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("mn_judges: fetch failed for %s: %s", url, exc)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        heading_el = soup.find("h1", class_="judical-directory__heading")
        if not heading_el:
            logger.warning("mn_judges: no heading element on %s", url)
            return None
        role, full_name = _split_role_and_name(heading_el.get_text(strip=True))

        photo_url = ""
        img_el = soup.find("img", class_="judical-directory__image")
        if img_el is not None:
            photo_url = _normalize_photo_url(img_el.get("src") or "")

        # Bio extraction: iterate the next 15 paragraphs after the heading
        # and collect the substantive ones, stopping when we hit the global
        # "Need Help?" sidebar that appears on every bio page. Some judges
        # have multi-paragraph bios so we join until we have enough text.
        bio_paragraphs: list[str] = []
        for p in heading_el.find_all_next("p", limit=20):
            text = p.get_text(separator=" ", strip=True)
            if len(text) < 50:
                # Probably a UI fragment ("Read More", a single bold label,
                # an icon caption). Skip but keep looking.
                continue
            if any(marker in text for marker in (
                "Self-Help Center",
                "Find a Lawyer",
                "State Law Library",
                "Get Legal Help",
                "Need Help",
                "Conciliation Court",
            )):
                # Hit the sidebar -- the rest is generic help-page boilerplate.
                break
            bio_paragraphs.append(text)
            if sum(len(t) for t in bio_paragraphs) > 800:
                break
        bio_summary = " ".join(bio_paragraphs)
        if len(bio_summary) > 1500:
            bio_summary = bio_summary[:1500].rsplit(" ", 1)[0] + "..."

        appointment_date = _parse_appointment_date(bio_summary)

        return ScrapedJudge(
            source_id=_slug_from_url(url),
            bio_url=url,
            full_name=full_name,
            role=role,
            court_kind=court_kind,
            photo_url=photo_url,
            bio_summary=bio_summary,
            appointment_date=appointment_date,
            confidence={
                "role": 0.95 if role != "UNKNOWN" else 0.4,
                "full_name": 0.95,
                "photo_url": 0.9 if photo_url else 0.0,
                "bio_summary": 0.85 if bio_summary else 0.0,
                "appointment_date": 0.7 if appointment_date else 0.0,
            },
        )

    def scrape_all(self) -> list[ScrapedJudge]:
        """Discover roster, fetch every bio, return the parsed list."""
        sct_urls, coa_urls = self.discover_bio_urls()
        out: list[ScrapedJudge] = []
        for url in sct_urls:
            scraped = self.fetch_bio(url, court_kind="SUPREME")
            if scraped is not None:
                out.append(scraped)
            self._sleep(SLEEP_BETWEEN_FETCHES)
        for url in coa_urls:
            scraped = self.fetch_bio(url, court_kind="APPEALS")
            if scraped is not None:
                out.append(scraped)
            self._sleep(SLEEP_BETWEEN_FETCHES)
        return out

    @staticmethod
    def django_slug_for(source_id: str) -> str:
        """SlugField-safe form of the URL's last segment.

        Periods aren't allowed in Django ``SlugField`` -- the source URL
        ``natalie-e.-hudson`` becomes the slug ``natalie-e-hudson``.
        """
        return slugify(source_id) or source_id
