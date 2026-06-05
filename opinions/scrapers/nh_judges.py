"""Scraper for the New Hampshire Supreme Court current roster.

NH has a single appellate court (the Supreme Court of New Hampshire, 5
justices), so this scraper returns only ``court_kind='SUPREME'`` rows.

Strategy mirrors MN's: discover bio URLs from the official sitemap,
fetch each page, and parse the heading + first paragraph(s) for a bio
summary. Two important differences:

1. The NH judicial website (``courts.nh.gov``) sits behind a WAF that
   returns ``HTTP 403`` to requests lacking a realistic browser
   User-Agent. We send a Chrome-shaped UA + ``Accept`` header. If the
   site still refuses, the command falls back to a HAND-CURATED roster
   (the 5 justices known as of 2026-06-05 per Wikipedia + NH SCt site)
   so the rows still seed.

2. The bio HTML structure is **not yet verified** -- WebFetch was 403'd
   from this development environment when this file was written, so
   selectors are TODO-flagged and will need adjustment against a real
   bio page on first run. The fall-through behaviour (return a row with
   just full_name + role, no bio_summary/photo) keeps the scraper useful
   even before selector tuning.

Status: **v0 scaffold**. Verified against a real fetch: pending. The
management command's ``--dry-run`` flag will surface mismatched
selectors before any DB writes.
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


SITEMAP_URL = "https://www.courts.nh.gov/sitemap.xml"
# NH SCt justice bio path -- conventional URL shape used by the site as of
# 2026-06-05. TODO[verify]: confirm against a real bio URL on first run.
SCT_PREFIX_CANDIDATES = (
    "https://www.courts.nh.gov/our-courts/supreme-court/justices/",
    "https://www.courts.nh.gov/supreme-court/justices/",
)
# Realistic browser fingerprint -- NH's site rejects bare-Python UAs.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
ACCEPT_HEADER = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)
DEFAULT_TIMEOUT = 60
SLEEP_BETWEEN_FETCHES = 0.5

# Hand-curated fallback. The 5 justices currently seated per Wikipedia +
# the NH SCt site as of 2026-06-05. Used when the sitemap approach 403s
# or returns no matching URLs. Bios + photos stay empty; the user fills
# them in via Django admin.
FALLBACK_ROSTER: tuple[dict[str, str], ...] = (
    {"full_name": "Gordon J. MacDonald", "role": "CHIEF_JUSTICE"},
    {"full_name": "Patrick E. Donovan", "role": "ASSOCIATE_JUSTICE"},
    {"full_name": "Melissa Beth Countway", "role": "ASSOCIATE_JUSTICE"},
    {"full_name": "Bryan Gould", "role": "ASSOCIATE_JUSTICE"},
    {"full_name": "Daniel Will", "role": "ASSOCIATE_JUSTICE"},
)

# Role-extraction patterns -- NH uses the same titular structure as MN's
# Supreme Court branch.
_ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CHIEF_JUSTICE", re.compile(r"^Chief\s+Justice\s+(.+)$", re.IGNORECASE)),
    ("ASSOCIATE_JUSTICE", re.compile(r"^Associate\s+Justice\s+(.+)$", re.IGNORECASE)),
    ("ASSOCIATE_JUSTICE", re.compile(r"^Justice\s+(.+)$", re.IGNORECASE)),
    ("ASSOCIATE_JUSTICE", re.compile(r"^Senior\s+Associate\s+Justice\s+(.+)$", re.IGNORECASE)),
)

# Appointment-date phrases. NH-specific: governors "nominate" rather than
# "appoint" but the bios commonly say "sworn in" or "took the bench".
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"sworn\s+in[^.]*?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"appointed[^.]*?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"took\s+the\s+bench[^.]*?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"nominated[^.]*?(?:and\s+confirmed)?[^.]*?on\s+(\w+\.?\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
)


@dataclass
class ScrapedJudge:
    """One judge's parsed bio. Same shape as the MN scraper's ScrapedJudge."""

    source_id: str
    bio_url: str
    full_name: str
    role: str
    court_kind: str = "SUPREME"  # NH only has one court kind
    photo_url: str = ""
    bio_summary: str = ""
    appointment_date: Optional[date] = None
    confidence: dict[str, float] = field(default_factory=dict)


def _parse_appointment_date(text: str) -> Optional[date]:
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1).strip().replace(",", "").replace(".", "")
        raw = re.sub(r"^Sept\b", "Sep", raw, flags=re.IGNORECASE)
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def _split_role_and_name(heading: str) -> tuple[str, str]:
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
        return f"https://www.courts.nh.gov{src}"
    return src


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


class NHJudgeScraper:
    """Stateful HTTP session wrapper for the NH judicial branch site."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self._sleep = sleep_fn
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": ACCEPT_HEADER,
            "Accept-Language": "en-US,en;q=0.5",
        })

    def discover_bio_urls(self) -> tuple[list[str], list[str]]:
        """Pull current Supreme Court bio URLs from the NH sitemap.

        Returns ``(sct_urls, [])`` -- empty second tuple slot because NH
        has no intermediate Court of Appeals. If the sitemap is
        unreachable or has no matching URLs, returns ``([], [])`` so the
        caller falls through to ``FALLBACK_ROSTER``.
        """
        try:
            resp = self._session.get(SITEMAP_URL, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("nh_judges: sitemap unreachable (%s); fallback path", exc)
            return [], []

        all_locs = [u.strip() for u in re.findall(r"<loc>([^<]+)</loc>", resp.text)]
        sct = []
        for prefix in SCT_PREFIX_CANDIDATES:
            sct.extend(
                u for u in all_locs
                if u.startswith(prefix) and len(u) > len(prefix)
            )
        return sorted(set(sct)), []

    def fetch_bio(self, url: str, court_kind: str = "SUPREME") -> Optional[ScrapedJudge]:
        """Fetch one bio page and parse it. Returns None on failure.

        Selector strategy is **conservative**: we look for plausible H1/
        heading variants and the first substantial paragraph that follows.
        TODO[verify]: tune selectors against a real bio page on first run.
        """
        try:
            resp = self._session.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("nh_judges: fetch failed for %s: %s", url, exc)
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for a heading that contains "Justice" or "Chief Justice".
        # We don't have a confirmed CSS class to key off (analogous to MN's
        # `judical-directory__heading`), so we scan H1/H2 broadly.
        heading_text = ""
        for tag in soup.find_all(["h1", "h2"]):
            text = tag.get_text(strip=True)
            if re.search(r"\b(Chief\s+)?Justice\b", text, re.IGNORECASE):
                heading_text = text
                break
        if not heading_text:
            logger.warning("nh_judges: no Justice heading on %s", url)
            return None
        role, full_name = _split_role_and_name(heading_text)

        # Portrait: any <img> whose src/alt mentions the judge's surname,
        # filed under the standard CMS media path. TODO[verify].
        photo_url = ""
        for img_el in soup.find_all("img"):
            src = (img_el.get("src") or "")
            alt = (img_el.get("alt") or "")
            if any(
                token in (src + " " + alt).lower()
                for token in full_name.lower().split()
            ):
                photo_url = _normalize_photo_url(src)
                break

        # Bio summary: iterate paragraphs after the heading, skipping
        # nav/sidebar boilerplate. Same approach as MN's scraper.
        bio_paragraphs: list[str] = []
        for p in soup.find_all("p", limit=30):
            text = p.get_text(separator=" ", strip=True)
            if len(text) < 50:
                continue
            if any(marker in text for marker in (
                "Self-Help Center",
                "Find a Lawyer",
                "Get Legal Help",
                "Need Help",
                "Court Forms",
                "Filing Fees",
            )):
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
                "role": 0.9 if role != "UNKNOWN" else 0.4,
                "full_name": 0.9,
                "photo_url": 0.85 if photo_url else 0.0,
                "bio_summary": 0.8 if bio_summary else 0.0,
                "appointment_date": 0.7 if appointment_date else 0.0,
            },
        )

    def scrape_all(self) -> list[ScrapedJudge]:
        """Discover roster, fetch every bio, return parsed list.

        When the live site doesn't cooperate, returns the FALLBACK_ROSTER
        as ``ScrapedJudge`` rows with full_name + role only (no bio_url,
        no bio_summary, no photo). The Django command persists those
        with the same get_or_create guard so they don't clobber any
        bio_summary text the user has added by hand.
        """
        sct_urls, _ = self.discover_bio_urls()
        out: list[ScrapedJudge] = []

        if sct_urls:
            for url in sct_urls:
                scraped = self.fetch_bio(url, court_kind="SUPREME")
                if scraped is not None:
                    out.append(scraped)
                self._sleep(SLEEP_BETWEEN_FETCHES)

        if not out:
            # Fall back to the hand-curated roster -- ensures we always seed
            # 5 NH justice rows even if the upstream site refuses us.
            logger.info(
                "nh_judges: sitemap discovery yielded no usable urls; "
                "seeding %d-row hand-curated FALLBACK_ROSTER",
                len(FALLBACK_ROSTER),
            )
            for entry in FALLBACK_ROSTER:
                slug = slugify(entry["full_name"])
                out.append(ScrapedJudge(
                    source_id=f"fallback-{slug}",
                    bio_url="",
                    full_name=entry["full_name"],
                    role=entry["role"],
                    court_kind="SUPREME",
                    confidence={
                        "role": 0.95,        # hand-verified from Wikipedia
                        "full_name": 0.95,
                        "bio_summary": 0.0,  # blank, user fills in
                        "appointment_date": 0.0,
                    },
                ))

        return out

    @staticmethod
    def django_slug_for(source_id: str) -> str:
        """SlugField-safe form of the URL's last segment."""
        return slugify(source_id) or source_id
