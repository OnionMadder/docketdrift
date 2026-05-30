"""
CourtListener REST API v4 client.

Free Law Project's API for US court opinions -- DocketDrift's primary source
of opinion text + judge metadata. Free token; authenticated rate limit is
~125 requests/day, so deep history backfill should use CourtListener's bulk
data dumps, not this client. This client is for incremental weekly ingestion.

Docs: https://www.courtlistener.com/api/rest/v4/
"""
from __future__ import annotations

import logging
from typing import Iterator, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.courtlistener.com/api/rest/v4/"
DEFAULT_TIMEOUT = 30  # seconds
USER_AGENT = "DocketDrift/0.1 (+https://docketdrift.com)"


class CourtListenerClient:
    """Thin REST client. Token is passed in -- callers pull it from env."""

    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        session: Optional[requests.Session] = None,
    ):
        if not token:
            raise ValueError(
                "CourtListener API token is required. "
                "Set COURTLISTENER_TOKEN in the environment."
            )
        self._token = token
        self._base_url = base_url
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {token}",
            "User-Agent": USER_AGENT,
        })

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = urljoin(self._base_url, path.lstrip("/"))
        response = self._session.get(url, params=params or {}, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.json()

    # --- Endpoints (stubs; concrete impls land in the ingestion pass) ----------

    def iter_clusters_for_court(
        self,
        court_id: str,
        since: Optional[str] = None,
    ) -> Iterator[dict]:
        """
        Yield opinion clusters for ``court_id``, filtered to
        ``date_filed__gte=since`` when provided.

        STUB: returns nothing. Real implementation will paginate /clusters/
        with ?court=<id>&date_filed__gte=<ISO date> and follow `next` links.
        """
        # TODO: implement paginated GET on /clusters/.
        return iter(())

    def fetch_opinions_for_cluster(self, cluster_id: str) -> Iterator[dict]:
        """
        Yield constituent opinions (majority, concurrence, dissent, ...) for
        a cluster.

        STUB: returns nothing. Real implementation hits /opinions/?cluster=<id>.
        """
        return iter(())

    def fetch_person(self, person_id: str) -> dict:
        """
        Return CourtListener person (judge) metadata.

        STUB: returns {}. Real impl: GET /people/<id>/ and normalize.
        """
        return {}
