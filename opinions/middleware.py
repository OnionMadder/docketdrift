"""
Host-header -> state routing middleware.

Reads the incoming Host header, extracts the leading subdomain. If it matches
a known State.code, attaches the State to request.state for downstream views.
Otherwise request.state is None and views render the apex (state picker).

This is the server-side equivalent of the JS hostname check on the placeholder.
"""
from __future__ import annotations

from django.utils.functional import SimpleLazyObject

from opinions.models import State


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
        host = request.get_host()
        request.state = SimpleLazyObject(lambda: _resolve_state(host))
        return self.get_response(request)
