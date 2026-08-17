"""Gunicorn access logger that records the client NETWORK, never the client.

Why this exists
===============
Behind NFSN's front proxy, gunicorn's ``%(h)s`` is the proxy's internal
``10.x`` address, so our access log could not distinguish a lawyer in
Minnesota from a scraper in a Singapore datacenter. That blindness is
what let a UA-rotating, resource-blocking crawler masquerade as "74% of
our traffic is Singapore" in analytics for a week (2026-08-18).

The obvious fix -- log ``X-Forwarded-For`` -- is the WRONG fix for this
project. A full client IP beside a path and a timestamp is exactly the
artifact "Data is sacred" refuses to create: it would let someone with a
subpoena ask "who read this opinion?" and get an answer. We cannot
produce what we never stored, and that guarantee is the product's
architectural edge, not a nicety.

So we store the NETWORK BLOCK only:

    203.0.113.47      -> 203.0.113.0/24
    2401:db00:1:2::5  -> 2401:db00:1::/48

That is enough to (a) recognize a datacenter range and fingerprint a
crawler, and (b) geolocate at the country level for "which state should
we build next" -- the two questions we actually have. It is NOT enough
to single out a reader: a /24 is 254 addresses and, on the residential
ISPs real users come from, is shared and dynamically reassigned.

If you are ever tempted to log the full address "just for a day" to
chase something: don't. Write the analysis against the /24 instead. The
one-day exception is how logs like this become permanent.
"""
from __future__ import annotations

from gunicorn.glogging import Logger as GunicornLogger


def client_network(forwarded_for: str) -> str:
    """Reduce an X-Forwarded-For value to its network block.

    Takes the FIRST address in the header (the originating client; later
    entries are intermediate proxies). Returns ``"-"`` for anything we
    can't parse, so a malformed header degrades to "unknown" rather than
    leaking a raw value into the log.
    """
    first = (forwarded_for or "").split(",")[0].strip()
    if not first:
        return "-"

    if ":" in first:
        # IPv6 -> /48 (the block a site is typically allocated).
        groups = first.split(":")
        if len(groups) < 3:
            return "-"
        return ":".join(groups[:3]) + "::/48"

    octets = first.split(".")
    if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        return "-"
    return ".".join(octets[:3]) + ".0/24"


class NetworkOnlyLogger(GunicornLogger):
    """Adds a ``{x-client-net}i`` atom holding the truncated client network.

    Referenced from run.sh's --access-logformat. Gunicorn keys header
    atoms as ``{header-name}i``; injecting our own key here means the
    format string can use ``%({x-client-net}i)s`` with no other changes.
    """

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        atoms["{x-client-net}i"] = client_network(
            environ.get("HTTP_X_FORWARDED_FOR", "")
        )
        return atoms
