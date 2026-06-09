"""
Host-header -> state routing middleware + crawler short-circuit.

Reads the incoming Host header, extracts the leading subdomain. If it matches
a known State.code, attaches the State to request.state for downstream views.
Otherwise request.state is None and views render the apex (state picker).

This is the server-side equivalent of the JS hostname check on the placeholder.

CrawlerBlockMiddleware short-circuits known noisy SEO crawlers with a 429
response before any view code or DB queries fire. Robots.txt disallow takes
24-48h to propagate; this is the immediate kill switch when one of them is
saturating our single gunicorn worker.
"""
from __future__ import annotations

from django.http import HttpResponse

from opinions.models import State


# Substrings matched (case-insensitive) against the request User-Agent.
# Hard-block at the middleware layer regardless of robots.txt status.
# Googlebot, Bingbot, DuckDuckBot, GPTBot, ClaudeBot, Google-Extended,
# PerplexityBot, CCBot, and ordinary browsers are NOT in this list -- they
# fall through to normal handling.
BLOCKED_CRAWLER_TOKENS = (
    "semrushbot",
    "ahrefsbot",
    "mj12bot",
    "dotbot",
    "seznambot",
    "blexbot",
    "petalbot",
    "rogerbot",
    "exabot",
    "magpie-crawler",
)


class CrawlerBlockMiddleware:
    """Reject noisy SEO crawlers with 429 before any view runs.

    These crawlers don't drive any traffic we care about (they index for
    backlink-monitoring SEO products that aren't our audience), but they
    individually hit per-page URLs at rates that saturate our single
    gunicorn worker. Robots.txt disallow is best-effort; a UA short-circuit
    is immediate.

    Sits BEFORE StateRouterMiddleware so a blocked crawler doesn't even
    incur the State lookup query.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ua = (request.META.get("HTTP_USER_AGENT") or "").lower()
        if any(token in ua for token in BLOCKED_CRAWLER_TOKENS):
            return HttpResponse(
                "Blocked by robots policy. Contact hello@docketdrift.com.\n",
                status=429,
                content_type="text/plain; charset=utf-8",
            )
        return self.get_response(request)


def _resolve_state(host: str | None):
    """Return a State instance (or None) for an incoming host."""
    if not host:
        return None
    host = host.lower().split(":", 1)[0]  # strip any :port
    parts = host.split(".")
    if not parts:
        return None
    if parts[0] == "www":
        parts = parts[1:]
    # Only treat parts[0] as a state code when we have a real subdomain
    # (e.g. mn.docketdrift.com -> ['mn', 'docketdrift', 'com']).
    if len(parts) < 3:
        return None
    candidate = parts[0].upper()
    if len(candidate) != 2 or not candidate.isalpha():
        return None
    try:
        return State.objects.get(code=candidate)
    except State.DoesNotExist:
        return None


class StateRouterMiddleware:
    """Attach request.state based on the request's subdomain."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Resolve eagerly. The view always reads request.state, so SimpleLazy-
        # Object would buy us nothing -- and worse, `request.state is None`
        # would always be False for a wrapped object, sending the apex view
        # into the wrong branch.
        request.state = _resolve_state(request.get_host())
        return self.get_response(request)
