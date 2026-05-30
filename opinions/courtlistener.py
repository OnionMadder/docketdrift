"""
CourtListener REST API v4 client.

Free Law Project's API for US court opinions -- DocketDrift's primary source
of opinion text + judge metadata. Free token; authenticated rate limit is
~125 requests/day, so deep historical backfill should use CourtListener's
bulk data dumps, not this client. This client is for incremental weekly
ingestion.

Docs: https://www.courtlistener.com/api/rest/v4/
"""
from __future__ import annotations

import logging
import time
from typing import Iterator, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.courtlistener.com/api/rest/v4/"
DEFAULT_TIMEOUT = 30  # seconds
USER_AGENT = "DocketDrift/0.1 (+https://docketdrift.com)"
DEFAULT_PAGE_SIZE = 50
MAX_RETRIES_ON_429 = 3
RETRY_AFTER_FALLBACK = 60  # seconds when Retry-After header is missing


class CourtListenerError(RuntimeError):
    """Raised when the API returns an error we can't recover from."""


class CourtListenerClient:
    """Thin REST client. Token is passed in -- callers pull it from env."""

    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        if not token:
            raise ValueError(
                "CourtListener API token is required. "
                "Set COURTLISTENER_TOKEN in the environment."
            )
        self._token = token
        self._base_url = base_url
        self._sleep = sleep_fn
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })

    # --- HTTP -----------------------------------------------------------------
    def _get(self, url_or_path: str, params: Optional[dict] = None) -> dict:
        """GET that handles absolute URLs (CL pagination ``next`` is a full URL)
        and honors 429 Retry-After with bounded retries."""
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            url = url_or_path
        else:
            url = urljoin(self._base_url, url_or_path.lstrip("/"))

        for attempt in range(MAX_RETRIES_ON_429 + 1):
            response = self._session.get(
                url, params=params or {}, timeout=DEFAULT_TIMEOUT
            )
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt >= MAX_RETRIES_ON_429:
                raise CourtListenerError(
                    f"Rate limited after {attempt} retries on {url}"
                )
            retry_after = response.headers.get("Retry-After")
            try:
                wait = int(retry_after) if retry_after else RETRY_AFTER_FALLBACK
            except ValueError:
                wait = RETRY_AFTER_FALLBACK
            logger.warning(
                "courtlistener: 429 on %s, sleeping %ss (attempt %s/%s)",
                url, wait, attempt + 1, MAX_RETRIES_ON_429,
            )
            self._sleep(wait)

        raise CourtListenerError("Unreachable retry loop exit")

    def _paginate(self, path: str, params: dict) -> Iterator[dict]:
        """Walk a paginated CL endpoint, yielding each ``results`` item.

        CL responses look like ``{count, next, previous, results}``. We follow
        ``next`` until null; the first request carries ``params``, follow-up
        requests use the full ``next`` URL (which already encodes them).
        """
        params = dict(params)
        params.setdefault("page_size", DEFAULT_PAGE_SIZE)
        url: Optional[str] = path
        first = True
        while url:
            page = self._get(url, params=params if first else None)
            first = False
            for item in page.get("results") or []:
                yield item
            url = page.get("next")

    # --- Endpoints ------------------------------------------------------------
    def fetch_court(self, court_id: str) -> dict:
        """Return raw metadata for ``court_id`` (e.g. 'minn', 'minnctapp')."""
        return self._get(f"courts/{court_id}/")

    def iter_clusters_for_court(
        self,
        court_id: str,
        since: Optional[str] = None,
    ) -> Iterator[dict]:
        """Yield opinion clusters for ``court_id``, newest first.

        A "cluster" groups all opinions for one decision (majority +
        concurrences + dissents). If ``since`` is provided (ISO 'YYYY-MM-DD')
        the result is filtered to ``date_filed__gte=since``.
        """
        params = {"court": court_id, "order_by": "-date_filed"}
        if since:
            params["date_filed__gte"] = since
        yield from self._paginate("clusters/", params)

    def fetch_opinions_for_cluster(self, cluster_id: str | int) -> Iterator[dict]:
        """Yield individual opinions within a cluster (majority, dissent, ...)."""
        yield from self._paginate("opinions/", {"cluster": str(cluster_id)})

    def fetch_person(self, person_id: str | int) -> dict:
        """Return raw CourtListener person (judge) metadata."""
        return self._get(f"people/{person_id}/")
