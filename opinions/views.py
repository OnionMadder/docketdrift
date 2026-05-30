"""Public-facing views.

``opinions.middleware.StateRouterMiddleware`` attaches ``request.state`` based
on the incoming subdomain; views use that to switch between the apex
state-picker and a per-state landing.

These responses are intentionally bare-bones HTML strings -- once we have
real per-state content to show (recent opinions, judge pages), we'll move to
proper templates.
"""
from django.http import HttpResponse

from opinions.models import State


_APEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>DocketDrift</title></head>
<body>
<h1>DocketDrift</h1>
<p>Public-record analysis of state appellate courts.</p>
<p>In development.</p>
<h2>States</h2>
{states_html}
</body>
</html>
"""

_STATE_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>DocketDrift &mdash; {name}</title></head>
<body>
<h1>DocketDrift &mdash; {name}</h1>
<p>Coverage in development for the {name} Supreme Court and Court of Appeals.</p>
</body>
</html>
"""


def home(request):
    """Apex state-picker when no subdomain matches; per-state landing otherwise."""
    state = getattr(request, "state", None)
    if state is None:
        live = list(State.objects.filter(is_live=True).order_by("name"))
        if live:
            tiles = "".join(
                f'<li><a href="https://{s.slug}.docketdrift.com/">{s.name}</a></li>'
                for s in live
            )
            states_html = f"<ul>{tiles}</ul>"
        else:
            states_html = "<p><em>No live states yet.</em></p>"
        return HttpResponse(_APEX_TEMPLATE.format(states_html=states_html))

    return HttpResponse(_STATE_TEMPLATE.format(name=state.name))
