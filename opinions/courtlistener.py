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
DEFAULT_TIMEOUT = 60  # seconds (CL can be slow on FK-traversal filters)
USER_AGENT = "DocketDrift/0.1 (+https://docketdrift.com)"
DEFAULT_PAGE_SIZE = 50
MAX_RETRIES_ON_429 = 3
RETRY_AFTER_FALLBACK = 60  # seconds when Retry-After header is missing

# Network-level retry config (separate from 429): ReadTimeout /
# ConnectionError / SSL drops mid-request. CL occasionally stalls past
# the 60s socket read budget, especially during throttle. We retry the
# whole request a bounded number of times with a short cooldown.
MAX_RETRIES_ON_NETERR = 5
NETERR_BACKOFF_SECONDS = 30


class CourtListenerError(RuntimeError):
    """Raised when the API returns an error we can't recover from."""


# /search/?type=o returns camelCase keys that differ from the /clusters/
# endpoint's snake_case. Normalize so the ingest layer sees one shape
# regardless of which endpoint we pull from. (Only the keys we actually
# read are mapped; the rest pass through unchanged.)
_SEARCH_KEY_MAP = {
    "caseName": "case_name",
    "caseNameFull": "case_name_full",
    "caseNameShort": "case_name_short",
    "dateFiled": "date_filed",
    "dateArgued": "date_argued",
    "docketNumber": "docket_number",
    "cluster_id": "id",
    "status": "precedential_status",
}


def _normalize_search_result(result: dict) -> dict:
    """Translate /search/?type=o camelCase keys to /clusters/ snake_case."""
    out = dict(result)
    for camel, snake in _SEARCH_KEY_MAP.items():
        if camel in out and snake not in out:
            out[snake] = out[camel]
    return out


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

        # Outer loop handles transport-level failures (ReadTimeout,
        # ConnectionError, SSL drops). CL occasionally stalls mid-response
        # past the 60s socket read budget, and the inner 429 retry can't
        # see those because they raise before the response object exists.
        # Without this outer retry, the management command sees an
        # unhandled requests.exceptions.ReadTimeout and aborts the whole
        # ingest -- which is exactly what killed three NH+AZ runs today.
        for net_attempt in range(MAX_RETRIES_ON_NETERR + 1):
            try:
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
                # Inner loop fell through without return -- shouldn't
                # happen given the explicit raise on exhausted retries.
                raise CourtListenerError("Unreachable inner retry loop exit")
            except requests.exceptions.RequestException as exc:
                # ReadTimeout / ConnectionError / SSL hiccup. Backoff
                # and retry. Final attempt raises through so the caller
                # sees a CourtListenerError, not an arbitrary
                # requests.* exception type.
                if net_attempt >= MAX_RETRIES_ON_NETERR:
                    raise CourtListenerError(
                        f"Transport failure after {net_attempt} retries on {url}: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                logger.warning(
                    "courtlistener: %s on %s, backing off %ss (net attempt %s/%s)",
                    type(exc).__name__, url, NETERR_BACKOFF_SECONDS,
                    net_attempt + 1, MAX_RETRIES_ON_NETERR,
                )
                self._sleep(NETERR_BACKOFF_SECONDS)
                continue

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
        # CL v4's /clusters/ endpoint doesn't whitelist `court` OR
        # `docket__court` as filter params (both return 400 "unknown_params").
        # The /search/?type=o endpoint DOES accept `court=` directly and
        # provides cluster-level metadata in a single response -- the only
        # cost is that search returns camelCase keys, which `_paginate` runs
        # through `_normalize_search_result` for us.
        params = {"type": "o", "court": court_id, "order_by": "dateFiled desc"}
        if since:
            params["filed_after"] = since
        for item in self._paginate("search/", params):
            yield _normalize_search_result(item)

    def fetch_opinion(self, opinion_id: str | int) -> dict:
        """Return a single opinion record by ID.

        Used to pull ``plain_text`` for opinions whose IDs come embedded in
        the ``opinions`` field of a ``/search/?type=o`` result. We prefer
        direct ID fetches over the cluster-scoped filter
        ``/opinions/?cluster=<id>`` because the latter is unreliably slow in
        v4 (multi-minute reads even with bounded result sets).
        """
        return self._get(f"opinions/{opinion_id}/")

    def fetch_person(self, person_id: str | int) -> dict:
        """Return raw CourtListener person (judge) metadata."""
        return self._get(f"people/{person_id}/")
